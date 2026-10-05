"""The ``fixture`` data source: serves the contract's examples as if they were real data.

It lets the views be built and tested before the engine's results reach the DAL (gaps G1 to G4).
Documents carry ``source: "fixture"`` so a view can say that it is showing sample data.

The fixtures are stateless: ``submit_ingest`` returns the scenario's example whatever file it is
given, and nothing is stored. Every document is parsed from the contract's JSON on each call, so
callers can never mutate the shared examples, and every document that is rebuilt after filtering
or paging is validated again.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Sequence
from functools import cache
from typing import Any, get_args

from pydantic import BaseModel

from pred_platform.config import DataSource
from pred_platform.contract import models
from pred_platform.contract.errors import ArtifactMissing, InvalidQuery
from pred_platform.contract.loader import parse_as
from pred_platform.contract.resources import read_example
from pred_platform.data.query import (
    DEFAULT_SIZE,
    PageRequest,
    check_choice,
    check_sort,
    contains_text,
    paginate,
    sort_items,
)
from pred_platform.data.repository import ArtifactDownload
from pred_platform.data.scenarios import DEFAULT_SCENARIO, Scenario, get_scenario

_SKU_CLASSES = get_args(models.SkuClass)
_FAMILIES = get_args(models.Family)
_TASK_STATES = get_args(models.EstadoTarea)
_FAMILY_STATES = get_args(models.EstadoFamilia)

_TOPOLOGY_SORT = {"sku_id", "adi", "cv2", "n_positive"}
_SELECTION_SORT = {"sku_id", "valor"}


@cache
def _text(example: str) -> str:
    return read_example(example)


def _load[T: BaseModel](model: type[T], example: str) -> T:
    return parse_as(model, _text(example))


def _with[T: BaseModel](doc: T, **updates: Any) -> T:
    """A copy of ``doc`` with some fields replaced, validated again."""
    return type(doc).model_validate({**dict(doc), **updates})


def _matches(wanted: str, actual: str | None) -> bool:
    """A document without an id (an "unavailable" one) answers for any id."""
    return actual is None or actual == wanted


def sample_csv() -> bytes:
    """A small, deterministic CSV in the engine's four canonical columns (guide 16.7)."""
    lines = ["sku_id,timestamp,demand_qty,lead_time_days"]
    for number in range(1, 5):
        for day in range(1, 4):
            lines.append(f"FIL-000{number},2026-01-0{day},{number * day},7")
    return ("\n".join(lines) + "\n").encode("utf-8")


