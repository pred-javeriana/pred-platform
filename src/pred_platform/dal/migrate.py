"""Versioned schema migrations for the DAL (ADR-05-002).

The schema version is SQLite's ``PRAGMA user_version``. Migrations are numbered SQL scripts in
``dal/migrations/`` (``NNNN_description.sql``, consecutive from 0001). Each pending one runs in its
own immediate transaction together with the ``user_version`` update, so it is applied whole or not
at all. There are no down migrations: going back means restoring a backup.

    uv run python -m pred_platform.dal.migrate            # migrate (alias: make migrate)
    uv run python -m pred_platform.dal.migrate --status   # only report, change nothing
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import sqlite3
import sys
import time
from collections.abc import Sequence
from contextlib import closing
from dataclasses import dataclass, replace
from importlib.resources import files
from importlib.resources.abc import Traversable
from pathlib import Path

_NAME = re.compile(r"^(\d{4})_([a-z0-9_]+)\.sql$")
_COMMENT = re.compile(r"--[^\n]*|/\*.*?\*/", re.DOTALL)
# The runner owns the transaction, the version and the foreign key switch.
_TRANSACTION_WORDS = {"BEGIN", "COMMIT", "END", "ROLLBACK", "SAVEPOINT", "RELEASE"}
_RESERVED_PRAGMA = re.compile(r"user_version|foreign_keys", re.IGNORECASE)


class MigrationError(Exception):
    """Base class of every migration failure."""


class MigrationFilesInvalid(MigrationError):
    """The migration scripts themselves are wrong (name, numbering, forbidden statements)."""


class SchemaTooNew(MigrationError):
    """The database is at a version this code does not know (for example, a newer backup)."""

    def __init__(self, found: int, latest: int) -> None:
        self.found = found
        self.latest = latest
        super().__init__(
            f"the database is at schema version {found} but this platform only knows up to "
            f"{latest}; it was probably written by a newer version. Nothing was changed"
        )


class MigrationFailed(MigrationError):
    """A migration raised while running; it was rolled back as a whole."""

    def __init__(self, migration: Migration, cause: BaseException) -> None:
        self.version = migration.version
        self.file_name = migration.file_name
        super().__init__(
            f"migration {migration.file_name} (version {migration.version}) failed and was rolled "
            f"back: {cause}"
        )


@dataclass(frozen=True, slots=True)
class Migration:
    """One numbered script. ``sha256`` is computed over the text with LF line endings."""

    version: int
    name: str
    sql: str
    statements: tuple[str, ...]
    sha256: str

    @property
    def file_name(self) -> str:
        return f"{self.version:04d}_{self.name}.sql"


@dataclass(frozen=True, slots=True)
class MigrationResult:
    from_version: int
    to_version: int
    applied: tuple[int, ...]
    backup_path: Path | None = None


@dataclass(frozen=True, slots=True)
class SchemaStatus:
    exists: bool
    current: int
    latest: int
    pending: tuple[int, ...]

    @property
    def up_to_date(self) -> bool:
        return not self.pending


# ----------------------------------------------------------------------------- loading
def split_statements(sql: str) -> list[str]:
    """Split a script into statements (``;``-terminated), keeping triggers and strings intact.

    ``executescript`` is not used because it commits the transaction in progress.
    """
    statements: list[str] = []
    buffer = ""
    for line in sql.splitlines(keepends=True):
        buffer += line
        if sqlite3.complete_statement(buffer):
            statements.append(buffer.strip())
            buffer = ""
    if _COMMENT.sub("", buffer).strip():
        raise ValueError(f"incomplete statement (missing ';'): {buffer.strip()[:60]!r}")
    return statements


def _check_statement(file_name: str, statement: str) -> None:
    stripped = _COMMENT.sub("", statement).strip()
    first = stripped.split(None, 1)[0].upper().rstrip(";")
    if first in _TRANSACTION_WORDS:
        raise MigrationFilesInvalid(
            f"{file_name}: '{first}' is not allowed; the runner owns the transaction"
        )
    if first == "PRAGMA" and _RESERVED_PRAGMA.search(stripped):
        raise MigrationFilesInvalid(
            f"{file_name}: a script may not set user_version or foreign_keys; the runner does"
        )


def load_migrations(source: Traversable | Path | None = None) -> list[Migration]:
    """The migrations in ``source`` (default: the packaged ``dal/migrations/``), in order.

    Raises:
        MigrationFilesInvalid: a bad file name, a duplicate or missing number, no migrations at
            all, a script that controls the transaction, or an incomplete statement.
    """
    folder = files("pred_platform.dal") / "migrations" if source is None else source
    found: dict[int, Migration] = {}
    for item in sorted(folder.iterdir(), key=lambda entry: entry.name):
        if not item.name.endswith(".sql"):
            continue
        match = _NAME.match(item.name)
        if match is None:
            raise MigrationFilesInvalid(
                f"{item.name}: name must look like 0001_description.sql (4 digits, lowercase)"
            )
        version = int(match.group(1))
        if version == 0:
            raise MigrationFilesInvalid(f"{item.name}: numbering starts at 0001")
        if version in found:
            raise MigrationFilesInvalid(
                f"two migrations share number {version:04d}: "
                f"{found[version].file_name} and {item.name}"
            )
        text = item.read_text(encoding="utf-8").replace("\r\n", "\n")
        try:
            statements = split_statements(text)
        except ValueError as error:
            raise MigrationFilesInvalid(f"{item.name}: {error}") from error
        if not statements:
            raise MigrationFilesInvalid(f"{item.name}: the script has no statements")
        for statement in statements:
            _check_statement(item.name, statement)
        found[version] = Migration(
            version=version,
            name=match.group(2),
            sql=text,
            statements=tuple(statements),
            sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        )
    if not found:
        raise MigrationFilesInvalid("there are no migrations")
    expected = list(range(1, len(found) + 1))
    if sorted(found) != expected:
        missing = sorted(set(expected) - set(found))
        raise MigrationFilesInvalid(
            f"migration numbers must be consecutive from 0001; missing {missing}"
        )
    return [found[version] for version in expected]


# ---------------------------------------------------------------------------- applying
def _user_version(conn: sqlite3.Connection) -> int:
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def _has_tables(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' LIMIT 1"
    ).fetchone()
    return row is not None


def _apply_one(conn: sqlite3.Connection, migration: Migration) -> bool:
    """Run one migration in its own transaction. ``False`` if another process already did."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        # Re-read under the write lock: a second process may have migrated while we waited.
        if _user_version(conn) >= migration.version:
            conn.execute("ROLLBACK")
            return False
        for statement in migration.statements:
            conn.execute(statement)
        violations = conn.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise sqlite3.IntegrityError(
                f"{len(violations)} foreign key violation(s), first: {tuple(violations[0])}"
            )
        conn.execute(f"PRAGMA user_version = {migration.version}")
        conn.execute("COMMIT")
    except Exception as error:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise MigrationFailed(migration, error) from error
    return True


