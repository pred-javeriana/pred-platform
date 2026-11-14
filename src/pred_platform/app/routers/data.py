"""Paso 1, Datos: carga, diagnóstico y topología de demanda.

Una corrida nueva elige aquí su archivo (``/corridas/nueva/datos``); una lanzada muestra el
archivo que usó, como registro de solo lectura (``/corridas/<id>/datos``). ``/datos`` lleva al
paso de la corrida nueva con la misma consulta.
"""

from __future__ import annotations

from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse

from pred_platform.app.borrador import next_revision, read_draft, save_draft
from pred_platform.app.charts import topology_scatter_svg
from pred_platform.app.charts_datos import demand_scatter_svg
from pred_platform.app.corridas import draft_facts, run_facts
from pred_platform.app.datos import (
    CSV_COLUMNS,
    CSV_HINT,
    PendingUpload,
    TopologyQuery,
    discard_pending,
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
    upload_blocked,
    validate_upload,
)
from pred_platform.app.ejecucion import accepted_ingests, families_hint
from pred_platform.app.pasos import NEW, Wizard, draft_wizard, run_wizard, step_href
from pred_platform.app.routers.corridas import (
    Locked,
    redirect_with_query,
    render_locked,
    render_missing_run,
)
from pred_platform.contract.errors import DataLayerError, InvalidQuery
from pred_platform.contract.models import IngestReport, TopologyReport
from pred_platform.data.deps import get_repository
from pred_platform.data.query import MAX_PAGE, MAX_SIZE
from pred_platform.data.repository import DataRepository

router = APIRouter()


_DRAFT_PATH = step_href(NEW, "datos")


@router.get("/datos")
def datos(request: Request) -> RedirectResponse:
    return redirect_with_query(_DRAFT_PATH, request.query_params.multi_items())


@router.get("/corridas/{corrida}/datos", response_class=HTMLResponse)
def paso_datos(
    request: Request,
    corrida: str,
    repository: DataRepository = Depends(get_repository),  # noqa: B008
) -> HTMLResponse:
    if corrida != NEW:
        facts = run_facts(repository, corrida)
        if facts is None:
            return render_missing_run(request, corrida)
        if facts.ingest_id is None:
            return render_locked(
                request,
                Locked(
                    title="Datos",
                    heading="Esta corrida no registró su archivo de datos",
                    text="Los pasos siguientes muestran lo que sí quedó guardado.",
                    actions=(("Ir a Ajustes", step_href(facts.id, "ajustes"), True),),
                ),
                run_wizard("datos", facts),
            )
        return _render_page(
            request,
            repository,
            ingest_id=facts.ingest_id,
            wizard=run_wizard("datos", facts),
            record=True,
        )
    params = request.query_params
    # Sin elegir otro archivo, el paso muestra el que ya eligió el borrador.
    ingest_id = params.get("ingest_id") or (
        None if params.get("pendiente") else read_draft(request).ingest_id
    )
    return _render_page(request, repository, ingest_id=ingest_id)


def _whole_map(query: TopologyQuery, page: TopologyReport) -> bool:
    """Si la página de la tabla trae todos los SKU en el orden de la lectura completa del mapa."""
    return (
        not query.sku_class
        and not query.q
        and query.sort == "sku_id"
        and not query.descending
        and page.page.number == 1
        and page.page.total <= page.page.size
    )


def _runnable(repository: DataRepository, ingest_id: str) -> bool:
    """Si la corrida nueva puede usar este archivo: aceptado y de datos reales."""
    return any(
        i.item.ingest_id == ingest_id and not i.synthetic for i in accepted_ingests(repository)
    )


@router.post("/corridas/nueva/datos")
def elegir_datos(
    request: Request,
    ingest_id: Annotated[str | None, Form()] = None,
    mostrar: Annotated[bool, Form()] = False,
    repository: DataRepository = Depends(get_repository),  # noqa: B008
) -> RedirectResponse:
    """«Continuar a Ajustes»: guarda el archivo elegido en el borrador. Solo un archivo aceptado
    de datos reales se puede elegir; otro vuelve a Datos para mostrar por qué no."""
    if ingest_id is None or not _runnable(repository, ingest_id):
        query = f"?{urlencode({'ingest_id': ingest_id})}" if ingest_id else ""
        return RedirectResponse(f"{_DRAFT_PATH}{query}", status_code=303)
    target = (
        f"{_DRAFT_PATH}?{urlencode({'ingest_id': ingest_id})}"
        if mostrar
        else step_href(NEW, "ajustes")
    )
    response = RedirectResponse(target, status_code=303)
    save_draft(
        request, response, next_revision(request, read_draft(request).with_ingest(ingest_id))
    )
    return response


@router.post("/datos/cancelar")
def cancelar(request: Request, pendiente: Annotated[str | None, Form()] = None) -> RedirectResponse:
    """Descarta la carga que esperaba confirmación; sus bytes se borran en el acto."""
    if pendiente:
        discard_pending(request, pendiente)
    return RedirectResponse(url=_DRAFT_PATH, status_code=303)


