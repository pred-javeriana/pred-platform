"""Superficie Ejecución (C4): lanzar, seguir, detener y reanudar una corrida.

La lectura sale de ``DataRepository`` (``get_run_status``, ``list_ingests``); las escrituras, de
``app.state.run_control`` (``worker/control.py``). Filtros y paginación de las tareas se delegan al
repositorio. Mientras la corrida está en curso el cuerpo se refresca con HTMX cada 2 s.
"""

from __future__ import annotations

from typing import Annotated, Any
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from pred_platform.app.datos import error_view, page_meta
from pred_platform.app.ejecucion import (
    ACTIVE_STATES,
    FAMILY_CHOICES,
    FINISHED_STATES,
    METRIC_CHOICES,
    NOTICES,
    ExecutionQuery,
    FormResult,
    default_form_values,
    error_action,
    parse_execution_query,
    parse_launch_form,
    run_actions,
    settings_items,
)
from pred_platform.app.seleccion import error_title
from pred_platform.contract.errors import DataLayerError, InvalidQuery
from pred_platform.contract.models import RunStatus
from pred_platform.dal.ejecuciones import RunRequestError
from pred_platform.data.deps import get_repository
from pred_platform.data.repository import DataRepository
from pred_platform.worker.control import ControlUnavailable, RunControl

router = APIRouter()

_TASK_STATES = ("pendiente", "ejecutando", "exitosa", "fallida", "no_ejecutable")


def _control(request: Request) -> RunControl:
    return request.app.state.run_control  # type: ignore[no-any-return]


def _worker_active(request: Request) -> bool:
    """Whether this app runs the worker (otherwise a launched run waits for ``make worker``)."""
    settings = request.app.state.settings
    return bool(settings.data_source == "dal" and settings.worker == "embedded")


def _accepted_ingests(repository: DataRepository) -> list[Any]:
    try:
        listing = repository.list_ingests(size=200)
    except DataLayerError:
        return []
    return [item for item in listing.items if item.status == "accepted"]


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
    action_error: str | None = None,
) -> dict[str, Any]:
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
    ingests = _accepted_ingests(repository)
    selected = (form.values.get("ingest_id") if form else None) or params.get("ingest_id")
    if not selected and ingests:
        selected = ingests[0].ingest_id
    poll = estado in ACTIVE_STATES and control.reason is None
    notice = NOTICES.get(params.get("aviso", ""))
    progress = status.progress if status is not None else None
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
        "rows": _task_rows(status) if status is not None else [],
        "pages": page_meta(status.page) if status and status.page else None,
        "actions": run_actions(estado, reason=control.reason),
        "control_reason": control.reason,
        "worker_active": _worker_active(request),
        "poll": poll,
        "finished": estado in FINISHED_STATES,
        "results_href": f"/resultados?ejecucion={run.id}" if run is not None else None,
        "notice": notice,
        "action_error": action_error,
        "ingests": ingests,
        "selected_ingest": selected,
        "form": form.values if form else default_form_values(),
        "form_errors": form.errors if form else {},
        "form_error": form_error,
        "family_choices": FAMILY_CHOICES,
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
) -> HTMLResponse | RedirectResponse:
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
    if not ingest_id:
        form.errors["ingest_id"] = "Elija la ingesta que se va a ejecutar."
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


def _act(request: Request, repository: DataRepository, ejecucion: str, action: str) -> Any:
    control = _control(request)
    try:
        number = int(ejecucion)
        done = control.stop(number) if action == "stop" else control.resume(number)
    except ValueError:
        return _render(
            request,
            "pages/execution.html",
            _context(request, repository, action_error="La ejecución indicada no es válida."),
            400,
        )
    except ControlUnavailable as exc:
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
