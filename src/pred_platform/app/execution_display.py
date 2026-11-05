"""Progress and cause presentation from the status already read for step 4."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pred_platform.app.ejecucion import FINISHED_STATES, STAGES, error_action
from pred_platform.app.presentation import family_label, format_duration, stage_term
from pred_platform.app.seleccion import error_title
from pred_platform.contract.models import RunStatus
from pred_platform.data.run_status import DisplayRunStatus


def execution_display(status: RunStatus | None, now: datetime) -> dict[str, Any]:
    if status is None or status.ejecucion is None:
        return {"live_snapshot": None, "stage_rows": [], "cause_groups": []}
    run, progress = status.ejecucion, status.progress
    timings = status.stage_timings if isinstance(status, DisplayRunStatus) else []
    causes = status.task_causes if isinstance(status, DisplayRunStatus) else []
    by_code = {item.code: item for item in timings}
    codes = [code for code, _ in STAGES]
    current = progress.etapa_actual if progress else None
    index = codes.index(current) if current in codes else -1
    stage_rows = []
    for i, (code, label) in enumerate(STAGES):
        timing = by_code.get(code)
        done = bool(timing and timing.finished_at) or run.estado in FINISHED_STATES or i < index
        active = code == current and run.estado == "ejecutando" and not done
        stopped = code == current and run.estado == "detenida" and not done
        state = (
            "hecha" if done else "en_curso" if active else "detenida" if stopped else "pendiente"
        )
        seconds = None
        if timing and (timing.finished_at or code == current):
            end = timing.finished_at or run.finalizada_en or now
            seconds = max(0, (end - timing.started_at).total_seconds())
        stage_rows.append(
            {
                "code": code,
                "label": label,
                "hint": stage_term(code).hint,
                "state": state,
                "seconds": seconds,
                "duration": format_duration(seconds) if seconds is not None else None,
                "started_at": timing.started_at.isoformat() if timing else None,
                "finished_at": timing.finished_at.isoformat()
                if timing and timing.finished_at
                else None,
            }
        )
    maximum = max((row["seconds"] or 0 for row in stage_rows), default=0)
    for row in stage_rows:
        row["width"] = round(100 * (row["seconds"] or 0) / maximum, 2) if maximum else 0
    groups: dict[str | None, dict[str, Any]] = {}
    for cause in causes:
        title = error_title(cause.code) if cause.code else "Sin causa informada"
        if title == cause.code:
            title = "La tarea no terminó; consulte el detalle técnico."
        group = groups.setdefault(
            cause.code,
            {
                "code": cause.code,
                "title": title,
                "action": error_action(cause.code) if cause.code else None,
                "families": set(),
                "count": 0,
            },
        )
        group["count"] += cause.count
        group["families"].add(family_label(cause.family))
    for group in groups.values():
        group["families"] = ", ".join(sorted(group["families"]))
    return {
        "stage_rows": stage_rows,
        "cause_groups": sorted(groups.values(), key=lambda item: (-item["count"], item["title"])),
        "live_snapshot": {
            "version": 1,
            "id": run.id,
            "identity": run.configuracion_id,
            "state": run.estado,
            "stateLabel": "Completada" if run.estado in FINISHED_STATES else None,
            "started": run.iniciada_en.isoformat() if run.iniciada_en else None,
            "stage": current,
            "stages": stage_rows,
            "counts": dict(progress.tareas_por_estado) if progress else {},
            "skus": progress.skus_terminados if progress else None,
        },
    }
