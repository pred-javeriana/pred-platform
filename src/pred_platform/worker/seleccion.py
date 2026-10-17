"""Import the engine's ``seleccion.jsonl`` (contract section 20) into the DAL.

The engine writes one line per SKU with its four families. Here each line is validated, put in
the contract's vocabulary (an error becomes an ``ErrorInfo``, a SKU the engine could not run
becomes an exclusion) and checked against the contract's own ``FamilyEntry``, so whatever is
stored reads back as a valid ``sku_selection``. A bad line only loses its SKU; a file of another
major version is refused whole.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

from pred_platform.contract import models
from pred_platform.dal import selecciones as dal
from pred_platform.dal.ejecuciones import Ejecucion
from pred_platform.worker.engine import MODELO_POR_FAMILIA, model_id
from pred_platform.worker.errores import error_info, exclusion_reason

SCHEMA = "pred-engine.seleccion"
SUPPORTED_MAJOR = 1
FILE_NAME = "seleccion.jsonl"

_logger = logging.getLogger("pred_platform.worker")


class _EngineError(BaseModel):
    model_config = ConfigDict(extra="ignore")

    stage: str
    text: str


class _EngineFamily(BaseModel):
    """A family as the engine writes it. Unknown extra fields of the same major are ignored."""

    model_config = ConfigDict(extra="ignore")

    family: models.Family
    estado: models.EstadoFamilia
    exclusion: models.Exclusion | None = None
    cause: str | None = None
    modelo: str | None = None
    produced_by: str | None = None
    evidence: dict[str, Any] | None = None
    forecast_config: dict[str, Any] | None = None
    forecast_seed: int | None = None
    trials: list[dict[str, Any]] | None = None
    walk_forward: dict[str, Any] | None = None
    error: _EngineError | None = None


class _EngineLine(BaseModel):
    model_config = ConfigDict(extra="ignore")

    sku_id: str
    sku_class: models.SkuClass
    profile: models.Profile
    policy_version: str | None = None
    families: list[_EngineFamily]


@dataclass(slots=True)
class SelectionImport:
    """What importing a run's selection did."""

    found: bool = False
    imported: int = 0
    rejected: list[str] = field(default_factory=list)


def _task_model(family: str) -> str:
    """``familia:algoritmo`` of the unit of ``family``, which names its task."""
    return model_id(family, MODELO_POR_FAMILIA.get(family, family))


def _unique_trial_ids(trials: list[dict[str, Any]] | None) -> list[dict[str, Any]] | None:
    """Give every trial of a study its own id, keeping all of them.

    The engine reuses a ``trial_id`` when an attempt has an invalid configuration and is retried,
    so a study with many trials can repeat one. The first keeps its id; the next ones become
    ``<id>#2``, ``<id>#3``... A trial is never dropped: each one is evidence.
    """
    if trials is None:
        return None
    seen: dict[str, int] = {}
    unique = []
    for trial in trials:
        trial_id = str(trial.get("id", ""))
        seen[trial_id] = seen.get(trial_id, 0) + 1
        unique.append(
            trial if seen[trial_id] == 1 else {**trial, "id": f"{trial_id}#{seen[trial_id]}"}
        )
    return unique


def _family_entry(raw: _EngineFamily) -> models.FamilyEntry:
    """The contract's entry for one engine family, validated by the contract's own rules."""
    exclusion = raw.exclusion
    if raw.estado == "no_ejecutable":
        cause = raw.cause or "El motor no ejecutó este SKU."
        exclusion = models.Exclusion(reason_code=exclusion_reason(cause), detail=cause)  # type: ignore[arg-type]
    error = None
    if raw.estado == "fallida":
        stage = raw.error.stage if raw.error else "L2"
        error = models.ErrorInfo(**error_info(stage, raw.error.text if raw.error else None))  # type: ignore[arg-type]
    return models.FamilyEntry.model_validate(
        {
            "family": raw.family,
            "estado": raw.estado,
            "exclusion": exclusion,
            "modelo": raw.modelo,
            "produced_by": raw.produced_by,
            "evidence": raw.evidence,
            "forecast_config": raw.forecast_config,
            "forecast_seed": raw.forecast_seed,
            "trials": _unique_trial_ids(raw.trials),
            "walk_forward": raw.walk_forward,
            "error": error,
        }
    )


def _to_store(line: _EngineLine) -> dal.SeleccionGuardar:
    families = []
    seen: set[str] = set()
    for raw in line.families:
        if raw.family in seen:
            raise ValueError(f"la familia {raw.family} aparece dos veces")
        seen.add(raw.family)
        entry = _family_entry(raw)
        dump = entry.model_dump(mode="json")
        walk = dump["walk_forward"]
        families.append(
            dal.FamiliaGuardar(
                familia=entry.family,
                estado=entry.estado,
                modelo_tarea=entry.modelo or _task_model(entry.family),
                modelo=entry.modelo,
                producido_por=entry.produced_by,
                exclusion=dump["exclusion"],
                evidencia=dump["evidence"],
                configuracion_pronostico=dump["forecast_config"],
                semilla_pronostico=entry.forecast_seed,
                error=dump["error"],
                ensayos=dump["trials"],
                walk_forward=walk,
            )
        )
    return dal.SeleccionGuardar(
        sku=line.sku_id,
        clase=line.sku_class,
        perfil=line.profile,
        version_politica=line.policy_version,
        familias=families,
    )


def import_selection(
    conn: sqlite3.Connection, run: Ejecucion, *, run_dir: Path, corte: str
) -> SelectionImport:
    """Store the run's ``seleccion.jsonl`` (if the engine left one); never raises for bad data."""
    path = run_dir / FILE_NAME
    result = SelectionImport()
    if not path.is_file():
        return result
    result.found = True
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        result.rejected.append(f"{FILE_NAME}: no se pudo leer ({exc})")
        _logger.warning("Corrida %s: %s", run.run_id, result.rejected[-1])
        return result
    for number, text in enumerate(lines, start=1):
        if not text.strip():
            continue
        try:
            data = json.loads(text)
            schema = str(data.get("schema", "")) if isinstance(data, dict) else ""
            name, _, major = schema.partition("/")
            if name != SCHEMA or not major.isdigit() or int(major) != SUPPORTED_MAJOR:
                result.rejected.append(
                    f"línea {number}: versión no soportada {schema!r}; se esperaba "
                    f"{SCHEMA}/{SUPPORTED_MAJOR}"
                )
                continue
            dal.guardar_seleccion(conn, run.id, corte, _to_store(_EngineLine.model_validate(data)))
            result.imported += 1
        except (
            ValueError,
            ValidationError,
            dal.SelectionImportError,
            sqlite3.DatabaseError,
        ) as exc:
            result.rejected.append(f"línea {number}: {exc}")
    for message in result.rejected:
        _logger.warning("Corrida %s: selección rechazada, %s", run.run_id, message)
    _logger.info(
        "Corrida %s: selección importada (%d SKU, %d rechazados)",
        run.run_id,
        result.imported,
        len(result.rejected),
    )
    return result
