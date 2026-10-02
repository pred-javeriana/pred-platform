"""FastAPI application factory for the PRED platform."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from pred_platform.config import Settings

_APP_DIR = Path(__file__).parent
_TEMPLATES_DIR = _APP_DIR / "templates"
_STATIC_DIR = _APP_DIR / "static"


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build and return the configured FastAPI application."""
    application = FastAPI(title="PRED", docs_url=None, redoc_url=None)
    application.state.settings = settings or Settings.from_env()
    application.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")
    templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))
    # Host-relative asset paths: Starlette's `url_for` builds absolute URLs from the request host.
    templates.env.globals["static_url"] = lambda path: application.url_path_for("static", path=path)

    @application.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @application.get("/", response_class=HTMLResponse)
    def index(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(request, "index.html")

    return application


app = create_app()
