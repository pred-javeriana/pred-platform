"""Writes of the selection per SKU (G4): the families of a SKU, their trials and windows.

The worker validates the engine's ``seleccion.jsonl`` and hands each SKU here as plain data; this
module only turns it into rows (SRS 3.6: all SQL lives in ``dal``). Replacing a SKU is atomic and
idempotent: importing the same run twice leaves the same rows, and a SKU whose import fails keeps
what it had.

The metrics of every Walk-Forward window go to ``metricas`` linked to the family's task, with the
window's index in ``ventana``; the rest of the window (cut, indices, duration, failure) is in
``ventanas_wf``.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from typing import Any


class SelectionImportError(Exception):
    """A SKU's selection cannot be stored (for example, its task does not exist)."""


@dataclass(frozen=True, slots=True)
class FamiliaGuardar:
    """One family of one SKU, in the vocabulary of the DAL, ready to store."""

    familia: str
    estado: str
    # `familia:algoritmo` of the unit; the task is looked up with it (also for `no_ejecutable`).
    modelo_tarea: str | None = None
    modelo: str | None = None
    producido_por: str | None = None
    exclusion: dict[str, Any] | None = None
    evidencia: dict[str, Any] | None = None
    configuracion_pronostico: dict[str, Any] | None = None
    semilla_pronostico: int | None = None
    error: dict[str, Any] | None = None
    # ``None``: no study was persisted; an empty list: the study closed no trial.
    ensayos: list[dict[str, Any]] | None = None
    walk_forward: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class SeleccionGuardar:
    sku: str
    clase: str
    perfil: str
    version_politica: str | None
    familias: list[FamiliaGuardar] = field(default_factory=list)


def _json(value: Any) -> str | None:
    return None if value is None else json.dumps(value, ensure_ascii=False)


def _find_task(
    conn: sqlite3.Connection, ejecucion_id: int, sku: str, modelo: str | None, corte: str
) -> int | None:
    if modelo is None:
        return None
    row = conn.execute(
        "SELECT id FROM tareas WHERE ejecucion_id = ? AND sku = ? AND modelo = ? AND corte = ?",
        (ejecucion_id, sku, modelo, corte),
    ).fetchone()
    return None if row is None else int(row[0])


def _delete_previous(conn: sqlite3.Connection, ejecucion_id: int, sku: str) -> None:
    """Remove the SKU's stored selection of this run, children first."""
    family_ids = (
        "SELECT f.id FROM seleccion_familias f JOIN selecciones s ON s.id = f.seleccion_id "
        "WHERE s.ejecucion_id = ? AND s.sku = ?"
    )
    for table in ("ensayos", "ventanas_wf"):
        conn.execute(
            f"DELETE FROM {table} WHERE seleccion_familia_id IN ({family_ids})",
            (ejecucion_id, sku),
        )
    # Window metrics belong to the task but are written by this import: aggregated ones
    # (``ventana`` NULL) are not touched.
    conn.execute(
        "DELETE FROM metricas WHERE ventana IS NOT NULL AND tarea_id IN "
        "(SELECT id FROM tareas WHERE ejecucion_id = ? AND sku = ?)",
        (ejecucion_id, sku),
    )
    conn.execute(
        "DELETE FROM seleccion_familias WHERE seleccion_id IN "
        "(SELECT id FROM selecciones WHERE ejecucion_id = ? AND sku = ?)",
        (ejecucion_id, sku),
    )
    conn.execute("DELETE FROM selecciones WHERE ejecucion_id = ? AND sku = ?", (ejecucion_id, sku))


