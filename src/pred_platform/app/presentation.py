"""Cómo se lee en pantalla cada código del contrato: etiqueta, explicación breve, ícono y color.

Este módulo es el único dueño de esas lecturas. Un mismo código (un veredicto, una clase de
demanda, una familia, un modelo, una etapa, una métrica) se lee igual en Datos, Ejecución y
Resultados, y la explicación de una línea que lo acompaña sale de aquí. Los valores guardados, las
respuestas del contrato, las columnas de los CSV y los atributos ``data-*`` y ``aria-*`` conservan
el código; solo cambia lo que la persona lee. No calcula métricas ni decide campeones.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta, timezone
from typing import Any, Literal

StatusKind = Literal["task", "family", "trial", "verdict", "run", "ingest", "severity"]
StatusModifier = Literal["neutral", "info", "ok", "err", "warn", "pruned"]


@dataclass(frozen=True, slots=True)
class StatusPresentation:
    """Canal triple exigido por RNF-USA-04 (ícono, texto y color) y una explicación breve.

    ``hint`` es una línea en lenguaje llano que dice qué significa el estado; ``code`` es el valor
    del contrato, para quien necesite cruzarlo con un CSV o con la tesis.
    """

    icon: str
    label: str
    modifier: StatusModifier
    hint: str | None = None
    code: str = ""


@dataclass(frozen=True, slots=True)
class Term:
    """Un código del contrato leído en español: la etiqueta y una explicación de una línea."""

    code: str
    label: str
    hint: str | None = None


# --------------------------------------------------------------------------- estados
# Estados de tarea: el contrato y la interfaz usan las mismas claves.
_TASK: dict[str, StatusPresentation] = {
    "pendiente": StatusPresentation("i-clock", "Pendiente", "neutral", "Todavía no empieza."),
    "ejecutando": StatusPresentation(
        "i-play", "Ejecutando", "info", "El motor la está procesando."
    ),
    "finalizo_m2": StatusPresentation(
        "i-check-circle",
        "Por evaluar",
        "info",
        "El modelo ya se ajustó y se validó; espera la evaluación final de la ejecución.",
    ),
    "exitosa": StatusPresentation("i-check", "Exitosa", "ok", "Terminó y entregó su resultado."),
    "fallida": StatusPresentation(
        "i-x", "Fallida", "err", "No pudo terminar; la causa dice qué hacer."
    ),
    "no_ejecutable": StatusPresentation(
        "i-ban",
        "No ejecutable",
        "warn",
        "El producto no cumple las condiciones para este modelo; no es un error del sistema.",
    ),
}

# Estados de una familia en la selección de un SKU: los de tarea más `excluida` (la política o la
# configuración de la corrida no la incluye; no es un fallo).
_FAMILY: dict[str, StatusPresentation] = {
    **_TASK,
    "excluida": StatusPresentation(
        "i-minus-circle",
        "Excluida",
        "neutral",
        "La política o los ajustes de la ejecución no incluyen esta familia; no es un fallo.",
    ),
}

# Ensayos de la búsqueda de configuraciones: claves del contrato (EstadoTrial).
_TRIAL: dict[str, StatusPresentation] = {
    "pendiente": StatusPresentation("i-clock", "Pendiente", "neutral", "Todavía no empieza."),
    "corriendo": StatusPresentation(
        "i-play", "Ejecutando", "info", "La búsqueda lo está probando."
    ),
    "completado": StatusPresentation("i-check", "Completada", "ok", "Se probó completo."),
    "podado": StatusPresentation(
        "i-prune",
        "Podada",
        "pruned",
        "La búsqueda lo detuvo antes de terminar porque no prometía; no es un fallo.",
    ),
    "fallido": StatusPresentation("i-x", "Fallida", "err", "No se pudo probar esta configuración."),
}

# Los cinco veredictos que publica la evaluación final (contrato v2). La etiqueta es la palabra de
# la tesis en español corriente y la explicación dice cuándo se emite (pred-engine,
# veredictos.py: se aplica la primera regla que se cumpla, en este orden).
_VERDICT: dict[str, StatusPresentation] = {
    "FALLO_TECNICO": StatusPresentation(
        "i-x-circle",
        "Error técnico",
        "err",
        "Ningún modelo produjo un pronóstico para este producto.",
    ),
    "NO_EVALUABLE": StatusPresentation(
        "i-ban",
        "No evaluable",
        "neutral",
        "No hay demanda observada en el periodo de prueba.",
    ),
    "EVIDENCIA_INSUFICIENTE": StatusPresentation(
        "i-minus-circle",
        "Evidencia insuficiente",
        "warn",
        "Muy pocas ventanas de prueba, o demanda siempre en cero, para concluir.",
    ),
    "VALIDADO": StatusPresentation(
        "i-check-circle",
        "Validado",
        "ok",
        "Se equivoca menos que la línea base, con datos reales y evidencia suficiente.",
    ),
    "EXPLORATORIO": StatusPresentation(
        "i-info",
        "Exploratorio",
        "info",
        "Sin validar: no mejora a la línea base o la comparación quedó incompleta.",
    ),
}

# El orden en que se leen los veredictos: primero los evaluados, como el embudo del contrato.
VERDICT_ORDER: tuple[str, ...] = (
    "VALIDADO",
    "EXPLORATORIO",
    "FALLO_TECNICO",
    "NO_EVALUABLE",
    "EVIDENCIA_INSUFICIENTE",
)

# Ejecución: claves de EstadoEjecucion. sin_ejecucion es estado de página, no de chip. Una
# ejecución completada con tareas fallidas terminó bien: el chip dice «Completada» y la página
# dice al lado cuántas tareas fallaron y cuántos productos tienen veredicto (``run_outcome``).
# Una detenida es una advertencia mientras pueda reanudarse; ``describe_run`` la vuelve error
# cuando reanudarla fallaría igual.
_RUN: dict[str, StatusPresentation] = {
    "pendiente": StatusPresentation(
        "i-clock", "Pendiente", "neutral", "En cola: espera su turno para empezar."
    ),
    "ejecutando": StatusPresentation(
        "i-play", "Ejecutando", "info", "El motor está trabajando en esta ejecución."
    ),
    "completada": StatusPresentation(
        "i-check", "Completada", "ok", "Terminó y todas sus tareas se completaron."
    ),
    "completada_con_fallos": StatusPresentation(
        "i-check", "Completada", "ok", "Terminó; algunas tareas de modelos fallaron."
    ),
    "detenida": StatusPresentation(
        "i-stop", "Detenida", "warn", "Se detuvo antes de terminar; puede reanudarse."
    ),
}
_RUN_STOPPED_FOR_GOOD = StatusPresentation(
    "i-stop",
    "Detenida",
    "err",
    "Se detuvo y reanudarla fallaría igual: corrija la causa y lance una ejecución nueva.",
    code="detenida",
)

# Ingesta: claves del contrato; frases de A1 para el diagnóstico.
_INGEST: dict[str, StatusPresentation] = {
    "accepted": StatusPresentation("i-check-circle", "Datos válidos", "ok"),
    "rejected": StatusPresentation("i-warn", "El archivo necesita correcciones", "warn"),
    "failed": StatusPresentation("i-x-circle", "No fue posible procesar el archivo", "err"),
    "needs_confirmation": StatusPresentation("i-info", "Se requiere confirmación", "info"),
}

# Severidad de diagnósticos y bitácora de calidad.
_SEVERITY: dict[str, StatusPresentation] = {
    "info": StatusPresentation("i-info", "Información", "info"),
    "advertencia": StatusPresentation("i-warn", "Advertencia", "warn"),
    "error": StatusPresentation("i-x-circle", "Error", "err"),
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
    """Resuelve ícono, etiqueta, modificador y explicación. Falla si se mezclan vocabularios."""
    table = _VOCABULARIES[kind]
    try:
        return replace(table[code], code=code)
    except KeyError as exc:
        raise UnknownStatus(f"estado {code!r} no pertenece al vocabulario {kind}") from exc


def describe_run(estado: str, *, resumable: bool) -> StatusPresentation:
    """El estado de una ejecución con el tono que corresponde a lo que se puede hacer con ella.

    Una detenida que puede reanudarse es una advertencia; solo una que reanudar no arreglaría
    (``ejecucion.offers_resume`` es falso, por ejemplo ``run.incompatible_config``) es un error.
    """
    if estado == "detenida" and not resumable:
        return _RUN_STOPPED_FOR_GOOD
    return describe_status("run", estado)


def verdict_label(code: str) -> str:
    """La etiqueta de un veredicto, para usarla dentro de una frase; el código si no se conoce."""
    found = _VERDICT.get(code)
    return found.label if found else code


# Estados de una corrida M3, que la interfaz llama «evaluación» (contrato v2, EstadoM3).
_EVALUATION_STATES: dict[str, str] = {
    "EN_CURSO": "en curso",
    "EVALUADA": "evaluada, sin publicar",
    "SELECCIONADA": "seleccionada, sin publicar",
    "PUBLICADA": "publicada",
    "RECHAZADA": "rechazada",
}


def evaluation_state_label(code: str) -> str:
    """El estado de una evaluación (corrida M3) en minúsculas, para usarlo dentro de una frase."""
    return _EVALUATION_STATES.get(code, code)


# ------------------------------------------------------------------ clases de demanda
# Orden de lectura de las clases de demanda en el resumen (no es un recálculo).
SKU_CLASS_ORDER: tuple[str, ...] = ("smooth", "erratic", "intermittent", "lumpy")

_SKU_CLASSES: dict[str, Term] = {
    "smooth": Term("smooth", "Suave", "Vende casi todos los días, en cantidades parecidas."),
    "erratic": Term(
        "erratic", "Errática", "Vende casi todos los días, pero en cantidades muy variables."
    ),
    "intermittent": Term(
        "intermittent",
        "Intermitente",
        "Muchos días sin venta; cuando vende, en cantidades parecidas.",
    ),
    "lumpy": Term("lumpy", "Grumosa", "Muchos días sin venta y cantidades muy variables."),
}

# ---------------------------------------------------------------------------- familias
FAMILY_ORDER: tuple[str, ...] = ("classical", "ml", "dl", "foundation")

_FAMILIES: dict[str, Term] = {
    "classical": Term("classical", "Clásica", "Modelo estadístico de series de tiempo (SARIMA)."),
    "ml": Term("ml", "Aprendizaje automático", "Árboles de decisión potenciados (LightGBM)."),
    "dl": Term("dl", "Aprendizaje profundo", "Red neuronal de capas densas (MLP)."),
    "foundation": Term(
        "foundation", "Fundacional", "Modelo preentrenado con miles de series (Chronos-2)."
    ),
    # La línea base de la evaluación final, que también puede ser la familia campeona de un SKU.
    "seasonal_naive": Term(
        "seasonal_naive",
        "Línea base estacional",
        "Repite la demanda del ciclo anterior; es la referencia que un modelo debe superar.",
    ),
}

# ----------------------------------------------------------------------------- modelos
# El motor nombra un modelo «familia:modelo» en las tareas y solo «modelo» en la evaluación.
_MODELS: dict[str, Term] = {
    "sarima": Term("sarima", "SARIMA", "Modelo estadístico con tendencia y estacionalidad."),
    "lightgbm": Term("lightgbm", "LightGBM", "Árboles de decisión entrenados en secuencia."),
    "mlp": Term("mlp", "MLP", "Red neuronal de capas densas."),
    "chronos2": Term("chronos2", "Chronos-2", "Modelo fundacional de series, preentrenado."),
    "seasonal_naive": Term(
        "seasonal_naive",
        "Ingenuo estacional",
        "Repite la demanda del mismo día del ciclo anterior.",
    ),
}

# ----------------------------------------------------------------------- etapas del motor
# Las etapas del motor, en orden (contrato 1.2.0, ``Progress.etapa_actual``); L0 y platform solo
# aparecen como etapa de un error.
STAGE_ORDER: tuple[str, ...] = ("L1", "L2", "L3", "L4")

_STAGES: dict[str, Term] = {
    "L0": Term("L0", "Datos sintéticos", "Generación de series sintéticas."),
    "L1": Term(
        "L1",
        "Preparación de los datos",
        "Valida el archivo y clasifica la demanda de cada producto.",
    ),
    "L2": Term(
        "L2",
        "Ajuste de modelos",
        "Cada familia busca su mejor configuración para cada producto.",
    ),
    "L3": Term(
        "L3",
        "Validación con ventanas",
        "Prueba cada modelo en ventanas sucesivas del historial (walk-forward).",
    ),
    "L4": Term(
        "L4",
        "Evaluación final",
        "Compara cada modelo con la línea base en el periodo de prueba y emite el veredicto.",
    ),
    "platform": Term("platform", "Plataforma", "La aplicación web, fuera del motor."),
}

# ----------------------------------------------------------------------------- métricas
_METRICS: dict[str, Term] = {
    "rmse": Term("rmse", "RMSE", "Raíz del error cuadrático medio: pesa más los errores grandes."),
    "mae": Term("mae", "MAE", "Error absoluto medio, en unidades de demanda."),
    "smape": Term("smape", "sMAPE", "Error porcentual absoluto medio, simétrico."),
    "mase": Term("mase", "MASE", "Error absoluto medio escalado por el de un pronóstico ingenuo."),
    "me": Term("me", "ME", "Error medio: dice si el modelo tiende a pasarse o a quedarse corto."),
    "razon_sn": Term(
        "razon_sn",
        "r frente a la línea base",
        "Error del modelo dividido por el de la línea base: menos de 1 es mejor.",
    ),
}

_AGGREGATIONS: dict[str, str] = {
    "media": "Media",
    "mediana": "Mediana",
    "media_recortada": "Media recortada",
}

# Términos de la tesis que la interfaz conserva, con su explicación llana (se muestran con
# ``<abbr title>``). Las métricas se explican en ``_METRICS``.
GLOSSARY: dict[str, str] = {
    "r": "Error del modelo dividido por el de la línea base: menos de 1 es mejor.",
    "HLN-DM": (
        "Prueba de Diebold-Mariano con la corrección de Harvey, Leybourne y Newbold: dice si la "
        "diferencia de error con la línea base es real o puede ser azar."
    ),
    "BH": "Ajuste de Benjamini-Hochberg: corrige los p-valores cuando se hacen muchas pruebas.",
    "BY": "Ajuste de Benjamini-Yekutieli: una corrección más prudente que BH.",
    "FCR": "Tasa de cobertura falsa: el nivel de los intervalos de los SKU significativos.",
    "IUT": "Prueba de intersección-unión: la campeona debe ganarle a cada competidora.",
    "Holm": "Ajuste de Holm: corrige los p-valores entre las clases de demanda.",
    "Wilcoxon": "Prueba de rangos con signo: compara dos modelos SKU a SKU.",
    "Hodges-Lehmann": "Estimación robusta de la razón típica entre dos modelos.",
    "IC": "Intervalo de confianza.",
    "ADI": "Intervalo medio entre días con demanda: cuánto se espera entre una venta y otra.",
    "CV²": "Variabilidad de las cantidades vendidas (coeficiente de variación al cuadrado).",
    "t*": "Fecha de corte: la historia hasta aquí entrena; lo posterior se reserva para probar.",
    "HPO": "Búsqueda automática de la mejor configuración de un modelo.",
    "walk-forward": "Validación que pronostica ventanas sucesivas, siempre con datos anteriores.",
    "p": "Probabilidad de ver una diferencia así si en realidad no la hubiera.",
    "α": "Umbral de significancia: un p menor o igual que α es significativo.",
}


def _term(table: dict[str, Term], code: str) -> Term:
    return table.get(code) or Term(code, code)


def sku_class_term(code: str) -> Term:
    """La clase de demanda y qué significa; el código del contrato si aún no hay traducción."""
    return _term(_SKU_CLASSES, code)


def sku_class_label(code: str) -> str:
    return sku_class_term(code).label


def family_term(code: str) -> Term:
    return _term(_FAMILIES, code)


def family_label(code: str) -> str:
    return family_term(code).label


def model_term(code: str) -> Term:
    """Un modelo del motor: ``classical:sarima``, ``sarima`` o el candidato
    ``1000866/classical/sarima`` se leen «SARIMA»."""
    return _term(_MODELS, code.replace("/", ":").rpartition(":")[2])


def model_label(code: str) -> str:
    return model_term(code).label


def stage_term(code: str) -> Term:
    return _term(_STAGES, code)


def stage_label(code: str) -> str:
    return stage_term(code).label


def metric_term(code: str) -> Term:
    return _term(_METRICS, code)


def metric_label(code: str) -> str:
    return metric_term(code).label


def aggregation_label(code: str) -> str:
    return _AGGREGATIONS.get(code, code)


def glossary_hint(term: str) -> str | None:
    """La explicación llana de un término de la tesis, o de una métrica; ``None`` si no hay."""
    found = _METRICS.get(term.lower())
    return GLOSSARY.get(term) or (found.hint if found else None)


# Opciones de los selectores de filtro: (valor del contrato, etiqueta), con la etiqueta de aquí.
def sku_class_options() -> list[tuple[str, str]]:
    return [("", "Todas")] + [(code, sku_class_label(code)) for code in SKU_CLASS_ORDER]


def family_options(*, baseline: bool = False) -> list[tuple[str, str]]:
    codes = FAMILY_ORDER + (("seasonal_naive",) if baseline else ())
    return [("", "Todas")] + [(code, family_label(code)) for code in codes]


def verdict_options() -> list[tuple[str, str]]:
    return [("", "Todos")] + [(code, _VERDICT[code].label) for code in VERDICT_ORDER]


def status_options(kind: StatusKind, codes: tuple[str, ...]) -> list[tuple[str, str]]:
    return [("", "Todos")] + [(code, describe_status(kind, code).label) for code in codes]


# ------------------------------------------------------------------------- formatos
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


_MONTHS: tuple[str, ...] = (
    "ene",
    "feb",
    "mar",
    "abr",
    "may",
    "jun",
    "jul",
    "ago",
    "sept",
    "oct",
    "nov",
    "dic",
)


# La hora que lee el usuario es la de Bogotá (America/Bogota). Colombia usa UTC−5 sin horario de
# verano desde 1993, así que un desfase fijo da la misma hora que la base de zonas horarias sin
# depender de que el sistema la traiga (Windows no la trae).
LOCAL_TIMEZONE = timezone(timedelta(hours=-5), "America/Bogota")


def _local(value: datetime) -> datetime:
    """El instante en la hora de Bogotá; uno sin zona se guardó en UTC, como todo en PRED."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(LOCAL_TIMEZONE)


