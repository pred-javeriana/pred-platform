"""The interface every data source implements (contract, section 10).

The views depend on ``DataRepository`` only. Today there are two sources, selected by
configuration: ``fixture`` (the contract's examples) and ``dal`` (the platform's SQLite database).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Protocol

from pred_platform.config import DataSource
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
    ValidationVerdicts,
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

    def get_validation_verdicts(self, run_id: str) -> ValidationVerdicts: ...

    def list_synthetic_runs(self, *, page: int = 1, size: int = DEFAULT_SIZE) -> SyntheticList: ...

    def get_synthetic_run(self, run_ref: str) -> SyntheticRun: ...

    def download_synthetic_artifact(self, run_ref: str) -> ArtifactDownload: ...
