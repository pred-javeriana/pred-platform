"""The packaged migrations: pinned hashes (immutability) and the schema version 1 produces."""

import sqlite3
from pathlib import Path

import pytest

from pred_platform.dal.migrate import apply_migrations, load_migrations
from pred_platform.dal.schema import CORE_TABLES, create_db

# SHA-256 of every released migration (text with LF line endings). A released migration is never
# edited: a change is a new migration, whose hash is added here in the same pull request. Reviewers:
# an edited value on an existing line is exactly what ADR-05-002 forbids.
RELEASED: dict[int, str] = {
    1: "7fd6a910dda7f39a1254421bbde0801e964250d4ab6d8740f74d10dc1cf8ee87",
    2: "0d855366ec4beb38636a1272e9840923f9d057d9f8052e65f1c696b539e7d6cf",
    3: "add1571ad21d5605cd49877833d351bf1db4e873ca99168b22c5919c664a4aa0",
    4: "d95daf0e756e24aaa0036889bbdeadbd8ce4322eaf43ed48bae26aa16583b44f",
    5: "ef81f941dea1256877058240e065b6be52c6cd40043d71548162d446c80f6828",
    6: "b8f0f53f938c2d81764f8bcaaee4fde713f5b393c6d647eb869840a1da829cf5",
    7: "992f5be0b7f670a393519ff7a0f6ed3d8074a20dcb3cce7614057061d23f6e37",
    8: "cba43152604647196739f77496222dd3bda5a8d88bac999b065961de7e17a62f",
    9: "ee0d58ffcd57ce34d65478334c571d5196f227dab2cbf2cae0f24175ec99dd01",
    10: "c5488c6fddf87b04ec3dc94b35cfabd2e4a2679db331597bc67b759df576d8a1",
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

    apply_migrations(conn, load_migrations()[:2])

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


# ----------------------------------------------------------------- the schema of version 3
def test_version_3_adds_run_columns_and_keeps_existing_runs_and_tasks() -> None:
    conn = _at_version(2)
    conn.execute(
        "INSERT INTO ingestas (sha256, nombre_archivo, filas, skus) VALUES ('s', 'f.csv', 10, 1)"
    )
    conn.execute("INSERT INTO configuraciones (version, parametros) VALUES (1, '{}')")
    conn.execute("INSERT INTO ejecuciones (ingesta_id, configuracion_id, seed) VALUES (1, 1, 7)")
    conn.execute(
        "INSERT INTO tareas (ejecucion_id, sku, modelo, corte, seed)"
        " VALUES (1, 'FIL-0001', 'classical:sarima', '2026-03-13', 7)"
    )
    conn.commit()

    apply_migrations(conn, load_migrations()[:3])

    assert conn.execute("PRAGMA user_version").fetchone()[0] == 3
    assert conn.execute("SELECT estado, run_id, intento, pid FROM ejecuciones").fetchall() == [
        ("pendiente", None, 0, None)
    ]
    assert conn.execute("SELECT modelo, tiempo_l2_s FROM tareas").fetchall() == [
        ("classical:sarima", None)
    ]
    conn.close()


def test_version_3_keeps_one_row_per_engine_run() -> None:
    conn = create_db()
    conn.execute(
        "INSERT INTO ingestas (sha256, nombre_archivo, filas, skus) VALUES ('s', 'f', 1, 1)"
    )
    conn.execute("INSERT INTO configuraciones (version, parametros) VALUES (1, '{}')")
    insert = "INSERT INTO ejecuciones (ingesta_id, configuracion_id, seed, run_id) VALUES (1,1,0,?)"
    conn.execute(insert, ("r-1",))
    conn.execute("INSERT INTO ejecuciones (ingesta_id, configuracion_id, seed) VALUES (1,1,0)")
    conn.execute("INSERT INTO ejecuciones (ingesta_id, configuracion_id, seed) VALUES (1,1,0)")
    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE constraint failed"):
        conn.execute(insert, ("r-1",))
    conn.close()


# ----------------------------------------------------------------- the schema of version 4
def _run_with_task(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT INTO ingestas (sha256, nombre_archivo, filas, skus) VALUES ('s', 'f.csv', 10, 1)"
    )
    conn.execute("INSERT INTO configuraciones (version, parametros) VALUES (1, '{}')")
    conn.execute("INSERT INTO ejecuciones (ingesta_id, configuracion_id, seed) VALUES (1, 1, 7)")
    conn.execute(
        "INSERT INTO tareas (ejecucion_id, sku, modelo, corte, seed)"
        " VALUES (1, 'FIL-0001', 'classical:sarima', '2026-03-13', 7)"
    )
    conn.execute(
        "INSERT INTO metricas (tarea_id, sku, modelo, metrica, valor)"
        " VALUES (1, 'FIL-0001', 'classical:sarima', 'mae', 1.5)"
    )


def test_version_4_adds_the_selection_tables_and_keeps_existing_metrics() -> None:
    conn = _at_version(3)
    _run_with_task(conn)
    conn.commit()

    apply_migrations(conn, load_migrations()[:4])

    assert conn.execute("PRAGMA user_version").fetchone()[0] == 4
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"selecciones", "seleccion_familias", "ensayos", "ventanas_wf"} <= tables
    # The aggregated metric written before the window column existed stays aggregated (NULL).
    assert conn.execute("SELECT metrica, valor, ventana FROM metricas").fetchall() == [
        ("mae", 1.5, None)
    ]
    assert conn.execute("SELECT COUNT(*) FROM tareas").fetchone()[0] == 1
    conn.close()