def guardar_seleccion(
    conn: sqlite3.Connection, ejecucion_id: int, corte: str, seleccion: SeleccionGuardar
) -> None:
    """Store (replacing) the selection of one SKU of the run; one transaction.

    Raises:
        SelectionImportError: a family reports Walk-Forward windows but its task is missing, so
            their metrics could not be linked to it. Nothing of the SKU is changed.
    """
    with conn:
        _delete_previous(conn, ejecucion_id, seleccion.sku)
        selection_id = conn.execute(
            "INSERT INTO selecciones (ejecucion_id, sku, clase, perfil, version_politica) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                ejecucion_id,
                seleccion.sku,
                seleccion.clase,
                seleccion.perfil,
                seleccion.version_politica,
            ),
        ).lastrowid
        for family in seleccion.familias:
            tarea_id = _find_task(conn, ejecucion_id, seleccion.sku, family.modelo_tarea, corte)
            summary = family.walk_forward
            family_id = conn.execute(
                "INSERT INTO seleccion_familias (seleccion_id, familia, estado, tarea_id, modelo, "
                "producido_por, exclusion_codigo, exclusion_detalle, evidencia_json, "
                "configuracion_pronostico_json, semilla_pronostico, error_json, "
                "ensayos_persistidos, metrica_objetivo, valor_agregado, completo, "
                "n_ventanas_evaluadas, n_ventanas_totales, alcance_seleccion, "
                "comparacion_estadistica) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    selection_id,
                    family.familia,
                    family.estado,
                    tarea_id,
                    family.modelo,
                    family.producido_por,
                    (family.exclusion or {}).get("reason_code"),
                    (family.exclusion or {}).get("detail"),
                    _json(family.evidencia),
                    _json(family.configuracion_pronostico),
                    family.semilla_pronostico,
                    _json(family.error),
                    0 if family.ensayos is None else 1,
                    summary["metrica_objetivo"] if summary else None,
                    summary.get("valor_agregado") if summary else None,
                    None if summary is None else int(bool(summary["completo"])),
                    summary["n_ventanas_evaluadas"] if summary else None,
                    summary["n_ventanas_totales"] if summary else None,
                    summary["selection_scope"] if summary else None,
                    None
                    if summary is None
                    else int(bool(summary["statistical_comparison_available"])),
                ),
            ).lastrowid
            for order, trial in enumerate(family.ensayos or []):
                conn.execute(
                    "INSERT INTO ensayos (seleccion_familia_id, orden, trial_id, "
                    "configuracion_json, estado, valor, n_ventanas, motivo, instante) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        family_id,
                        order,
                        trial["id"],
                        json.dumps(trial["configuracion"], ensure_ascii=False),
                        trial["estado"],
                        trial.get("valor"),
                        trial["n_ventanas"],
                        trial.get("motivo"),
                        trial.get("timestamp"),
                    ),
                )
            windows = (summary or {}).get("windows") or []
            if windows and tarea_id is None:
                raise SelectionImportError(
                    f"{seleccion.sku} / {family.familia}: hay ventanas pero no existe su tarea"
                )
            for window in windows:
                conn.execute(
                    "INSERT INTO ventanas_wf (seleccion_familia_id, indice, corte, inicio_train, "
                    "fin_train, inicio_val, fin_val, duracion_s, fallo) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        family_id,
                        window["indice"],
                        window["corte"],
                        window["inicio_train"],
                        window["fin_train"],
                        window["inicio_val"],
                        window["fin_val"],
                        window.get("duracion_s"),
                        window.get("fallo"),
                    ),
                )
                for metric, value in window["metricas"].items():
                    if value is None:
                        continue
                    conn.execute(
                        "INSERT INTO metricas (tarea_id, sku, modelo, metrica, valor, ventana) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (
                            tarea_id,
                            seleccion.sku,
                            family.modelo_tarea,
                            metric,
                            value,
                            window["indice"],
                        ),
                    )


def contar_selecciones(conn: sqlite3.Connection, ejecucion_id: int) -> int:
    """How many SKUs have a stored selection in the run."""
    return int(
        conn.execute(
            "SELECT COUNT(*) FROM selecciones WHERE ejecucion_id = ?", (ejecucion_id,)
        ).fetchone()[0]
    )
