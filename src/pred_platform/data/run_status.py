"""Local display fields for run status; the published read contract stays unchanged."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import AwareDatetime, BaseModel, Field

from pred_platform.contract.models import RunStatus

STAGES = ("L1", "L2", "L3", "L4")


class StageTiming(BaseModel):
    code: str
    started_at: AwareDatetime
    finished_at: AwareDatetime | None = None
    finish_state: str | None = None


class StageReference(BaseModel):
    id: str
    seed: int
    timings: list[StageTiming]


def comparison_key(ingest_id: str | None, settings: Mapping[str, Any]) -> tuple[object, ...]:
    """The same data and evaluation settings, independent of seed and worker count."""
    return (
        ingest_id,
        tuple(sorted(settings.get("families") or ())),
        *(
            settings.get(field)
            for field in ("metric", "min_train", "horizon", "step", "seasonality")
        ),
    )


class TaskCause(BaseModel):
    code: str | None
    family: str
    count: int = Field(ge=0)


class CauseSnapshot(BaseModel):
    read_at: AwareDatetime
    causes: list[TaskCause]


class DisplayRunStatus(RunStatus):
    """Presentation evidence from the same status read, not a new contract document."""

    stage_timings: list[StageTiming] = Field(default_factory=list, exclude=True)
    last_stage: str | None = Field(default=None, exclude=True)


def _timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        stamp = datetime.fromisoformat(value)
    except ValueError:
        return None
    return stamp if stamp.tzinfo is not None else None


def _unit(event: dict[str, Any]) -> tuple[str | None, str | None, str | None]:
    sku, family, model = event.get("sku_id"), event.get("family"), event.get("model")
    return (
        sku if isinstance(sku, str) else None,
        family if isinstance(family, str) else None,
        model if isinstance(model, str) else None,
    )


def read_run_progress(
    path: Path,
) -> tuple[str | None, int | None, int | None, list[StageTiming]]:
    """Read one log once per file version, shared by the page's two status reads.

    A retry begins at the last run_started. A repeated planned event replaces the old plan.
    Missing timestamps never become invented durations. Cache only the small parsed summary.
    """
    try:
        stat = path.stat()
        return _read_progress(path, stat.st_ino, stat.st_mtime_ns, stat.st_size)
    except OSError:
        return None, None, None, []


@lru_cache(maxsize=8)
def _read_progress(
    path: Path, inode: int, modified: int, size: int
) -> tuple[str | None, int | None, int | None, list[StageTiming]]:
    stage = None
    plan = None
    finished = set()
    timings: dict[str, StageTiming] = {}
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    for line in lines:
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        kind = event.get("event")
        code = event.get("stage")
        if kind == "run_started":
            stage, plan, finished, timings = None, None, set(), {}
        elif kind == "stage_started" and code in STAGES:
            if stage != code:
                stage, plan, finished = code, None, set()
            stamp = _timestamp(event.get("t"))
            if stamp is not None:
                timings[code] = StageTiming(code=code, started_at=stamp)
        elif kind == "stage_finished" and isinstance(code, str) and code in timings:
            state = event.get("state")
            timings[code].finish_state = state if isinstance(state, str) else None
            stamp = _timestamp(event.get("t"))
            if stamp is not None and stamp >= timings[code].started_at:
                timings[code].finished_at = stamp
        elif kind == "planned" and code == stage:
            units = event.get("units")
            if isinstance(units, list):
                plan = {_unit(unit) for unit in units if isinstance(unit, dict)}
                finished = set()
        elif kind == "unit_finished" and code == stage and plan is not None:
            finished.add(_unit(event))
    ordered = [timings[code] for code in STAGES if code in timings]
    if plan is None:
        return stage, None, None, ordered
    skus = {sku for sku, _, _ in plan}
    pending = {sku for sku, _, _ in plan - finished}
    return stage, len(skus), len(skus - pending), ordered
