"""Pasos 2 a 4 de una corrida: Ajustes, Revisar y lanzar, y Ejecución (C4).

La lectura sale de ``DataRepository`` (``get_run_status``, ``list_ingests``); las escrituras, de
``app.state.run_control`` (``worker/control.py``). Filtros y paginación de las tareas se delegan al
repositorio. Mientras la corrida está en curso el cuerpo se refresca con HTMX cada 2 s.

Una corrida nueva guarda sus ajustes en el borrador (``borrador``) y se lanza desde Revisar con
``POST /ejecucion/lanzar``, la misma ruta y la misma validación de siempre. Una corrida lanzada
muestra en Ajustes y Revisar lo que se lanzó, como registro. ``/ejecucion`` lleva al paso que
corresponde con la misma consulta.
"""

from __future__ import annotations

import json
from contextlib import suppress
from datetime import UTC, datetime
from importlib.metadata import version as package_version
from typing import Annotated, Any, Literal
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response

from pred_platform.app.borrador import (
    Draft,
    discard_draft,
    is_stale,
    next_revision,
    read_draft,
    save_draft,
)
from pred_platform.app.configuracion import (
    MAX_BYTES,
    ConfigExportError,
    config_filename,
    export_config,
    form_values,
    parse_config,
)
from pred_platform.app.corridas import (
    draft_facts,
    facts_from_status,
    ingests_by_id,
    resolve_run_id,
    run_facts,
)
from pred_platform.app.datos import error_view, ingest_name, page_meta
from pred_platform.app.ejecucion import (
    FAMILY_CHOICES,
    FINISHED_STATES,
    FOCUS_SIZE,
    METRIC_CHOICES,
    NOTICES,
    STAGES,
    SYNTHETIC_INGEST,
    ExclusionWarning,
    ExecutionQuery,
    FormResult,
    LaunchIngest,
    accepted_ingests,
    classes_without_family,
    draft_items,
    elapsed_text,
    exclusion_confirmation,
    exclusion_warning,
    families_hint,
    focus_state,
    focus_tasks,
    history_needed,
    parse_launch_form,
    read_execution_query,
    refusal_text,
    run_actions,
    run_compares_nothing,
    run_error,
    settings_items,
    short_of_two,
    should_poll,
    single_family_classes,
)
from pred_platform.app.execution_display import (
    add_cause_snapshot,
    add_stage_references,
    execution_display,
    task_rows,
)
from pred_platform.app.historial import earlier_runs, timestamp
from pred_platform.app.pasos import (
    AJUSTES_FORM,
    NEW,
    STEP_KEYS,
    RunFacts,
    StepKey,
    draft_resume,
    draft_wizard,
    run_wizard,
    step_href,
)
from pred_platform.app.presentation import describe_run
from pred_platform.app.resultados_m3 import (
    publication_time,
    published_evaluation,
    run_outcome_details,
)
from pred_platform.app.routers.corridas import (
    Locked,
    redirect_with_query,
    render_locked,
    render_missing_run,
)
from pred_platform.contract import CONTRACT_VERSION, M3_CONTRACT_VERSION
from pred_platform.contract.errors import DataLayerError, InvalidQuery
from pred_platform.contract.models import RunStatus, SkuClass, TopologyMetrics
from pred_platform.dal import lectura
from pred_platform.dal.ejecuciones import RunRequestError
from pred_platform.data.deps import get_repository
from pred_platform.data.query import LATEST, MAX_SIZE
from pred_platform.data.repository import DataRepository
from pred_platform.data.run_status import CauseSnapshot
from pred_platform.worker.candado import WorkerLock
from pred_platform.worker.control import ControlUnavailable, RunControl
from pred_platform.worker.service import PARAMETERS_SCHEMA_VERSION, run_id_for

router = APIRouter()

_TASK_STATES = ("pendiente", "ejecutando", "finalizo_m2", "exitosa", "fallida", "no_ejecutable")
_LAUNCH_FIELDS = ("min_train", "horizon", "step", "seasonality", "trials", "seed", "workers")


def _control(request: Request) -> RunControl:
    return request.app.state.run_control  # type: ignore[no-any-return]


def _worker_state(request: Request) -> Literal["active", "off", "stopped", "unlockable"]:
    """Whether a worker follows the queue: this app's live thread, or another process that holds
    the database. Otherwise a launched run waits for ``make worker``: ``off`` when this app runs
    no worker, ``stopped`` when its thread ended (the app log says why). ``unlockable`` is this
    app's thread alive but retrying because it cannot open its lock file: nobody takes the
    queue until the folder is writable, and no earlier run is to blame."""
    settings = request.app.state.settings
    if settings.data_source != "dal":
        return "off"
    thread = getattr(request.app.state, "worker_thread", None)
    if thread is not None and thread.is_alive():
        worker = getattr(request.app.state, "worker", None)
        if worker is not None and worker.lock_problem is not None:
            return "unlockable"
        return "active"
    if WorkerLock.for_database(settings.db_path).held_elsewhere():
        return "active"
    return "stopped" if settings.worker == "embedded" else "off"


def _render(
    request: Request,
    template: str,
    context: dict[str, Any],
    status_code: int = 200,
) -> HTMLResponse:
    return request.app.state.templates.TemplateResponse(
        request, template, context, status_code=status_code
    )


