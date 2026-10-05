"""Traducción de vocabularios del contrato a presentación (ícono + etiqueta + color).

No calcula métricas ni decide campeones: solo nombra estados que el motor ya produjo.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

StatusKind = Literal["task", "trial", "verdict", "run"]
StatusModifier = Literal["neutral", "info", "ok", "err", "warn", "pruned"]


@dataclass(frozen=True, slots=True)
class StatusPresentation:
    """Canal triple exigido por RNF-USA-04: ícono, texto y color (modificador CSS)."""

    icon: str
    label: str
    modifier: StatusModifier


# Estados de tarea: el contrato y la interfaz usan las mismas claves.
_TASK: dict[str, StatusPresentation] = {
    "pendiente": StatusPresentation("i-clock", "pendiente", "neutral"),
    "ejecutando": StatusPresentation("i-play", "ejecutando", "info"),
    "exitosa": StatusPresentation("i-check", "exitosa", "ok"),
    "fallida": StatusPresentation("i-x", "fallida", "err"),
    "no_ejecutable": StatusPresentation("i-ban", "no ejecutable", "warn"),
}

# Trials HPO: claves del contrato (EstadoTrial); etiquetas de A1/B2.
_TRIAL: dict[str, StatusPresentation] = {
    "pendiente": StatusPresentation("i-clock", "pendiente", "neutral"),
    "corriendo": StatusPresentation("i-play", "ejecutando", "info"),
    "completado": StatusPresentation("i-check", "completada", "ok"),
    "podado": StatusPresentation("i-prune", "podada", "pruned"),
    "fallido": StatusPresentation("i-x", "fallida", "err"),
}

# Veredictos retrospectivos: claves del contrato (Veredicto).
_VERDICT: dict[str, StatusPresentation] = {
    "mantiene": StatusPresentation("i-check-circle", "se sostiene", "ok"),
    "parcial": StatusPresentation("i-minus-circle", "se sostiene parcialmente", "warn"),
    "falla": StatusPresentation("i-x-circle", "no se sostiene", "err"),
}

# Corrida: claves de EstadoEjecucion. sin_ejecucion es estado de página, no de chip.
_RUN: dict[str, StatusPresentation] = {
    "pendiente": StatusPresentation("i-clock", "pendiente", "neutral"),
    "ejecutando": StatusPresentation("i-play", "ejecutando", "info"),
    "completada": StatusPresentation("i-check", "completada", "ok"),
    "completada_con_fallos": StatusPresentation("i-warn", "completada con fallos", "warn"),
    "detenida": StatusPresentation("i-stop", "detenida", "err"),
}

_VOCABULARIES: dict[StatusKind, dict[str, StatusPresentation]] = {
    "task": _TASK,
    "trial": _TRIAL,
    "verdict": _VERDICT,
    "run": _RUN,
}


class UnknownStatus(ValueError):
    """El llamador pidió un estado que no pertenece al vocabulario indicado."""


def describe_status(kind: StatusKind, code: str) -> StatusPresentation:
    """Resuelve ícono, etiqueta y modificador. Falla si se mezclan vocabularios."""
    table = _VOCABULARIES[kind]
    try:
        return table[code]
    except KeyError as exc:
        raise UnknownStatus(f"estado {code!r} no pertenece al vocabulario {kind}") from exc


def format_count(value: int) -> str:
    """Separador de miles al estilo español, para barras y métricas."""
    return f"{value:,}".replace(",", ".")