def apply_migrations(
    conn: sqlite3.Connection, migrations: Sequence[Migration] | None = None
) -> MigrationResult:
    """Apply every pending migration to an open connection (no backup; see ``migrate``).

    Foreign key enforcement is switched off outside the transaction while applying, as SQLite
    requires to rebuild tables, and restored afterwards; ``foreign_key_check`` runs before each
    commit.

    Raises:
        SchemaTooNew: the database is at a version newer than the last known migration.
        MigrationFailed: a migration failed; it was rolled back, earlier ones are kept.
    """
    available = list(load_migrations() if migrations is None else migrations)
    latest = available[-1].version
    start = _user_version(conn)
    if start > latest:
        raise SchemaTooNew(start, latest)
    if start == latest:
        return MigrationResult(start, start, ())
    if conn.in_transaction:
        raise MigrationError("the connection has an open transaction; commit or roll it back first")

    previous_isolation = conn.isolation_level
    foreign_keys = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    conn.isolation_level = None  # manual BEGIN/COMMIT
    conn.execute("PRAGMA foreign_keys = OFF")
    applied: list[int] = []
    try:
        for migration in available:
            if migration.version <= _user_version(conn):
                continue
            if _apply_one(conn, migration):
                applied.append(migration.version)
    finally:
        conn.execute(f"PRAGMA foreign_keys = {'ON' if foreign_keys else 'OFF'}")
        conn.isolation_level = previous_isolation
    return MigrationResult(start, _user_version(conn), tuple(applied))


