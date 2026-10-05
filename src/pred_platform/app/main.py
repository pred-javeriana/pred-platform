"""Fábrica FastAPI de la plataforma PRED."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from pred_platform.app.routers import catalog, data, execution, results
from pred_platform.app.templating import build_templates
from pred_platform.config import Settings
from pred_platform.dal.migrate import migrate
from pred_platform.data.factory import build_repository

_APP_DIR = Path(__file__).parent
_TEMPLATES_DIR = _APP_DIR / "templates"
_STATIC_DIR = _APP_DIR / "static"


def create_app(settings: Settings | None = None) -> FastAPI:
    """Construye y devuelve la aplicación configurada."""
    active = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        # Solo el DAL real tiene esquema que mantener; los fixtures no tocan el disco.
        if active.data_source == "dal":
            migrate(active.db_path)
        yield

    application = FastAPI(title="PRED", docs_url=None, redoc_url=None, lifespan=lifespan)
    application.state.settings = active
    application.state.repository = build_repository(active)
    application.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")
    application.state.templates = build_templates(
        _TEMPLATES_DIR,
        lambda path: application.url_path_for("static", path=path),
    )

    @application.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @application.get("/")
    def index() -> RedirectResponse:
        return RedirectResponse(url="/datos", status_code=307)

    application.include_router(data.router)
    application.include_router(execution.router)
    application.include_router(results.router)
    application.include_router(catalog.router)
    return application


app = create_app()
