"""Close a run: take the engine's final files as the truth and settle every task and the run.

``corrida.json`` and ``unidades.jsonl`` are written when the engine ends, so they win over what
the live progress stream said. Anything still in flight is closed: a unit cut off is ``fallida``
and one that never started is ``no_ejecutable``. Then the selection per SKU the engine left in
``seleccion.jsonl`` (G4) is imported.
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any

from pred_platform.dal import ejecuciones as dal
from pred_platform.dal.ejecuciones import Ejecucion
from pred_platform.worker.engine import MODELO_POR_FAMILIA, log_tail, model_id
from pred_platform.worker.errores import error_info, exclusion_info, platform_error
from pred_platform.worker.seleccion import import_selection


def _read_units(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    units: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                value = json.loads(line)
            except ValueError:
                continue
            if isinstance(value, dict):
                units.append(value)
    return units


def finalize(
    conn: sqlite3.Connection,
    run: Ejecucion,
    *,
    run_dir: Path,
    returncode: int | None,
    corte: str,
    families: list[str],
    log_path: Path,
    previous_summary: os.stat_result | None = None,
) -> str:
    """Settle ``run`` after its engine process ended and return its final ``estado``."""
    corrida_path = run_dir / "corrida.json"
    interrupted = platform_error(
        "pipeline.stage_failed", "La unidad quedó a medias: la corrida terminó antes de acabarla."
    )
    skipped = platform_error(
        "pipeline.stage_failed", "La unidad no llegó a empezar: una etapa anterior falló."
    )
    current_summary = corrida_path.stat() if corrida_path.is_file() else None
    unchanged = (
        current_summary is not None
        and previous_summary is not None
        and (current_summary.st_ino, current_summary.st_mtime_ns, current_summary.st_size)
        == (previous_summary.st_ino, previous_summary.st_mtime_ns, previous_summary.st_size)
    )
    if current_summary is None or unchanged:
        detail = log_tail(log_path) or (
            f"El motor terminó con código {returncode} sin publicar corrida.json de este intento."
        )
        dal.close_open_tasks(conn, run.id, interrupted=interrupted, skipped=skipped)
        dal.finish_run(
            conn,
            run.id,
            estado="detenida",
            error=platform_error("platform.artifact_missing", detail),
        )
        return "detenida"

    corrida = json.loads(corrida_path.read_text(encoding="utf-8"))
    summary: dict[str, Any] = corrida.get("summary", {})

    # The unit trace is final: reapplying it is idempotent and corrects any missed event.
    for unit in _read_units(run_dir / "unidades.jsonl"):
        modelo = model_id(str(unit["family"]), str(unit.get("model") or unit["family"]))
        stage, sku = str(unit.get("stage", "L2")), str(unit["sku_id"])
        if unit.get("started_at"):
            dal.task_started(
                conn,
                run.id,
                sku=sku,
                modelo=modelo,
                corte=corte,
                seed=run.seed,
                iniciada_en=str(unit["started_at"]),
                running=False,
            )
        completed = unit.get("state") == "completada"
        dal.task_stage_finished(
            conn,
            run.id,
            sku=sku,
            modelo=modelo,
            corte=corte,
            seed=run.seed,
            stage=stage,
            completed=completed,
            seconds=unit.get("seconds"),
            finished_at=str(unit.get("finished_at") or ""),
            error=None if completed else error_info(stage, unit.get("error"), unit.get("code")),
        )
    for exclusion in summary.get("exclusions", []):
        for family in families:
            dal.task_excluded(
                conn,
                run.id,
                sku=str(exclusion["sku_id"]),
                modelo=model_id(family, MODELO_POR_FAMILIA.get(family, family)),
                corte=corte,
                seed=run.seed,
                error=exclusion_info(str(exclusion.get("cause", "")), exclusion.get("code")),
            )

    dal.close_open_tasks(conn, run.id, interrupted=interrupted, skipped=skipped)
    # The selection per SKU (G4) links its families to the tasks settled just above. A bad line
    # only loses its SKU: it never changes how the run ended.
    import_selection(conn, run, run_dir=run_dir, corte=corte)
    counts = dal.task_counts(conn, run.id)
    reserve = summary.get("reserve")
    failed = [s for s in summary.get("stages", []) if s.get("state") == "failed"]

    if failed:
        stage = failed[0]
        return _stop(
            conn,
            run,
            reserve,
            platform_error(
                "pipeline.stage_failed",
                f"La etapa {stage.get('id')} falló: {stage.get('message', '')}".strip(),
            ),
        )
    if not counts:
        return _stop(
            conn,
            run,
            reserve,
            platform_error("pipeline.stage_failed", "La corrida no produjo ninguna tarea."),
        )
    estado = (
        "completada_con_fallos"
        if counts.get("fallida", 0) or counts.get("no_ejecutable", 0)
        else "completada"
    )
    dal.finish_run(conn, run.id, estado=estado, reserva=reserve)
    return estado


def _stop(
    conn: sqlite3.Connection, run: Ejecucion, reserve: dict[str, Any] | None, error: dict[str, Any]
) -> str:
    dal.finish_run(conn, run.id, estado="detenida", error=error, reserva=reserve)
    return "detenida"
