"""Ayudas de la superficie Datos: validación local, consulta de topología y pendientes.

La autoridad de la ingesta sigue siendo DataRepository. Aquí solo se traducen
errores de interacción y se arma la consulta server-side del reporte.
"""

from __future__ import annotations

import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import IO, Any, cast
from urllib.parse import urlencode
from uuid import uuid4

from fastapi import Request
from starlette.datastructures import UploadFile

from pred_platform import mensajes
from pred_platform.contract import models_v2
from pred_platform.contract.errors import DataLayerError, InvalidQuery
from pred_platform.contract.models import IngestReport, Page, TopologyReport
from pred_platform.data.query import DEFAULT_SIZE, int_param
from pred_platform.data.repository import DataRepository
from pred_platform.fallas import texto_soporte
from pred_platform.worker import ingesta
from pred_platform.worker.solo_lectura import database_read_only

_SUBMITTED_ATTR = "submitted_reports"

# Lo que el motor exige de cada columna canónica del CSV (barrera de formato de la ingesta).
_COLUMN_RULES: dict[str, str] = {
    "sku_id": "Identificador del producto.",
    "timestamp": "Fecha ISO 8601 (AAAA-MM-DD); la hora se ignora.",
    "demand_qty": "Demanda del día: un número mayor o igual que 0.",
    "lead_time_days": "Tiempo de reposición: un entero de 1 día o más.",
}
CSV_COLUMNS: tuple[tuple[str, str], ...] = tuple(
    (name, _COLUMN_RULES[name]) for name in ingesta.CANONICAL_HEADERS
)
CSV_HINT = (
    f"Un archivo .csv de hasta {ingesta.MAX_BYTES // (1024 * 1024)} MiB, una fila por SKU y día."
)
READ_ONLY_UPLOAD = (
    "La base de datos es de solo lectura: puede consultar los archivos ya cargados, pero no "
    "cargar uno nuevo."
)


def upload_blocked(request: Request) -> str | None:
    """Por qué no se puede cargar un archivo ahora; ``None`` si se puede.

    La carga corre el motor y guarda su informe en la base: con una base de solo lectura el motor
    dejaría archivos en la raíz de datos y el informe no se podría guardar.
    """
    settings = request.app.state.settings
    if settings.data_source == "dal" and database_read_only(settings.db_path):
        return READ_ONLY_UPLOAD
    return None


@dataclass(frozen=True, slots=True)
class PendingUpload:
    """Archivo retenido solo mientras el contrato pide confirmación de sobrescritura."""

    file_name: str
    content: bytes
    report: IngestReport


@dataclass(slots=True)
class _Held:
    file_name: str
    report: IngestReport
    data: IO[bytes]
    stored_at: float


