"""M3 reads are publication-gated, read-only and do not recalculate metrics."""

from __future__ import annotations

import sqlite3
import sys
from contextlib import closing
from pathlib import Path

from pred_platform.config import Settings
from pred_platform.dal.evidencia import m3_run_status, read_m3_evidence
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
    assert all(rows == [] for rows in hidden.values())
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
    assert all(rows == [] for rows in read_m3_evidence(db, "m2-test").values())


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
