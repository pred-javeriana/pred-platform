"""Prepare, start, stop and inspect a local PRED installation (the `make setup|up|down|status`).

The Makefile owns the order of the steps and reuses its own targets (`migrate`, `worker`); this
script only does what make cannot do portably: check the prerequisites, write `.env` from
`.env.example`, load the sample file through the same path as a browser upload, and run the server
in the background with a PID file so it can be stopped and checked later.

    uv run python scripts/entorno.py revisar --motor ../pred-engine
    uv run python scripts/entorno.py env --motor ../pred-engine
    uv run python scripts/entorno.py cargar-demo
    uv run python scripts/entorno.py up --host 127.0.0.1 --port 8000
    uv run python scripts/entorno.py status
    uv run python scripts/entorno.py down

Every command reads the platform settings from the environment (`make` exports `.env`). The
server state (`servidor.json`) and log (`servidor.log`) live under `PRED_DATA_ROOT`.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import psutil
from dotenv import load_dotenv

from pred_platform.config import DATA_ROOT_VAR, DATA_SOURCE_VAR, ENGINE_COMMAND_VAR, Settings
from pred_platform.worker.ingesta import IngestLaunchError, submit_csv

REPO = Path(__file__).resolve().parent.parent
# Relative to the directory make runs from, like the Makefile's `include .env`.
ENV_EXAMPLE = Path(".env.example")
ENV_FILE = Path(".env")
DEMO_CSV = REPO / "ejemplos" / "panel_demo.csv"
ENGINE_REPO = "https://github.com/pred-javeriana/pred-engine.git"
ENGINE_REVISION = REPO / ".github" / "pred-engine.commit"
APP = "pred_platform.app.main:app"
DEFAULT_HOST = "127.0.0.1"
# A server bound to every interface still answers the health check on the loopback.
WILDCARD_HOSTS = {"0.0.0.0": "127.0.0.1", "::": "::1"}
STATE_FILE = "servidor.json"
LOG_FILE = "servidor.log"
START_TIMEOUT_S = 60.0
# The app waits up to 15 s for its worker thread, which stops the engine first.
STOP_TIMEOUT_S = 60.0


def _ok(text: str) -> None:
    print(f"  ok  {text}")


def _fail(text: str, fix: str) -> None:
    print(f"  falta  {text}\n         {fix}")


def revisar(motor: Path) -> int:
    """Check what `make setup` needs before it installs anything. Returns the exit code."""
    print("Revisando lo necesario:")
    missing = 0
    _ok(f"uv y Python {sys.version_info.major}.{sys.version_info.minor}")
    if sys.platform.startswith("linux"):
        if shutil.which("setpriv"):
            _ok("setpriv (util-linux)")
        else:
            missing += 1
            _fail("setpriv", "Instale util-linux con el gestor de paquetes del sistema.")
    revision = ENGINE_REVISION.read_text(encoding="ascii").strip()
    quoted_motor = shlex.quote(motor.as_posix())
    checkout = f"git -C {quoted_motor} checkout --detach {revision}"
    git = shutil.which("git")
    if git is None:
        _fail("Git", "Instale Git y repita make setup.")
        return 1
    if not (motor / "pyproject.toml").is_file():
        _fail(
            f"pred-engine en {motor}",
            f"Clone el motor: git clone {ENGINE_REPO} {quoted_motor}\n         {checkout}",
        )
        return 1
    git_at_motor = [git, "--no-optional-locks", "-C", str(motor)]
    head = subprocess.run(  # noqa: S603
        [*git_at_motor, "rev-parse", "--show-prefix", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    if head.returncode or head.stdout.splitlines() != ["", revision]:
        _fail(
            f"pred-engine en la revisión {revision}", f"Use una copia de Git y ejecute: {checkout}"
        )
        return 1
    status = subprocess.run(  # noqa: S603
        [*git_at_motor, "status", "--porcelain", "--untracked-files=all"],
        capture_output=True,
        text=True,
        check=False,
    )
    if status.returncode or status.stdout:
        _fail(f"copia limpia de pred-engine en {motor}", "Use otra copia limpia del mismo commit.")
        return 1
    _ok(f"pred-engine en {motor} ({revision})")
    if missing:
        print("Corrija lo que falta y repita make setup.")
        return 1
    return 0


def _with_value(text: str, key: str, value: str) -> str:
    lines = text.splitlines(keepends=True)
    for i, line in enumerate(lines):
        if line.startswith(f"{key}="):
            lines[i] = f"{key}={value}\n"
            return "".join(lines)
    raise SystemExit(f"error: {ENV_EXAMPLE.name} no define {key}")


def env(motor: Path) -> int:
    """Create `.env` from `.env.example` for real data and the engine at ``motor``.

    A ``PRED_DATA_ROOT`` from the shell goes into the new `.env`: once `.env` exists, the
    Makefile's ``include .env`` overrides the shell value, so it would be lost otherwise.
    """
    engine = f"uv run --project {shlex.quote(motor.as_posix())} pred-engine"
    if ENV_FILE.exists():
        print(f"Se conserva {ENV_FILE.name}, que ya existía.")
        load_dotenv(ENV_FILE)
        try:
            settings = Settings.from_env()
        except ValueError as exc:
            print(f"error en {ENV_FILE.name}: {exc}", file=sys.stderr)
            return 1
        command = settings.engine_command
        if (
            len(command) != 5
            or command[:3] != ("uv", "run", "--project")
            or Path(command[3]).resolve() != motor.resolve()
            or command[4] != "pred-engine"
        ):
            print(
                f"error: para usar el motor verificado, configure {ENV_FILE.name} con "
                f"{ENGINE_COMMAND_VAR}={engine}",
                file=sys.stderr,
            )
            return 1
        if settings.data_source != "dal":
            print(
                f"  aviso: {DATA_SOURCE_VAR}={settings.data_source} muestra datos de ejemplo "
                f"del contrato; para datos reales ponga {DATA_SOURCE_VAR}=dal en {ENV_FILE.name}."
            )
        return 0
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    text = _with_value(text, DATA_SOURCE_VAR, "dal")
    text = _with_value(text, ENGINE_COMMAND_VAR, engine)
    data_root = os.environ.get(DATA_ROOT_VAR, "").strip()
    if data_root:
        text = _with_value(text, DATA_ROOT_VAR, data_root)
    ENV_FILE.write_text(text, encoding="utf-8")
    print(f"Se creó {ENV_FILE.name} desde {ENV_EXAMPLE.name}:")
    if data_root:
        print(f"  {DATA_ROOT_VAR}={data_root}")
    print(f"  {DATA_SOURCE_VAR}=dal")
    print(f"  {ENGINE_COMMAND_VAR}={engine}")
    return 0


def cargar_demo(settings: Settings) -> int:
    """Ingest the bundled synthetic sample and import its report.

    A different file already loaded under the same name (an older sample) is kept next to this
    one, as the Datos page does when the operator confirms the upload.
    """
    content = DEMO_CSV.read_bytes()
    informe = {}
    for confirm in (False, True):
        try:
            sha256 = submit_csv(settings, DEMO_CSV.name, content, confirm_overwrite=confirm)
        except (ValueError, IngestLaunchError) as exc:
            print(f"error: no se pudo cargar {DEMO_CSV.name}: {exc}", file=sys.stderr)
            return 1
        report = settings.data_root / "ingestas" / f"{sha256}.json"
        informe = json.loads(report.read_text(encoding="utf-8"))
        if informe.get("status") != "needs_confirmation":
            break
    status = informe.get("status")
    if status != "accepted":
        print(f"error: el motor dejó {DEMO_CSV.name} en estado {status!r}", file=sys.stderr)
        return 1
    if informe["source_file"].get("datos_sinteticos") is not True:
        print(f"error: el motor debe registrar {DEMO_CSV.name} como sintético", file=sys.stderr)
        return 1
    print(f"Ejemplo sintético listo en Datos: {DEMO_CSV.name}")
    return 0


def _state_path(settings: Settings) -> Path:
    return settings.data_root / STATE_FILE


@dataclass(frozen=True, slots=True)
class _Server:
    pid: int
    host: str
    port: int


def _read_state(settings: Settings) -> _Server | None:
    """The recorded server, or ``None`` when no PRED server from this data root is alive."""
    path = _state_path(settings)
    try:
        recorded = json.loads(path.read_text(encoding="utf-8"))
        state = _Server(
            int(recorded["pid"]), str(recorded.get("host", DEFAULT_HOST)), int(recorded["port"])
        )
        process = psutil.Process(state.pid)
        # The command line guards against a PID the system gave to another process.
        if process.is_running() and APP in process.cmdline():
            return state
    except (OSError, ValueError, KeyError, TypeError, psutil.Error):
        pass
    path.unlink(missing_ok=True)
    return None


def _url(host: str, port: int) -> str:
    return f"http://[{host}]:{port}" if ":" in host else f"http://{host}:{port}"


def _healthy(host: str, port: int) -> bool:
    url = _url(WILDCARD_HOSTS.get(host, host), port)
    try:
        with urllib.request.urlopen(f"{url}/health", timeout=2) as response:
            return response.status == 200
    except (OSError, urllib.error.URLError):
        return False


def _port_free(host: str, port: int) -> bool:
    family, kind, protocol, _, address = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)[0]
    with socket.socket(family, kind, protocol) as probe:
        # Bind as uvicorn does: a port left in TIME_WAIT by a stopped server is still usable.
        # On Windows the same option would let the probe share a port another server holds.
        if sys.platform != "win32":
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(address)
        except OSError:
            return False
    return True


def _log_tail(settings: Settings, lines: int = 15) -> str:
    log = settings.data_root / LOG_FILE
    text = log.read_text(encoding="utf-8", errors="replace") if log.is_file() else ""
    return "\n".join(text.splitlines()[-lines:])


def _startup_error(settings: Settings, start: int) -> str:
    """Why the server stopped at startup: the error its log ends with since ``start``, or the
    log's tail when it ended some other way. A startup error (a database that cannot be opened or
    migrated) is the last line of the traceback uvicorn logs before "Application startup failed".
    """
    with (settings.data_root / LOG_FILE).open("rb") as log:
        log.seek(start)
        lines = log.read().decode("utf-8", errors="replace").splitlines()
    failed = [i for i, line in enumerate(lines) if "Application startup failed" in line]
    if failed:
        for line in reversed(lines[: failed[-1]]):
            if line.strip():
                # "package.module.Error: message": the message says what failed and what to do.
                name, separator, message = line.partition(": ")
                if separator and not line[0].isspace() and " " not in name:
                    return message
                break
    return "\n" + _log_tail(settings)


def _stop(pid: int) -> bool:
    try:
        process = psutil.Process(pid)
        process.terminate()
        process.wait(STOP_TIMEOUT_S)
    except psutil.NoSuchProcess:
        return True
    except psutil.TimeoutExpired:
        return False
    return True


def up(settings: Settings, host: str, port: int) -> int:
    """Start the server on ``host`` in the background and wait until `/health` answers."""
    state = _read_state(settings)
    if state is not None:
        print(f"PRED ya está corriendo en {_url(state.host, state.port)} (PID {state.pid}).")
        return 0
    if not ENV_FILE.exists():
        print(f"  aviso: no hay {ENV_FILE.name}; corra make setup para preparar la instalación.")
    try:
        free = _port_free(host, port)
    except OSError as exc:
        print(f"error: no se puede usar la dirección {host}: {exc}", file=sys.stderr)
        return 1
    if not free:
        print(
            f"error: el puerto {port} ya está en uso; elija otro: make up PORT={port + 1}",
            file=sys.stderr,
        )
        return 1
    settings.data_root.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, "-m", "uvicorn", APP, "--host", host, "--port", str(port)]
    with (settings.data_root / LOG_FILE).open("ab") as log:
        start = log.tell()
        server = subprocess.Popen(  # noqa: S603 - fixed command line
            command, stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True
        )
    recorded = {"pid": server.pid, "host": host, "port": port}
    _state_path(settings).write_text(json.dumps(recorded) + "\n")
    print(f"Iniciando PRED (PID {server.pid}); registro en {settings.data_root / LOG_FILE}")
    deadline = time.monotonic() + START_TIMEOUT_S
    while time.monotonic() < deadline:
        if server.poll() is not None:
            _state_path(settings).unlink(missing_ok=True)
            print(
                f"error: el servidor terminó al iniciar: {_startup_error(settings, start)}",
                file=sys.stderr,
            )
            return 1
        if _healthy(host, port):
            print(f"PRED está listo: {_url(host, port)}")
            print("Ábralo en el navegador. Para detenerlo: make down")
            return 0
        time.sleep(0.5)
    _stop(server.pid)
    _state_path(settings).unlink(missing_ok=True)
    print(
        f"error: el servidor no respondió en {START_TIMEOUT_S:.0f} s:\n{_log_tail(settings)}",
        file=sys.stderr,
    )
    return 1


def down(settings: Settings) -> int:
    """Stop the recorded server; the app stops its worker and the engine before it exits."""
    state = _read_state(settings)
    if state is None:
        print("PRED no está corriendo.")
        return 0
    print(f"Deteniendo PRED (PID {state.pid})...")
    if not _stop(state.pid):
        print(
            f"error: PRED no se detuvo en {STOP_TIMEOUT_S:.0f} s; revise "
            f"{settings.data_root / LOG_FILE} y repita make down",
            file=sys.stderr,
        )
        return 1
    _state_path(settings).unlink(missing_ok=True)
    print("PRED se detuvo.")
    return 0


def status(settings: Settings) -> int:
    """Say whether the server runs and answers. The Makefile adds the database and the runs."""
    state = _read_state(settings)
    if state is None:
        print("Servidor: detenido. Para iniciarlo: make up")
        return 0
    answer = "responde" if _healthy(state.host, state.port) else "no responde todavía"
    print(f"Servidor: corriendo en {_url(state.host, state.port)} (PID {state.pid}), {answer}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="scripts/entorno.py", description="Prepare, start, stop and inspect PRED."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("revisar", "env"):
        sub = commands.add_parser(name)
        sub.add_argument("--motor", type=Path, required=True, help="the pred-engine checkout")
    commands.add_parser("cargar-demo")
    sub = commands.add_parser("up")
    sub.add_argument("--host", default=DEFAULT_HOST)
    sub.add_argument("--port", type=int, default=8000)
    commands.add_parser("down")
    commands.add_parser("status")
    args = parser.parse_args(argv)
    # Keep the order of messages and errors when make pipes them.
    sys.stdout.reconfigure(line_buffering=True)  # type: ignore[union-attr]

    if args.command == "revisar":
        return revisar(args.motor)
    if args.command == "env":
        return env(args.motor)
    try:
        settings = Settings.from_env()
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.command == "cargar-demo":
        return cargar_demo(settings)
    if args.command == "up":
        return up(settings, args.host, args.port)
    if args.command == "down":
        return down(settings)
    return status(settings)


if __name__ == "__main__":
    raise SystemExit(main())
