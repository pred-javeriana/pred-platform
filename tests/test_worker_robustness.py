"""Supervisión real de procesos, señales y recuperación del worker."""

from __future__ import annotations

import errno
import logging
import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path

import psutil
import pytest

from pred_platform.worker import engine
from pred_platform.worker.bucle import Worker
from pred_platform.worker.candado import WorkerLock
from pred_platform.worker.service import enqueue_run
from tests.test_worker import SMALL, Env, _wait
from tests.test_worker import env as env


def _cli(env: Env, *args: str) -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        [sys.executable, "-m", "pred_platform.worker", *args],
        env={
            **os.environ,
            "PRED_DATA_ROOT": str(env.settings.data_root),
            "PRED_DB_PATH": str(env.db),
            "PRED_ENGINE_CMD": " ".join(env.settings.engine_command),
            "FAKE_ENGINE_SCENARIO": "slow",
        },
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
        _wait(lambda: env.rows("SELECT estado FROM ejecuciones") == [("detenida",)], timeout=8)
        assert env.rows("SELECT pid FROM ejecuciones") == [(None,)]
        assert children[0].poll() is not None
        assert "KeyError" in caplog.text
        assert "sku_id" in env.rows("SELECT error_json FROM ejecuciones")[0][0]
        monkeypatch.setenv("FAKE_ENGINE_SCENARIO", "ok")
        enqueue_run(env.settings, env.ingest_id, replace(SMALL, seed=18))
        _wait(lambda: env.rows("SELECT estado FROM ejecuciones ORDER BY id")[-1][0] == "completada")
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


def _group_exists(pid: int) -> bool:
    try:
        os.killpg(pid, 0)
    except ProcessLookupError:
        return False
    return True
