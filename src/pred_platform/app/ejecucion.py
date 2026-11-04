"""Ayudas de la superficie Ejecución (C4): formulario de lanzamiento, consulta y textos.

La interfaz no decide nada del motor: valida el formulario con ``LaunchSettings`` (la misma
regla que el worker), delega filtros y paginación en ``get_run_status`` y traduce a español lo que
el contrato ya trae (estados, errores). Las escrituras salen de ``app.state.run_control``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import urlencode

from pred_platform import mensajes
from pred_platform.app.presentation import (
    format_count,
    format_decimal,
    format_duration,
    sku_class_label,
)
from pred_platform.app.seleccion import error_title, family_label
from pred_platform.contract.models import (
    Ejecucion,
    ErrorInfo,
    EstadoTarea,
    IngestSummary,
    RunSettings,
    SkuClass,
)
from pred_platform.data.query import DEFAULT_SIZE, LATEST, int_param
from pred_platform.worker.engine import FAMILIES_BY_SKU_CLASS
from pred_platform.worker.service import LaunchSettings

ACTIVE_STATES = ("pendiente", "ejecutando")
RESUMABLE_STATES = ("detenida", "completada_con_fallos")
# La única causa que reanudar no corrige: el motor rechaza la identidad guardada de la corrida.
INCOMPATIBLE_CONFIG = "run.incompatible_config"
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


def error_action(code: str) -> str | None:
    """Lo que el operador puede hacer ante ``code`` (catálogo único en `mensajes`)."""
    return mensajes.accion(code)


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


@dataclass(frozen=True, slots=True)
class LaunchIngest:
    """Una ingesta aceptada que ofrece el formulario; una que declara datos sintéticos no se lanza.

    La plataforma solo ejecuta datos reales: el motor rechazaría esa ingesta al lanzarla.
    """

    item: IngestSummary
    synthetic: bool


SYNTHETIC_INGEST = (
    "La ingesta elegida declara datos sintéticos y la plataforma solo ejecuta datos reales. "
    "Elija una ingesta de datos reales."
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


def _either(names: Sequence[str]) -> str:
    """«A», «A o B», «A, B o C»."""
    return names[0] if len(names) == 1 else f"{', '.join(names[:-1])} o {names[-1]}"


def _both(names: Sequence[str]) -> str:
    """«A», «A y B», «A, B y C»."""
    return names[0] if len(names) == 1 else f"{', '.join(names[:-1])} y {names[-1]}"


def uncovered_classes(by_class: Mapping[SkuClass, int], families: Sequence[str]) -> str | None:
    """Qué clases de la ingesta quedan con menos de dos familias elegidas; ``None`` si ninguna.

    El motor compara al menos dos familias por SKU (ADR-023): si solo una entrega candidato, excluye
    el SKU antes de L3 (``eligibility.fewer_than_two_families``), y sin ninguna la etapa L2 falla.
    Por eso se avisa antes de lanzar, con las familias que la clase admite.
    """
    parts = []
    for sku_class, count in by_class.items():
        admitted = FAMILIES_BY_SKU_CLASS.get(sku_class, ())
        chosen = [f for f in admitted if f in families]
        if not count or len(chosen) >= min(2, len(admitted)):
            continue
        missing = [family_label(f) for f in admitted if f not in chosen]
        verb = "queda" if count == 1 else "quedan"
        who = f"{format_count(count)} SKU {sku_class_label(sku_class)}"
        if chosen:
            parts.append(
                f"{who} {verb} con una sola familia ({family_label(chosen[0])}): "
                f"agregue {_either(missing)}."
            )
        elif len(missing) == 2:
            parts.append(f"{who} {verb} sin modelo: agregue {_both(missing)}.")
        else:
            parts.append(f"{who} {verb} sin modelo: agregue dos de {_either(missing)}.")
    if not parts:
        return None
    return " ".join(parts) + " El motor compara al menos dos familias por SKU."


def families_hint() -> str:
    """Qué familias admite cada clase de demanda, para elegirlas antes de lanzar."""
    groups: dict[tuple[str, ...], list[str]] = {}
    for sku_class, admitted in FAMILIES_BY_SKU_CLASS.items():
        groups.setdefault(admitted, []).append(sku_class_label(sku_class))
    parts = []
    for admitted, classes in groups.items():
        names = [family_label(f) for f in admitted]
        which = "las cuatro" if len(admitted) == len(FAMILY_CHOICES) else _both(names)
        parts.append(f"{_both(classes)} {'admite' if len(classes) == 1 else 'admiten'} {which}")
    return f"Cada SKU necesita al menos dos familias que admitan su clase: {'; '.join(parts)}."


# ----------------------------------------------------------------------------- vistas
@dataclass(frozen=True, slots=True)
class RunError:
    """El error que guardó la ejecución, en las palabras del catálogo de mensajes."""

    title: str
    detail: str | None
    action: str | None
    code: str
    retryable: bool


def run_error(info: ErrorInfo | None) -> RunError | None:
    """El error que guardó la ejecución, con el título y el paso siguiente del catálogo."""
    if info is None:
        return None
    return RunError(
        title=error_title(info.code, info.stage, info.row_index, info.column),
        detail=info.detail,
        action=error_action(info.code),
        code=info.code,
        retryable=info.retryable,
    )


@dataclass(frozen=True, slots=True)
class RunActions:
    """Qué puede hacerse con la ejecución mostrada y, si algo no, por qué."""

    can_stop: bool
    can_resume: bool
    # Reanudar no se ofrece si la corrida se detuvo por un error que reintentar no corrige.
    offers_resume: bool
    disabled_reason: str | None


def run_actions(
    estado: str | None, *, reason: str | None, error: RunError | None = None
) -> RunActions:
    """``reason`` es por qué las escrituras están apagadas (``RunControl.reason``)."""
    if estado is None:
        return RunActions(False, False, False, reason)
    # La misma regla que aplica la cola al reanudar (``dal.ejecuciones._resume``): solo una
    # identidad incompatible no se reanuda. Vale también para filas guardadas antes con
    # ``retryable`` falso por un motor que murió sin publicar.
    offers_resume = estado in RESUMABLE_STATES and (
        error is None or error.code != INCOMPATIBLE_CONFIG
    )
    return RunActions(
        can_stop=reason is None and estado in ACTIVE_STATES,
        can_resume=reason is None and offers_resume,
        offers_resume=offers_resume,
        disabled_reason=reason,
    )


def should_poll(
    estado: str | None, counts: Mapping[EstadoTarea, int], *, reason: str | None
) -> bool:
    """Si la página sigue preguntando por el avance.

    Mientras la corrida está en cola o en curso y, después de Detener, hasta que el worker cierra
    las tareas que quedaron abiertas: antes de eso los conteos todavía cambian.
    """
    if reason is not None or estado is None:
        return False
    if estado in ACTIVE_STATES:
        return True
    return estado == "detenida" and counts.get("pendiente", 0) + counts.get("ejecutando", 0) > 0


# Las etapas del motor, en orden (contrato 1.2.0, ``Progress.etapa_actual``).
STAGES: tuple[tuple[str, str], ...] = (
    ("L1", "Ingesta de datos (M1)"),
    ("L2", "Ajuste e HPO (M2)"),
    ("L3", "Validación walk-forward (M2)"),
    ("L4", "Evaluación retrospectiva (M3)"),
)


@dataclass(frozen=True, slots=True)
class Step:
    code: str
    label: str
    state: str  # hecha | en_curso | pendiente


def stage_steps(estado: str, etapa: str | None) -> list[Step]:
    """Las cuatro etapas y dónde va la corrida; vacío si no se sabe (en cola o detenida)."""
    codes = [code for code, _ in STAGES]
    if estado in FINISHED_STATES:
        current = len(codes)
    elif estado == "ejecutando" and etapa in codes:
        current = codes.index(etapa)
    else:
        return []
    return [
        Step(code, label, "hecha" if i < current else "en_curso" if i == current else "pendiente")
        for i, (code, label) in enumerate(STAGES)
    ]


def elapsed_text(run: Ejecucion, now: datetime) -> str | None:
    """Cuánto lleva o cuánto duró la corrida; ``None`` si todavía no empezó."""
    if run.iniciada_en is None:
        return None
    end = run.finalizada_en if run.finalizada_en is not None else now
    return format_duration((end - run.iniciada_en).total_seconds())


def settings_items(settings: RunSettings) -> list[tuple[str, str]]:
    """Los ajustes de la ejecución como filas «etiqueta, valor» para mostrarlos."""
    families = (
        ", ".join(family_label(family) for family in settings.families)
        if settings.families
        else "—"
    )
    metrics = dict(METRIC_CHOICES)
    return [
        ("Familias", families),
        ("Métrica de selección", metrics.get(settings.metric, settings.metric)),
        ("Mínimo de entrenamiento", f"{settings.min_train} días"),
        ("Horizonte", f"{settings.horizon} días"),
        ("Paso entre ventanas", f"{settings.step} días"),
        ("Estacionalidad", f"{settings.seasonality} días"),
        (
            "Agregación",
            f"{settings.aggregation} (recorte {format_decimal(settings.trim, 2)})"
            if settings.aggregation is not None and settings.trim is not None
            else "Lo informa el motor al terminar la corrida",
        ),
    ]
