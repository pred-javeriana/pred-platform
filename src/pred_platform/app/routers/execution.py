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
from datetime import UTC, datetime
from importlib.metadata import version as package_version
from typing import Annotated, Any, Literal
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response

from pred_platform.app.borrador import Draft, clear_draft, read_draft, save_draft
from pred_platform.app.configuracion import (
    MAX_BYTES,
    ConfigExportError,
    config_filename,
    export_config,
    parse_config,
)
from pred_platform.app.corridas import (
    draft_facts,
    ingests_by_id,
    resolve_run_id,
    run_facts,
    status_facts,
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
    accepted_ingests,
    classes_without_family,
    draft_items,
    elapsed_text,
    error_action,
    exclusion_confirmation,
    exclusion_warning,
    families_hint,
    focus_state,
    focus_tasks,
    history_needed,
    parse_execution_query,
    parse_launch_form,
    refusal_text,
    run_actions,
    run_compares_nothing,
    run_error,
    settings_items,
    short_of_two,
    should_poll,
    single_family_classes,
)
from pred_platform.app.execution_display import execution_display
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
from pred_platform.app.resultados_m3 import run_outcome
from pred_platform.app.routers.corridas import (
    Locked,
    redirect_with_query,
    render_locked,
    render_missing_run,
)
from pred_platform.app.seleccion import error_title
from pred_platform.contract import CONTRACT_VERSION, M3_CONTRACT_VERSION
from pred_platform.contract.errors import DataLayerError, InvalidQuery
from pred_platform.contract.models import RunStatus, SkuClass, TopologyMetrics
from pred_platform.dal import lectura
from pred_platform.dal.ejecuciones import RunRequestError
from pred_platform.data.deps import get_repository
from pred_platform.data.query import LATEST, MAX_SIZE
from pred_platform.data.repository import DataRepository
from pred_platform.worker.candado import WorkerLock
from pred_platform.worker.control import ControlUnavailable, RunControl
from pred_platform.worker.service import PARAMETERS_SCHEMA_VERSION

router = APIRouter()

_TASK_STATES = ("pendiente", "ejecutando", "finalizo_m2", "exitosa", "fallida", "no_ejecutable")
_LAUNCH_FIELDS = ("min_train", "horizon", "step", "seasonality", "trials", "seed", "workers")


def _control(request: Request) -> RunControl:
    return request.app.state.run_control  # type: ignore[no-any-return]


def _worker_state(request: Request) -> Literal["active", "off", "stopped"]:
    """Whether a worker follows the queue: this app's live thread, or another process that holds
    the database. Otherwise a launched run waits for ``make worker``: ``off`` when this app runs
    no worker, ``stopped`` when its thread ended (the app log says why)."""
    settings = request.app.state.settings
    if settings.data_source != "dal":
        return "off"
    thread = getattr(request.app.state, "worker_thread", None)
    if thread is not None and thread.is_alive():
        return "active"
    if WorkerLock.for_database(settings.db_path).held_elsewhere():
        return "active"
    return "stopped" if settings.worker == "embedded" else "off"


