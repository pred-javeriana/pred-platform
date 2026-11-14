"""Launch the engine's CLI as a subprocess and stop it.

The platform never imports ``pred_engine``: it runs ``pred-engine run`` (the command is
``Settings.engine_command``) and reads its run folder, the same file-based coupling as the ingest
reports. A crash or a hang of the engine therefore cannot take the web app down.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Literal

import psutil

from pred_platform.config import Settings
from pred_platform.dal.ejecuciones import Ejecucion, RunRequestError

# The concrete algorithm of each engine family (contract 7.4, decision D5).
MODELO_POR_FAMILIA: dict[str, str] = {
    "classical": "sarima",
    "ml": "lightgbm",
    "dl": "mlp",
    "foundation": "chronos2",
}
# The families the engine routes to each demand class: a copy of the matrix of its policy
# 2.2.0-initial (FAMILIES_BY_SKU_CLASS in pred-engine optimizacion/router/politica.py). The
# platform does not import the engine: if the policy changes there, this copy changes too. The
# launch form reads it from here.
FAMILIES_BY_SKU_CLASS: dict[str, tuple[str, ...]] = {
    "smooth": ("classical", "ml", "dl", "foundation"),
    "erratic": ("classical", "ml", "dl", "foundation"),
    "intermittent": ("classical", "ml", "foundation"),
    "lumpy": ("classical", "foundation"),
}


def engine_origin(settings: Settings, ingest_id: str, declarada: bool | None) -> bool | None:
    """Consulta el veredicto de origen del motor sin abrir una transacción de escritura."""
    command = [
        *settings.engine_command,
        "origen",
        "--ingest-id",
        ingest_id,
        "--data-root",
        str(settings.data_root.resolve()),
        "--db-path",
        str(settings.db_path.resolve()),
    ]
    if declarada is not None:
        command.append("--datos-sinteticos" if declarada else "--datos-reales")
    try:
        result = subprocess.run(command, capture_output=True, timeout=60, check=False)
        if result.returncode not in (0, 3):
            cause = result.stderr.decode("utf-8", errors="replace").strip()
            raise ValueError(f"código {result.returncode}: {cause}")
        payload = json.loads(result.stdout)
        if not isinstance(payload, dict):
            raise ValueError("la respuesta no es un objeto JSON")
        if result.returncode == 3:
            detail = payload.get("detail")
            if payload.get("code") != "provenance.origin_conflict" or not isinstance(detail, str):
                raise ValueError("el conflicto no contiene code y detail válidos")
            raise RunRequestError(detail)
        if payload.get("ingest_id") != ingest_id:
            raise ValueError("la respuesta no corresponde a la ingesta consultada")
        if "datos_sinteticos" not in payload:
            raise ValueError("falta datos_sinteticos en la respuesta")
        origin = payload["datos_sinteticos"]
        if origin is not None and type(origin) is not bool:
            raise ValueError("datos_sinteticos no es booleano o nulo")
        return origin
    except subprocess.TimeoutExpired as exc:
        logging.getLogger(__name__).warning("Consulta de origen agotada: %s", exc)
        raise RunRequestError(
            "No se pudo consultar al motor el origen de los datos. "
            "Intente de nuevo en unos minutos."
        ) from exc
    except (OSError, ValueError) as exc:
        raise RunRequestError(f"No se pudo consultar el origen al motor: {exc}") from exc


def model_id(family: str, model: str) -> str:
    """``familia:algoritmo`` in lowercase (decision D5)."""
    return f"{family}:{model}".lower()


def run_dir(settings: Settings, run: Ejecucion) -> Path:
    return (settings.data_root / "runs" / run.run_id).resolve()


class OrigenNoReal(ValueError):
    """La corrida guardó un origen sintético o sin declarar; la plataforma no la ejecuta."""


def require_real_origin(parametros: dict[str, Any]) -> None:
    """Exige el origen real guardado: la plataforma solo arranca o reanuda corridas de datos reales.

    Raises:
        OrigenNoReal: la corrida guardó datos sintéticos o un origen sin declarar.
        ValueError: la configuración no trae el origen o no es booleano ni nulo.
    """
    origen = parametros.get("datos_sinteticos")
    if "datos_sinteticos" not in parametros or (origen is not None and type(origen) is not bool):
        raise ValueError(
            "procedencia ausente o invalida; vuelva a encolar la corrida para registrar el origen"
        )
    if origen is not False:
        guardado = "datos sintéticos" if origen else "un origen sin declarar"
        raise OrigenNoReal(
            f"La plataforma solo ejecuta datos reales y esta corrida guardó {guardado}. "
            "Lance una corrida nueva sobre una ingesta de datos reales."
        )


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
        *[str(f) for f in p.get("families", ["classical", "ml", "dl", "foundation"])],
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
    require_real_origin(p)
    command.append("--datos-reales")
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
    if sys.platform == "linux":
        # setpriv execs the engine in place. The kernel also stops it when reload or a
        # forced shutdown kills the supervisor before Python can run its cleanup.
        if shutil.which(command[0], path=env.get("PATH")) is None:
            raise FileNotFoundError(f"No existe el ejecutable del motor: {command[0]}")
        command = ["setpriv", "--pdeathsig", "SIGTERM", "--", *command]
    with log_path.open("ab") as log:
        return subprocess.Popen(  # noqa: S603 - the command comes from trusted settings
            command,
            stdout=log,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            env=env,
            start_new_session=True,
        )


# Which database a live process is an engine for (``engine_owner``).
EngineOwner = Literal["this", "other", "conflict", "none"]


def engine_owner(pid: int, db_path: Path) -> EngineOwner:
    """Which database the live process ``pid`` is an engine for, read from its ``PRED_DB_PATH``.

    ``launch`` sets that variable for every engine. ``"this"``: an engine of ``db_path``.
    ``"other"``: an engine of another database, such as the original of a copy served here.
    ``"conflict"``: the members of its process group name different databases, so none of them
    is trusted. ``"none"``: no such process, or a process that is not a platform engine (a reused
    pid, or one this user cannot inspect).

    ``launch`` makes the engine command the leader of its own process group, so the group keeps
    the recorded pid as its id. Its members are read too: a wrapper command (a resource-slot
    script, ``uv run``) can die while the engine it started keeps running in that group. A live
    leader is authoritative: when it names another database, the group is ``"other"`` whatever
    its members say. Only a group whose every identity names ``db_path`` is ``"this"``, the one
    answer that lets a caller stop it.
    """
    own = os.path.normcase(db_path.resolve())
    leader = _served_database(pid)
    if leader is not None and leader != own:
        return "other"
    identities = {leader} if leader is not None else set()
    if sys.platform != "win32":
        for member in _group_members(pid):
            if member != pid and (served := _served_database(member)) is not None:
                identities.add(served)
    if not identities:
        return "none"
    if len(identities) > 1:
        return "conflict"
    return "this" if identities == {own} else "other"


def _served_database(pid: int) -> str | None:
    """The normalized ``PRED_DB_PATH`` of the live process ``pid``, if it has one we can read."""
    try:
        served = psutil.Process(pid).environ().get("PRED_DB_PATH")
    except psutil.Error:
        return None
    return os.path.normcase(Path(served).resolve()) if served else None


def _group_members(group: int) -> list[int]:
    """The live processes of the process group ``group`` (POSIX); zombies are left out."""
    members = []
    for proc in psutil.process_iter():
        with contextlib.suppress(OSError, psutil.Error):
            if os.getpgid(proc.pid) == group and proc.status() != psutil.STATUS_ZOMBIE:
                members.append(proc.pid)
    return members


def is_alive(pid: int) -> bool:
    """Whether a live process with this id exists; a zombie counts as gone.

    Used to clean up after a crashed platform. A process that this user cannot inspect counts as
    alive.
    """
    if sys.platform == "win32":
        return _windows_is_alive(pid)
    try:
        # A terminated child can remain a zombie until its Popen owner reaps it.
        # It needs no grace period and must not trigger a later SIGKILL.
        return psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False
    except psutil.AccessDenied:
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


class EngineStillRunning(TimeoutError):
    """The engine's processes did not exit after they were killed: its run must stay owned."""


