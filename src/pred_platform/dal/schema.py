"""Core table names and the create_db() helper (SRS §3.6).

The schema itself lives in the numbered migrations under ``dal/migrations/`` (ADR-05-002): the
current schema is what applying all of them produces. ``dal/migrate.py`` applies them.
"""

import sqlite3
from pathlib import Path

from pred_platform.dal.migrate import apply_migrations, migrate

CORE_TABLES: list[str] = [
    "usuarios",
    "configuraciones",
    "ingestas",
    "bitacora_calidad",
    "series",
    "ejecuciones",
    "tareas",
    "pronosticos",
    "metricas",
    "resultados_comparativos",
    "reportes_validacion",
]


def create_db(path: str | Path = ":memory:") -> sqlite3.Connection:
    """Bring a new or existing SQLite database to the latest schema and return the connection.

    A file database goes through ``migrate`` (with its backup before changing existing data); an
    in-memory one is migrated directly. WAL mode is enabled for reader/writer concurrency.
    """
    if str(path) == ":memory:":
        conn = sqlite3.connect(":memory:")
        apply_migrations(conn)
    else:
        migrate(path)
        conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn
