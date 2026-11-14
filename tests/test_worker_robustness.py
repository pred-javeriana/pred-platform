"""Supervisión real de procesos, señales y recuperación del worker."""

from __future__ import annotations

import errno
import json
import logging
import os
import signal
import subprocess
import sys
import threading
import time
from contextlib import closing
from dataclasses import replace
from pathlib import Path

import psutil
import pytest
from fastapi.testclient import TestClient

from pred_platform.app.main import create_app
from pred_platform.worker import engine
from pred_platform.worker.bucle import Worker
from pred_platform.worker.candado import WorkerLock
from pred_platform.worker.service import enqueue_run
from tests.fake_engine import RESERVE
from tests.test_worker import SMALL, Env, _LockUntilGivenUp, _wait
from tests.test_worker import env as env
from tests.test_worker import lock as lock


def _cli_env(env: Env, scenario: str, **extra: str) -> dict[str, str]:
    return {
        **os.environ,
        "PRED_DATA_ROOT": str(env.settings.data_root),
        "PRED_DB_PATH": str(env.db),
        "PRED_ENGINE_CMD": " ".join(env.settings.engine_command),
        "FAKE_ENGINE_SCENARIO": scenario,
        **extra,
    }


def _cli(env: Env, *args: str) -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        [sys.executable, "-m", "pred_platform.worker", *args],
        env=_cli_env(env, "slow"),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


@pytest.mark.skipif(sys.platform == "win32", reason="señales POSIX")
@pytest.mark.parametrize("signum", [signal.SIGTERM, getattr(signal, "SIGHUP", 1), signal.SIGINT])
@pytest.mark.parametrize("once", [False, True])
def test_cli_signals_stop_the_engine_and_allow_resume(env: Env, signum: int, once: bool):
    enqueue_run(env.settings, env.ingest_id, SMALL)
    worker = _cli(env, *(["--once"] if once else []))
    pid = None
    try:
        _wait(lambda: env.rows("SELECT pid FROM ejecuciones")[0][0] is not None)
        pid = env.rows("SELECT pid FROM ejecuciones")[0][0]
        _wait(lambda: env.rows("SELECT COUNT(*) FROM tareas")[0][0] > 0)
        worker.send_signal(signum)
        assert worker.wait(timeout=10) == (130 if once else 0)
        _wait(lambda: not engine.is_alive(pid), timeout=3, what="engine to stop after signal")
        assert env.rows("SELECT estado FROM ejecuciones") == [("ejecutando",)]
        Worker(env.settings).drain()
        assert env.rows("SELECT estado, pid FROM ejecuciones") == [("completada", None)]
    finally:
        if worker.poll() is None:
            worker.kill()
        worker.wait(timeout=10)
        if pid is not None and engine.is_alive(pid):
            engine.terminate(pid, grace=0.1)


@pytest.mark.skipif(not hasattr(signal, "SIGHUP"), reason="SIGHUP es POSIX")
def test_a_worker_started_under_nohup_keeps_supervising_after_sighup(env: Env):
    # nohup leaves SIGHUP ignored: closing the terminal must not stop the worker or its engine.
    enqueue_run(env.settings, env.ingest_id, SMALL)
    worker = subprocess.Popen(
        [sys.executable, "-m", "pred_platform.worker"],
        env=_cli_env(env, "slow"),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        preexec_fn=lambda: signal.signal(signal.SIGHUP, signal.SIG_IGN),  # noqa: PLW1509
    )
    pid = None
    try:
        _wait(lambda: env.rows("SELECT COUNT(*) FROM tareas")[0][0] > 0)
        pid = env.rows("SELECT pid FROM ejecuciones")[0][0]
        worker.send_signal(signal.SIGHUP)
        time.sleep(0.5)
        assert worker.poll() is None
        assert engine.is_alive(pid)
        assert env.rows("SELECT estado, pid FROM ejecuciones") == [("ejecutando", pid)]
        worker.send_signal(signal.SIGTERM)  # the other stop signals still stop it
        assert worker.wait(timeout=10) == 0
        _wait(lambda: not engine.is_alive(pid), timeout=3, what="engine to stop after SIGTERM")
    finally:
        if worker.poll() is None:
            worker.kill()
        worker.wait(timeout=10)
        if pid is not None and engine.is_alive(pid):
            engine.terminate(pid, grace=0.1)


@pytest.mark.skipif(sys.platform != "linux", reason="muerte del supervisor en Linux")
def test_kernel_stops_engine_when_supervisor_is_killed(env: Env):
    enqueue_run(env.settings, env.ingest_id, SMALL)
    worker = _cli(env)
    pid = None
    try:
        _wait(lambda: env.rows("SELECT COUNT(*) FROM tareas")[0][0] > 0)
        pid = env.rows("SELECT pid FROM ejecuciones")[0][0]
        worker.kill()  # la segunda interrupción de reload no permite ejecutar un finally
        worker.wait(timeout=10)
        _wait(lambda: not engine.is_alive(pid), timeout=3, what="kernel parent-death signal")
        Worker(env.settings).drain()
        assert env.rows("SELECT estado, intento, pid FROM ejecuciones") == [("completada", 2, None)]
    finally:
        if worker.poll() is None:
            worker.kill()
        worker.wait(timeout=10)
        if pid is not None and engine.is_alive(pid):
            engine.terminate(pid, grace=0.1)


@pytest.mark.skipif(sys.platform == "win32", reason="proceso POSIX")
def test_termination_reaps_its_child_without_waiting_the_grace_period():
    child = subprocess.Popen(["sleep", "300"], start_new_session=True)
    try:
        started = time.monotonic()
        engine.terminate(child.pid, grace=3)
        assert time.monotonic() - started < 2
        assert child.poll() is not None
    finally:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=10)


@pytest.mark.parametrize("scenario", ["malformed_progress", "malformed_after_publication"])
def test_malformed_progress_stops_the_claim_and_the_loop_runs_the_next_run(
    env: Env, caplog, monkeypatch, scenario
):
    caplog.set_level(logging.ERROR, logger="pred_platform.worker")
    enqueue_run(env.settings, env.ingest_id, SMALL)
    monkeypatch.setenv("FAKE_ENGINE_SCENARIO", scenario)
    children = []

    def launcher(command, **kwargs):
        child = engine.launch(command, **kwargs)
        children.append(child)
        return child

    stop = threading.Event()
    worker = Worker(env.settings, launcher=launcher, poll_interval=0.05)
    thread = threading.Thread(target=worker.run_forever, args=(stop,), daemon=True)
    thread.start()
    try:
        _wait(
            lambda: bool(children) and children[0].poll() is not None and "KeyError" in caplog.text,
            timeout=8,
            what="the supervision error",
        )
        monkeypatch.setenv("FAKE_ENGINE_SCENARIO", "ok")
        enqueue_run(env.settings, env.ingest_id, replace(SMALL, seed=18))
        _wait(lambda: env.rows("SELECT estado FROM ejecuciones ORDER BY id")[-1][0] == "completada")
        # The loop settled the first claim before it claimed the second run.
        ((estado, pid, error),) = env.rows(
            "SELECT estado, pid, error_json FROM ejecuciones ORDER BY id LIMIT 1"
        )
        if scenario == "malformed_after_publication":
            # The engine had published the run but left no final files: the run is stopped with
            # the import diagnostic and keeps its process, so the next worker start imports it.
            assert (estado, pid) == ("detenida", children[0].pid)
            diagnostic = json.loads(error)
            assert diagnostic["code"] == "platform.result_import_failed"
            assert "sku_id" in diagnostic["detail"]
        else:
            assert estado == "detenida" and pid is None and "sku_id" in error
    finally:
        stop.set()
        thread.join(timeout=15)
        assert not thread.is_alive()
        for child in children:
            if child.poll() is None:
                engine.terminate(child.pid, grace=0.1)
                child.wait(timeout=10)


@pytest.mark.skipif(os.name != "posix" or os.geteuid() == 0, reason="permisos POSIX sin root")
def test_worker_retries_a_lock_without_permission_until_mode_is_restored(env: Env, caplog):
    caplog.set_level(logging.ERROR, logger="pred_platform.worker")
    enqueue_run(env.settings, env.ingest_id, SMALL)
    lock = WorkerLock.for_database(env.db)
    lock.path.touch(mode=0o000)
    stop = threading.Event()
    thread = threading.Thread(
        target=Worker(env.settings, poll_interval=0.05).run_forever, args=(stop,), daemon=True
    )
    thread.start()
    try:
        _wait(lambda: str(lock.path) in caplog.text)
        assert thread.is_alive()
        assert env.rows("SELECT estado FROM ejecuciones") == [("pendiente",)]
        lock.path.chmod(0o600)
        _wait(lambda: env.rows("SELECT estado FROM ejecuciones") == [("completada",)])
    finally:
        lock.path.chmod(0o600)
        stop.set()
        thread.join(timeout=15)
        assert not thread.is_alive()


