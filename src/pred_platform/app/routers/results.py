"""Superficie Resultados: selección por SKU (C2). La interfaz no elige campeones.

Dos páginas sobre ``DataRepository``: la tabla «Por SKU» (``/resultados``) y el detalle de un SKU
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

from pred_platform.app.datos import error_view, page_meta
from pred_platform.app.resultados_m3 import (
    CLASS_FAMILIES,
    MOTIVOS,
    PROFILE_METRICS,
    M3Classes,
    M3Decision,
    M3Profiles,
    M3Summary,
    classes,
    decision,
    profiles,
    reserve_windows,
    summary,
)
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

VIEWS = ("sku", "resumen", "clase", "errores")


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


@router.get("/resultados", response_class=HTMLResponse)
def resultados(
    request: Request,
    repository: DataRepository = Depends(get_repository),  # noqa: B008
) -> HTMLResponse:
    params = dict(request.query_params)
    vista = params.get("vista") if params.get("vista") in VIEWS else "sku"
    context = _base_context(repository, vista or "sku")
    query_error: str | None = None
    try:
        query = parse_selection_query(params)
    except InvalidQuery as exc:
        query, query_error = SelectionQuery(), str(exc)
    context.update(
        query=query,
        query_error=query_error,
        listing=None,
        list_error=None,
        rows=[],
        pages=None,
        run=None,
        m3=M3Decision(),
        m3_summary=M3Summary(),
        m3_profiles=M3Profiles(),
        m3_classes=M3Classes(),
        metric_hrefs={},
        profile_metrics=PROFILE_METRICS,
        class_families=CLASS_FAMILIES,
        class_headers=[family_label(family) for family in CLASS_FAMILIES],
        motivos=MOTIVOS,
        m3_hrefs={},
        tab_query=urlencode({"ejecucion": query.ejecucion}) if query.ejecucion != LATEST else "",
    )
    if vista == "sku" or vista is None:
        _fill_list(repository, query, context)
    elif vista in ("resumen", "errores", "clase") and context["champion_capability"].available:
        context["run"] = run = _run_header(repository, query.ejecucion)
        if run is not None and run.ejecucion is not None:
            # Los enlaces de la pestaña conservan la ejecución y la corrida M3 resuelta.
            base = {"vista": vista}
            if query.ejecucion != LATEST:
                base["ejecucion"] = query.ejecucion
            resolved: models_v2.M3Run | None
            if vista == "resumen":
                context["m3_summary"] = found_summary = summary(
                    repository, run.ejecucion.id, query.m3
                )
                resolved = found_summary.run
            elif vista == "clase":
                context["m3_classes"] = found_classes = classes(
                    repository, run.ejecucion.id, query.m3
                )
                resolved = found_classes.run
            else:
                context["m3_profiles"] = found_profiles = profiles(
                    repository, run.ejecucion.id, query.m3, params.get("metrica") or "razon_sn"
                )
                resolved = found_profiles.run
                metric_base = {**base, **({"m3": resolved.run_id} if resolved else {})}
                context["metric_hrefs"] = {
                    name: "/resultados?" + urlencode({**metric_base, "metrica": name})
                    for name in PROFILE_METRICS
                }
            if resolved is not None:
                # El panel lateral muestra la misma corrida M3 que la pestaña (sin su causa,
                # que ya está en el área principal).
                others = tuple(
                    r for r in resolved.publicadas_misma_identidad if r != resolved.run_id
                )
                context["m3"] = M3Decision(run=resolved, others=others)
                context["m3_hrefs"] = {
                    other: "/resultados?" + urlencode({**base, "m3": other}) for other in others
                }
    return request.app.state.templates.TemplateResponse(request, "pages/results.html", context)


def _fill_list(repository: DataRepository, query: SelectionQuery, context: dict[str, Any]) -> None:
    listing: SkuSelectionList | None = None
    try:
        listing = repository.list_sku_selections(query.ejecucion, **query.as_kwargs())
    except InvalidQuery as exc:
        context["query_error"] = str(exc)
    except DataLayerError as exc:
        context["list_error"] = error_view(exc)
    context["run"] = _run_header(repository, query.ejecucion)
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
    # Los enlaces de esta página conservan la corrida M3 resuelta.
    resolved_m3 = m3.run.run_id if m3.run is not None else wanted_m3
    base = {
        k: v
        for k, v in (
            ("ejecucion", reference if reference != LATEST else ""),
            ("m3", resolved_m3 or ""),
            ("volver", back),
        )
        if v
    }
    context.update(
        sku_id=sku_id,
        selection=selection,
        detail_error=detail_error,
        not_found=status == 404,
        with_windows=with_windows,
        back_href="/resultados" + (f"?{back}" if back else ""),
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
        reserve=None,
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