class FixtureRepository:
    """``DataRepository`` over the examples of the contract, driven by a scenario."""

    source: DataSource = "fixture"

    def __init__(self, scenario: str = DEFAULT_SCENARIO) -> None:
        self.scenario: Scenario = get_scenario(scenario)

    # ----------------------------------------------------------------- capabilities
    def get_capabilities(self) -> models.Capabilities:
        return _load(models.Capabilities, self.scenario.capabilities)

    # ---------------------------------------------------------------------- ingests
    def list_ingests(self, *, page: int = 1, size: int = DEFAULT_SIZE) -> models.IngestList:
        doc = _load(models.IngestList, self.scenario.ingest_list)
        items, paging = paginate(doc.items, PageRequest(page, size))
        return _with(doc, items=items, page=paging)

    def get_ingest_report(self, ingest_id: str) -> models.IngestReport:
        for example in self.scenario.ingest_reports:
            report = _load(models.IngestReport, example)
            if report.ingest_id == ingest_id:
                return report
        raise ArtifactMissing(f"No hay informe de ingesta {ingest_id!r} en el escenario fixture")

    def submit_ingest(
        self, file_name: str, content: bytes, *, confirm_overwrite: bool = False
    ) -> models.IngestReport:
        if not file_name.strip().lower().endswith(".csv"):
            raise InvalidQuery(f"file_name must be a .csv file, got {file_name!r}")
        if not content:
            raise InvalidQuery("content is empty")
        without, with_confirmation = self.scenario.submit_ingest
        return _load(models.IngestReport, with_confirmation if confirm_overwrite else without)

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
        wanted_class = check_choice("sku_class", sku_class, _SKU_CLASSES)
        key_name = check_sort(sort, _TOPOLOGY_SORT)
        request = PageRequest(page, size)
        doc = self._find(
            models.TopologyReport,
            self.scenario.topology_reports,
            ingest_id,
            lambda d: d.ingest_id,
            f"No hay informe de topología para la ingesta {ingest_id!r}",
        )
        rows = [
            row
            for row in doc.items
            if (wanted_class is None or row.sku_class == wanted_class)
            and contains_text(q, row.sku_id)
        ]
        rows = sort_items(rows, lambda row: getattr(row, key_name), descending=descending)
        items, paging = paginate(rows, request)
        # `summary` describes the whole report, not the filtered page.
        return _with(doc, items=items, page=paging)

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
        wanted_state = check_choice("estado", estado, _TASK_STATES)
        wanted_family = check_choice("family", family, _FAMILIES)
        request = PageRequest(page, size)
        doc = self._find(
            models.RunStatus,
            self.scenario.run_status,
            run_id,
            lambda d: d.ejecucion.id if d.ejecucion else None,
            f"No existe la ejecución {run_id!r} en el escenario fixture",
        )
        tasks = [
            task
            for task in doc.tasks
            if (wanted_state is None or task.estado == wanted_state)
            and (not sku or task.sku == sku)
            and (wanted_family is None or task.family == wanted_family)
        ]
        items, paging = paginate(tasks, request)
        # `progress` counts every task of the run, not only the filtered page.
        return _with(doc, tasks=items, page=paging)

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
        wanted_class = check_choice("sku_class", sku_class, _SKU_CLASSES)
        wanted_family = check_choice("family", family, _FAMILIES)
        wanted_state = check_choice("estado", estado, _FAMILY_STATES)
        key_name = check_sort(sort, _SELECTION_SORT)
        request = PageRequest(page, size)
        doc = self._find(
            models.SkuSelectionList,
            (self.scenario.sku_selection_list,),
            run_id,
            lambda d: d.run_id,
            f"No hay selecciones para la ejecución {run_id!r}",
        )

        def keep(row: models.SkuSelectionRow) -> bool:
            if wanted_class is not None and row.sku_class != wanted_class:
                return False
            if not contains_text(q, row.sku_id):
                return False
            if wanted_family is None and wanted_state is None:
                return True
            return any(
                (wanted_family is None or entry.family == wanted_family)
                and (wanted_state is None or entry.estado == wanted_state)
                for entry in row.families
            )

        def best_value(row: models.SkuSelectionRow) -> float | None:
            values = [entry.valor for entry in row.families if entry.valor is not None]
            return min(values) if values else None

        key: Callable[[models.SkuSelectionRow], Any] = (
            (lambda row: row.sku_id) if key_name == "sku_id" else best_value
        )
        rows = sort_items([row for row in doc.items if keep(row)], key, descending=descending)
        items, paging = paginate(rows, request)
        return _with(doc, items=items, page=paging)

    def get_sku_selection(
        self, run_id: str, sku_id: str, *, include_windows: bool = False
    ) -> models.SkuSelection:
        for example in self.scenario.sku_selections:
            data = json.loads(_text(example))
            if data["sku_id"] != sku_id or not _matches(run_id, data.get("run_id")):
                continue
            if not include_windows:
                for entry in data["families"]:
                    if entry.get("walk_forward"):
                        entry["walk_forward"]["windows"] = None
            return parse_as(models.SkuSelection, data)
        raise ArtifactMissing(f"No hay detalle del SKU {sku_id!r} en la ejecución {run_id!r}")

    # -------------------------------------------------------------------- verdicts
    def get_validation_verdicts(self, run_id: str) -> models.ValidationVerdicts:
        return self._find(
            models.ValidationVerdicts,
            (self.scenario.validation_verdicts,),
            run_id,
            lambda d: d.run_id,
            f"No hay veredictos para la ejecución {run_id!r}",
        )

    # -------------------------------------------------------------- synthetic runs
    def list_synthetic_runs(
        self, *, page: int = 1, size: int = DEFAULT_SIZE
    ) -> models.SyntheticList:
        doc = _load(models.SyntheticList, self.scenario.synthetic_list)
        items, paging = paginate(
            [self._consistent_with_download(run) for run in doc.items], PageRequest(page, size)
        )
        return _with(doc, items=items, page=paging)

    def get_synthetic_run(self, run_ref: str) -> models.SyntheticRun:
        for example in self.scenario.synthetic_runs:
            run = _load(models.SyntheticRun, example)
            if run.run_ref == run_ref:
                return self._consistent_with_download(run)
        raise ArtifactMissing(f"No existe la corrida sintética {run_ref!r}")

    def download_synthetic_artifact(self, run_ref: str) -> ArtifactDownload:
        run = self.get_synthetic_run(run_ref)
        return ArtifactDownload.verified(
            run.artifact.file_name, run.artifact.media_type, sample_csv(), run.artifact.sha256
        )

    # --------------------------------------------------------------------- helpers
    @staticmethod
    def _consistent_with_download(run: models.SyntheticRun) -> models.SyntheticRun:
        """Make the run's declared artifact the sample CSV that is actually downloaded.

        The example's hash and its 52 000 rows are illustrative and match no real file; here the
        document and the download must agree, so the declared hash and row count are those of
        ``sample_csv()``.
        """
        content = sample_csv()
        digest = hashlib.sha256(content).hexdigest()
        rows = content.count(b"\n") - 1
        data = run.model_dump(mode="json")
        data["artifact"].update(sha256=digest, rows=rows)
        data["log"].update(artefacto_sha256=digest, row_count=rows)
        return models.SyntheticRun.model_validate(data)

    @staticmethod
    def _find[T: BaseModel](
        model: type[T],
        examples: Sequence[str],
        wanted: str,
        identity: Callable[[T], str | None],
        not_found: str,
    ) -> T:
        for example in examples:
            doc = _load(model, example)
            if _matches(wanted, identity(doc)):
                return doc
        raise ArtifactMissing(not_found)