def _selection(conn: sqlite3.Connection) -> None:
    _run_with_task(conn)
    conn.execute(
        "INSERT INTO selecciones (ejecucion_id, sku, clase, perfil)"
        " VALUES (1, 'FIL-0001', 'smooth', 'dense_stable')"
    )
    conn.execute(
        "INSERT INTO seleccion_familias (seleccion_id, familia, estado) VALUES (1, 'ml', 'exitosa')"
    )


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO selecciones (ejecucion_id, sku, clase, perfil)"
        " VALUES (1, 'x', 'Smooth', 'dense_stable')",
        "INSERT INTO selecciones (ejecucion_id, sku, clase, perfil)"
        " VALUES (1, 'x', 'smooth', 'dense')",
        "INSERT INTO seleccion_familias (seleccion_id, familia, estado)"
        " VALUES (1, 'sarima', 'exitosa')",
        "INSERT INTO seleccion_familias (seleccion_id, familia, estado)"
        " VALUES (1, 'dl', 'completada')",
        "INSERT INTO ensayos (seleccion_familia_id, orden, trial_id, configuracion_json, estado,"
        " n_ventanas) VALUES (1, 0, 't', '{}', 'podada', 1)",
    ],
)
def test_version_4_checks_the_vocabulary_of_the_engine(statement: str) -> None:
    conn = create_db()
    _selection(conn)
    with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
        conn.execute(statement)
    conn.close()


_TRIAL = (
    "INSERT INTO ensayos (seleccion_familia_id, orden, trial_id, configuracion_json, estado,"
    " n_ventanas) VALUES (1, 0, 't-0', '{}', 'podado', 1)"
)
_WINDOW = (
    "INSERT INTO ventanas_wf (seleccion_familia_id, indice, corte, inicio_train, fin_train,"
    " inicio_val, fin_val) VALUES (1, 0, '2026-01-01', 0, 5, 5, 7)"
)


@pytest.mark.parametrize(
    ("first", "again"),
    [
        # `_selection` already stored the SKU and its `ml` family: repeating them collides.
        (
            None,
            "INSERT INTO selecciones (ejecucion_id, sku, clase, perfil)"
            " VALUES (1, 'FIL-0001', 'lumpy', 'sparse_variable')",
        ),
        (
            None,
            "INSERT INTO seleccion_familias (seleccion_id, familia, estado)"
            " VALUES (1, 'ml', 'fallida')",
        ),
        (_TRIAL, _TRIAL),
        (_WINDOW, _WINDOW),
    ],
)
def test_version_4_keeps_one_row_per_run_sku_family_trial_and_window(
    first: str | None, again: str
) -> None:
    conn = create_db()
    _selection(conn)
    if first is not None:
        conn.execute(first)
    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE constraint failed"):
        conn.execute(again)
    conn.close()


def test_version_10_preserves_published_runs_and_exposes_champions(tmp_path: Path) -> None:
    from pred_platform.dal.migrate import migrate
    from tests.m3_publicada import publish

    db = tmp_path / "version9.db"
    migrate(db, load_migrations()[:9])
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO ingestas (sha256, nombre_archivo, filas, skus) VALUES (?, 'a.csv', 3, 3)",
            ("a" * 64,),
        )
    publish(
        db,
        ingesta_sha256="a" * 64,
        run_id_m2="legacy",
        verdicts={
            "A": ("smooth", "VALIDADO"),
            "B": ("smooth", "EXPLORATORIO"),
            "C": ("lumpy", "NO_EVALUABLE"),
        },
        publish_m4=False,
    )

    migrate(db)

    with sqlite3.connect(db) as conn:
        assert conn.execute("PRAGMA user_version").fetchone() == (10,)
        assert conn.execute("SELECT estado FROM m3_corridas").fetchall() == [("PUBLICADA",)]
        assert conn.execute(
            "SELECT sku_class, familia, n_skus, n_evaluados, n_no_evaluados "
            "FROM m3_campeones ORDER BY sku_class IS NULL, sku_class, familia"
        ).fetchall() == [
            ("lumpy", "classical", 1, 0, 1),
            ("smooth", "classical", 2, 2, 0),
            (None, "classical", 3, 2, 1),
        ]
        assert conn.execute("SELECT * FROM m3_cortes").fetchall() == []
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_readme_names_the_current_schema_version() -> None:
    readme = (Path(__file__).parents[1] / "README.md").read_text()
    assert f"The current schema is version {len(load_migrations())}." in readme
