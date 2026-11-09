"""Progress and cause presentation from the status already read for step 4."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from typing import Any

from pred_platform.app.ejecucion import FINISHED_STATES, STAGES, error_action
from pred_platform.app.presentation import (
    describe_status,
    family_label,
    format_duration,
    stage_term,
)
from pred_platform.app.seleccion import error_title, exclusion_text
from pred_platform.contract.models import RunStatus
from pred_platform.data.run_status import CauseSnapshot, DisplayRunStatus, StageReference


def add_stage_references(context: dict[str, Any], references: list[StageReference]) -> None:
    history = []
    for reference in references:
        stages = {}
        for timing in reference.timings:
            if timing.finished_at is None or timing.finish_state == "failed":
                continue
            seconds = max(0, (timing.finished_at - timing.started_at).total_seconds())
            stages[timing.code] = {"seconds": seconds, "duration": format_duration(seconds)}
        if stages:
            history.append({"id": reference.id, "seed": reference.seed, "stages": stages})
    context["stage_history"] = history
    maximum = max(
        [row["seconds"] or 0 for row in context["stage_rows"]]
        + [stage["seconds"] for reference in history for stage in reference["stages"].values()],
        default=0,
    )
    for row in context["stage_rows"]:
        row["width"] = 100 * (row["seconds"] or 0) / maximum if maximum else 0
        row["references"] = [
            {
                "seed": reference["seed"],
                "duration": reference["stages"][row["code"]]["duration"],
                "width": 100 * reference["stages"][row["code"]]["seconds"] / maximum
                if maximum
                else 0,
            }
            for reference in history
            if row["code"] in reference["stages"]
        ]


_WAITING_CAPTIONS = {
    "pendiente": "Las etapas del nuevo intento se mostrarán cuando el procesador lo inicie.",
    "detenida": "El nuevo intento se detuvo antes de empezar, así que no tiene tiempos por etapa.",
}


def execution_display(status: RunStatus | None, now: datetime) -> dict[str, Any]:
    if status is None or status.ejecucion is None:
        return {"live_snapshot": None, "stage_rows": [], "stage_caption": None, "cause_groups": []}
    run, progress = status.ejecucion, status.progress
    timings = (
        status.stage_timings
        if isinstance(status, DisplayRunStatus) and run.estado != "pendiente"
        else []
    )
    by_code = {item.code: item for item in timings}
    codes = [code for code, _ in STAGES]
    current = progress.etapa_actual if progress else None
    if run.estado == "detenida" and isinstance(status, DisplayRunStatus):
        current = status.last_stage
    index = codes.index(current) if current in codes else -1
    # A stage after the last one that started never ran: the run ended before reaching it.
    ran = max([codes.index(code) for code in by_code] + [index], default=-1)
    stage_rows = []
    for i, (code, label) in enumerate(STAGES):
        timing = by_code.get(code)
        failed = bool(timing and timing.finish_state == "failed")
        done = (
            bool(timing and timing.finished_at)
            or i < max(index, ran)
            or run.estado == "completada"
            or (run.estado in FINISHED_STATES and i <= ran)
        )
        active = code == current and run.estado == "ejecutando" and not done
        stopped = code == current and run.estado == "detenida" and not done
        state = (
            "fallida"
            if failed
            else "hecha"
            if done
            else "sin_ejecutar"
            if run.estado in FINISHED_STATES
            else "en_curso"
            if active
            else "detenida"
            if stopped
            else "pendiente"
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
    waiting = run.estado == "pendiente" or getattr(status, "stopped_before_start", False)
    return {
        "stage_rows": stage_rows,
        "stage_caption": _WAITING_CAPTIONS.get(run.estado) if waiting else None,
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


def add_cause_snapshot(context: dict[str, Any], snapshot: CauseSnapshot) -> None:
    groups: dict[str | None, dict[str, Any]] = {}
    for cause in snapshot.causes:
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
    context["cause_groups"] = sorted(
        groups.values(), key=lambda item: (-item["count"], item["title"])
    )
    context["cause_snapshot_at"] = snapshot.read_at


def task_rows(status: RunStatus) -> list[dict[str, Any]]:
    rows = []
    for task in status.tasks:
        error = task.error
        families = (
            status.family_states.get(task.sku or "", {})
            if isinstance(status, DisplayRunStatus)
            else {}
        )
        family = families.get(task.family or "")
        excluded = bool(
            task.estado == "no_ejecutable"
            and family
            and family.estado == "excluida"
            and family.exclusion_code
        )
        shown_status = describe_status("task", task.estado)
        reason = None
        if excluded and family is not None:
            reason = exclusion_text(
                family.exclusion_code or "", task.family or "", family.sku_class
            )
            shown_status = replace(
                describe_status("family", "excluida"),
                label="No elegida para esta clase"
                if family.exclusion_code == "not_in_policy_matrix"
                else "No elegida en esta corrida"
                if family.exclusion_code == "not_configured"
                else "Excluida",
                hint=reason,
            )
        elif error and error.code == "eligibility.fewer_than_two_families":
            reason = "No se ejecutó la comparación: quedaron menos de dos familias evaluables."
            failed = [
                family_label(code) for code, entry in families.items() if entry.estado == "fallida"
            ]
            if failed:
                reason += (
                    (" Falló " if len(failed) == 1 else " Fallaron ") + ", ".join(failed) + "."
                )
        rows.append(
            {
                "task": task,
                "status": shown_status,
                "reason": reason,
                "error": None
                if error is None
                else {
                    "title": reason
                    or error_title(error.code, error.stage, error.row_index, error.column),
                    "detail": error.detail,
                    "code": error.code,
                    "stage": error.stage,
                    "action": None if excluded else error_action(error.code),
                },
            }
        )
    return rows