@pytest.mark.skipif(os.name != "posix", reason="flock POSIX")
@pytest.mark.parametrize("code", [errno.ENOLCK, errno.EOPNOTSUPP])
def test_lock_system_errors_are_not_reported_as_another_worker(tmp_path: Path, monkeypatch, code):
    import fcntl

    def unavailable(*args):
        raise OSError(code, "lock unavailable")

    monkeypatch.setattr(fcntl, "flock", unavailable)
    lock = WorkerLock.for_database(tmp_path / "pred.db")
    with pytest.raises(OSError, match=str(lock.path)):
        lock.acquire()
    monkeypatch.undo()
    assert lock.acquire()  # the failed attempt released its descriptor and left no lock held
    lock.release()


@pytest.mark.skipif(sys.platform == "win32", reason="grupos POSIX")
def test_interrupt_stops_the_whole_engine_group_and_drain_resumes(env: Env, monkeypatch):
    from pred_platform.dal import ejecuciones as dal

    enqueue_run(env.settings, env.ingest_id, SMALL)
    children = []

    def launcher(command, **kwargs):
        child = subprocess.Popen(["sh", "-c", "sleep 300 & exec sleep 300"], start_new_session=True)
        children.append(child)
        _wait(lambda: bool(psutil.Process(child.pid).children()))
        return child

    def interrupt(*args):
        raise KeyboardInterrupt

    try:
        with monkeypatch.context() as patch:
            patch.setattr(dal, "run_estado", interrupt)
            with pytest.raises(KeyboardInterrupt):
                Worker(env.settings, launcher=launcher).run_next()
        child = children[0]
        _wait(lambda: not _group_exists(child.pid), timeout=3, what="whole engine group to exit")
        Worker(env.settings).drain()
        assert env.rows("SELECT estado, intento, pid FROM ejecuciones") == [("completada", 2, None)]
    finally:
        for child in children:
            if _group_exists(child.pid):
                os.killpg(child.pid, signal.SIGKILL)
            child.wait(timeout=10)


def test_once_reports_no_execution_without_promising_another_worker_will_run(env: Env, caplog):
    enqueue_run(env.settings, env.ingest_id, SMALL)
    lock = WorkerLock.for_database(env.db)
    assert lock.acquire()
    try:
        Worker(env.settings).drain()
        assert "--once no ejecutó nada" in caplog.text
        assert "para el siguiente que arranque" in caplog.text
        assert env.rows("SELECT estado, intento FROM ejecuciones") == [("pendiente", 0)]
    finally:
        lock.release()


@pytest.mark.skipif(sys.platform != "linux", reason="cierre abrupto con reload en Linux")
def test_two_fast_interrupts_with_reload_do_not_leave_an_engine(env: Env):
    enqueue_run(env.settings, env.ingest_id, SMALL)
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "pred_platform.app.main:app",
            "--reload",
            "--host",
            "127.0.0.1",
            "--port",
            "0",
            "--reload-dir",
            str(Path(__file__).resolve().parents[1] / "src"),
        ],
        env={
            **os.environ,
            "PRED_DATA_SOURCE": "dal",
            "PRED_DATA_ROOT": str(env.settings.data_root),
            "PRED_DB_PATH": str(env.db),
            "PRED_ENGINE_CMD": " ".join(env.settings.engine_command),
            "PRED_WORKER": "embedded",
            "FAKE_ENGINE_SCENARIO": "slow",
        },
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    pid = None
    try:
        _wait(lambda: env.rows("SELECT COUNT(*) FROM tareas")[0][0] > 0)
        pid = env.rows("SELECT pid FROM ejecuciones")[0][0]
        os.killpg(process.pid, signal.SIGINT)
        time.sleep(0.1)
        if _group_exists(process.pid):
            os.killpg(process.pid, signal.SIGINT)
        process.wait(timeout=15)
        _wait(lambda: not engine.is_alive(pid), timeout=3, what="engine after reload shutdown")
        Worker(env.settings).drain()
        assert env.rows("SELECT estado, pid FROM ejecuciones") == [("completada", None)]
    finally:
        if _group_exists(process.pid):
            os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=10)
        if pid is not None and engine.is_alive(pid):
            engine.terminate(pid, grace=0.1)