# ------------------------------------------------------------------------- Ejecución
def _context(
    request: Request,
    repository: DataRepository,
    *,
    ejecucion: str | None = None,
    action_error: str | None = None,
    published: bool | None = None,
) -> dict[str, Any]:
    """El cuerpo de Ejecución. ``ejecucion`` es la corrida del paso (la ruta manda sobre
    ``?ejecucion=``); sin ella, la de la consulta o la más reciente (el sondeo). ``published``
    es si la corrida publicó, cuando quien llama ya lo sabe (``published_evaluation``)."""
    params = dict(request.query_params)
    if ejecucion is not None:
        params["ejecucion"] = ejecucion
    control = _control(request)
    capabilities = repository.get_capabilities()
    orchestration = next(item for item in capabilities.items if item.id == "run_orchestration")

    query, query_error = read_execution_query(params)
    status: RunStatus | None = None
    status_error: dict[str, object] | None = None
    try:
        status = repository.get_run_status(query.ejecucion, **query.as_kwargs())
    except InvalidQuery:
        query_error = "Ese filtro no existe; se muestran todas las tareas."
        query = ExecutionQuery(ejecucion=query.ejecucion, todas=True)
        try:
            status = repository.get_run_status(query.ejecucion)
        except DataLayerError as exc:
            status_error = error_view(exc)
    except DataLayerError as exc:
        status_error = error_view(exc)

    run = status.ejecucion if status is not None else None
    estado = run.estado if run is not None else None
    progress = status.progress if status is not None else None
    counts = dict(progress.tareas_por_estado) if progress is not None else {}
    # Mientras la ejecución avanza, el avance es lo principal: la lista de tareas se reduce a las
    # que necesitan atención y la completa queda a un enlace.
    focus = focus_tasks(estado, query)
    focus_estado = focus_state(counts) if focus else None
    tasks = status
    if focus and run is not None:
        try:
            tasks = repository.get_run_status(run.id, estado=focus_estado, size=FOCUS_SIZE)
        except (InvalidQuery, DataLayerError):
            tasks = status
            focus = False
            focus_estado = None
    poll = should_poll(
        estado, counts, reason=control.reason, stopped_at=run.finalizada_en if run else None
    )
    error = run_error(repository.get_run_error(run.id)) if run is not None else None
    actions = run_actions(
        estado,
        reason=control.reason,
        error=error,
        nothing_to_compare=run_compares_nothing(repository, run, status),
    )
    now = datetime.now(UTC)
    # Si la corrida publicó lo decide una sola regla (``published_evaluation``), la misma de la
    # página y del sondeo; el texto del resultado no la cambia.
    m3_run_id = None
    if run is not None and published is None:
        m3_run_id = published_evaluation(repository, run.id)
        published = m3_run_id is not None
    outcome = (
        run_outcome_details(repository, run, counts, m3_run_id, published=published)
        if run is not None
        else None
    )
    return {
        **execution_display(status, now),
        "capabilities": capabilities,
        "orchestration": orchestration,
        "source": repository.source,
        "query": query,
        "query_error": query_error,
        "query_string": query.query_string(),
        "status": status,
        "status_error": status_error,
        # Un número de ejecución que no existe se dice con ese número, no con el error genérico.
        "missing_run": query.ejecucion
        if status_error is not None
        and status_error["code"] in ("platform.not_found", "platform.artifact_missing")
        and query.ejecucion != LATEST
        else None,
        "run": run,
        # El intento sale de la misma lectura del estado, no de otra consulta de la ejecución.
        "resumed": (getattr(status, "attempt", None) or 0) > 1,
        "stopped_open": estado == "detenida"
        and not poll
        and counts.get("pendiente", 0) + counts.get("ejecutando", 0) > 0,
        "settings_rows": settings_items(status.settings) if status and status.settings else [],
        "reserve": status.reserve if status is not None else None,
        "progress": progress,
        "state_counts": [
            (code, progress.tareas_por_estado.get(code, 0))  # type: ignore[call-overload]
            for code in _TASK_STATES
        ]
        if progress is not None
        else [],
        "rows": task_rows(tasks) if tasks is not None else [],
        "pages": page_meta(tasks.page) if tasks and tasks.page and not focus else None,
        "focus": focus,
        "focus_estado": focus_estado,
        "focus_total": tasks.page.total if focus and tasks and tasks.page else 0,
        "outcome": outcome.text if outcome else None,
        "published": bool(published),
        "run_ingest": ingest_name(repository, run.ingest_id) if run is not None else None,
        "run_error": error,
        "current_stage": dict(STAGES).get(progress.etapa_actual or "") if progress else None,
        "elapsed": elapsed_text(run, now) if run is not None else None,
        "actions": actions,
        "run_status": describe_run(run.estado, resumable=actions.offers_resume)
        if run is not None
        else None,
        "control_reason": control.reason,
        "worker": _worker_state(request),
        "poll": poll,
        "finished": estado in FINISHED_STATES,
        "results_href": step_href(run.id, "resultados") if run is not None else None,
        "notice": NOTICES.get(params.get("aviso", "")),
        "action_error": action_error,
    }


def _latest_run_id(repository: DataRepository) -> str | None:
    return resolve_run_id(repository, LATEST)


