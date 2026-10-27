"""The worker (G3): launch, follow, settle and resume runs, with a stand-in engine.

``tests/fake_engine.py`` plays ``pred-engine run``: same command line, same files. Scenarios are
chosen with ``FAKE_ENGINE_SCENARIO``.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import os
import queue
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator
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
from pred_platform.worker.bucle import Launcher, Worker
from pred_platform.worker.errores import (
    error_info,
    exclusion_info,
    exclusion_reason,
    run_stopped_error,
)
from pred_platform.worker.importacion import finalize
from pred_platform.worker.progreso import ProgressFollower
from pred_platform.worker.service import (
    LaunchSettings,
    RunRequestError,
    enqueue_run,
    run_id_for,
)

_FIXTURES = Path(__file__).parent / "fixtures" / "motor"
_FAKE = Path(__file__).parent / "fake_engine.py"
SMALL = LaunchSettings(
    min_train=40,
    horizon=2,
    step=14,
    metric="mae",
    trials=1,
    workers=2,
    seed=17,
)


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
    accepted = root / "ingestas" / f"{_fixture_id('accepted')}.json"
    report = json.loads(accepted.read_text())
    report["source_file"]["datos_sinteticos"] = False
    report["published"]["sha256"] = (
        "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    )
    accepted.write_text(json.dumps(report))
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


def test_la_configuracion_de_lanzamiento_no_admite_origen() -> None:
    with pytest.raises(TypeError):
        LaunchSettings(datos_sinteticos=True)  # type: ignore[call-arg]
    assert LaunchSettings().to_parametros()["datos_sinteticos"] is False


def test_id_publico_no_cambia_al_resolver_procedencia_de_m1(env):
    settings = LaunchSettings(families=("ml",), trials=1)
    expected = run_id_for(env.ingest_id, settings)
    queued = enqueue_run(env.db, env.ingest_id, settings)
    assert queued.run_id == expected
    with closing(dal.connect(env.db)) as conn:
        run = dal.get_run(conn, queued.ejecucion_id)
    assert run is not None and run.parametros["datos_sinteticos"] is False


@pytest.mark.parametrize("known", [True, False])
def test_trabajador_resuelve_configuracion_antigua_antes_de_lanzar(env, known):
    queued = enqueue_run(env.db, env.ingest_id, SMALL)
    with closing(dal.connect(env.db)) as conn:
        cfg, raw = conn.execute(
            "SELECT c.id,c.parametros FROM configuraciones c "
            "JOIN ejecuciones e ON e.configuracion_id=c.id WHERE e.id=?",
            (queued.ejecucion_id,),
        ).fetchone()
        params = json.loads(raw)
        params.pop("datos_sinteticos")
        conn.execute(
            "UPDATE configuraciones SET parametros=? WHERE id=?", (json.dumps(params), cfg)
        )
        if not known:
            conn.execute("DELETE FROM m3_ingestas")
        conn.commit()
    assert env.worker().run_next() is True
    state, error, pid = env.rows("SELECT estado,error_json,pid FROM ejecuciones")[0]
    if known:
        assert state == "completada" and error is None
        assert (
            json.loads(env.rows("SELECT parametros FROM configuraciones WHERE id=?", cfg)[0][0])[
                "datos_sinteticos"
            ]
            is False
        )
    else:
        assert state == "detenida" and pid is None
        assert "encolar" in json.loads(error)["detail"]
        assert not (env.settings.data_root / "runs" / queued.run_id / "motor.log").exists()


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
    [
        {"min_train": 0},
        {"metric": "mape"},
        {"families": ()},
        {"trials": 0},
        {"workers": 0},
        {"aggregation": "media"},
        {"trim": 0.2},
    ],
)
def test_launch_settings_are_validated(kwargs: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        LaunchSettings(**kwargs)


def test_a_queued_run_records_the_settings_the_engine_uses(env: Env) -> None:
    enqueue_run(env.db, env.ingest_id, SMALL)

    parametros = json.loads(env.rows("SELECT parametros FROM configuraciones")[0][0])
    assert (parametros["aggregation"], parametros["trim"]) == ("media_recortada", 0.1)
    with pytest.raises(ValueError, match="media_recortada"):
        LaunchSettings(aggregation="media", trim=0.2)
    assert env.rows("SELECT COUNT(*) FROM ejecuciones") == [(1,)]  # nothing else was queued


def _make_legacy(env: Env) -> None:
    """Leave the stored settings as a platform that queued ``media`` / 0.2 left them."""
    with closing(sqlite3.connect(env.db)) as conn:
        conn.execute(
            "UPDATE configuraciones SET parametros = json_set(parametros, '$.aggregation', "
            "'media', '$.trim', 0.2)"
        )
        conn.commit()


def test_a_legacy_run_with_unsupported_settings_is_stopped_before_the_engine(env: Env) -> None:
    enqueue_run(env.db, env.ingest_id, SMALL)
    _make_legacy(env)
    launched: list[list[str]] = []

    def launcher(command: list[str], **kwargs: Any) -> Any:
        launched.append(command)
        return engine.launch(command, **kwargs)

    Worker(env.settings, launcher=launcher).run_next()

    estado, error_json = _run_state(env)
    error = json.loads(error_json or "{}")
    assert (estado, error["code"], error["retryable"]) == (
        "detenida",
        "platform.contract_invalid",
        False,
    )
    assert "media_recortada" in error["detail"]
    assert launched == []


def test_a_legacy_run_with_task_history_ends_with_all_its_tasks_settled(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _scenario(monkeypatch, "unit_fails")
    enqueue_run(env.db, env.ingest_id, SMALL)
    env.worker().run_next()
    assert _run_state(env)[0] == "completada_con_fallos"
    _make_legacy(env)
    with closing(dal.connect(env.db)) as conn:
        assert dal.resume_run(conn, 1)  # a pre-upgrade queue entry, as the old resume left it
    launched: list[list[str]] = []

    def launcher(command: list[str], **kwargs: Any) -> Any:
        launched.append(command)
        return engine.launch(command, **kwargs)

    assert Worker(env.settings, launcher=launcher).run_next() is True

    tasks = env.rows("SELECT estado, detalle_error FROM tareas")
    assert tasks and {estado for estado, _ in tasks} <= {"exitosa", "no_ejecutable"}
    settled = [json.loads(detail) for estado, detail in tasks if estado == "no_ejecutable"]
    assert settled and {e["code"] for e in settled} == {"platform.contract_invalid"}
    assert env.rows("SELECT pid FROM ejecuciones") == [(None,)]
    assert launched == []
    before = (env.rows("SELECT * FROM tareas"), env.rows("SELECT * FROM ejecuciones"))
    assert Worker(env.settings, launcher=launcher).run_next() is False  # nothing left to claim
    assert (env.rows("SELECT * FROM tareas"), env.rows("SELECT * FROM ejecuciones")) == before


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
def test_subsecond_summary_rewrite_is_not_confused_with_an_old_attempt(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _scenario(monkeypatch, "ok")
    queued = enqueue_run(env.db, env.ingest_id, SMALL)
    env.worker().run_next()
    folder = env.settings.data_root / "runs" / queued.run_id
    current = (folder / "corrida.json").stat()
    # stat_result compara su tupla con tiempos enteros; su atributo ns conserva
    # la diferencia que identifica dos escrituras dentro del mismo segundo.
    previous = os.stat_result(current, {"st_mtime_ns": current.st_mtime_ns - 1})
    assert previous == current and previous.st_mtime_ns != current.st_mtime_ns
    with closing(dal.connect(env.db)) as conn:
        run = dal.get_run(conn, queued.ejecucion_id)
        assert run is not None and run.t_star is not None
        assert (
            finalize(
                conn,
                run,
                run_dir=folder,
                returncode=7,
                corte=run.t_star,
                families=["classical", "ml", "dl"],
                log_path=folder / "motor.log",
                previous_summary=previous,
            )
            == "completada"
        )


def test_retry_that_crashes_cannot_import_the_previous_attempt_as_its_final_result(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _scenario(monkeypatch, "unit_fails")
    queued = enqueue_run(env.db, env.ingest_id, SMALL)
    env.worker().run_next()
    folder = env.settings.data_root / "runs" / queued.run_id
    previous = (folder / "corrida.json").read_bytes()
    with closing(dal.connect(env.db)) as conn:
        assert dal.resume_run(conn, queued.ejecucion_id)
    _scenario(monkeypatch, "crash")
    env.worker().run_next()
    state, error = _run_state(env)
    assert state == "detenida"
    assert error is not None
    assert json.loads(error)["code"] == "platform.artifact_missing"
    assert (folder / "corrida.json").read_bytes() == previous
    assert env.rows("SELECT COUNT(*) FROM tareas WHERE estado IN ('pendiente','ejecutando')") == [
        (0,)
    ]


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


def test_a_fatal_l4_stops_the_run_but_keeps_tasks_and_selection(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _scenario(monkeypatch, "l4_fails")
    enqueue_run(env.db, env.ingest_id, SMALL)

    env.worker().run_next()

    estado, error_json = _run_state(env)
    assert estado == "detenida"
    error = json.loads(error_json or "{}")
    assert error["code"] == "pipeline.stage_failed"
    assert "L4" in error["detail"]
    # The successful L2/L3 tasks stay stored.
    counts = dict(env.rows("SELECT estado, COUNT(*) FROM tareas GROUP BY estado"))
    assert counts.get("exitosa", 0) == 9  # 3 SKU × 3 families
    assert counts.get("fallida", 0) == 0
    # The per-SKU selection (G4) was imported and linked.
    assert env.rows("SELECT COUNT(*) FROM selecciones")[0][0] > 0


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


def test_closing_the_platform_reaps_the_engine_and_keeps_the_run_queued(
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
    assert estado == "pendiente" and error_json is None
    assert not engine.is_alive(pid)
    assert env.rows("SELECT COUNT(*) FROM tareas WHERE estado = 'ejecutando'") == [(0,)]
    _scenario(monkeypatch, "ok")
    env.worker().run_next()
    assert _run_state(env)[0] == "completada"


@pytest.mark.parametrize("state", ["completada", "completada_con_fallos"])
def test_engine_publication_before_cli_exit_is_not_an_operator_stop(
    env: Env, monkeypatch: pytest.MonkeyPatch, state: str
) -> None:
    _scenario(monkeypatch, "publishes_before_exit")
    monkeypatch.setenv("FAKE_ENGINE_TERMINAL_STATE", state)
    queued = enqueue_run(env.db, env.ingest_id, SMALL)
    env.worker().run_next()
    assert _run_state(env) == ("completada", None)
    assert env.rows("SELECT pid FROM ejecuciones") == [(None,)]
    assert env.rows("SELECT COUNT(*) FROM selecciones")[0][0] == 3
    assert (env.settings.data_root / "runs" / queued.run_id / "corrida.json").is_file()


def test_resumed_follower_ignores_history_but_applies_events_written_after_its_creation(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _scenario(monkeypatch, "unit_fails")
    queued = enqueue_run(env.db, env.ingest_id, SMALL)
    env.worker().run_next()
    path = env.settings.data_root / "runs" / queued.run_id / "progreso.jsonl"
    with closing(dal.connect(env.db)) as conn:
        assert dal.resume_run(conn, queued.ejecucion_id)
        run = dal.get_run(conn, queued.ejecucion_id)
        assert run is not None and run.t_star is not None
        follower = ProgressFollower(conn, run, path, corte=run.t_star, skip_history=True)
        before = env.rows("SELECT * FROM tareas ORDER BY id")
        assert follower.poll() == 0
        assert env.rows("SELECT * FROM tareas ORDER BY id") == before
        with path.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {
                        "event": "unit_started",
                        "stage": "L2",
                        "sku_id": "FIL-0002",
                        "family": "ml",
                        "model": "lightgbm",
                        "started_at": dal.now_iso(),
                    }
                )
                + "\n"
            )
        assert follower.poll() == 1
    assert env.rows("SELECT estado FROM tareas WHERE sku='FIL-0002' AND modelo='ml:lightgbm'") == [
        ("ejecutando",)
    ]
    assert env.rows("SELECT COUNT(*) FROM tareas WHERE estado='exitosa'") == [(8,)]


def test_final_trace_import_never_marks_completed_work_running_again(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _scenario(monkeypatch, "ok")
    enqueue_run(env.db, env.ingest_id, SMALL)
    original = dal.task_started
    regressions = []

    def observed(conn, run_id, **kwargs):
        key = (run_id, kwargs["sku"], kwargs["modelo"])
        before = conn.execute(
            "SELECT estado FROM tareas WHERE ejecucion_id=? AND sku=? AND modelo=?", key
        ).fetchone()
        original(conn, run_id, **kwargs)
        after = conn.execute(
            "SELECT estado FROM tareas WHERE ejecucion_id=? AND sku=? AND modelo=?", key
        ).fetchone()
        if before and before[0] == "exitosa" and after[0] != "exitosa":
            regressions.append(key)

    monkeypatch.setattr(dal, "task_started", observed)
    env.worker().run_next()
    assert _run_state(env)[0] == "completada" and regressions == []


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


@pytest.mark.parametrize("state", ["completada", "completada_con_fallos"])
def test_recovery_after_m3_publication_only_requeues_unreaped_claims(
    env: Env, monkeypatch: pytest.MonkeyPatch, state: str
) -> None:
    _scenario(monkeypatch, "ok")
    enqueue_run(env.db, env.ingest_id, SMALL)
    env.worker().run_next()
    env.worker().recover()
    assert _run_state(env)[0] == "completada"
    with closing(dal.connect(env.db)) as conn, conn:
        conn.execute("UPDATE ejecuciones SET estado=?, pid=42424242", (state,))
    monkeypatch.setattr(engine, "is_alive", lambda pid: False)
    env.worker().recover()
    assert _run_state(env)[0] == "pendiente"
    assert env.rows("SELECT COUNT(*) FROM tareas WHERE estado='exitosa'") == [(9,)]
    assert env.rows("SELECT pid FROM ejecuciones") == [(None,)]
    env.worker().run_next()
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


def _escribir_progreso(env: Env, run_id: str, *eventos: dict[str, Any]) -> None:
    ruta = env.settings.data_root / "runs" / run_id / "progreso.jsonl"
    ruta.parent.mkdir(parents=True, exist_ok=True)
    with ruta.open("a", encoding="utf-8") as archivo:
        archivo.writelines(json.dumps(evento) + "\n" for evento in eventos)


def test_el_estado_de_la_ejecucion_informa_la_etapa_en_curso(env: Env) -> None:
    queued = enqueue_run(env.db, env.ingest_id, SMALL)
    with closing(dal.connect(env.db)) as conn:
        run = dal.claim_pending(conn)
        assert run is not None
        dal.set_launch(conn, run.id, pid=os.getpid(), ruta_corrida=f"runs/{run.run_id}")

    def etapa() -> str | None:
        progress = env.repo().get_run_status(queued.run_id).progress
        assert progress is not None
        return progress.etapa_actual

    _escribir_progreso(
        env,
        queued.run_id,
        {"event": "run_started", "attempt": 1},
        {"event": "stage_started", "stage": "L1"},
        {"event": "stage_finished", "stage": "L1", "state": "completed"},
        {"event": "stage_started", "stage": "L2"},
    )
    assert etapa() == "L2"
    _escribir_progreso(
        env,
        queued.run_id,
        {"event": "stage_finished", "stage": "L2", "state": "completed"},
        {"event": "stage_started", "stage": "L3"},
    )
    assert etapa() == "L3"
    # Una reanudación empieza un intento nuevo: la etapa del intento anterior ya no está en curso.
    _escribir_progreso(env, queued.run_id, {"event": "run_started", "attempt": 2})
    assert etapa() is None
    _escribir_progreso(env, queued.run_id, {"event": "stage_started", "stage": "L4"})
    assert etapa() == "L4"
    # Una ejecución que terminó ya no tiene etapa en curso.
    with closing(dal.connect(env.db)) as conn:
        dal.finish_run(conn, run.id, estado="completada")
    assert etapa() is None


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


def test_lanzamiento_por_linea_de_comandos_declara_datos_reales(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Una ingesta sin origen declarado, ni en su informe ni en la base.
    path = env.settings.data_root / "ingestas" / f"{env.ingest_id}.json"
    report = json.loads(path.read_text())
    report["source_file"]["datos_sinteticos"] = None
    path.write_text(json.dumps(report))
    with closing(dal.connect(env.db)) as conn:
        conn.execute("DELETE FROM m3_ingestas")
        conn.commit()
    monkeypatch.setenv("PRED_DATA_ROOT", str(env.settings.data_root))
    monkeypatch.setenv("PRED_DB_PATH", str(env.db))

    assert worker_main(["--launch", "--ingest", env.ingest_id]) == 0

    with closing(dal.connect(env.db)) as conn:
        run = dal.get_run(conn, 1)
    assert run is not None and run.parametros["datos_sinteticos"] is False
    command = engine.build_command(
        env.settings, run, env.settings.data_root / "processed/demo_canonico.parquet"
    )
    assert "--datos-reales" in command and "--datos-sinteticos" not in command


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


def test_a_locked_database_after_launch_stops_the_run_and_reaps_the_engine(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _scenario(monkeypatch, "slow")
    real_connect, real_set_launch = dal.connect, dal.set_launch
    monkeypatch.setattr(dal, "connect", lambda path: _short_timeout(real_connect, path))
    holder = sqlite3.connect(env.db, isolation_level=None)
    children: list[Any] = []

    def launcher(command: list[str], **kwargs: Any) -> Any:
        process = engine.launch(command, **kwargs)
        children.append(process)
        holder.execute("BEGIN IMMEDIATE")  # another writer holds the lock past the timeout
        return process

    def set_launch(*args: Any, **kwargs: Any) -> None:
        try:
            real_set_launch(*args, **kwargs)
        finally:
            holder.rollback()

    monkeypatch.setattr(dal, "set_launch", set_launch)
    enqueue_run(env.db, env.ingest_id, SMALL)

    Worker(env.settings, launcher=launcher).run_next()

    estado, error_json = _run_state(env)
    error = json.loads(error_json or "{}")
    assert estado == "detenida"
    assert (error["code"], error["retryable"]) == ("platform.database_busy", True)
    assert "database is locked" in error["detail"]
    assert env.rows("SELECT pid FROM ejecuciones")[0][0] is None
    (child,) = children
    assert child.poll() is not None  # terminated and reaped, not left running
    holder.close()


def test_a_locked_database_before_launch_stops_the_claimed_run_without_an_engine(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_connect, real_claim, real_stop = dal.connect, dal.claim_pending, dal.stop_claimed
    monkeypatch.setattr(dal, "connect", lambda path: _short_timeout(real_connect, path))
    holder = sqlite3.connect(env.db, isolation_level=None)
    launched: list[list[str]] = []

    def claim(conn: sqlite3.Connection) -> Any:
        run = real_claim(conn)
        holder.execute("BEGIN IMMEDIATE")  # another writer holds the lock past the timeout
        return run

    def stop_claimed(*args: Any, **kwargs: Any) -> None:
        holder.rollback()  # the lock is released only once the worker records its diagnostic
        real_stop(*args, **kwargs)

    monkeypatch.setattr(dal, "claim_pending", claim)

    def launcher(command: list[str], **kwargs: Any) -> Any:
        launched.append(command)
        return engine.launch(command, **kwargs)

    monkeypatch.setattr(dal, "stop_claimed", stop_claimed)
    enqueue_run(env.db, env.ingest_id, SMALL)

    Worker(env.settings, launcher=launcher).run_next()

    estado, error_json = _run_state(env)
    error = json.loads(error_json or "{}")
    assert estado == "detenida"
    assert (error["code"], error["retryable"]) == ("platform.database_busy", True)
    assert "database is locked" in error["detail"]
    assert env.rows("SELECT pid FROM ejecuciones")[0][0] is None
    assert launched == []
    holder.close()


def _short_timeout(real_connect: Callable[[Path], sqlite3.Connection], path: Path) -> Any:
    conn = real_connect(path)
    conn.execute("PRAGMA busy_timeout = 200")
    return conn


def test_a_busy_database_error_has_its_own_operator_guidance() -> None:
    from pred_platform.app.ejecucion import error_action
    from pred_platform.app.seleccion import error_title

    assert error_action("platform.database_busy")
    assert error_title("platform.database_busy") != "platform.database_busy"


class _LockUntilGivenUp(logging.Handler):
    """Another writer holds the lock until the worker logs a database failure it gave up on."""

    def __init__(self, db: Path) -> None:
        super().__init__(logging.ERROR)
        self.holder = sqlite3.connect(db, isolation_level=None, check_same_thread=False)

    def take(self) -> None:
        self.holder.execute("BEGIN IMMEDIATE")

    def emit(self, record: logging.LogRecord) -> None:
        if record.exc_info and self.holder.in_transaction:
            self.holder.rollback()


@pytest.fixture
def lock(env: Env, monkeypatch: pytest.MonkeyPatch) -> Iterator[_LockUntilGivenUp]:
    real_connect = dal.connect
    monkeypatch.setattr(dal, "connect", lambda path: _short_timeout(real_connect, path))
    handler = _LockUntilGivenUp(env.db)
    logger = logging.getLogger("pred_platform.worker")
    logger.addHandler(handler)
    yield handler
    logger.removeHandler(handler)
    handler.holder.close()


def _settled_by_the_running_loop(env: Env, launcher: Launcher) -> dict[str, Any]:
    """Run the worker loop, as the platform does, until the run is stopped; return its error."""
    enqueue_run(env.db, env.ingest_id, SMALL)
    stop = threading.Event()
    worker = Worker(env.settings, poll_interval=0.05, launcher=launcher)
    thread = threading.Thread(target=worker.run_forever, args=(stop,))
    thread.start()
    try:
        _wait(lambda: _run_state(env)[0] == "detenida", timeout=10, what="the run to settle")
    finally:
        stop.set()
        thread.join(timeout=30)
    assert env.rows("SELECT pid FROM ejecuciones")[0][0] is None
    return json.loads(_run_state(env)[1] or "{}")


def test_a_lock_that_outlasts_the_diagnostic_stops_the_run_once_it_clears(
    env: Env, lock: _LockUntilGivenUp, monkeypatch: pytest.MonkeyPatch
) -> None:
    _scenario(monkeypatch, "slow")
    children: list[Any] = []

    def launcher(command: list[str], **kwargs: Any) -> Any:
        process = engine.launch(command, **kwargs)
        children.append(process)
        lock.take()  # held through the launch record and the diagnostic after it
        return process

    error = _settled_by_the_running_loop(env, launcher)

    assert (error["code"], error["retryable"]) == ("platform.database_busy", True)
    assert "database is locked" in error["detail"]
    (child,) = children
    assert child.poll() is not None  # terminated and reaped, not left running


def test_a_lock_that_outlasts_a_pre_launch_diagnostic_stops_the_run_once_it_clears(
    env: Env, lock: _LockUntilGivenUp
) -> None:
    def launcher(command: list[str], **kwargs: Any) -> Any:
        lock.take()  # held through the diagnostic of the failed launch
        raise OSError("no such engine")

    error = _settled_by_the_running_loop(env, launcher)

    assert (error["code"], error["retryable"]) == ("platform.database_busy", True)


def _strand_a_launched_run(
    env: Env, lock: _LockUntilGivenUp, monkeypatch: pytest.MonkeyPatch
) -> tuple[Worker, list[Any]]:
    """A worker launches the run, then the database fails past its diagnostic: the claim stays."""
    _scenario(monkeypatch, "slow")
    real_set_launch = dal.set_launch
    children: list[Any] = []

    def set_launch(*args: Any, **kwargs: Any) -> None:
        real_set_launch(*args, **kwargs)
        lock.take()  # the next write (a progress event) fails, and so does its diagnostic

    def launcher(command: list[str], **kwargs: Any) -> Any:
        process = engine.launch(command, **kwargs)
        children.append(process)
        return process

    monkeypatch.setattr(dal, "set_launch", set_launch)
    enqueue_run(env.db, env.ingest_id, SMALL)
    worker = Worker(env.settings, launcher=launcher)
    worker.run_next()
    monkeypatch.setattr(dal, "set_launch", real_set_launch)
    ((estado, intento, pid),) = env.rows("SELECT estado, intento, pid FROM ejecuciones")
    assert (estado, intento) == ("ejecutando", 1) and pid is not None
    return worker, children


def test_a_late_settlement_after_an_operator_stop_keeps_its_error_and_clears_the_pid(
    env: Env, lock: _LockUntilGivenUp, monkeypatch: pytest.MonkeyPatch
) -> None:
    worker, children = _strand_a_launched_run(env, lock, monkeypatch)
    with closing(dal.connect(env.db)) as conn:
        assert dal.request_stop(conn, 1, error=run_stopped_error())
    stopped_at = env.rows("SELECT finalizada_en FROM ejecuciones")[0][0]

    worker.run_next()

    (child,) = children
    assert child.poll() is not None  # reaped, so no process may stay recorded
    stopped = json.dumps(run_stopped_error(), ensure_ascii=False)
    assert env.rows("SELECT estado, pid, error_json, finalizada_en FROM ejecuciones") == [
        ("detenida", None, stopped, stopped_at)
    ]
    assert env.rows("SELECT COUNT(*) FROM tareas WHERE estado IN ('pendiente', 'ejecutando')") == [
        (0,)
    ]


def test_a_stale_settlement_leaves_the_claim_of_a_resumed_run_alone(
    env: Env, lock: _LockUntilGivenUp, monkeypatch: pytest.MonkeyPatch
) -> None:
    stale, _ = _strand_a_launched_run(env, lock, monkeypatch)
    with closing(dal.connect(env.db)) as conn:  # the operator stops and resumes the run
        assert dal.request_stop(conn, 1, error=run_stopped_error())
        assert dal.resume_run(conn, 1)
    stop = threading.Event()
    replacement = threading.Thread(target=env.worker().run_next, args=(stop,))
    replacement.start()
    try:
        _wait(
            lambda: (
                env.rows(
                    "SELECT e.intento, e.pid IS NOT NULL, COUNT(t.id) FROM ejecuciones e "
                    "LEFT JOIN tareas t ON t.ejecucion_id = e.id AND t.estado = 'ejecutando'"
                )[0]
                == (2, 1, 1)
            ),
            what="a second worker running attempt 2",
        )
        before = env.rows("SELECT estado, intento, pid, error_json FROM ejecuciones")
        tasks = env.rows("SELECT id, estado, detalle_error FROM tareas ORDER BY id")

        stale.run_next()  # the first worker settles its old attempt 1 late

        assert env.rows("SELECT estado, intento, pid, error_json FROM ejecuciones") == before
        assert env.rows("SELECT id, estado, detalle_error FROM tareas ORDER BY id") == tasks
        assert before[0][0] == "ejecutando"
    finally:
        stop.set()
        replacement.join(timeout=20)


_ONCE_DURING_AN_OUTAGE = """
import sys
from pred_platform.dal import ejecuciones as dal
from pred_platform.worker.__main__ import main

