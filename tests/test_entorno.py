"""`scripts/entorno.py`, the script behind `make setup|up|down|status`, through its command line.

Each test runs the script as make does: from a working directory with its own `.env.example`, with
the platform settings in the environment. The server tests start the real app on a free loopback
port with the contract's sample data, so they need no database or engine.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import socket
import subprocess
import sys
import urllib.request
from pathlib import Path

import psutil
import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "entorno.py"
FAKE_ENGINE = REPO / "tests" / "fake_engine.py"


def _run(cwd: Path, *args: str, **env: str) -> subprocess.CompletedProcess[str]:
    environ = {k: v for k, v in os.environ.items() if not k.startswith("PRED_")}
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=cwd,
        env={**environ, **env},
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@pytest.fixture
def workdir(tmp_path: Path) -> Path:
    shutil.copy(REPO / ".env.example", tmp_path / ".env.example")
    return tmp_path


def test_env_creates_the_file_for_real_data_and_the_engine_checkout(workdir: Path) -> None:
    done = _run(workdir, "env", "--motor", "../pred-engine")

    assert done.returncode == 0, done.stderr
    lines = (workdir / ".env").read_text(encoding="utf-8").splitlines()
    assert "PRED_DATA_SOURCE=dal" in lines
    assert "PRED_ENGINE_CMD=uv run --project ../pred-engine pred-engine" in lines
    # Every other key keeps the example's value and comments.
    example = (workdir / ".env.example").read_text(encoding="utf-8").splitlines()
    assert len(lines) == len(example)
    assert "PRED_WORKER=embedded" in lines


def test_env_keeps_an_existing_file_and_warns_about_sample_data(workdir: Path) -> None:
    (workdir / ".env").write_text("PRED_DATA_SOURCE=fixture\n", encoding="utf-8")

    done = _run(workdir, "env", "--motor", "../pred-engine", PRED_DATA_SOURCE="fixture")

    assert done.returncode == 0, done.stderr
    assert (workdir / ".env").read_text(encoding="utf-8") == "PRED_DATA_SOURCE=fixture\n"
    assert "PRED_DATA_SOURCE=dal" in done.stdout


def test_revisar_names_a_missing_engine_checkout_and_how_to_get_it(workdir: Path) -> None:
    done = _run(workdir, "revisar", "--motor", str(workdir / "pred-engine"))

    assert done.returncode == 1
    assert "git clone https://github.com/pred-javeriana/pred-engine.git" in done.stdout


def test_revisar_accepts_an_engine_checkout_with_its_setup_target(workdir: Path) -> None:
    motor = workdir / "pred-engine"
    motor.mkdir()
    (motor / "pyproject.toml").write_text('[project]\nname = "pred-engine"\n', encoding="utf-8")
    (motor / "Makefile").write_text("test:\n\ttrue\n", encoding="utf-8")

    old = _run(workdir, "revisar", "--motor", str(motor))
    (motor / "Makefile").write_text("test:\n\ttrue\n\nsetup:\n\ttrue\n", encoding="utf-8")
    current = _run(workdir, "revisar", "--motor", str(motor))

    assert old.returncode == 1
    assert f"git -C {motor} pull" in old.stdout
    assert f"ok  pred-engine en {motor}" in current.stdout
    assert "make setup de pred-engine" not in current.stdout


def test_cargar_demo_leaves_the_sample_file_accepted_in_the_database(workdir: Path) -> None:
    data = workdir / "data"
    engine = f"{shlex.quote(sys.executable)} {shlex.quote(str(FAKE_ENGINE))}"
    subprocess.run(
        [sys.executable, "-m", "pred_platform.dal.migrate", "--db", str(data / "pred.db")],
        check=True,
        capture_output=True,
    )

    done = _run(workdir, "cargar-demo", PRED_DATA_ROOT=str(data), PRED_ENGINE_CMD=engine)

    assert done.returncode == 0, done.stderr
    assert "panel_demo.csv" in done.stdout
    [report] = (data / "ingestas").glob("*.json")
    assert json.loads(report.read_text(encoding="utf-8"))["status"] == "accepted"


def test_up_status_and_down_manage_one_background_server(workdir: Path) -> None:
    port = str(_free_port())
    env = {"PRED_DATA_ROOT": str(workdir / "data"), "PRED_DATA_SOURCE": "fixture"}

    started = _run(workdir, "up", "--port", port, **env)
    try:
        assert started.returncode == 0, started.stderr
        assert f"PRED está listo: http://127.0.0.1:{port}" in started.stdout
        pid = json.loads((workdir / "data" / "servidor.json").read_text())["pid"]
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=5) as answer:
            assert answer.status == 200
        again = _run(workdir, "up", "--port", port, **env)
        running = _run(workdir, "status", **env)
    finally:
        stopped = _run(workdir, "down", **env)

    assert "ya está corriendo" in again.stdout
    assert f"corriendo en http://127.0.0.1:{port} (PID {pid}), responde" in running.stdout
    assert stopped.returncode == 0, stopped.stderr
    assert "PRED se detuvo." in stopped.stdout
    assert not psutil.pid_exists(pid) or psutil.Process(pid).status() == psutil.STATUS_ZOMBIE
    assert not (workdir / "data" / "servidor.json").exists()
    assert "detenido" in _run(workdir, "status", **env).stdout
    assert "no está corriendo" in _run(workdir, "down", **env).stdout


def test_up_right_after_down_reuses_the_same_port(workdir: Path) -> None:
    # Requests to the stopped server leave its port in TIME_WAIT for a while; uvicorn can still
    # bind it, so `make up` must not report it as taken.
    port = str(_free_port())
    env = {"PRED_DATA_ROOT": str(workdir / "data"), "PRED_DATA_SOURCE": "fixture"}
    try:
        assert _run(workdir, "up", "--port", port, **env).returncode == 0
        for _ in range(3):
            urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=5).close()
        assert _run(workdir, "down", **env).returncode == 0

        again = _run(workdir, "up", "--port", port, **env)

        assert again.returncode == 0, again.stderr
    finally:
        _run(workdir, "down", **env)


def test_up_refuses_a_port_in_use_and_names_another(workdir: Path) -> None:
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen()
        port = busy.getsockname()[1]

        done = _run(workdir, "up", "--port", str(port), PRED_DATA_ROOT=str(workdir / "data"))

    assert done.returncode == 1
    assert f"make up PORT={port + 1}" in done.stderr
    assert not (workdir / "data" / "servidor.json").exists()
