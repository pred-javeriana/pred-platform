"""Command line of the worker.

python -m pred_platform.worker                         # follow the queue until stopped
python -m pred_platform.worker --once                  # run what is queued, then exit
python -m pred_platform.worker --launch --ingest <sha256> [--once]   # queue (and run) a run
python -m pred_platform.worker --status                # list runs and their task counts
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
import threading
from collections.abc import Sequence
from contextlib import closing
from pathlib import Path

from pred_platform.config import Settings
from pred_platform.dal import ejecuciones as dal
from pred_platform.dal.importacion import import_reports
from pred_platform.dal.migrate import MigrationError, migrate
from pred_platform.worker.bucle import Worker
from pred_platform.worker.service import LaunchSettings, RunRequestError, enqueue_run


def _status(db_path: Path) -> int:
    if not db_path.is_file():
        print(f"{db_path}: no existe la base de datos")
        return 0
    with closing(sqlite3.connect(db_path)) as conn:
        runs = conn.execute(
            "SELECT id, run_id, estado, intento FROM ejecuciones ORDER BY id"
        ).fetchall()
        if not runs:
            print("No hay ejecuciones.")
        for ejecucion_id, run_id, estado, intento in runs:
            counts = dal.task_counts(conn, ejecucion_id)
            detail = ", ".join(f"{n} {k}" for k, n in sorted(counts.items())) or "sin tareas"
            print(f"#{ejecucion_id} {run_id}  {estado}  (intento {intento})  {detail}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m pred_platform.worker", description="PRED run worker."
    )
    parser.add_argument("--once", action="store_true", help="run the queued runs and exit")
    parser.add_argument("--status", action="store_true", help="list runs; change nothing")
    parser.add_argument("--launch", action="store_true", help="queue a run for --ingest")
    parser.add_argument("--ingest", help="SHA-256 of an accepted ingestion (with --launch)")
    defaults = LaunchSettings()
    parser.add_argument("--min-train", type=int, default=defaults.min_train)
    parser.add_argument("--horizon", type=int, default=defaults.horizon)
    parser.add_argument("--step", type=int, default=defaults.step)
    parser.add_argument("--seasonality", type=int, default=defaults.seasonality)
    parser.add_argument("--metric", default=defaults.metric)
    parser.add_argument("--families", nargs="+", default=list(defaults.families))
    parser.add_argument("--trials", type=int, default=None)
    parser.add_argument("--seed", type=int, default=defaults.seed)
    parser.add_argument("--workers", type=int, default=defaults.workers)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s")
    try:
        settings = Settings.from_env()
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.status:
        return _status(settings.db_path)

    try:
        migrate(settings.db_path)
        import_reports(settings.db_path, settings.data_root)
    except MigrationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.launch:
        if not args.ingest:
            print("error: --launch necesita --ingest <sha256>", file=sys.stderr)
            return 2
        try:
            launch = LaunchSettings(
                min_train=args.min_train,
                horizon=args.horizon,
                step=args.step,
                seasonality=args.seasonality,
                metric=args.metric,
                families=tuple(args.families),
                trials=args.trials,
                seed=args.seed,
                workers=args.workers,
            )
            queued = enqueue_run(settings.db_path, args.ingest, launch)
        except (ValueError, RunRequestError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        verb = "en cola" if queued.queued else "ya existía"
        print(f"ejecución #{queued.ejecucion_id} ({queued.run_id}): {verb}")
        if not args.once:
            return 0

    worker = Worker(settings)
    if args.once:
        worker.drain()
        return _status(settings.db_path)

    stop = threading.Event()
    try:
        worker.run_forever(stop)
    except KeyboardInterrupt:
        stop.set()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
