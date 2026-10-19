"""Follow the engine's ``progreso.jsonl`` and apply its events to the DAL.

Applying an event is idempotent: replaying the whole file (after a restart) leaves the same rows.
The state of a task follows the unit: ``planned`` -> ``pendiente``, ``unit_started`` ->
``ejecutando``, a unit finished in L3 -> ``exitosa``, a unit that failed -> ``fallida``, and a
skipped SKU -> ``no_ejecutable`` (section 7.4 and 18 of the data contract).
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from pred_platform.dal import ejecuciones as dal
from pred_platform.dal.ejecuciones import Ejecucion
from pred_platform.worker.engine import MODELO_POR_FAMILIA, model_id
from pred_platform.worker.errores import error_info, exclusion_info

SUPPORTED_MAJOR = 1


class ProgressFollower:
    """Reads new lines of one run's progress file and applies them."""

    def __init__(self, conn: sqlite3.Connection, run: Ejecucion, path: Path, *, corte: str) -> None:
        self._conn = conn
        self._run = run
        self._path = path
        self._corte = corte
        self._offset = 0
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
            sku, cause = str(event["sku_id"]), str(event.get("cause", ""))
            code = event.get("code")
            for family in self.families:
                dal.task_excluded(
                    self._conn,
                    run.id,
                    sku=sku,
                    modelo=model_id(family, MODELO_POR_FAMILIA.get(family, family)),
                    corte=corte,
                    seed=run.seed,
                    error=exclusion_info(cause, code),
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
                else error_info(stage, event.get("error"), event.get("code")),
            )
        elif kind == "stage_finished" and event.get("state") == "failed":
            self.failed_stages.append(str(event.get("stage")))
        elif kind == "run_finished":
            self.finished = event