def format_when(value: datetime | None, *, with_seconds: bool = False) -> str:
    """Fecha y hora de Bogotá para leer de un vistazo: «4 nov 2026, 4:36 p. m.».

    El motor y la plataforma guardan las horas en UTC; la página las muestra en la hora local y
    deja el instante exacto en UTC para el title (``exact_when``). Em dash si no hay fecha.
    """
    if value is None:
        return "—"
    local = _local(value)
    hour = local.hour % 12 or 12
    period = "a. m." if local.hour < 12 else "p. m."
    time_part = (
        f"{hour}:{local.minute:02d}:{local.second:02d}"
        if with_seconds
        else f"{hour}:{local.minute:02d}"
    )
    return f"{local.day} {_MONTHS[local.month - 1]} {local.year}, {time_part} {period}"


def group_option_label(
    option: Any,
    options_for_seed: Sequence[Any],
    index: int = 1,
) -> str:
    """Etiqueta para una opción de semilla: «Publicada el 27 oct 2026, 5:00 a. m.».

    Si dos opciones de la misma semilla caen en el mismo minuto (su etiqueta sin segundos se
    repite), muestra también los segundos (p. ej. «5:00:40 a. m.»). Si no tiene fecha, usa
    «Publicación {index}».
    """
    if option.creado_en is None:
        return f"Publicación {index}"
    base_labels = [
        format_when(opt.creado_en)
        for opt in options_for_seed
        if getattr(opt, "creado_en", None) is not None
    ]
    my_label = format_when(option.creado_en)
    if base_labels.count(my_label) > 1:
        time_text = format_when(option.creado_en, with_seconds=True)
    else:
        time_text = my_label
    return f"Publicada el {time_text}"


