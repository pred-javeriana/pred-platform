"""Superficie Ejecución (C4): lanzar, seguir, detener y reanudar una corrida.

La lectura sale de ``DataRepository`` (``get_run_status``, ``list_ingests``); las escrituras, de
``app.state.run_control`` (``worker/control.py``). Filtros y paginación de las tareas se delegan al
repositorio. Mientras la corrida está en curso el cuerpo se refresca con HTMX cada 2 s.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from importlib.metadata import version as package_version
from typing import Annotated, Any, Literal
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response

from pred_platform.app.configuracion import (
    MAX_BYTES,
    ConfigExportError,
    config_filename,
    export_config,
    parse_config,
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
    classes_without_family,
    default_form_values,
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
    stage_steps,
)
from pred_platform.app.presentation import describe_run
from pred_platform.app.resultados_m3 import run_outcome
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


def _accepted_ingests(repository: DataRepository) -> list[LaunchIngest]:
    """Las ingestas aceptadas, marcadas si su informe declara datos sintéticos."""
    try:
        listing = repository.list_ingests(size=200)
    except DataLayerError:
        return []
    accepted = [item for item in listing.items if item.status == "accepted"]
    synthetic = repository.synthetic_ingests(
        [item.ingest_id for item in accepted if item.ingest_id]
    )
    return [LaunchIngest(item, item.ingest_id in synthetic) for item in accepted]


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


def _context(
    request: Request,
    repository: DataRepository,
    *,
    form: FormResult | None = None,
    form_error: str | None = None,
    import_error: str | None = None,
    action_error: str | None = None,
    imported: tuple[str, str] | None = None,
    exclusion: tuple[ExclusionWarning, str | None] | None = None,
) -> dict[str, Any]:
    """``imported`` es el aviso (variante, texto) de una configuración importada: abre el formulario
    con sus valores y, si el archivo trae otra ingesta que esta plataforma no tiene, sin ingesta
    elegida, para que nadie lance los ajustes importados sobre otros datos sin darse cuenta.
    ``exclusion`` es el aviso de los SKU que quedarían sin campeón y la clave que confirma lanzar
    así (``None`` si el formulario no lanza de todos modos)."""
    params = dict(request.query_params)
    control = _control(request)
    capabilities = repository.get_capabilities()
    orchestration = next(item for item in capabilities.items if item.id == "run_orchestration")

    query_error: str | None = None
    try:
        query = parse_execution_query(params)
    except InvalidQuery as exc:
        query, query_error = ExecutionQuery(), str(exc)
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
    ingests = _accepted_ingests(repository)
    selected = (form.values.get("ingest_id") if form else None) or params.get("ingest_id")
    if not selected and imported is None:
        selected = next((i.item.ingest_id for i in ingests if not i.synthetic), None)
    poll = should_poll(estado, counts, reason=control.reason)
    notice = imported or NOTICES.get(params.get("aviso", ""))
    error = run_error(repository.get_run_error(run.id)) if run is not None else None
    actions = run_actions(
        estado,
        reason=control.reason,
        error=error,
        nothing_to_compare=run_compares_nothing(repository, run, status),
    )
    return {
        "active_surface": "ejecucion",
        "page_title": "Ejecución",
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
        and status_error["code"] == "platform.artifact_missing"
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
        "steps": stage_steps(run.estado, progress.etapa_actual if progress else None)
        if run is not None
        else [],
        "current_stage": dict(STAGES).get(progress.etapa_actual or "") if progress else None,
        "elapsed": elapsed_text(run, datetime.now(UTC)) if run is not None else None,
        "actions": actions,
        "run_status": describe_run(run.estado, resumable=actions.offers_resume)
        if run is not None
        else None,
        "control_reason": control.reason,
        "worker": _worker_state(request),
        "poll": poll,
        "finished": estado in FINISHED_STATES,
        "results_href": f"/resultados?ejecucion={run.id}" if run is not None else None,
        "notice": notice,
        "imported": imported is not None,
        "action_error": action_error,
        "ingests": ingests,
        "selected_ingest": selected,
        "form": form.values if form else default_form_values(),
        "form_errors": form.errors if form else {},
        "form_error": form_error,
        "import_error": import_error,
        # El formulario se abre si no hay corrida que mostrar o si se lo pidió (Nueva ejecución,
        # una ingesta elegida en Datos o Resultados, un error o una importación por revisar).
        "launch_open": run is None
        or bool(params.get("ingest_id") or params.get("lanzar"))
        or bool(form and form.errors)
        or form_error is not None
        or import_error is not None
        or imported is not None
        or exclusion is not None,
        "exclusion_warning": exclusion[0] if exclusion else None,
        "exclusion_confirmation": exclusion[1] if exclusion else None,
        "family_choices": FAMILY_CHOICES,
        "families_hint": families_hint(),
        "metric_choices": METRIC_CHOICES,
    }


def _render(
    request: Request,
    template: str,
    context: dict[str, Any],
    status_code: int = 200,
) -> HTMLResponse:
    return request.app.state.templates.TemplateResponse(
        request, template, context, status_code=status_code
    )


@router.get("/ejecucion", response_class=HTMLResponse)
def ejecucion(
    request: Request,
    repository: DataRepository = Depends(get_repository),  # noqa: B008
) -> HTMLResponse:
    return _render(request, "pages/execution.html", _context(request, repository))


@router.get("/ejecucion/estado", response_class=HTMLResponse)
def ejecucion_estado(
    request: Request,
    repository: DataRepository = Depends(get_repository),  # noqa: B008
) -> HTMLResponse:
    """El cuerpo de la página para el sondeo. 286 le dice a htmx que deje de preguntar."""
    context = _context(request, repository)
    return _render(
        request, "partials/execution_body.html", context, 200 if context["poll"] else 286
    )


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
    """Encola la ejecución. Si alguna clase queda con una sola familia, primero avisa qué SKU
    quedarían sin campeón; ``confirmar`` con la clave de ese aviso lanza igual."""
    form = parse_launch_form(
        families=families or [],
        metric=metric,
        fields={
            "min_train": min_train,
            "horizon": horizon,
            "step": step,
            "seasonality": seasonality,
            "trials": trials,
            "seed": seed,
            "workers": workers,
        },
    )
    form.values["ingest_id"] = ingest_id or ""
    synthetic = {i.item.ingest_id for i in _accepted_ingests(repository) if i.synthetic}
    if not ingest_id:
        form.errors["ingest_id"] = "Elija el archivo de datos que se va a ejecutar."
    elif ingest_id in synthetic:
        form.errors["ingest_id"] = SYNTHETIC_INGEST
    elif not form.errors:
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
        key = exclusion_confirmation(ingest_id, chosen)
        if refusal:
            form.errors["families"] = refusal_text(refusal, by_class, skus, needed)
        # Con un rechazo, el aviso dice además lo que quedaría por corregir, sin lanzar igual.
        if single and (refusal or confirmar != key):
            warning = exclusion_warning(chosen, by_class, skus, needed)
            return _render(
                request,
                "pages/execution.html",
                _context(
                    request, repository, form=form, exclusion=(warning, None if refusal else key)
                ),
            )
    if form.errors or form.settings is None or ingest_id is None:
        return _render(request, "pages/execution.html", _context(request, repository, form=form))
    try:
        result = _control(request).launch(ingest_id, form.settings)
    except ControlUnavailable as exc:
        return _render(
            request,
            "pages/execution.html",
            _context(request, repository, form=form, form_error=str(exc)),
        )
    except RunRequestError as exc:
        return _render(
            request,
            "pages/execution.html",
            _context(request, repository, form=form, form_error=str(exc)),
        )
    aviso = "en_cola" if result.queued else "existente"
    return RedirectResponse(
        f"/ejecucion?{urlencode({'ejecucion': result.ejecucion_id, 'aviso': aviso})}",
        status_code=303,
    )


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
            f"No existe la ejecución {reference!r}: no hay una configuración que exportar.\n",
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
    """Rellena el formulario de lanzamiento con un archivo exportado. No lanza ni guarda nada."""
    raw = b"" if archivo is None else archivo.file.read(MAX_BYTES + 1)
    imported = parse_config(raw if isinstance(raw, bytes) else raw.encode("utf-8"))
    if imported.values is None:
        problem = f"No se importó la configuración. {' '.join(imported.errors)}"
        context = _context(request, repository, import_error=problem)
        return _render(request, "pages/execution.html", context, 400)
    known = {i.item.ingest_id for i in _accepted_ingests(repository) if not i.synthetic}
    form = FormResult(imported.values, None, {})
    origin = f" de la ejecución {imported.source_run_id}" if imported.source_run_id else ""
    if imported.ingest_sha256 in known:
        form.values["ingest_id"] = imported.ingest_sha256
        notice = (
            "success",
            f"Configuración importada{origin}. Revise los valores y lance la ejecución.",
        )
    else:
        form.values["ingest_id"] = ""
        notice = (
            "warning",
            f"Configuración importada{origin}, pero sus datos no están cargados en esta "
            f"plataforma (ingesta {(imported.ingest_sha256 or '')[:12]}…). Elija otro archivo "
            "antes de lanzar: los resultados solo son comparables sobre los mismos datos.",
        )
    return _render(
        request, "pages/execution.html", _context(request, repository, form=form, imported=notice)
    )


def _act(request: Request, repository: DataRepository, ejecucion: str, action: str) -> Any:
    control = _control(request)
    number = lectura.run_number(ejecucion.strip())
    if number is None:
        return _render(
            request,
            "pages/execution.html",
            _context(request, repository, action_error="La ejecución indicada no es válida."),
            400,
        )
    try:
        done = control.stop(number) if action == "stop" else control.resume(number)
    except (ControlUnavailable, RunRequestError) as exc:
        return _render(
            request, "pages/execution.html", _context(request, repository, action_error=str(exc))
        )
    ok = "detenida" if action == "stop" else "reanudada"
    return RedirectResponse(
        f"/ejecucion?{urlencode({'ejecucion': number, 'aviso': ok if done else 'no_aplica'})}",
        status_code=303,
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
