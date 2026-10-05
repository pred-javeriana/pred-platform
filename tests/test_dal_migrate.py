"""The migration runner (ADR-05-002), exercised with small fake migration folders."""

import sqlite3
import threading
from collections.abc import Callable
from contextlib import closing
from pathlib import Path

import pytest

from pred_platform.dal import migrate as migrate_module
from pred_platform.dal.migrate import (
    MigrationError,
    MigrationFailed,
    MigrationFilesInvalid,
    SchemaTooNew,
    apply_migrations,
    load_migrations,
    main,
    migrate,
    split_statements,
    status,
)

BASE = """
CREATE TABLE padre (id INTEGER PRIMARY KEY, nombre TEXT NOT NULL);
CREATE TABLE hijo (
    id INTEGER PRIMARY KEY,
    padre_id INTEGER NOT NULL REFERENCES padre(id),
    valor TEXT
);
"""
ADD_COLUMN = "ALTER TABLE padre ADD COLUMN nota TEXT;"
REBUILD_PADRE = """
CREATE TABLE padre_new (id INTEGER PRIMARY KEY, nombre TEXT NOT NULL UNIQUE, nota TEXT);
INSERT INTO padre_new (id, nombre) SELECT id, nombre FROM padre;
DROP TABLE padre;
ALTER TABLE padre_new RENAME TO padre;
"""


def folder_with(tmp_path: Path, scripts: dict[str, str], name: str = "migrations") -> Path:
    folder = tmp_path / name
    folder.mkdir(exist_ok=True)
    for file_name, sql in scripts.items():
        (folder / file_name).write_text(sql, encoding="utf-8")
    return folder


@pytest.fixture
def two(tmp_path: Path) -> Path:
    return folder_with(tmp_path, {"0001_base.sql": BASE, "0002_add_note.sql": ADD_COLUMN})


def user_version(path: Path) -> int:
    with closing(sqlite3.connect(path)) as conn:
        return conn.execute("PRAGMA user_version").fetchone()[0]


def columns(path: Path, table: str) -> list[str]:
    with closing(sqlite3.connect(path)) as conn:
        return [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]


def backups(directory: Path) -> list[str]:
    return sorted(p.name for p in directory.glob("*.bak"))


# ------------------------------------------------------------------ fresh and repeated
def test_a_new_database_is_created_at_the_latest_version_without_a_backup(
    tmp_path: Path, two: Path
) -> None:
    db = tmp_path / "nested" / "pred.db"
    result = migrate(db, load_migrations(two))
    assert (result.from_version, result.to_version, result.applied) == (0, 2, (1, 2))
    assert result.backup_path is None
    assert user_version(db) == 2
    assert columns(db, "padre") == ["id", "nombre", "nota"]
    assert backups(db.parent) == []


def test_migrating_again_does_nothing_and_does_not_back_up(tmp_path: Path, two: Path) -> None:
    db = tmp_path / "pred.db"
    migrate(db, load_migrations(two))
    again = migrate(db, load_migrations(two))
    assert (again.from_version, again.to_version, again.applied) == (2, 2, ())
    assert again.backup_path is None
    assert backups(tmp_path) == []


def test_the_packaged_migrations_build_the_real_schema(tmp_path: Path) -> None:
    db = tmp_path / "pred.db"
    result = migrate(db)
    assert result.applied == (1,)
    with closing(sqlite3.connect(db)) as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"ingestas", "tareas", "reportes_validacion"} <= tables