_GROUP_PROBLEM_MAP: dict[str, str] = {
    "Las corridas no comparten la ingesta.": "Las evaluaciones elegidas no usan los mismos datos.",
    "Las corridas difieren en algo distinto de la semilla.": (
        "Las evaluaciones elegidas difieren en algo más que la semilla."
    ),
    "Falta alfa_dm en la política publicada.": (
        "La política publicada no tiene un nivel de significancia válido."
    ),
    "alfa_dm no es válido.": ("La política publicada no tiene un nivel de significancia válido."),
    "Cada semilla debe tener una corrida distinta.": (
        "Cada semilla debe tener una publicación distinta."
    ),
    "Las corridas no comparten los productos y sus clases.": (
        "Las evaluaciones elegidas no comparten los productos y sus clases."
    ),
    "Una prueba no usa el alfa de la política.": (
        "Una prueba no usa el nivel de significancia de la política."
    ),
    "Un producto aparece más de una vez en una corrida.": (
        "Un producto aparece más de una vez en una evaluación."
    ),
    "Seleccione una corrida distinta por semilla.": "Elija una publicación distinta por semilla.",
    "Falta elegir una corrida publicada para cada semilla.": (
        "Falta elegir una publicación para cada semilla."
    ),
    "Falta elegir una evaluación publicada para cada semilla.": (
        "Falta elegir una publicación para cada semilla."
    ),
}