def _render_execution(
    request: Request,
    repository: DataRepository,
    corrida: str,
    *,
    action_error: str | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    number = lectura.run_number(corrida)
    if number is None:
        return render_missing_run(request, corrida)
    context = _context(request, repository, ejecucion=str(number), action_error=action_error)
    status = context["status"]
    if status is None or status.ejecucion is None:
        return render_missing_run(request, corrida)
    run_id = status.ejecucion.id
    # La barra de pasos reutiliza el estado, la ingesta y el resultado que la página ya leyó.
    context["wizard"] = run_wizard("ejecucion", _execution_facts(repository, context))
    try:
        references = repository.get_run_stage_history(run_id)
    except DataLayerError:
        references = []
    add_stage_references(context, references)
    counts = context["progress"].tareas_por_estado if context["progress"] else {}
    if counts.get("fallida", 0) + counts.get("no_ejecutable", 0) == 0:
        # Sin tareas fallidas ni no ejecutables no hay causas: los conteos ya leídos lo dicen.
        add_cause_snapshot(context, CauseSnapshot(read_at=datetime.now(UTC), causes=[]))
    else:
        with suppress(DataLayerError):
            add_cause_snapshot(context, repository.get_run_causes(run_id))
    return _render(request, "pages/execution.html", context, status_code)


def _execution_facts(repository: DataRepository, context: dict[str, Any]) -> RunFacts:
    """Los hechos de la barra de pasos desde lo que ya leyó :func:`_context`, sin leer de nuevo
    el estado ni la lista de ingestas. Una ejecución que no terminó no tiene resumen de lo que
    dejó: si está publicada, lo dice ``published_evaluation`` en :func:`_context`."""
    status = context["status"]
    run = status.ejecucion
    # ``_context`` ya aplicó la regla de publicación (``published_evaluation``).
    published = context["published"]
    ingest = context["run_ingest"]
    return facts_from_status(
        status,
        published=published,
        resumable=context["actions"].offers_resume,
        file_name=ingest.file_name if ingest else None,
        n_skus=ingest.n_skus if ingest else None,
        publicada_en=(
            publication_time(repository.get_stage_timings(run.id)) if published else None
        ),
    )


@router.get("/ejecucion")
def ejecucion(
    request: Request,
    repository: DataRepository = Depends(get_repository),  # noqa: B008
) -> RedirectResponse:
    """La ruta anterior: el formulario de lanzamiento va a la corrida nueva (con el archivo
    elegido, en Ajustes) y el seguimiento, al paso Ejecución de la corrida pedida o la más
    reciente."""
    params = request.query_params
    items = params.multi_items()
    if params.get("ingest_id"):
        return redirect_with_query(step_href(NEW, "ajustes"), items, drop=("lanzar", "ejecucion"))
    if params.get("lanzar"):
        return redirect_with_query(step_href(NEW, "datos"), items, drop=("lanzar", "ejecucion"))
    wanted = (params.get("ejecucion") or "").strip()
    target = resolve_run_id(repository, wanted or LATEST)
    if target is None and wanted not in ("", LATEST):
        target = wanted  # Una referencia desconocida conserva la página 404.
    if target is None:
        return redirect_with_query(step_href(NEW, "datos"), items, drop=("ejecucion",))
    return redirect_with_query(step_href(target, "ejecucion"), items, drop=("ejecucion",))


@router.get("/corridas/{corrida}/ejecucion", response_class=HTMLResponse)
def paso_ejecucion(
    request: Request,
    corrida: str,
    repository: DataRepository = Depends(get_repository),  # noqa: B008
) -> HTMLResponse:
    if corrida == NEW:
        draft = draft_facts(repository, read_draft(request))
        resume = draft_resume(draft)
        return render_locked(
            request,
            Locked(
                title="Ejecución",
                heading="Todavía no hay corrida",
                text="Este paso se abre al lanzar. Antes, revise los datos y los ajustes.",
                actions=((resume.label, resume.href, True),),
            ),
            draft_wizard("ejecucion", draft),
        )
    return _render_execution(request, repository, corrida)


@router.get("/ejecucion/estado", response_class=HTMLResponse)
def ejecucion_estado(
    request: Request,
    repository: DataRepository = Depends(get_repository),  # noqa: B008
) -> HTMLResponse:
    """El cuerpo de la página para el sondeo, con la barra de pasos al día. 286 le dice a htmx
    que deje de preguntar."""
    context = _context(request, repository)
    status = context["status"]
    if status is not None and status.ejecucion is not None:
        facts = facts_from_status(
            status,
            published=context["published"],
            resumable=context["actions"].offers_resume,
            file_name=context["run_ingest"].file_name if context["run_ingest"] else None,
            publicada_en=(
                publication_time(repository.get_stage_timings(status.ejecucion.id))
                if context["published"]
                else None
            ),
        )
        context["wizard"] = run_wizard("ejecucion", facts)
    return _render(
        request, "partials/execution_poll.html", context, 200 if context["poll"] else 286
    )


# ---------------------------------------------------------------- Ajustes y Revisar
def _class_skus(
    repository: DataRepository, ingest_id: str, sku_class: SkuClass | None
) -> list[TopologyMetrics]:
    """Todos los SKU de una clase de la ingesta, en orden, página a página de la topología.

    Si una página no se puede leer, los leídos hasta ahí: el aviso cuenta los que no nombra.
    """
    rows: list[TopologyMetrics] = []
    page = 1
    while True:
        try:
            report = repository.get_topology_report(
                ingest_id, sku_class=sku_class, page=page, size=MAX_SIZE
            )
        except DataLayerError:
            return rows
        rows.extend(report.items)
        if not report.items or len(rows) >= report.page.total:
            return rows
        page += 1


def _check_launch(
    repository: DataRepository,
    ingest_id: str | None,
    form: FormResult,
    *,
    ingests: list[LaunchIngest] | None = None,
    by_class: dict[SkuClass, int] | None = None,
) -> tuple[ExclusionWarning, str | None] | None:
    """La regla de lanzamiento sobre los datos y las familias: deja los errores en ``form`` y
    devuelve el aviso de los SKU que quedarían sin campeón si alguna clase queda con una sola
    familia, con la clave que confirma lanzar así (``None`` si un rechazo impide lanzar igual).

    ``ingests`` y ``by_class`` son las ingestas aceptadas y los SKU por clase de ``ingest_id`` si
    la página ya los leyó; sin ellos, se leen aquí.
    """
    form.values["ingest_id"] = ingest_id or ""
    if ingests is None:
        ingests = accepted_ingests(repository)
    synthetic = {i.item.ingest_id for i in ingests if i.synthetic}
    if not ingest_id:
        form.errors["ingest_id"] = "Elija el archivo de datos que se va a ejecutar."
        return None
    if ingest_id in synthetic:
        form.errors["ingest_id"] = SYNTHETIC_INGEST
        return None
    if form.errors:
        return None
    if by_class is None:
        try:
            by_class = repository.get_topology_report(ingest_id, size=1).summary.by_class
        except DataLayerError:
            by_class = {}  # sin topología publicada, decide el motor
        except InvalidQuery:  # no es el SHA-256 de una ingesta
            by_class = {}
            form.errors["ingest_id"] = "Elija un archivo de datos aceptado en Datos."
    chosen = form.values["families"]
    needed = history_needed(form.settings) if form.settings is not None else 0
    history = _class_skus(repository, ingest_id, None) if by_class else []
    # Una lectura parcial no demuestra que todos los SKU sean demasiado cortos.
    complete = bool(history) and len(history) >= sum(by_class.values())
    if complete:
        longest = max(row.n_periods for row in history)
        if longest < needed:
            form.errors["min_train"] = (
                "Con estos ajustes ningún SKU tiene historia suficiente: "
                f"se necesitan {needed} días antes del corte y el SKU más largo tiene "
                f"{longest}. Reduzca el mínimo de entrenamiento, el horizonte o el paso."
            )
            return None
    single = single_family_classes(by_class, chosen)
    # Con el mapa completo ya leído, los SKU de cada clase salen de él, en el mismo orden.
    skus: dict[SkuClass, list[TopologyMetrics]] = {
        c: [row for row in history if row.sku_class == c]
        if complete
        else _class_skus(repository, ingest_id, c)
        for c in short_of_two(by_class, chosen)
    }
    refusal = classes_without_family(by_class, chosen, skus, needed)
    if refusal:
        form.errors["families"] = refusal_text(refusal, by_class, skus, needed)
    if not single:
        return None
    # Con un rechazo, el aviso dice además lo que quedaría por corregir, sin lanzar igual.
    warning = exclusion_warning(chosen, by_class, skus, needed)
    return warning, None if refusal else exclusion_confirmation(ingest_id, chosen)


def _launch_form(
    families: list[str] | None, metric: str | None, fields: dict[str, str | None]
) -> FormResult:
    return parse_launch_form(families=families or [], metric=metric, fields=fields)


def _render_ajustes(
    request: Request,
    repository: DataRepository,
    *,
    form: FormResult | None = None,
    ingest_id: str | None = None,
    form_error: str | None = None,
    import_error: str | None = None,
    imported: tuple[str, str] | None = None,
    exclusion: tuple[ExclusionWarning, str | None] | None = None,
    save: Draft | None = None,
    autosave: bool = False,
    conflict: bool = False,
    checked: bool = False,
    ingests: list[LaunchIngest] | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    """Ajustes de la corrida nueva: el formulario con los valores del borrador (o los enviados).

    ``imported`` es el aviso (variante, texto) de una copia o una importación, o de por qué no
    se pudo copiar. ``exclusion`` es el aviso de los SKU que quedarían sin campeón, junto al
    rechazo que impide continuar. ``save`` es el borrador que la página guarda y muestra en la
    barra de pasos; ``autosave``, que lo guarda el guardado automático. ``conflict`` avisa que lo
    enviado no se guardó porque el borrador cambió después de abrir esa página. ``checked`` dice
    que quien llama ya aplicó la regla de lanzamiento a ``form`` (``exclusion`` es su aviso, o
    ``None``), y ``ingests``, las ingestas aceptadas si ya las leyó: la página no las lee otra vez.
    """
    if save is not None:
        save = next_revision(request, save)
    draft = save or read_draft(request)
    values = form.values if form is not None else dict(draft.values)
    if form is None:
        values["ingest_id"] = ingest_id or draft.ingest_id or ""
    if ingests is None:
        ingests = accepted_ingests(repository)
    # El archivo que eligió la persona (el borrador, un enlace o el formulario enviado). Sin
    # elegir, o con uno que la lista no tiene, la lista pide elegir uno: nunca elige por la
    # persona, porque Atrás o un paso de la barra lo guardarían en el borrador.
    chosen = values.get("ingest_id") or None
    selected = chosen if chosen in {i.item.ingest_id for i in ingests} else None
    control = _control(request)
    warning = exclusion
    if form is None:
        form = _launch_form(
            values.get("families"), values.get("metric"), {k: values.get(k) for k in _LAUNCH_FIELDS}
        )
    by_class = {}
    if selected:
        with suppress(DataLayerError, InvalidQuery):
            by_class = repository.get_topology_report(selected, size=1).summary.by_class
    if selected and not checked and warning is None:
        warning = _check_launch(repository, selected, form, ingests=ingests, by_class=by_class)
    details = _stored_runs(request)
    known = {i.item.ingest_id: i.item for i in ingests if i.item.ingest_id}
    context = {
        "wizard": draft_wizard(
            "ajustes", draft_facts(repository, draft, chosen, known), can_continue=bool(ingests)
        ),
        "source": repository.source,
        "record": False,
        "ingests": ingests,
        "selected_ingest": selected,
        "form": values,
        "form_errors": form.errors if form is not None else {},
        "form_error": form_error,
        "import_error": import_error,
        "notice": imported,
        "family_choices": FAMILY_CHOICES,
        "families_hint": families_hint(),
        "class_counts": by_class,
        "exclusion_warning": warning[0] if warning else None,
        "copy_runs": [
            {"id": r.id, "seed": r.seed, "iniciada_en": timestamp(r.iniciada_en)} for r in details
        ],
        "copy_run": request.query_params.get("copiar", ""),
        "metric_choices": METRIC_CHOICES,
        "control_reason": control.reason,
        "launch_form_id": AJUSTES_FORM,
        # La revisión que esta página vio: lo que envíe después solo vale si sigue siendo la última.
        "draft_rev": draft.stamp,
        "conflict": conflict,
    }
    response = _render(request, "pages/paso_ajustes.html", context, status_code)
    # Atrás del navegador pide la página de nuevo: una copia guardada por otra salida, con la
    # revisión que ya no es la vigente, solo daría un aviso de cambio sin que nadie lo hiciera.
    response.headers["Cache-Control"] = "no-store"
    if save is not None:
        save_draft(request, response, save, autosave=autosave)
        response.headers["X-Pred-Draft-Rev"] = save.stamp
    return response


def _stored_runs(request: Request) -> list[lectura.RunDetail]:
    settings = request.app.state.settings
    return lectura.list_runs(settings.db_path) if settings.data_source == "dal" else []


def _duplicate(request: Request, ingest_id: str, form: FormResult) -> lectura.RunDetail | None:
    settings = request.app.state.settings
    if settings.data_source != "dal" or form.settings is None or form.errors:
        return None
    return lectura.get_run_for_identity(settings.db_path, run_id_for(ingest_id, form.settings))


def _duration_context(
    request: Request,
    ingest_id: str,
    exclude: str | None = None,
    stored: list[lectura.RunDetail] | None = None,
) -> dict[str, Any]:
    """Las duraciones de las corridas anteriores con estos datos; ``stored`` son las corridas
    guardadas si la página ya las leyó."""
    history = earlier_runs(
        _stored_runs(request) if stored is None else stored,
        ingest_id,
        request.app.state.settings.data_root,
        exclude,
    )
    maximum = max((d.seconds for r in history for d in r.stages.values()), default=1) or 1
    return {"earlier_runs": history, "duration_max": maximum, "stage_choices": STAGES}


def _render_revisar(
    request: Request,
    repository: DataRepository,
    *,
    ingest_id: str,
    values: dict[str, Any],
    form_error: str | None = None,
    launched: tuple[FormResult, tuple[ExclusionWarning, str | None] | None] | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    """Revisar y lanzar: lo que se va a lanzar, con «Cambiar» por sección, y el botón Lanzar.

    El aviso de los SKU que quedarían sin campeón se muestra aquí; lanzar desde esta página lo
    confirma (la misma clave que pide ``POST /ejecucion/lanzar``). ``launched`` es el formulario
    y el aviso que Lanzar ya comprobó, sin una corrida igual: no se comprueban otra vez.
    """
    if launched is None:
        form = _launch_form(
            values.get("families"),
            values.get("metric"),
            {k: values.get(k) for k in _LAUNCH_FIELDS},
        )
        warning = _check_launch(repository, ingest_id, form)
        duplicate = _duplicate(request, ingest_id, form)
    else:
        (form, warning), duplicate = launched, None
    ingests = ingests_by_id(repository)
    ingest = ingests.get(ingest_id)
    control = _control(request)
    draft = read_draft(request)
    context = {
        "wizard": draft_wizard("revisar", draft_facts(repository, draft, ingest_id, ingests)),
        "source": repository.source,
        "record": False,
        "duplicate": duplicate,
        **_duration_context(request, ingest_id),
        "ingest": ingest,
        "ingest_id": ingest_id,
        "review_rows": draft_items(form.values),
        "form": form.values,
        "form_errors": form.errors,
        "form_error": form_error,
        "exclusion_warning": warning[0] if warning else None,
        "exclusion_confirmation": warning[1] if warning else None,
        "control_reason": control.reason,
        "worker": _worker_state(request),
    }
    return _render(request, "pages/paso_revisar.html", context, status_code)


def _render_record(
    request: Request, repository: DataRepository, facts: RunFacts, step: StepKey
) -> HTMLResponse:
    """Ajustes o Revisar de una corrida lanzada: lo que se eligió, sin poder cambiarlo."""
    try:
        status = repository.get_run_status(facts.id, size=1)
    except (DataLayerError, InvalidQuery):
        return render_missing_run(request, facts.id)
    stored = _stored_runs(request)
    detail = next((r for r in stored if str(r.id) == facts.id), None)
    values = None
    record_error = None
    if detail is not None:
        try:
            values = form_values(detail)
        except ConfigExportError as exc:
            record_error = str(exc)
    by_class = {}
    if facts.ingest_id:
        with suppress(DataLayerError, InvalidQuery):
            by_class = repository.get_topology_report(facts.ingest_id, size=1).summary.by_class
    rows = settings_items(status.settings) if status.settings else []
    if values is not None:
        stored_labels = {label for label, _ in rows}
        rows.extend(row for row in draft_items(values) if row[0] not in stored_labels)
    context = {
        "wizard": run_wizard(step, facts),
        "source": repository.source,
        "record": True,
        "facts": facts,
        "run": status.ejecucion,
        "review_rows": rows,
        "form": values,
        "record_error": record_error,
        "family_choices": FAMILY_CHOICES,
        "metric_choices": METRIC_CHOICES,
        "families_hint": families_hint(),
        "class_counts": by_class,
        "reserve": status.reserve,
    }
    if step == "revisar":  # Ajustes no muestra las duraciones anteriores
        context.update(_duration_context(request, facts.ingest_id or "", facts.id, stored))
    template = "pages/paso_ajustes.html" if step == "ajustes" else "pages/paso_revisar.html"
    return _render(request, template, context)


@router.get("/corridas/{corrida}/ajustes", response_class=HTMLResponse, response_model=None)
def paso_ajustes(
    request: Request,
    corrida: str,
    repository: DataRepository = Depends(get_repository),  # noqa: B008
) -> HTMLResponse | RedirectResponse:
    if corrida == NEW:
        # ``?ingest_id=`` llega de un enlace que ya eligió el archivo (Resultados, rutas viejas):
        # si se puede ejecutar, el borrador lo guarda, para que Datos, la barra de pasos y Revisar
        # lean el mismo archivo que muestra la página. Un archivo igual al del borrador no escribe
        # nada: recargar el enlace no deja vieja a ninguna página.
        linked = request.query_params.get("ingest_id") or None
        accepted = accepted_ingests(repository)
        runnable = {i.item.ingest_id for i in accepted if not i.synthetic}
        draft = read_draft(request)
        # Un enlace con un archivo que no se puede ejecutar muestra el del borrador.
        shown = linked if linked and linked in runnable else None
        save = draft.with_ingest(shown) if shown and shown != draft.ingest_id else None
        copiar_de = (request.query_params.get("copiar_de") or "").strip().lstrip("#")
        if copiar_de:
            return _copy_settings(request, repository, copiar_de, ingest_id=shown, save=save)
        if save is not None:
            return _saved_to_ajustes(request, save)
        return _render_ajustes(
            request,
            repository,
            ingest_id=shown,
            imported=_shown_notice(request),
            ingests=accepted,
        )
    facts = run_facts(repository, corrida)
    if facts is None:
        return render_missing_run(request, corrida)
    return _render_record(request, repository, facts, "ajustes")


def _saved_to_ajustes(request: Request, draft: Draft, **notice: str) -> RedirectResponse:
    """Guarda ``draft`` y vuelve a Ajustes con un 303 (Post/Redirect/Get).

    Recargar, el botón Atrás del navegador o restaurar la sesión piden después esa dirección, que
    no escribe nada: una copia, una importación o un enlace no se repiten sobre lo que se editó
    después. ``notice`` (``aviso`` y ``corrida``) solo dice qué aviso mostrar."""
    target = step_href(NEW, "ajustes")
    if notice:
        target += "?" + urlencode(notice)
    response = RedirectResponse(target, status_code=303)
    save_draft(request, response, next_revision(request, draft))
    return response


def _shown_notice(request: Request) -> tuple[str, str] | None:
    """El aviso de una copia o importación ya guardada (``?aviso=``), solo para mostrar."""
    aviso = request.query_params.get("aviso")
    corrida = request.query_params.get("corrida") or ""
    if aviso in ("copiado", "copiado_sin_datos") and not corrida.isdigit():
        return None
    return {
        "copiado": (
            "info",
            f"Ajustes copiados de la corrida #{corrida}. Cambie la semilla y continúe.",
        ),
        "copiado_sin_datos": (
            "warning",
            f"Ajustes copiados de la corrida #{corrida}, pero el archivo de esa corrida no está "
            "disponible. Elija otros datos y cambie la semilla antes de continuar.",
        ),
        "importado": (
            "success",
            "Configuración importada. Revise los valores y continúe para lanzarla.",
        ),
        "importado_sin_datos": (
            "warning",
            "Configuración importada, pero sus datos no están cargados en esta plataforma. "
            "Cargue ese archivo en Datos o elija otro antes de lanzar: los resultados solo son "
            "comparables sobre los mismos datos.",
        ),
    }.get(aviso or "")


@router.post("/corridas/nueva/copiar", response_model=None)
def copiar_ajustes(
    request: Request,
    copiar: Annotated[str, Form()],
    repository: DataRepository = Depends(get_repository),  # noqa: B008
    draft_rev: Annotated[str | None, Form()] = None,
) -> HTMLResponse | RedirectResponse:
    """«Partir de una corrida anterior» en Ajustes."""
    if is_stale(request, draft_rev):
        return _render_ajustes(request, repository, conflict=True, status_code=409)
    return _copy_settings(request, repository, copiar.strip().lstrip("#"))


def _copy_settings(
    request: Request,
    repository: DataRepository,
    reference: str,
    *,
    ingest_id: str | None = None,
    save: Draft | None = None,
) -> HTMLResponse | RedirectResponse:
    """Copia todos los ajustes guardados de una corrida al borrador, sin validarlos: no crea ni
    reanuda una corrida, y Ajustes queda por revisar. Si no se puede copiar, el borrador no cambia
    y Ajustes dice por qué. Lo usan el formulario de copia y el enlace ``?copiar_de=`` de
    Resultados. Lo copiado se guarda y la respuesta lleva a Ajustes (``_saved_to_ajustes``)."""

    def refused(reason: str) -> HTMLResponse:
        notice = (
            "warning",
            f"No se pudieron copiar los ajustes de la corrida #{reference}: {reason}.",
        )
        return _render_ajustes(request, repository, ingest_id=ingest_id, imported=notice, save=save)

    settings = request.app.state.settings
    if settings.data_source != "dal":
        return refused("la plataforma funciona con datos de prueba")
    detail = lectura.get_run(settings.db_path, reference)
    if detail is None:
        return refused("no existe esa corrida")
    try:
        values = form_values(detail)
    except ConfigExportError as exc:
        return refused(str(exc).rstrip("."))
    known = {i.item.ingest_id for i in accepted_ingests(repository) if not i.synthetic}
    values["ingest_id"] = detail.ingest_sha256 if detail.ingest_sha256 in known else ""
    copy = read_draft(request).with_values(values["ingest_id"] or None, values)
    aviso = "copiado" if values["ingest_id"] else "copiado_sin_datos"
    return _saved_to_ajustes(request, copy, aviso=aviso, corrida=str(detail.id))


@router.post("/corridas/nueva/ajustes/comprobar", response_model=None)
@router.post("/corridas/nueva/ajustes", response_model=None)
def guardar_ajustes(
    request: Request,
    repository: DataRepository = Depends(get_repository),  # noqa: B008
    ingest_id: Annotated[str | None, Form()] = None,
    families: Annotated[list[str] | None, Form()] = None,
    metric: Annotated[str | None, Form()] = None,
    min_train: Annotated[str | None, Form()] = None,
    horizon: Annotated[str | None, Form()] = None,
    step: Annotated[str | None, Form()] = None,
    seasonality: Annotated[str | None, Form()] = None,
    trials: Annotated[str | None, Form()] = None,
    seed: Annotated[str | None, Form()] = None,
    workers: Annotated[str | None, Form()] = None,
    destino: Annotated[str | None, Form()] = None,
    draft_rev: Annotated[str | None, Form()] = None,
) -> HTMLResponse | RedirectResponse | Response:
    """«Continuar a Revisar y lanzar»: valida como lo hará Lanzar y guarda el borrador. Nada se
    lanza todavía; un error vuelve a Ajustes con su explicación.

    Con ``destino`` (Atrás o un paso de la barra), guarda lo escrito sin validarlo y va a ese
    paso: salir de Ajustes nunca pierde un valor. Revisar valida igual que «Continuar».

    Si la página que envía vio una revisión del borrador más vieja que la vigente (otra pestaña
    copió, importó o editó después), no se guarda nada: el guardado automático recibe 409 y los
    demás envíos vuelven a Ajustes con el aviso de que el borrador cambió.
    """
    autosave = request.headers.get("X-Pred-Autosave") == "true"
    if not request.url.path.endswith("/comprobar") and is_stale(request, draft_rev):
        if autosave:
            return Response(
                status_code=409, headers={"X-Pred-Draft-Rev": read_draft(request).stamp}
            )
        return _render_ajustes(request, repository, conflict=True, status_code=409)
    fields = {
        "min_train": min_train,
        "horizon": horizon,
        "step": step,
        "seasonality": seasonality,
        "trials": trials,
        "seed": seed,
        "workers": workers,
    }
    if destino in STEP_KEYS and destino != "revisar":
        draft = read_draft(request)
        typed = {"families": families or [], "metric": metric, **fields}
        saved = draft.with_values(ingest_id or draft.ingest_id, typed)
        if destino == "ajustes" and autosave:
            return _render_ajustes(request, repository, save=saved, autosave=True)
        response = RedirectResponse(step_href(NEW, destino), status_code=303)
        save_draft(request, response, next_revision(request, saved))
        return response
    form = _launch_form(families, metric, fields)
    warning = _check_launch(repository, ingest_id, form)
    if request.url.path.endswith("/comprobar") or form.errors or ingest_id is None:
        return _render_ajustes(request, repository, form=form, exclusion=warning, checked=True)
    response = RedirectResponse(step_href(NEW, "revisar"), status_code=303)
    settled = read_draft(request).with_settings(ingest_id, form.values)
    save_draft(request, response, next_revision(request, settled))
    return response


@router.get("/corridas/{corrida}/revisar", response_class=HTMLResponse)
def paso_revisar(
    request: Request,
    corrida: str,
    repository: DataRepository = Depends(get_repository),  # noqa: B008
) -> HTMLResponse:
    if corrida != NEW:
        facts = run_facts(repository, corrida)
        if facts is None:
            return render_missing_run(request, corrida)
        return _render_record(request, repository, facts, "revisar")
    draft = read_draft(request)
    if draft.ingest_id is None:
        return render_locked(
            request,
            Locked(
                title="Revisar y lanzar",
                heading="Primero elija los datos",
                text="Para revisar y lanzar hace falta un archivo validado.",
                actions=(("Ir a Datos", step_href(NEW, "datos"), True),),
            ),
            draft_wizard("revisar", draft_facts(repository, draft)),
        )
    return _render_revisar(request, repository, ingest_id=draft.ingest_id, values=draft.values)


@router.post("/ejecucion/lanzar", response_model=None)
def lanzar(
    request: Request,
    repository: DataRepository = Depends(get_repository),  # noqa: B008
    ingest_id: Annotated[str | None, Form()] = None,
    families: Annotated[list[str] | None, Form()] = None,
    metric: Annotated[str | None, Form()] = None,
    min_train: Annotated[str | None, Form()] = None,
    horizon: Annotated[str | None, Form()] = None,
    step: Annotated[str | None, Form()] = None,
    seasonality: Annotated[str | None, Form()] = None,
    trials: Annotated[str | None, Form()] = None,
    seed: Annotated[str | None, Form()] = None,
    workers: Annotated[str | None, Form()] = None,
    confirmar: Annotated[str | None, Form()] = None,
) -> HTMLResponse | RedirectResponse:
    """Encola la ejecución. Un formulario con errores vuelve a Ajustes; si alguna clase queda con
    una sola familia, Revisar avisa qué SKU quedarían sin campeón, y ``confirmar`` con la clave de
    ese aviso lanza igual. Al lanzar, el borrador se descarta."""
    fields = {
        "min_train": min_train,
        "horizon": horizon,
        "step": step,
        "seasonality": seasonality,
        "trials": trials,
        "seed": seed,
        "workers": workers,
    }
    form = _launch_form(families, metric, fields)
    warning = _check_launch(repository, ingest_id, form)
    if form.errors or form.settings is None or ingest_id is None:
        return _render_ajustes(request, repository, form=form, exclusion=warning, checked=True)
    duplicate = _duplicate(request, ingest_id, form)
    if duplicate is not None:
        return RedirectResponse(
            f"{step_href(str(duplicate.id), 'ejecucion')}?aviso=existente", status_code=303
        )
    if warning is not None and confirmar != warning[1]:
        return _render_revisar(
            request, repository, ingest_id=ingest_id, values=form.values, launched=(form, warning)
        )
    try:
        result = _control(request).launch(ingest_id, form.settings)
    except (ControlUnavailable, RunRequestError) as exc:
        return _render_revisar(
            request,
            repository,
            ingest_id=ingest_id,
            values=form.values,
            form_error=str(exc),
            launched=(form, warning),
        )
    aviso = "en_cola" if result.queued else "existente"
    response = RedirectResponse(
        f"{step_href(str(result.ejecucion_id), 'ejecucion')}?{urlencode({'aviso': aviso})}",
        status_code=303,
    )
    discard_draft(request, response)
    return response


@router.get("/ejecucion/configuracion.json", response_model=None)
def exportar_configuracion(request: Request) -> Response:
    """La configuración de una ejecución guardada (``?ejecucion=``; vacío, la más reciente)."""
    settings = request.app.state.settings
    if settings.data_source == "fixture":
        return PlainTextResponse(
            "Está viendo datos de prueba: no hay una configuración real que exportar.\n",
            status_code=409,
        )
    reference = (request.query_params.get("ejecucion") or "").strip() or LATEST
    detail = lectura.get_run(settings.db_path, reference)
    if detail is None:
        return PlainTextResponse(
            f"No existe la ejecución «{reference}»: no hay una configuración que exportar.\n",
            status_code=404,
        )
    versions = {
        "plataforma": package_version("pred-platform"),
        "contrato_v1": CONTRACT_VERSION,
        "contrato_v2": M3_CONTRACT_VERSION,
        "formato_parametros": PARAMETERS_SCHEMA_VERSION,
    }
    try:
        document = export_config(detail, versions)
    except ConfigExportError as exc:
        return PlainTextResponse(f"{exc}\n", status_code=409)
    return Response(
        content=json.dumps(document, indent=2, ensure_ascii=False) + "\n",
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{config_filename(detail.run_id)}"'},
    )


@router.post("/ejecucion/configuracion", response_model=None)
def importar_configuracion(
    request: Request,
    repository: DataRepository = Depends(get_repository),  # noqa: B008
    archivo: Annotated[UploadFile | None, File()] = None,
    draft_rev: Annotated[str | None, Form()] = None,
) -> HTMLResponse | RedirectResponse:
    """Rellena el borrador con un archivo exportado, sin validarlo, y vuelve a Ajustes. No lanza."""
    if is_stale(request, draft_rev):
        return _render_ajustes(request, repository, conflict=True, status_code=409)
    raw = b"" if archivo is None else archivo.file.read(MAX_BYTES + 1)
    imported = parse_config(raw if isinstance(raw, bytes) else raw.encode("utf-8"))
    if imported.values is None:
        problem = f"No se importó la configuración. {' '.join(imported.errors)}"
        return _render_ajustes(request, repository, import_error=problem, status_code=400)
    known = {i.item.ingest_id for i in accepted_ingests(repository) if not i.synthetic}
    values = dict(imported.values)
    # Si el archivo trae otra ingesta que esta plataforma no tiene, queda sin ingesta elegida,
    # para que nadie lance los ajustes importados sobre otros datos sin darse cuenta.
    values["ingest_id"] = imported.ingest_sha256 if imported.ingest_sha256 in known else ""
    # El borrador guarda lo importado: salir de Ajustes no lo pierde (Revisar lo valida).
    save = read_draft(request).with_values(values["ingest_id"] or None, values)
    aviso = "importado" if values["ingest_id"] else "importado_sin_datos"
    return _saved_to_ajustes(request, save, aviso=aviso)


def _render_action_error(
    request: Request,
    repository: DataRepository,
    corrida: str | None,
    message: str,
    status_code: int = 200,
) -> HTMLResponse:
    """Una acción que no se aplicó: el paso Ejecución de esa corrida con el motivo; si la corrida
    no existe, el de la más reciente, y sin corridas, una página que lo dice."""
    target = corrida if corrida and run_facts(repository, corrida) else _latest_run_id(repository)
    if target is None:
        return render_locked(
            request,
            Locked(title="Ejecución", heading=message, text="Todavía no hay corridas."),
            status_code=status_code,
        )
    return _render_execution(
        request, repository, target, action_error=message, status_code=status_code
    )


def _act(request: Request, repository: DataRepository, ejecucion: str, action: str) -> Any:
    control = _control(request)
    number = lectura.run_number(ejecucion.strip())
    if number is None:
        message = "La ejecución indicada no es válida."
        return _render_action_error(request, repository, None, message, status_code=400)
    try:
        done = control.stop(number) if action == "stop" else control.resume(number)
    except (ControlUnavailable, RunRequestError) as exc:
        return _render_action_error(request, repository, str(number), str(exc))
    aviso = ("detenida" if action == "stop" else "reanudada") if done else "no_aplica"
    return RedirectResponse(
        f"{step_href(str(number), 'ejecucion')}?{urlencode({'aviso': aviso})}", status_code=303
    )


@router.post("/ejecucion/detener", response_model=None)
def detener(
    request: Request,
    ejecucion: Annotated[str, Form()],
    repository: DataRepository = Depends(get_repository),  # noqa: B008
) -> Any:
    return _act(request, repository, ejecucion, "stop")


@router.post("/ejecucion/reanudar", response_model=None)
def reanudar(
    request: Request,
    ejecucion: Annotated[str, Form()],
    repository: DataRepository = Depends(get_repository),  # noqa: B008
) -> Any:
    return _act(request, repository, ejecucion, "resume")
