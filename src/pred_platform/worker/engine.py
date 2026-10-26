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

import psutil

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
    origen = p.get("datos_sinteticos")
    if "datos_sinteticos" not in p or (origen is not None and type(origen) is not bool):
        raise ValueError(
            "procedencia ausente o invalida; vuelva a encolar la corrida para registrar el origen"
        )
    if origen is not None:
        command.append("--datos-sinteticos" if origen else "--datos-reales")
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
    if sys.platform == "win32":
        return _windows_is_alive(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _windows_is_alive(pid: int) -> bool:
    """Consulta el objeto de proceso sin enviar señales ni confundir un PID ya terminado."""
    if sys.platform != "win32":
        raise OSError("La consulta nativa requiere Windows")
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    # SYNCHRONIZE permite consultar si el objeto de proceso ya fue señalado.
    handle = kernel.OpenProcess(0x00100000, False, pid)
    if not handle:
        error = ctypes.get_last_error()
        if error == 5:  # Acceso denegado no significa proceso ausente.
            return True
        if error == 87:  # ERROR_INVALID_PARAMETER: el PID no identifica un proceso.
            return False
        raise ctypes.WinError(error)
    try:
        state = kernel.WaitForSingleObject(handle, 0)
        if state not in (0, 258):
            raise ctypes.WinError(ctypes.get_last_error())
        return state == 258  # WAIT_TIMEOUT: el proceso todavía está activo.
    finally:
        kernel.CloseHandle(handle)


def terminate(pid: int, *, grace: float = 5.0) -> None:
    """Stop the engine tree: process-group signals on POSIX, taskkill /T on Windows."""
    if sys.platform == "win32":
        try:
            parent = psutil.Process(pid)
            processes = [parent, *parent.children(recursive=True)]
        except psutil.NoSuchProcess:
            return
        # /T incluye el pool del motor; terminar solo el padre deja hijos calculando.
        result = subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW,
            timeout=max(1.0, grace + 5.0),
            check=False,
        )
        if result.returncode and is_alive(pid):
            raise OSError(f"No se pudo detener el árbol del proceso {pid}.")
        _, alive = psutil.wait_procs(processes, timeout=max(0.0, grace))
        if alive:
            raise TimeoutError(f"El árbol del proceso {pid} no terminó dentro del plazo.")
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
