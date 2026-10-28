"""Ayudas de la superficie Resultados, vista «Por SKU» (C2): consulta, textos y evidencia.

La interfaz solo nombra lo que el repositorio ya entregó: no recalcula métricas, no elige
campeones y no filtra ni ordena. Aquí se arma la consulta que se delega en
``list_sku_selections`` y se traduce a español el vocabulario del contrato.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

from pred_platform import mensajes
from pred_platform.app.presentation import format_decimal
from pred_platform.contract.errors import InvalidQuery
from pred_platform.contract.models import (
    Capabilities,
    EvidenciaClasica,
    EvidenciaFundacional,
    EvidenciaML,
    FamilyEntry,
    TrialRow,
    WindowResult,
)
from pred_platform.data.query import DEFAULT_SIZE, LATEST

FAMILY_ORDER: tuple[str, ...] = ("classical", "ml", "dl", "foundation")

_FAMILY_LABELS: dict[str, str] = {
    "classical": "Clásica",
    "ml": "ML",
    "dl": "DL",
    "foundation": "Fundacional",
    # La línea base de M3, que también puede ser la familia campeona de un SKU.
    "seasonal_naive": "Seasonal Naive",
}

_PROFILE_LABELS: dict[str, str] = {
    "dense_stable": "denso y estable",
    "dense_variable": "denso y variable",
    "sparse_stable": "disperso y estable",
    "sparse_variable": "disperso y variable",
}

_EXCLUSION_TEXT: dict[str, str] = {
    "not_in_policy_matrix": "La política de selección no asigna esta familia a la clase del SKU.",
    "not_configured": "La corrida se configuró sin esta familia.",
    "series_too_short": "La serie del SKU es demasiado corta para ejecutarla.",
    "execution_failed": "El motor no pudo ejecutar el SKU.",
}

# Qué necesita la acción «Ejecutar selección» (contrato, sección 7.8) y cómo se llama cada cosa.
_ACTION_REQUIREMENTS: tuple[tuple[str, str], ...] = (
    ("parquet_read", "Lectura del Parquet"),
    ("family_comparison", "Comparación común entre familias"),
    ("selection_results_persisted", "Resultados de selección guardados"),
)

_REASONS: dict[str, str] = {
    "not_implemented": "todavía no está implementada",
    "stage_not_implemented": "la etapa aún no existe",
}


def family_label(code: str) -> str:
    return _FAMILY_LABELS.get(code, code)


def profile_label(code: str) -> str:
    return _PROFILE_LABELS.get(code, code)


# Exclusiones cuyo `detail` ya las explica completas (el motor nombra la política o las familias).
_SELF_EXPLAINED = frozenset({"not_in_policy_matrix", "not_configured"})


def exclusion_text(reason_code: str, detail: str | None) -> str:
    """La razón de una exclusión: el detalle del motor si la explica sola; si no, la frase de la
    plataforma seguida de la causa del motor."""
    base = _EXCLUSION_TEXT.get(reason_code, reason_code)
    if not detail:
        return base
    return detail if reason_code in _SELF_EXPLAINED else f"{base} {detail}"


def error_title(
    code: str,
    stage: str | None = None,
    row_index: int | None = None,
    column: str | None = None,
) -> str:
    """Título legible de un error (catálogo único en `mensajes`); el código mismo si no lo tiene."""
    return mensajes.titulo(code, stage=stage, row_index=row_index, column=column)


def int_param(raw: str | None, default: int, name: str) -> int:
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise InvalidQuery(f"{name} must be an integer, got {raw!r}") from exc


@dataclass(frozen=True, slots=True)
class SelectionQuery:
    """Parámetros que se delegan en ``list_sku_selections``, sin filtrar en el cliente."""

    ejecucion: str = LATEST
    # La corrida M3 exacta cuyo campeón y veredicto se muestran; vacía, la PUBLICADA más nueva.
    m3: str | None = None
    sku_class: str | None = None
    family: str | None = None
    estado: str | None = None
    q: str | None = None
    sort: str = "sku_id"
    descending: bool = False
    page: int = 1
    size: int = DEFAULT_SIZE

    def as_kwargs(self) -> dict[str, Any]:
        return {
            "sku_class": self.sku_class,
            "family": self.family,
            "estado": self.estado,
            "q": self.q,
            "sort": self.sort,
            "descending": self.descending,
            "page": self.page,
            "size": self.size,
        }

    def query_string(self, **overrides: object) -> str:
        """Query string de la vista «Por SKU», con filtros y ejecución conservados."""
        values: dict[str, object] = {
            "vista": "sku",
            "ejecucion": "" if self.ejecucion == LATEST else self.ejecucion,
            "m3": self.m3 or "",
            "sku_class": self.sku_class or "",
            "family": self.family or "",
            "estado": self.estado or "",
            "q": self.q or "",
            "sort": "" if self.sort == "sku_id" else self.sort,
            "descending": "1" if self.descending else "",
            "page": self.page if self.page > 1 else "",
            "size": "" if self.size == DEFAULT_SIZE else self.size,
        }
        values.update(overrides)
        return urlencode(
            [
                (k, "1" if v is True else str(v))
                for k, v in values.items()
                if v not in ("", None, False)
            ]
        )

    def detail_query(self, **overrides: object) -> str:
        """Lo que el detalle de un SKU necesita para volver a esta lista sin perder nada."""
        values: dict[str, object] = {
            "ejecucion": "" if self.ejecucion == LATEST else self.ejecucion,
            "m3": self.m3 or "",
            "volver": self.query_string(),
        }
        values.update(overrides)
        return urlencode([(k, str(v)) for k, v in values.items() if v not in ("", None, False)])


def parse_selection_query(params: Mapping[str, str]) -> SelectionQuery:
    """Lee la query HTTP; los valores desconocidos los rechaza el repositorio (``InvalidQuery``)."""
    return SelectionQuery(
        ejecucion=(params.get("ejecucion") or "").strip() or LATEST,
        m3=(params.get("m3") or "").strip() or None,
        sku_class=params.get("sku_class") or None,
        family=params.get("family") or None,
        estado=params.get("estado") or None,
        q=params.get("q") or None,
        sort=params.get("sort") or "sku_id",
        descending=params.get("descending") in {"1", "true", "on"},
        page=int_param(params.get("page"), 1, "page"),
        size=int_param(params.get("size"), DEFAULT_SIZE, "size"),
    )


# --------------------------------------------------------------------------- la acción
@dataclass(frozen=True, slots=True)
class RunAction:
    """«Ejecutar selección»: habilitada solo con todos los requisitos, con la razón si no."""

    enabled: bool
    missing: tuple[str, ...]


def run_action(capabilities: Capabilities) -> RunAction:
    by_id = {item.id: item for item in capabilities.items}
    missing: list[str] = []
    for capability, label in _ACTION_REQUIREMENTS:
        item = by_id.get(capability)  # type: ignore[call-overload]
        if item is not None and item.available:
            continue
        reason = (
            _REASONS.get(item.reason_code or "", "no está disponible")
            if item
            else ("no está disponible")
        )
        gaps = f" ({', '.join(item.blocked_by)})" if item and item.blocked_by else ""
        missing.append(f"{label}: {reason}{gaps}.")
    return RunAction(enabled=not missing, missing=tuple(missing))


# ------------------------------------------------------------------------- la evidencia
def _number(value: object) -> str:
    if isinstance(value, bool):
        return "sí" if value else "no"
    if isinstance(value, float):
        return f"{value:.4g}".replace(".", ",")
    return str(value)


def compact_config(config: Mapping[str, Any] | None) -> str:
    """Una configuración en una línea: ``p=1, d=1, q=2``. Vacía si no hay."""
    if not config:
        return "—"
    return ", ".join(f"{key}={_number(value)}" for key, value in config.items())


def evidence_items(entry: FamilyEntry) -> list[tuple[str, str]]:
    """Filas «etiqueta, valor» de la evidencia de la configuración elegida."""
    evidence = entry.evidence
    items: list[tuple[str, str]] = []
    if isinstance(evidence, EvidenciaFundacional):
        items.append(("Configuración", compact_config(evidence.configuracion)))
        items.append(("Optimización", "No optimiza: no hay estudio ni ensayos."))
    elif evidence is not None:
        if isinstance(evidence, EvidenciaClasica):
            items.append(("Orden (p, d, q)", _tuple(evidence.order)))
            items.append(("Orden estacional (P, D, Q, m)", _tuple(evidence.seasonal_order)))
        elif isinstance(evidence, EvidenciaML) or hasattr(evidence, "hiperparametros"):
            items.append(("Hiperparámetros", compact_config(evidence.hiperparametros)))  # type: ignore[union-attr]
        items.append(("Métrica objetivo", evidence.metrica_objetivo))
        items.append(("Valor en la búsqueda", format_decimal(evidence.valor, 3)))
        items.append(("Ventanas de la búsqueda", str(evidence.n_ventanas)))
        items.append(("Semilla", str(evidence.seed)))
        if evidence.estudio_hpo:
            items.append(("Estudio HPO", evidence.estudio_hpo))
    if entry.forecast_config:
        items.append(("Configuración de pronóstico", compact_config(entry.forecast_config)))
    if entry.forecast_seed is not None:
        items.append(("Semilla de pronóstico", str(entry.forecast_seed)))
    return items


def _tuple(values: list[int]) -> str:
    return "(" + ", ".join(str(v) for v in values) + ")"


@dataclass(frozen=True, slots=True)
class TrialCounts:
    total: int
    completados: int
    podados: int
    fallidos: int


def trial_counts(trials: list[TrialRow]) -> TrialCounts:
    """Cuenta los ensayos tal como llegaron (para el encabezado de la tabla), sin juzgarlos."""
    states = [trial.estado for trial in trials]
    return TrialCounts(
        total=len(states),
        completados=states.count("completado"),
        podados=states.count("podado"),
        fallidos=states.count("fallido"),
    )


def window_metric_names(windows: list[WindowResult]) -> list[str]:
    """Las métricas que traen las ventanas, en el orden del contrato (sin inventar ninguna)."""
    present = {name for window in windows for name in window.metricas}
    ordered = [name for name in ("mae", "rmse", "smape", "mase") if name in present]
    return ordered + sorted(present - set(ordered))
