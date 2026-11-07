"""A resumed attempt cannot present the previous attempt as current progress."""

import re
from contextlib import closing

import pytest

from pred_platform.dal import ejecuciones as dal
from pred_platform.worker.errores import run_stopped_error
from tests.test_ejecucion import _dal_client, _launch, _set_run
from tests.test_execution_live import _running, _snapshot
from tests.test_worker import Env, _escribir_progreso
from tests.test_worker import env as env  # noqa: F401


@pytest.mark.parametrize("worker", ["off", "embedded"])
def test_queued_resume_waits_for_new_stage_timings_without_deleting_logs(
    env: Env, worker: str
) -> None:
    client, run = _running(env)
    _escribir_progreso(env, run.run_id, {"event": "run_started"})
    for code in ("L1", "L2", "L3", "L4"):
        _escribir_progreso(
            env,
            run.run_id,
            {"event": "stage_started", "stage": code, "t": "2026-11-04T20:00:00Z"},
            {
                "event": "stage_finished",
                "stage": code,
                "state": "completed",
                "t": "2026-11-04T20:02:00Z",
            },
        )
    with closing(dal.connect(env.db)) as conn:
        dal.task_stage_finished(
            conn,
            run.id,
            sku="FIL-0001",
            modelo="classical:sarima",
            corte="2026-06-29",
            seed=run.seed,
            stage="L2",
            completed=False,
            seconds=1,
            finished_at="2026-11-04T20:02:00Z",
            error={"code": "model.fit_failed"},
        )
    _set_run(env, "completada_con_fallos", None)
    path = env.settings.data_root / "runs" / run.run_id / "progreso.jsonl"
    artifacts = path.read_bytes()
    client = _dal_client(env, worker=worker)
    busy = None
    if worker == "embedded":
        # Another run already owns the processor when this one returns to the queue.
        assert _launch(client, env, seed="18").status_code == 303
        with closing(dal.connect(env.db)) as conn:
            busy = dal.claim_pending(conn)
            assert busy is not None and busy.id != run.id
    response = client.post(
        "/ejecucion/reanudar", data={"ejecucion": str(run.id)}, follow_redirects=False
    )
    assert response.status_code == 303
    for route in (response.headers["location"], "/ejecucion/estado?ejecucion=1"):
        body = client.get(route).text
        snapshot = _snapshot(body)
        assert snapshot["state"] == "pendiente"
        assert snapshot["counts"]["pendiente"] == 1
        for row in snapshot["stages"]:
            assert row["state"] == "pendiente"
            assert row["seconds"] is None and row["started_at"] is None
        assert "Duración por etapa del intento actual" not in body
        assert "Las etapas del nuevo intento se mostrarán cuando el procesador lo inicie." in body
    assert path.read_bytes() == artifacts
    if busy is not None:
        with closing(dal.connect(env.db)) as conn:
            dal.request_stop(conn, busy.id, error=run_stopped_error())
    with closing(dal.connect(env.db)) as conn:
        assert dal.claim_pending(conn) is not None
    _escribir_progreso(
        env,
        run.run_id,
        {"event": "run_started", "t": "2026-11-05T20:00:00Z"},
        {"event": "stage_started", "stage": "L1", "t": "2026-11-05T20:00:00Z"},
    )
    started = _snapshot(client.get("/ejecucion/estado?ejecucion=1").text)
    assert started["stages"][0]["state"] == "en_curso"
    assert started["stages"][0]["started_at"] == "2026-11-05T20:00:00+00:00"
    assert started["stages"][1]["seconds"] is None


def _completed_with_failures(env: Env, *, worker: str = "off"):
    client, run = _running(env)
    _escribir_progreso(env, run.run_id, {"event": "run_started"})
    for code in ("L1", "L2", "L3", "L4"):
        _escribir_progreso(
            env,
            run.run_id,
            {"event": "stage_started", "stage": code, "t": "2026-11-04T20:00:00Z"},
            {
                "event": "stage_finished",
                "stage": code,
                "state": "completed",
                "t": "2026-11-04T20:02:00Z",
            },
        )
    _escribir_progreso(env, run.run_id, {"event": "run_finished", "state": "completed"})
    _set_run(env, "completada_con_fallos", None)
    return _dal_client(env, worker=worker), run


