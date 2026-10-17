"""The selection per SKU (G4): import from ``seleccion.jsonl`` and read it back from the DAL.

The stand-in engine writes the file (``FAKE_ENGINE_SELECTION`` shapes it): three SKUs of three
demand classes, so the policy matrix leaves families out, one failed unit and one excluded SKU.
Whatever is read back must validate against the contract's JSON Schemas.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from pred_platform.contract import models
from pred_platform.contract.errors import ArtifactMissing, InvalidQuery
from pred_platform.contract.resources import read_schema
from pred_platform.dal import selecciones as dal
from pred_platform.dal.ejecuciones import connect, get_run
from pred_platform.dal.migrate import apply_migrations, load_migrations
from pred_platform.data import capabilities
from pred_platform.data.dal import DalRepository
from pred_platform.worker.seleccion import import_selection
from tests.test_worker import SMALL, Env, _scenario, enqueue_run
from tests.test_worker import env as env  # noqa: F401  (the fixture)

RUN = "1"


def _conforms(document: Any, kind: str) -> None:
    validator = Draft202012Validator(
        read_schema(kind), format_checker=Draft202012Validator.FORMAT_CHECKER
    )
    data = json.loads(document.model_dump_json(exclude_none=True))
    assert [e.message for e in validator.iter_errors(data)] == []


def _run(env: Env, monkeypatch: pytest.MonkeyPatch, scenario: str = "ok", **more: str) -> None:
    _scenario(monkeypatch, scenario)
    for name, value in more.items():
        monkeypatch.setenv(name, value)
    enqueue_run(env.db, env.ingest_id, SMALL)
    env.worker().run_next()


def _families(entry: models.SkuSelection) -> dict[str, models.FamilyEntry]:
    return {f.family: f for f in entry.families}


# ------------------------------------------------------------------------- importing
def test_the_run_leaves_one_selection_per_sku_with_the_four_families(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run(env, monkeypatch)

    assert env.rows("SELECT sku, clase, perfil FROM selecciones ORDER BY sku") == [
        ("FIL-0001", "smooth", "dense_stable"),
        ("FIL-0002", "intermittent", "sparse_stable"),
        ("FIL-0003", "lumpy", "sparse_variable"),
    ]
    assert env.rows("SELECT COUNT(*) FROM seleccion_familias") == [(12,)]
    # Each family that ran is linked to its task (SKU x model, decision D6).
    linked = env.rows(
        "SELECT f.familia, t.modelo, t.estado FROM seleccion_familias f "
        "JOIN selecciones s ON s.id = f.seleccion_id "
        "JOIN tareas t ON t.id = f.tarea_id WHERE s.sku = 'FIL-0001' ORDER BY f.familia"
    )
    assert linked == [
        ("classical", "classical:sarima", "exitosa"),
        ("dl", "dl:mlp", "exitosa"),
        ("ml", "ml:lightgbm", "exitosa"),
    ]
    assert env.rows("SELECT estado FROM ejecuciones") == [("completada",)]


def test_every_window_metric_is_a_row_of_metricas_linked_to_its_task(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run(env, monkeypatch)

    rows = env.rows(
        "SELECT t.sku, t.modelo, m.ventana, m.metrica, m.valor FROM metricas m "
        "JOIN tareas t ON t.id = m.tarea_id WHERE t.sku = 'FIL-0001' AND t.modelo = 'ml:lightgbm' "
        "ORDER BY m.ventana, m.metrica"
    )
    # Two windows x the three metrics the engine could compute (smape was null: not stored).
    assert [(r[2], r[3]) for r in rows] == [
        (0, "mae"),
        (0, "mase"),
        (0, "rmse"),
        (1, "mae"),
        (1, "mase"),
        (1, "rmse"),
    ]
    assert {r[1] for r in rows} == {"ml:lightgbm"}
    # 6 families ran (the matrix leaves dl out of FIL-0002 and ml and dl out of FIL-0003): 2 windows
    assert env.rows("SELECT COUNT(*) FROM ventanas_wf") == [(2 * 6,)]


def test_importing_the_same_selection_twice_leaves_the_same_rows(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run(env, monkeypatch)
    tables = ("selecciones", "seleccion_familias", "ensayos", "ventanas_wf", "metricas")
    before = {t: env.rows(f"SELECT COUNT(*) FROM {t}")[0][0] for t in tables}
    run_dir = env.settings.data_root / "runs" / env.rows("SELECT run_id FROM ejecuciones")[0][0]

    with closing(connect(env.db)) as conn:
        run = get_run(conn, 1)
        assert run is not None
        again = import_selection(conn, run, run_dir=run_dir, corte="2026-03-13")

    assert (again.imported, again.rejected) == (3, [])
    assert {t: env.rows(f"SELECT COUNT(*) FROM {t}")[0][0] for t in tables} == before


def test_a_bad_line_loses_only_its_sku(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    _run(env, monkeypatch, FAKE_ENGINE_SELECTION="bad_line")

    assert env.rows("SELECT COUNT(*) FROM selecciones") == [(3,)]
    assert env.rows("SELECT estado FROM ejecuciones") == [("completada",)]
    run_dir = env.settings.data_root / "runs" / env.rows("SELECT run_id FROM ejecuciones")[0][0]
    with closing(connect(env.db)) as conn:
        run = get_run(conn, 1)
        assert run is not None
        result = import_selection(conn, run, run_dir=run_dir, corte="2026-03-13")
    assert result.imported == 3 and len(result.rejected) == 2
    assert "línea 2" in result.rejected[0]
    assert "FIL-9999" not in {r[0] for r in env.rows("SELECT sku FROM selecciones")}
    assert "no coincide" in result.rejected[1]


def test_a_file_of_another_major_version_is_refused_without_stopping_the_run(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run(env, monkeypatch, FAKE_ENGINE_SELECTION="future")

    assert env.rows("SELECT COUNT(*) FROM selecciones") == [(0,)]
    assert env.rows("SELECT estado FROM ejecuciones") == [("completada",)]
    doc = env.repo().list_sku_selections(RUN)
    assert doc.availability.status == "unavailable"
    assert doc.availability.reason_code == "prerequisite_missing"


def test_an_engine_without_the_file_leaves_the_run_unaffected(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run(env, monkeypatch, FAKE_ENGINE_SELECTION="none")

    assert env.rows("SELECT estado FROM ejecuciones") == [("completada",)]
    doc = env.repo().list_sku_selections(RUN)
    assert doc.availability.status == "unavailable"
    assert "no la entrega" in (doc.availability.detail or "")
    assert doc.run_id == "1" and doc.items == []
    with pytest.raises(ArtifactMissing, match="No hay selección del SKU 'FIL-0001'"):
        env.repo().get_sku_selection(RUN, "FIL-0001")


def test_a_selection_replaces_what_the_sku_had_and_failing_keeps_it(env: Env) -> None:
    with closing(connect(env.db)) as conn:
        conn.execute("INSERT INTO configuraciones (version, parametros) VALUES (1, '{}')")
        conn.execute("INSERT INTO ejecuciones (ingesta_id, configuracion_id, seed) VALUES (1,1,0)")
        conn.execute(
            "INSERT INTO tareas (ejecucion_id, sku, modelo, corte, seed) "
            "VALUES (1, 'S', 'ml:lightgbm', '2026-03-13', 0)"
        )
        conn.commit()

        def selection(valor: float, *, task: str | None = "ml:lightgbm") -> dal.SeleccionGuardar:
            window = {
                "indice": 0,
                "corte": "2026-02-10",
                "inicio_train": 0,
                "fin_train": 5,
                "inicio_val": 5,
                "fin_val": 7,
                "metricas": {"mae": valor},
                "duracion_s": 0.1,
                "fallo": None,
            }
            summary = {
                "metrica_objetivo": "mae",
                "valor_agregado": valor,
                "completo": True,
                "n_ventanas_evaluadas": 1,
                "n_ventanas_totales": 1,
                "selection_scope": "same_history",
                "statistical_comparison_available": False,
                "windows": [window],
            }
            return dal.SeleccionGuardar(
                "S",
                "smooth",
                "dense_stable",
                "p",
                [
                    dal.FamiliaGuardar(
                        "ml",
                        "exitosa",
                        modelo_tarea=task,
                        modelo="ml:lightgbm",
                        walk_forward=summary,
                    )
                ],
            )

        dal.guardar_seleccion(conn, 1, "2026-03-13", selection(1.0))
        dal.guardar_seleccion(conn, 1, "2026-03-13", selection(2.0))
        assert conn.execute("SELECT COUNT(*) FROM selecciones").fetchone() == (1,)
        assert conn.execute("SELECT valor FROM metricas").fetchall() == [(2.0,)]
        assert dal.contar_selecciones(conn, 1) == 1

        # The family reports windows but its task does not exist: nothing of the SKU changes.
        with pytest.raises(dal.SelectionImportError, match="no existe su tarea"):
            dal.guardar_seleccion(conn, 1, "2026-03-13", selection(9.0, task="ml:otro"))
        assert conn.execute("SELECT valor_agregado FROM seleccion_familias").fetchall() == [(2.0,)]
        assert conn.execute("SELECT valor FROM metricas").fetchall() == [(2.0,)]


# ------------------------------------------------------------------------- reading
def test_the_detail_of_a_sku_validates_against_the_contract_schema(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run(env, monkeypatch)

    detail = env.repo().get_sku_selection(RUN, "FIL-0001")

    _conforms(detail, "sku_selection")
    assert detail.source == "dal" and detail.availability.status == "available"
    assert (detail.sku_class, detail.profile) == ("smooth", "dense_stable")
    assert detail.ingest_id == env.ingest_id and detail.run_id == "1"
    assert detail.policy_version == "2.2.0-initial:configured:classical,ml,dl"
    assert detail.topology is not None and detail.topology.sku_id == "FIL-0001"
    assert detail.champion is None  # M3 writes it
    assert [f.family for f in detail.families] == ["classical", "ml", "dl", "foundation"]
    families = _families(detail)
    classical = families["classical"]
    assert (classical.estado, classical.modelo) == ("exitosa", "classical:sarima")
    assert classical.evidence is not None and classical.evidence.family == "classical"
    assert classical.forecast_seed == 17 and classical.forecast_config == {"m": 7}
    assert classical.walk_forward is not None and classical.walk_forward.windows is None
    assert families["foundation"].estado == "excluida"
    assert families["foundation"].trials is None and families["foundation"].evidence is None


def test_trials_keep_pruned_and_failed_apart_with_their_reason(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run(env, monkeypatch, "unit_fails")

    families = _families(env.repo().get_sku_selection(RUN, "FIL-0002"))

    ok = families["classical"].trials
    assert ok is not None
    assert [(t.estado, t.valor, t.motivo is None) for t in ok] == [
        ("completado", 1.5, True),
        ("podado", 2.5, False),
    ]
    assert ok[1].motivo == "escalon=1 valor=2.5 umbral_top_1=1.5"
    failed = families["ml"]
    assert failed.estado == "fallida" and failed.walk_forward is None and failed.evidence is None
    assert failed.error is not None
    assert (failed.error.code, failed.error.stage) == ("model.fit_failed", "L2")
    assert failed.trials is not None
    assert [(t.estado, t.valor) for t in failed.trials] == [("fallido", None), ("podado", 2.5)]
    assert "ajuste_fallido" in (failed.trials[0].motivo or "")


def test_windows_come_back_only_when_asked_and_carry_their_metrics(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run(env, monkeypatch)

    detail = env.repo().get_sku_selection(RUN, "FIL-0001", include_windows=True)

    _conforms(detail, "sku_selection")
    walk = _families(detail)["ml"].walk_forward
    assert walk is not None and walk.windows is not None
    assert [w.indice for w in walk.windows] == [0, 1]
    assert [w.corte for w in walk.windows] == ["2026-02-10", "2026-02-24"]
    assert walk.windows[0].metricas == {"mae": 1.25, "mase": 0.9, "rmse": 2.25}
    assert (walk.windows[1].inicio_val, walk.windows[1].fin_val) == (54, 56)
    assert (walk.metrica_objetivo, walk.valor_agregado, walk.completo) == ("mae", 1.25, True)
    assert walk.selection_scope == "same_history" and walk.statistical_comparison_available is False


def test_the_engine_exclusions_and_the_policy_exclusions_read_back_with_their_reason(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run(env, monkeypatch, "excluded")

    skipped = _families(env.repo().get_sku_selection(RUN, "FIL-0003"))
    assert skipped["classical"].estado == "no_ejecutable"
    assert skipped["classical"].exclusion is not None
    assert skipped["classical"].exclusion.reason_code == "series_too_short"
    assert "termina antes de t*" in (skipped["classical"].exclusion.detail or "")
    assert skipped["ml"].exclusion is not None
    assert skipped["ml"].exclusion.reason_code == "not_in_policy_matrix"  # lumpy has no ml
    assert skipped["dl"].exclusion is not None
    assert skipped["dl"].exclusion.reason_code == "not_in_policy_matrix"
    assert skipped["foundation"].exclusion is not None
    assert skipped["foundation"].exclusion.reason_code == "not_configured"
    _conforms(env.repo().get_sku_selection(RUN, "FIL-0003"), "sku_selection")


def test_the_list_validates_against_the_contract_schema_and_has_every_family(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run(env, monkeypatch)

    doc = env.repo().list_sku_selections(RUN)

    _conforms(doc, "sku_selection_list")
    assert doc.availability.status == "available" and doc.page.total == 3
    assert doc.ingest_id == env.ingest_id and doc.run_id == "1"
    assert [r.sku_id for r in doc.items] == ["FIL-0001", "FIL-0002", "FIL-0003"]
    first = doc.items[0]
    assert [(f.family, f.estado) for f in first.families] == [
        ("classical", "exitosa"),
        ("ml", "exitosa"),
        ("dl", "exitosa"),
        ("foundation", "excluida"),
    ]
    assert (first.families[0].metrica_objetivo, first.families[0].valor) == ("mae", 1.0)
    assert first.families[3].valor is None and first.champion is None
    assert doc.items[1].profile == "sparse_stable"


def test_the_list_filters_sorts_and_pages_in_sql(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    _run(env, monkeypatch, "unit_fails")
    repo = env.repo()

    assert [r.sku_id for r in repo.list_sku_selections(RUN, sku_class="lumpy").items] == [
        "FIL-0003"
    ]
    assert [r.sku_id for r in repo.list_sku_selections(RUN, q="0002").items] == ["FIL-0002"]
    # The family and the state must hold in the same family: only FIL-0002 has a failed ml.
    assert [
        r.sku_id for r in repo.list_sku_selections(RUN, family="ml", estado="fallida").items
    ] == ["FIL-0002"]
    assert repo.list_sku_selections(RUN, family="dl", estado="fallida").items == []
    assert [
        r.sku_id for r in repo.list_sku_selections(RUN, family="dl", estado="exitosa").items
    ] == ["FIL-0001"]
    assert repo.list_sku_selections(RUN, family="dl", estado="fallida").availability.status == (
        "available"
    )  # an empty filter is not a missing document
    best = repo.list_sku_selections(RUN, sort="valor")
    assert [r.sku_id for r in best.items] == ["FIL-0001", "FIL-0002", "FIL-0003"]
    worst = repo.list_sku_selections(RUN, sort="valor", descending=True)
    assert [r.sku_id for r in worst.items] == ["FIL-0003", "FIL-0002", "FIL-0001"]
    paged = repo.list_sku_selections(RUN, page=2, size=2)
    assert [r.sku_id for r in paged.items] == ["FIL-0003"]
    assert (paged.page.number, paged.page.size, paged.page.total) == (2, 2, 3)


def test_a_sku_without_any_value_sorts_last_in_both_directions(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run(env, monkeypatch, "excluded")  # FIL-0003 ran nothing: no aggregated value

    repo = env.repo()
    assert repo.list_sku_selections(RUN, sort="valor").items[-1].sku_id == "FIL-0003"
    assert repo.list_sku_selections(RUN, sort="valor", descending=True).items[-1].sku_id == (
        "FIL-0003"
    )


def test_the_champion_is_read_from_resultados_comparativos(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run(env, monkeypatch)
    with closing(sqlite3.connect(env.db)) as conn:
        conn.execute(
            "INSERT INTO resultados_comparativos (ejecucion_id, sku, modelo_campeon, "
            "metrica_seleccion, valor_seleccion) "
            "VALUES (1, 'FIL-0001', 'classical:sarima', 'mae', 1.0)"
        )
        conn.commit()

    repo = env.repo()
    champion = repo.get_sku_selection(RUN, "FIL-0001").champion
    assert champion is not None
    assert (champion.modelo, champion.family, champion.valor_seleccion) == (
        "classical:sarima",
        "classical",
        1.0,
    )
    rows = {r.sku_id: r.champion for r in repo.list_sku_selections(RUN).items}
    assert rows["FIL-0001"] == champion and rows["FIL-0002"] is None
    _conforms(repo.get_sku_selection(RUN, "FIL-0001"), "sku_selection")


def test_a_run_that_has_not_finished_has_no_selection_yet(env: Env) -> None:
    enqueue_run(env.db, env.ingest_id, SMALL)

    doc = env.repo().list_sku_selections(RUN)

    assert doc.availability.status == "unavailable"
    assert doc.availability.reason_code == "prerequisite_missing"
    assert "cuando termine la ejecución" in (doc.availability.detail or "")


def test_the_run_can_be_asked_by_its_engine_id(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    _run(env, monkeypatch)
    engine_id = env.rows("SELECT run_id FROM ejecuciones")[0][0]

    assert env.repo().list_sku_selections(engine_id).page.total == 3
    assert env.repo().get_sku_selection(engine_id, "FIL-0002").sku_id == "FIL-0002"


def test_unknown_runs_and_skus_are_missing_and_no_run_is_an_empty_state(env: Env) -> None:
    repo = env.repo()
    empty = repo.list_sku_selections(RUN)
    assert (empty.availability.status, empty.availability.reason_code) == (
        "unavailable",
        "no_data",
    )
    with pytest.raises(ArtifactMissing, match="No existe la ejecución '9'"):
        repo.get_sku_selection("9", "FIL-0001")
    enqueue_run(env.db, env.ingest_id, SMALL)
    with pytest.raises(ArtifactMissing, match="No existe la ejecución '9'"):
        repo.list_sku_selections("9")


def test_the_filters_are_validated(env: Env) -> None:
    repo = env.repo()
    with pytest.raises(InvalidQuery):
        repo.list_sku_selections(RUN, sku_class="Smooth")
    with pytest.raises(InvalidQuery):
        repo.list_sku_selections(RUN, sort="price")
    with pytest.raises(InvalidQuery):
        repo.list_sku_selections(RUN, estado="completada")


def test_a_database_before_migration_0004_reads_as_no_selection(tmp_path: Path) -> None:
    old = tmp_path / "v3.db"
    with closing(sqlite3.connect(old)) as conn:
        apply_migrations(conn, load_migrations()[:3])
        conn.execute(
            "INSERT INTO ingestas (sha256, nombre_archivo, filas, skus) VALUES (?, 'f', 1, 1)",
            ("a" * 64,),
        )
        conn.execute(
            "INSERT INTO configuraciones (version, parametros) VALUES (1, ?)",
            (
                json.dumps(
                    {"min_train": 4, "horizon": 1, "step": 1, "metric": "mae", "seasonality": 1}
                ),
            ),
        )
        conn.execute("INSERT INTO ejecuciones (ingesta_id, configuracion_id, seed) VALUES (1,1,0)")
        conn.commit()

    repo = DalRepository(old)
    assert repo.list_sku_selections(RUN).availability.reason_code == "prerequisite_missing"
    with pytest.raises(ArtifactMissing):
        repo.get_sku_selection(RUN, "FIL-0001")
    # And the migration brings it forward without losing the run.
    with closing(sqlite3.connect(old)) as conn:
        apply_migrations(conn, load_migrations())
        assert conn.execute("PRAGMA user_version").fetchone() == (4,)
        assert conn.execute("SELECT COUNT(*) FROM ejecuciones").fetchone() == (1,)


def test_the_capabilities_of_the_selection_are_available() -> None:
    for name in (
        "selection_results_persisted",
        "trial_detail_persisted",
        "walkforward_evidence_persisted",
        "family_comparison",
    ):
        assert capabilities.REGISTRY[name].available  # type: ignore[index]
    assert not capabilities.REGISTRY["champion_selection"].available
    assert not capabilities.REGISTRY["retrospective_validation"].available
