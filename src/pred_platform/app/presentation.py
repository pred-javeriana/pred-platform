"""Traducción de vocabularios del contrato a presentación (ícono + etiqueta + color).

No calcula métricas ni decide campeones: solo nombra estados que el motor ya produjo.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

StatusKind = Literal["task", "family", "trial", "verdict", "run", "ingest", "severity"]
StatusModifier = Literal["neutral", "info", "ok", "err", "warn", "pruned"]

# Orden de lectura de las clases de demanda en el resumen (no es un recálculo).
SKU_CLASS_ORDER: tuple[str, ...] = ("smooth", "erratic", "intermittent", "lumpy")

_SKU_CLASS_LABELS: dict[str, str] = {
    "smooth": "Smooth (Suave)",
    "erratic": "Erratic (Errática)",
    "intermittent": "Intermittent (Intermitente)",
    "lumpy": "Lumpy (Grumosa)",
}


@dataclass(frozen=True, slots=True)
class StatusPresentation:
    """Canal triple exigido por RNF-USA-04: ícono, texto y color (modificador CSS)."""

    icon: str
    label: str
    modifier: StatusModifier


# Estados de tarea: el contrato y la interfaz usan las mismas claves.
_TASK: dict[str, StatusPresentation] = {
    "pendiente": StatusPresentation("i-clock", "Pendiente", "neutral"),
    "ejecutando": StatusPresentation("i-play", "Ejecutando", "info"),
    "finalizo_m2": StatusPresentation(
        "i-check-circle",
        "Terminó configuración con HPO, esperando evaluación retrospectiva",
        "info",
    ),
    "exitosa": StatusPresentation("i-check", "Exitosa", "ok"),
    "fallida": StatusPresentation("i-x", "Fallida", "err"),
    "no_ejecutable": StatusPresentation("i-ban", "No Ejecutable", "warn"),
}

# Estados de una familia en la selección de un SKU: los de tarea más `excluida` (la política o la
# configuración de la corrida no la incluye; no es un fallo).
_FAMILY: dict[str, StatusPresentation] = {
    **_TASK,
    "excluida": StatusPresentation("i-minus-circle", "Excluida", "neutral"),
}

# Trials HPO: claves del contrato (EstadoTrial); etiquetas de A1/B2.
_TRIAL: dict[str, StatusPresentation] = {
    "pendiente": StatusPresentation("i-clock", "Pendiente", "neutral"),
    "corriendo": StatusPresentation("i-play", "Ejecutando", "info"),
    "completado": StatusPresentation("i-check", "Completada", "ok"),
    "podado": StatusPresentation("i-prune", "Podada", "pruned"),
    "fallido": StatusPresentation("i-x", "Fallida", "err"),
}

# Veredictos retrospectivos: claves del contrato (Veredicto).
_VERDICT: dict[str, StatusPresentation] = {
    "mantiene": StatusPresentation("i-check-circle", "se sostiene", "ok"),
    "parcial": StatusPresentation("i-minus-circle", "se sostiene parcialmente", "warn"),
    "falla": StatusPresentation("i-x-circle", "no se sostiene", "err"),
}

# Corrida: claves de EstadoEjecucion. sin_ejecucion es estado de página, no de chip.
_RUN: dict[str, StatusPresentation] = {
    "pendiente": StatusPresentation("i-clock", "Pendiente", "neutral"),
    "ejecutando": StatusPresentation("i-play", "Ejecutando", "info"),
    "completada": StatusPresentation("i-check", "Completada", "ok"),
    "completada_con_fallos": StatusPresentation("i-warn", "Completada con fallos", "warn"),
    "detenida": StatusPresentation("i-stop", "Detenida", "err"),
}

# Ingesta: claves del contrato; frases de A1 para el diagnóstico.
_INGEST: dict[str, StatusPresentation] = {
    "accepted": StatusPresentation("i-check-circle", "Datos válidos", "ok"),
    "rejected": StatusPresentation("i-warn", "El archivo necesita correcciones", "warn"),
    "failed": StatusPresentation("i-x-circle", "No fue posible procesar el archivo", "err"),
    "needs_confirmation": StatusPresentation("i-info", "Se requiere confirmación", "info"),
}

# Severidad de diagnósticos y bitácora de calidad.
_SEVERITY: dict[str, StatusPresentation] = {
    "info": StatusPresentation("i-info", "info", "info"),
    "advertencia": StatusPresentation("i-warn", "advertencia", "warn"),
    "error": StatusPresentation("i-x-circle", "error", "err"),
}

_VOCABULARIES: dict[StatusKind, dict[str, StatusPresentation]] = {
    "task": _TASK,
    "family": _FAMILY,
    "trial": _TRIAL,
    "verdict": _VERDICT,
    "run": _RUN,
    "ingest": _INGEST,
    "severity": _SEVERITY,
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


def sku_class_label(code: str) -> str:
    """Etiqueta de clase de demanda; el código del contrato si aún no hay traducción."""
    return _SKU_CLASS_LABELS.get(code, code)


def format_count(value: int) -> str:
    """Separador de miles al estilo español, para barras y métricas."""
    return f"{value:,}".replace(",", ".")


def format_decimal(value: float, digits: int = 2) -> str:
    """Decimal con coma, para umbrales y métricas ADI/CV²."""
    return f"{value:.{digits}f}".replace(".", ",")


def abbreviate_id(value: str, width: int = 12) -> str:
    """Hash o identificador recortado para trazabilidad compacta."""
    if len(value) <= width:
        return value
    return value[:width]


def format_when(value: datetime | None) -> str:
    """Marca temporal compacta; em dash si el contrato no trae fecha."""
    if value is None:
        return "—"
    return value.strftime("%Y-%m-%d %H:%M")
