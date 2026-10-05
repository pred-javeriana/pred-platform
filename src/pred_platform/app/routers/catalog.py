"""Catálogo interno de B2. Fuera de la navegación de producto."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from pred_platform.app.presentation import describe_status, format_count

router = APIRouter()


@router.get("/_dev/componentes", response_class=HTMLResponse)
def catalogo(request: Request) -> HTMLResponse:
    return request.app.state.templates.TemplateResponse(
        request,
        "pages/catalog.html",
        {
            "active_surface": None,
            "task_statuses": [
                describe_status("task", code)
                for code in ("pendiente", "ejecutando", "exitosa", "fallida", "no_ejecutable")
            ],
            "trial_statuses": [
                describe_status("trial", code)
                for code in ("pendiente", "corriendo", "completado", "podado", "fallido")
            ],
            "verdict_statuses": [
                describe_status("verdict", code) for code in ("mantiene", "parcial", "falla")
            ],
            "progress_completed": format_count(821),
            "progress_total": format_count(1284),
            "progress_percent": 64,
            "metric_items": [
                {"label": "SKU", "value": "110", "tone": "data"},
                {"label": "Intermitentes", "value": "98", "tone": None},
                {"label": "Lumpy", "value": "12", "tone": None},
                {"label": "Candidatos", "value": "203", "tone": None},
            ],
            "tab_items": [
                {"id": "resumen", "label": "Resumen", "href": "#resumen"},
                {"id": "clase", "label": "Por clase", "href": "#clase"},
                {"id": "sku", "label": "Por SKU", "href": "#sku"},
            ],
        },
    )
