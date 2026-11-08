"""El recorrido de una corrida: los cinco pasos, su barra lateral, su encabezado y su navegación.

Este módulo es el único dueño del modelo de pasos: el nombre, el propósito y el «Después» de cada
paso, en qué estado está cada uno (hecho, por hacer, en curso o bloqueado) y adónde llevan
«Atrás» y «Siguiente». No lee el repositorio: recibe lo que ya se sabe de la corrida (``RunFacts``)
o del borrador (``DraftFacts``) y devuelve lo que la plantilla pinta (``Wizard``).

Una corrida nueva vive en ``/corridas/nueva/<paso>`` hasta que se lanza; una lanzada, en
``/corridas/<id>/<paso>``. Después de lanzar, Datos, Ajustes y Revisar quedan como registro de
solo lectura de lo que se eligió.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from pred_platform.app.presentation import (
    STAGE_ORDER,
    format_count,
    format_decimal,
    format_duration,
)

NEW = "nueva"
# El id del formulario de Ajustes: «Continuar» al pie del paso lo envía.
AJUSTES_FORM = "formulario-ajustes"

StepKey = Literal["datos", "ajustes", "revisar", "ejecucion", "resultados"]
StepState = Literal["done", "todo", "live", "locked"]


@dataclass(frozen=True, slots=True)
class StepDef:
    key: StepKey
    label: str
    # El nombre corto que cabe bajo la marca de la barra lateral.
    short: str
    purpose: str
    # «Después: <paso>.» en negrita y lo que se hará allí.
    after_lead: str | None
    after_text: str


STEPS: tuple[StepDef, ...] = (
    StepDef(
        "datos",
        "Datos",
        "Datos",
        "Use un archivo ya validado o cargue uno nuevo. PRED revisa el archivo y clasifica cada "
        "producto por su patrón de demanda.",
        "Después: Ajustes.",
        "Elegirá qué familias de modelos compiten.",
    ),
    StepDef(
        "ajustes",
        "Ajustes",
        "Ajustes",
        "Las familias de modelos que compiten, la métrica con que se elige la campeona y la "
        "ventana de evaluación.",
        "Después: Revisar y lanzar.",
        "Verá un resumen antes de empezar; nada empieza hasta que lo confirme.",
    ),
    StepDef(
        "revisar",
        "Revisar y lanzar",
        "Lanzar",
        "Confirme los datos y los ajustes.",
        "Después: Ejecución.",
        "Seguirá el avance; puede cerrar la página y volver cuando quiera.",
    ),
    StepDef(
        "ejecucion",
        "Ejecución",
        "Ejecución",
        "El motor recorre cuatro etapas. La corrida sigue aunque cierre esta página; al volver, "
        "PRED la abre en este paso.",
        "Después: Resultados.",
        "Se abren cuando la evaluación final publique.",
    ),
    StepDef(
        "resultados",
        "Resultados",
        "Resultados",
        "Qué familia pronostica mejor cada tipo de demanda, en cuántos productos se verifica la "
        "mejora y si se sostiene con otra semilla.",
        None,
        "Último paso. Puede exportar los resultados o empezar una corrida nueva con estos ajustes.",
    ),
)
STEP_KEYS: tuple[StepKey, ...] = tuple(step.key for step in STEPS)
_BY_KEY = {step.key: step for step in STEPS}

# Lo que dice el encabezado de Datos, Ajustes y Revisar de una corrida ya lanzada.
LAUNCHED_PURPOSE = (
    "Esta corrida ya se lanzó. Este paso muestra lo que se eligió y no se puede cambiar; para "
    "usar otros valores, empiece una corrida nueva con estos ajustes."
)
LAUNCHED_AFTER = "El avance está en Ejecución y los resultados, en Resultados."
_RECORD_STEPS = ("datos", "ajustes", "revisar")
# Una corrida en estos estados todavía no tiene resultados que leer.
_UNFINISHED = ("pendiente", "ejecutando", "detenida")


def step_href(corrida: str, key: str) -> str:
    return f"/corridas/{corrida}/{key}"


@dataclass(frozen=True, slots=True)
class DraftFacts:
    """Lo que se sabe del borrador de una corrida nueva."""

    file_name: str | None
    n_skus: int | None
    families: int
    seed: str
    # Si Ajustes se completó con «Continuar».
    settings_ready: bool

    @property
    def has_data(self) -> bool:
        return self.file_name is not None


@dataclass(frozen=True, slots=True)
class RunFacts:
    """Lo que se sabe de una corrida lanzada para ubicarla en el recorrido."""

    id: str
    seed: int
    estado: str
    iniciada_en: datetime | None
    finalizada_en: datetime | None
    ingest_id: str | None
    file_name: str | None
    n_skus: int | None
    families: int | None
    etapa: str | None
    avance_pct: float | None
    # Si la evaluación final de la corrida está publicada.
    published: bool
    # Si una corrida detenida se puede reanudar (``ejecucion.offers_resume``).
    resumable: bool
    publicada_en: datetime | None = None

    @property
    def finished(self) -> bool:
        return self.estado not in _UNFINISHED

    @property
    def stage_number(self) -> int | None:
        return STAGE_ORDER.index(self.etapa) + 1 if self.etapa in STAGE_ORDER else None

    @property
    def duration(self) -> str | None:
        if self.iniciada_en is None or self.finalizada_en is None:
            return None
        return format_duration((self.finalizada_en - self.iniciada_en).total_seconds())


@dataclass(frozen=True, slots=True)
class RailStep:
    key: StepKey
    number: int
    label: str
    short: str
    state: StepState
    sub: str
    # Un instante que la línea de estado nombra («Lanzada 1 nov 2026»), con su valor exacto.
    sub_when: datetime | None
    href: str | None
    current: bool

    @property
    def state_text(self) -> str:
        return {
            "done": "hecho",
            "live": "en curso",
            "locked": "todavía no disponible",
            "todo": "por hacer",
        }[self.state]


@dataclass(frozen=True, slots=True)
class NavLink:
    label: str
    href: str
    # En Ajustes de una corrida nueva, Atrás envía el formulario del paso (``submits``) con el
    # paso al que va (``destino``), para que el borrador guarde lo escrito antes de salir.
    submits: str | None = None
    destino: str = ""


@dataclass(frozen=True, slots=True)
class NavNext:
    """La acción «Siguiente». Sin ``href`` ni formulario, sale deshabilitada con un candado."""

    label: str
    href: str | None = None
    hint: str = ""
    # El id del formulario del paso que este botón envía (Ajustes guarda el borrador así).
    submits: str | None = None
    # Un formulario propio: la acción y sus campos ocultos (Datos elige el archivo así).
    post_to: str | None = None
    fields: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class Wizard:
    corrida: str
    # Lo que la barra superior dice de la corrida mostrada.
    title: str
    steps: tuple[RailStep, ...]
    step: StepDef
    purpose: str
    after_lead: str | None
    after_text: str
    back: NavLink | None
    next: NavNext | None

    @property
    def current(self) -> RailStep:
        return next(item for item in self.steps if item.current)

    @property
    def page_title(self) -> str:
        who = "Corrida nueva" if self.corrida == NEW else f"Corrida #{self.corrida}"
        return f"{self.step.label} · {who} · PRED"


def _rail(
    corrida: str, current: StepKey, rows: list[tuple[StepState, str, datetime | None]]
) -> tuple[RailStep, ...]:
    return tuple(
        RailStep(
            key=step.key,
            number=index + 1,
            label=step.label,
            short=step.short,
            state=state,
            sub=sub,
            sub_when=when,
            href=None if state == "locked" else step_href(corrida, step.key),
            current=step.key == current,
        )
        for index, (step, (state, sub, when)) in enumerate(zip(STEPS, rows, strict=True))
    )


def _data_sub(file_name: str | None, n_skus: int | None) -> str:
    if file_name is None:
        return "Elegir o cargar un archivo"
    return f"{file_name} · {format_count(n_skus)} productos" if n_skus is not None else file_name


def _settings_sub(families: int | None, seed: object) -> str:
    if families is None:
        return f"Semilla {seed}"
    return f"{families} {'familia' if families == 1 else 'familias'} · semilla {seed}"


def draft_resume(draft: DraftFacts) -> NavLink:
    """Adónde lleva un paso bloqueado de la corrida nueva: a Revisar y lanzar si ya hay datos
    elegidos; si no, a Datos, que es donde empieza."""
    if draft.has_data:
        return NavLink("Ir a Revisar y lanzar", step_href(NEW, "revisar"))
    return NavLink("Ir a Datos", step_href(NEW, "datos"))


def draft_wizard(
    current: StepKey, draft: DraftFacts, *, can_continue: bool = True, chosen: str | None = None
) -> Wizard:
    """El recorrido de una corrida nueva. ``can_continue`` dice si el paso ya permite seguir (en
    Datos, si el archivo que se muestra se puede elegir; en Ajustes, si hay algún archivo que
    ejecutar); ``chosen`` es el archivo que muestra Datos."""
    rail = _rail(
        NEW,
        current,
        [
            ("done" if draft.has_data else "todo", _data_sub(draft.file_name, draft.n_skus), None),
            (
                "done" if draft.has_data and draft.settings_ready else "todo",
                _settings_sub(draft.families, draft.seed),
                None,
            ),
            (
                ("todo", "Confirmar y empezar", None)
                if draft.has_data
                else ("locked", "Primero elija los datos", None)
            ),
            ("locked", "Se abre al lanzar", None),
            ("locked", "Se abre cuando la corrida publique", None),
        ],
    )
    step = _BY_KEY[current]
    back: NavLink | None
    nxt: NavNext | None
    if current == "datos":
        back = NavLink("Corridas", "/")
        nxt = (
            NavNext(
                "Continuar a Ajustes",
                hint="Su elección queda guardada si vuelve a este paso.",
                post_to=step_href(NEW, "datos"),
                fields=(("ingest_id", chosen),),
            )
            if can_continue and chosen
            else NavNext(
                "Continuar a Ajustes",
                hint="Elija un archivo válido para continuar."
                if chosen
                else "Elija un archivo para continuar.",
            )
        )
    elif current == "ajustes":
        back = (
            NavLink("Atrás: Datos", step_href(NEW, "datos"), submits=AJUSTES_FORM, destino="datos")
            if can_continue
            else NavLink("Atrás: Datos", step_href(NEW, "datos"))
        )
        nxt = (
            NavNext(
                "Continuar a Revisar y lanzar", hint="Nada empieza todavía.", submits=AJUSTES_FORM
            )
            if can_continue
            else NavNext(
                "Continuar a Revisar y lanzar", hint="Primero cargue un archivo válido en Datos."
            )
        )
    elif current == "revisar":
        back = NavLink("Atrás: Ajustes", step_href(NEW, "ajustes"))
        nxt = None
    elif current == "ejecucion":
        back = NavLink("Atrás: Revisar y lanzar", step_href(NEW, "revisar"))
        nxt = NavNext("Resultados", hint="Se abren cuando la evaluación final publique.")
    else:
        back = NavLink("Atrás: Ejecución", step_href(NEW, "ejecucion"))
        nxt = None
    return Wizard(
        corrida=NEW,
        title="Corrida nueva (sin lanzar)",
        steps=rail,
        step=step,
        purpose=step.purpose,
        after_lead=step.after_lead,
        after_text=step.after_text,
        back=back,
        next=nxt,
    )


def run_wizard(current: StepKey, run: RunFacts) -> Wizard:
    """El recorrido de una corrida lanzada: Datos, Ajustes y Revisar quedan como registro."""
    if run.estado == "pendiente":
        execution: tuple[StepState, str, datetime | None] = ("live", "En cola", None)
    elif run.estado == "ejecutando":
        stage = f"Etapa {run.stage_number} de 4" if run.stage_number else "Trabajando"
        pct = f" · {format_decimal(run.avance_pct, 1)} %" if run.avance_pct is not None else ""
        execution = ("live", stage + pct, None)
    elif run.estado == "detenida":
        execution = ("todo", "Detenida · puede reanudarse" if run.resumable else "Detenida", None)
    else:
        execution = ("done", f"Terminó en {run.duration}" if run.duration else "Terminó", None)
    if run.published:
        results: tuple[StepState, str, datetime | None] = ("done", "Publicados", run.publicada_en)
    elif run.finished:
        results = ("todo", "Sin evaluación final publicada", None)
    else:
        results = ("locked", "Se abre cuando la evaluación final publique", None)
    rail = _rail(
        run.id,
        current,
        [
            ("done", _data_sub(run.file_name, run.n_skus), None),
            ("done", _settings_sub(run.families, run.seed), None),
            ("done", "Lanzada" if run.iniciada_en else "Lanzada · en cola", run.iniciada_en),
            execution,
            results,
        ],
    )
    step = _BY_KEY[current]
    navigation: dict[StepKey, tuple[NavLink | None, NavNext | None]] = {
        "datos": (
            NavLink("Corridas", "/"),
            NavNext("Continuar a Ajustes", step_href(run.id, "ajustes")),
        ),
        "ajustes": (
            NavLink("Atrás: Datos", step_href(run.id, "datos")),
            NavNext("Continuar a Revisar y lanzar", step_href(run.id, "revisar")),
        ),
        "revisar": (
            NavLink("Atrás: Ajustes", step_href(run.id, "ajustes")),
            NavNext("Ir a Ejecución", step_href(run.id, "ejecucion")),
        ),
        "ejecucion": (
            NavLink("Atrás: Revisar y lanzar", step_href(run.id, "revisar")),
            NavNext(
                "Continuar a Resultados",
                step_href(run.id, "resultados") if run.published else None,
                hint=""
                if run.published
                else (
                    "Sin evaluación final publicada."
                    if run.finished
                    else "Se abre cuando la evaluación final publique."
                ),
            ),
        ),
        "resultados": (NavLink("Atrás: Ejecución", step_href(run.id, "ejecucion")), None),
    }
    back, nxt = navigation[current]
    after_lead, after_text = step.after_lead, step.after_text
    if current == "ejecucion":
        if run.published:
            after_text = "Ya están publicados."
        elif run.finished:
            after_text = "No hay una evaluación final publicada."
    elif current in _RECORD_STEPS:
        after_lead, after_text = None, LAUNCHED_AFTER
    return Wizard(
        corrida=run.id,
        title=f"Corrida #{run.id} · semilla {run.seed}",
        steps=rail,
        step=step,
        purpose=LAUNCHED_PURPOSE if current in _RECORD_STEPS else step.purpose,
        after_lead=after_lead,
        after_text=after_text,
        back=back,
        next=nxt,
    )