_DYNAMIC_GROUP_PROBLEMS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"^Seleccione una corrida PUBLICADA de la semilla (\d+)\.?$"),
        r"Elija una publicación de la semilla \1.",
    ),
    (
        re.compile(r"^La corrida (.+) no está PUBLICADA\.?$"),
        r"La evaluación \1 no está publicada.",
    ),
    (
        re.compile(r"^La corrida (.+) no declara datos reales\.?$"),
        r"La evaluación \1 no declara datos reales.",
    ),
    (
        re.compile(r"^La corrida (.+) no tiene una semilla entera publicada\.?$"),
        r"La evaluación \1 no tiene una semilla entera publicada.",
    ),
    (
        re.compile(r"^La corrida (.+) no publica los bloques de la reserva\.?$"),
        r"La evaluación \1 no publica los bloques de la reserva.",
    ),
    (
        re.compile(r"^Se requieren las semillas (.+); no se reemplazan corridas\.?$"),
        r"Se requieren las semillas \1; no se reemplazan evaluaciones.",
    ),
)


def group_problem_text(problem: str) -> str:
    """Traduce un aviso del cálculo del grupo al lenguaje llano de la interfaz."""
    if problem in _GROUP_PROBLEM_MAP:
        return _GROUP_PROBLEM_MAP[problem]
    for pattern, replacement in _DYNAMIC_GROUP_PROBLEMS:
        if pattern.match(problem):
            return pattern.sub(replacement, problem)
    return (
        problem.replace("corridas", "evaluaciones")
        .replace("corrida", "evaluación")
        .replace("PUBLICADA", "publicada")
        .replace("ingesta", "datos")
        .replace("alfa_dm", "nivel de significancia")
    )


