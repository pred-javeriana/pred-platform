"""The M3 read operations of the ``dal`` source: M3's published evidence as v2 documents.

``dal/evidencia.py`` reads the rows in one snapshot; this module validates them as contract v2
documents (``models_v2``), which is where data from the shared database enters the platform. The
filters, the order and the page come from ``data/m3_query.py``, the same rules the fixtures apply.

The funnel, the late launches and the products M2 excluded from the competition cover the whole
run. The cut and the excluded products come from M3's SQLite publication; when the cut is absent
the evidence answers ``metadata_missing`` instead of claiming zero exclusions. A run published
before M3 listed M2's exclusions reports them as ``None``, not as zero. Rows that do not satisfy
the contract raise ``ContractInvalid``.
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
from pred_platform.data import m3_documents as docs
from pred_platform.data import m3_query
from pred_platform.data.m3_query import VERDICTS, M3Query
from pred_platform.data.query import DEFAULT_SIZE
from pred_platform.data.repository import M3ReserveBlocks


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
    """Lecturas M3 de ``DataRepository`` sobre la base de datos de la plataforma."""

    source: DataSource = "dal"
    db_path: Path
    data_root: Path | None

    def get_m3_reserve_blocks(self, run_id: str) -> M3ReserveBlocks | None:
        """El contrato de lectura actual aún no expone estos límites resueltos."""
        # TODO: leer el valor oficial que exponga M3; no deducirlo de cortes ni ventanas.
        return None

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
        empty = {"funnel": None, "late_launches": None, "eligibility_exclusions": None}
        unavailable = self._unavailable(models_v2.M3Evidence, read, query, empty)
        if unavailable is not None:
            return unavailable
        assert read.run is not None and read.verdict_counts is not None
        if read.t_star is None:
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
        excluded = [{**trace, **item} for item in read.late_launches or []]
        eligibility = (
            None
            if read.eligibility_exclusions is None
            else [{**trace, **item} for item in read.eligibility_exclusions]
        )
        return _validated(
            models_v2.M3Evidence,
            {
                **docs.envelope(self.source, docs.available()),
                "run": read.run,
                "items": read.rows,
                "page": docs.page_of(query, read.total),
                "funnel": {
                    **trace,
                    "total_productos": len(excluded)
                    + len(eligibility or [])
                    + evaluated
                    + not_evaluated,
                    "excluded_count": len(excluded),
                    "eligibility_excluded_count": None if eligibility is None else len(eligibility),
                    "tras_exclusiones": evaluated + not_evaluated,
                    "no_evaluados": not_evaluated,
                    "evaluados": evaluated,
                    "conteos": counts,
                },
                "late_launches": {
                    **trace,
                    "t_star": read.t_star,
                    "excluded_count": len(excluded),
                    "items": excluded,
                },
                "eligibility_exclusions": None
                if eligibility is None
                else {**trace, "excluded_count": len(eligibility), "items": eligibility},
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

    def get_m3_observations(
        self,
        run_id: str,
        *,
        sku: str | None = None,
        fecha: str | None = None,
        page: int = 1,
        size: int = DEFAULT_SIZE,
    ) -> models_v2.M3Observations:
        query = m3_query.observations_query(sku=sku, fecha=fecha, page=page, size=size)
        return self._published(models_v2.M3Observations, "observations", run_id, query)

    def get_m3_champions(
        self,
        run_id: str,
        *,
        sku_class: str | None = None,
        familia: str | None = None,
        page: int = 1,
        size: int = DEFAULT_SIZE,
    ) -> models_v2.M3Champions:
        query = m3_query.champions_query(sku_class=sku_class, familia=familia, page=page, size=size)
        return self._published(models_v2.M3Champions, "champions", run_id, query)

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
        unavailable = self._unavailable(
            model,
            read,
            query,
            {},
            missing_detail=docs.OBSERVATIONS_MISSING
            if kind == "observations" and read.t_star is None
            else None,
        )
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
        missing_detail: str | None = None,
    ) -> T | None:
        """The ``no_data`` or ``not_published`` document, or ``None`` for a PUBLICADA run."""
        if read.run is None:
            availability = docs.unavailable("no_data", docs.NO_RUN)
        elif read.run["estado"] != "PUBLICADA":
            run = _validated(models_v2.M3Run, read.run)
            availability = docs.unavailable("not_published", docs.not_published(run))
        elif missing_detail is not None:
            availability = docs.unavailable("metadata_missing", missing_detail)
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
