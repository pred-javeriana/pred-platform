"""El manual de usuario (``/manual``): una página aparte con la guía de quien opera PRED."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

router = APIRouter()


@router.get("/manual", response_class=HTMLResponse)
def manual(request: Request) -> HTMLResponse:
    return request.app.state.templates.TemplateResponse(  # type: ignore[no-any-return]
        request,
        "pages/manual.html",
        {"page_title": "Manual de usuario", "at_manual": True},
    )