_GROUP_LUMPY_CAUSES: dict[str, str] = {
    "No hay productos lumpy en el grupo.": "No hay productos de demanda grumosa en el grupo.",
    "Falta la confirmación publicada de lumpy.": (
        "Falta la confirmación publicada de la demanda grumosa."
    ),
    "La campeona de lumpy no es Fundacional en todas las corridas.": (
        "La campeona de la demanda grumosa no es Fundacional en todas las evaluaciones."
    ),
}


def group_lumpy_cause_text(cause: str) -> str:
    """Traduce la causa descriptiva de lumpy al español llano de la página."""
    return _GROUP_LUMPY_CAUSES.get(cause, cause)


def exact_when(value: datetime | None) -> str:
    """El instante exacto que se guardó, para un title: «2026-11-04 21:36:38 UTC (hora universal
    coordinada)»."""
    if value is None:
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S UTC (hora universal coordinada)")


def format_date(value: str | date | None) -> str:
    """Un día del calendario para leer: «11 sept 2016»; el texto tal cual si no es una fecha ISO.

    Un instante (``datetime``) se lee en el día de Bogotá, no en el de UTC.
    """
    if value is None or value == "":
        return "—"
    if isinstance(value, datetime):
        value = _local(value).date()
    try:
        day = value if isinstance(value, date) else date.fromisoformat(str(value)[:10])
    except ValueError:
        return str(value)
    return f"{day.day} {_MONTHS[day.month - 1]} {day.year}"


def format_duration(seconds: float) -> str:
    """Una duración legible de un vistazo: 45 s, 12 min, 1 h 05 min, 2 d 3 h."""
    total = max(int(seconds), 0)
    if total < 60:
        return f"{total} s"
    minutes, _ = divmod(total, 60)
    if minutes < 60:
        return f"{minutes} min"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours} h {minutes:02d} min"
    days, hours = divmod(hours, 24)
    return f"{days} d {hours} h"
