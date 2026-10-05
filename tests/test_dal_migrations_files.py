"""The packaged migrations: pinned hashes (immutability) and the schema version 1 produces."""

import sqlite3

import pytest

from pred_platform.dal.migrate import load_migrations
from pred_platform.dal.schema import CORE_TABLES, create_db

# SHA-256 of every released migration (text with LF line endings). A released migration is never
# edited: a change is a new migration, whose hash is added here in the same pull request. Reviewers:
# an edited value on an existing line is exactly what ADR-05-002 forbids.
RELEASED: dict[int, str] = {
    1: "7fd6a910dda7f39a1254421bbde0801e964250d4ab6d8740f74d10dc1cf8ee87",
}


def test_the_packaged_migrations_load_and_are_consecutive() -> None:
    migrations = load_migrations()
    assert [m.version for m in migrations] == list(range(1, len(migrations) + 1))
    assert migrations[0].file_name == "0001_initial_schema.sql"


@pytest.mark.parametrize("version", sorted(RELEASED))
def test_a_released_migration_was_not_edited(version: int) -> None:
    migration = next(m for m in load_migrations() if m.version == version)
    assert migration.sha256 == RELEASED[version], (
        f"{migration.file_name} changed after release. Released migrations are immutable "
        "(ADR-05-002): revert the edit and put the change in a new migration."
    )


def test_every_migration_is_registered_in_released() -> None:
    packaged = {m.version for m in load_migrations()}
    assert packaged == set(RELEASED), (
        "add the SHA-256 of each new migration to RELEASED in this test "
        '(python -c "from pred_platform.dal.migrate import load_migrations; '
        '[print(m.version, m.sha256) for m in load_migrations()]")'
    )


# ----------------------------------------------------------------- the schema of version 1
def test_version_1_has_the_eleven_core_tables_and_two_indexes() -> None:
    conn = create_db()
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    indexes = {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND sql IS NOT NULL"
        )
    }
    assert tables - {"sqlite_sequence"} == set(CORE_TABLES)
    assert indexes == {"ix_pronosticos_sku_run", "ix_metricas_sku_run"}
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
    conn.close()


def test_version_1_keeps_its_check_constraints_and_foreign_keys() -> None:
    conn = create_db()
    with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):  # usuarios.rol
        conn.execute("INSERT INTO usuarios (username, password_hash, rol) VALUES ('a', 'h', 'x')")
    with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):  # tareas.estado
        conn.execute(
            "INSERT INTO ingestas (sha256, nombre_archivo, filas, skus) VALUES ('s', 'f', 1, 1)"
        )
        conn.execute("INSERT INTO configuraciones (version, parametros) VALUES (1, '{}')")
        conn.execute(
            "INSERT INTO ejecuciones (ingesta_id, configuracion_id, seed) VALUES (1, 1, 7)"
        )
        conn.execute(
            "INSERT INTO tareas (ejecucion_id, modelo, corte, estado, seed)"
            " VALUES (1, 'classical:sarima', '2026-06-29', 'completada', 7)"
        )
    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE constraint failed"):  # tareas
        for _ in range(2):
            conn.execute(
                "INSERT INTO tareas (ejecucion_id, sku, modelo, corte, seed)"
                " VALUES (1, 'FIL-0001', 'classical:sarima', '2026-06-29', 7)"
            )
    conn.close()