def _assert_imported(env: Env, *, intento: int) -> None:
    ((estado, attempt, pid, error, reserve),) = env.rows(
        "SELECT estado, intento, pid, error_json, reserva_json FROM ejecuciones"
    )
    assert (estado, attempt, pid, error) == ("completada", intento, None, None)
    assert json.loads(reserve) == RESERVE
    assert env.rows("SELECT estado, tiempo_pared_s, COUNT(*) FROM tareas GROUP BY 1, 2") == [
        ("exitosa", 2.0, 12)
    ]
    assert env.rows("SELECT COUNT(*) FROM selecciones") == [(3,)]
    assert env.rows(
        "SELECT COUNT(*) FROM seleccion_familias f JOIN tareas t ON t.id = f.tarea_id "
        "WHERE t.estado = 'exitosa'"
    ) == [(12,)]


def _assert_import_failed(env: Env, *, intento: int) -> None:
    ((state, attempt, pid, error, reserve),) = env.rows(
        "SELECT estado, intento, pid, error_json, reserva_json FROM ejecuciones"
    )
    assert (state, attempt, reserve) == ("detenida", intento, None)
    assert pid is not None and not engine.is_alive(pid)
    diagnostic = json.loads(error)
    assert diagnostic["code"] == "platform.result_import_failed"
    assert diagnostic["retryable"] is True
    assert "import" in diagnostic["detail"]
    assert env.rows("SELECT COUNT(*) FROM selecciones") == [(0,)]
    with TestClient(create_app(env.settings)) as client:
        response = client.get("/ejecucion/estado?ejecucion=1")
        assert response.status_code == 286
        assert 'data-pred-run-state="detenida"' in response.text
        assert 'data-pred-poll="false"' in response.text
        page = client.get("/corridas/1/ejecucion")
        assert page.status_code == 200
        assert "Reanudar" in page.text
        assert "No se pudieron importar los resultados finales" in page.text


