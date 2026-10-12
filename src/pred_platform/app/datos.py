"""Ayudas de la superficie Datos: validación local, consulta de topología y pendientes.

La autoridad de la ingesta sigue siendo DataRepository. Aquí solo se traducen
errores de interacción y se arma la consulta server-side del reporte.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast
from urllib.parse import urlencode
from uuid import uuid4

from fastapi import Request
from starlette.datastructures import UploadFile

from pred_platform.app.presentation import SKU_CLASS_ORDER, format_count, sku_class_label
from pred_platform.contract.errors import DataLayerError, InvalidQuery
from pred_platform.contract.models import IngestReport, Page, SkuClass, TopologyReport
from pred_platform.data.query import DEFAULT_SIZE

_PENDING_ATTR = "pending_uploads"
_SUBMITTED_ATTR = "submitted_reports"


@dataclass(frozen=True, slots=True)
class PendingUpload:
    """Archivo retenido solo mientras el contrato pide confirmación de sobrescritura."""

    file_name: str
    content: bytes
    report: IngestReport


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
    """Nombre y contenido del archivo recibido; vacíos si no hubo selección."""
    if upload is None:
        return "", b""
    name = (upload.filename or "").strip()
    content = upload.file.read()
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
        page=_int_param(params.get("page"), 1, "page"),
        size=_int_param(params.get("size"), DEFAULT_SIZE, "size"),
    )


def store_pending(request: Request, pending: PendingUpload) -> str:
    token = uuid4().hex
    _pending_map(request)[token] = pending
    return token


def pop_pending(request: Request, token: str) -> PendingUpload | None:
    return _pending_map(request).pop(token, None)


def peek_pending(request: Request, token: str) -> PendingUpload | None:
    return _pending_map(request).get(token)


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
    """Estado de interfaz a partir de un error normalizado. Nunca incluye traceback."""
    info = exc.to_error_info()
    return {
        "code": info.code,
        "detail": info.detail,
        "field": info.field,
        "column": info.column,
        "retryable": info.retryable,
        "blocked_by": list(exc.blocked_by),
        "title": _error_title(info.code),
    }


def topology_ready(report: TopologyReport | None) -> bool:
    """La continuación a Ejecución exige reporte presente y disponible."""
    return report is not None and report.availability.status == "available"


def topology_summary_metrics(report: TopologyReport) -> list[dict[str, str | None]]:
    """Fila plana de KPI tomada de TopologyReport.summary, no de la página visible."""
    items: list[dict[str, str | None]] = [
        {"label": "SKU totales", "value": format_count(report.summary.n_skus), "tone": "data"}
    ]
    for code in SKU_CLASS_ORDER:
        items.append(
            {
                "label": sku_class_label(code),
                "value": format_count(report.summary.by_class.get(cast(SkuClass, code), 0)),
                "tone": None,
            }
        )
    return items


def page_meta(page: Page) -> dict[str, object]:
    total_pages = max(1, (page.total + page.size - 1) // page.size)
    return {
        "total_pages": total_pages,
        "has_prev": page.number > 1,
        "has_next": page.number < total_pages,
        "show": page.total > page.size,
    }


def _pending_map(request: Request) -> dict[str, PendingUpload]:
    store = getattr(request.app.state, _PENDING_ATTR, None)
    if store is None:
        store = {}
        setattr(request.app.state, _PENDING_ATTR, store)
    return store


def _submitted_map(request: Request) -> dict[str, IngestReport]:
    store = getattr(request.app.state, _SUBMITTED_ATTR, None)
    if store is None:
        store = {}
        setattr(request.app.state, _SUBMITTED_ATTR, store)
    return store


def _int_param(raw: str | None, default: int, name: str) -> int:
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise InvalidQuery(f"{name} must be an integer, got {raw!r}") from exc


def _error_title(code: str) -> str:
    if code == "platform.engine_unavailable":
        return "Carga temporalmente no disponible."
    if code == "platform.artifact_missing":
        return "La caracterización todavía no está disponible."
    if code == "platform.contract_invalid":
        return "No fue posible leer el documento de datos."
    if code == "platform.schema_version_unsupported":
        return "La versión del contrato no es compatible."
    return "No fue posible completar la operación."
