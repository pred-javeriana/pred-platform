"""Queue a run on the engine (the API behind ``python -m pred_platform.worker --launch``).

Launching is a write, so it is not one of the contract's read models (section 19 of the data
contract): it creates a ``configuraciones`` row with the run's parameters and an ``ejecuciones``
row ``pendiente`` that the worker picks up.
"""

from __future__ import annotations

import hashlib
import json
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pred_platform.dal import ejecuciones as dal
from pred_platform.dal.ejecuciones import RunRequestError
from pred_platform.dal.procedencia import resolver_procedencia

PARAMETERS_SCHEMA_VERSION = 1
_FAMILIES = ("classical", "ml", "dl", "foundation")
_METRICS = ("mae", "rmse", "smape", "mase")
# ``pred-engine run`` has no option for these two: it always evaluates with them, so they are the
# only values a run can record without claiming settings that did not produce its results.
ENGINE_AGGREGATION = "media_recortada"
ENGINE_TRIM = 0.1


def unsupported_evaluation(aggregation: object, trim: object) -> str | None:
    """Why the engine cannot evaluate with this aggregation and trim, or ``None`` if it can."""
    if aggregation == ENGINE_AGGREGATION and type(trim) is float and trim == ENGINE_TRIM:
        return None
    return (
        f"El motor solo evalúa con aggregation={ENGINE_AGGREGATION!r} y trim={ENGINE_TRIM}; "
        f"se recibió aggregation={aggregation!r} y trim={trim!r}."
    )


@dataclass(frozen=True, slots=True)
class LaunchSettings:
    """Parameters of a run; they are stored as ``configuraciones.parametros`` (decision D5)."""

    min_train: int = 40
    horizon: int = 7
    step: int = 7
    seasonality: int = 7
    metric: Literal["mae", "rmse", "smape", "mase"] = "rmse"
    families: tuple[str, ...] = ("classical", "ml", "dl")
    trials: int | None = None
    seed: int = 0
    workers: int = 2
    # The engine's evaluation defaults; recorded so the run's settings are complete (RunSettings).
    aggregation: str = ENGINE_AGGREGATION
    trim: float = ENGINE_TRIM

    def __post_init__(self) -> None:
        for name in ("min_train", "horizon", "step", "seasonality", "workers"):
            value = getattr(self, name)
            if type(value) is not int or value < 1:
                raise ValueError(f"{name} debe ser un entero >= 1, se recibió {value!r}")
        if (reason := unsupported_evaluation(self.aggregation, self.trim)) is not None:
            raise ValueError(reason)
        if self.trials is not None and (type(self.trials) is not int or self.trials < 1):
            raise ValueError(f"trials debe ser un entero >= 1 o nulo, se recibió {self.trials!r}")
        if type(self.seed) is not int or self.seed < 0:
            raise ValueError(f"seed debe ser un entero >= 0, se recibió {self.seed!r}")
        if self.metric not in _METRICS:
            raise ValueError(f"metric debe ser una de {_METRICS}, se recibió {self.metric!r}")
        if not self.families or any(f not in _FAMILIES for f in self.families):
            raise ValueError(f"families debe usar solo {_FAMILIES}, se recibió {self.families!r}")

    def to_parametros(self) -> dict[str, Any]:
        return {
            "schema_version": PARAMETERS_SCHEMA_VERSION,
            "min_train": self.min_train,
            "horizon": self.horizon,
            "step": self.step,
            "seasonality": self.seasonality,
            "metric": self.metric,
            "families": list(self.families),
            "trials": self.trials,
            "seed": self.seed,
            "datos_sinteticos": False,  # la plataforma solo trabaja con datos reales
            "workers": self.workers,
            "aggregation": self.aggregation,
            "trim": self.trim,
        }


def run_id_for(ingest_sha256: str, settings: LaunchSettings) -> str:
    """A stable id from the input and the settings that change results (not ``workers``).

    Asking for the same input and settings again resolves to the same run, which is how a resume
    reuses the engine's stored HPO studies.
    """
    parametros = settings.to_parametros()
    parametros.pop("workers")
    # La procedencia pertenece a la ingesta inmutable, no al ajuste M2.
    parametros.pop("datos_sinteticos")
    digest = hashlib.sha256(
        json.dumps({"input": ingest_sha256, "settings": parametros}, sort_keys=True).encode()
    ).hexdigest()
    return f"r-{digest[:16]}"


@dataclass(frozen=True, slots=True)
class EnqueueResult:
    ejecucion_id: int
    run_id: str
    queued: bool  # False: it already existed and is pending, running or completed


def enqueue_run(
    db_path: Path, ingest_id: str, settings: LaunchSettings | None = None
) -> EnqueueResult:
    """Queue a run for an accepted ingestion.

    Raises:
        RunRequestError: the ingestion is unknown or was not accepted.
    """
    chosen = settings or LaunchSettings()
    with closing(dal.connect(db_path)) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        dal.accepted_ingestion_id(conn, ingest_id)
        try:
            origen = resolver_procedencia(conn, ingest_id, False)
        except ValueError as exc:
            raise RunRequestError(str(exc)) from exc
        run_id = run_id_for(ingest_id, chosen)
        previa = conn.execute(
            "SELECT c.id, c.parametros, i.sha256, e.estado FROM ejecuciones e "
            "JOIN configuraciones c ON c.id=e.configuracion_id "
            "JOIN ingestas i ON i.id=e.ingesta_id WHERE e.run_id=?",
            (run_id,),
        ).fetchone()
        if previa is not None:
            parametros = json.loads(previa[1])
            # A missing key is a legacy queue entry; an explicit null is a declared unknown
            # origin, which only a run that has not started yet may still resolve.
            antigua = "datos_sinteticos" not in parametros
            origen_previo = parametros.get("datos_sinteticos")
            cambia = not antigua and origen_previo != origen
            if previa[2] != ingest_id or (
                cambia and (origen_previo is not None or previa[3] != "pendiente")
            ):
                # Raising rolls back the whole transaction, including the provenance resolved above.
                raise RunRequestError("la identidad o procedencia de la ejecucion no puede cambiar")
            if antigua or cambia:
                parametros["datos_sinteticos"] = origen
                conn.execute(
                    "UPDATE configuraciones SET parametros=? WHERE id=?",
                    (json.dumps(parametros, sort_keys=True), previa[0]),
                )
        ejecucion_id, queued = dal.create_run(
            conn,
            ingest_sha256=ingest_id,
            parametros=chosen.to_parametros(),
            seed=chosen.seed,
            run_id=run_id,
        )
    return EnqueueResult(ejecucion_id, run_id, queued)


__all__ = [
    "EnqueueResult",
    "LaunchSettings",
    "RunRequestError",
    "enqueue_run",
    "run_id_for",
    "unsupported_evaluation",
]
