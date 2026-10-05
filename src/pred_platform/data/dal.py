"""The ``dal`` data source: what the platform's own database can answer today.

The engine's results do not reach the DAL yet (gaps G1 to G4, plus G8 for the synthetic log), so
most operations answer honestly that they are not available and name the gap that blocks them
(``data/capabilities.py`` is the single source for that). The one real read is the list of
ingests. Nothing is ever invented: a view that shows this data shows only what is stored.

Documents that always exist in some form (lists, topology, run status, verdicts) come back with
``availability.status == "unavailable"``. Documents that have no empty form (an ingest report, one
SKU's selection, a synthetic run, a download) raise ``ArtifactMissing`` naming the open gaps.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Never, get_args

from pred_platform.config import DataSource
from pred_platform.contract import CONTRACT_VERSION, models
from pred_platform.contract.errors import ArtifactMissing, EngineUnavailable, InvalidQuery
from pred_platform.contract.models import CapabilityId
from pred_platform.dal import lectura
from pred_platform.data import capabilities
from pred_platform.data.query import (
    DEFAULT_SIZE,
    PageRequest,
    check_choice,
    check_sort,
    paginate,
)
from pred_platform.data.repository import ArtifactDownload

# Same constants the engine's classifier uses (ADR-006 of the engine); shown while no metrics exist.
_ADI_THRESHOLD = 1.32
_CV2_THRESHOLD = 0.49

_SHA256 = re.compile(r"^[0-9a-f]{64}$")

_SKU_CLASSES = get_args(models.SkuClass)
_FAMILIES = get_args(models.Family)
_TASK_STATES = get_args(models.EstadoTarea)
_FAMILY_STATES = get_args(models.EstadoFamilia)
_TOPOLOGY_SORT = {"sku_id", "adi", "cv2", "n_positive"}
_SELECTION_SORT = {"sku_id", "valor"}

# `submit_ingest` needs the worker (G3) and the engine's overwrite detection (G5), neither of
# which has a capability of its own in the contract.
_SUBMIT_GAPS = ("G1", "G5")


def _now() -> datetime:
    return datetime.now(UTC)


def _envelope(availability: models.Availability) -> dict[str, object]:
    return {
        "schema_version": CONTRACT_VERSION,
        "generated_at": _now(),
        "source": "dal",
        "availability": availability,
    }


def _missing(capability: CapabilityId, what: str) -> Never:
    raise ArtifactMissing(
        f"{what}: la plataforma aún no lo guarda", blocked_by=capabilities.blocked_by(capability)
    )


class DalRepository:
    """``DataRepository`` over the platform's SQLite database (read-only)."""

    source: DataSource = "dal"

    def __init__(self, db_path: Path) -> None:
        # Not opened here: the application must boot without the database (CI smoke test).
        self.db_path = db_path

    # ----------------------------------------------------------------- capabilities
    def get_capabilities(self) -> models.Capabilities:
        return models.Capabilities(
            **_envelope(models.Availability(status="available")),  # type: ignore[arg-type]
            items=capabilities.capability_items(),
        )

    # ---------------------------------------------------------------------- ingests
    def list_ingests(self, *, page: int = 1, size: int = DEFAULT_SIZE) -> models.IngestList:
        request = PageRequest(page, size)
        rows, total = lectura.list_ingestas(
            self.db_path, offset=(request.number - 1) * request.size, limit=request.size
        )
        paging = models.Page(number=request.number, size=request.size, total=total)
        availability = (
            models.Availability(status="available")
            if total
            else models.Availability(
                status="unavailable",
                reason_code="no_data",
                detail="Todavía no se ha cargado ningún archivo.",
            )
        )
        items = [
            models.IngestSummary(
                ingest_id=row.sha256, name=row.nombre_archivo, rows=row.filas, n_skus=row.skus
            )
            for row in rows
        ]
        return models.IngestList(**_envelope(availability), items=items, page=paging)  # type: ignore[arg-type]

    def get_ingest_report(self, ingest_id: str) -> models.IngestReport:
        _missing("ingest_report_persisted", f"No hay informe de la ingesta {ingest_id!r}")

    def submit_ingest(
        self, file_name: str, content: bytes, *, confirm_overwrite: bool = False
    ) -> models.IngestReport:
        raise EngineUnavailable(
            "Cargar archivos necesita el motor, que aún no está conectado a la plataforma",
            blocked_by=_SUBMIT_GAPS,
        )

    # --------------------------------------------------------------------- topology
    def get_topology_report(
        self,
        ingest_id: str,
        *,
        sku_class: str | None = None,
        q: str | None = None,
        sort: str = "sku_id",
        descending: bool = False,
        page: int = 1,
        size: int = DEFAULT_SIZE,
    ) -> models.TopologyReport:
        check_choice("sku_class", sku_class, _SKU_CLASSES)
        check_sort(sort, _TOPOLOGY_SORT)
        _, paging = paginate([], PageRequest(page, size))
        self._require_ingest(ingest_id)
        availability = capabilities.unavailable(
            "topology_persisted", "La plataforma aún no guarda ADI, CV2 ni conteos por SKU."
        )
        return models.TopologyReport(
            **_envelope(availability),  # type: ignore[arg-type]
            ingest_id=ingest_id,
            thresholds=models.Thresholds(adi=_ADI_THRESHOLD, cv2=_CV2_THRESHOLD),
            summary=models.TopologySummary(n_skus=0, by_class=dict.fromkeys(_SKU_CLASSES, 0)),
            items=[],
            page=paging,
        )

    # -------------------------------------------------------------------- run status
    def get_run_status(
        self,
        run_id: str,
        *,
        estado: str | None = None,
        sku: str | None = None,
        family: str | None = None,
        page: int = 1,
        size: int = DEFAULT_SIZE,
    ) -> models.RunStatus:
        check_choice("estado", estado, _TASK_STATES)
        check_choice("family", family, _FAMILIES)
        PageRequest(page, size)
        availability = capabilities.unavailable(
            "run_orchestration", "Aún no hay ejecuciones: la orquestación no está implementada."
        )
        return models.RunStatus(**_envelope(availability), tasks=[])  # type: ignore[arg-type]

    # ------------------------------------------------------------------ selections
    def list_sku_selections(
        self,
        run_id: str,
        *,
        sku_class: str | None = None,
        family: str | None = None,
        estado: str | None = None,
        q: str | None = None,
        sort: str = "sku_id",
        descending: bool = False,
        page: int = 1,
        size: int = DEFAULT_SIZE,
    ) -> models.SkuSelectionList:
        check_choice("sku_class", sku_class, _SKU_CLASSES)
        check_choice("family", family, _FAMILIES)
        check_choice("estado", estado, _FAMILY_STATES)
        check_sort(sort, _SELECTION_SORT)
        _, paging = paginate([], PageRequest(page, size))
        availability = capabilities.unavailable(
            "selection_results_persisted",
            "La plataforma aún no guarda resultados de selección por SKU.",
        )
        return models.SkuSelectionList(
            **_envelope(availability),  # type: ignore[arg-type]
            run_id=run_id,
            items=[],
            page=paging,
        )

    def get_sku_selection(
        self, run_id: str, sku_id: str, *, include_windows: bool = False
    ) -> models.SkuSelection:
        _missing("selection_results_persisted", f"No hay selección del SKU {sku_id!r}")

    # -------------------------------------------------------------------- verdicts
    def get_validation_verdicts(self, run_id: str) -> models.ValidationVerdicts:
        availability = capabilities.unavailable(
            "retrospective_validation", "La validación retrospectiva (L4) no está implementada."
        )
        return models.ValidationVerdicts(
            **_envelope(availability),  # type: ignore[arg-type]
            run_id=run_id,
            items=[],
        )

    # -------------------------------------------------------------- synthetic runs
    def list_synthetic_runs(
        self, *, page: int = 1, size: int = DEFAULT_SIZE
    ) -> models.SyntheticList:
        _, paging = paginate([], PageRequest(page, size))
        availability = capabilities.unavailable(
            "synthetic_log_read", "La plataforma aún no lee las bitácoras de corridas sintéticas."
        )
        return models.SyntheticList(**_envelope(availability), items=[], page=paging)  # type: ignore[arg-type]

    def get_synthetic_run(self, run_ref: str) -> models.SyntheticRun:
        _missing("synthetic_log_read", f"No hay corrida sintética {run_ref!r}")

    def download_synthetic_artifact(self, run_ref: str) -> ArtifactDownload:
        _missing("synthetic_log_read", f"No hay artefacto de la corrida sintética {run_ref!r}")

    # --------------------------------------------------------------------- helpers
    def _require_ingest(self, ingest_id: str) -> None:
        if not _SHA256.match(ingest_id):
            raise InvalidQuery(
                f"ingest_id must be a lowercase SHA-256 hex digest, got {ingest_id!r}"
            )
        if not lectura.ingesta_existe(self.db_path, ingest_id):
            raise ArtifactMissing(f"No existe la ingesta {ingest_id[:12]}…")
