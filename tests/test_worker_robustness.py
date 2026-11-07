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

from pred_platform.worker import engine
from pred_platform.worker.bucle import Worker
from pred_platform.worker.candado import WorkerLock
from pred_platform.worker.service import enqueue_run
from tests.fake_engine import RESERVE
from tests.test_worker import SMALL, Env, _wait
from tests.test_worker import env as env


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
            # The engine had published the run but left no final files: the run keeps its state
            # and its process, so the next worker start runs it again and imports it.
            assert (estado, pid, error) == ("completada", children[0].pid, None)
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
    assert cli.returncode == 0, cli.stderr
    assert "KeyError" in cli.stderr
    if units == "ok":
        _assert_imported(env, intento=1)
    else:
        # Nothing was imported: the run keeps its process as the mark for the next recovery.
        ((estado, pid, error, reserve),) = env.rows(
            "SELECT estado, pid, error_json, reserva_json FROM ejecuciones"
        )
        assert (estado, error, reserve) == ("completada", None, None)
        assert pid is not None and not engine.is_alive(pid)
        assert env.rows("SELECT COUNT(*) FROM tareas WHERE estado = 'exitosa'") == [(0,)]
        assert env.rows("SELECT COUNT(*) FROM selecciones") == [(0,)]

    # A restart of the worker in this process: an imported run stays as it is, and a kept one
    # is run again on the same run_id and imported.
    monkeypatch.setenv("FAKE_ENGINE_SCENARIO", "m3_ok")
    Worker(env.settings).drain()
    _assert_imported(env, intento=1 if units == "ok" else 2)


def test_settling_a_claim_keeps_a_run_the_engine_already_published(env: Env):
    from pred_platform.dal import ejecuciones as dal
    from pred_platform.worker.errores import run_stopped_error

    enqueue_run(env.settings, env.ingest_id, SMALL)
    with closing(dal.connect(env.db)) as conn:
        run = dal.claim_pending(conn)
        assert run is not None
        with conn:
            conn.execute("UPDATE ejecuciones SET estado='completada', pid=55555")
        dal.stop_claimed(conn, run.id, intento=run.intento, error=run_stopped_error())
        # Its recorded process is the mark the next worker start recovers it by.
        assert [r.id for r in dal.interrupted_runs(conn)] == [run.id]
    assert env.rows("SELECT estado, pid, error_json FROM ejecuciones") == [
        ("completada", 55555, None)
    ]


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
