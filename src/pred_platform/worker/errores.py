"""Normalize what the engine reports about a failed unit into the contract's ``ErrorInfo``.

The engine records a failure as ``NombreDeClase: mensaje`` next to its stable ``code`` (section 11
of the data contract, G6 and G7), and that code is stored as is. Runs written by an older engine
carry no code: for those the code is deduced from the class name (or, for exclusions, from the
cause text). ``stage`` is the engine stage where it happened (``L2`` or ``L3``).
"""

from __future__ import annotations

import re
from typing import Any

from pred_platform.mensajes import reintentable

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


# Exclusions whose eligibility code means "the series has too little history" (7.5.1).
_SHORT_SERIES = frozenset(
    {
        "eligibility.no_admissible_history",
        "eligibility.no_reserved_observations",
        "eligibility.insufficient_history",
    }
)


def engine_code(value: object) -> str | None:
    """The engine's ``code`` if it is a well-formed contract code, else ``None``."""
    return value if isinstance(value, str) and _CODE.match(value) else None


def error_info(stage: str, text: str | None, code: str | None = None) -> dict[str, Any]:
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
    return {
        "code": code,
        "stage": stage,
        "severity": "error",
        "detail": message or raw or "La unidad falló sin detalle.",
        "source_exception": name or None,
        "retryable": reintentable(code),
    }


def _legacy_exclusion_code(cause: str) -> str:
    lowered = cause.lower()
    if "historia" in lowered or "termina antes" in lowered or "comienza" in lowered:
        return "model.series_too_short"
    if "huecos" in lowered or "calendario" in lowered:
        return "ingest.temporal_continuity"
    return "pipeline.stage_failed"


def exclusion_info(cause: str, code: str | None = None) -> dict[str, Any]:
    """The ``ErrorInfo`` of a SKU the engine could not run.

    ``code`` is the engine's ``eligibility.*`` code; without it (an older run) it is deduced from
    the free-text cause.
    """
    code = engine_code(code) or _legacy_exclusion_code(cause)
    return {
        "code": code,
        "stage": "L2",
        "severity": "error",
        "detail": cause,
        "retryable": False,
    }


def exclusion_reason(cause: str, code: str | None = None) -> str:
    """The contract's ``reason_code`` (7.5.1) of a SKU the engine could not run.

    A history too short or ending before t* is ``series_too_short``; any other cause is
    ``execution_failed``. The engine's ``code`` decides; the cause text is only the fallback for
    runs that carry none.
    """
    code = engine_code(code)
    if code is not None:
        return "series_too_short" if code in _SHORT_SERIES else "execution_failed"
    lowered = cause.lower()
    short = "historia" in lowered or "termina antes" in lowered or "comienza" in lowered
    return "series_too_short" if short else "execution_failed"


def run_stopped_error() -> dict[str, Any]:
    """What a run stopped by the operator records (contract section 11)."""
    return platform_error(
        "platform.run_stopped", "La ejecución se detuvo a petición del operador.", retryable=True
    )


def platform_error(code: str, detail: str, *, retryable: bool = False) -> dict[str, Any]:
    """An error of the platform itself (the engine could not be launched, the app closed...)."""
    return {
        "code": code,
        "stage": "platform",
        "severity": "error",
        "detail": detail,
        "retryable": retryable,
    }
