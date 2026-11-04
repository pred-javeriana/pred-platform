"""Superficie Resultados: selección por SKU (C2). La interfaz no elige campeones.

Dos páginas sobre ``DataRepository``: las pestañas de ``/resultados`` y el detalle de un SKU
(``/resultados/sku/{sku_id}``). Filtros, orden y paginación se delegan al repositorio; aquí solo se
arma la consulta y se presentan los documentos tal como llegan. La selección es la de M2; el
campeón y el veredicto de cada SKU son los que publicó M3 en una corrida exacta
(``resultados_m3``), y los enlaces conservan esa corrida resuelta.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from pred_platform.app.charts import champion_forecast_svg, champion_share_svg
from pred_platform.app.consulta import ConsultaQuery, parse_consulta_query
from pred_platform.app.datos import error_view, page_meta
from pred_platform.app.resultados_m3 import (
    CLASS_FAMILIES,
    MOTIVOS,
    PROFILE_METRICS,
    M3Classes,
    M3Consulta,
    M3Decision,
    M3Profiles,
    M3Summary,
    champion_bars,
    classes,
    consulta,
    decision,
    profiles,
    published_executions,
    reserve_windows,
    summary,
)
from pred_platform.app.resultados_pronostico import champion_forecast
from pred_platform.app.seleccion import (
    FAMILY_ORDER,
    SelectionQuery,
    family_label,
    parse_selection_query,
    run_action,
)
from pred_platform.contract import models_v2
from pred_platform.contract.errors import ArtifactMissing, DataLayerError, InvalidQuery
from pred_platform.contract.models import RunStatus, SkuSelection, SkuSelectionList
from pred_platform.data.deps import get_repository
from pred_platform.data.query import LATEST
from pred_platform.data.repository import DataRepository

router = APIRouter()

# Las pestañas, en orden de lectura: primero la vista general (Shneiderman: «overview first»),
# después el detalle. «consulta» es el nombre anterior de la lista de veredictos.
VIEWS = ("resumen", "clase", "errores", "sku")
# Las dos lecturas de la pestaña SKU: lo que M3 decidió por SKU y lo que M2 dejó por familia.
LISTS = ("veredictos", "familias")


def _base_context(repository: DataRepository, vista: str) -> dict[str, Any]:
    capabilities = repository.get_capabilities()
    champion = next(item for item in capabilities.items if item.id == "champion_selection")
    return {
        "active_surface": "resultados",
        "page_title": "Resultados",
        "capabilities": capabilities,
        "vista": vista,
        "action": run_action(capabilities),
        "champion_capability": champion,
        "source": repository.source,
    }


def _run_header(repository: DataRepository, reference: str) -> RunStatus | None:
    """El encabezado de la ejecución mostrada; sin él la página sigue siendo válida."""
    try:
        status = repository.get_run_status(reference, size=1)
    except (DataLayerError, InvalidQuery):
        return None
    return status if status.ejecucion is not None else None


def _vista(params: dict[str, str], evidence: bool) -> str:
    """La pestaña pedida; sin pedirla, el resumen si hay evidencia M3 publicada y, si no, la
    lista de SKU, que ya muestra lo que dejó M2."""
    wanted = params.get("vista") or ""
    if wanted == "consulta":
        return "sku"
    if wanted in VIEWS:
        return wanted
    return "resumen" if evidence else "sku"


def _lista(params: dict[str, str], champion: bool, evidence: bool) -> str:
    wanted = params.get("lista") or ""
    if wanted in LISTS:
        return wanted if champion else "familias"
    # Los filtros de familia y de estado son de la lista de M2: un enlace con ellos la abre.
    if not evidence or params.get("family") or params.get("estado"):
        return "familias"
    return "veredictos"


@router.get("/resultados", response_class=HTMLResponse)
def resultados(
    request: Request,
    repository: DataRepository = Depends(get_repository),  # noqa: B008
) -> HTMLResponse:
    params = dict(request.query_params)
    probe = _base_context(repository, "")
    champion = probe["champion_capability"].available
    # La ejecución y la corrida M3 se leen aparte de los filtros: un page o un size inválido
    # nunca hace perder qué ejecución se estaba mirando. Sin pedir una, la más nueva con
    # evidencia M3 publicada.
    executions = published_executions(repository) if champion else []
    explicit = (params.get("ejecucion") or "").strip()
    reference = explicit or (executions[0].ejecucion_id if executions else LATEST)
    evidence = champion and any(e.ejecucion_id == reference for e in executions)
    vista = _vista(params, evidence)
    lista = _lista(params, champion, evidence) if vista == "sku" else None
    context = {**probe, "vista": vista, "lista": lista}
    wanted_m3 = (params.get("m3") or "").strip() or None
    query_error: str | None = None
    try:
        query = replace(parse_selection_query(params), ejecucion=reference, m3=wanted_m3)
    except InvalidQuery as exc:
        query, query_error = SelectionQuery(ejecucion=reference, m3=wanted_m3), str(exc)
    run = _run_header(repository, reference)
    context.update(
        query=query,
        query_error=query_error,
        listing=None,
        list_error=None,
        rows=[],
        pages=None,
        run=run,
        executions=executions,
        reference=reference,
        missing_execution=explicit if run is None and explicit else None,
        m3=M3Decision(),
        m3_summary=M3Summary(),
        m3_profiles=M3Profiles(),
        m3_classes=M3Classes(),
        champion_svg="",
        m3_consulta=M3Consulta(),
        cq=ConsultaQuery(ejecucion=reference, m3=wanted_m3),
        consulta_error=None,
        consulta_pages=None,
        metric_hrefs={},
        profile_metrics=PROFILE_METRICS,
        class_families=CLASS_FAMILIES,
        class_headers=[family_label(family) for family in CLASS_FAMILIES],
        motivos=MOTIVOS,
        m3_hrefs={},
        tab_query=urlencode({"ejecucion": reference}) if reference != LATEST else "",
    )
    if run is None or run.ejecucion is None:
        return _render(request, context)
    if vista == "sku" and lista == "familias":
        _fill_list(repository, query, context)
    elif vista == "sku":
        _fill_consulta(repository, params, context)
    elif champion:
        _fill_m3_view(repository, run.ejecucion.id, vista, params, context)
    resolved = context["m3"].run
    if resolved is not None:
        # Las pestañas conservan la corrida M3 resuelta, no «la más nueva».
        context["tab_query"] = urlencode(
            {
                **({"ejecucion": reference} if reference != LATEST else {}),
                "m3": resolved.run_id,
            }
        )
    return _render(request, context)


def _render(request: Request, context: dict[str, Any]) -> HTMLResponse:
    return request.app.state.templates.TemplateResponse(request, "pages/results.html", context)


def _fill_m3_view(
    repository: DataRepository,
    ejecucion_id: str,
    vista: str,
    params: dict[str, str],
    context: dict[str, Any],
) -> None:
    """Resumen, Por clase y Perfiles de error: la misma corrida M3 resuelta para la ejecución."""
    query: SelectionQuery = context["query"]
    base = {"vista": vista, "ejecucion": context["reference"]}
    resolved: models_v2.M3Run | None
    if vista == "resumen":
        context["m3_summary"] = found_summary = summary(repository, ejecucion_id, query.m3)
        resolved = found_summary.run
        if found_summary.champions is not None:
            context["champion_svg"] = champion_share_svg(
                champion_bars(found_summary.champions),
                found_summary.champions.published_total,
            )
    elif vista == "clase":
        context["m3_classes"] = found_classes = classes(repository, ejecucion_id, query.m3)
        resolved = found_classes.run
    else:
        context["m3_profiles"] = found_profiles = profiles(
            repository, ejecucion_id, query.m3, params.get("metrica") or "razon_sn"
        )
        resolved = found_profiles.run
        metric_base = {**base, **({"m3": resolved.run_id} if resolved else {})}
        context["metric_hrefs"] = {
            name: "/resultados?" + urlencode({**metric_base, "metrica": name})
            for name in PROFILE_METRICS
        }
    if resolved is not None:
        # El encabezado muestra la misma corrida M3 que la pestaña; la causa de un problema la
        # explica solo la pestaña.
        others = tuple(r for r in resolved.publicadas_misma_identidad if r != resolved.run_id)
        context["m3"] = M3Decision(run=resolved, others=others)
        context["m3_hrefs"] = {
            other: "/resultados?" + urlencode({**base, "m3": other}) for other in others
        }


def _fill_consulta(
    repository: DataRepository, params: dict[str, str], context: dict[str, Any]
) -> None:
    """La lista de veredictos: lo que M3 publicó por SKU, filtrado y paginado por el repositorio."""
    reference, wanted_m3 = context["reference"], context["query"].m3
    try:
        cq = replace(parse_consulta_query(params), ejecucion=reference, m3=wanted_m3)
    except InvalidQuery as exc:
        cq, context["consulta_error"] = ConsultaQuery(ejecucion=reference, m3=wanted_m3), str(exc)
    context["cq"] = cq
    run = context["run"]
    found = consulta(repository, run.ejecucion.id, cq)
    context["m3_consulta"] = found
    if found.run is not None:
        # Filtros, páginas y detalle conservan la corrida M3 resuelta, no «la más nueva».
        context["cq"] = cq = replace(cq, m3=found.run.run_id)
        others = tuple(r for r in found.run.publicadas_misma_identidad if r != found.run.run_id)
        context["m3"] = M3Decision(run=found.run, others=others)
        context["m3_hrefs"] = {
            other: "/resultados?" + cq.query_string(m3=other, page="") for other in others
        }
    if found.evidence is not None:
        context["consulta_pages"] = page_meta(found.evidence.page)


def _fill_list(repository: DataRepository, query: SelectionQuery, context: dict[str, Any]) -> None:
    """La lista de M2: el estado y el valor de cada familia por SKU, con el veredicto de M3."""
    listing: SkuSelectionList | None = None
    try:
        listing = repository.list_sku_selections(query.ejecucion, **query.as_kwargs())
    except InvalidQuery as exc:
        context["query_error"] = str(exc)
    except DataLayerError as exc:
        context["list_error"] = error_view(exc)
    if listing is None:
        return
    context["listing"] = listing
    context["pages"] = page_meta(listing.page)
    if listing.availability.status == "available":
        m3 = decision(repository, listing.run_id, [row.sku_id for row in listing.items], query.m3)
        context["m3"] = m3
        if m3.run is not None:
            # Filtros, páginas y detalle conservan la corrida M3 resuelta, no «la más nueva».
            context["query"] = replace(query, m3=m3.run.run_id)
        context["m3_hrefs"] = {
            other: "/resultados?" + query.query_string(m3=other, page="") for other in m3.others
        }
    # Una celda por familia, en el orden del contrato; una familia que el documento no trae es «—».
    context["rows"] = [
        {"row": row, "cells": {brief.family: brief for brief in row.families}}
        for row in listing.items
    ]


@router.get("/resultados/sku/{sku_id}", response_class=HTMLResponse)
def sku_detalle(
    request: Request,
    sku_id: str,
    repository: DataRepository = Depends(get_repository),  # noqa: B008
) -> HTMLResponse:
    params = dict(request.query_params)
    reference = (params.get("ejecucion") or "").strip() or LATEST
    wanted_m3 = (params.get("m3") or "").strip() or None
    with_windows = params.get("ventanas") == "1"
    back = params.get("volver", "")
    context = _base_context(repository, "sku")
    status = 200
    selection: SkuSelection | None = None
    detail_error: dict[str, Any] | None = None
    try:
        selection = repository.get_sku_selection(reference, sku_id, include_windows=with_windows)
    except ArtifactMissing as exc:
        detail_error, status = error_view(exc), 404
    except InvalidQuery as exc:
        detail_error = {
            "title": "La consulta no es válida.",
            "detail": str(exc),
            "code": "platform.invalid_query",
            "blocked_by": [],
        }
        status = 400
    except DataLayerError as exc:
        detail_error = error_view(exc)
    m3 = (
        decision(repository, selection.run_id, [sku_id], wanted_m3)
        if selection is not None
        else M3Decision()
    )
    interval = None
    interval_error = False
    if m3.run is not None and sku_id in m3.verdicts:
        try:
            intervals = repository.get_m3_intervals(m3.run.run_id, [sku_id])
            interval = next((row for row in intervals if row.sku == sku_id), None)
        except DataLayerError:
            interval_error = True
    # Los enlaces de esta página conservan la corrida M3 resuelta.
    forecast = champion_forecast(repository, m3, sku_id)
    resolved_m3 = m3.run.run_id if m3.run is not None else wanted_m3
    scope = {
        k: v
        for k, v in (
            ("ejecucion", reference if reference != LATEST else ""),
            ("m3", resolved_m3 or ""),
        )
        if v
    }
    base = {**scope, **({"volver": back} if back else {})}
    context.update(
        sku_id=sku_id,
        selection=selection,
        detail_error=detail_error,
        not_found=status == 404,
        with_windows=with_windows,
        # La ruta de migas: Resultados (el resumen de la misma corrida) y la lista de la que se
        # llegó, con sus filtros; sin «volver», la lista de veredictos de la misma corrida.
        results_href="/resultados" + (f"?{urlencode(scope)}" if scope else ""),
        list_href="/resultados?" + (back or urlencode({"vista": "sku", **scope})),
        executions=[],
        lista=None,
        windows_href=f"/resultados/sku/{sku_id}?{urlencode({**base, 'ventanas': 1})}",
        reserve_href=f"/resultados/sku/{sku_id}?{urlencode({**base, 'reserva': 1})}",
        hide_windows_href=f"/resultados/sku/{sku_id}" + (f"?{urlencode(base)}" if base else ""),
        family_order=FAMILY_ORDER,
        run=None,
        m3=m3,
        m3_hrefs={
            other: f"/resultados/sku/{sku_id}?{urlencode({**base, 'm3': other})}"
            for other in m3.others
        },
        verdict=m3.verdicts.get(sku_id),
        interval=interval,
        interval_error=interval_error,
        reserve=None,
        forecast=forecast,
        forecast_svg=champion_forecast_svg(forecast),
    )
    if params.get("reserva") == "1" and m3.run is not None and sku_id in m3.verdicts:
        context["reserve"] = reserve_windows(repository, m3.run.run_id, m3.verdicts[sku_id])
    if selection is not None:
        context["run"] = _run_header(repository, selection.run_id or reference)
        context["entries"] = sorted(
            selection.families,
            key=lambda f: FAMILY_ORDER.index(f.family) if f.family in FAMILY_ORDER else 99,
        )
    return request.app.state.templates.TemplateResponse(
        request, "pages/results_sku.html", context, status_code=status
    )