def _stage_rows(body: str) -> dict[str, str]:
    found = re.findall(r'(<li[^>]*data-stage-code="(L\d)".*?</li>)', body, re.S)
    return {code: html for html, code in found}


@pytest.mark.parametrize("worker", ["off", "embedded"])
def test_stopped_retry_that_never_started_does_not_show_the_previous_attempt(
    env: Env, worker: str
) -> None:
    client, run = _completed_with_failures(env, worker=worker)
    path = env.settings.data_root / "runs" / run.run_id / "progreso.jsonl"
    artifacts = path.read_bytes()
    if worker == "embedded":
        assert _launch(client, env, seed="18").status_code == 303
        with closing(dal.connect(env.db)) as conn:
            assert dal.claim_pending(conn) is not None
    for action in ("reanudar", "detener"):
        response = client.post(
            f"/ejecucion/{action}", data={"ejecucion": "1"}, follow_redirects=False
        )
        assert response.status_code == 303
    for route in ("/corridas/1/ejecucion", "/ejecucion/estado?ejecucion=1"):
        body = client.get(route).text
        snapshot = _snapshot(body)
        assert snapshot["state"] == "detenida"
        assert snapshot["stage"] is None
        for row in snapshot["stages"]:
            assert row["state"] == "pendiente"
            assert row["seconds"] is None and row["started_at"] is None
        for text in _stage_rows(body).values():
            assert "terminada" not in text and "2 min" not in text
        assert "Duración por etapa del intento actual" not in body
        assert "El nuevo intento se detuvo antes de empezar" in body
    assert path.read_bytes() == artifacts


def test_stopped_retry_that_started_shows_only_its_own_timings(env: Env) -> None:
    client, run = _completed_with_failures(env)
    response = client.post("/ejecucion/reanudar", data={"ejecucion": "1"}, follow_redirects=False)
    assert response.status_code == 303
    with closing(dal.connect(env.db)) as conn:
        assert dal.claim_pending(conn) is not None
    _escribir_progreso(
        env,
        run.run_id,
        {"event": "run_started", "t": "2026-11-05T20:00:00Z"},
        {"event": "stage_started", "stage": "L1", "t": "2026-11-05T20:00:00Z"},
        {
            "event": "stage_finished",
            "stage": "L1",
            "state": "completed",
            "t": "2026-11-05T20:01:00Z",
        },
    )
    with closing(dal.connect(env.db)) as conn:
        assert dal.request_stop(conn, 1, error=run_stopped_error())
    body = client.get("/ejecucion/estado?ejecucion=1").text
    stages = {row["code"]: row for row in _snapshot(body)["stages"]}
    assert stages["L1"]["state"] == "hecha" and stages["L1"]["duration"] == "1 min"
    assert all(stages[code]["seconds"] is None for code in ("L2", "L3", "L4"))
    assert "Duración por etapa del intento actual" in body


@pytest.mark.parametrize("failed", ["L2", "L3"])
def test_stages_after_a_failed_stage_that_never_ran_do_not_show_as_done(
    env: Env, failed: str
) -> None:
    client, run = _running(env)
    codes = ("L1", "L2", "L3", "L4")
    _escribir_progreso(env, run.run_id, {"event": "run_started"})
    for code in codes[: codes.index(failed) + 1]:
        _escribir_progreso(
            env,
            run.run_id,
            {"event": "stage_started", "stage": code, "t": "2026-11-04T20:00:00Z"},
            {
                "event": "stage_finished",
                "stage": code,
                "state": "failed" if code == failed else "completed",
                "t": "2026-11-04T20:02:00Z",
            },
        )
    _set_run(env, "completada_con_fallos", None)
    for route in ("/corridas/1/ejecucion", "/ejecucion/estado?ejecucion=1"):
        body = client.get(route).text
        states = {row["code"]: row["state"] for row in _snapshot(body)["stages"]}
        ran = codes[: codes.index(failed)]
        for code in codes:
            expected = "hecha" if code in ran else "fallida" if code == failed else "sin_ejecutar"
            assert states[code] == expected
        rows = _stage_rows(body)
        for code in codes[codes.index(failed) + 1 :]:
            assert "terminada" not in rows[code] and "#i-check" not in rows[code]
            assert "no se ejecutó" in rows[code] and "No se ejecutó" in rows[code]
        assert "#i-check" not in rows[failed]
