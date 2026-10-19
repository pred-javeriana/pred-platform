"""Launch the engine's CLI as a subprocess and stop it.

The platform never imports ``pred_engine``: it runs ``pred-engine run`` (the command is
``Settings.engine_command``) and reads its run folder, the same file-based coupling as the ingest
reports. A crash or a hang of the engine therefore cannot take the web app down.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from pred_platform.config import Settings
from pred_platform.dal.ejecuciones import Ejecucion

# The concrete algorithm of each engine family (contract 7.4, decision D5).
MODELO_POR_FAMILIA: dict[str, str] = {
    "classical": "sarima",
    "ml": "lightgbm",
    "dl": "mlp",
    "foundation": "chronos2",
}


def model_id(family: str, model: str) -> str:
    """``familia:algoritmo`` in lowercase (decision D5)."""
    return f"{family}:{model}".lower()


def run_dir(settings: Settings, run: Ejecucion) -> Path:
    return (settings.data_root / "runs" / run.run_id).resolve()


def build_command(settings: Settings, run: Ejecucion, parquet: Path) -> list[str]:
    """The ``pred-engine run`` line for ``run``, from its stored parameters."""
    p: dict[str, Any] = run.parametros
    command = [
        *settings.engine_command,
        "run",
        "--parquet",
        str(parquet),
        "--data-root",
        str(settings.data_root.resolve()),
        "--run-id",
        run.run_id,
        "--families",
        *[str(f) for f in p.get("families", ["classical", "ml", "dl"])],
        "--min-train",
        str(p["min_train"]),
        "--horizon",
        str(p["horizon"]),
        "--step",
        str(p["step"]),
        "--seasonality",
        str(p["seasonality"]),
        "--metric",
        str(p["metric"]),
        "--seed",
        str(run.seed),
        "--workers",
        str(p.get("workers", 1)),
    ]
    if p.get("trials") is not None:
        command += ["--trials", str(p["trials"])]
    return command


def launch(command: list[str], *, settings: Settings, log_path: Path) -> subprocess.Popen[bytes]:
    """Start the engine in its own process group, with its output in ``log_path``.

    Raises:
        OSError: the command cannot be started (for example, ``pred-engine`` is not installed).
    """
    log_path.parent.mkdir(parents=True, exist_ok=True)
    env = {
        **os.environ,
        "PRED_DATA_ROOT": str(settings.data_root.resolve()),
        "PRED_DB_PATH": str(settings.db_path.resolve()),
    }
    with log_path.open("ab") as log:
        return subprocess.Popen(  # noqa: S603 - the command comes from trusted settings
            command,
            stdout=log,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            env=env,
            start_new_session=True,
        )


def is_alive(pid: int) -> bool:
    """Whether a process with this id exists (used to clean up after a crashed platform)."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def terminate(pid: int, *, grace: float = 5.0) -> None:
    """Stop the engine and its worker processes: SIGTERM to the group, SIGKILL after ``grace``."""
    if sys.platform == "win32":  # pragma: no cover - the platform targets POSIX hosts
        with contextlib.suppress(OSError):
            os.kill(pid, signal.SIGTERM)
        return
    try:
        group = os.getpgid(pid)
    except ProcessLookupError:
        return
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(group, signal.SIGTERM)
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline:
        if not is_alive(pid):
            return
        time.sleep(0.05)
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(group, signal.SIGKILL)


def log_tail(path: Path, lines: int = 8) -> str:
    """The last lines of the engine's output, for an error message."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return "\n".join(text.strip().splitlines()[-lines:])
