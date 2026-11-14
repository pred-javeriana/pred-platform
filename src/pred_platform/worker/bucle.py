"""The worker loop: claim a pending run, launch the engine, follow it, settle it.

One run at a time (the engine already parallelises SKU x family units inside a run), and one worker
at a time per database (``candado.WorkerLock``): a second worker waits, or with ``--once`` exits
without running anything, with no guarantee that another worker will run the queue. Every state
change is its own short transaction, so a crash loses at most the event in flight; on the next
start, a run left ``ejecutando`` is queued again with the same ``run_id`` and the engine resumes.
A run whose database failure could not even be recorded is stopped as soon as the database answers
again, before the next claim, so a running worker never leaves an unowned run ``ejecutando``.
A run whose final files could not be imported becomes ``detenida`` with
``platform.result_import_failed`` (recorded the same way), never a finished run without results;
the next worker start, or the operator's resume, runs it again with the same files.
"""

from __future__ import annotations

import logging
import sqlite3
import subprocess
import threading
import time
from collections.abc import Callable
from contextlib import closing
from typing import Any

from pred_platform.config import Settings
from pred_platform.dal import ejecuciones as dal
from pred_platform.dal.ejecuciones import Ejecucion
from pred_platform.worker import engine
from pred_platform.worker.candado import WorkerLock
from pred_platform.worker.errores import platform_error, result_import_error, run_stopped_error
from pred_platform.worker.importacion import finalize, fresh_summary
from pred_platform.worker.progreso import ProgressFollower
from pred_platform.worker.solo_lectura import database_read_only

_logger = logging.getLogger("pred_platform.worker")

Launcher = Callable[..., "subprocess.Popen[bytes]"]


class _ResultImportFailed(Exception):
    """The engine's final files could not be imported; the cause is ``__cause__``."""


def _unimported(error: dict[str, Any]) -> dict[str, Any]:
    """What a run the engine already published records when ``error`` stopped its import."""
    if error["code"] == "platform.result_import_failed":
        return error
    return result_import_error(
        f"El motor publicó la corrida, pero sus archivos finales no se importaron: "
        f"{error['detail']}"
    )