def _enable_wal(conn: sqlite3.Connection, timeout: float = 30.0) -> None:
    """Switch to WAL, retrying while another connection holds a lock.

    SQLite does not apply the busy timeout when changing the journal mode, so two processes
    starting together could fail here with "database is locked".
    """
    if str(conn.execute("PRAGMA journal_mode").fetchone()[0]).lower() == "wal":
        return
    deadline = time.monotonic() + timeout
    while True:
        try:
            conn.execute("PRAGMA journal_mode = WAL")
            return
        except sqlite3.OperationalError as error:
            if "locked" not in str(error) or time.monotonic() >= deadline:
                raise
            time.sleep(0.02)


def _backup(conn: sqlite3.Connection, db_path: Path, target_version: int) -> Path:
    """A consistent copy of the database, replacing any earlier one for the same target."""
    destination = db_path.with_name(f"{db_path.name}.antes-de-v{target_version:04d}.bak")
    partial = db_path.with_name(f"{db_path.name}.antes-de-v{target_version:04d}.part.bak")
    partial.unlink(missing_ok=True)
    try:
        with closing(sqlite3.connect(partial)) as copy:
            conn.backup(copy)
        os.replace(partial, destination)
    except Exception as error:
        partial.unlink(missing_ok=True)
        raise MigrationError(
            f"could not back up {db_path} before migrating, so nothing was changed: {error}"
        ) from error
    return destination


def migrate(
    db_path: str | Path,
    migrations: Sequence[Migration] | None = None,
    *,
    backup: bool = True,
) -> MigrationResult:
    """Bring the database file to the latest version, creating it (and its folder) if missing.

    Before applying anything to a database that already has tables, a copy is saved next to it as
    ``<name>.antes-de-v000N.bak`` (N = target version). If the copy fails, nothing is migrated. A
    database newer than this code is rejected untouched.

    Raises:
        SchemaTooNew, MigrationFailed, MigrationError: see ``apply_migrations``.
    """
    available = list(load_migrations() if migrations is None else migrations)
    latest = available[-1].version
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path, timeout=30)) as conn:
        current = _user_version(conn)
        if current > latest:
            raise SchemaTooNew(current, latest)
        if current == latest:
            return MigrationResult(current, current, ())
        backup_path = _backup(conn, path, latest) if backup and _has_tables(conn) else None
        _enable_wal(conn)
        return replace(apply_migrations(conn, available), backup_path=backup_path)


def status(db_path: str | Path, migrations: Sequence[Migration] | None = None) -> SchemaStatus:
    """Where the database stands, read-only. A missing file is reported, never created."""
    available = list(load_migrations() if migrations is None else migrations)
    latest = available[-1].version
    path = Path(db_path)
    if not path.is_file():
        return SchemaStatus(False, 0, latest, tuple(m.version for m in available))
    with closing(sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)) as conn:
        current = _user_version(conn)
    return SchemaStatus(
        True, current, latest, tuple(m.version for m in available if m.version > current)
    )


# ------------------------------------------------------------------------------- CLI
def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m pred_platform.dal.migrate", description="Migrate the PRED database."
    )
    parser.add_argument(
        "--db", type=Path, help="database file (default: PRED_DB_PATH or data root)"
    )
    parser.add_argument("--status", action="store_true", help="only report, change nothing")
    args = parser.parse_args(argv)

    if args.db is None:
        from pred_platform.config import Settings  # local: keep the DAL free of app settings

        try:
            args.db = Settings.from_env().db_path
        except ValueError as error:
            print(f"error: {error}", file=sys.stderr)
            return 2

    try:
        if args.status:
            report = status(args.db)
            if not report.exists:
                print(
                    f"{args.db}: does not exist; migrating would create it at "
                    f"version {report.latest}"
                )
            elif report.up_to_date:
                print(f"{args.db}: schema version {report.current} (up to date)")
            else:
                print(
                    f"{args.db}: schema version {report.current} of {report.latest}; "
                    f"pending: {', '.join(f'{v:04d}' for v in report.pending)}"
                )
            return 0
        result = migrate(args.db)
    except MigrationError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    if not result.applied:
        print(f"{args.db}: already at schema version {result.to_version}; nothing to do")
    else:
        print(
            f"{args.db}: migrated from version {result.from_version} to {result.to_version} "
            f"(applied: {', '.join(f'{v:04d}' for v in result.applied)})"
        )
        if result.backup_path is not None:
            print(f"backup: {result.backup_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
