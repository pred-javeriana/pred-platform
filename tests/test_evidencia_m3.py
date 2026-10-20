"""M3 reads are publication-gated, read-only and do not recalculate metrics."""

from __future__ import annotations

import sqlite3
import sys
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any

import pytest

from pred_platform.config import Settings
from pred_platform.dal import evidencia
from pred_platform.dal.evidencia import m3_run_status, read_m3_evidence
from pred_platform.dal.lectura import _open_readonly
from pred_platform.dal.migrate import migrate
from pred_platform.worker.engine import launch


def _database(path: Path) -> None:
    migrate(path)
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(
            "INSERT INTO ingestas (sha256, nombre_archivo, filas, skus) "
            "VALUES ('source', 'source.csv', 35, 2)"
        )
        conn.execute(
            "INSERT INTO m3_corridas (run_id, ingesta_sha256, run_id_m2, identidad, "
            "datos_sinteticos, estado) "
            "VALUES ('m3-test', 'source', 'm2-test', '{}', 1, 'EN_CURSO')"
        )
        conn.execute(
            "INSERT INTO m3_protocolos (run_id, protocolo, manifiesto, procedencia, "
            "reserva_abierta_en, n_unidades_previstas) "
            "VALUES ('m3-test', '{}', '{}', '{}', '2026-10-18T12:00:00+00:00', 12)"
        )
        conn.execute(
            "INSERT INTO m3_categorias (run_id, sku_class, familia_campeona, motivo, adverso, "
            "medianas_r, skus_comparables, excluidos, familias_excluidas, conteos, "
            "porcentajes, n_adversos) VALUES ('m3-test', 'smooth', 'classical', "
            "'menor_mediana', 0, '{}', '[]', '{}', '{}', '{}', '{}', 0)"
        )
        for sku in ("A", "B"):
            conn.execute("INSERT INTO m3_skus VALUES ('m3-test', ?)", (sku,))
            conn.execute(
                "INSERT INTO m3_veredictos (run_id, sku, sku_class, veredicto, familia_campeona, "
                "n_ventanas, cobertura, razon_sn, comparacion_incompleta, dm_n_ventanas, "
                "dm_horizonte, dm_causa, justificacion, modelos_evaluados) "
                "VALUES ('m3-test', ?, 'smooth', 'EXPLORATORIO', 'classical', 40, "
                "1.0, 0.75, 0, 0, 0, 'datos_sinteticos', 'datos aumentados', '[]')",
                (sku,),
            )
        conn.execute(
            "INSERT INTO m3_cartera VALUES "
            "('m3-test', 'smooth', 'classical', 'rmse', 2, 2, 2, 0, 3.0, 3.0, 2.0, 4.0)"
        )
        conn.commit()


def test_unpublished_run_has_status_but_no_evidence(tmp_path: Path) -> None:
    db = tmp_path / "platform.db"
    _database(db)
    hidden = read_m3_evidence(db, "m2-test")
    assert set(hidden) == {
        "m3_cartera",
        "m3_categorias",
        "m3_corridas",
        "m3_fallos",
        "m3_metricas",
        "m3_pronosticos",
        "m3_protocolos",
        "m3_skus",
        "m3_unidades",
        "m3_veredictos",
    }
    assert list(map(len, hidden.values())) == [0] * 10
    assert m3_run_status(db, "m2-test")[0]["estado"] == "EN_CURSO"
    assert m3_run_status(db, "m2-test")[0]["n_unidades_previstas"] == 12
    with closing(sqlite3.connect(db)) as conn:
        conn.execute("UPDATE m3_corridas SET estado = 'PUBLICADA' WHERE run_id = 'm3-test'")
        conn.commit()
    published = read_m3_evidence(db, "m2-test")
    assert [
        (row["sku"], row["veredicto"], row["razon_sn"]) for row in published["m3_veredictos"]
    ] == [("A", "EXPLORATORIO", 0.75), ("B", "EXPLORATORIO", 0.75)]
    assert published["m3_cartera"][0]["mediana"] == 3.0


