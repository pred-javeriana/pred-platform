"""A stand-in for ``pred-engine run`` used by the worker tests.

It accepts the same command line the worker builds and leaves the same files in the run folder
(``progreso.jsonl``, ``unidades.jsonl``, ``corrida.json``), so the worker is tested without the
real engine, its heavy dependencies or its running time. The scenario comes from the
``FAKE_ENGINE_SCENARIO`` environment variable and the SKUs from ``FAKE_ENGINE_SKUS``:

    ok            every unit completes in L2 and L3 (exit 7, like the real engine without L4)
    unit_fails    FIL-0002 / ml fails in L2
    excluded      FIL-0003 is excluded before L2
    crash         dies after the first unit started, without corrida.json
    stage_fails   L3 fails as a whole: no L3 unit, corrida.json says so (exit 1)
    slow          plans and starts one unit, then waits until it is killed
    slow_ok       like ok but each unit takes ~0.3 s, so a poller can see it in flight
    future_schema writes a progress stream of an unsupported major version
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

MODELS = {"classical": "sarima", "ml": "lightgbm", "dl": "mlp", "foundation": "chronos2"}
RESERVE = {
    "fraction": 0.2,
    "t_star": "2026-03-13",
    "first_reserved": "2026-03-14",
    "last_observed": "2026-03-31",
    "reserved_days": 18,
}


def _now() -> str:
    return datetime.now(UTC).isoformat()


class Stream:
    def __init__(self, path: Path) -> None:
        self.path = path

    def emit(self, event: str, **data: Any) -> None:
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"event": event, "t": _now(), **data}) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command")
    parser.add_argument("--parquet")
    parser.add_argument("--data-root", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--families", nargs="+", default=["classical", "ml", "dl"])
    parser.add_argument("--trials")
    parser.add_argument("--min-train")
    parser.add_argument("--horizon")
    parser.add_argument("--step")
    parser.add_argument("--seasonality")
    parser.add_argument("--metric")
    parser.add_argument("--seed")
    parser.add_argument("--workers")
    args = parser.parse_args()

    scenario = os.environ.get("FAKE_ENGINE_SCENARIO", "ok")
    skus = os.environ.get("FAKE_ENGINE_SKUS", "FIL-0001,FIL-0002,FIL-0003").split(",")
    folder = Path(args.data_root) / "runs" / args.run_id
    folder.mkdir(parents=True, exist_ok=True)
    stream = Stream(folder / "progreso.jsonl")
    attempt = (
        1 + sum(1 for line in stream.path.read_text().splitlines() if '"run_started"' in line)
        if stream.path.exists()
        else 1
    )
    schema = "pred-engine.progreso/2" if scenario == "future_schema" else "pred-engine.progreso/1"
    stream.emit(
        "run_started", schema=schema, run_id=args.run_id, attempt=attempt, workers=args.workers
    )

    excluded = ["FIL-0003"] if scenario == "excluded" else []
    runnable = [s for s in skus if s not in excluded]
    delay = 0.3 if scenario == "slow_ok" else 0.0
    units = [(s, f) for s in runnable for f in args.families]
    unit_rows: list[dict[str, Any]] = []

    stream.emit("stage_started", stage="L1")
    stream.emit("stage_finished", stage="L1", state="completed")
    stream.emit("stage_started", stage="L2")
    for sku in excluded:
        stream.emit("excluded", sku_id=sku, cause="la serie termina antes de t*: sin reserva")
    stream.emit(
        "planned",
        stage="L2",
        units=[{"sku_id": s, "family": f, "model": MODELS[f]} for s, f in units],
    )

    def run_unit(stage: str, sku: str, family: str, *, fail: bool = False) -> None:
        label = {"stage": stage, "sku_id": sku, "family": family, "model": MODELS[family]}
        started = _now()
        stream.emit("unit_started", **label, pid=os.getpid(), started_at=started)
        time.sleep(delay)
        state = "fallida" if fail else "completada"
        error = "AjusteModeloError: el modelo no converge" if fail else None
        finished = _now()
        row = {
            **label,
            "state": state,
            "pid": os.getpid(),
            "started_at": started,
            "finished_at": finished,
            "seconds": 1.5 if stage == "L2" else 0.5,
            "cpu_s": 1.0,
            "error": error,
        }
        stream.emit("unit_finished", **row)
        unit_rows.append(row)

    if scenario == "slow":
        first_sku, first_family = units[0]
        stream.emit("stage_started", stage="L2")
        label = {"stage": "L2", "sku_id": first_sku, "family": first_family}
        stream.emit(
            "unit_started",
            **label,
            model=MODELS[first_family],
            pid=os.getpid(),
            started_at=_now(),
        )
        time.sleep(60)
        return 0

    for index, (sku, family) in enumerate(units):
        if scenario == "crash" and index == 1:
            os._exit(3)
        run_unit(
            "L2", sku, family, fail=scenario == "unit_fails" and (sku, family) == ("FIL-0002", "ml")
        )

    ok_units = [(u["sku_id"], u["family"]) for u in unit_rows if u["state"] == "completada"]
    stages = [{"id": "L1", "state": "completed"}, {"id": "L2", "state": "completed"}]
    if scenario == "stage_fails":
        stream.emit("stage_started", stage="L3")
        stream.emit("stage_finished", stage="L3", state="failed", message="ninguna unidad evaluó")
        stages.append({"id": "L3", "state": "failed", "message": "ninguna unidad evaluó"})
    else:
        stream.emit("stage_finished", stage="L2", state="completed")
        stream.emit("stage_started", stage="L3")
        stream.emit(
            "planned",
            stage="L3",
            units=[{"sku_id": s, "family": f, "model": MODELS[f]} for s, f in ok_units],
        )
        for sku, family in ok_units:
            run_unit("L3", sku, family)
        stream.emit("stage_finished", stage="L3", state="completed")
        stages.append({"id": "L3", "state": "completed"})
    stages.append({"id": "L4", "state": "blocked"})

    (folder / "unidades.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in unit_rows), encoding="utf-8"
    )
    summary = {
        "complete": False,
        "blocked_stage": "L4",
        "stages": stages,
        "reserve": RESERVE,
        "exclusions": [
            {"sku_id": s, "cause": "la serie termina antes de t*: sin reserva"} for s in excluded
        ],
        "failures": [
            {"stage": r["stage"], "sku_id": r["sku_id"], "family": r["family"], "error": r["error"]}
            for r in unit_rows
            if r["state"] == "fallida"
        ],
    }
    (folder / "corrida.json").write_text(
        json.dumps({"schema": "pred-engine.corrida/1", "run_id": args.run_id, "summary": summary}),
        encoding="utf-8",
    )
    failed = scenario == "stage_fails"
    stream.emit(
        "run_finished",
        state="failed" if failed else "completed",
        exit_code=1 if failed else 7,
        failed_stage="L3" if failed else None,
        blocked_stage="L4",
    )
    return 1 if failed else 7


if __name__ == "__main__":
    sys.exit(main())
