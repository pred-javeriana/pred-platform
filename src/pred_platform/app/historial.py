"""Duraciones publicadas de corridas anteriores, nunca estimaciones de una corrida nueva."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from pred_platform.app.presentation import STAGE_ORDER
from pred_platform.dal.lectura import RunDetail


def timestamp(value: str | None) -> datetime | None:
    """Una fecha guardada válida o ausencia, sin atribuirle una zona horaria inventada."""
    try:
        return datetime.fromisoformat(value) if value else None
    except ValueError:
        return None


@dataclass(frozen=True, slots=True)
class StageDuration:
    started: datetime
    finished: datetime

    @property
    def seconds(self) -> float:
        return (self.finished - self.started).total_seconds()


@dataclass(frozen=True, slots=True)
class EarlierRun:
    run: RunDetail
    stages: dict[str, StageDuration]

    @property
    def finished(self) -> datetime | None:
        return timestamp(self.run.finalizada_en)


def stage_durations(path: Path) -> dict[str, StageDuration]:
    """Lee solo eventos de etapa. Un archivo ausente, incompleto o roto no inventa tiempos.

    Una reanudación empieza otro intento; solo se muestran los pares de inicio y fin que ese
    intento publicó. Se lee línea a línea para no cargar los eventos de todos los productos.
    """
    starts: dict[str, datetime] = {}
    durations: dict[str, StageDuration] = {}
    try:
        with path.open(encoding="utf-8", errors="replace") as stream:
            for line in stream:
                if not any(
                    key in line for key in ("stage_started", "stage_finished", "run_started")
                ):
                    continue
                try:
                    event = json.loads(line)
                    if not isinstance(event, dict):
                        continue
                    kind, stage = event.get("event"), event.get("stage")
                    if kind == "run_started":
                        starts.clear()
                        durations.clear()
                        continue
                    if stage not in STAGE_ORDER:
                        continue
                    stamp = datetime.fromisoformat(event["t"])
                    if stamp.tzinfo is None:
                        continue
                    if kind == "stage_started":
                        starts[stage] = stamp
                        durations.pop(stage, None)
                    elif kind == "stage_finished" and stage in starts and stamp >= starts[stage]:
                        durations[stage] = StageDuration(starts[stage], stamp)
                except (ValueError, KeyError, TypeError):
                    continue
    except OSError:
        return {}
    return durations


def earlier_runs(details: list[RunDetail], ingest_id: str, data_root: Path) -> list[EarlierRun]:
    """Las dos últimas corridas terminadas sobre estos datos, con o sin traza de etapas."""
    matching = [
        run
        for run in details
        if run.ingest_sha256 == ingest_id
        and run.estado in ("completada", "completada_con_fallos")
        and run.finalizada_en is not None
    ][:2]
    return [
        EarlierRun(
            run,
            stage_durations(data_root / run.ruta_corrida / "progreso.jsonl")
            if run.ruta_corrida
            else {},
        )
        for run in reversed(matching)
    ]