def _task_rows(status: RunStatus) -> list[dict[str, Any]]:
    rows = []
    for task in status.tasks:
        error = task.error
        rows.append(
            {
                "task": task,
                "error": None
                if error is None
                else {
                    "title": error_title(error.code, error.stage, error.row_index, error.column),
                    "detail": error.detail,
                    "code": error.code,
                    "stage": error.stage,
                    "action": error_action(error.code),
                },
            }
        )
    return rows


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
) -> dict[str, Any]:
    """El cuerpo de Ejecución. ``ejecucion`` es la corrida del paso (la ruta manda sobre
    ``?ejecucion=``); sin ella, la de la consulta o la más reciente (el sondeo)."""
    params = dict(request.query_params)
    if ejecucion is not None:
        params["ejecucion"] = ejecucion
    control = _control(request)
    capabilities = repository.get_capabilities()
    orchestration = next(item for item in capabilities.items if item.id == "run_orchestration")

    query_error: str | None = None
    try:
        query = parse_execution_query(params)
    except InvalidQuery as exc:
        query, query_error = ExecutionQuery(ejecucion=ejecucion or LATEST), str(exc)
    status: RunStatus | None = None
    status_error: dict[str, object] | None = None
    try:
        status = repository.get_run_status(query.ejecucion, **query.as_kwargs())
    except InvalidQuery as exc:
        query_error = str(exc)
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
    poll = should_poll(estado, counts, reason=control.reason)
    error = run_error(repository.get_run_error(run.id)) if run is not None else None
    actions = run_actions(
        estado,
        reason=control.reason,
        error=error,
        nothing_to_compare=run_compares_nothing(repository, run, status),
    )
    now = datetime.now(UTC)
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
        "settings_rows": settings_items(status.settings) if status and status.settings else [],
        "reserve": status.reserve if status is not None else None,
        "progress": progress,
        "state_counts": [
            (code, progress.tareas_por_estado.get(code, 0))  # type: ignore[call-overload]
            for code in _TASK_STATES
        ]
        if progress is not None
        else [],
        "rows": _task_rows(tasks) if tasks is not None else [],
        "pages": page_meta(tasks.page) if tasks and tasks.page and not focus else None,
        "focus": focus,
        "focus_estado": focus_estado,
        "focus_total": tasks.page.total if focus and tasks and tasks.page else 0,
        "outcome": run_outcome(repository, run, counts) if run is not None else None,
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
    facts = run_facts(repository, corrida)
    if facts is None:
        return render_missing_run(request, corrida)
    context = _context(request, repository, ejecucion=facts.id, action_error=action_error)
    context["wizard"] = run_wizard("ejecucion", facts)
    return _render(request, "pages/execution.html", context, status_code)


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
        return redirect_with_query(step_href(NEW, "datos"), [])
    wanted = (params.get("ejecucion") or "").strip()
    target = resolve_run_id(repository, wanted or LATEST)
    if target is None and wanted not in ("", LATEST):
        target = wanted  # Una referencia desconocida conserva la página 404.
    if target is None:
        return redirect_with_query(step_href(NEW, "datos"), [])
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
    facts = status_facts(repository, status) if status is not None else None
    if facts is not None:
        context["wizard"] = run_wizard("ejecucion", facts)
    return _render(
        request, "partials/execution_poll.html", context, 200 if context["poll"] else 286
    )


# ---------------------------------------------------------------- Ajustes y Revisar
def _class_skus(
    repository: DataRepository, ingest_id: str, sku_class: SkuClass
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
    repository: DataRepository, ingest_id: str | None, form: FormResult
) -> tuple[ExclusionWarning, str | None] | None:
    """La regla de lanzamiento sobre los datos y las familias: deja los errores en ``form`` y
    devuelve el aviso de los SKU que quedarían sin campeón si alguna clase queda con una sola
    familia, con la clave que confirma lanzar así (``None`` si un rechazo impide lanzar igual).
    """
    form.values["ingest_id"] = ingest_id or ""
    synthetic = {i.item.ingest_id for i in accepted_ingests(repository) if i.synthetic}
    if not ingest_id:
        form.errors["ingest_id"] = "Elija el archivo de datos que se va a ejecutar."
        return None
    if ingest_id in synthetic:
        form.errors["ingest_id"] = SYNTHETIC_INGEST
        return None
    if form.errors:
        return None
    try:
        by_class = repository.get_topology_report(ingest_id, size=1).summary.by_class
    except DataLayerError:
        by_class = {}  # sin topología publicada, decide el motor
    except InvalidQuery:  # no es el SHA-256 de una ingesta
        by_class = {}
        form.errors["ingest_id"] = "Elija un archivo de datos aceptado en Datos."
    chosen = form.values["families"]
    needed = history_needed(form.settings) if form.settings is not None else 0
    single = single_family_classes(by_class, chosen)
    skus: dict[SkuClass, list[TopologyMetrics]] = {
        c: _class_skus(repository, ingest_id, c) for c in short_of_two(by_class, chosen)
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
    status_code: int = 200,
) -> HTMLResponse:
    """Ajustes de la corrida nueva: el formulario con los valores del borrador (o los enviados).

    ``imported`` es el aviso (variante, texto) de una configuración importada: rellena el
    formulario y, si el archivo trae otra ingesta que esta plataforma no tiene, lo deja sin
    ingesta elegida, para que nadie lance los ajustes importados sobre otros datos sin darse
    cuenta. ``exclusion`` es el aviso de los SKU que quedarían sin campeón, junto al rechazo que
    impide continuar. ``save`` es el borrador que la página guarda y muestra en la barra de pasos.
    """
    draft = save or read_draft(request)
    values = form.values if form is not None else dict(draft.values)
    if form is None:
        values["ingest_id"] = ingest_id or draft.ingest_id or ""
    ingests = accepted_ingests(repository)
    # El archivo que eligió la persona (el borrador, un enlace o el formulario enviado); sin
    # elegir, el formulario ofrece el primero que se puede ejecutar, pero Datos sigue por hacer.
    chosen = values.get("ingest_id") or None
    selected = chosen
    if not selected and imported is None:
        selected = next((i.item.ingest_id for i in ingests if not i.synthetic), None)
    control = _control(request)
    context = {
        "wizard": draft_wizard(
            "ajustes", draft_facts(repository, draft, chosen), can_continue=bool(ingests)
        ),
        "source": repository.source,
        "ingests": ingests,
        "selected_ingest": selected,
        "form": values,
        "form_errors": form.errors if form is not None else {},
        "form_error": form_error,
        "import_error": import_error,
        "notice": imported,
        "exclusion_warning": exclusion[0] if exclusion else None,
        "family_choices": FAMILY_CHOICES,
        "families_hint": families_hint(),
        "metric_choices": METRIC_CHOICES,
        "control_reason": control.reason,
        "launch_form_id": AJUSTES_FORM,
    }
    response = _render(request, "pages/paso_ajustes.html", context, status_code)
    if save is not None:
        save_draft(response, save)
    return response


