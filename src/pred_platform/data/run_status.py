"""Local display fields for run status; the published read contract stays unchanged."""

from __future__ import annotations

import json
from collections import OrderedDict
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import Any

from pydantic import AwareDatetime, BaseModel, Field

from pred_platform.contract.models import RunStatus

STAGES = ("L1", "L2", "L3", "L4")


class StageTiming(BaseModel):
    """El inicio y el fin de una etapa del motor (L1 a L4) en un intento de la corrida.

    ``finished_at`` queda vacío si la etapa no terminó o si su fin es anterior a su inicio (un
    reloj que retrocede); ``finish_state`` es el estado final que escribió el motor. Solo una
    etapa ``completed`` publica.
    """

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


class FamilyTaskState(BaseModel):
    estado: str
    exclusion_code: str | None = None
    sku_class: str


class DisplayRunStatus(RunStatus):
    """Presentation evidence from the same status read, not a new contract document."""

    stage_timings: list[StageTiming] = Field(default_factory=list, exclude=True)
    last_stage: str | None = Field(default=None, exclude=True)
    # True when a retry was stopped while queued: the log only holds the attempt that ended
    # before it, and none of that describes the retry.
    stopped_before_start: bool = Field(default=False, exclude=True)
    family_states: dict[str, dict[str, FamilyTaskState]] = Field(default_factory=dict, exclude=True)
    # The stored attempt (``ejecuciones.intento``) from the same row: more than 1 means resumed.
    attempt: int | None = Field(default=None, exclude=True)


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


type ProgressSummary = tuple[str | None, int | None, int | None, list[StageTiming], bool]

# The summary of the current attempt, and every stage of every attempt in log order.
type _Progress = tuple[ProgressSummary, tuple[StageTiming, ...]]

_EMPTY: _Progress = ((None, None, None, [], False), ())
_PROGRESS_CACHE: OrderedDict[Path, tuple[tuple[int, int, int], _Progress]] = OrderedDict()
_PROGRESS_CACHE_LOCK = Lock()


def read_run_progress(
    path: Path,
) -> ProgressSummary:
    """Read one log once per file version, shared by the page's two status reads.

    A retry begins at the last run_started. A repeated planned event replaces the old plan.
    Missing timestamps never become invented durations. Keep one small summary per file, so
    successive live versions cannot evict unchanged completed files from the eight-file cache.
    """
    return _progress(path)[0]


def read_stage_attempts(path: Path) -> tuple[StageTiming, ...]:
    """Every stage every attempt of the log opened, in log order, from the same cached read.

    The stages of the current attempt are the ones :func:`read_run_progress` returns, with the
    same rules: an end before the start is not kept and ``finish_state`` says how it ended. The
    publication date of Results needs the earlier attempts too, to date an older publication by
    the L4 that created it.
    """
    return _progress(path)[1]


def _progress(path: Path) -> _Progress:
    try:
        stat = path.stat()
        version = (stat.st_ino, stat.st_mtime_ns, stat.st_size)
        with _PROGRESS_CACHE_LOCK:
            cached = _PROGRESS_CACHE.get(path)
            if cached is not None and cached[0] == version:
                _PROGRESS_CACHE.move_to_end(path)
                return cached[1]
        progress = _read_progress(path)
        with _PROGRESS_CACHE_LOCK:
            _PROGRESS_CACHE[path] = version, progress
            _PROGRESS_CACHE.move_to_end(path)
            if len(_PROGRESS_CACHE) > 8:
                _PROGRESS_CACHE.popitem(last=False)
        return progress
    except OSError:
        return _EMPTY


def _read_progress(path: Path) -> _Progress:
    stage = None
    plan = None
    finished = set()
    timings: dict[str, StageTiming] = {}
    # Every attempt's stages; the open one of each code may belong to an earlier attempt.
    attempts: list[StageTiming] = []
    open_stage: dict[str, StageTiming] = {}
    ended = False
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
            ended = False
            stage, plan, finished, timings = None, None, set(), {}
        elif kind == "run_finished":
            ended = True
        elif kind == "stage_started" and code in STAGES:
            if stage != code:
                stage, plan, finished = code, None, set()
            stamp = _timestamp(event.get("t"))
            if stamp is not None:
                timings[code] = open_stage[code] = StageTiming(code=code, started_at=stamp)
                attempts.append(timings[code])
        elif kind == "stage_finished" and isinstance(code, str):
            timing = timings[code] if code in timings else open_stage.get(code)
            if timing is None:
                continue
            open_stage.pop(code, None)
            state = event.get("state")
            timing.finish_state = state if isinstance(state, str) else None
            stamp = _timestamp(event.get("t"))
            if stamp is not None and stamp >= timing.started_at:
                timing.finished_at = stamp
        elif kind == "planned" and code == stage:
            units = event.get("units")
            if isinstance(units, list):
                plan = {_unit(unit) for unit in units if isinstance(unit, dict)}
                finished = set()
        elif kind == "unit_finished" and code == stage and plan is not None:
            finished.add(_unit(event))
    ordered = [timings[code] for code in STAGES if code in timings]
    if plan is None:
        return (stage, None, None, ordered, ended), tuple(attempts)
    skus = {sku for sku, _, _ in plan}
    pending = {sku for sku, _, _ in plan - finished}
    return (stage, len(skus), len(skus - pending), ordered, ended), tuple(attempts)