# How long ``terminate`` waits for the group to exit after SIGKILL (a process in uninterruptible
# I/O dies only when that I/O ends).
KILL_WAIT = 5.0


def terminate(pid: int, *, grace: float = 5.0, kill_wait: float | None = None) -> None:
    """Stop the engine tree: process-group signals on POSIX, taskkill /T on Windows.

    Returns once no process of the tree is left alive.

    Raises:
        EngineStillRunning: a process of the tree outlived SIGKILL by ``kill_wait`` seconds
            (``KILL_WAIT`` by default); the caller must not start another engine for its run.
    """
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
            raise EngineStillRunning(f"El árbol del proceso {pid} no terminó dentro del plazo.")
        return
    try:
        group = os.getpgid(pid)
    except ProcessLookupError:
        # The leader is gone, but the engine a wrapper started may still run in its group.
        group = pid
    if not _group_members(group):
        return
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(group, signal.SIGTERM)
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline:
        # The whole group, not only the leader: a member that ignores SIGTERM still runs
        # when the leader is already a zombie.
        if not _group_members(group):
            return
        time.sleep(0.05)
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(group, signal.SIGKILL)
    deadline = time.monotonic() + (KILL_WAIT if kill_wait is None else kill_wait)
    while _group_members(group):
        if time.monotonic() >= deadline:
            raise EngineStillRunning(f"El grupo de procesos {group} sigue vivo después de SIGKILL.")
        time.sleep(0.05)


def log_tail(path: Path, lines: int = 8) -> str:
    """The last lines of the engine's output, for an error message."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return "\n".join(text.strip().splitlines()[-lines:])
