"""The worker loop: claim a pending run, launch the engine, follow it, settle it.

One run at a time (the engine already parallelises SKU x family units inside a run). Every state
change is its own short transaction, so a crash loses at most the event in flight; on the next
start, a run left ``ejecutando`` is queued again with the same ``run_id`` and the engine resumes.
"""

from __future__ import annotations

import logging
import sqlite3
import subprocess
import threading
import time
from collections.abc import Callable
from contextlib import closing

from pred_platform.config import Settings
from pred_platform.dal import ejecuciones as dal
from pred_platform.dal.ejecuciones import Ejecucion
from pred_platform.worker import engine
from pred_platform.worker.errores import platform_error
from pred_platform.worker.importacion import finalize
from pred_platform.worker.progreso import ProgressFollower

_logger = logging.getLogger("pred_platform.worker")

Launcher = Callable[..., "subprocess.Popen[bytes]"]


class Worker:
    """Runs queued engine runs for one database."""

    def __init__(
        self,
        settings: Settings,
        *,
        poll_interval: float = 0.5,
        launcher: Launcher = engine.launch,
    ) -> None:
        self.settings = settings
        self.poll_interval = poll_interval
        self._launch = launcher

    # ------------------------------------------------------------------ lifecycle
    def run_forever(self, stop: threading.Event) -> None:
        """Process queued runs until ``stop`` is set (the running one is stopped and marked)."""
        try:
            self.recover()
        except Exception:  # noqa: BLE001 - the loop must start even if recovery fails
            _logger.exception("No se pudieron retomar las corridas interrumpidas")
        while not stop.is_set():
            try:
                worked = self.run_next(stop)
            except Exception:  # noqa: BLE001 - one bad run must not kill the worker thread
                _logger.exception("La corrida falló de forma inesperada")
                worked = False
            if not worked:
                stop.wait(self.poll_interval)

    def recover(self) -> None:
        """Queue again the runs a crashed platform left ``ejecutando``."""
        with closing(dal.connect(self.settings.db_path)) as conn:
            for run in dal.interrupted_runs(conn):
                if run.pid is not None and engine.is_alive(run.pid):
                    engine.terminate(run.pid)
                dal.requeue(conn, run.id)
                _logger.info("Corrida %s retomada tras una caída", run.run_id)

    def run_next(self, stop: threading.Event | None = None) -> bool:
        """Claim and run one pending run to the end. Returns whether there was one."""
        with closing(dal.connect(self.settings.db_path)) as conn:
            run = dal.claim_pending(conn)
            if run is None:
                return False
            self._execute(conn, run, stop or threading.Event())
        return True

    # ------------------------------------------------------------------- one run
    def _execute(self, conn: sqlite3.Connection, run: Ejecucion, stop: threading.Event) -> None:
        corte = run.t_star
        if run.ruta_parquet is None or corte is None:
            dal.finish_run(
                conn,
                run.id,
                estado="detenida",
                error=platform_error(
                    "platform.artifact_missing",
                    "La ingesta no tiene Parquet publicado ni corte t*; vuelva a ingerirla.",
                ),
            )
            return
        parquet = (self.settings.data_root / run.ruta_parquet).resolve()
        folder = engine.run_dir(self.settings, run)
        log_path = folder / "motor.log"
        if not parquet.is_file():
            dal.finish_run(
                conn,
                run.id,
                estado="detenida",
                error=platform_error("platform.artifact_missing", f"No existe {parquet}."),
            )
            return

        command = engine.build_command(self.settings, run, parquet)
        try:
            process = self._launch(command, settings=self.settings, log_path=log_path)
        except OSError as exc:
            dal.finish_run(
                conn,
                run.id,
                estado="detenida",
                error=platform_error(
                    "platform.engine_unavailable",
                    f"No se pudo lanzar el motor ({' '.join(self.settings.engine_command)}): {exc}",
                    retryable=True,
                ),
            )
            return

        dal.set_launch(
            conn,
            run.id,
            pid=process.pid,
            ruta_corrida=folder.relative_to(self.settings.data_root.resolve()).as_posix(),
        )
        follower = ProgressFollower(conn, run, folder / "progreso.jsonl", corte=corte)
        stopped = False
        try:
            while process.poll() is None:
                follower.poll()
                if stop.is_set():
                    stopped = True
                    engine.terminate(process.pid)
                    break
                time.sleep(0.2)
            process.wait()
            follower.poll()
        except ValueError as exc:  # a progress stream this platform cannot read
            engine.terminate(process.pid)
            dal.close_open_tasks(
                conn,
                run.id,
                interrupted=platform_error("platform.schema_version_unsupported", str(exc)),
                skipped=platform_error("platform.schema_version_unsupported", str(exc)),
            )
            dal.finish_run(
                conn,
                run.id,
                estado="detenida",
                error=platform_error("platform.schema_version_unsupported", str(exc)),
            )
            return

        if stopped:
            detail = "La plataforma se cerró durante la corrida; puede reanudarla."
            error = platform_error("platform.engine_unavailable", detail, retryable=True)
            dal.close_open_tasks(conn, run.id, interrupted=error, skipped=error)
            dal.finish_run(conn, run.id, estado="detenida", error=error)
            return

        estado = finalize(
            conn,
            run,
            run_dir=folder,
            returncode=process.returncode,
            corte=corte,
            families=follower.families,
            log_path=log_path,
        )
        _logger.info("Corrida %s terminó: %s", run.run_id, estado)
