"""Superficie Datos: carga, diagnóstico y topología en una sola ruta de producto."""

from __future__ import annotations

from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse

from pred_platform.app.charts import topology_scatter_svg
from pred_platform.app.datos import (
    PendingUpload,
    error_view,
    page_meta,
    parse_topology_query,
    peek_pending,
    pop_pending,
    read_upload,
    recalled_report,
    remember_report,
    store_pending,
    topology_ready,
    topology_summary_metrics,
    validate_upload,
)
from pred_platform.contract.errors import DataLayerError, InvalidQuery
from pred_platform.contract.models import IngestReport, TopologyReport
from pred_platform.data.deps import get_repository
from pred_platform.data.repository import DataRepository

router = APIRouter()

_CSV_HINT = (
    "Solo archivos .csv. La validación de columnas y filas la realiza el repositorio, "
    "no esta vista."
)


@router.get("/datos", response_class=HTMLResponse)
def datos(
    request: Request,
    repository: DataRepository = Depends(get_repository),  # noqa: B008
) -> HTMLResponse:
    return _render_page(request, repository)


@router.post("/datos/cargar", response_model=None)
def cargar(
    request: Request,
    repository: DataRepository = Depends(get_repository),  # noqa: B008
    archivo: Annotated[UploadFile | None, File()] = None,
    pendiente: Annotated[str | None, Form()] = None,
    confirm_overwrite: Annotated[bool, Form()] = False,
) -> HTMLResponse | RedirectResponse:
    """Delega la carga en DataRepository.submit_ingest. Nunca valida el CSV en la vista."""
    if pendiente:
        stored = pop_pending(request, pendiente)
        if stored is None:
            return _render_page(
                request,
                repository,
                form_error="La confirmación expiró. Vuelva a cargar el archivo.",
            )
        return _submit(
            request,
            repository,
            stored.file_name,
            stored.content,
            confirm_overwrite=True,
        )

    file_name, content = read_upload(archivo)
    local_error = validate_upload(file_name, content)
    if local_error:
        return _render_page(request, repository, form_error=local_error)
    return _submit(
        request,
        repository,
        file_name,
        content,
        confirm_overwrite=confirm_overwrite,
    )


def _submit(
    request: Request,
    repository: DataRepository,
    file_name: str,
    content: bytes,
    *,
    confirm_overwrite: bool,
) -> HTMLResponse | RedirectResponse:
    try:
        report = repository.submit_ingest(file_name, content, confirm_overwrite=confirm_overwrite)
    except DataLayerError as exc:
        return _render_page(request, repository, layer_error=error_view(exc))
    except InvalidQuery as exc:
        return _render_page(request, repository, form_error=str(exc))

    if report.status == "needs_confirmation":
        token = store_pending(
            request, PendingUpload(file_name=file_name, content=content, report=report)
        )
        return RedirectResponse(
            url=f"/datos?{urlencode({'pendiente': token})}",
            status_code=303,
        )
    remember_report(request, report)
    return RedirectResponse(
        url=f"/datos?{urlencode({'ingest_id': report.ingest_id})}",
        status_code=303,
    )


def _render_page(
    request: Request,
    repository: DataRepository,
    *,
    form_error: str | None = None,
    layer_error: dict[str, object] | None = None,
) -> HTMLResponse:
    listing = None
    list_error = None
    try:
        listing = repository.list_ingests()
    except DataLayerError as exc:
        list_error = error_view(exc)

    params = {key: value for key, value in request.query_params.items()}
    pending_token = params.get("pendiente")
    ingest_id = params.get("ingest_id")
    report: IngestReport | None = None
    report_error = layer_error
    topology: TopologyReport | None = None
    topology_error = None
    topology_query = None
    topology_metrics = None
    topology_pages = None
    chart_svg = None
    query_error = None

    if pending_token:
        stored = peek_pending(request, pending_token)
        if stored is not None:
            report = stored.report
        elif report_error is None:
            report_error = {
                "code": "platform.artifact_missing",
                "detail": "La confirmación ya no está disponible. Vuelva a cargar el archivo.",
                "field": None,
                "column": None,
                "retryable": True,
                "blocked_by": [],
                "title": "Se requiere confirmar de nuevo.",
            }

    elif ingest_id:
        try:
            report = repository.get_ingest_report(ingest_id)
        except DataLayerError as exc:
            report = recalled_report(request, ingest_id)
            if report is None:
                report_error = error_view(exc)
        except InvalidQuery as exc:
            query_error = str(exc)

    if report is not None and report.status == "accepted":
        try:
            topology_query = parse_topology_query(params)
            topology = repository.get_topology_report(
                report.ingest_id, **topology_query.as_kwargs()
            )
            topology_metrics = topology_summary_metrics(topology)
            topology_pages = page_meta(topology.page)
            if topology.availability.status == "available":
                chart_svg = topology_scatter_svg(topology)
            else:
                topology_error = {
                    "code": topology.availability.reason_code,
                    "detail": topology.availability.detail,
                    "field": None,
                    "column": None,
                    "retryable": False,
                    "blocked_by": list(topology.availability.blocked_by),
                    "title": "La caracterización todavía no está disponible.",
                }
        except DataLayerError as exc:
            topology_error = error_view(exc)
        except InvalidQuery as exc:
            query_error = str(exc)

    source = repository.source
    if listing is not None:
        source = listing.source
    elif report is not None:
        source = report.source

    context = {
        "active_surface": "datos",
        "page_title": "Datos",
        "capabilities": repository.get_capabilities(),
        "listing": listing,
        "list_error": list_error,
        "report": report,
        "report_error": report_error,
        "form_error": form_error,
        "query_error": query_error,
        "pending_token": pending_token
        if report and report.status == "needs_confirmation"
        else None,
        "topology": topology,
        "topology_error": topology_error,
        "topology_query": topology_query,
        "topology_metrics": topology_metrics,
        "topology_pages": topology_pages,
        "chart_svg": chart_svg,
        "can_continue": topology_ready(topology)
        and report is not None
        and report.status == "accepted",
        "source": source,
        "csv_hint": _CSV_HINT,
        "has_ingest": report is not None,
    }
    return request.app.state.templates.TemplateResponse(
        request,
        "pages/data.html",
        context,
    )
