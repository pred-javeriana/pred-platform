"""Small Results view over the final seed group, separate from single-run views."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse

from pred_platform.data.deps import get_repository
from pred_platform.data.repository import DataRepository
from pred_platform.evidence.export import document
from pred_platform.evidence.models import EvidenceInvalid, GroupRunOption

router = APIRouter()


def _selection(
    request: Request, options: tuple[GroupRunOption, ...]
) -> tuple[dict[int, list[GroupRunOption]], tuple[str, ...]]:
    by_seed = {seed: [r for r in options if r.seed == seed] for seed in range(3)}
    # Publication alone does not declare a run to be final thesis evidence.
    # Require explicit choices, even when only one publication exists per seed.
    selected = tuple(request.query_params.get(f"semilla{seed}", "").strip() for seed in range(3))
    return by_seed, selected


def _read(
    repository: DataRepository, by_seed: dict[int, list[GroupRunOption]], selected: tuple[str, ...]
):
    for seed, run_id in enumerate(selected):
        if run_id not in {r.run_id for r in by_seed[seed]}:
            raise EvidenceInvalid(f"Elija una evaluación publicada para la semilla {seed}.")
    return repository.get_m3_group(selected)


@router.get("/resultados/grupo", response_class=HTMLResponse)
def group_results(
    request: Request,
    repository: DataRepository = Depends(get_repository),  # noqa: B008
) -> HTMLResponse:
    context: dict[str, Any] = {
        "active_surface": "resultados",
        "page_title": "Grupo de semillas",
        "options": {seed: [] for seed in range(3)},
        "selected": ("", "", ""),
        "group": None,
        "problem": None,
        "product": None,
        "sku": request.query_params.get("sku", "").strip(),
        "download_url": "",
        "source": repository.source,
    }
    try:
        by_seed, selected = _selection(request, repository.list_m3_group_runs())
        context.update(options=by_seed, selected=selected)
        if all(selected):
            group = _read(repository, by_seed, selected)
            context["group"] = group
            context["download_url"] = "/resultados/grupo.json?" + urlencode(
                {f"semilla{seed}": r for seed, r in enumerate(selected)}
            )
            context["product"] = next(
                (p for p in group.significance.products if p.sku == context["sku"]), None
            )
        elif any(request.query_params.get(f"semilla{s}") for s in range(3)):
            context["problem"] = "Falta elegir una evaluación publicada para cada semilla."
    except (EvidenceInvalid, ValueError) as exc:
        context["problem"] = str(exc)
    return request.app.state.templates.TemplateResponse(
        request, "pages/group_results.html", context
    )


@router.get("/resultados/grupo.json")
def group_download(
    request: Request,
    repository: DataRepository = Depends(get_repository),  # noqa: B008
) -> JSONResponse:
    try:
        by_seed, selected = _selection(request, repository.list_m3_group_runs())
        result = _read(repository, by_seed, selected)
    except (EvidenceInvalid, ValueError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return JSONResponse(
        document(result),
        headers={
            "Content-Disposition": 'attachment; filename="grupo-semillas.json"',
        },
    )
