"""The envelope and the availability of the v2 documents, shared by both data sources.

Every M3 document carries ``schema_version``, ``generated_at``, ``source`` and ``availability``
(contract v2, sections 3 and 8). The visible details of the unavailable states live here so both
sources say the same thing.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pred_platform.config import DataSource
from pred_platform.contract import M3_CONTRACT_VERSION, models_v2
from pred_platform.data.m3_query import M3Query

Reason = Literal["no_data", "not_published", "metadata_missing"]

NO_RUN = "No hay ninguna corrida M3 con ese identificador."
NO_RUNS = "No hay corridas M3 que coincidan con la consulta."
METADATA_MISSING = (
    "La publicación no conserva los metadatos de exclusión de la ingesta (corte t* y productos "
    "lanzados después del corte): sin ellos no hay embudo ni veredictos. La falta de metadatos no "
    "significa cero exclusiones; vuelva a ingerir el archivo con un motor que los publique."
)


def not_published(run: models_v2.M3Run) -> str:
    """Detail of a run that exists but is not PUBLICADA: its state, and the cause if rejected."""
    if run.estado == "RECHAZADA":
        return f"La corrida M3 {run.run_id} fue RECHAZADA: {run.causa_rechazo}"
    return f"La corrida M3 {run.run_id} está {run.estado}; su evidencia aparece al publicarse."


def available() -> models_v2.Availability:
    return models_v2.Availability(status="available", reason_code=None, detail=None)


def unavailable(reason: Reason, detail: str) -> models_v2.Availability:
    return models_v2.Availability(status="unavailable", reason_code=reason, detail=detail)


def envelope(source: DataSource, availability: models_v2.Availability) -> dict[str, Any]:
    return {
        "schema_version": M3_CONTRACT_VERSION,
        "generated_at": datetime.now(UTC),
        "source": source,
        "availability": availability,
    }


def empty_page(query: M3Query) -> models_v2.Page:
    """The requested page of a document that has no rows."""
    return models_v2.Page(number=query.page.number, size=query.page.size, total=0)


def page_of(query: M3Query, total: int) -> models_v2.Page:
    return models_v2.Page(number=query.page.number, size=query.page.size, total=total)