def _render_revisar(
    request: Request,
    repository: DataRepository,
    *,
    ingest_id: str,
    values: dict[str, Any],
    form_error: str | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    """Revisar y lanzar: lo que se va a lanzar, con «Cambiar» por fila, y el botón Lanzar.

    El aviso de los SKU que quedarían sin campeón se muestra aquí; lanzar desde esta página lo
    confirma (la misma clave que pide ``POST /ejecucion/lanzar``).
    """
    form = _launch_form(
        values.get("families"), values.get("metric"), {k: values.get(k) for k in _LAUNCH_FIELDS}
    )
    warning = _check_launch(repository, ingest_id, form)
    ingest = ingests_by_id(repository).get(ingest_id)
    control = _control(request)
    draft = read_draft(request)
    context = {
        "wizard": draft_wizard("revisar", draft_facts(repository, draft, ingest_id)),
        "source": repository.source,
        "record": False,
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
    context = {
        "wizard": run_wizard(step, facts),
        "source": repository.source,
        "record": True,
        "facts": facts,
        "run": status.ejecucion,
        "review_rows": settings_items(status.settings) if status.settings else [],
        "reserve": status.reserve,
    }
    template = "pages/paso_ajustes.html" if step == "ajustes" else "pages/paso_revisar.html"
    return _render(request, template, context)


@router.get("/corridas/{corrida}/ajustes", response_class=HTMLResponse)
def paso_ajustes(
    request: Request,
    corrida: str,
    repository: DataRepository = Depends(get_repository),  # noqa: B008
) -> HTMLResponse:
    if corrida == NEW:
        # ``?ingest_id=`` llega de un enlace que ya eligió el archivo (Resultados, rutas viejas):
        # si se puede ejecutar, el borrador lo guarda, para que Datos, la barra de pasos y Revisar
        # lean el mismo archivo que muestra la página.
        linked = request.query_params.get("ingest_id") or None
        runnable = {i.item.ingest_id for i in accepted_ingests(repository) if not i.synthetic}
        save = read_draft(request).with_ingest(linked) if linked and linked in runnable else None

        copiar_de = (request.query_params.get("copiar_de") or "").strip().lstrip("#")
        if copiar_de:
            settings = request.app.state.settings
            if settings.data_source == "fixture":
                notice = (
                    "warning",
                    f"No se pudieron copiar los ajustes de la corrida #{copiar_de}: "
                    "la plataforma funciona con datos de prueba.",
                )
                return _render_ajustes(
                    request, repository, ingest_id=linked, imported=notice, save=save
                )
            detail = lectura.get_run(settings.db_path, copiar_de)
            if detail is None:
                notice = (
                    "warning",
                    f"No se pudieron copiar los ajustes de la corrida #{copiar_de}: "
                    "no existe esa corrida.",
                )
                return _render_ajustes(
                    request, repository, ingest_id=linked, imported=notice, save=save
                )
            versions = {
                "plataforma": package_version("pred-platform"),
                "contrato_v1": CONTRACT_VERSION,
                "contrato_v2": M3_CONTRACT_VERSION,
                "formato_parametros": PARAMETERS_SCHEMA_VERSION,
            }
            try:
                document = export_config(detail, versions)
                imported = parse_config(json.dumps(document).encode("utf-8"))
            except ConfigExportError as exc:
                motivo = str(exc).rstrip(".")
                notice = (
                    "warning",
                    f"No se pudieron copiar los ajustes de la corrida #{copiar_de}: {motivo}.",
                )
                return _render_ajustes(
                    request, repository, ingest_id=linked, imported=notice, save=save
                )
            if imported.values is not None:
                form = FormResult(imported.values, None, {})
                form.values["ingest_id"] = imported.ingest_sha256 or ""
                notice = (
                    "info",
                    f"Ajustes copiados de la corrida #{copiar_de}. Cambie la semilla y continúe.",
                )
                save_copy = read_draft(request).with_values(
                    form.values["ingest_id"] or None, form.values
                )
                return _render_ajustes(
                    request, repository, form=form, imported=notice, save=save_copy
                )
            motivo = (
                " ".join(imported.errors).rstrip(".")
                if imported.errors
                else "la configuración no es válida"
            )
            notice = (
                "warning",
                f"No se pudieron copiar los ajustes de la corrida #{copiar_de}: {motivo}.",
            )
            return _render_ajustes(
                request, repository, ingest_id=linked, imported=notice, save=save
            )

        return _render_ajustes(request, repository, ingest_id=linked, save=save)
    facts = run_facts(repository, corrida)
    if facts is None:
        return render_missing_run(request, corrida)
    return _render_record(request, repository, facts, "ajustes")


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
) -> HTMLResponse | RedirectResponse:
    """«Continuar a Revisar y lanzar»: valida como lo hará Lanzar y guarda el borrador. Nada se
    lanza todavía; un error vuelve a Ajustes con su explicación.

    Con ``destino`` (Atrás o un paso de la barra), guarda lo escrito sin validarlo y va a ese
    paso: salir de Ajustes nunca pierde un valor. Revisar valida igual que «Continuar».
    """
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
        response = RedirectResponse(step_href(NEW, destino), status_code=303)
        save_draft(response, draft.with_values(ingest_id or draft.ingest_id, typed))
        return response
    form = _launch_form(families, metric, fields)
    warning = _check_launch(repository, ingest_id, form)
    if form.errors or ingest_id is None:
        return _render_ajustes(request, repository, form=form, exclusion=warning)
    response = RedirectResponse(step_href(NEW, "revisar"), status_code=303)
    save_draft(response, read_draft(request).with_settings(ingest_id, form.values))
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
        return _render_ajustes(request, repository, form=form, exclusion=warning)
    if warning is not None and confirmar != warning[1]:
        return _render_revisar(request, repository, ingest_id=ingest_id, values=form.values)
    try:
        result = _control(request).launch(ingest_id, form.settings)
    except (ControlUnavailable, RunRequestError) as exc:
        return _render_revisar(
            request, repository, ingest_id=ingest_id, values=form.values, form_error=str(exc)
        )
    aviso = "en_cola" if result.queued else "existente"
    response = RedirectResponse(
        f"{step_href(str(result.ejecucion_id), 'ejecucion')}?{urlencode({'aviso': aviso})}",
        status_code=303,
    )
    clear_draft(response)
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
) -> HTMLResponse:
    """Rellena Ajustes y el borrador con un archivo exportado, sin validarlo. No lanza."""
    raw = b"" if archivo is None else archivo.file.read(MAX_BYTES + 1)
    imported = parse_config(raw if isinstance(raw, bytes) else raw.encode("utf-8"))
    if imported.values is None:
        problem = f"No se importó la configuración. {' '.join(imported.errors)}"
        return _render_ajustes(request, repository, import_error=problem, status_code=400)
    known = {i.item.ingest_id for i in accepted_ingests(repository) if not i.synthetic}
    form = FormResult(imported.values, None, {})
    if imported.ingest_sha256 in known:
        form.values["ingest_id"] = imported.ingest_sha256
        notice = (
            "success",
            "Configuración importada. Revise los valores y continúe para lanzarla.",
        )
    else:
        form.values["ingest_id"] = ""
        notice = (
            "warning",
            "Configuración importada, pero sus datos no están cargados en esta plataforma. "
            "Cargue ese archivo en Datos o elija otro antes de lanzar: los resultados solo son "
            "comparables sobre los mismos datos.",
        )
    # El borrador guarda lo importado: salir de Ajustes no lo pierde (Revisar lo valida).
    save = read_draft(request).with_values(form.values["ingest_id"] or None, form.values)
    return _render_ajustes(request, repository, form=form, imported=notice, save=save)


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
