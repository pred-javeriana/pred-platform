"""FastAPI application factory for the PRED platform."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

_TEMPLATES_DIR = Path(__file__).parent / "templates"


def create_app() -> FastAPI:
    """Build and return the configured FastAPI application."""
    application = FastAPI(title="PRED", docs_url=None, redoc_url=None)
    templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

    @application.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @application.get("/", response_class=HTMLResponse)
    def index(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(request, "index.html")

    return application


app = create_app()