# ----------------------------------------------------------------------- legacy database
def test_a_database_from_before_versioning_adopts_version_1_and_is_backed_up(
    tmp_path: Path,
) -> None:
    db = tmp_path / "pred.db"
    with closing(sqlite3.connect(db)) as conn:  # the old create_db: tables, user_version 0
        conn.executescript(load_migrations()[0].sql)
        conn.execute(
            "INSERT INTO ingestas (sha256, nombre_archivo, filas, skus) VALUES ('a', 'x.csv', 3, 1)"
        )
        conn.commit()
    assert user_version(db) == 0

    result = migrate(db)

    assert (result.from_version, result.to_version, result.applied) == (0, 1, (1,))
    assert result.backup_path == tmp_path / "pred.db.antes-de-v0001.bak"
    assert result.backup_path is not None
    assert user_version(db) == 1
    with closing(sqlite3.connect(db)) as conn:
        assert conn.execute("SELECT nombre_archivo FROM ingestas").fetchall() == [("x.csv",)]
    assert user_version(result.backup_path) == 0
    with closing(sqlite3.connect(result.backup_path)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM ingestas").fetchone()[0] == 1


# --------------------------------------------------------------------------- upgrades
def test_an_upgrade_keeps_rows_and_backs_up_the_previous_state(tmp_path: Path) -> None:
    folder = folder_with(tmp_path, {"0001_base.sql": BASE})
    db = tmp_path / "pred.db"
    migrate(db, load_migrations(folder))
    with closing(sqlite3.connect(db)) as conn:
        conn.execute("INSERT INTO padre (id, nombre) VALUES (1, 'uno')")
        conn.execute("INSERT INTO hijo (id, padre_id, valor) VALUES (1, 1, 'v')")
        conn.commit()

    (folder / "0002_add_note.sql").write_text(ADD_COLUMN, encoding="utf-8")
    result = migrate(db, load_migrations(folder))

    assert (result.from_version, result.to_version, result.applied) == (1, 2, (2,))
    assert result.backup_path == tmp_path / "pred.db.antes-de-v0002.bak"
    assert result.backup_path is not None
    assert columns(db, "padre") == ["id", "nombre", "nota"]
    with closing(sqlite3.connect(db)) as conn:
        assert conn.execute("SELECT id, nombre, nota FROM padre").fetchall() == [(1, "uno", None)]
    assert user_version(result.backup_path) == 1
    assert columns(result.backup_path, "padre") == ["id", "nombre"]


def test_a_second_backup_for_the_same_target_replaces_the_first(tmp_path: Path) -> None:
    folder = folder_with(tmp_path, {"0001_base.sql": BASE})
    db = tmp_path / "pred.db"
    migrate(db, load_migrations(folder))
    (folder / "0002_add_note.sql").write_text(ADD_COLUMN, encoding="utf-8")
    first = migrate(db, load_migrations(folder))

    # Roll the database back to version 1 by hand and migrate again to the same target.
    with closing(sqlite3.connect(db)) as conn:
        conn.execute("ALTER TABLE padre DROP COLUMN nota")
        conn.execute("PRAGMA user_version = 1")
    second = migrate(db, load_migrations(folder))

    assert first.backup_path == second.backup_path
    assert backups(tmp_path) == ["pred.db.antes-de-v0002.bak"]


def test_a_table_rebuild_works_with_foreign_keys_on_and_keeps_the_data(tmp_path: Path) -> None:
    folder = folder_with(tmp_path, {"0001_base.sql": BASE})
    db = tmp_path / "pred.db"
    migrate(db, load_migrations(folder))
    with closing(sqlite3.connect(db)) as conn:
        conn.execute("INSERT INTO padre (id, nombre) VALUES (1, 'uno')")
        conn.execute("INSERT INTO hijo (id, padre_id) VALUES (1, 1)")
        conn.commit()
    (folder / "0002_note.sql").write_text(ADD_COLUMN, encoding="utf-8")
    (folder / "0003_rebuild_padre.sql").write_text(REBUILD_PADRE, encoding="utf-8")

    conn = sqlite3.connect(db)
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        result = apply_migrations(conn, load_migrations(folder))
        assert result.applied == (2, 3)
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1  # restored
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        assert conn.execute("SELECT padre_id FROM hijo").fetchall() == [(1,)]
        with pytest.raises(sqlite3.IntegrityError):  # enforcement is really back on
            conn.execute("INSERT INTO hijo (id, padre_id) VALUES (2, 999)")
    finally:
        conn.close()


# ---------------------------------------------------------------------------- failures
def test_a_failing_migration_is_rolled_back_as_a_whole(tmp_path: Path) -> None:
    folder = folder_with(
        tmp_path,
        {
            "0001_base.sql": BASE,
            "0002_broken.sql": ADD_COLUMN + "\nINSERT INTO no_such_table VALUES (1);",
        },
    )
    db = tmp_path / "pred.db"
    with pytest.raises(MigrationFailed) as raised:
        migrate(db, load_migrations(folder))
    assert raised.value.version == 2
    assert "0002_broken.sql" in str(raised.value)
    assert user_version(db) == 1  # 0001 stays; 0002 left no trace
    assert columns(db, "padre") == ["id", "nombre"]


def test_the_migrations_after_a_failure_do_not_run(tmp_path: Path) -> None:
    folder = folder_with(
        tmp_path,
        {
            "0001_base.sql": BASE,
            "0002_note.sql": ADD_COLUMN,
            "0003_broken.sql": "SELECT * FROM no_such_table;",
            "0004_later.sql": "CREATE TABLE later (id INTEGER);",
        },
    )
    db = tmp_path / "pred.db"
    with pytest.raises(MigrationFailed) as raised:
        migrate(db, load_migrations(folder))
    assert raised.value.version == 3
    assert user_version(db) == 2
    with closing(sqlite3.connect(db)) as conn:
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "later" not in names


def test_a_foreign_key_violation_rolls_the_migration_back(tmp_path: Path) -> None:
    folder = folder_with(
        tmp_path,
        {
            "0001_base.sql": BASE,
            "0002_orphan.sql": "INSERT INTO hijo (id, padre_id) VALUES (1, 12345);",
        },
    )
    db = tmp_path / "pred.db"
    with pytest.raises(MigrationFailed, match="foreign key"):
        migrate(db, load_migrations(folder))
    assert user_version(db) == 1
    with closing(sqlite3.connect(db)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM hijo").fetchone()[0] == 0


def test_foreign_keys_are_restored_even_after_a_failure(tmp_path: Path) -> None:
    folder = folder_with(tmp_path, {"0001_bad.sql": "SELECT * FROM no_such_table;"})
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON")
    with pytest.raises(MigrationFailed):
        apply_migrations(conn, load_migrations(folder))
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert not conn.in_transaction
    conn.close()


def test_an_open_transaction_is_refused(tmp_path: Path, two: Path) -> None:
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE t (x)")  # implicit transaction in legacy mode
    conn.execute("INSERT INTO t VALUES (1)")
    assert conn.in_transaction
    with pytest.raises(MigrationError, match="open transaction"):
        apply_migrations(conn, load_migrations(two))
    conn.close()


# --------------------------------------------------------------------------- too new
def test_a_database_newer_than_the_code_is_rejected_untouched(tmp_path: Path, two: Path) -> None:
    db = tmp_path / "pred.db"
    with closing(sqlite3.connect(db)) as conn:
        conn.execute("CREATE TABLE t (x)")
        conn.execute("PRAGMA user_version = 99")
    before = db.read_bytes()

    with pytest.raises(SchemaTooNew) as raised:
        migrate(db, load_migrations(two))

    assert (raised.value.found, raised.value.latest) == (99, 2)
    assert db.read_bytes() == before
    assert backups(tmp_path) == []
    report = status(db, load_migrations(two))
    assert (report.current, report.pending) == (99, ())


# --------------------------------------------------------------------------- backups
def test_a_failed_backup_aborts_the_migration(tmp_path: Path) -> None:
    folder = folder_with(tmp_path, {"0001_base.sql": BASE})
    db = tmp_path / "pred.db"
    migrate(db, load_migrations(folder))
    (folder / "0002_note.sql").write_text(ADD_COLUMN, encoding="utf-8")
    (tmp_path / "pred.db.antes-de-v0002.bak").mkdir()  # the destination cannot be replaced

    with pytest.raises(MigrationError, match="could not back up"):
        migrate(db, load_migrations(folder))

    assert user_version(db) == 1
    assert columns(db, "padre") == ["id", "nombre"]
    assert not list(tmp_path.glob("*.part.bak"))


def test_backup_can_be_turned_off(tmp_path: Path) -> None:
    folder = folder_with(tmp_path, {"0001_base.sql": BASE})
    db = tmp_path / "pred.db"
    migrate(db, load_migrations(folder))
    (folder / "0002_note.sql").write_text(ADD_COLUMN, encoding="utf-8")
    result = migrate(db, load_migrations(folder), backup=False)
    assert result.applied == (2,) and result.backup_path is None
    assert backups(tmp_path) == []


# ------------------------------------------------------------------------- concurrency
def test_two_processes_starting_together_apply_each_migration_once(tmp_path: Path) -> None:
    folder = folder_with(tmp_path, {"0001_base.sql": BASE, "0002_note.sql": ADD_COLUMN})
    available = load_migrations(folder)
    db = tmp_path / "pred.db"
    barrier = threading.Barrier(2)
    results: list[migrate_module.MigrationResult] = []
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            barrier.wait()
            results.append(migrate(db, available))
        except BaseException as error:  # noqa: BLE001 - reported below
            errors.append(error)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert sorted(v for r in results for v in r.applied) == [1, 2]
    assert user_version(db) == 2


# ------------------------------------------------------------------------------ status
def test_status_of_a_missing_file_reports_it_and_never_creates_it(
    tmp_path: Path, two: Path
) -> None:
    db = tmp_path / "missing.db"
    report = status(db, load_migrations(two))
    assert (report.exists, report.current, report.latest, report.pending) == (False, 0, 2, (1, 2))
    assert not report.up_to_date
    assert not db.exists()


def test_status_lists_the_pending_versions(tmp_path: Path) -> None:
    folder = folder_with(tmp_path, {"0001_base.sql": BASE})
    db = tmp_path / "pred.db"
    migrate(db, load_migrations(folder))
    (folder / "0002_note.sql").write_text(ADD_COLUMN, encoding="utf-8")
    report = status(db, load_migrations(folder))
    assert (report.exists, report.current, report.pending) == (True, 1, (2,))
    assert user_version(db) == 1  # status changed nothing


# ------------------------------------------------------------------------------ loading
@pytest.mark.parametrize(
    ("scripts", "message"),
    [
        ({"1_base.sql": BASE}, "name must look like"),
        ({"0001_Base.sql": BASE}, "name must look like"),
        ({"0000_zero.sql": BASE}, "starts at 0001"),
        ({"0001_a.sql": BASE, "0003_c.sql": ADD_COLUMN}, "missing \\[2\\]"),
        ({"0002_b.sql": BASE}, "missing \\[1\\]"),
        ({"0001_a.sql": BASE, "0001_b.sql": BASE}, "share number 0001"),
        ({}, "no migrations"),
        ({"0001_a.sql": "-- only a comment"}, "no statements"),
        ({"0001_a.sql": "CREATE TABLE t (x)"}, "incomplete statement"),
        ({"0001_a.sql": "BEGIN; CREATE TABLE t (x); COMMIT;"}, "'BEGIN' is not allowed"),
        ({"0001_a.sql": "CREATE TABLE t (x);\nCOMMIT;"}, "'COMMIT' is not allowed"),
        ({"0001_a.sql": "PRAGMA user_version = 5;"}, "user_version or foreign_keys"),
        ({"0001_a.sql": "PRAGMA foreign_keys = OFF;"}, "user_version or foreign_keys"),
    ],
)
def test_invalid_migration_folders_are_rejected_when_loaded(
    tmp_path: Path, scripts: dict[str, str], message: str
) -> None:
    with pytest.raises(MigrationFilesInvalid, match=message):
        load_migrations(folder_with(tmp_path, scripts))


def test_files_that_are_not_sql_are_ignored(tmp_path: Path) -> None:
    folder = folder_with(tmp_path, {"0001_base.sql": BASE, "README.md": "notes"})
    assert [m.file_name for m in load_migrations(folder)] == ["0001_base.sql"]


def test_the_hash_ignores_line_endings(tmp_path: Path) -> None:
    unix = folder_with(tmp_path, {"0001_a.sql": "CREATE TABLE t (x);\n"}, "unix")
    windows = tmp_path / "windows"
    windows.mkdir()
    (windows / "0001_a.sql").write_bytes(b"CREATE TABLE t (x);\r\n")
    assert load_migrations(unix)[0].sha256 == load_migrations(windows)[0].sha256


# ------------------------------------------------------------------------------ splitter
def test_split_statements_keeps_triggers_strings_and_comments_intact() -> None:
    sql = """
    -- leading comment
    CREATE TABLE a (x TEXT DEFAULT 'one; two');
    CREATE TRIGGER t AFTER INSERT ON a BEGIN
        UPDATE a SET x = 'y'; DELETE FROM a WHERE x = 'z';
    END;
    INSERT INTO a VALUES ('semi;colon'); -- trailing; comment
    /* closing comment */
    """
    statements = split_statements(sql)
    assert len(statements) == 3
    assert statements[1].startswith("CREATE TRIGGER") and statements[1].endswith("END;")
    assert "'semi;colon'" in statements[2]


# ----------------------------------------------------------------------------------- CLI
def test_cli_migrates_then_reports_nothing_to_do(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "pred.db"
    assert main(["--db", str(db)]) == 0
    assert "migrated from version 0 to 1 (applied: 0001)" in capsys.readouterr().out
    assert main(["--db", str(db)]) == 0
    assert "already at schema version 1; nothing to do" in capsys.readouterr().out


def test_cli_mentions_the_backup(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db = tmp_path / "pred.db"
    with closing(sqlite3.connect(db)) as conn:
        conn.executescript(load_migrations()[0].sql)
    assert main(["--db", str(db)]) == 0
    assert "backup:" in capsys.readouterr().out


def test_cli_status_changes_nothing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db = tmp_path / "pred.db"
    assert main(["--db", str(db), "--status"]) == 0
    assert "does not exist; migrating would create it at version 1" in capsys.readouterr().out
    assert not db.exists()
    migrate(db)
    assert main(["--db", str(db), "--status"]) == 0
    assert "schema version 1 (up to date)" in capsys.readouterr().out


def test_cli_status_lists_pending_versions(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "pred.db"
    with closing(sqlite3.connect(db)) as conn:
        conn.execute("CREATE TABLE t (x)")
    monkeypatch.setattr(
        migrate_module,
        "load_migrations",
        lambda source=None: load_migrations(
            folder_with(tmp_path, {"0001_a.sql": BASE, "0002_b.sql": ADD_COLUMN}, "fake")
        ),
    )
    assert main(["--db", str(db), "--status"]) == 0
    assert "schema version 0 of 2; pending: 0001, 0002" in capsys.readouterr().out


def test_cli_reports_a_too_new_database_with_exit_code_1(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "pred.db"
    with closing(sqlite3.connect(db)) as conn:
        conn.execute("PRAGMA user_version = 99")
    assert main(["--db", str(db)]) == 1
    assert "error:" in capsys.readouterr().err


def test_cli_uses_the_configured_database_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "from_env.db"
    monkeypatch.setenv("PRED_DB_PATH", str(db))
    assert main([]) == 0
    assert user_version(db) == 1
    assert str(db) in capsys.readouterr().out


def test_cli_rejects_an_invalid_environment(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("PRED_DATA_SOURCE", "mock")
    assert main([]) == 2
    assert "PRED_DATA_SOURCE" in capsys.readouterr().err


def run_repeatedly(times: int, action: Callable[[], None]) -> None:
    for _ in range(times):
        action()


def test_the_concurrent_start_is_stable(tmp_path: Path) -> None:
    """The two-process race must not be flaky: repeat it on fresh databases."""

    def once() -> None:
        folder = folder_with(tmp_path, {"0001_base.sql": BASE}, f"m{len(list(tmp_path.iterdir()))}")
        db = tmp_path / f"{folder.name}.db"
        available = load_migrations(folder)
        barrier = threading.Barrier(2)
        errors: list[BaseException] = []

        def worker() -> None:
            try:
                barrier.wait()
                migrate(db, available)
            except BaseException as error:  # noqa: BLE001
                errors.append(error)

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert errors == []
        assert user_version(db) == 1

    run_repeatedly(25, once)