class Worker:
    """Runs queued engine runs for one database."""

    def __init__(
        self,
        settings: Settings,
        *,
        poll_interval: float = 0.5,
        launcher: Launcher = engine.launch,
        lock: WorkerLock | None = None,
    ) -> None:
        self.settings = settings
        self.poll_interval = poll_interval
        self._launch = launcher
        self._lock = lock or WorkerLock.for_database(settings.db_path)
        # The run this worker claimed but could not record as stopped, with the error to record.
        self._unsettled: tuple[Ejecucion, dict[str, Any]] | None = None
        # The runs whose final result import failed after the engine had finished.
        self._import_failed: set[str] = set()

    # ------------------------------------------------------------------ lifecycle
    def run_forever(self, stop: threading.Event) -> None:
        """Process queued runs until shutdown, leaving interrupted work queued for restart.

        While another worker holds the database, the database is read-only, or its lock cannot
        be opened, this worker waits without touching any run and retries until shutdown.
        """
        if not self._wait_until_writable(stop) or not self._wait_for_lock(stop):
            return
        try:
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
        finally:
            self._lock.release()

    def drain(self) -> bool:
        """Run the queued runs until none is left and no claim is left unsettled (``--once``).

        Returns whether every final result import succeeded; a failed import leaves its run
        recoverable and the caller exits non-zero.

        While another worker holds the database, this one exits without running anything;
        queued work waits for that worker or the next one. A read-only database is left alone.
        """
        if database_read_only(self.settings.db_path):
            _logger.warning(
                "La base %s es de solo lectura: no se ejecuta ninguna corrida",
                self.settings.db_path,
            )
            return True
        try:
            locked = self._lock.acquire()
        except OSError as exc:
            self._log_unlockable(exc)
            raise
        if not locked:
            _logger.warning(
                "Otro worker ya atiende %s; --once no ejecutó nada: las corridas en cola quedan "
                "para ese worker o para el siguiente que arranque",
                self.settings.db_path,
            )
            return True
        try:
            self.recover()
            while self.run_next() or self._unsettled is not None:
                if self._unsettled is not None:
                    time.sleep(self.poll_interval)  # the database still fails: wait, then retry
        finally:
            self._lock.release()
        return not self._import_failed

    def _log_unlockable(self, exc: OSError) -> None:
        _logger.error(
            "El worker no puede crear su candado %s (%s): no ejecuta corridas de %s",
            self._lock.path,
            exc,
            self.settings.db_path,
        )

    def _wait_until_writable(self, stop: threading.Event) -> bool:
        """Wait while the database is read-only (a copy served to look at), until ``stop``."""
        if not database_read_only(self.settings.db_path):
            return True
        _logger.warning(
            "La base %s es de solo lectura: el worker espera a que se pueda escribir",
            self.settings.db_path,
        )
        while not stop.wait(self.poll_interval):
            if not database_read_only(self.settings.db_path):
                return True
        return False

    def _wait_for_lock(self, stop: threading.Event) -> bool:
        """Wait for the database lock, retrying access and system errors until ``stop``."""
        waiting = False
        while not stop.is_set():
            try:
                if self._lock.acquire():
                    return True
            except OSError as exc:
                self._log_unlockable(exc)
            else:
                if not waiting:
                    _logger.warning(
                        "Otro worker ya atiende %s; este espera a que termine para seguir la cola",
                        self.settings.db_path,
                    )
                    waiting = True
            stop.wait(self.poll_interval)
        return False

    def recover(self) -> None:
        """Queue again the runs left ``ejecutando`` by a worker that is gone.

        Only the worker that holds the database lock may call it: no other worker is alive to
        own those runs, so an engine still running for one of them is an orphan and is stopped.
        A live engine of another database is left alone with its run: this database is a copy
        taken while that engine ran, and stopping or relaunching it would break the original.
        A pid now used by a process that is no engine is not stopped either, nor a group whose
        processes name different databases.
        """
        db_path = self.settings.db_path
        with closing(dal.connect(db_path)) as conn:
            for run in dal.interrupted_runs(conn):
                if run.pid is not None:
                    owner = engine.engine_owner(run.pid, db_path)
                    if owner == "other":
                        _logger.warning(
                            "Corrida %s: su motor (pid %s) trabaja para otra base; esta base es "
                            "una copia y la corrida queda como está",
                            run.run_id,
                            run.pid,
                        )
                        continue
                    if owner == "conflict":
                        _logger.warning(
                            "Corrida %s: los procesos del grupo de su motor (pid %s) declaran "
                            "bases distintas; no se detienen y la corrida queda como está",
                            run.run_id,
                            run.pid,
                        )
                        continue
                    if owner == "this":
                        engine.terminate(run.pid)
                if dal.requeue(conn, run.id):
                    _logger.info("Corrida %s retomada tras una caída", run.run_id)

    def run_next(self, stop: threading.Event | None = None) -> bool:
        """Claim and run one pending run to the end. Returns whether there was one.

        The caller holds the database lock (``run_forever`` and ``drain`` take it). A run left
        unsettled by a database failure is stopped first; while the database still fails,
        nothing new is claimed.
        """
        with closing(dal.connect(self.settings.db_path)) as conn:
            if not self._settle(conn):
                return False
            run = dal.claim_pending(conn)
            if run is None:
                return False
            try:
                self._execute(conn, run, stop or threading.Event())
            except _ResultImportFailed as failure:
                cause = failure.__cause__
                _logger.exception("Corrida %s: falló la importación de resultados", run.run_id)
                conn.rollback()
                self._import_failed.add(run.run_id)
                self._unsettled = (
                    run,
                    result_import_error(
                        "No se pudieron importar los archivos finales del motor: "
                        f"{type(cause).__name__}: {cause}"
                    ),
                )
                self._settle(conn)
            except sqlite3.Error as exc:  # the database failed before the run was settled
                _logger.error("Corrida %s: falló la base de datos: %s", run.run_id, exc)
                conn.rollback()
                error = platform_error(
                    "platform.database_busy",
                    f"No se pudo registrar el estado de la corrida en la base de datos: {exc}",
                    retryable=True,
                )
                self._unsettled = (run, error)
                self._settle(conn)
            except Exception as exc:  # noqa: BLE001 - settle every unexpected supervision failure
                _logger.exception("Corrida %s: falló la supervisión", run.run_id)
                conn.rollback()
                self._unsettled = (
                    run,
                    platform_error(
                        "pipeline.stage_failed",
                        f"Falló la supervisión del motor: {type(exc).__name__}: {exc}",
                        retryable=True,
                    ),
                )
                self._settle(conn)
        return True

    def _settle(self, conn: sqlite3.Connection) -> bool:
        """Record the stop of the run left unsettled, if any. Returns whether none is left."""
        if self._unsettled is None:
            return True
        run, error = self._unsettled
        try:
            if dal.stop_claimed(
                conn, run.id, intento=run.intento, error=error, unimported=_unimported(error)
            ):
                self._import_failed.add(run.run_id)
        except sqlite3.Error:
            # Still failing: keep the claim and try again on the next pass of the loop.
            _logger.exception("Corrida %s: no se pudo registrar el diagnóstico", run.run_id)
            return False
        self._unsettled = None
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
                    "platform.input_missing",
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
                error=platform_error("platform.input_missing", f"No existe {parquet}."),
            )
            return

        try:
            # Comprobar que se puede registrar el proceso antes de arrancarlo.
            with conn:
                conn.execute("BEGIN IMMEDIATE")
            command = engine.build_command(self.settings, run, parquet)
            # Fijar el desplazamiento antes de lanzar: el motor puede terminar muy rapido.
            follower = ProgressFollower(
                conn, run, folder / "progreso.jsonl", corte=corte, skip_history=True
            )
            summary_path = folder / "corrida.json"
            previous_summary = summary_path.stat() if summary_path.exists() else None
            process = self._launch(command, settings=self.settings, log_path=log_path)
        except engine.OrigenNoReal as exc:
            # Otra configuración no la vuelve real: la siguiente lanzada abre una corrida nueva.
            error = platform_error("run.incompatible_config", str(exc))
            dal.close_open_tasks(conn, run.id, interrupted=error, skipped=error)
            dal.finish_run(conn, run.id, estado="detenida", error=error)
            return
        except ValueError as exc:
            dal.finish_run(
                conn,
                run.id,
                estado="detenida",
                error=platform_error("pipeline.stage_failed", str(exc)),
            )
            return
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

        stopped = False
        stopped_by_user: dict[str, Any] | None = None
        try:
            dal.set_launch(
                conn,
                run.id,
                pid=process.pid,
                ruta_corrida=folder.relative_to(self.settings.data_root.resolve()).as_posix(),
            )
            while process.poll() is None:
                follower.poll()
                if stop.is_set():
                    stopped = True
                    engine.terminate(process.pid)
                    break
                state, error = dal.run_estado(conn, run.id)
                if state == "detenida" and error and error.get("code") == "platform.run_stopped":
                    # The operator stopped the run from the interface (`request_stop`).
                    stopped_by_user = error
                    engine.terminate(process.pid)
                    break
                time.sleep(0.2)
            process.wait()
            follower.poll()
        except ValueError as exc:  # a progress stream this platform cannot read
            engine.terminate(process.pid)
            process.wait()
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
        except sqlite3.Error:  # the database failed with the engine already running
            engine.terminate(process.pid)
            process.wait()
            raise
        except Exception:
            # An unexpected supervision error, such as a progress record without its keys. An
            # attempt the engine already finished (it exited, or sent run_finished after its
            # final files) is imported from those files. Any other attempt is settled by
            # run_next, which also records a published run whose files were not imported.
            finished = process.poll() is not None or follower.finished is not None
            engine.terminate(process.pid)
            process.wait()
            if not (finished and fresh_summary(folder, previous_summary)):
                raise
            _logger.exception(
                "Corrida %s: falló la supervisión; se importan los archivos finales del motor",
                run.run_id,
            )
        except BaseException:
            # An interrupt or a shutdown: the engine runs in its own session, so stop it here.
            # The run stays `ejecutando` for recovery.
            engine.terminate(process.pid)
            process.wait()
            raise

        if stopped_by_user is not None:
            # `request_stop` already left the run `detenida` with its error; the engine is reaped,
            # so release its process and close the tasks it did not finish, if this attempt is
            # still the run's.
            dal.stop_claimed(
                conn,
                run.id,
                intento=run.intento,
                error=stopped_by_user,
                unimported=_unimported(stopped_by_user),
            )
            _logger.info("Corrida %s detenida por el operador", run.run_id)
            return

        if stopped:
            # El cierre de la aplicacion no es una orden del operador de cancelar.
            # El siguiente worker retoma la cola con la misma identidad y checkpoints.
            if not dal.requeue(conn, run.id):
                stop_error = run_stopped_error()
                dal.stop_claimed(
                    conn,
                    run.id,
                    intento=run.intento,
                    error=stop_error,
                    unimported=_unimported(stop_error),
                )
            return

        try:
            estado = finalize(
                conn,
                run,
                run_dir=folder,
                returncode=process.returncode,
                corte=corte,
                families=follower.families,
                log_path=log_path,
                previous_summary=previous_summary,
            )
        except Exception as exc:
            raise _ResultImportFailed from exc
        _logger.info("Corrida %s terminó: %s", run.run_id, estado)
