"""Follow the engine's ``progreso.jsonl`` and apply its events to the DAL.

Applying an event is idempotent: replaying the whole file (after a restart) leaves the same rows.
The state of a task follows the unit: ``planned`` -> ``pendiente``, ``unit_started`` ->
``ejecutando``, a unit finished in L3 -> ``finalizo_m2`` (M2 is L2 and L3), a unit finished in
L4 -> ``exitosa`` (M3 stored its candidate), a unit that failed -> ``fallida``, and a skipped
SKU -> ``no_ejecutable`` (section 7.4 and 18 of the data contract), except a unit of it that
already failed. A start never takes a task in ``finalizo_m2`` or ``exitosa`` back to
``ejecutando``. When the run closes, ``worker/importacion.py`` settles every task that finished
L3 as ``exitosa`` if M3 was published or L4 was blocked, even without L4 events.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from pred_platform.dal import ejecuciones as dal
from pred_platform.dal.ejecuciones import Ejecucion
from pred_platform.worker.engine import FAMILIES_BY_SKU_CLASS, MODELO_POR_FAMILIA, model_id
from pred_platform.worker.errores import error_info, exclusion_info

SUPPORTED_MAJOR = 1


def read_sku_classes(run_dir: Path) -> dict[str, str]:
    """Read known demand classes from the engine's L1 topology artifact."""
    try:
        topology = json.loads((run_dir / "topologia.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(topology, dict) or topology.get("schema") != "pred-engine.topologia/1":
        return {}
    metrics = topology.get("metrics")
    if not isinstance(metrics, list):
        return {}
    return {
        row["sku_id"]: row["sku_class"]
        for row in metrics
        if isinstance(row, dict)
        and isinstance(row.get("sku_id"), str)
        and isinstance(row.get("sku_class"), str)
        and row["sku_class"] in FAMILIES_BY_SKU_CLASS
    }


def exclude_sku(
    conn: sqlite3.Connection,
    run: Ejecucion,
    *,
    sku: str,
    corte: str,
    families: list[str],
    error: dict[str, Any],
    sku_class: str | None = None,
) -> None:
    """Settle only configured families routed to the SKU's known demand class.

    An older engine without topology keeps the historical per-run-family count. Progress
    defers exclusions until topology is available, so it cannot create phantom tasks first.
    A unit that failed keeps its state and error through ``task_excluded``.
    """
    admitted = FAMILIES_BY_SKU_CLASS[sku_class] if sku_class else families
    for family in families:
        if family not in admitted:
            continue
        dal.task_excluded(
            conn,
            run.id,
            sku=sku,
            modelo=model_id(family, MODELO_POR_FAMILIA.get(family, family)),
            corte=corte,
            seed=run.seed,
            error=error,
        )


class ProgressFollower:
    """Reads new lines of one run's progress file and applies them."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        run: Ejecucion,
        path: Path,
        *,
        corte: str,
        skip_history: bool = False,
    ) -> None:
        self._conn = conn
        self._run = run
        self._path = path
        self._corte = corte
        self._offset = path.stat().st_size if skip_history and path.exists() else 0
        self._classes: dict[str, str] = {}
        self.finished: dict[str, Any] | None = None
        self.failed_stages: list[str] = []

    @property
    def families(self) -> list[str]:
        configured = self._run.parametros.get("families") or list(MODELO_POR_FAMILIA)
        return [str(f) for f in configured]

    def poll(self) -> int:
        """Apply the complete lines written since the last call. Returns how many."""
        try:
            with self._path.open("rb") as handle:
                handle.seek(self._offset)
                chunk = handle.read()
        except FileNotFoundError:
            return 0
        end = chunk.rfind(b"\n")
        if end < 0:
            return 0
        self._offset += end + 1
        if not self._classes:
            self._classes = read_sku_classes(self._path.parent)
        applied = 0
        for raw in chunk[: end + 1].splitlines():
            if not raw.strip():
                continue
            try:
                event = json.loads(raw)
            except ValueError:
                continue  # a torn or foreign line: progress is best-effort observability
            if isinstance(event, dict):
                self._apply(event)
                applied += 1
        return applied

    # ----------------------------------------------------------------------- events
    def _apply(self, event: dict[str, Any]) -> None:
        kind = event.get("event")
        run, corte = self._run, self._corte
        if kind == "run_started":
            major = str(event.get("schema", "")).rpartition("/")[2]
            if major.isdigit() and int(major) != SUPPORTED_MAJOR:
                raise ValueError(f"progreso de versión mayor {major} no soportada")
        elif kind == "planned":
            units = [
                (str(u["sku_id"]), model_id(str(u["family"]), str(u["model"])))
                for u in event.get("units", [])
            ]
            dal.plan_tasks(self._conn, run.id, corte=corte, seed=run.seed, units=units)
        elif kind == "excluded":
            sku = str(event["sku_id"])
            sku_class = self._classes.get(sku)
            if sku_class is None:
                # The engine writes topology at publication; finalize replays its exclusions.
                return
            exclude_sku(
                self._conn,
                run,
                sku=sku,
                corte=corte,
                families=self.families,
                sku_class=sku_class,
                error=exclusion_info(
                    str(event.get("cause", "")), event.get("code"), event.get("facts")
                ),
            )
        elif kind == "unit_started":
            dal.task_started(
                self._conn,
                run.id,
                sku=str(event["sku_id"]),
                modelo=model_id(str(event["family"]), str(event["model"])),
                corte=corte,
                seed=run.seed,
                iniciada_en=str(event.get("started_at") or event.get("t")),
            )
        elif kind == "unit_finished":
            completed = event.get("state") == "completada"
            stage = str(event.get("stage", "L2"))
            dal.task_stage_finished(
                self._conn,
                run.id,
                sku=str(event["sku_id"]),
                modelo=model_id(str(event["family"]), str(event["model"])),
                corte=corte,
                seed=run.seed,
                stage=stage,
                completed=completed,
                seconds=event.get("seconds"),
                finished_at=str(event.get("finished_at") or event.get("t")),
                error=None
                if completed
                else error_info(stage, event.get("error"), event.get("code"), event.get("facts")),
            )
        elif kind == "stage_finished" and event.get("state") == "failed":
            self.failed_stages.append(str(event.get("stage")))
        elif kind == "run_finished":
            self.finished = event
