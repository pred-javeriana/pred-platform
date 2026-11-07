"""Read-only stage references, requested at page load and never by the polling route."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from time import monotonic

from pred_platform.dal import lectura
from pred_platform.data.run_status import StageReference, comparison_key, read_run_progress


def read_stage_references(db_path: Path, data_root: Path, run_id: str) -> list[StageReference]:
    return _references(db_path, data_root, run_id, int(monotonic() // 60))


@lru_cache(maxsize=16)
def _references(db_path: Path, data_root: Path, run_id: str, minute: int) -> list[StageReference]:
    runs = lectura.list_runs(db_path)
    current = next((run for run in runs if str(run.id) == run_id or run.run_id == run_id), None)
    if current is None:
        return []
    identity = comparison_key(current.ingest_sha256, json.loads(current.parametros))
    by_seed = {}
    for run in runs:  # newest first, one completed earlier reference per seed
        if (
            run.id >= current.id
            or run.estado not in ("completada", "completada_con_fallos")
            or not run.ruta_corrida
            or comparison_key(run.ingest_sha256, json.loads(run.parametros)) != identity
        ):
            continue
        by_seed.setdefault(run.seed, run)
    selected = sorted(by_seed.values(), key=lambda run: run.id, reverse=True)[:2]
    references = []
    for run in sorted(selected, key=lambda run: run.seed):
        _, _, _, timings = read_run_progress(data_root / run.ruta_corrida / "progreso.jsonl")
        if timings:
            references.append(StageReference(id=str(run.id), seed=run.seed, timings=timings))
    return references
