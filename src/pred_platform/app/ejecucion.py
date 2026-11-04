"""Ayudas de la superficie Ejecución (C4): formulario de lanzamiento, consulta y textos.

La interfaz no decide nada del motor: valida el formulario con ``LaunchSettings`` (la misma
regla que el worker), delega filtros y paginación en ``get_run_status`` y traduce a español lo que
el contrato ya trae (estados, errores). Las escrituras salen de ``app.state.run_control``.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import urlencode

from pred_platform import mensajes
from pred_platform.app.presentation import (
    FAMILY_ORDER,
    SKU_CLASS_ORDER,
    STAGE_ORDER,
    aggregation_label,
    family_label,
    format_count,
    format_decimal,
    format_duration,
    metric_label,
    model_label,
    sku_class_label,
    stage_label,
)
from pred_platform.app.seleccion import error_title
from pred_platform.contract.models import (
    Ejecucion,
    ErrorInfo,
    EstadoTarea,
    IngestSummary,
    RunSettings,
    SkuClass,
    TopologyMetrics,
)
from pred_platform.dal.lectura import SQLITE_MAX_INTEGER
from pred_platform.data.query import DEFAULT_SIZE, LATEST, int_param
from pred_platform.worker.engine import FAMILIES_BY_SKU_CLASS
from pred_platform.worker.service import LaunchSettings

ACTIVE_STATES = ("pendiente", "ejecutando")
RESUMABLE_STATES = ("detenida", "completada_con_fallos")
# La única causa que reanudar no corrige: el motor rechaza la identidad guardada de la corrida.
INCOMPATIBLE_CONFIG = "run.incompatible_config"
FINISHED_STATES = ("completada", "completada_con_fallos")

# El modelo que corre cada familia (pred-engine, una estrategia por familia).
_FAMILY_MODELS: dict[str, str] = {
    "classical": "sarima",
    "ml": "lightgbm",
    "dl": "mlp",
    "foundation": "chronos2",
}
# Familias que ofrece el formulario, en el orden del contrato: «Clásica (SARIMA)».
FAMILY_CHOICES: tuple[tuple[str, str], ...] = tuple(
    (code, f"{family_label(code)} ({model_label(_FAMILY_MODELS[code])})") for code in FAMILY_ORDER
)
METRIC_CHOICES: tuple[tuple[str, str], ...] = tuple(
    (code, metric_label(code)) for code in ("rmse", "mae", "smape", "mase")
)
# Avisos que se muestran tras una acción (el redirect lleva `aviso=<clave>`).
NOTICES: dict[str, tuple[str, str]] = {
    "en_cola": (
        "success",
        "La ejecución quedó en cola; empezará cuando el procesador de ejecuciones la tome.",
    ),
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
    """Parámetros que se delegan en ``get_run_status``, sin filtrar en el cliente.

    ``todas`` pide la lista completa de tareas mientras la ejecución avanza; sin ella, y sin otro
    filtro, la página muestra solo las tareas que necesitan atención (``focus_tasks``).
    """

    ejecucion: str = LATEST
    estado: str | None = None
    sku: str | None = None
    family: str | None = None
    page: int = 1
    size: int = DEFAULT_SIZE
    todas: bool = False

    @property
    def explicit(self) -> bool:
        """Si la persona pidió una lista concreta de tareas (un filtro, una página o todas)."""
        return bool(
            self.todas
            or self.estado
            or self.sku
            or self.family
            or self.page > 1
            or self.size != DEFAULT_SIZE
        )

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
            "tareas": "todas" if self.todas else "",
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
        todas=params.get("tareas") == "todas",
    )


# Mientras la ejecución avanza, la página muestra a lo sumo estas tareas bajo el avance.
FOCUS_SIZE = 5


def focus_tasks(estado: str | None, query: ExecutionQuery) -> bool:
    """Si la lista de tareas se reduce a las que necesitan atención para no tapar el avance.

    Solo mientras la ejecución está en cola o en curso y nadie pidió otra lista: entonces la
    página muestra las tareas fallidas (o, si no hay, las que se ejecutan ahora) y ofrece la lista
    completa en un enlace.
    """
    return estado in ACTIVE_STATES and not query.explicit


def focus_state(counts: Mapping[EstadoTarea, int]) -> str:
    """Qué tareas muestra la lista reducida: las fallidas primero; si no hay, las que corren."""
    return "fallida" if counts.get("fallida", 0) else "ejecutando"


@dataclass(frozen=True, slots=True)
class LaunchIngest:
    """Una ingesta aceptada que ofrece el formulario; una que declara datos sintéticos no se lanza.

    La plataforma solo ejecuta datos reales: el motor rechazaría esa ingesta al lanzarla.
    """

    item: IngestSummary
    synthetic: bool


SYNTHETIC_INGEST = (
    "El archivo elegido declara datos sintéticos y la plataforma solo ejecuta datos reales. "
    "Elija un archivo de datos reales."
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


def _form_int(raw: str) -> int:
    """``int(raw)``, but an integer with more significant digits than ``SQLITE_MAX_INTEGER`` is
    one past that limit, with its sign, because ``int`` refuses more than 4300 digits."""
    text = raw.strip()
    digits = text[1:] if text.startswith(("+", "-")) else text
    if not (digits.startswith("_") or digits.endswith("_") or "__" in digits):
        significant = digits.replace("_", "").lstrip("0")
        if significant.isdecimal() and len(significant) > len(str(SQLITE_MAX_INTEGER)):
            return -(SQLITE_MAX_INTEGER + 1) if text.startswith("-") else SQLITE_MAX_INTEGER + 1
    return int(raw)


def parse_launch_form(
    *,
    families: Sequence[str],
    metric: str | None,
    fields: Mapping[str, str | None],
) -> FormResult:
    """Valida el formulario de lanzamiento con la misma regla que usa el worker."""
    values: dict[str, Any] = {
        "families": list(dict.fromkeys(f for f in families if f)),  # sin repetidas
        "metric": metric or _DEFAULTS.metric,
        "trials": (fields.get("trials") or "").strip(),
    }
    errors: dict[str, str] = {}
    numbers: dict[str, int] = {}
    for name, label, minimum in _INT_FIELDS:
        raw = (fields.get(name) or "").strip()
        values[name] = raw
        try:
            number = _form_int(raw)
        except ValueError:
            errors[name] = f"{label}: escriba un número entero."
            continue
        if number < minimum:
            errors[name] = f"{label}: debe ser {'mayor que cero' if minimum else 'cero o más'}."
            continue
        if number > SQLITE_MAX_INTEGER:
            errors[name] = f"{label}: el número es demasiado grande."
            continue
        numbers[name] = number
    trials: int | None = None
    if values["trials"]:
        try:
            trials = _form_int(values["trials"])
        except ValueError:
            errors["trials"] = (
                "Configuraciones a probar por familia: escriba un número entero o déjelo vacío."
            )
        else:
            if trials < 1:
                errors["trials"] = "Configuraciones a probar por familia: debe ser mayor que cero."
            elif trials > SQLITE_MAX_INTEGER:
                errors["trials"] = (
                    "Configuraciones a probar por familia: el número es demasiado grande."
                )
    if not values["families"]:
        errors["families"] = "Elija al menos una familia de modelos."
    elif not set(values["families"]) <= {key for key, _ in FAMILY_CHOICES}:
        errors["families"] = "Elija solo familias de la lista."
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


# Las ventanas walk-forward que el motor exige además del entrenamiento y el horizonte, menos una
# (``ReglasPoda().min_ventanas - 1`` en ``pipeline._exclusion_cause`` del motor).
_WINDOW_STEPS = 3


def history_needed(settings: LaunchSettings) -> int:
    """Los días de historia que el motor exige a un SKU antes del corte con estos ajustes.

    Un SKU con menos días de historia hasta el corte t* (``n_periods``) queda excluido con
    cualquier familia (``eligibility.insufficient_history``).
    """
    return settings.min_train + settings.horizon + _WINDOW_STEPS * settings.step


def compares_nothing(by_class: Mapping[SkuClass, int], families: Sequence[str]) -> bool:
    """Si con estas familias ninguna clase presente de la ingesta tiene dos familias que comparar.

    Entonces ningún SKU puede tener campeón: la etapa L2 no produce candidatos y la corrida se
    detiene, o termina sin nada evaluado; repetirla con las mismas familias da lo mismo.
    """
    present = [c for c, count in by_class.items() if count]
    return bool(present) and not any(
        sum(f in families for f in FAMILIES_BY_SKU_CLASS.get(c, ())) >= 2 for c in present
    )


def short_of_two(by_class: Mapping[SkuClass, int], families: Sequence[str]) -> list[SkuClass]:
    """Las clases presentes de la ingesta con menos de dos familias elegidas que admitan."""
    return [
        c
        for c, count in by_class.items()
        if count and sum(f in families for f in FAMILIES_BY_SKU_CLASS.get(c, ())) < 2
    ]


def _short(rows: Sequence[TopologyMetrics], needed: int) -> list[str]:
    return [row.sku_id for row in rows if row.n_periods < needed]


def classes_without_family(
    by_class: Mapping[SkuClass, int],
    families: Sequence[str],
    skus: Mapping[SkuClass, Sequence[TopologyMetrics]],
    needed: int,
) -> list[SkuClass]:
    """Las clases de la ingesta sin ninguna familia elegida que admitan.

    La política del motor no produce decisiones para esa clase y la etapa L2 falla para toda la
    corrida, así que el formulario no lanza. Una clase cuyos SKU (``skus``) son todos más cortos
    que ``needed`` no cuenta: el motor los excluye antes de L2 con cualquier familia.
    """
    found = []
    for sku_class, count in by_class.items():
        admitted = FAMILIES_BY_SKU_CLASS.get(sku_class, ())
        if not count or not admitted or any(f in families for f in admitted):
            continue
        rows = skus.get(sku_class, ())
        if len(rows) >= count and len(_short(rows, needed)) == len(rows):
            continue
        found.append(sku_class)
    return found


def refusal_text(
    classes: Sequence[SkuClass],
    by_class: Mapping[SkuClass, int],
    skus: Mapping[SkuClass, Sequence[TopologyMetrics]],
    needed: int,
) -> str:
    """Por qué el formulario no lanza (:func:`classes_without_family`) y qué familias agregar.

    Cuenta solo los SKU que llegarían a L2: los más cortos que ``needed`` quedan excluidos de
    todos modos, si se leyeron todas las filas de la clase (``skus``).
    """
    parts = []
    for sku_class in classes:
        count = by_class.get(sku_class, 0)
        rows = skus.get(sku_class, ())
        if len(rows) >= count:
            count -= len(_short(rows, needed))
        names = [family_label(f) for f in FAMILIES_BY_SKU_CLASS.get(sku_class, ())]
        verb = "queda" if count == 1 else "quedan"
        add = _both(names) if len(names) == 2 else f"dos de {_either(names)}"
        parts.append(
            f"{format_count(count)} SKU {sku_class_label(sku_class)} {verb} sin modelo: "
            f"agregue {add}."
        )
    return " ".join(parts) + " Sin ninguna familia para una clase, el motor no puede ejecutar."


def single_family_classes(
    by_class: Mapping[SkuClass, int], families: Sequence[str]
) -> dict[SkuClass, str]:
    """Las clases de la ingesta con una sola familia elegida que admitan, con esa familia."""
    single: dict[SkuClass, str] = {}
    for sku_class, count in by_class.items():
        chosen = [f for f in FAMILIES_BY_SKU_CLASS.get(sku_class, ()) if f in families]
        if count and len(chosen) == 1:
            single[sku_class] = chosen[0]
    return single


@dataclass(frozen=True, slots=True)
class WarningPart:
    """Un grupo de SKU del aviso: la frase, los SKU que nombra, cuántos más cuenta y el consejo."""

    text: str
    skus: tuple[str, ...]
    more: int
    advice: str | None


@dataclass(frozen=True, slots=True)
class ExclusionWarning:
    parts: tuple[WarningPart, ...]
    closing: str


def exclusion_warning(
    families: Sequence[str],
    by_class: Mapping[SkuClass, int],
    skus: Mapping[SkuClass, Sequence[TopologyMetrics]],
    needed: int,
) -> ExclusionWarning:
    """El aviso antes de lanzar con clases de una sola familia: qué SKU quedan sin campeón.

    El motor compara al menos dos familias por SKU (ADR-023). Si la única familia entrega
    candidato, excluye el SKU antes de L3 (``eligibility.fewer_than_two_families``); si falla, M3
    lo juzga ``FALLO_TECNICO``. En los dos casos el SKU no compite. Los SKU más cortos que
    ``needed`` se nombran aparte: el motor los excluye con cualquier familia, así que agregar una
    no los evalúa. ``skus`` nombra los SKU de cada clase; los que no nombra se cuentan.
    """
    parts = []
    for sku_class, family in single_family_classes(by_class, families).items():
        count = by_class.get(sku_class, 0)
        rows = skus.get(sku_class, ())
        short = _short(rows, needed)
        named = [row.sku_id for row in rows if row.n_periods >= needed]
        more = max(count - len(rows), 0)
        label = sku_class_label(sku_class)
        if named or more:
            eligible = len(named) + more
            missing = [
                family_label(f) for f in FAMILIES_BY_SKU_CLASS.get(sku_class, ()) if f != family
            ]
            parts.append(
                WarningPart(
                    f"{format_count(eligible)} SKU {label} "
                    f"{'queda' if eligible == 1 else 'quedan'} con una sola familia "
                    f"({family_label(family)})",
                    tuple(named),
                    more,
                    f"Para evaluarlos, agregue {_either(missing)}.",
                )
            )
        if short:
            parts.append(
                WarningPart(
                    f"{format_count(len(short))} SKU {label} "
                    f"{'queda' if len(short) == 1 else 'quedan'} fuera por historia insuficiente "
                    f"con cualquier familia (menos de {format_count(needed)} días)",
                    tuple(short),
                    0,
                    None,
                )
            )
    if compares_nothing(by_class, families):
        closing = (
            "Con estas familias ningún SKU tiene dos familias que comparar: la corrida no elige "
            "ningún campeón y puede detenerse en la etapa L2."
        )
    else:
        closing = (
            "El motor compara al menos dos familias por SKU: si lanza así, esos SKU quedan fuera "
            "de la comparación y sin campeón."
        )
    return ExclusionWarning(tuple(parts), closing)


def exclusion_confirmation(ingest_id: str, families: Sequence[str]) -> str:
    """La clave con que el operador confirma lanzar con el aviso de :func:`exclusion_warning`.

    Vale solo para esta ingesta y estas familias: si cambia alguna, el formulario vuelve a avisar.
    """
    return f"{ingest_id}:{','.join(sorted(families))}"


def families_hint() -> tuple[tuple[str, tuple[str, ...]], ...]:
    """Qué familias admite cada clase de demanda, para elegirlas antes de lanzar.

    Pares (clase, familias) en el orden de las clases y de las familias; la vista los nombra.
    """
    return tuple(
        (sku_class, tuple(f for f in FAMILY_ORDER if f in FAMILIES_BY_SKU_CLASS[sku_class]))
        for sku_class in SKU_CLASS_ORDER
        if sku_class in FAMILIES_BY_SKU_CLASS
    )


# ----------------------------------------------------------------------------- vistas
@dataclass(frozen=True, slots=True)
class RunError:
    """El error que guardó la ejecución, en las palabras del catálogo de mensajes."""

    title: str
    detail: str | None
    action: str | None
    code: str
    retryable: bool
    stage: str | None = None


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
        stage=info.stage,
    )


# Fallas de L2 que dependen solo de la política del motor, de la ingesta y de los ajustes, así
# que reanudar las repite: la política no da decisiones para una clase, o no quedó ninguna unidad
# que correr (``_no_results_message`` del motor con 0 unidades).
_L2_SAME_ON_RESUME = re.compile(r"no produjo decisiones|L2 sin resultados \(0 unidades")


def _same_on_resume(error: RunError | None) -> bool:
    return (
        error is not None
        and error.code == "pipeline.stage_failed"
        and error.stage == "L2"
        and _L2_SAME_ON_RESUME.search(error.detail or "") is not None
    )


@dataclass(frozen=True, slots=True)
class RunActions:
    """Qué puede hacerse con la ejecución mostrada y, si algo no, por qué."""

    can_stop: bool
    can_resume: bool
    # Reanudar no se ofrece si la corrida se detuvo por un error que reintentar no corrige.
    offers_resume: bool
    disabled_reason: str | None
    # Las familias de la corrida no dan a ninguna clase dos que comparar (``compares_nothing``).
    nothing_to_compare: bool = False


def offers_resume(estado: str, error_code: str | None) -> bool:
    """Si la ejecución se puede reanudar, con la misma regla que aplica la cola al reanudar
    (``dal.ejecuciones._resume``): solo una identidad incompatible no se reanuda.

    Vale también para filas guardadas antes con ``retryable`` falso por un motor que murió sin
    publicar.
    """
    return estado in RESUMABLE_STATES and error_code != INCOMPATIBLE_CONFIG


def run_actions(
    estado: str | None,
    *,
    reason: str | None,
    error: RunError | None = None,
    nothing_to_compare: bool = False,
) -> RunActions:
    """``reason`` es por qué las escrituras están apagadas (``RunControl.reason``).

    Reanudar no se ofrece en tres casos que reintentar no corrige: una identidad incompatible
    (la misma regla que aplica la cola, ``dal.ejecuciones._resume``), una corrida cuyas
    familias no dan a ninguna clase dos que comparar (``nothing_to_compare``) y una falla de L2
    que solo depende de la política y de los datos (``_same_on_resume``). Un error con
    ``retryable`` falso no basta: el motor marca así cualquier etapa que falla, también por una
    causa pasajera que reanudar sí corrige.
    """
    if estado is None:
        return RunActions(False, False, False, reason)
    resumable = (
        offers_resume(estado, error.code if error else None)
        and not nothing_to_compare
        and not _same_on_resume(error)
    )
    return RunActions(
        can_stop=reason is None and estado in ACTIVE_STATES,
        can_resume=reason is None and resumable,
        offers_resume=resumable,
        disabled_reason=reason,
        nothing_to_compare=nothing_to_compare and estado in RESUMABLE_STATES,
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


# Las etapas del motor, en orden (contrato 1.2.0, ``Progress.etapa_actual``); sus nombres son los
# de ``presentation``.
STAGES: tuple[tuple[str, str], ...] = tuple((code, stage_label(code)) for code in STAGE_ORDER)


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


def _percent(share: float) -> str:
    """Una proporción del motor en porcentaje, sin decimales si es entera: 0,1 es «10»."""
    value = round(100 * share, 6)
    return format_decimal(value, 0 if value == int(value) else 1)


def settings_items(settings: RunSettings) -> list[tuple[str, str]]:
    """Los ajustes de la ejecución como filas «etiqueta, valor» para mostrarlos."""
    families = (
        ", ".join(family_label(family) for family in settings.families)
        if settings.families
        else "—"
    )
    return [
        ("Familias", families),
        ("Métrica de selección", metric_label(settings.metric)),
        ("Mínimo de entrenamiento", f"{settings.min_train} días"),
        ("Horizonte", f"{settings.horizon} días"),
        ("Paso entre ventanas", f"{settings.step} días"),
        ("Estacionalidad", f"{settings.seasonality} días"),
        (
            "Resumen de las ventanas",
            f"{aggregation_label(settings.aggregation)} "
            f"(recorte del {_percent(settings.trim)} % en cada extremo)"
            if settings.aggregation is not None and settings.trim is not None
            else "Lo informa el motor al terminar la ejecución",
        ),
    ]
