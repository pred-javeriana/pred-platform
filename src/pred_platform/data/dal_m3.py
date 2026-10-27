"""The M3 read operations of the ``dal`` source: M3's published evidence as v2 documents.

``dal/evidencia.py`` reads the rows in one snapshot; this module validates them as contract v2
documents (``models_v2``), which is where data from the shared database enters the platform. The
filters, the order and the page come from ``data/m3_query.py``, the same rules the fixtures apply.

The funnel and the late launches cover the whole run. The cut and the excluded products come from
the metadata M1 stores in the ingestion's published Parquet (``dal/metadatos_parquet.py``); when
that metadata is absent the evidence answers ``metadata_missing`` instead of claiming zero
exclusions. Rows that do not satisfy the contract raise ``ContractInvalid``.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from pred_platform.config import DataSource
from pred_platform.contract import models_v2
from pred_platform.contract.errors import ContractInvalid
from pred_platform.dal import evidencia
from pred_platform.dal.metadatos_parquet import CutoffMetadata, read_cutoff_metadata
from pred_platform.data import m3_documents as docs
from pred_platform.data import m3_query
from pred_platform.data.m3_query import VERDICTS, M3Query
from pred_platform.data.query import DEFAULT_SIZE


def _validated[T: BaseModel](model: type[T], data: dict[str, Any]) -> T:
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        first = exc.errors()[0]
        path = ".".join(str(part) for part in first["loc"])
        raise ContractInvalid(
            f"{model.__name__}: la evidencia M3 guardada no cumple el contrato v2: "
            f"{path or '(raíz)'}: {first['msg']}",
            field=path or None,
        ) from exc


class M3DalSource:
    """The nine M3 operations of ``DataRepository`` over the platform's database."""

    source: DataSource = "dal"
    db_path: Path
    data_root: Path | None

    # ------------------------------------------------------------------------ runs
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
        query = m3_query.runs_query(
            identidad=identidad, ingesta_sha256=ingesta_sha256, estado=estado, page=page, size=size
        )
        reference = (run_id_m2 or "").strip() or None
        read = evidencia.list_runs(self.db_path, reference, query)
        if read.total == 0:
            return models_v2.M3Runs(
                **docs.envelope(self.source, docs.unavailable("no_data", docs.NO_RUNS)),
                items=[],
                page=docs.empty_page(query),
                selected_run_id=None,
            )
        return _validated(
            models_v2.M3Runs,
            {
                **docs.envelope(self.source, docs.available()),
                "items": read.rows,
                "page": docs.page_of(query, read.total),
                "selected_run_id": read.selected_run_id,
            },
        )

    def get_m3_run_status(self, run_id: str) -> models_v2.M3RunStatus:
        read = evidencia.run_status(self.db_path, run_id)
        if read.run is None:
            return models_v2.M3RunStatus(
                **docs.envelope(self.source, docs.unavailable("no_data", docs.NO_RUN)),
                run=None,
                progress=None,
            )
        return _validated(
            models_v2.M3RunStatus,
            {
                **docs.envelope(self.source, docs.available()),
                "run": read.run,
                "progress": read.progress,
            },
        )

    # -------------------------------------------------------------------- evidence
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
        query = m3_query.evidence_query(
            skus=skus,
            q=q,
            sku_class=sku_class,
            veredicto=veredicto,
            grupo=grupo,
            familia_campeona=familia_campeona,
            sort=sort,
            descending=descending,
            page=page,
            size=size,
        )
        read = evidencia.read_published(self.db_path, run_id, "evidence", query)
        empty = {"funnel": None, "late_launches": None}
        unavailable = self._unavailable(models_v2.M3Evidence, read, query, empty)
        if unavailable is not None:
            return unavailable
        assert read.run is not None and read.verdict_counts is not None
        metadata = self._cutoff(read.ruta_parquet)
        if metadata is None:
            return _validated(
                models_v2.M3Evidence,
                {
                    **docs.envelope(
                        self.source, docs.unavailable("metadata_missing", docs.METADATA_MISSING)
                    ),
                    "run": read.run,
                    "items": [],
                    "page": docs.empty_page(query),
                    **empty,
                },
            )
        trace = {"run_id": run_id, "sku": None, "ingesta_sha256": read.run["ingesta_sha256"]}
        counts = {verdict: read.verdict_counts.get(verdict, 0) for verdict in VERDICTS}
        evaluated = sum(counts[v] for v in m3_query.GROUPS["evaluados"])
        not_evaluated = sum(counts[v] for v in m3_query.GROUPS["no_evaluados"])
        excluded = [{**trace, "sku": sku, "causa": cause} for sku, cause in metadata.exclusions]
        return _validated(
            models_v2.M3Evidence,
            {
                **docs.envelope(self.source, docs.available()),
                "run": read.run,
                "items": read.rows,
                "page": docs.page_of(query, read.total),
                "funnel": {
                    **trace,
                    "total_productos": len(excluded) + evaluated + not_evaluated,
                    "excluded_count": len(excluded),
                    "tras_exclusiones": evaluated + not_evaluated,
                    "no_evaluados": not_evaluated,
                    "evaluados": evaluated,
                    "conteos": counts,
                },
                "late_launches": {
                    **trace,
                    "t_star": metadata.t_star,
                    "excluded_count": len(excluded),
                    "items": excluded,
                },
            },
        )

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
    ) -> models_v2.M3Metrics:
        query = m3_query.metrics_query(
            sku=sku,
            candidato_id=candidato_id,
            ventana=ventana,
            metrica=metrica,
            page=page,
            size=size,
        )
        return self._published(models_v2.M3Metrics, "metrics", run_id, query)

    def get_m3_categories(
        self,
        run_id: str,
        *,
        sku_class: str | None = None,
        familia: str | None = None,
        page: int = 1,
        size: int = DEFAULT_SIZE,
    ) -> models_v2.M3Categories:
        query = m3_query.categories_query(
            sku_class=sku_class, familia=familia, page=page, size=size
        )
        return self._published(models_v2.M3Categories, "categories", run_id, query)

    def get_m3_portfolio(
        self,
        run_id: str,
        *,
        sku_class: str | None = None,
        familia: str | None = None,
        metrica: str | None = None,
        page: int = 1,
        size: int = DEFAULT_SIZE,
    ) -> models_v2.M3Portfolio:
        query = m3_query.portfolio_query(
            sku_class=sku_class, familia=familia, metrica=metrica, page=page, size=size
        )
        return self._published(models_v2.M3Portfolio, "portfolio", run_id, query)

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
    ) -> models_v2.M3Forecasts:
        query = m3_query.forecasts_query(
            sku=sku, candidato_id=candidato_id, origen=origen, h=h, page=page, size=size
        )
        return self._published(models_v2.M3Forecasts, "forecasts", run_id, query)

    def get_m3_failures(
        self,
        run_id: str,
        *,
        sku: str | None = None,
        candidato_id: str | None = None,
        page: int = 1,
        size: int = DEFAULT_SIZE,
    ) -> models_v2.M3Failures:
        query = m3_query.failures_query(sku=sku, candidato_id=candidato_id, page=page, size=size)
        return self._published(models_v2.M3Failures, "failures", run_id, query)

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
    ) -> models_v2.M3Units:
        query = m3_query.units_query(
            sku=sku, candidato_id=candidato_id, origen=origen, estado=estado, page=page, size=size
        )
        return self._published(models_v2.M3Units, "units", run_id, query)

    # --------------------------------------------------------------------- helpers
    def _published[T: models_v2.PublishedDocument](
        self, model: type[T], kind: evidencia.Kind, run_id: str, query: M3Query
    ) -> T:
        read = evidencia.read_published(self.db_path, run_id, kind, query)
        unavailable = self._unavailable(model, read, query, {})
        if unavailable is not None:
            return unavailable
        return _validated(
            model,
            {
                **docs.envelope(self.source, docs.available()),
                "run": read.run,
                "items": read.rows,
                "page": docs.page_of(query, read.total),
            },
        )

    def _unavailable[T: models_v2.PublishedDocument](
        self,
        model: type[T],
        read: evidencia.PublishedRead,
        query: M3Query,
        empty: dict[str, None],
    ) -> T | None:
        """The ``no_data`` or ``not_published`` document, or ``None`` for a PUBLICADA run."""
        if read.run is None:
            availability = docs.unavailable("no_data", docs.NO_RUN)
        elif read.run["estado"] != "PUBLICADA":
            run = _validated(models_v2.M3Run, read.run)
            availability = docs.unavailable("not_published", docs.not_published(run))
        else:
            return None
        return _validated(
            model,
            {
                **docs.envelope(self.source, availability),
                "run": read.run,
                "items": [],
                "page": docs.empty_page(query),
                **empty,
            },
        )

    def _cutoff(self, ruta_parquet: str | None) -> CutoffMetadata | None:
        if ruta_parquet is None:
            return None
        path = Path(ruta_parquet)
        if not path.is_absolute() and self.data_root is not None:
            path = self.data_root / path
        try:
            return read_cutoff_metadata(path)
        except ValueError as exc:
            raise ContractInvalid(f"Parquet publicado de la ingesta: {exc}") from exc
