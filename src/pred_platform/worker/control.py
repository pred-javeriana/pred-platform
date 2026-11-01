"""Launch, stop and resume runs from the interface (TASK-UI-1.2-C4).

These are writes, so they are not part of the contract's read models (section 19 of the data
contract) nor of ``DataRepository``: the views get them from ``app.state.run_control``, the same
way they get the repository. ``DalRunControl`` writes ``ejecuciones`` for the worker to follow;
``UnavailableRunControl`` is what fixtures use, so the screen can show the actions disabled and
say why.
"""

from __future__ import annotations

from contextlib import closing
from typing import Protocol

from pred_platform.config import Settings
from pred_platform.dal import ejecuciones as dal
from pred_platform.dal.ejecuciones import RunRequestError
from pred_platform.worker.engine import require_real_origin
from pred_platform.worker.errores import run_stopped_error
from pred_platform.worker.service import (
    EnqueueResult,
    LaunchSettings,
    enqueue_run,
)


class ControlUnavailable(Exception):
    """The actions cannot be taken in this configuration (the message says why)."""


class RunControl(Protocol):
    """What the Ejecución screen can ask for. ``reason`` is why it cannot, when it cannot."""

    @property
    def reason(self) -> str | None: ...

    def launch(self, ingest_id: str, settings: LaunchSettings) -> EnqueueResult: ...

    def stop(self, ejecucion_id: int) -> bool: ...

    def resume(self, ejecucion_id: int) -> bool: ...


class DalRunControl:
    """Queues, stops and resumes runs in the platform's database."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._db_path = settings.db_path

    @property
    def reason(self) -> str | None:
        return None

    def launch(self, ingest_id: str, settings: LaunchSettings) -> EnqueueResult:
        """Queue a run of an accepted ingestion (an identical run that exists is returned).

        Raises:
            RunRequestError: the ingestion is unknown or was not accepted.
        """
        return enqueue_run(self._settings, ingest_id, settings)

    def stop(self, ejecucion_id: int) -> bool:
        """Stop a queued or running run. False if it had already finished."""
        with closing(dal.connect(self._db_path)) as conn:
            return dal.request_stop(conn, ejecucion_id, error=run_stopped_error())

    def resume(self, ejecucion_id: int) -> bool:
        """Queue again a stopped run or one that ended with failures. False if it cannot be.

        Raises:
            RunRequestError: the run saved synthetic data, an undeclared origin or no origin.
        """
        with closing(dal.connect(self._db_path)) as conn:
            run = dal.get_run(conn, ejecucion_id)
            if run is not None:
                try:
                    require_real_origin(run.parametros)
                except ValueError as exc:  # el worker la detendría sin arrancar el motor
                    raise RunRequestError(str(exc)) from exc
            return dal.resume_run(conn, ejecucion_id)


class UnavailableRunControl:
    """The actions are off; every one raises ``ControlUnavailable`` with the reason."""

    def __init__(self, reason: str) -> None:
        self._reason = reason

    @property
    def reason(self) -> str | None:
        return self._reason

    def launch(self, ingest_id: str, settings: LaunchSettings) -> EnqueueResult:
        raise ControlUnavailable(self._reason)

    def stop(self, ejecucion_id: int) -> bool:
        raise ControlUnavailable(self._reason)

    def resume(self, ejecucion_id: int) -> bool:
        raise ControlUnavailable(self._reason)


def build_run_control(settings: Settings) -> RunControl:
    """``DalRunControl`` with the real database; with fixtures, the disabled one."""
    if settings.data_source == "fixture":
        return UnavailableRunControl(
            "Estás viendo datos de prueba: lanzar, detener y reanudar necesitan la base real "
            "(PRED_DATA_SOURCE=dal)."
        )
    return DalRunControl(settings)


__all__ = [
    "ControlUnavailable",
    "DalRunControl",
    "RunControl",
    "RunRequestError",
    "UnavailableRunControl",
    "build_run_control",
]
