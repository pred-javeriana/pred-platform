"""Fábrica FastAPI de la plataforma PRED."""

import logging
import sqlite3
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from pred_platform.app.borrador import DraftStore
from pred_platform.app.datos import PendingUploads
from pred_platform.app.routers import (
    catalog,
    corridas,
    data,
    execution,
    exportar,
    group_results,
    results,
)
from pred_platform.app.templating import build_templates
from pred_platform.config import Settings
from pred_platform.dal.importacion import ReportImportError, import_reports
from pred_platform.dal.migrate import migrate
from pred_platform.data.factory import build_repository
from pred_platform.worker.bucle import Worker
from pred_platform.worker.control import build_run_control
from pred_platform.worker.ingesta import STAGING_DIR
from pred_platform.worker.solo_lectura import is_read_only_error

_logger = logging.getLogger("pred_platform.app")
_APP_DIR = Path(__file__).parent
_TEMPLATES_DIR = _APP_DIR / "templates"
_STATIC_DIR = _APP_DIR / "static"


class DatabaseUnavailable(RuntimeError):
    """The database file cannot be opened, so the application does not start."""


def _migrate(settings: Settings) -> None:
    try:
        migrate(settings.db_path)
    except sqlite3.OperationalError as exc:
        if settings.db_path.is_dir():
            raise DatabaseUnavailable(
                f"PRED_DB_PATH nombra la carpeta {settings.db_path}: indique el archivo de la "
                "base de datos, por ejemplo pred.db dentro de esa carpeta."
            ) from None
        if not is_read_only_error(exc):
            raise
        raise DatabaseUnavailable(
            f"No se puede abrir la base de datos {settings.db_path} ({exc}). Una copia de solo "
            "lectura en WAL necesita una carpeta con permiso de escritura; para una carpeta de "
            "solo lectura, pásela antes a PRAGMA journal_mode=DELETE (README.md, «Serving a "
            "read-only copy»)."
        ) from None


def _import_engine_reports(settings: Settings) -> None:
    """Importa al DAL los informes de ingesta que dejó el motor (G1).

    Un informe malo se registra y se omite: no debe impedir que la aplicación arranque.
    """
    try:
        result = import_reports(settings.db_path, settings.data_root)
    except ReportImportError as exc:
        _logger.warning("No se importaron los informes de ingesta: %s", exc)
        return
    for failure in result.failed:
        _logger.warning("Informe de ingesta omitido: %s", failure)
    if result.imported:
        _logger.info("Informes de ingesta importados: %d", result.imported)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Construye y devuelve la aplicación configurada."""
    active = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Solo el DAL real tiene esquema que mantener; los fixtures no tocan el disco.
        if active.data_source == "dal":
            _migrate(active)
            _import_engine_reports(active)
        stop = threading.Event()
        thread: threading.Thread | None = None
        if active.data_source == "dal" and active.worker == "embedded":
            # El worker corre como hilo de la app; el mismo código se puede correr aparte con
            # `python -m pred_platform.worker`.
            thread = threading.Thread(
                target=Worker(active).run_forever, args=(stop,), name="pred-worker", daemon=True
            )
            thread.start()
        # The pages ask this thread whether the embedded worker still follows the queue.
        app.state.worker_thread = thread
        try:
            yield
        finally:
            stop.set()
            if thread is not None:
                thread.join(timeout=15)

    application = FastAPI(title="PRED", docs_url=None, redoc_url=None, lifespan=lifespan)
    application.state.settings = active
    application.state.repository = build_repository(active)
    application.state.run_control = build_run_control(active)
    # El borrador de la corrida nueva se guarda por sesión del navegador, en la raíz de datos.
    application.state.drafts = DraftStore(
        active.data_root / "borradores" if active.data_source == "dal" else None
    )
    # Las cargas sin confirmar esperan en disco, junto a las demás cargas de la raíz de datos.
    application.state.pending_uploads = PendingUploads(
        directory=active.data_root / STAGING_DIR if active.data_source == "dal" else None
    )
    application.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")
    application.state.templates = build_templates(
        _TEMPLATES_DIR,
        lambda path: application.url_path_for("static", path=path),
    )

    # HEAD también: las sondas de disponibilidad suelen preguntar sin cuerpo.
    @application.api_route("/health", methods=["GET", "HEAD"])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    # Una dirección que no existe se lee como una página de PRED en un navegador; un cliente que
    # no pide HTML (una sonda, un script) sigue recibiendo el JSON de siempre.
    @application.exception_handler(StarletteHTTPException)
    async def not_found(request: Request, exc: StarletteHTTPException) -> Response:
        if exc.status_code == 404 and "text/html" in request.headers.get("accept", ""):
            return application.state.templates.TemplateResponse(
                request, "pages/not_found.html", {}, status_code=404
            )
        return await http_exception_handler(request, exc)

    application.include_router(corridas.router)
    application.include_router(data.router)
    application.include_router(execution.router)
    application.include_router(results.router)
    application.include_router(group_results.router)
    application.include_router(exportar.router)
    application.include_router(catalog.router)
    return application


app = create_app()