@router.post("/datos/cargar", response_model=None)
def cargar(
    request: Request,
    repository: DataRepository = Depends(get_repository),  # noqa: B008
    archivo: Annotated[UploadFile | None, File()] = None,
    pendiente: Annotated[str | None, Form()] = None,
    confirm_overwrite: Annotated[bool, Form()] = False,
) -> HTMLResponse | RedirectResponse:
    """Delega la carga en DataRepository.submit_ingest. Nunca valida el CSV en la vista."""
    if upload_blocked(request) is not None:
        if pendiente:
            discard_pending(request, pendiente)
        return _render_page(request, repository)  # la página dice por qué no se carga
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
        if upload_blocked(request) is not None:
            # The database became read-only while the engine ran, so its report could not be
            # saved: the page gives that reason. The report stays under the data root and is
            # imported at the next start with a writable database.
            return _render_page(request, repository)
        return _render_page(request, repository, layer_error=error_view(exc))
    except InvalidQuery as exc:
        return _render_page(request, repository, form_error=str(exc))

    if report.status == "needs_confirmation":
        token = store_pending(
            request, PendingUpload(file_name=file_name, content=content, report=report)
        )
        return RedirectResponse(
            url=f"{_DRAFT_PATH}?{urlencode({'pendiente': token})}",
            status_code=303,
        )
    remember_report(request, report)
    response = RedirectResponse(
        url=f"{_DRAFT_PATH}?{urlencode({'ingest_id': report.ingest_id})}",
        status_code=303,
    )
    # Un archivo cargado y aceptado queda elegido para la corrida nueva.
    if report.ingest_id and _runnable(repository, report.ingest_id):
        save_draft(
            request,
            response,
            next_revision(request, read_draft(request).with_ingest(report.ingest_id)),
        )
    return response


def _render_page(
    request: Request,
    repository: DataRepository,
    *,
    ingest_id: str | None = None,
    wizard: Wizard | None = None,
    record: bool = False,
    form_error: str | None = None,
    layer_error: dict[str, object] | None = None,
) -> HTMLResponse:
    """El paso Datos. ``ingest_id`` es el archivo que se muestra; ``record``, el registro de una
    corrida lanzada (sin cargar ni elegir otro archivo). Sin ``wizard``, el de la corrida nueva."""
    params = dict(request.query_params)
    path = request.url.path if request.method == "GET" else _DRAFT_PATH
    listing = None
    list_error = None
    history_query_error = None
    history_page = 1
    try:
        history_page = int(params.get("history_page") or "1")
        if not 1 <= history_page <= MAX_PAGE:
            raise ValueError
    except ValueError:
        history_query_error = (
            f"La página del historial «{params.get('history_page')}» no existe; "
            "se muestra la primera."
        )
        history_page = 1
    try:
        listing = repository.list_ingests(page=history_page, size=20)
    except DataLayerError as exc:
        list_error = error_view(exc)

    pending_token = None if record else params.get("pendiente")
    report: IngestReport | None = None
    report_error = layer_error
    topology: TopologyReport | None = None
    topology_error = None
    overview_error = None
    topology_query = None
    topology_pages = None
    chart_svg = None
    summary_chart_svg = None
    query_error = history_query_error

    if pending_token:
        report = peek_pending(request, pending_token)
        if report is None and report_error is None:
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
            topology_pages = page_meta(topology.page)
            if topology.availability.status == "available":
                chart_svg = topology_scatter_svg(topology)
                try:
                    # La página pedida ya es el mapa completo cuando no filtra, está en el orden
                    # de la lectura completa y cabe en una página: no se lee otra vez.
                    whole = (
                        topology
                        if _whole_map(topology_query, topology)
                        else repository.get_topology_report(report.ingest_id, size=MAX_SIZE)
                    )
                    items = list(whole.items)
                    for page in range(2, (whole.page.total + MAX_SIZE - 1) // MAX_SIZE + 1):
                        items.extend(
                            repository.get_topology_report(
                                report.ingest_id, size=MAX_SIZE, page=page
                            ).items
                        )
                    summary_chart_svg = demand_scatter_svg(
                        whole.model_copy(update={"items": items})
                    )
                except DataLayerError as exc:
                    overview_error = error_view(exc)
                except InvalidQuery as exc:
                    query_error = str(exc)
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
    synthetic_ids = (
        repository.synthetic_ingests([item.ingest_id for item in listing.items if item.ingest_id])
        if listing is not None
        else frozenset()
    )

    can_continue = (
        not record
        and topology_ready(topology)
        and report is not None
        and report.status == "accepted"
        and report.ingest_id not in repository.synthetic_ingests([report.ingest_id])
    )
    if wizard is None:
        wizard = draft_wizard(
            "datos",
            draft_facts(repository, read_draft(request)),
            can_continue=can_continue,
            chosen=report.ingest_id if report is not None else None,
        )
    context = {
        "wizard": wizard,
        "record": record,
        "datos_path": path,
        "capabilities": repository.get_capabilities(),
        "listing": listing,
        "list_error": list_error,
        "synthetic_ids": synthetic_ids,
        "history_pages": page_meta(listing.page) if listing else None,
        "history_reset_href": f"{path}?"
        + urlencode({key: value for key, value in params.items() if key != "history_page"})
        + "#historial",
        "history_prev_href": f"{path}?"
        + urlencode({**params, "history_page": history_page - 1})
        + "#historial",
        "history_next_href": f"{path}?"
        + urlencode({**params, "history_page": history_page + 1})
        + "#historial",
        "report": report,
        "report_error": report_error,
        "form_error": form_error,
        "query_error": query_error,
        "pending_token": pending_token
        if report and report.status == "needs_confirmation"
        else None,
        "topology": topology,
        "topology_error": topology_error,
        "overview_error": overview_error,
        "topology_query": topology_query,
        "topology_pages": topology_pages,
        "chart_svg": chart_svg,
        "summary_chart_svg": summary_chart_svg,
        "families_hint": dict(families_hint()),
        "can_continue": can_continue,
        "source": source,
        "csv_hint": CSV_HINT,
        "csv_columns": CSV_COLUMNS,
        "upload_blocked": upload_blocked(request),
    }
    return request.app.state.templates.TemplateResponse(
        request,
        "pages/data.html",
        context,
    )
