"""Las corridas en el recorrido: lo que el inicio lista y lo que la barra de pasos necesita saber.

Lee el repositorio (``get_run_status``, ``list_runs``, ``list_ingests``, ``get_run_error`` y las
evaluaciones publicadas de ``resultados_m3``) y deja los hechos en ``pasos.RunFacts`` y
``pasos.DraftFacts``; ``pasos`` decide qué paso está hecho, en curso o bloqueado. No calcula
resultados: una corrida tiene resultados cuando su evaluación final está publicada.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from pred_platform.app.borrador import Draft
from pred_platform.app.datos import error_view
from pred_platform.app.ejecucion import run_resumable
from pred_platform.app.pasos import DraftFacts, RunFacts, step_href
from pred_platform.app.presentation import (
    StatusPresentation,
    describe_run,
    format_decimal,
    format_duration,
    stage_label,
)
from pred_platform.app.resultados_m3 import publication_time, published_executions, run_outcome
from pred_platform.contract.errors import DataLayerError, InvalidQuery
from pred_platform.contract.models import IngestSummary, RunStatus
from pred_platform.dal.lectura import run_number
from pred_platform.data.query import MAX_SIZE
from pred_platform.data.repository import DataRepository

# El grupo final de la tesis: tres corridas con los mismos datos y ajustes que solo cambian la
# semilla. La estabilidad de la campeona se lee cuando las tres publican.
THESIS_SEEDS = (0, 1, 2)


def ingests_by_id(repository: DataRepository) -> dict[str, IngestSummary]:
    """Las ingestas registradas por su id; vacío si la lista no se pudo leer."""
    try:
        listing = repository.list_ingests(size=MAX_SIZE)
    except DataLayerError:
        return {}
    return {item.ingest_id: item for item in listing.items if item.ingest_id}


def published_runs(repository: DataRepository) -> frozenset[str]:
    """Las corridas con su evaluación final publicada."""
    return frozenset(c.ejecucion_id for c in published_executions(repository, limit=None))


def _status(repository: DataRepository, corrida: str) -> RunStatus | None:
    try:
        status = repository.get_run_status(corrida, size=1)
    except (DataLayerError, InvalidQuery):
        return None
    return status if status.ejecucion is not None else None


def resolve_run_id(repository: DataRepository, reference: str) -> str | None:
    """El número de corrida de una referencia anterior: número, id del motor o ``latest``."""
    status = _status(repository, reference)
    return status.ejecucion.id if status is not None and status.ejecucion is not None else None


def _facts(
    repository: DataRepository,
    status: RunStatus,
    ingests: dict[str, IngestSummary],
    published: frozenset[str],
) -> RunFacts:
    run = status.ejecucion
    assert run is not None
    ingest = ingests.get(run.ingest_id or "")
    progress = status.progress
    settings = status.settings
    return RunFacts(
        id=run.id,
        seed=run.seed,
        estado=run.estado,
        iniciada_en=run.iniciada_en,
        finalizada_en=run.finalizada_en,
        publicada_en=publication_time(status),
        ingest_id=run.ingest_id,
        file_name=ingest.name if ingest else None,
        n_skus=ingest.n_skus if ingest else None,
        families=len(settings.families) if settings and settings.families else None,
        etapa=progress.etapa_actual if progress else None,
        avance_pct=progress.avance_pct if progress else None,
        published=run.id in published,
        resumable=run_resumable(repository, status),
    )


def run_facts(
    repository: DataRepository, corrida: str, published: frozenset[str] | None = None
) -> RunFacts | None:
    """Una corrida lanzada por su número; ``None`` si no existe o no se pudo leer. ``published``
    son las corridas publicadas si quien llama ya las leyó."""
    if run_number(corrida) is None:
        return None
    status = _status(repository, corrida)
    return None if status is None else status_facts(repository, status, published)


def status_facts(
    repository: DataRepository, status: RunStatus, published: frozenset[str] | None = None
) -> RunFacts | None:
    """Los hechos de una corrida cuyo estado quien llama ya leyó (el sondeo de Ejecución)."""
    if status.ejecucion is None:
        return None
    if published is None:
        published = published_runs(repository)
    return _facts(repository, status, ingests_by_id(repository), published)


def draft_facts(
    repository: DataRepository, draft: Draft, ingest_id: str | None = None
) -> DraftFacts:
    """El borrador para la barra de pasos; ``ingest_id`` es el archivo que muestra la página si
    no es el del borrador (por ejemplo, el que llega en ``?ingest_id=``)."""
    chosen = ingest_id or draft.ingest_id
    ingest = ingests_by_id(repository).get(chosen or "") if chosen else None
    return DraftFacts(
        file_name=ingest.name if ingest else None,
        n_skus=ingest.n_skus if ingest else None,
        families=len(draft.values.get("families") or []),
        seed=str(draft.values.get("seed", "")),
        settings_ready=draft.settings_ready,
    )


# ------------------------------------------------------------------------- inicio
@dataclass(frozen=True, slots=True)
class RunRow:
    """Una fila de «Todas las corridas»."""

    facts: RunFacts
    status: StatusPresentation
    # Lo que va al lado del estado: el avance y cuánto lleva una corrida en curso.
    detail: str | None
    # Los cinco pasos en miniatura: cuántos están hechos y cuál es el actual (-1 si ninguno).
    done: int
    current: int
    live: bool
    action: str
    action_href: str
    primary: bool


@dataclass(frozen=True, slots=True)
class GroupSlot:
    seed: int
    facts: RunFacts | None
    status: StatusPresentation


@dataclass(frozen=True, slots=True)
class Home:
    rows: tuple[RunRow, ...]
    group: tuple[GroupSlot, ...]
    # El error de la lectura de las corridas (``datos.error_view``), si falló.
    error: dict[str, object] | None = None


_RESULTS_READY = StatusPresentation("i-check", "Completada · resultados listos", "ok")
_RESULTS_READY_WITH_FAILURES = StatusPresentation(
    "i-warn", "Completada con fallos · resultados listos", "warn"
)
_NOT_PUBLISHED = StatusPresentation("i-check", "Completada · sin evaluación final publicada", "ok")
_QUEUED = StatusPresentation("i-clock", "En cola", "neutral")
_NOT_LAUNCHED = StatusPresentation("i-clock", "Sin lanzar", "neutral")
_SLOT_PUBLISHED = StatusPresentation("i-check", "Publicada", "ok")
_SLOT_RUNNING = StatusPresentation("i-play", "En curso", "info")
_SLOT_DONE = StatusPresentation("i-check", "Terminada, sin publicar", "ok")


def _row(facts: RunFacts, now: datetime, outcome: str | None = None) -> RunRow:
    execution = step_href(facts.id, "ejecucion")
    if facts.published and facts.estado == "completada_con_fallos":
        return RunRow(
            facts, _RESULTS_READY_WITH_FAILURES, outcome, 5, -1, False, "Ver resultados",
            step_href(facts.id, "resultados"), False,
        )  # fmt: skip
    if facts.published:
        return RunRow(
            facts, _RESULTS_READY, None, 5, -1, False, "Ver resultados",
            step_href(facts.id, "resultados"), False,
        )  # fmt: skip
    if facts.finished:
        return RunRow(facts, _NOT_PUBLISHED, None, 4, 4, False, "Ver la corrida", execution, False)
    if facts.estado == "ejecutando":
        stage = f" · {stage_label(facts.etapa)}" if facts.etapa else ""
        running = describe_run("ejecutando", resumable=True)
        parts = []
        if facts.avance_pct is not None:
            parts.append(f"{format_decimal(facts.avance_pct, 1)} %")
        if facts.iniciada_en is not None:
            parts.append(f"lleva {format_duration((now - facts.iniciada_en).total_seconds())}")
        status = StatusPresentation(running.icon, running.label + stage, running.modifier)
        return RunRow(
            facts, status, " · ".join(parts) or None, 3, 3, True, "Seguir la corrida",
            execution, True,
        )  # fmt: skip
    if facts.estado == "pendiente":
        return RunRow(facts, _QUEUED, None, 3, 3, False, "Seguir la corrida", execution, True)
    stopped = describe_run(facts.estado, resumable=facts.resumable)
    label = "Detenida · puede reanudarse" if facts.resumable else stopped.label
    status = StatusPresentation(stopped.icon, label, stopped.modifier, stopped.hint)
    return RunRow(facts, status, None, 3, 3, False, "Seguir la corrida", execution, True)


def _slot_status(facts: RunFacts) -> StatusPresentation:
    if facts.published:
        return _SLOT_PUBLISHED
    if facts.estado == "ejecutando":
        return _SLOT_RUNNING
    if facts.estado == "pendiente":
        return _QUEUED
    if facts.finished:
        return _SLOT_DONE
    return describe_run(facts.estado, resumable=facts.resumable)


def group_key(status: RunStatus) -> tuple[object, ...] | None:
    """Los datos y los ajustes que comparten las corridas de un grupo; todo menos la semilla.

    La agregación y el recorte los informa el motor al terminar: no separan un grupo.
    """
    run, settings = status.ejecucion, status.settings
    if run is None or settings is None:
        return None
    return (
        run.ingest_id,
        tuple(sorted(settings.families or ())),
        settings.metric,
        settings.min_train,
        settings.horizon,
        settings.step,
        settings.seasonality,
    )


def _thesis_group(
    statuses: Sequence[RunStatus], facts: dict[str, RunFacts]
) -> tuple[GroupSlot, ...]:
    """El grupo de la tesis: las corridas con los mismos datos y ajustes y semillas 0, 1 y 2.

    Si hay varios grupos así, el de más corridas (y, a igualdad, el más nuevo). Sin al menos dos
    corridas con semillas distintas no hay grupo que mostrar.
    """
    groups: dict[tuple[object, ...], dict[int, RunFacts]] = {}
    for status in statuses:  # de la más nueva a la más vieja: queda la corrida más nueva
        key = group_key(status)
        run = status.ejecucion
        if key is None or run is None or run.seed not in THESIS_SEEDS:
            continue
        groups.setdefault(key, {}).setdefault(run.seed, facts[run.id])
    candidates = [members for members in groups.values() if len(members) >= 2]
    if not candidates:
        return ()
    best = max(candidates, key=lambda m: (len(m), max(int(f.id) for f in m.values())))
    return tuple(
        GroupSlot(
            seed,
            best.get(seed),
            _slot_status(best[seed]) if seed in best else _NOT_LAUNCHED,
        )
        for seed in THESIS_SEEDS
    )


def _newest_first(statuses: Iterable[RunStatus]) -> list[RunStatus]:
    return sorted(
        statuses,
        key=lambda s: int(s.ejecucion.id) if s.ejecucion and s.ejecucion.id.isdigit() else 0,
        reverse=True,
    )


def _failure_outcome(repository: DataRepository, status: RunStatus, facts: RunFacts) -> str | None:
    """Qué dejó una corrida publicada que terminó con tareas fallidas; sin fallos, no hay frase."""
    if not (facts.published and facts.estado == "completada_con_fallos"):
        return None
    run, progress = status.ejecucion, status.progress
    assert run is not None
    return run_outcome(repository, run, progress.tareas_por_estado if progress else None)


def home(repository: DataRepository, now: datetime | None = None) -> Home:
    """Todas las corridas, con la que está en curso primero, y el grupo de la tesis."""
    try:
        runs = repository.list_runs()
    except DataLayerError as exc:
        return Home((), (), error=error_view(exc))
    statuses = _newest_first(
        status for status in (_status(repository, run.id) for run in runs) if status is not None
    )
    ingests = ingests_by_id(repository)
    published = published_runs(repository)
    facts = {
        status.ejecucion.id: _facts(repository, status, ingests, published)
        for status in statuses
        if status.ejecucion is not None
    }
    moment = now or datetime.now(UTC)
    rows = [
        _row(facts[s.ejecucion.id], moment, _failure_outcome(repository, s, facts[s.ejecucion.id]))
        for s in statuses
        if s.ejecucion is not None
    ]
    # La corrida en curso va primero y la que espera turno, después: son las que la persona
    # vuelve a mirar.
    rows.sort(key=lambda row: {"ejecutando": 0, "pendiente": 1}.get(row.facts.estado, 2))
    return Home(tuple(rows), _thesis_group(statuses, facts))
