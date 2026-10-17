"""Lanzar, detener y reanudar corridas desde la interfaz y el worker que obedece."""

from __future__ import annotations

import json
import threading
from contextlib import closing

import pytest

from pred_platform.dal import ejecuciones as dal
from pred_platform.worker import engine
from pred_platform.worker.control import (
    ControlUnavailable,
    DalRunControl,
    UnavailableRunControl,
    build_run_control,
)
from pred_platform.worker.service import LaunchSettings, RunRequestError
from tests.test_worker import SMALL, Env, _scenario, _wait
from tests.test_worker import env as env  # noqa: F401  (the fixture)


def _control(env: Env) -> DalRunControl:
    return DalRunControl(env.db)


def _state(env: Env) -> tuple[str, dict[str, object] | None]:
    ((estado, error),) = env.rows("SELECT estado, error_json FROM ejecuciones ORDER BY id DESC")[:1]
    return estado, json.loads(error) if error else None


# ----------------------------------------------------------------------------- lanzar
def test_launching_queues_a_run_with_the_chosen_parameters(env: Env) -> None:
    result = _control(env).launch(env.ingest_id, SMALL)

    assert result.queued is True
    ((estado, seed, parametros),) = env.rows(
        "SELECT e.estado, e.seed, c.parametros FROM ejecuciones e "
        "JOIN configuraciones c ON c.id = e.configuracion_id"
    )
    assert (estado, seed) == ("pendiente", 17)
    assert json.loads(parametros)["min_train"] == 40


def test_launching_the_same_run_again_returns_the_existing_one(env: Env) -> None:
    first = _control(env).launch(env.ingest_id, SMALL)
    again = _control(env).launch(env.ingest_id, SMALL)

    assert again.ejecucion_id == first.ejecucion_id and again.queued is False


def test_only_an_accepted_ingestion_can_be_launched(env: Env) -> None:
    with pytest.raises(RunRequestError):
        _control(env).launch("c" * 64, LaunchSettings())


# ----------------------------------------------------------------------------- detener
def test_a_queued_run_can_be_stopped_and_the_worker_never_takes_it(env: Env) -> None:
    control = _control(env)
    control.launch(env.ingest_id, SMALL)

    assert control.stop(1) is True

    estado, error = _state(env)
    assert estado == "detenida" and error is not None
    assert (error["code"], error["stage"], error["retryable"]) == (
        "platform.run_stopped",
        "platform",
        True,
    )
    assert env.worker().run_next() is False


