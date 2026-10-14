"""Normalize what the engine reports about a failed unit into the contract's ``ErrorInfo``.

The engine records a failure as ``NombreDeClase: mensaje``. The code is deduced from the class,
following section 11 of the data contract; G6 and G7 will replace this table with the engine's own
stable codes. ``stage`` is the engine stage where it happened (``L2`` or ``L3``).
"""

from __future__ import annotations

from typing import Any

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


def error_info(stage: str, text: str | None) -> dict[str, Any]:
    """The ``ErrorInfo`` of a unit that failed, from the engine's ``NombreDeClase: mensaje``."""
    raw = (text or "").strip()
    name, separator, message = raw.partition(": ")
    if not separator:
        name, message = "", raw
    code = _BY_CLASS.get(name)
    if code == "selection.contract" and "ningun trial" in message.lower():
        code = "selection.no_viable_trial"
    if code is None:
        code = "evaluation.failed" if stage == "L3" else "pipeline.stage_failed"
    return {
        "code": code,
        "stage": stage,
        "severity": "error",
        "detail": message or raw or "La unidad falló sin detalle.",
        "source_exception": name or None,
        "retryable": False,
    }


def exclusion_info(cause: str) -> dict[str, Any]:
    """The ``ErrorInfo`` of a SKU the engine could not run (free text until G7)."""
    lowered = cause.lower()
    if "historia" in lowered or "termina antes" in lowered or "comienza" in lowered:
        code = "model.series_too_short"
    elif "huecos" in lowered or "calendario" in lowered:
        code = "ingest.temporal_continuity"
    else:
        code = "pipeline.stage_failed"
    return {
        "code": code,
        "stage": "L2",
        "severity": "error",
        "detail": cause,
        "retryable": False,
    }


def platform_error(code: str, detail: str, *, retryable: bool = False) -> dict[str, Any]:
    """An error of the platform itself (the engine could not be launched, the app closed...)."""
    return {
        "code": code,
        "stage": "platform",
        "severity": "error",
        "detail": detail,
        "retryable": retryable,
    }
