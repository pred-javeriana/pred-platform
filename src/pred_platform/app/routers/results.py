"""Superficie Resultados: frontera de página. No selecciona campeones."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from pred_platform.data.deps import get_repository
from pred_platform.data.repository import DataRepository

router = APIRouter()


@router.get("/resultados", response_class=HTMLResponse)
def resultados(
    request: Request,
    repository: DataRepository = Depends(get_repository),  # noqa: B008
) -> HTMLResponse:
    capabilities = repository.get_capabilities()
    return request.app.state.templates.TemplateResponse(
        request,
        "pages/results.html",
        {
            "active_surface": "resultados",
            "page_title": "Resultados",
            "capabilities": capabilities,
        },
    )
