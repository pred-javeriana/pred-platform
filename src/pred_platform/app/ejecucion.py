"""Ayudas de la superficie Ejecución (C4): formulario de lanzamiento, consulta y textos.

La interfaz no decide nada del motor: valida el formulario con ``LaunchSettings`` (la misma
regla que el worker), delega filtros y paginación en ``get_run_status`` y traduce a español lo que
el contrato ya trae (estados, errores). Las escrituras salen de ``app.state.run_control``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

from pred_platform.app.presentation import format_decimal
from pred_platform.app.seleccion import int_param
from pred_platform.contract.models import RunSettings
from pred_platform.data.query import DEFAULT_SIZE, LATEST
from pred_platform.worker.service import LaunchSettings

ACTIVE_STATES = ("pendiente", "ejecutando")
RESUMABLE_STATES = ("detenida", "completada_con_fallos")
FINISHED_STATES = ("completada", "completada_con_fallos")

# Familias que ofrece el formulario, en el orden del contrato.
FAMILY_CHOICES: tuple[tuple[str, str], ...] = (
    ("classical", "Clásica (SARIMA)"),
    ("ml", "Aprendizaje automático (LightGBM)"),
    ("dl", "Aprendizaje profundo (MLP)"),
    ("foundation", "Fundacional (Chronos-2)"),
)
METRIC_CHOICES: tuple[tuple[str, str], ...] = (
    ("rmse", "RMSE"),
    ("mae", "MAE"),
    ("smape", "sMAPE"),
    ("mase", "MASE"),
)

# Avisos que se muestran tras una acción (el redirect lleva `aviso=<clave>`).
NOTICES: dict[str, tuple[str, str]] = {
    "en_cola": ("success", "La ejecución quedó en cola; empezará en cuanto el worker la tome."),
    "existente": (
        "info",
        "Ya existía una ejecución con esta ingesta y estos parámetros; se muestra esa.",
    ),
    "detenida": ("warning", "Se pidió detener la ejecución."),
    "reanudada": ("success", "La ejecución volvió a la cola y retomará sus estudios guardados."),
    "no_aplica": ("info", "La ejecución ya no estaba en un estado que admita esa acción."),
}

# Qué hacer ante cada error (texto accionable). Los títulos están en `seleccion.error_title`.
_ERROR_ACTIONS: dict[str, str] = {
    "model.fit_failed": (
        "Pruebe con más ensayos o con otra familia; las demás familias del SKU no se afectan."
    ),
    "selection.no_viable_trial": (
        "Ninguna configuración terminó: aumente los ensayos o revise la serie del SKU."
    ),
    "model.series_too_short": (
        "Cargue más historia de este SKU o reduzca el mínimo de entrenamiento (min_train)."
    ),
    "ingest.temporal_continuity": (
        "Corrija los huecos de fechas del SKU en el CSV y vuelva a cargarlo."
    ),
    "evaluation.failed": "Reduzca el horizonte o el paso de validación, o pruebe otra familia.",
    "model.foundation_unavailable": (
        "Instale las dependencias de la familia fundacional o quítela de la configuración."
    ),
    "platform.run_stopped": "Reanude la ejecución cuando quiera.",
    "platform.engine_unavailable": "Verifique que el motor esté instalado y reanude la ejecución.",
    "platform.artifact_missing": "Revise el registro del motor (motor.log) y lance de nuevo.",
    "pipeline.stage_failed": "Revise el registro del motor (motor.log) y lance de nuevo.",
}


def error_action(code: str) -> str | None:
    """Lo que el operador puede hacer ante ``code``; ``None`` si todavía no hay consejo."""
    return _ERROR_ACTIONS.get(code)


# ---------------------------------------------------------------------------- consulta
@dataclass(frozen=True, slots=True)
class ExecutionQuery:
    """Parámetros que se delegan en ``get_run_status``, sin filtrar en el cliente."""

    ejecucion: str = LATEST
    estado: str | None = None
    sku: str | None = None
    family: str | None = None
    page: int = 1
    size: int = DEFAULT_SIZE

    def as_kwargs(self) -> dict[str, Any]:
        return {
            "estado": self.estado,
            "sku": self.sku,
            "family": self.family,
            "page": self.page,
            "size": self.size,
        }

    def query_string(self, **overrides: object) -> str:
        values: dict[str, object] = {
            "ejecucion": "" if self.ejecucion == LATEST else self.ejecucion,
            "estado": self.estado or "",
            "sku": self.sku or "",
            "family": self.family or "",
            "page": self.page if self.page > 1 else "",
            "size": "" if self.size == DEFAULT_SIZE else self.size,
        }
        values.update(overrides)
        return urlencode([(k, str(v)) for k, v in values.items() if v not in ("", None)])


def parse_execution_query(params: Mapping[str, str]) -> ExecutionQuery:
    return ExecutionQuery(
        ejecucion=(params.get("ejecucion") or "").strip() or LATEST,
        estado=params.get("estado") or None,
        sku=(params.get("sku") or "").strip() or None,
        family=params.get("family") or None,
        page=int_param(params.get("page"), 1, "page"),
        size=int_param(params.get("size"), DEFAULT_SIZE, "size"),
    )


# -------------------------------------------------------------------------- formulario
_DEFAULTS = LaunchSettings()
_INT_FIELDS: tuple[tuple[str, str, int], ...] = (
    ("min_train", "Mínimo de entrenamiento (días)", 1),
    ("horizon", "Horizonte (días)", 1),
    ("step", "Paso entre ventanas (días)", 1),
    ("seasonality", "Estacionalidad (días)", 1),
    ("workers", "Procesos en paralelo", 1),
    ("seed", "Semilla", 0),
)


def default_form_values() -> dict[str, Any]:
    """Los valores iniciales del formulario: los del motor (``LaunchSettings``)."""
    return {
        "families": list(_DEFAULTS.families),
        "metric": _DEFAULTS.metric,
        "min_train": str(_DEFAULTS.min_train),
        "horizon": str(_DEFAULTS.horizon),
        "step": str(_DEFAULTS.step),
        "seasonality": str(_DEFAULTS.seasonality),
        "trials": "",
        "seed": str(_DEFAULTS.seed),
        "workers": str(_DEFAULTS.workers),
    }


@dataclass(frozen=True, slots=True)
class FormResult:
    """El formulario leído: los ajustes si son válidos y, si no, un mensaje por campo."""

    values: dict[str, Any]
    settings: LaunchSettings | None
    errors: dict[str, str]


def parse_launch_form(
    *,
    families: Sequence[str],
    metric: str | None,
    fields: Mapping[str, str | None],
) -> FormResult:
    """Valida el formulario de lanzamiento con la misma regla que usa el worker."""
    values: dict[str, Any] = {
        "families": [f for f in families if f],
        "metric": metric or _DEFAULTS.metric,
        "trials": (fields.get("trials") or "").strip(),
    }
    errors: dict[str, str] = {}
    numbers: dict[str, int] = {}
    for name, label, minimum in _INT_FIELDS:
        raw = (fields.get(name) or "").strip()
        values[name] = raw
        try:
            number = int(raw)
        except ValueError:
            errors[name] = f"{label}: escriba un número entero."
            continue
        if number < minimum:
            errors[name] = f"{label}: debe ser {'mayor que cero' if minimum else 'cero o más'}."
            continue
        numbers[name] = number
    trials: int | None = None
    if values["trials"]:
        try:
            trials = int(values["trials"])
        except ValueError:
            errors["trials"] = "Ensayos por estudio: escriba un número entero o déjelo vacío."
        else:
            if trials < 1:
                errors["trials"] = "Ensayos por estudio: debe ser mayor que cero."
    if not values["families"]:
        errors["families"] = "Elija al menos una familia de modelos."
    if values["metric"] not in {key for key, _ in METRIC_CHOICES}:
        errors["metric"] = "Elija una de las métricas de la lista."
    if errors:
        return FormResult(values, None, errors)
    try:
        settings = LaunchSettings(
            min_train=numbers["min_train"],
            horizon=numbers["horizon"],
            step=numbers["step"],
            seasonality=numbers["seasonality"],
            metric=values["metric"],
            families=tuple(values["families"]),
            trials=trials,
            seed=numbers["seed"],
            workers=numbers["workers"],
        )
    except ValueError as exc:  # una regla del motor que el formulario no previó
        return FormResult(values, None, {"form": str(exc)})
    return FormResult(values, settings, {})


# ----------------------------------------------------------------------------- vistas
@dataclass(frozen=True, slots=True)
class RunActions:
    """Qué puede hacerse con la ejecución mostrada y, si algo no, por qué."""

    can_stop: bool
    can_resume: bool
    disabled_reason: str | None


def run_actions(estado: str | None, *, reason: str | None) -> RunActions:
    """``reason`` es por qué las escrituras están apagadas (``RunControl.reason``)."""
    if estado is None:
        return RunActions(False, False, reason)
    return RunActions(
        can_stop=reason is None and estado in ACTIVE_STATES,
        can_resume=reason is None and estado in RESUMABLE_STATES,
        disabled_reason=reason,
    )


def settings_items(settings: RunSettings) -> list[tuple[str, str]]:
    """Los ajustes de la ejecución como filas «etiqueta, valor» para mostrarlos."""
    families = ", ".join(settings.families) if settings.families else "—"
    return [
        ("Familias", families),
        ("Métrica", settings.metric),
        ("Mínimo de entrenamiento", f"{settings.min_train} días"),
        ("Horizonte", f"{settings.horizon} días"),
        ("Paso", f"{settings.step} días"),
        ("Estacionalidad", f"{settings.seasonality} días"),
        ("Agregación", f"{settings.aggregation} (recorte {format_decimal(settings.trim, 2)})"),
    ]
