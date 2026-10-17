"""Superficie Resultados: selección por SKU (C2). La interfaz no elige campeones.

Dos páginas sobre ``DataRepository``: la tabla «Por SKU» (``/resultados``) y el detalle de un SKU
(``/resultados/sku/{sku_id}``). Filtros, orden y paginación se delegan al repositorio; aquí solo se
arma la consulta y se presentan los documentos tal como llegan.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from pred_platform.app.datos import error_view, page_meta
from pred_platform.app.seleccion import (
    FAMILY_ORDER,
    SelectionQuery,
    parse_selection_query,
    run_action,
)
from pred_platform.contract.errors import ArtifactMissing, DataLayerError, InvalidQuery
from pred_platform.contract.models import RunStatus, SkuSelection, SkuSelectionList
from pred_platform.data.deps import get_repository
from pred_platform.data.query import LATEST
from pred_platform.data.repository import DataRepository

router = APIRouter()

VIEWS = ("sku", "resumen", "clase")


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
        tab_query=urlencode({"ejecucion": query.ejecucion}) if query.ejecucion != LATEST else "",
    )
    if vista == "sku" or vista is None:
        _fill_list(repository, query, context)
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
    with_windows = params.get("ventanas") == "1"
    back = params.get("volver", "")
    context = _base_context(repository, "sku")
    base = {
        k: v
        for k, v in (("ejecucion", reference if reference != LATEST else ""), ("volver", back))
        if v
    }
    context.update(
        sku_id=sku_id,
        selection=None,
        detail_error=None,
        not_found=False,
        with_windows=with_windows,
        back_href="/resultados" + (f"?{back}" if back else ""),
        windows_href=f"/resultados/sku/{sku_id}?{urlencode({**base, 'ventanas': 1})}",
        hide_windows_href=f"/resultados/sku/{sku_id}" + (f"?{urlencode(base)}" if base else ""),
        family_order=FAMILY_ORDER,
        run=None,
    )
    status = 200
    selection: SkuSelection | None = None
    try:
        selection = repository.get_sku_selection(reference, sku_id, include_windows=with_windows)
    except ArtifactMissing as exc:
        context["not_found"] = True
        context["detail_error"] = error_view(exc)
        status = 404
    except InvalidQuery as exc:
        context["detail_error"] = {
            "title": "La consulta no es válida.",
            "detail": str(exc),
            "code": "platform.invalid_query",
            "blocked_by": [],
        }
        status = 400
    except DataLayerError as exc:
        context["detail_error"] = error_view(exc)
    if selection is not None:
        context["selection"] = selection
        context["run"] = _run_header(repository, selection.run_id or reference)
        context["entries"] = sorted(
            selection.families,
            key=lambda f: FAMILY_ORDER.index(f.family) if f.family in FAMILY_ORDER else 99,
        )
    return request.app.state.templates.TemplateResponse(
        request, "pages/results_sku.html", context, status_code=status
    )
