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


def _run(
    cwd: Path, *args: str, script: Path | str = SCRIPT, **env: str
) -> subprocess.CompletedProcess[str]:
    environ = {k: v for k, v in os.environ.items() if not k.startswith("PRED_")}
    return subprocess.run(
        [sys.executable, str(script), *args],
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


def test_env_writes_the_data_root_given_in_the_shell(workdir: Path) -> None:
    # `make setup` includes the new .env, which then overrides the shell value.
    root = workdir / "raiz"

    done = _run(workdir, "env", "--motor", "../pred-engine", PRED_DATA_ROOT=str(root))

    assert done.returncode == 0, done.stderr
    lines = (workdir / ".env").read_text(encoding="utf-8").splitlines()
    assert f"PRED_DATA_ROOT={root}" in lines
    assert "PRED_DATA_ROOT=data" not in lines
    assert f"PRED_DATA_ROOT={root}" in done.stdout


def test_env_keeps_an_existing_file_and_warns_about_sample_data(workdir: Path) -> None:
    existing = (
        "PRED_DATA_SOURCE=fixture\nPRED_ENGINE_CMD=uv run --project ../pred-engine pred-engine\n"
    )
    (workdir / ".env").write_text(existing, encoding="utf-8")

    done = _run(workdir, "env", "--motor", "../pred-engine", PRED_DATA_SOURCE="fixture")

    assert done.returncode == 0, done.stderr
    assert (workdir / ".env").read_text(encoding="utf-8") == existing
    assert "PRED_DATA_SOURCE=dal" in done.stdout


def test_env_rejects_a_command_for_a_different_engine(workdir: Path) -> None:
    existing = "PRED_ENGINE_CMD=uv run --project ../other-engine pred-engine\n"
    (workdir / ".env").write_text(existing, encoding="utf-8")

    done = _run(workdir, "env", "--motor", "../pred-engine")

    assert done.returncode == 1
    assert "PRED_ENGINE_CMD=uv run --project ../pred-engine pred-engine" in done.stderr
    assert (workdir / ".env").read_text(encoding="utf-8") == existing


def test_revisar_names_a_missing_engine_checkout_and_how_to_get_it(workdir: Path) -> None:
    done = _run(workdir, "revisar", "--motor", str(workdir / "pred-engine"))

    assert done.returncode == 1
    assert "git clone https://github.com/pred-javeriana/pred-engine.git" in done.stdout
    assert "checkout --detach" in done.stdout


def test_revisar_requires_the_clean_pinned_engine_checkout(workdir: Path) -> None:
    motor = workdir / "pred-engine"
    motor.mkdir()
    (motor / "pyproject.toml").write_text('[project]\nname = "pred-engine"\n', encoding="utf-8")
    makefile = motor / "Makefile"
    makefile.write_text("setup:\n\ttrue\n", encoding="utf-8")

    def git(*args: str) -> str:
        return subprocess.run(
            [
                "git",
                "-c",
                "user.name=PRED test",
                "-c",
                "user.email=test@example.com",
                "-c",
                "commit.gpgsign=false",
                "-c",
                "core.hooksPath=.git/hooks",
                *args,
            ],
            cwd=motor,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    git("init")
    git("add", "pyproject.toml", "Makefile")
    git("commit", "-m", "Pinned")
    revision = git("rev-parse", "HEAD")
    scripts = workdir / "scripts"
    scripts.mkdir()
    script = scripts / SCRIPT.name
    shutil.copy(SCRIPT, script)
    (workdir / ".github").mkdir()
    (workdir / ".github" / "pred-engine.commit").write_text(revision + "\n", encoding="ascii")

    good = _run(workdir, "revisar", "--motor", str(motor), script=script)
    assert good.returncode == 0, good.stdout
    assert revision in good.stdout

    makefile.write_text("setup:\n\tfalse\n", encoding="utf-8")
    dirty = _run(workdir, "revisar", "--motor", str(motor), script=script)
    assert dirty.returncode == 1
    assert "copia limpia" in dirty.stdout

    git("add", "Makefile")
    git("commit", "-m", "Different revision")
    wrong = _run(workdir, "revisar", "--motor", str(motor), script=script)
    assert wrong.returncode == 1
    assert f"checkout --detach {revision}" in wrong.stdout


def test_cargar_demo_ingests_the_sample_as_synthetic(workdir: Path) -> None:
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
    assert "sintético" in done.stdout
    [report] = (data / "ingestas").glob("*.json")
    info = json.loads(report.read_text(encoding="utf-8"))
    assert info["status"] == "accepted"
    assert info["source_file"]["datos_sinteticos"] is True


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


def test_up_serves_the_host_it_is_given(workdir: Path) -> None:
    port = str(_free_port())
    env = {"PRED_DATA_ROOT": str(workdir / "data"), "PRED_DATA_SOURCE": "fixture"}
    try:
        started = _run(workdir, "up", "--host", "0.0.0.0", "--port", port, **env)  # noqa: S104
        assert started.returncode == 0, started.stderr
        assert f"PRED está listo: http://0.0.0.0:{port}" in started.stdout
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=5) as answer:
            assert answer.status == 200
        running = _run(workdir, "status", **env)
        pid = json.loads((workdir / "data" / "servidor.json").read_text())["pid"]
        listening = {
            (c.laddr.ip, c.laddr.port)
            for c in psutil.Process(pid).net_connections(kind="tcp")
            if c.status == psutil.CONN_LISTEN
        }
    finally:
        _run(workdir, "down", **env)

    assert f"corriendo en http://0.0.0.0:{port} (PID {pid}), responde" in running.stdout
    assert listening == {("0.0.0.0", int(port))}  # noqa: S104


def test_make_up_passes_host_and_ignores_one_from_the_environment() -> None:
    def command(*args: str, **env: str) -> str:
        done = subprocess.run(
            ["make", "-n", "-C", str(REPO), "up", *args],  # noqa: S607
            env={**os.environ, **env},
            capture_output=True,
            text=True,
            check=True,
        )
        return done.stdout

    assert "up --host 127.0.0.1 --port 8000" in command(HOST="otra-maquina")
    assert "up --host 0.0.0.0 --port 8001" in command("HOST=0.0.0.0", "PORT=8001")


def test_up_refuses_a_port_in_use_and_names_another(workdir: Path) -> None:
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen()
        port = busy.getsockname()[1]

        done = _run(workdir, "up", "--port", str(port), PRED_DATA_ROOT=str(workdir / "data"))

    assert done.returncode == 1
    assert f"make up PORT={port + 1}" in done.stderr
    assert not (workdir / "data" / "servidor.json").exists()


def test_up_names_a_database_file_that_is_no_sqlite_in_one_line(workdir: Path) -> None:
    data = workdir / "data"
    data.mkdir()
    (data / "pred.db").write_bytes(b"basura\n")
    env = {"PRED_DATA_ROOT": str(data), "PRED_DATA_SOURCE": "dal", "PRED_WORKER": "off"}

    done = _run(workdir, "up", "--port", str(_free_port()), **env)

    assert done.returncode == 1
    assert done.stderr.splitlines() == [
        f"error: el servidor terminó al iniciar: {data / 'pred.db'} is not a SQLite database "
        "(file is not a database); check PRED_DB_PATH and PRED_DATA_ROOT, or restore its backup "
        "(<name>.antes-de-vNNNN.bak). Nothing was changed"
    ]
    assert not (data / "servidor.json").exists()
    assert (data / "pred.db").read_bytes() == b"basura\n"
