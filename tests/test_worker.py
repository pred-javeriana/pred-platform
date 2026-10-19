"""The worker (G3): launch, follow, settle and resume runs, with a stand-in engine.

``tests/fake_engine.py`` plays ``pred-engine run``: same command line, same files. Scenarios are
chosen with ``FAKE_ENGINE_SCENARIO``.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import sys
import threading
import time
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator

from pred_platform.app.main import create_app
from pred_platform.config import Settings
from pred_platform.contract.errors import ArtifactMissing
from pred_platform.contract.resources import read_schema
from pred_platform.dal import ejecuciones as dal
from pred_platform.dal.importacion import import_reports
from pred_platform.dal.schema import create_db
from pred_platform.data.dal import DalRepository
from pred_platform.worker import engine
from pred_platform.worker.__main__ import main as worker_main
from pred_platform.worker.bucle import Worker
from pred_platform.worker.errores import error_info, exclusion_info, exclusion_reason
from pred_platform.worker.progreso import ProgressFollower
from pred_platform.worker.service import (
    LaunchSettings,
    RunRequestError,
    enqueue_run,
    run_id_for,
)

_FIXTURES = Path(__file__).parent / "fixtures" / "motor"
_FAKE = Path(__file__).parent / "fake_engine.py"
SMALL = LaunchSettings(min_train=40, horizon=2, step=14, metric="mae", trials=1, workers=2, seed=17)


@dataclass
class Env:
    settings: Settings
    ingest_id: str

    @property
    def db(self) -> Path:
        return self.settings.db_path

    def worker(self) -> Worker:
        return Worker(self.settings, poll_interval=0.05)

    def repo(self) -> DalRepository:
        return DalRepository(self.settings.db_path, self.settings.data_root)

    def rows(self, sql: str, *params: object) -> list[tuple[Any, ...]]:
        with closing(sqlite3.connect(self.db)) as conn:
            return conn.execute(sql, params).fetchall()


def _fixture_id(label: str) -> str:
    return json.loads((_FIXTURES / f"ingesta.{label}.json").read_text())["ingest_id"]


@pytest.fixture
def env(tmp_path: Path) -> Env:
    root = tmp_path / "data"
    (root / "ingestas").mkdir(parents=True)
    for label in ("accepted", "failed"):
        shutil.copyfile(
            _FIXTURES / f"ingesta.{label}.json", root / "ingestas" / f"{_fixture_id(label)}.json"
        )
    (root / "processed").mkdir()
    (root / "processed" / "demo_canonico.parquet").write_bytes(b"")
    db = tmp_path / "pred.db"
    create_db(db).close()
    assert import_reports(db, root).ok
    settings = Settings(
        data_root=root,
        db_path=db,
        engine_command=(sys.executable, str(_FAKE)),
        worker="off",
    )
    return Env(settings, _fixture_id("accepted"))


def _scenario(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    monkeypatch.setenv("FAKE_ENGINE_SCENARIO", name)


def _wait(condition: Callable[[], bool], *, timeout: float = 20.0, what: str = "") -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(0.05)
    raise AssertionError(f"timed out waiting for {what or 'condition'}")


def _run_state(env: Env) -> tuple[str, str | None]:
    row = env.rows("SELECT estado, error_json FROM ejecuciones ORDER BY id DESC LIMIT 1")[0]
    return row[0], row[1]


# ------------------------------------------------------------------------- enqueue
def test_a_run_is_queued_with_its_parameters(env: Env) -> None:
    queued = enqueue_run(env.db, env.ingest_id, SMALL)

    assert queued.queued is True and queued.run_id == run_id_for(env.ingest_id, SMALL)
    ((estado, run_id, seed),) = env.rows("SELECT estado, run_id, seed FROM ejecuciones")
    assert (estado, run_id, seed) == ("pendiente", queued.run_id, 17)
    parametros = json.loads(env.rows("SELECT parametros FROM configuraciones")[0][0])
    assert parametros["schema_version"] == 1
    assert (parametros["min_train"], parametros["metric"], parametros["trials"]) == (40, "mae", 1)
    assert parametros["families"] == ["classical", "ml", "dl"]


def test_asking_again_for_the_same_run_returns_the_existing_one(env: Env) -> None:
    first = enqueue_run(env.db, env.ingest_id, SMALL)
    second = enqueue_run(env.db, env.ingest_id, SMALL)

    assert second.ejecucion_id == first.ejecucion_id and second.queued is False
    assert env.rows("SELECT COUNT(*) FROM ejecuciones") == [(1,)]


def test_the_number_of_processes_does_not_change_the_run_identity() -> None:
    a = LaunchSettings(workers=1)
    b = LaunchSettings(workers=8)
    assert run_id_for("a" * 64, a) == run_id_for("a" * 64, b)
    assert run_id_for("a" * 64, a) != run_id_for("a" * 64, LaunchSettings(metric="mae"))


@pytest.mark.parametrize("ingest_id", ["c" * 64, _fixture_id("failed")])
def test_only_an_accepted_ingestion_can_be_run(env: Env, ingest_id: str) -> None:
    with pytest.raises(RunRequestError):
        enqueue_run(env.db, ingest_id, SMALL)
    assert env.rows("SELECT COUNT(*) FROM ejecuciones") == [(0,)]


@pytest.mark.parametrize(
    "kwargs",
    [{"min_train": 0}, {"metric": "mape"}, {"families": ()}, {"trials": 0}, {"workers": 0}],
)
def test_launch_settings_are_validated(kwargs: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        LaunchSettings(**kwargs)


# ------------------------------------------------------------------------ a full run
def test_a_run_ends_with_all_its_tasks_done(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    _scenario(monkeypatch, "ok")
    queued = enqueue_run(env.db, env.ingest_id, SMALL)

    assert env.worker().run_next() is True

    ((estado, pid, ruta, reserva, error, intento),) = env.rows(
        "SELECT estado, pid, ruta_corrida, reserva_json, error_json, intento FROM ejecuciones"
    )
    assert (estado, pid, error, intento) == ("completada", None, None, 1)
    assert ruta == f"runs/{queued.run_id}"
    assert json.loads(reserva)["t_star"] == "2026-03-13"
    tasks = env.rows("SELECT sku, modelo, corte, estado, tiempo_pared_s FROM tareas ORDER BY id")
    assert len(tasks) == 9  # 3 SKU x 3 families
    assert {t[3] for t in tasks} == {"exitosa"}
    assert {t[2] for t in tasks} == {"2026-03-13"}  # the reserve cut t*, decision D6
    assert {t[1] for t in tasks} == {"classical:sarima", "ml:lightgbm", "dl:mlp"}
    assert {t[4] for t in tasks} == {2.0}  # 1.5 s in L2 + 0.5 s in L3


def test_a_failed_unit_does_not_stop_the_others(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    _scenario(monkeypatch, "unit_fails")
    enqueue_run(env.db, env.ingest_id, SMALL)

    env.worker().run_next()

    estado, _ = _run_state(env)
    assert estado == "completada_con_fallos"
    counts = dict(env.rows("SELECT estado, COUNT(*) FROM tareas GROUP BY estado"))
    assert counts == {"exitosa": 8, "fallida": 1}
    ((error_json,),) = env.rows("SELECT detalle_error FROM tareas WHERE estado = 'fallida'")
    error = json.loads(error_json)
    assert (error["code"], error["stage"]) == ("model.fit_failed", "L2")
    assert error["detail"] == "el modelo no converge"
    assert error["source_exception"] == "AjusteModeloError"


def test_a_skipped_sku_becomes_one_not_runnable_task_per_family(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _scenario(monkeypatch, "excluded")
    enqueue_run(env.db, env.ingest_id, SMALL)

    env.worker().run_next()

    assert _run_state(env)[0] == "completada_con_fallos"
    skipped = env.rows(
        "SELECT sku, modelo, detalle_error FROM tareas "
        "WHERE estado = 'no_ejecutable' ORDER BY modelo"
    )
    assert [(s[0], s[1]) for s in skipped] == [
        ("FIL-0003", "classical:sarima"),
        ("FIL-0003", "dl:mlp"),
        ("FIL-0003", "ml:lightgbm"),
    ]
    assert json.loads(skipped[0][2])["code"] == "eligibility.no_reserved_observations"


# ------------------------------------------------------------------- engine problems
def test_an_engine_that_dies_leaves_a_stopped_run_and_no_task_in_flight(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _scenario(monkeypatch, "crash")
    enqueue_run(env.db, env.ingest_id, SMALL)

    env.worker().run_next()

    estado, error_json = _run_state(env)
    assert estado == "detenida"
    assert json.loads(error_json or "{}")["code"] == "platform.artifact_missing"
    states = {r[0] for r in env.rows("SELECT estado FROM tareas")}
    assert states <= {"exitosa", "fallida", "no_ejecutable"}
    assert "fallida" in states or "no_ejecutable" in states
    assert env.rows("SELECT COUNT(*) FROM tareas WHERE estado IN ('pendiente','ejecutando')") == [
        (0,)
    ]


def test_a_stage_without_results_stops_the_run(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    _scenario(monkeypatch, "stage_fails")
    enqueue_run(env.db, env.ingest_id, SMALL)

    env.worker().run_next()

    estado, error_json = _run_state(env)
    assert estado == "detenida"
    error = json.loads(error_json or "{}")
    assert error["code"] == "pipeline.stage_failed" and "L3" in error["detail"]


def test_a_missing_engine_command_is_a_clear_stopped_run(env: Env) -> None:
    broken = Settings(
        data_root=env.settings.data_root,
        db_path=env.db,
        engine_command=("/no/such/pred-engine",),
        worker="off",
    )
    enqueue_run(env.db, env.ingest_id, SMALL)

    Worker(broken).run_next()

    estado, error_json = _run_state(env)
    error = json.loads(error_json or "{}")
    assert estado == "detenida"
    assert (error["code"], error["retryable"]) == ("platform.engine_unavailable", True)
    assert "/no/such/pred-engine" in error["detail"]


def test_an_unsupported_progress_version_stops_the_run(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _scenario(monkeypatch, "future_schema")
    enqueue_run(env.db, env.ingest_id, SMALL)

    env.worker().run_next()

    estado, error_json = _run_state(env)
    assert estado == "detenida"
    assert json.loads(error_json or "{}")["code"] == "platform.schema_version_unsupported"


# ------------------------------------------------------------------ live and resume
def test_the_run_can_be_followed_while_it_runs(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    _scenario(monkeypatch, "slow_ok")
    enqueue_run(env.db, env.ingest_id, SMALL)
    thread = threading.Thread(target=env.worker().run_next)
    thread.start()
    seen: set[str] = set()
    try:

        def _in_flight() -> bool:
            seen.update(r[0] for r in env.rows("SELECT DISTINCT estado FROM tareas"))
            return {"pendiente", "ejecutando"} <= seen

        _wait(_in_flight, what="tasks pending and running at the same time")
        status = env.repo().get_run_status("1")
        assert status.ejecucion is not None and status.ejecucion.estado == "ejecutando"
        assert status.progress is not None and 0 <= status.progress.avance_pct < 100
    finally:
        thread.join(timeout=30)
    assert _run_state(env)[0] == "completada"
    assert env.repo().get_run_status("1").progress.avance_pct == 100.0  # type: ignore[union-attr]


def test_closing_the_platform_stops_the_engine_and_marks_the_run(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _scenario(monkeypatch, "slow")
    enqueue_run(env.db, env.ingest_id, SMALL)
    stop = threading.Event()
    thread = threading.Thread(target=env.worker().run_next, args=(stop,))
    thread.start()
    _wait(
        lambda: env.rows("SELECT COUNT(*) FROM tareas WHERE estado = 'ejecutando'")[0][0] > 0,
        what="a unit in flight",
    )
    pid = env.rows("SELECT pid FROM ejecuciones")[0][0]

    stop.set()
    thread.join(timeout=20)

    estado, error_json = _run_state(env)
    assert estado == "detenida"
    assert json.loads(error_json or "{}")["retryable"] is True
    assert not engine.is_alive(pid)
    assert env.rows("SELECT COUNT(*) FROM tareas WHERE estado IN ('pendiente','ejecutando')") == [
        (0,)
    ]


def test_resuming_a_stopped_run_reuses_its_rows(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    _scenario(monkeypatch, "crash")
    first = enqueue_run(env.db, env.ingest_id, SMALL)
    env.worker().run_next()
    assert _run_state(env)[0] == "detenida"

    _scenario(monkeypatch, "ok")
    again = enqueue_run(env.db, env.ingest_id, SMALL)
    assert (again.ejecucion_id, again.queued) == (first.ejecucion_id, True)
    env.worker().run_next()

    ((estado, intento),) = env.rows("SELECT estado, intento FROM ejecuciones")
    assert (estado, intento) == ("completada", 2)
    assert env.rows("SELECT COUNT(*) FROM ejecuciones") == [(1,)]
    assert env.rows("SELECT COUNT(*), COUNT(DISTINCT sku || modelo) FROM tareas") == [(9, 9)]
    assert env.rows("SELECT DISTINCT estado FROM tareas") == [("exitosa",)]


def test_a_run_left_running_by_a_crash_is_queued_again_and_finishes(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _scenario(monkeypatch, "ok")
    enqueue_run(env.db, env.ingest_id, SMALL)
    with closing(dal.connect(env.db)) as conn:
        assert dal.claim_pending(conn) is not None  # "running", but nothing is running it
    assert _run_state(env)[0] == "ejecutando"

    worker = env.worker()
    worker.recover()
    assert _run_state(env)[0] == "pendiente"
    worker.run_next()

    assert _run_state(env)[0] == "completada"


def test_two_workers_never_claim_the_same_run(env: Env) -> None:
    enqueue_run(env.db, env.ingest_id, SMALL)
    barrier = threading.Barrier(2)
    claimed: list[object] = []

    def claim() -> None:
        with closing(dal.connect(env.db)) as conn:
            barrier.wait()
            claimed.append(dal.claim_pending(conn))

    threads = [threading.Thread(target=claim) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(c is not None for c in claimed) == [False, True]


def test_applying_the_progress_twice_leaves_the_same_tasks(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _scenario(monkeypatch, "unit_fails")
    queued = enqueue_run(env.db, env.ingest_id, SMALL)
    env.worker().run_next()
    before = env.rows(
        "SELECT sku, modelo, estado, tiempo_pared_s, detalle_error FROM tareas ORDER BY id"
    )
    run_path = env.settings.data_root / "runs" / queued.run_id / "progreso.jsonl"

    with closing(dal.connect(env.db)) as conn:
        run = dal.get_run(conn, 1)
        assert run is not None and run.t_star is not None
        follower = ProgressFollower(conn, run, run_path, corte=run.t_star)
        assert follower.poll() > 0

    after = env.rows(
        "SELECT sku, modelo, estado, tiempo_pared_s, detalle_error FROM tareas ORDER BY id"
    )
    assert after == before


# ----------------------------------------------------------------- reading the run
def _conforms(document: Any) -> None:
    validator = Draft202012Validator(
        read_schema("run_status"), format_checker=Draft202012Validator.FORMAT_CHECKER
    )
    data = json.loads(document.model_dump_json(exclude_none=True))
    assert [e.message for e in validator.iter_errors(data)] == []


def test_the_repository_returns_a_valid_run_status(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _scenario(monkeypatch, "unit_fails")
    queued = enqueue_run(env.db, env.ingest_id, SMALL)
    env.worker().run_next()

    status = env.repo().get_run_status("1")

    assert status.source == "dal" and status.availability.status == "available"
    assert status.ejecucion is not None
    assert (status.ejecucion.estado, status.ejecucion.ingest_id) == (
        "completada_con_fallos",
        env.ingest_id,
    )
    assert status.settings is not None
    assert (status.settings.min_train, status.settings.metric) == (40, "mae")
    assert status.reserve is not None and status.reserve.t_star == "2026-03-13"
    assert status.progress is not None
    assert status.progress.tareas_total == 9
    assert status.progress.tareas_por_estado["exitosa"] == 8
    assert status.progress.tareas_por_estado["fallida"] == 1
    assert status.progress.avance_pct == 100.0
    failed = [t for t in status.tasks if t.estado == "fallida"]
    assert failed[0].error is not None and failed[0].error.code == "model.fit_failed"
    assert failed[0].family == "ml"
    _conforms(status)
    assert env.repo().get_run_status(queued.run_id).ejecucion == status.ejecucion


def test_run_status_filters_and_pages_in_sql_but_keeps_the_whole_progress(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _scenario(monkeypatch, "unit_fails")
    enqueue_run(env.db, env.ingest_id, SMALL)
    env.worker().run_next()
    repo = env.repo()

    only_failed = repo.get_run_status("1", estado="fallida")
    assert [(t.sku, t.modelo) for t in only_failed.tasks] == [("FIL-0002", "ml:lightgbm")]
    assert only_failed.page is not None and only_failed.page.total == 1
    assert only_failed.progress is not None and only_failed.progress.tareas_total == 9

    by_sku = repo.get_run_status("1", sku="FIL-0001")
    assert {t.sku for t in by_sku.tasks} == {"FIL-0001"} and len(by_sku.tasks) == 3
    by_family = repo.get_run_status("1", family="dl")
    assert {t.family for t in by_family.tasks} == {"dl"}

    second = repo.get_run_status("1", size=4, page=2)
    assert len(second.tasks) == 4 and second.page is not None and second.page.total == 9


def test_no_run_yet_is_an_empty_state_and_an_unknown_run_is_missing(env: Env) -> None:
    empty = env.repo().get_run_status("1")
    assert empty.availability.status == "unavailable"
    assert empty.availability.reason_code == "no_data"
    assert empty.tasks == []
    _conforms(empty)

    enqueue_run(env.db, env.ingest_id, SMALL)
    with pytest.raises(ArtifactMissing):
        env.repo().get_run_status("999")


def test_the_capability_of_orchestration_is_available(env: Env) -> None:
    by_id = {c.id: c for c in env.repo().get_capabilities().items}
    assert by_id["run_orchestration"].available is True


# ------------------------------------------------------------- errors and the CLI
@pytest.mark.parametrize(
    ("stage", "text", "code"),
    [
        ("L2", "AjusteModeloError: no converge", "model.fit_failed"),
        ("L3", "VentanaInsuficienteError: pocas ventanas", "model.series_too_short"),
        ("L2", "SelectionContractError: ningun trial de HPO completo", "selection.no_viable_trial"),
        ("L3", "RuntimeError: algo raro", "evaluation.failed"),
        ("L2", "RuntimeError: algo raro", "pipeline.stage_failed"),
        ("L2", None, "pipeline.stage_failed"),
    ],
)
def test_error_codes_come_from_the_exception_class(stage: str, text: str | None, code: str) -> None:
    info = error_info(stage, text)
    assert info["code"] == code and info["stage"] == stage and info["severity"] == "error"


def test_exclusion_codes_fall_back_to_the_cause_text_for_older_runs() -> None:
    assert exclusion_info("la serie termina antes de t*")["code"] == "model.series_too_short"
    assert exclusion_info("calendario diario con huecos")["code"] == "ingest.temporal_continuity"
    assert exclusion_info("otra cosa")["code"] == "pipeline.stage_failed"


def test_the_engine_code_wins_over_the_exception_class_and_the_text() -> None:
    info = error_info("L2", "RuntimeError: algo raro", "model.fit_failed")
    assert info["code"] == "model.fit_failed" and info["retryable"] is True
    # The text says nothing useful: only the code decides.
    exclusion = exclusion_info("causa libre", "eligibility.calendar_gaps")
    assert exclusion["code"] == "eligibility.calendar_gaps" and exclusion["retryable"] is False
    assert exclusion["detail"] == "causa libre" and exclusion["stage"] == "L2"


def test_a_malformed_engine_code_is_ignored_not_stored() -> None:
    assert error_info("L2", "RuntimeError: x", "No Es Un Codigo")["code"] == "pipeline.stage_failed"
    assert error_info("L3", "RuntimeError: x", "")["code"] == "evaluation.failed"
    assert exclusion_info("calendario con huecos", "X.Y")["code"] == "ingest.temporal_continuity"


@pytest.mark.parametrize(
    ("code", "reason"),
    [
        ("eligibility.no_admissible_history", "series_too_short"),
        ("eligibility.no_reserved_observations", "series_too_short"),
        ("eligibility.insufficient_history", "series_too_short"),
        ("eligibility.calendar_gaps", "execution_failed"),
        ("eligibility.non_finite_demand", "execution_failed"),
    ],
)
def test_the_reason_code_comes_from_the_engine_code(code: str, reason: str) -> None:
    assert exclusion_reason("texto que no se lee", code) == reason


def test_the_reason_code_falls_back_to_the_text_without_a_code() -> None:
    assert exclusion_reason("la serie termina antes de t*") == "series_too_short"
    assert exclusion_reason("calendario diario con huecos") == "execution_failed"


def test_the_cli_launches_runs_and_reports_status(
    env: Env, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _scenario(monkeypatch, "ok")
    monkeypatch.setenv("PRED_DATA_ROOT", str(env.settings.data_root))
    monkeypatch.setenv("PRED_DB_PATH", str(env.db))
    monkeypatch.setenv("PRED_ENGINE_CMD", f"{sys.executable} {_FAKE}")

    code = worker_main(["--launch", "--ingest", env.ingest_id, "--min-train", "40", "--once"])

    out = capsys.readouterr().out
    assert code == 0 and "en cola" in out
    assert "completada" in out and "9 exitosa" in out
    assert worker_main(["--status"]) == 0
    assert "completada" in capsys.readouterr().out


def test_the_cli_rejects_a_launch_without_a_valid_ingestion(
    env: Env, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("PRED_DATA_ROOT", str(env.settings.data_root))
    monkeypatch.setenv("PRED_DB_PATH", str(env.db))

    assert worker_main(["--launch"]) == 2
    assert worker_main(["--launch", "--ingest", "c" * 64]) == 1
    assert "no existe la ingesta" in capsys.readouterr().err


# -------------------------------------------------------------------- the app
def test_the_app_runs_the_worker_as_a_thread_and_stops_it(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _scenario(monkeypatch, "ok")
    settings = Settings(
        data_root=env.settings.data_root,
        db_path=env.db,
        engine_command=(sys.executable, str(_FAKE)),
        worker="embedded",
    )
    enqueue_run(env.db, env.ingest_id, SMALL)

    with TestClient(create_app(settings)) as client:
        _wait(lambda: _run_state(env)[0] == "completada", what="the embedded worker to finish")
        assert client.get("/health").status_code == 200

    assert not [t for t in threading.enumerate() if t.name == "pred-worker"]


def test_a_worker_thread_is_not_started_with_the_worker_off(env: Env) -> None:
    with TestClient(create_app(env.settings)):
        assert not [t for t in threading.enumerate() if t.name == "pred-worker"]
