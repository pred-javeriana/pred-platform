"""The packaged migrations: pinned hashes (immutability) and the schema version 1 produces."""

import sqlite3

import pytest

from pred_platform.dal.migrate import apply_migrations, load_migrations
from pred_platform.dal.schema import CORE_TABLES, create_db

# SHA-256 of every released migration (text with LF line endings). A released migration is never
# edited: a change is a new migration, whose hash is added here in the same pull request. Reviewers:
# an edited value on an existing line is exactly what ADR-05-002 forbids.
RELEASED: dict[int, str] = {
    1: "7fd6a910dda7f39a1254421bbde0801e964250d4ab6d8740f74d10dc1cf8ee87",
    2: "0d855366ec4beb38636a1272e9840923f9d057d9f8052e65f1c696b539e7d6cf",
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
def _at_version(version: int) -> sqlite3.Connection:
    """A fresh in-memory database migrated only up to ``version``."""
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON")
    apply_migrations(conn, load_migrations()[:version])
    return conn


def test_version_1_has_the_eleven_core_tables_and_two_indexes() -> None:
    conn = _at_version(1)
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
    conn = _at_version(1)
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


# ----------------------------------------------------------------- the schema of version 2
def test_version_2_adds_report_and_topology_columns_without_losing_rows() -> None:
    conn = _at_version(1)
    conn.execute(
        "INSERT INTO ingestas (sha256, nombre_archivo, filas, skus) VALUES ('s', 'f.csv', 10, 1)"
    )
    conn.execute("INSERT INTO series (ingesta_id, sku, n_obs) VALUES (1, 'FIL-0001', 8)")
    conn.commit()

    apply_migrations(conn, load_migrations())

    assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
    assert conn.execute("SELECT nombre_archivo, estado, ruta_parquet FROM ingestas").fetchall() == [
        ("f.csv", None, None)
    ]
    assert conn.execute(
        "SELECT sku, n_obs, adi, cv2, n_positive, clase FROM series"
    ).fetchall() == [("FIL-0001", 8, None, None, None, None)]
    conn.close()


def test_version_2_checks_status_and_class_values() -> None:
    conn = create_db()
    with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
        conn.execute(
            "INSERT INTO ingestas (sha256, nombre_archivo, filas, skus, estado)"
            " VALUES ('s', 'f', 1, 1, 'aceptada')"
        )
    conn.execute(
        "INSERT INTO ingestas (sha256, nombre_archivo, filas, skus, estado)"
        " VALUES ('s', 'f', 1, 1, 'accepted')"
    )
    with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
        conn.execute(
            "INSERT INTO series (ingesta_id, sku, n_obs, clase) VALUES (1, 'a', 3, 'Smooth')"
        )
    conn.close()