def test_progress_separates_successes_from_failed_durable_units(tmp_path: Path) -> None:
    db = tmp_path / "platform.db"
    _database(db)
    with closing(sqlite3.connect(db)) as conn:
        conn.execute(
            "INSERT INTO m3_unidades "
            "(run_id, sku, candidato_id, origen, familia, modelo, estado, causa) "
            "VALUES ('m3-test', 'A', 'A/ml', '2024-01-14', 'ml', 'lightgbm', 'confirmada', NULL)"
        )
        conn.execute(
            "INSERT INTO m3_unidades "
            "(run_id, sku, candidato_id, origen, familia, modelo, estado, causa) "
            "VALUES ('m3-test', 'B', 'B/ml', '2024-01-14', 'ml', 'lightgbm', 'fallida', 'error')"
        )
        conn.commit()
    status = m3_run_status(db, "m2-test")[0]
    assert (status["n_resueltas"], status["n_confirmadas"], status["n_fallidas"]) == (2, 1, 1)
    assert status == {
        "run_id": "m3-test",
        "run_id_m2": "m2-test",
        "estado": "EN_CURSO",
        "causa_rechazo": None,
        "datos_sinteticos": 1,
        "origen_declarado": 1,
        "n_unidades_previstas": 12,
        "n_resueltas": 2,
        "n_confirmadas": 1,
        "n_fallidas": 1,
    }
    hidden = read_m3_evidence(db, "m2-test")
    assert len(hidden) == 10 and [len(rows) for rows in hidden.values()] == [0] * 10
    with closing(sqlite3.connect(db)) as conn:
        conn.execute("UPDATE m3_corridas SET estado = 'PUBLICADA'")
        conn.commit()
    assert [
        (r["sku"], r["veredicto"]) for r in read_m3_evidence(db, "m2-test")["m3_veredictos"]
    ] == [("A", "EXPLORATORIO"), ("B", "EXPLORATORIO")]


def test_published_sku_scope_and_reads_never_write(tmp_path: Path) -> None:
    db = tmp_path / "platform.db"
    _database(db)
    with closing(sqlite3.connect(db)) as conn:
        conn.execute("UPDATE m3_corridas SET estado = 'PUBLICADA'")
        conn.commit()
    before = db.read_bytes()
    one = read_m3_evidence(db, "m3-test", sku="B")
    assert [(row["sku"], row["dm_causa"]) for row in one["m3_veredictos"]] == [
        ("B", "datos_sinteticos")
    ]
    assert "m3_cartera" not in one
    assert m3_run_status(db, "m3-test")[0]["n_confirmadas"] == 0
    assert db.read_bytes() == before
    with evidencia._open_readonly(db) as conn:
        assert conn is not None
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            conn.execute("UPDATE m3_corridas SET estado = 'EN_CURSO'")
    assert m3_run_status(db, "m3-test")[0]["estado"] == "PUBLICADA"


def test_publication_during_read_keeps_one_sqlite_snapshot(tmp_path, monkeypatch):
    db = tmp_path / "snapshot.db"
    _database(db)
    scheduled = False

    @contextmanager
    def scheduled_reader(path):
        nonlocal scheduled
        with _open_readonly(path) as conn:

            def publish(sql):
                nonlocal scheduled
                if not scheduled and sql.startswith('SELECT * FROM "m3_categorias"'):
                    scheduled = True
                    with closing(sqlite3.connect(db)) as writer:
                        writer.execute("UPDATE m3_corridas SET estado='PUBLICADA'")
                        writer.commit()

            assert conn is not None
            conn.set_trace_callback(publish)
            yield conn

    monkeypatch.setattr(evidencia, "_open_readonly", scheduled_reader)
    result = read_m3_evidence(db, "m2-test")
    assert scheduled is True
    assert {table: len(rows) for table, rows in result.items()} == {
        "m3_cartera": 0,
        "m3_categorias": 0,
        "m3_corridas": 0,
        "m3_fallos": 0,
        "m3_metricas": 0,
        "m3_pronosticos": 0,
        "m3_protocolos": 0,
        "m3_skus": 0,
        "m3_unidades": 0,
        "m3_veredictos": 0,
    }
    complete = read_m3_evidence(db, "m2-test")
    assert [r["sku"] for r in complete["m3_veredictos"]] == ["A", "B"]
    assert complete["m3_cartera"][0]["mediana"] == 3.0


def test_m2_alias_gates_each_run_and_keeps_run_identity(tmp_path):
    db = tmp_path / "mixed.db"
    _database(db)
    with closing(sqlite3.connect(db)) as conn:
        conn.execute("UPDATE m3_corridas SET estado='PUBLICADA'")
        conn.execute(
            "INSERT INTO m3_corridas (run_id, ingesta_sha256, run_id_m2, identidad, "
            "datos_sinteticos, estado) VALUES "
            "('m3-second', 'source', 'm2-test', 'second', 1, 'EVALUADA')"
        )
        for table in ("m3_protocolos", "m3_skus", "m3_categorias", "m3_veredictos", "m3_cartera"):
            rows = conn.execute(f'SELECT * FROM "{table}" WHERE run_id=?', ("m3-test",)).fetchall()
            for row in rows:
                conn.execute(
                    f'INSERT INTO "{table}" VALUES ({",".join("?" for _ in row)})',
                    ("m3-second", *row[1:]),
                )
        conn.commit()
    assert [(r["run_id"], r["sku"]) for r in read_m3_evidence(db, "m2-test")["m3_veredictos"]] == [
        ("m3-test", "A"),
        ("m3-test", "B"),
    ]
    assert sorted((r["run_id"], r["estado"]) for r in m3_run_status(db, "m2-test")) == [
        ("m3-second", "EVALUADA"),
        ("m3-test", "PUBLICADA"),
    ]
    with closing(sqlite3.connect(db)) as conn:
        conn.execute("UPDATE m3_corridas SET estado='PUBLICADA'")
        conn.commit()
    assert sorted(
        (r["run_id"], r["sku"]) for r in read_m3_evidence(db, "m2-test")["m3_veredictos"]
    ) == [("m3-second", "A"), ("m3-second", "B"), ("m3-test", "A"), ("m3-test", "B")]


