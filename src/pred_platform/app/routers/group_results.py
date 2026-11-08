"""Public routes for the final seed group and its exact JSON export."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse

from pred_platform.app.resultados_grupo import group_context, group_selection, read_group
from pred_platform.data.deps import get_repository
from pred_platform.data.repository import DataRepository
from pred_platform.evidence.export import document
from pred_platform.evidence.models import EvidenceInvalid

router = APIRouter()


@router.get("/resultados/grupo", response_class=HTMLResponse)
def group_results(
    request: Request,
    repository: DataRepository = Depends(get_repository),  # noqa: B008
) -> HTMLResponse:
    return request.app.state.templates.TemplateResponse(
        request, "pages/group_results.html", group_context(repository, request.query_params)
    )


@router.get("/resultados/grupo.json")
def group_download(
    request: Request,
    repository: DataRepository = Depends(get_repository),  # noqa: B008
) -> JSONResponse:
    try:
        by_seed, selected = group_selection(request.query_params, repository.list_m3_group_runs())
        result = read_group(repository, by_seed, selected)
    except (EvidenceInvalid, ValueError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return JSONResponse(
        document(result),
        headers={"Content-Disposition": 'attachment; filename="grupo-semillas.json"'},
    )
