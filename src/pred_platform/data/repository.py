"""The interface every data source implements (contract, section 10).

The views depend on ``DataRepository`` only. Today there are two sources, selected by
configuration: ``fixture`` (the contract's examples) and ``dal`` (the platform's SQLite database).

The ``*_m3_*`` operations read M3's published evidence (contract v2, section 7). Every one but
``list_m3_runs`` needs the exact M3 ``run_id``: ``list_m3_runs`` resolves an M2 reference or an
identity to its runs and names the newest PUBLICADA one in ``selected_run_id``. The evidence is
available only for a PUBLICADA run; a run in another state answers with its metadata only.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from pred_platform.config import DataSource
from pred_platform.contract import models_v2
from pred_platform.contract.errors import ContractInvalid
from pred_platform.contract.models import (
    Capabilities,
    Ejecucion,
    ErrorInfo,
    IngestList,
    IngestReport,
    IngestSummary,
    RunStatus,
    SkuSelection,
    SkuSelectionList,
    SyntheticList,
    SyntheticRun,
    TopologyReport,
)
from pred_platform.data.m3_confirmation import M3Confirmation
from pred_platform.data.m3_intervals import M3Interval
from pred_platform.data.query import DEFAULT_SIZE
from pred_platform.data.run_status import CauseSnapshot, StageReference
from pred_platform.evidence.models import GroupEvidence, GroupRunOption


@dataclass(frozen=True, slots=True)
class ArtifactDownload:
    """A file the user can download, already checked against its declared SHA-256."""

    file_name: str
    media_type: str
    content: bytes
    sha256: str

    @classmethod
    def verified(
        cls, file_name: str, media_type: str, content: bytes, expected_sha256: str
    ) -> ArtifactDownload:
        """Build the download; ``ContractInvalid`` if ``content`` is not the declared file."""
        actual = hashlib.sha256(content).hexdigest()
        if actual != expected_sha256:
            raise ContractInvalid(
                f"{file_name}: el SHA-256 del contenido ({actual[:12]}…) no coincide con el "
                f"declarado ({expected_sha256[:12]}…)"
            )
        return cls(file_name, media_type, content, actual)


@dataclass(frozen=True, slots=True)
class M3DateRange:
    """Rango y conteo ya publicados; los extremos nulos representan un rango vacío."""

    start: str | None
    end: str | None
    count: int


@dataclass(frozen=True, slots=True)
class StageTiming:
    """El inicio y el fin de una etapa del motor (L1 a L4) en un intento de la corrida.

    ``finished_at`` queda vacío si la etapa no terminó bien: una etapa fallida no publica.
    """

    code: str
    started_at: datetime
    finished_at: datetime | None


@dataclass(frozen=True, slots=True)
class M3ReserveBlocks:
    """Los bloques de la reserva que M3 fijó en el protocolo de la corrida (ADR-03-012).

    Orígenes y fechas objetivo son conceptos distintos: un origen pronostica días posteriores.
    """

    selection_origins: M3DateRange
    embargo_origins: M3DateRange
    test_origins: M3DateRange
    selection_dates: M3DateRange
    test_dates: M3DateRange


class DataRepository(Protocol):
    """What the views read. Every list is filtered, sorted and paged on the server."""

    source: DataSource

    def get_capabilities(self) -> Capabilities: ...

    def list_ingests(self, *, page: int = 1, size: int = DEFAULT_SIZE) -> IngestList: ...

    def get_ingest(self, ingest_id: str) -> IngestSummary | None: ...

    def get_ingest_report(self, ingest_id: str) -> IngestReport: ...

    def submit_ingest(
        self, file_name: str, content: bytes, *, confirm_overwrite: bool = False
    ) -> IngestReport: ...

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
    ) -> TopologyReport: ...

    def synthetic_ingests(self, ingest_ids: Sequence[str]) -> frozenset[str]:
        """The ingests among ``ingest_ids`` whose report declares synthetic data.

        The platform only runs real data, so the views mark them as not runnable.
        """
        ...

    def get_run_status(
        self,
        run_id: str,
        *,
        estado: str | None = None,
        sku: str | None = None,
        family: str | None = None,
        page: int = 1,
        size: int = DEFAULT_SIZE,
    ) -> RunStatus: ...

    def list_runs(self) -> list[Ejecucion]:
        """Every launched run, newest first; empty when none was launched.

        Each run's status, settings and progress come from ``get_run_status``.
        """
        ...

    def get_run_causes(self, run_id: str) -> CauseSnapshot:
        """Whole-run cause snapshot, read only when the execution page opens."""
        ...

    def get_run_stage_history(self, run_id: str) -> list[StageReference]:
        """Cached stage timestamps of comparable completed runs; initial page load only."""
        ...

    def get_run_error(self, run_id: str) -> ErrorInfo | None:
        """The error a stopped or failed run recorded; ``Ejecucion`` (contract v1) has none."""
        ...

    def get_stage_timings(self, run_id: str) -> tuple[StageTiming, ...]:
        """When each engine stage of the run started and ended, every attempt in log order.

        Empty when the source keeps no stage times; Results then says the time is unknown.
        """
        ...

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
    ) -> SkuSelectionList: ...

    def get_sku_selection(
        self, run_id: str, sku_id: str, *, include_windows: bool = False
    ) -> SkuSelection: ...

    def list_m3_runs(
        self,
        *,
        run_id_m2: str | None = None,
        identidad: str | None = None,
        ingesta_sha256: str | None = None,
        estado: str | None = None,
        page: int = 1,
        size: int = DEFAULT_SIZE,
    ) -> models_v2.M3Runs:
        """M3 runs, newest first. ``run_id_m2`` is an M2 reference: the ``dal`` source also takes
        the platform's run id (``ejecuciones.id``) and resolves it to the engine's run id."""
        ...

    def get_m3_run(self, run_id: str) -> models_v2.M3Run | None:
        """One M3 run's metadata, or ``None`` when it is unknown. Unlike ``get_m3_run_status``
        it does not count the run's units, so a view can name the run without that cost."""
        ...

    def get_m3_run_status(self, run_id: str) -> models_v2.M3RunStatus: ...

    def get_m3_evidence(
        self,
        run_id: str,
        *,
        skus: Sequence[str] | None = None,
        q: str | None = None,
        sku_class: str | None = None,
        veredicto: str | None = None,
        grupo: str | None = None,
        familia_campeona: str | None = None,
        sort: str = "sku",
        descending: bool = False,
        page: int = 1,
        size: int = DEFAULT_SIZE,
    ) -> models_v2.M3Evidence:
        """The verdicts of one run; ``funnel`` and ``late_launches`` always cover the whole run."""
        ...

    def get_m3_metrics(
        self,
        run_id: str,
        *,
        sku: str | None = None,
        candidato_id: str | None = None,
        ventana: str | None = None,
        metrica: str | None = None,
        page: int = 1,
        size: int = DEFAULT_SIZE,
    ) -> models_v2.M3Metrics: ...

    def get_m3_categories(
        self,
        run_id: str,
        *,
        sku_class: str | None = None,
        familia: str | None = None,
        page: int = 1,
        size: int = DEFAULT_SIZE,
    ) -> models_v2.M3Categories: ...

    def get_m3_portfolio(
        self,
        run_id: str,
        *,
        sku_class: str | None = None,
        familia: str | None = None,
        metrica: str | None = None,
        page: int = 1,
        size: int = DEFAULT_SIZE,
    ) -> models_v2.M3Portfolio: ...

    def get_m3_observations(
        self,
        run_id: str,
        *,
        sku: str | None = None,
        fecha: str | None = None,
        page: int = 1,
        size: int = DEFAULT_SIZE,
    ) -> models_v2.M3Observations: ...

    def get_m3_reserve_blocks(self, run_id: str) -> M3ReserveBlocks | None:
        """Los bloques de la reserva de una corrida exacta; ``None`` si su protocolo no los fija."""
        ...

    def get_m3_intervals(self, run_id: str, skus: Sequence[str]) -> list[M3Interval]:
        """Unadjusted r sets for exact SKUs of a PUBLICADA run, ordered by SKU.

        Missing rows, unknown/unpublished runs and older databases return no intervals.
        ``superior=None`` means unbounded; ``inferior_cola`` adds a disjoint upper tail.
        """
        ...

    def list_m3_group_runs(self) -> tuple[GroupRunOption, ...]:
        """Published seed 0/1/2 choices for the final group, never development defaults."""
        ...

    def get_m3_group(self, run_ids: Sequence[str]) -> GroupEvidence:
        """Compute the final three-seed evidence on read, without persisting derived rows.

        The last few results are kept in memory only and reused until ``pred.db`` or its WAL
        changes.
        """
        ...

    def comparable_m3_runs(self, reference: str, run_ids: Sequence[str]) -> frozenset[str]:
        """The PUBLICADA runs of ``run_ids`` the final group accepts beside ``reference``.

        Applies the group's own rules: same ingest, identity, protocol and population, and a
        different seed. ``EvidenceInvalid`` if ``reference`` itself is not usable seed evidence.
        """
        ...

    def get_m3_confirmation(self, run_id: str) -> M3Confirmation | None:
        """La confirmación del campeón en el bloque de prueba de una corrida PUBLICADA.

        ``None`` si la corrida no está PUBLICADA o se publicó antes de que M3 la calculara.
        """
        ...

    def get_m3_champions(
        self,
        run_id: str,
        *,
        sku_class: str | None = None,
        familia: str | None = None,
        page: int = 1,
        size: int = DEFAULT_SIZE,
    ) -> models_v2.M3Champions: ...

    def get_m3_forecasts(
        self,
        run_id: str,
        *,
        sku: str | None = None,
        candidato_id: str | None = None,
        origen: str | None = None,
        h: int | None = None,
        page: int = 1,
        size: int = DEFAULT_SIZE,
    ) -> models_v2.M3Forecasts: ...

    def get_m3_failures(
        self,
        run_id: str,
        *,
        sku: str | None = None,
        candidato_id: str | None = None,
        page: int = 1,
        size: int = DEFAULT_SIZE,
    ) -> models_v2.M3Failures: ...

    def get_m3_units(
        self,
        run_id: str,
        *,
        sku: str | None = None,
        candidato_id: str | None = None,
        origen: str | None = None,
        estado: str | None = None,
        page: int = 1,
        size: int = DEFAULT_SIZE,
    ) -> models_v2.M3Units: ...

    def list_synthetic_runs(self, *, page: int = 1, size: int = DEFAULT_SIZE) -> SyntheticList: ...

    def get_synthetic_run(self, run_ref: str) -> SyntheticRun: ...

    def download_synthetic_artifact(self, run_ref: str) -> ArtifactDownload: ...