def test_corrupt_files_are_not_missing_evidence(tmp_path):
    db = tmp_path / "corrupt.db"
    db.write_bytes(b"not a sqlite database")
    for reader in (read_m3_evidence, m3_run_status):
        with pytest.raises(sqlite3.DatabaseError, match="not a database"):
            reader(db, "run")
    _database(tmp_path / "valid.db")
    assert m3_run_status(tmp_path / "valid.db", "m2-test")[0]["n_unidades_previstas"] == 12


def test_engine_child_receives_the_configured_central_database(tmp_path: Path, monkeypatch) -> None:
    db = tmp_path / "central.db"
    log = tmp_path / "child.log"
    monkeypatch.setenv("PRED_DB_PATH", "/wrong-database.db")
    settings = Settings(data_root=tmp_path / "data", db_path=db)
    process = launch(
        [sys.executable, "-c", "import os; print(os.environ['PRED_DB_PATH'])"],
        settings=settings,
        log_path=log,
    )
    assert process.wait(timeout=10) == 0
    assert log.read_text().strip() == str(db)


def test_missing_and_old_databases_return_no_evidence(tmp_path: Path) -> None:
    missing = tmp_path / "missing.db"
    assert read_m3_evidence(missing, "run") == {}
    assert m3_run_status(missing, "run") == []
    assert not missing.exists()
    with closing(sqlite3.connect(tmp_path / "old.db")) as conn:
        conn.execute("CREATE TABLE sentinel (value INTEGER)")
    assert read_m3_evidence(tmp_path / "old.db", "run") == {}
    assert m3_run_status(tmp_path / "old.db", "run") == []
    present = tmp_path / "current.db"
    _database(present)
    assert m3_run_status(present, "m2-test")[0]["estado"] == "EN_CURSO"


def test_unknown_origin_is_not_read_as_declared_real_data(tmp_path: Path) -> None:
    db = tmp_path / "platform.db"
    _database(db)
    with closing(sqlite3.connect(db)) as conn:
        conn.execute(
            "UPDATE m3_corridas SET datos_sinteticos = 0, origen_declarado = 0, "
            "estado = 'PUBLICADA' WHERE run_id = 'm3-test'"
        )
        conn.commit()
        published = conn.execute(
            "SELECT sku, datos_sinteticos, origen_declarado FROM evidencia_publicada"
        ).fetchall()
    assert published == [("A", 0, 0), ("B", 0, 0)]
    status = m3_run_status(db, "m2-test")[0]
    assert (status["datos_sinteticos"], status["origen_declarado"]) == (0, 0)


def test_status_reads_schema_and_rows_from_one_snapshot_during_an_upgrade(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pred_platform.dal.migrate import apply_migrations, load_migrations

    db = tmp_path / "platform.db"
    migrate(db, load_migrations()[:7])  # version 7: no origin declaration marker yet

    def upgrade_and_publish_unknown_origin() -> None:
        with closing(sqlite3.connect(db)) as writer:
            apply_migrations(writer, load_migrations())
            writer.execute(
                "INSERT INTO m3_corridas (run_id, ingesta_sha256, run_id_m2, identidad, "
                "datos_sinteticos, origen_declarado, estado) "
                "VALUES ('m3-race', 'source', 'm2-race', '{}', 0, 0, 'EN_CURSO')"
            )
            writer.commit()

    real_open = evidencia._open_readonly

    @contextmanager
    def racing_open(path: Path) -> Any:
        with real_open(path) as conn:
            assert conn is not None

            def trace(sql: str) -> None:
                # The engine upgrades and writes between the schema probe and the row read.
                if sql.startswith("SELECT c.run_id"):
                    conn.set_trace_callback(None)
                    upgrade_and_publish_unknown_origin()

            conn.set_trace_callback(trace)
            yield conn

    monkeypatch.setattr(evidencia, "_open_readonly", racing_open)
    assert m3_run_status(db, "m2-race") == []
    monkeypatch.setattr(evidencia, "_open_readonly", real_open)
    status = m3_run_status(db, "m2-race")
    assert [(s["datos_sinteticos"], s["origen_declarado"]) for s in status] == [(0, 0)]