real_connect, real_claim, claims = dal.connect, dal.claim_pending, []

def connect(path):
    conn = real_connect(path)
    conn.execute("PRAGMA busy_timeout = 200")
    return conn

def claim_pending(conn):
    run = real_claim(conn)
    if run is not None and not claims:
        claims.append(run)
        print("claimed", flush=True)
        sys.stdin.readline()  # another writer takes the lock now
    return run

dal.connect, dal.claim_pending = connect, claim_pending
raise SystemExit(main(["--once"]))
"""


class _OnceDuringAnOutage:
    """``worker --once`` as a real subprocess whose first claim waits until another writer locks."""

    def __init__(self, env: Env) -> None:
        environ = {
            **os.environ,
            "PRED_DATA_ROOT": str(env.settings.data_root),
            "PRED_DB_PATH": str(env.db),
            "PRED_ENGINE_CMD": f"{sys.executable} {_FAKE}",
            "FAKE_ENGINE_SCENARIO": "ok",
        }
        self.holder = sqlite3.connect(env.db, isolation_level=None, check_same_thread=False)
        self.process = subprocess.Popen(  # noqa: S603 - the test's own interpreter and script
            [sys.executable, "-c", _ONCE_DURING_AN_OUTAGE],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=environ,
        )
        self._lines: queue.Queue[tuple[str, str]] = queue.Queue()
        self._pumps = [
            threading.Thread(target=self._pump, args=(name, stream), daemon=True)
            for name, stream in (("stdout", self.process.stdout), ("stderr", self.process.stderr))
        ]
        for pump in self._pumps:
            pump.start()

    def _pump(self, name: str, stream: Any) -> None:
        for line in stream:
            self._lines.put((name, line))

    def run(self, *, diagnostic: str, count: int, timeout: float) -> int:
        """Hold the lock from the first claim until ``count`` failed diagnostics; return the exit.

        Reading, waiting and exiting share one deadline; the lock and the process are released
        however this ends.
        """
        deadline = time.monotonic() + timeout
        try:
            self._expect("stdout", "claimed", 1, deadline)
            self.holder.execute("BEGIN IMMEDIATE")
            assert self.process.stdin is not None
            self.process.stdin.write("\n")
            self.process.stdin.flush()
            self._expect("stderr", diagnostic, count, deadline)
            self.holder.rollback()
            return self.process.wait(timeout=max(deadline - time.monotonic(), 0))
        finally:
            if self.holder.in_transaction:
                self.holder.rollback()
            self.holder.close()
            if self.process.poll() is None:
                self.process.kill()
            self.process.communicate(timeout=10)
            for pump in self._pumps:
                pump.join(timeout=10)

    def _expect(self, stream: str, text: str, count: int, deadline: float) -> None:
        seen = 0
        while seen < count:
            try:
                name, line = self._lines.get(timeout=max(deadline - time.monotonic(), 0))
            except queue.Empty:
                raise AssertionError(
                    f"timed out waiting for {count} {stream} lines with {text!r} (saw {seen})"
                ) from None
            seen += name == stream and text in line


def test_worker_once_waits_for_a_late_settlement_and_drains_the_queue(env: Env) -> None:
    first = enqueue_run(env.db, env.ingest_id, SMALL)
    second = enqueue_run(env.db, env.ingest_id, dataclasses.replace(SMALL, seed=18))

    # The lock clears after the second failed diagnostic, where the command used to give up.
    code = _OnceDuringAnOutage(env).run(
        diagnostic="no se pudo registrar el diagnóstico", count=2, timeout=60
    )

    assert code == 0
    assert env.rows("SELECT id, estado, pid FROM ejecuciones ORDER BY id") == [
        (first.ejecucion_id, "detenida", None),
        (second.ejecucion_id, "completada", None),
    ]


def test_a_diagnostic_that_never_arrives_fails_the_outage_drive_in_time_and_cleans_up(
    env: Env,
) -> None:
    enqueue_run(env.db, env.ingest_id, SMALL)
    once = _OnceDuringAnOutage(env)
    started = time.monotonic()

    with pytest.raises(AssertionError, match="timed out waiting"):
        once.run(diagnostic="a wording the worker never logs", count=2, timeout=3)

    assert time.monotonic() - started < 3 + 10
    assert once.process.poll() is not None  # killed and reaped, not left retrying
    with closing(sqlite3.connect(env.db, timeout=0)) as conn:
        conn.execute("BEGIN IMMEDIATE")  # no writer lock is left behind
        conn.rollback()
