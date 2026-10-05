"""FastAPI application factory for the PRED platform."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from pred_platform.config import Settings
from pred_platform.dal.migrate import migrate
from pred_platform.data.factory import build_repository

_APP_DIR = Path(__file__).parent
_TEMPLATES_DIR = _APP_DIR / "templates"
_STATIC_DIR = _APP_DIR / "static"


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build and return the configured FastAPI application."""
    active = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        # Runs when the server starts, not when the module is imported. Only the real DAL has a
        # schema to keep up to date; fixtures never touch the disk (ADR-05-002).
        if active.data_source == "dal":
            migrate(active.db_path)
        yield

    application = FastAPI(title="PRED", docs_url=None, redoc_url=None, lifespan=lifespan)
    application.state.settings = active
    # Which data the views read (real DAL or fixtures) is decided here, from the settings only.
    application.state.repository = build_repository(active)
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