class PendingUploads:
    """Las cargas que esperan confirmación, en disco y por un tiempo acotado.

    Los bytes van a un archivo temporal sin nombre que el sistema borra al cerrarlo, también si el
    proceso se cae, así que la memoria no crece con cada carga sin confirmar. ``directory`` es
    dónde se crea (el ``staging`` de la raíz de datos: un /tmp en memoria no ahorraría nada); sin
    él, el temporal del sistema. Una carga vence a la hora y solo se conservan las ``limit`` más
    recientes; cancelar la borra en el acto.
    """

    def __init__(
        self,
        *,
        directory: Path | None = None,
        ttl: float = 3600.0,
        limit: int = 5,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._directory = directory
        self._ttl = ttl
        self._limit = limit
        self._clock = clock
        self._held: dict[str, _Held] = {}
        self._lock = threading.Lock()

    def put(self, pending: PendingUpload) -> str:
        """Guarda la carga y devuelve el token que la confirma o la cancela."""
        if self._directory is not None:
            self._directory.mkdir(parents=True, exist_ok=True)
        # Se cierra (y se borra) al confirmar, cancelar o vencer, no al salir de este bloque.
        data = tempfile.TemporaryFile(dir=self._directory)  # noqa: SIM115
        data.write(pending.content)
        token = uuid4().hex
        with self._lock:
            self._held[token] = _Held(pending.file_name, pending.report, data, self._clock())
            self._prune()
        return token

    def report(self, token: str) -> IngestReport | None:
        """El informe de una carga pendiente, sin leer sus bytes; ``None`` si venció."""
        with self._lock:
            self._prune()
            held = self._held.get(token)
            return None if held is None else held.report

    def pop(self, token: str) -> PendingUpload | None:
        """Saca la carga para confirmarla; ``None`` si venció o ya se usó."""
        with self._lock:
            self._prune()
            held = self._held.pop(token, None)
        if held is None:
            return None
        with held.data:
            held.data.seek(0)
            return PendingUpload(held.file_name, held.data.read(), held.report)

    def discard(self, token: str) -> None:
        """Borra la carga (Cancelar); un token desconocido no hace nada."""
        with self._lock:
            held = self._held.pop(token, None)
        if held is not None:
            held.data.close()

    def __len__(self) -> int:
        with self._lock:
            self._prune()
            return len(self._held)

    def _prune(self) -> None:
        now = self._clock()
        newest = sorted(self._held, key=lambda token: self._held[token].stored_at, reverse=True)
        for position, token in enumerate(newest):
            if position >= self._limit or now - self._held[token].stored_at > self._ttl:
                self._held.pop(token).data.close()


@dataclass(frozen=True, slots=True)
class TopologyQuery:
    """Parámetros que se delegan en get_topology_report, sin filtrar en el cliente."""

    sku_class: str | None = None
    q: str | None = None
    sort: str = "sku_id"
    descending: bool = False
    page: int = 1
    size: int = DEFAULT_SIZE

    def as_kwargs(self) -> dict[str, Any]:
        return {
            "sku_class": self.sku_class,
            "q": self.q,
            "sort": self.sort,
            "descending": self.descending,
            "page": self.page,
            "size": self.size,
        }

    def query_string(self, ingest_id: str, **overrides: object) -> str:
        """Query string para filtros y paginación, anclada a la ingesta seleccionada."""
        values: dict[str, object] = {
            "ingest_id": ingest_id,
            "sku_class": self.sku_class or "",
            "q": self.q or "",
            "sort": self.sort,
            "descending": "1" if self.descending else "",
            "page": self.page,
            "size": self.size,
        }
        values.update(overrides)
        params: list[tuple[str, str]] = []
        for key, raw in values.items():
            if raw in ("", None, False):
                continue
            params.append((key, "1" if raw is True else str(raw)))
        return urlencode(params)


def validate_upload(file_name: str | None, content: bytes) -> str | None:
    """Error de interacción local, o None si el archivo puede delegarse al repositorio."""
    name = (file_name or "").strip()
    if not name:
        return "Seleccione un archivo CSV."
    if not name.lower().endswith(".csv"):
        return "El archivo debe tener extensión .csv."
    if not content:
        return "El archivo está vacío."
    return None


def read_upload(upload: UploadFile | None) -> tuple[str, bytes]:
    """Nombre y contenido del archivo recibido; vacíos si no hubo selección.

    Lee a lo sumo un byte más que el límite de carga: ese byte basta para que la ingesta rechace
    el archivo sin llevar a memoria uno de varios gigabytes.
    """
    if upload is None:
        return "", b""
    name = (upload.filename or "").strip()
    content = upload.file.read(ingesta.MAX_BYTES + 1)
    if isinstance(content, str):
        content = content.encode("utf-8")
    return name, content


def parse_topology_query(params: dict[str, str]) -> TopologyQuery:
    """Lee filtros de la query HTTP y los entrega al repositorio sin refiltrar."""
    sku_class = params.get("sku_class") or None
    sort = params.get("sort") or "sku_id"
    q = params.get("q") or None
    descending = params.get("descending") in {"1", "true", "on"}
    return TopologyQuery(
        sku_class=sku_class,
        q=q,
        sort=sort,
        descending=descending,
        page=int_param(params.get("page"), 1, "page"),
        size=int_param(params.get("size"), DEFAULT_SIZE, "size"),
    )


def store_pending(request: Request, pending: PendingUpload) -> str:
    return _pending(request).put(pending)


def pop_pending(request: Request, token: str) -> PendingUpload | None:
    return _pending(request).pop(token)


def peek_pending(request: Request, token: str) -> IngestReport | None:
    return _pending(request).report(token)


def discard_pending(request: Request, token: str) -> None:
    _pending(request).discard(token)


def remember_report(request: Request, report: IngestReport) -> None:
    """Conserva el informe devuelto por submit_ingest para el redirect PRG.

    Los fixtures son stateless: en ``vacio`` no hay informes persistidos, pero
    submit_ingest sí devuelve uno. No se inventan datos; solo se retiene lo ya
    recibido del repositorio en este proceso.
    """
    _submitted_map(request)[report.ingest_id] = report


def recalled_report(request: Request, ingest_id: str) -> IngestReport | None:
    return _submitted_map(request).get(ingest_id)


def error_view(exc: DataLayerError) -> dict[str, object]:
    """Estado de interfaz a partir de un error normalizado. Nunca incluye traceback.

    ``detail`` es la frase visible: el aviso del error si lo trae (``notice``) o la acción del
    catálogo. El texto técnico va completo en ``support``, el detalle plegado para soporte.
    """
    info = exc.to_error_info()
    action = mensajes.accion(info.code)
    return {
        "code": info.code,
        "detail": exc.notice
        or action
        or "Vuelva a intentarlo; si persiste, copie el detalle para soporte.",
        "field": info.field,
        "column": info.column,
        "retryable": info.retryable,
        "blocked_by": list(exc.blocked_by),
        "title": _error_title(info.code),
        "action": action,
        "support": texto_soporte(
            info.code,
            etapa=info.stage,
            excepcion=info.source_exception,
            detalle=exc.detail,
            campo=info.field,
            bloqueos=list(exc.blocked_by),
        ),
    }


@dataclass(frozen=True, slots=True)
class IngestName:
    """Cómo se nombra una ingesta en pantalla: el archivo y su fecha; el hash queda de detalle."""

    ingest_id: str
    file_name: str | None
    published_at: datetime | None
    n_skus: int | None = None


def ingest_name(repository: DataRepository, ingest_id: str | None) -> IngestName | None:
    """El nombre del archivo de una ingesta, para mostrarlo en lugar de su hash SHA-256.

    Una lectura que falla o una ingesta que no existe dejan el hash como nombre.
    """
    if not ingest_id:
        return None
    try:
        found = repository.get_ingest(ingest_id)
    except (DataLayerError, InvalidQuery):
        found = None
    if found is None:
        return IngestName(ingest_id, None, None)
    return IngestName(ingest_id, found.name, found.published_at, found.n_skus)


def topology_ready(report: TopologyReport | None) -> bool:
    """La continuación a Ejecución exige reporte presente y disponible."""
    return report is not None and report.availability.status == "available"


def page_meta(page: Page | models_v2.Page) -> dict[str, object]:
    total_pages = max(1, (page.total + page.size - 1) // page.size)
    return {
        "total_pages": total_pages,
        "has_prev": page.number > 1,
        "has_next": page.number < total_pages,
        "show": page.total > page.size,
    }


def _pending(request: Request) -> PendingUploads:
    return cast(PendingUploads, request.app.state.pending_uploads)


def _submitted_map(request: Request) -> dict[str, IngestReport]:
    store = getattr(request.app.state, _SUBMITTED_ATTR, None)
    if store is None:
        store = {}
        setattr(request.app.state, _SUBMITTED_ATTR, store)
    return store


def _error_title(code: str) -> str:
    if code in mensajes.CATALOGO:
        return mensajes.titulo(code)
    return "No fue posible completar la operación."
