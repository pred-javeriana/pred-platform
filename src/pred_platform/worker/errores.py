"""Normalize what the engine reports about a failed unit into the contract's ``ErrorInfo``.

The engine records a failure as ``NombreDeClase: mensaje`` next to its stable ``code`` (section 11
of the data contract, G6 and G7), and that code is stored as is. Runs written by an older engine
carry no code: for those the code is deduced from the class name (or, for exclusions, from the
cause text). ``stage`` is the engine stage where it happened (``L2`` or ``L3``).

Since contract 1.3.0 the engine also reports the error's ``facts`` (section 11.1). They enter
here: facts that do not satisfy the contract's ``ErrorFacts`` are dropped, never stored, so a
newer engine field cannot make a stored task unreadable.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from pydantic import ValidationError

from pred_platform.contract import models
from pred_platform.mensajes import reintentable

_log = logging.getLogger(__name__)

_CODE = re.compile(r"^[a-z_]+\.[a-z_]+$")

_BY_CLASS: dict[str, str] = {
    "AjusteModeloError": "model.fit_failed",
    "SerieCortaError": "model.series_too_short",
    "VentanaInsuficienteError": "model.series_too_short",
    "ModeloFundacionalNoDisponibleError": "model.foundation_unavailable",
    "EspacioInvalidoError": "hpo.invalid_space",
    "EstudioError": "hpo.study_failed",
    "SelectionContractError": "selection.contract",
    "UnregisteredFamilyError": "selection.unregistered_family",
    "UnknownSkuClassError": "selection.unknown_class",
    "RouterConfigurationError": "selection.router_config",
    "DuplicateStrategyError": "selection.router_config",
    "EvaluacionError": "evaluation.failed",
}


def engine_code(value: object) -> str | None:
    """The engine's ``code`` if it is a well-formed contract code, else ``None``."""
    return value if isinstance(value, str) and _CODE.match(value) else None


def error_facts(value: object) -> dict[str, Any] | None:
    """The engine's ``facts`` if they satisfy the contract's ``ErrorFacts``, else ``None``."""
    if not value:
        return None
    try:
        facts = models.ErrorFacts.model_validate(value)
    except ValidationError as exc:
        _log.warning("facts del motor descartados: no cumplen ErrorFacts (%s)", exc)
        return None
    return facts.model_dump(mode="json", exclude_none=True) or None


def error_info(
    stage: str, text: str | None, code: str | None = None, facts: object = None
) -> dict[str, Any]:
    """The ``ErrorInfo`` of a unit that failed, from the engine's ``NombreDeClase: mensaje``."""
    raw = (text or "").strip()
    name, separator, message = raw.partition(": ")
    if not separator:
        name, message = "", raw
    code = engine_code(code)
    if code is None:  # a run written before the engine reported codes
        code = _BY_CLASS.get(name)
        if code == "selection.contract" and "ningun trial" in message.lower():
            code = "selection.no_viable_trial"
        if code is None:
            code = "evaluation.failed" if stage == "L3" else "pipeline.stage_failed"
    info: dict[str, Any] = {
        "code": code,
        "stage": stage,
        "severity": "error",
        "detail": message or raw or "La unidad falló sin detalle.",
        "source_exception": name or None,
        "retryable": reintentable(code),
    }
    checked = error_facts(facts)
    if checked is not None:
        info["facts"] = checked
    return info


def _legacy_exclusion_code(cause: str) -> str:
    lowered = cause.lower()
    if "historia" in lowered or "termina antes" in lowered or "comienza" in lowered:
        return "model.series_too_short"
    if "huecos" in lowered or "calendario" in lowered:
        return "ingest.temporal_continuity"
    return "pipeline.stage_failed"


def exclusion_info(cause: str, code: str | None = None, facts: object = None) -> dict[str, Any]:
    """The ``ErrorInfo`` of a SKU the engine could not run.

    ``code`` is the engine's ``eligibility.*`` code; without it (an older run) it is deduced from
    the free-text cause.
    """
    code = engine_code(code) or _legacy_exclusion_code(cause)
    info: dict[str, Any] = {
        "code": code,
        "stage": "L2",
        "severity": "error",
        "detail": cause,
        "retryable": False,
    }
    checked = error_facts(facts)
    if checked is not None:
        info["facts"] = checked
    return info


def exclusion_reason(cause: str, code: str | None = None) -> str:
    """The contract's ``reason_code`` (7.5.1) of a SKU the engine could not run.

    Keep a valid engine code unchanged. For older runs without a code, a history too short
    or ending before t* is ``series_too_short``; any other cause is ``execution_failed``.
    """
    code = engine_code(code)
    if code is not None:
        return code
    lowered = cause.lower()
    short = "historia" in lowered or "termina antes" in lowered or "comienza" in lowered
    return "series_too_short" if short else "execution_failed"


def run_stopped_error() -> dict[str, Any]:
    """What a run stopped by the operator records (contract section 11)."""
    return platform_error(
        "platform.run_stopped", "La ejecución se detuvo a petición del operador.", retryable=True
    )


def result_import_error(detail: str) -> dict[str, Any]:
    """What a run records when the worker could not import the engine's final files."""
    return platform_error("platform.result_import_failed", detail, retryable=True)


def platform_error(code: str, detail: str, *, retryable: bool = False) -> dict[str, Any]:
    """An error of the platform itself (the engine could not be launched, the app closed...)."""
    return {
        "code": code,
        "stage": "platform",
        "severity": "error",
        "detail": detail,
        "retryable": retryable,
    }
