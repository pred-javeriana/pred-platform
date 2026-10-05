"""Read-only queries over the DAL, for the data layer's views (SRS 3.6: all SQL lives in ``dal``).

These functions never create or modify the database: the file is opened with ``mode=ro`` and only
when a query runs, so building the application does not need the database to exist. A missing
file, or a database that has not been initialised yet, reads as "no data" instead of failing.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class IngestRow:
    """One row of ``ingestas``, in the DAL's own vocabulary."""

    sha256: str
    nombre_archivo: str
    filas: int
    skus: int


@contextmanager
def _open_readonly(db_path: Path) -> Iterator[sqlite3.Connection | None]:
    """A read-only connection, or ``None`` when the file does not exist."""
    if not db_path.is_file():
        yield None
        return
    conn = sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True)
    try:
        yield conn
    finally:
        conn.close()


def _is_missing_table(error: sqlite3.OperationalError) -> bool:
    return "no such table" in str(error)


def list_ingestas(db_path: Path, *, offset: int, limit: int) -> tuple[list[IngestRow], int]:
    """A page of ingests, newest first, and how many there are in total."""
    with _open_readonly(db_path) as conn:
        if conn is None:
            return [], 0
        try:
            (total,) = conn.execute("SELECT COUNT(*) FROM ingestas").fetchone()
            rows = conn.execute(
                "SELECT sha256, nombre_archivo, filas, skus FROM ingestas "
                "ORDER BY id DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        except sqlite3.OperationalError as error:
            if _is_missing_table(error):
                return [], 0
            raise
    return [IngestRow(*row) for row in rows], total


def ingesta_existe(db_path: Path, sha256: str) -> bool:
    """Whether an ingest with this SHA-256 is stored."""
    with _open_readonly(db_path) as conn:
        if conn is None:
            return False
        try:
            row = conn.execute("SELECT 1 FROM ingestas WHERE sha256 = ?", (sha256,)).fetchone()
        except sqlite3.OperationalError as error:
            if _is_missing_table(error):
                return False
            raise
    return row is not None
