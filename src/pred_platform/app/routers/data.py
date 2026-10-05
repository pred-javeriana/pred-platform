"""Superficie Datos: frontera de página. El flujo C1/C3 se implementa después."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from pred_platform.data.deps import get_repository
from pred_platform.data.repository import DataRepository

router = APIRouter()


@router.get("/datos", response_class=HTMLResponse)
def datos(
    request: Request,
    repository: DataRepository = Depends(get_repository),  # noqa: B008
) -> HTMLResponse:
    capabilities = repository.get_capabilities()
    return request.app.state.templates.TemplateResponse(
        request,
        "pages/data.html",
        {
            "active_surface": "datos",
            "page_title": "Datos",
            "capabilities": capabilities,
        },
    )