@pytest.mark.parametrize("units", ["ok", "bad_line"])
def test_a_supervision_error_after_publication_imports_the_final_files_or_keeps_recovery(
    env: Env, monkeypatch, units: str
):
    # The engine records M3 as completada, writes valid final files and run_finished, then a
    # progress record without keys breaks the supervision. With units=bad_line the import of
    # those files fails too.
    enqueue_run(env.settings, env.ingest_id, SMALL)
    cli = subprocess.run(
        [sys.executable, "-m", "pred_platform.worker", "--once"],
        env=_cli_env(env, "m3_published_then_malformed", FAKE_ENGINE_UNITS=units),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    # A final import that fails is a failed command, as on a worker without recovery.
    assert cli.returncode == (0 if units == "ok" else 1), cli.stderr
    assert "KeyError" in cli.stderr
    if units == "ok":
        _assert_imported(env, intento=1)
    else:
        _assert_import_failed(env, intento=1)
        assert env.rows("SELECT COUNT(*) FROM tareas WHERE estado = 'exitosa'") == [(0,)]
        assert env.rows("SELECT COUNT(*) FROM selecciones") == [(0,)]

    # A restart of the worker in this process: an imported run stays as it is, and a kept one
    # is run again on the same run_id and imported.
    monkeypatch.setenv("FAKE_ENGINE_SCENARIO", "m3_ok")
    Worker(env.settings).drain()
    _assert_imported(env, intento=1 if units == "ok" else 2)


def test_a_supervision_error_before_run_finished_stops_the_run_until_the_operator_resumes(
    env: Env, monkeypatch
):
    # The engine leaves valid final files, then a progress record without keys breaks the
    # supervision while it is still alive and before run_finished. The worker cannot tell the
    # attempt finished, so it stops the run; only the operator's resume runs it again.
    from pred_platform.dal import ejecuciones as dal

    enqueue_run(env.settings, env.ingest_id, SMALL)
    monkeypatch.setenv("FAKE_ENGINE_SCENARIO", "malformed_before_run_finished")
    assert Worker(env.settings, poll_interval=0.01).drain()
    folder = next((env.settings.data_root / "runs").iterdir())
    for name in ("corrida.json", "unidades.jsonl", "seleccion.jsonl"):
        assert (folder / name).is_file(), name
    ((estado, attempt, pid, error),) = env.rows(
        "SELECT estado, intento, pid, error_json FROM ejecuciones"
    )
    assert (estado, attempt, pid) == ("detenida", 1, None)
    diagnostic = json.loads(error)
    assert diagnostic["code"] == "pipeline.stage_failed"
    assert "KeyError" in diagnostic["detail"]
    assert env.rows("SELECT COUNT(*) FROM selecciones") == [(0,)]
    with TestClient(create_app(env.settings)) as client:
        page = client.get("/corridas/1/ejecucion")
        assert page.status_code == 200
        assert "Reanudar" in page.text
    tasks = env.rows("SELECT id FROM tareas ORDER BY id")

    # A restart of the worker leaves it stopped: it has no process left to recover.
    monkeypatch.setenv("FAKE_ENGINE_SCENARIO", "m3_ok")
    assert Worker(env.settings).drain()
    assert env.rows("SELECT estado, intento FROM ejecuciones") == [("detenida", 1)]

    with closing(dal.connect(env.db)) as conn:
        assert dal.resume_run(conn, 1)
    assert Worker(env.settings).drain()
    _assert_imported(env, intento=2)
    assert env.rows("SELECT id FROM tareas ORDER BY id") == tasks


def test_a_failed_final_import_exits_non_zero_and_a_restart_imports_the_files(
    env: Env, monkeypatch
):
    # The engine publishes the run and exits normally, but a unit of its final files lacks its
    # keys: nothing is imported, the command fails and the run stays recoverable.
    enqueue_run(env.settings, env.ingest_id, SMALL)
    cli = subprocess.run(
        [sys.executable, "-m", "pred_platform.worker", "--once"],
        env=_cli_env(env, "publishes_before_exit", FAKE_ENGINE_UNITS="bad_line"),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert cli.returncode == 1, cli.stderr
    _assert_import_failed(env, intento=1)
    assert env.rows("SELECT COUNT(*) FROM tareas WHERE estado = 'exitosa'") == [(0,)]

    tasks = env.rows("SELECT id FROM tareas ORDER BY id")
    repeated = subprocess.run(
        [sys.executable, "-m", "pred_platform.worker", "--once"],
        env=_cli_env(env, "publishes_before_exit", FAKE_ENGINE_UNITS="bad_line"),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert repeated.returncode == 1, repeated.stderr
    _assert_import_failed(env, intento=2)
    assert env.rows("SELECT id FROM tareas ORDER BY id") == tasks

    monkeypatch.setenv("FAKE_ENGINE_SCENARIO", "m3_ok")
    assert Worker(env.settings).drain()
    _assert_imported(env, intento=3)


@pytest.mark.parametrize("state", ["completada", "completada_con_fallos"])
def test_a_running_worker_reports_failed_import_without_polling_forever(env: Env, state: str):
    enqueue_run(env.settings, env.ingest_id, SMALL)
    worker = subprocess.Popen(
        [sys.executable, "-m", "pred_platform.worker"],
        env=_cli_env(
            env,
            "publishes_before_exit",
            FAKE_ENGINE_UNITS="bad_line",
            FAKE_ENGINE_TERMINAL_STATE=state,
        ),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        _wait(
            lambda: env.rows("SELECT estado FROM ejecuciones") == [("detenida",)],
            timeout=8,
            what="the import failure diagnostic",
        )
        assert worker.poll() is None
        _assert_import_failed(env, intento=1)
    finally:
        worker.terminate()
        worker.wait(timeout=15)


def test_a_real_write_lock_after_publication_records_failure_then_recovers_unchanged_files(
    env: Env, lock: _LockUntilGivenUp, monkeypatch: pytest.MonkeyPatch
):
    from pred_platform.worker import bucle

    enqueue_run(env.settings, env.ingest_id, SMALL)
    monkeypatch.setenv("FAKE_ENGINE_SCENARIO", "publishes_before_exit")
    finalize = bucle.finalize

    def locked_import(*args, **kwargs):
        lock.take()
        return finalize(*args, **kwargs)

    monkeypatch.setattr(bucle, "finalize", locked_import)
    assert not Worker(env.settings, poll_interval=0.01).drain()
    _assert_import_failed(env, intento=1)
    tasks = env.rows("SELECT id FROM tareas ORDER BY id")
    folder = next((env.settings.data_root / "runs").iterdir())
    artifacts = {name: (folder / name).read_bytes() for name in ("unidades.jsonl", "corrida.json")}

    monkeypatch.setattr(bucle, "finalize", finalize)
    assert Worker(env.settings).drain()
    _assert_imported(env, intento=2)
    assert env.rows("SELECT id FROM tareas ORDER BY id") == tasks
    # The same valid unit identities and reserve were recovered without changing the producer.
    assert artifacts["unidades.jsonl"]
    assert json.loads(artifacts["corrida.json"])["summary"]["reserve"] == RESERVE


def test_settling_a_claim_the_engine_already_published_records_it_without_results(env: Env):
    from pred_platform.dal import ejecuciones as dal
    from pred_platform.worker.errores import result_import_error, run_stopped_error

    enqueue_run(env.settings, env.ingest_id, SMALL)
    unimported = result_import_error("Los archivos finales no se importaron.")
    with closing(dal.connect(env.db)) as conn:
        run = dal.claim_pending(conn)
        assert run is not None
        with conn:
            conn.execute("UPDATE ejecuciones SET estado='completada', pid=55555")
        assert dal.stop_claimed(
            conn, run.id, intento=run.intento, error=run_stopped_error(), unimported=unimported
        )
        # Its recorded process is the mark the next worker start recovers it by.
        assert [r.id for r in dal.interrupted_runs(conn)] == [run.id]
    ((estado, pid, error),) = env.rows("SELECT estado, pid, error_json FROM ejecuciones")
    assert (estado, pid, json.loads(error)) == ("detenida", 55555, unimported)


@pytest.mark.skipif(sys.platform == "win32", reason="grupos POSIX")
def test_termination_kills_group_members_that_survive_sigterm(tmp_path: Path):
    marker = tmp_path / "member.pid"
    member = (
        "import signal, sys, time; from pathlib import Path; "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        "Path(sys.argv[1]).write_text(str(__import__('os').getpid())); time.sleep(300)"
    )
    leader_code = (
        "import subprocess, sys, time; "
        f"subprocess.Popen([sys.executable, '-c', {member!r}, sys.argv[1]]); time.sleep(300)"
    )
    leader = subprocess.Popen(
        [sys.executable, "-c", leader_code, str(marker)], start_new_session=True
    )
    member_pid = None
    try:
        _wait(lambda: marker.exists() and marker.read_text() != "", timeout=10)
        member_pid = int(marker.read_text())
        engine.terminate(leader.pid, grace=1)
        # The leader dies on SIGTERM and stays a zombie until reaped; the member ignores
        # SIGTERM, so only the SIGKILL after the grace period stops it.
        _wait(lambda: not engine.is_alive(member_pid), timeout=3, what="member to be killed")
    finally:
        if _group_exists(leader.pid):
            os.killpg(leader.pid, signal.SIGKILL)
        leader.wait(timeout=10)


@pytest.mark.skipif(sys.platform != "linux", reason="muerte del supervisor en Linux")
def test_recovery_stops_an_engine_a_wrapper_left_behind_before_relaunching(
    env: Env, tmp_path: Path, monkeypatch
):
    # A wrapper runs the engine as its child, as a resource-slot script or `uv run` does.
    # The kernel stops only the wrapper when the supervisor dies abruptly.
    wrapper = tmp_path / "engine-wrapper.sh"
    wrapper.write_text(
        f'#!/bin/sh\n{" ".join(env.settings.engine_command)} "$@" &\nwait\n', encoding="utf-8"
    )
    wrapper.chmod(0o755)
    enqueue_run(env.settings, env.ingest_id, SMALL)
    worker = subprocess.Popen(
        [sys.executable, "-m", "pred_platform.worker"],
        env={
            **os.environ,
            "PRED_DATA_ROOT": str(env.settings.data_root),
            "PRED_DB_PATH": str(env.db),
            "PRED_ENGINE_CMD": str(wrapper),
            "FAKE_ENGINE_SCENARIO": "slow",
        },
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    group = None
    orphans: list[int] = []
    try:
        _wait(lambda: env.rows("SELECT COUNT(*) FROM tareas")[0][0] > 0)
        group = env.rows("SELECT pid FROM ejecuciones")[0][0]
        worker.kill()
        worker.wait(timeout=10)
        _wait(lambda: not engine.is_alive(group), timeout=3, what="wrapper to stop")
        orphans = [p.pid for p in psutil.process_iter() if _pgid(p.pid) == group]
        assert orphans and all(engine.is_alive(pid) for pid in orphans)

        launched_beside: list[list[int]] = []

        def launcher(command, **kwargs):
            launched_beside.append([pid for pid in orphans if engine.is_alive(pid)])
            return engine.launch(command, **kwargs)

        monkeypatch.setenv("FAKE_ENGINE_SCENARIO", "ok")
        Worker(env.settings, launcher=launcher).drain()

        assert launched_beside == [[]]  # no second engine while the first still ran
        assert env.rows("SELECT estado, intento, pid FROM ejecuciones") == [("completada", 2, None)]
    finally:
        if worker.poll() is None:
            worker.kill()
        worker.wait(timeout=10)
        if group is not None and _group_exists(group):
            os.killpg(group, signal.SIGKILL)


def _pgid(pid: int) -> int | None:
    try:
        return os.getpgid(pid)
    except OSError:
        return None


def _group_exists(pid: int) -> bool:
    try:
        os.killpg(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _mixed_group(
    tmp_path: Path, *, leader_db: Path, member_db: Path
) -> tuple[subprocess.Popen[bytes], int]:
    """A process group whose leader and member name different databases in ``PRED_DB_PATH``."""
    marker = tmp_path / "member.pid"
    member = (
        "import os, sys, time; from pathlib import Path; "
        "Path(sys.argv[1]).write_text(str(os.getpid())); time.sleep(300)"
    )
    leader_code = (
        "import os, subprocess, sys, time; "
        f"subprocess.Popen([sys.executable, '-c', {member!r}, sys.argv[1]], "
        "env={**os.environ, 'PRED_DB_PATH': sys.argv[2]}); time.sleep(300)"
    )
    leader = subprocess.Popen(
        [sys.executable, "-c", leader_code, str(marker), str(member_db)],
        env={**os.environ, "PRED_DB_PATH": str(leader_db)},
        start_new_session=True,
    )
    _wait(lambda: marker.exists() and marker.read_text() != "", timeout=10)
    return leader, int(marker.read_text())


@pytest.mark.skipif(sys.platform == "win32", reason="grupos POSIX")
@pytest.mark.parametrize("leader_is_this", [False, True])
def test_recovery_leaves_alone_a_group_whose_processes_name_different_databases(
    env: Env, tmp_path: Path, leader_is_this: bool
):
    from pred_platform.dal import ejecuciones as dal

    other = tmp_path / "otra.db"
    leader_db, member_db = (env.db, other) if leader_is_this else (other, env.db)
    process, member = _mixed_group(tmp_path, leader_db=leader_db, member_db=member_db)
    leader = process.pid
    try:
        # A live leader of another database is authoritative; a leader of this database whose
        # member names another one is a contradiction. Neither is stopped.
        expected = "conflict" if leader_is_this else "other"
        assert engine.engine_owner(leader, env.db) == expected
        enqueue_run(env.settings, env.ingest_id, SMALL)
        with closing(dal.connect(env.db)) as conn:
            run = dal.claim_pending(conn)
            assert run is not None
            dal.set_launch(conn, run.id, pid=leader, ruta_corrida=f"runs/{run.run_id}")

        Worker(env.settings).recover()

        assert engine.is_alive(leader) and engine.is_alive(member)
        assert env.rows("SELECT estado, pid FROM ejecuciones") == [("ejecutando", leader)]
    finally:
        os.killpg(leader, signal.SIGKILL)
        process.wait(timeout=10)
        _wait(lambda: not engine.is_alive(member), timeout=5, what="member to be killed")
