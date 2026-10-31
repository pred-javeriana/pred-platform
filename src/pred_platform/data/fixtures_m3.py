"""The M3 read operations of the ``fixture`` source: the contract's v2 examples (contract v2, §7).

The v2 examples are independent illustrative data, not the results of an experiment, and they are
not linked to the v1 runs. A scenario names which example answers each operation; the filters,
the order and the page come from ``data/m3_query.py``, the same rules the DAL turns into SQL, and
every rebuilt document is validated again. The funnel and the late launches always describe the
whole run, never the filtered page.

Limits inherited from the examples: only some runs have each document. A run the scenario does not
know answers ``no_data``; a known run that is not PUBLICADA answers ``not_published`` with its
metadata; a PUBLICADA run without an example of that document raises ``ArtifactMissing`` instead
of inventing an empty result.
"""

from __future__ import annotations

from collections.abc import Sequence
from functools import cache
from typing import Any

from pydantic import BaseModel

from pred_platform.config import DataSource
from pred_platform.contract import models_v2
from pred_platform.contract.errors import ArtifactMissing
from pred_platform.contract.loader import parse_as
from pred_platform.contract.resources import read_example
from pred_platform.data import m3_documents as docs
from pred_platform.data import m3_query
from pred_platform.data.m3_query import M3Query
from pred_platform.data.query import DEFAULT_SIZE
from pred_platform.data.repository import M3DateRange, M3ReserveBlocks
from pred_platform.data.scenarios import Scenario


@cache
def _text(example: str) -> str:
    return read_example(example)


def _load[T: BaseModel](model: type[T], example: str) -> T:
    return parse_as(model, _text(example))


def _page(rows: Sequence[Any], query: M3Query) -> tuple[list[Any], models_v2.Page]:
    start = (query.page.number - 1) * query.page.size
    return list(rows[start : start + query.page.size]), docs.page_of(query, len(rows))