def test_a_running_run_is_stopped_the_engine_dies_and_no_task_stays_in_flight(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _scenario(monkeypatch, "slow")
    control = _control(env)
    control.launch(env.ingest_id, SMALL)
    thread = threading.Thread(target=env.worker().run_next)
    thread.start()
    _wait(
        lambda: (
            env.rows("SELECT estado, pid FROM ejecuciones")[0][0] == "ejecutando"
            and env.rows("SELECT COUNT(*) FROM tareas WHERE estado = 'ejecutando'")[0][0] > 0
        ),
        what="the run to be running a unit",
    )
    pid = env.rows("SELECT pid FROM ejecuciones")[0][0]
    assert pid is not None and engine.is_alive(pid)

    assert control.stop(1) is True
    thread.join(timeout=20)

    assert not thread.is_alive()
    estado, error = _state(env)
    assert estado == "detenida" and error is not None and error["code"] == "platform.run_stopped"
    assert not engine.is_alive(pid)
    assert env.rows("SELECT COUNT(*) FROM tareas WHERE estado IN ('pendiente','ejecutando')") == [
        (0,)
    ]
    closed = env.rows("SELECT estado, detalle_error FROM tareas WHERE estado = 'fallida'")
    assert closed and json.loads(closed[0][1])["code"] == "platform.run_stopped"
    # The run was not settled as if it had finished: nothing of a selection was imported.
    assert env.rows("SELECT COUNT(*) FROM selecciones") == [(0,)]


def test_stopping_a_finished_run_changes_nothing(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    _scenario(monkeypatch, "ok")
    control = _control(env)
    control.launch(env.ingest_id, SMALL)
    env.worker().run_next()

    assert control.stop(1) is False
    assert _state(env)[0] == "completada"


def test_stopping_an_unknown_run_is_not_an_error(env: Env) -> None:
    assert _control(env).stop(99) is False


# ----------------------------------------------------------------------------- reanudar
def test_a_stopped_run_resumes_with_the_same_parameters_and_the_same_task_rows(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _scenario(monkeypatch, "ok")
    control = _control(env)
    control.launch(env.ingest_id, SMALL)
    control.stop(1)

    assert control.resume(1) is True
    assert _state(env) == ("pendiente", None)
    env.worker().run_next()

    assert _state(env)[0] == "completada"
    assert env.rows("SELECT COUNT(*) FROM ejecuciones") == [(1,)]
    assert env.rows("SELECT COUNT(*) FROM tareas") == [(9,)]


def test_resuming_puts_the_tasks_the_stop_closed_back_to_pending(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _scenario(monkeypatch, "slow")
    control = _control(env)
    control.launch(env.ingest_id, SMALL)
    thread = threading.Thread(target=env.worker().run_next)
    thread.start()
    _wait(
        lambda: env.rows("SELECT COUNT(*) FROM tareas WHERE estado = 'ejecutando'")[0][0] > 0,
        what="a unit running",
    )
    control.stop(1)
    thread.join(timeout=20)
    assert env.rows("SELECT COUNT(*) FROM tareas WHERE estado IN ('fallida','no_ejecutable')")[0][0]

    assert control.resume(1) is True

    # Until the engine starts them again nothing may look finished or failed.
    assert env.rows("SELECT DISTINCT estado FROM tareas") == [("pendiente",)]
    assert env.rows("SELECT COUNT(*) FROM tareas WHERE detalle_error IS NOT NULL") == [(0,)]


def test_a_run_with_failures_can_be_retried(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    _scenario(monkeypatch, "unit_fails")
    control = _control(env)
    control.launch(env.ingest_id, SMALL)
    env.worker().run_next()
    assert _state(env)[0] == "completada_con_fallos"

    assert control.resume(1) is True
    _scenario(monkeypatch, "ok")
    env.worker().run_next()

    assert _state(env)[0] == "completada"
    assert env.rows("SELECT COUNT(*) FROM tareas WHERE estado = 'exitosa'") == [(9,)]


@pytest.mark.parametrize("scenario", ["completed", "pending"])
def test_only_a_stopped_or_failed_run_can_be_resumed(
    env: Env, monkeypatch: pytest.MonkeyPatch, scenario: str
) -> None:
    control = _control(env)
    control.launch(env.ingest_id, SMALL)
    if scenario == "completed":
        _scenario(monkeypatch, "ok")
        env.worker().run_next()

    assert control.resume(1) is False
    assert control.resume(99) is False


def test_the_request_to_stop_does_not_overwrite_a_run_that_just_finished(env: Env) -> None:
    control = _control(env)
    control.launch(env.ingest_id, SMALL)
    with closing(dal.connect(env.db)) as conn:
        dal.finish_run(conn, 1, estado="completada")
        assert dal.request_stop(conn, 1, error={"code": "platform.run_stopped"}) is False
    assert _state(env)[0] == "completada"


# ----------------------------------------------------------------------- la configuración
def test_fixtures_get_a_control_that_explains_why_it_is_off(env: Env) -> None:
    from dataclasses import replace

    off = build_run_control(replace(env.settings, data_source="fixture"))

    assert isinstance(off, UnavailableRunControl) and "datos de prueba" in (off.reason or "")
    for call in (
        lambda: off.launch(env.ingest_id, SMALL),
        lambda: off.stop(1),
        lambda: off.resume(1),
    ):
        with pytest.raises(ControlUnavailable, match="datos de prueba"):
            call()
    assert isinstance(build_run_control(env.settings), DalRunControl)
    assert build_run_control(env.settings).reason is None
