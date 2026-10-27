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
from typing import Protocol

from pred_platform.config import DataSource
from pred_platform.contract import models_v2
from pred_platform.contract.errors import ContractInvalid
from pred_platform.contract.models import (
    Capabilities,
    IngestList,
    IngestReport,
    RunStatus,
    SkuSelection,
    SkuSelectionList,
    SyntheticList,
    SyntheticRun,
    TopologyReport,
)
from pred_platform.data.query import DEFAULT_SIZE


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


class DataRepository(Protocol):
    """What the views read. Every list is filtered, sorted and paged on the server."""

    source: DataSource

    def get_capabilities(self) -> Capabilities: ...

    def list_ingests(self, *, page: int = 1, size: int = DEFAULT_SIZE) -> IngestList: ...

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