class M3FixtureSource:
    """Lecturas M3 de ``DataRepository`` sobre los ejemplos v2 del escenario."""

    source: DataSource = "fixture"
    scenario: Scenario

    def get_m3_reserve_blocks(self, run_id: str) -> M3ReserveBlocks | None:
        """Simula cinco rangos resueltos del protocolo, sin embargo de fechas objetivo."""
        if run_id != "m3-real-02" or self.scenario.name != "normal":
            return None
        return M3ReserveBlocks(
            selection_origins=M3DateRange("2026-06-29", "2026-06-29", 1),
            embargo_origins=M3DateRange("2026-06-30", "2026-06-30", 1),
            test_origins=M3DateRange("2026-07-01", "2026-07-01", 1),
            selection_dates=M3DateRange("2026-06-30", "2026-07-01", 2),
            test_dates=M3DateRange("2026-07-02", "2026-07-03", 2),
        )

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
        doc = _load(models_v2.M3Runs, self.scenario.m3_runs)
        wanted_m2 = (run_id_m2 or "").strip() or None
        rows = [
            run
            for run in doc.items
            if query.matches(run) and (wanted_m2 is None or run.run_id_m2 == wanted_m2)
        ]
        if not rows:
            return models_v2.M3Runs(
                **docs.envelope(self.source, docs.unavailable("no_data", docs.NO_RUNS)),
                items=[],
                page=docs.empty_page(query),
                selected_run_id=None,
            )
        rows = m3_query.ordered(rows, query)
        selected = next((run.run_id for run in rows if run.estado == "PUBLICADA"), None)
        items, paging = _page(rows, query)
        return models_v2.M3Runs.model_validate(
            {**dict(doc), "items": items, "page": paging, "selected_run_id": selected}
        )

    def get_m3_run_status(self, run_id: str) -> models_v2.M3RunStatus:
        for example in self.scenario.m3_run_status:
            doc = _load(models_v2.M3RunStatus, example)
            if doc.run is not None and doc.run.run_id == run_id:
                return doc
        run = self._known_runs().get(run_id)
        if run is None:
            return models_v2.M3RunStatus(
                **docs.envelope(self.source, docs.unavailable("no_data", docs.NO_RUN)),
                run=None,
                progress=None,
            )
        # The run is known from another example; its durable progress is not.
        return models_v2.M3RunStatus(
            **docs.envelope(self.source, docs.available()), run=run, progress=None
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
        return self._published(models_v2.M3Evidence, self.scenario.m3_evidence, run_id, query)

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
        return self._published(models_v2.M3Metrics, self.scenario.m3_metrics, run_id, query)

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
        return self._published(models_v2.M3Categories, self.scenario.m3_categories, run_id, query)

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
        return self._published(models_v2.M3Portfolio, self.scenario.m3_portfolio, run_id, query)

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
        for example in self.scenario.m3_evidence:
            evidence = _load(models_v2.M3Evidence, example)
            if (
                evidence.run is not None
                and evidence.run.run_id == run_id
                and evidence.availability.reason_code == "metadata_missing"
            ):
                return models_v2.M3Observations(
                    **docs.envelope(
                        self.source, docs.unavailable("metadata_missing", docs.OBSERVATIONS_MISSING)
                    ),
                    run=evidence.run,
                    items=[],
                    page=docs.empty_page(query),
                )
        return self._published(
            models_v2.M3Observations, self.scenario.m3_observations, run_id, query
        )

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
        return self._published(models_v2.M3Champions, self.scenario.m3_champions, run_id, query)

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
        return self._published(models_v2.M3Forecasts, self.scenario.m3_forecasts, run_id, query)

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
        return self._published(models_v2.M3Failures, self.scenario.m3_failures, run_id, query)

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
        return self._published(models_v2.M3Units, self.scenario.m3_units, run_id, query)

    # --------------------------------------------------------------------- helpers
    def _known_runs(self) -> dict[str, models_v2.M3Run]:
        """Every run some example of the scenario describes, by its exact M3 id."""
        runs: dict[str, models_v2.M3Run] = {}
        for run in _load(models_v2.M3Runs, self.scenario.m3_runs).items:
            runs.setdefault(run.run_id, run)
        for model, examples in self._published_examples():
            for example in examples:
                run = _load(model, example).run
                if run is not None:
                    runs.setdefault(run.run_id, run)
        for example in self.scenario.m3_run_status:
            run = _load(models_v2.M3RunStatus, example).run
            if run is not None:
                runs.setdefault(run.run_id, run)
        return runs

    def _published_examples(
        self,
    ) -> list[tuple[type[models_v2.PublishedDocument], tuple[str, ...]]]:
        s = self.scenario
        return [
            (models_v2.M3Evidence, s.m3_evidence),
            (models_v2.M3Metrics, s.m3_metrics),
            (models_v2.M3Categories, s.m3_categories),
            (models_v2.M3Portfolio, s.m3_portfolio),
            (models_v2.M3Observations, s.m3_observations),
            (models_v2.M3Champions, s.m3_champions),
            (models_v2.M3Forecasts, s.m3_forecasts),
            (models_v2.M3Failures, s.m3_failures),
            (models_v2.M3Units, s.m3_units),
        ]

    def _published[T: models_v2.PublishedDocument](
        self, model: type[T], examples: Sequence[str], run_id: str, query: M3Query
    ) -> T:
        for example in examples:
            doc: Any = _load(model, example)
            if doc.run is None or doc.run.run_id != run_id:
                continue
            if doc.availability.status != "available":
                return model.model_validate({**dict(doc), "page": docs.empty_page(query)})
            rows = m3_query.ordered([row for row in doc.items if query.matches(row)], query)
            items, paging = _page(rows, query)
            # The funnel and the late launches of an evidence document stay those of the run.
            return model.model_validate({**dict(doc), "items": items, "page": paging})

        run = self._known_runs().get(run_id)
        empty = {name: None for name in ("funnel", "late_launches") if name in model.model_fields}
        if run is None:
            return model.model_validate(
                {
                    **docs.envelope(self.source, docs.unavailable("no_data", docs.NO_RUN)),
                    "run": None,
                    "items": [],
                    "page": docs.empty_page(query),
                    **empty,
                }
            )
        if run.estado != "PUBLICADA":
            return model.model_validate(
                {
                    **docs.envelope(
                        self.source, docs.unavailable("not_published", docs.not_published(run))
                    ),
                    "run": run,
                    "items": [],
                    "page": docs.empty_page(query),
                    **empty,
                }
            )
        raise ArtifactMissing(
            f"El escenario fixture {self.scenario.name!r} no tiene {model.__name__} de la "
            f"corrida M3 {run_id!r}"
        )
