"""Superficie Ejecución: shell + fragmento HTMX. Sin polling ni worker."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from pred_platform.data.deps import get_repository
from pred_platform.data.repository import DataRepository

router = APIRouter()


def _context(repository: DataRepository) -> dict[str, object]:
    capabilities = repository.get_capabilities()
    orchestration = next(item for item in capabilities.items if item.id == "run_orchestration")
    return {
        "active_surface": "ejecucion",
        "page_title": "Ejecución",
        "capabilities": capabilities,
        "orchestration": orchestration,
    }


@router.get("/ejecucion", response_class=HTMLResponse)
def ejecucion(
    request: Request,
    repository: DataRepository = Depends(get_repository),  # noqa: B008
) -> HTMLResponse:
    return request.app.state.templates.TemplateResponse(
        request,
        "pages/execution.html",
        _context(repository),
    )


@router.get("/ejecucion/estado", response_class=HTMLResponse)
def ejecucion_estado(
    request: Request,
    repository: DataRepository = Depends(get_repository),  # noqa: B008
) -> HTMLResponse:
    # Fragmento para actualizaciones parciales. El cliente aún no hace polling (G3).
    return request.app.state.templates.TemplateResponse(
        request,
        "partials/execution_body.html",
        _context(repository),
    )
