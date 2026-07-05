"""Tests for the DAL schema builder."""

import sqlite3
from pathlib import Path

import pytest

from pred_platform.dal.schema import CORE_TABLES, create_db


def _get_tables(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;"
    ).fetchall()
    return {row[0] for row in rows}


def test_create_db_in_memory_has_all_tables() -> None:
    conn = create_db()
    assert CORE_TABLES
    missing = set(CORE_TABLES) - _get_tables(conn)
    assert not missing, f"Missing tables: {missing}"
    conn.close()


def test_create_db_file_has_all_tables(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    conn = create_db(db_path)
    conn.close()

    conn2 = sqlite3.connect(str(db_path))
    missing = set(CORE_TABLES) - _get_tables(conn2)
    assert not missing, f"Missing tables: {missing}"
    conn2.close()


def test_wal_mode_enabled(tmp_path: Path) -> None:
    # WAL mode requires a file-based database; it is a no-op for :memory:.
    conn = create_db(tmp_path / "wal_test.db")
    result = conn.execute("PRAGMA journal_mode;").fetchone()
    assert result is not None
    assert result[0] == "wal"
    conn.close()


def test_foreign_keys_enforced() -> None:
    conn = create_db()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO bitacora_calidad (ingesta_id, severidad, codigo, mensaje)"
            " VALUES (999, 'info', 'X', 'test');"
        )
        conn.commit()
    conn.close()
