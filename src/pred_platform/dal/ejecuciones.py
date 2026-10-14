"""Writes for the run orchestration (G3): launches, tasks and their state.

The worker is the only writer of ``ejecuciones`` and ``tareas``; every statement lives here so the
SQL stays in the DAL (SRS 3.6). All functions take an open connection and commit nothing by
themselves unless they say so: callers use ``with conn:`` to make one change atomic.

Task formats follow decisions D5 and D6: ``modelo`` is ``familia:algoritmo``, ``corte`` is the
reserve cut t* as ``AAAA-MM-DD`` and ``detalle_error`` is the JSON of an ``ErrorInfo``.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Unit states a task can be in; mirrors the CHECK of ``tareas.estado``.
PENDIENTE = "pendiente"
EJECUTANDO = "ejecutando"
EXITOSA = "exitosa"
FALLIDA = "fallida"
NO_EJECUTABLE = "no_ejecutable"


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def connect(db_path: Path) -> sqlite3.Connection:
    """A connection for one thread: foreign keys on, waits for locks, autocommit off by ``with``."""
    conn = sqlite3.connect(db_path, timeout=30)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@dataclass(frozen=True, slots=True)
class Ejecucion:
    """A run to launch or follow, with what the worker needs about its ingestion."""

    id: int
    run_id: str
    estado: str
    seed: int
    intento: int
    pid: int | None
    parametros: dict[str, Any]
    ingest_sha256: str
    ruta_parquet: str | None
    t_star: str | None


class RunRequestError(Exception):
    """The run cannot be created (unknown or unaccepted ingestion)."""


def _row_to_run(row: tuple[Any, ...]) -> Ejecucion:
    return Ejecucion(
        id=row[0],
        run_id=row[1],
        estado=row[2],
        seed=row[3],
        intento=row[4],
        pid=row[5],
        parametros=json.loads(row[6]),
        ingest_sha256=row[7],
        ruta_parquet=row[8],
        t_star=row[9],
    )


_SELECT_RUN = (
    "SELECT e.id, e.run_id, e.estado, e.seed, e.intento, e.pid, c.parametros, "
    "i.sha256, i.ruta_parquet, i.t_star "
    "FROM ejecuciones e JOIN configuraciones c ON c.id = e.configuracion_id "
    "JOIN ingestas i ON i.id = e.ingesta_id"
)


def _begin_immediate(conn: sqlite3.Connection) -> None:
    """Take the write lock now, so a read-then-write is atomic against another worker."""
    if not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")


def get_run(conn: sqlite3.Connection, ejecucion_id: int) -> Ejecucion | None:
    row = conn.execute(f"{_SELECT_RUN} WHERE e.id = ?", (ejecucion_id,)).fetchone()
    return None if row is None else _row_to_run(row)


def create_run(
    conn: sqlite3.Connection,
    *,
    ingest_sha256: str,
    parametros: dict[str, Any],
    seed: int,
    run_id: str,
) -> tuple[int, bool]:
    """Queue a run for an accepted ingestion. Returns ``(ejecucion_id, queued)``.

    The engine's ``run_id`` is unique: asking again for the same one returns the existing run.
    A run that was stopped or finished with failures is queued again to resume (``queued`` is
    True); one pending, running or completed is left alone (``queued`` is False).
    """
    with conn:
        _begin_immediate(conn)
        existing = conn.execute(
            "SELECT id, estado FROM ejecuciones WHERE run_id = ?", (run_id,)
        ).fetchone()
        if existing is not None:
            ejecucion_id, estado = existing
            if estado in ("detenida", "completada_con_fallos"):
                conn.execute(
                    "UPDATE ejecuciones SET estado = 'pendiente', error_json = NULL, "
                    "finalizada_en = NULL, pid = NULL WHERE id = ?",
                    (ejecucion_id,),
                )
                return int(ejecucion_id), True
            return int(ejecucion_id), False

        ingest = conn.execute(
            "SELECT id, estado FROM ingestas WHERE sha256 = ?", (ingest_sha256,)
        ).fetchone()
        if ingest is None:
            raise RunRequestError(f"no existe la ingesta {ingest_sha256[:12]}…")
        if ingest[1] != "accepted":
            raise RunRequestError(
                f"la ingesta {ingest_sha256[:12]}… no fue aceptada (estado: {ingest[1]})"
            )
        version = conn.execute("SELECT COALESCE(MAX(version), 0) + 1 FROM configuraciones")
        configuracion_id = conn.execute(
            "INSERT INTO configuraciones (version, descripcion, parametros) VALUES (?, ?, ?)",
            (version.fetchone()[0], f"run {run_id}", json.dumps(parametros, sort_keys=True)),
        ).lastrowid
        cursor = conn.execute(
            "INSERT INTO ejecuciones (ingesta_id, configuracion_id, seed, estado, run_id) "
            "VALUES (?, ?, ?, 'pendiente', ?)",
            (ingest[0], configuracion_id, seed, run_id),
        )
        return int(cursor.lastrowid or 0), True


def claim_pending(conn: sqlite3.Connection) -> Ejecucion | None:
    """Atomically take the oldest pending run (two workers never take the same one)."""
    with conn:
        _begin_immediate(conn)
        row = conn.execute(
            "SELECT id FROM ejecuciones WHERE estado = 'pendiente' ORDER BY id LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        conn.execute(
            "UPDATE ejecuciones SET estado = 'ejecutando', intento = intento + 1, "
            "iniciada_en = COALESCE(iniciada_en, ?), finalizada_en = NULL, error_json = NULL "
            "WHERE id = ? AND estado = 'pendiente'",
            (now_iso(), row[0]),
        )
    return get_run(conn, int(row[0]))


def interrupted_runs(conn: sqlite3.Connection) -> list[Ejecucion]:
    """Runs left ``ejecutando`` by a platform that went away (they have no live worker)."""
    rows = conn.execute(f"{_SELECT_RUN} WHERE e.estado = 'ejecutando' ORDER BY e.id").fetchall()
    return [_row_to_run(r) for r in rows]


def requeue(conn: sqlite3.Connection, ejecucion_id: int) -> None:
    with conn:
        conn.execute(
            "UPDATE ejecuciones SET estado = 'pendiente', pid = NULL WHERE id = ? "
            "AND estado = 'ejecutando'",
            (ejecucion_id,),
        )


def set_launch(conn: sqlite3.Connection, ejecucion_id: int, *, pid: int, ruta_corrida: str) -> None:
    with conn:
        conn.execute(
            "UPDATE ejecuciones SET pid = ?, ruta_corrida = ? WHERE id = ?",
            (pid, ruta_corrida, ejecucion_id),
        )


def finish_run(
    conn: sqlite3.Connection,
    ejecucion_id: int,
    *,
    estado: str,
    error: dict[str, Any] | None = None,
    reserva: dict[str, Any] | None = None,
) -> None:
    with conn:
        conn.execute(
            "UPDATE ejecuciones SET estado = ?, finalizada_en = ?, pid = NULL, error_json = ?, "
            "reserva_json = COALESCE(?, reserva_json) WHERE id = ?",
            (
                estado,
                now_iso(),
                json.dumps(error, ensure_ascii=False) if error else None,
                json.dumps(reserva) if reserva else None,
                ejecucion_id,
            ),
        )


# ----------------------------------------------------------------------------- tasks
def _ensure_task(
    conn: sqlite3.Connection, ejecucion_id: int, sku: str, modelo: str, corte: str, seed: int
) -> None:
    conn.execute(
        "INSERT INTO tareas (ejecucion_id, sku, modelo, corte, estado, seed) "
        "VALUES (?, ?, ?, ?, 'pendiente', ?) "
        "ON CONFLICT (ejecucion_id, sku, modelo, corte) DO NOTHING",
        (ejecucion_id, sku, modelo, corte, seed),
    )


def plan_tasks(
    conn: sqlite3.Connection,
    ejecucion_id: int,
    *,
    corte: str,
    seed: int,
    units: list[tuple[str, str]],
) -> None:
    """Create the planned units as ``pendiente`` (existing rows are left as they are)."""
    with conn:
        for sku, modelo in units:
            _ensure_task(conn, ejecucion_id, sku, modelo, corte, seed)


def task_started(
    conn: sqlite3.Connection,
    ejecucion_id: int,
    *,
    sku: str,
    modelo: str,
    corte: str,
    seed: int,
    iniciada_en: str,
) -> None:
    with conn:
        _ensure_task(conn, ejecucion_id, sku, modelo, corte, seed)
        conn.execute(
            "UPDATE tareas SET estado = 'ejecutando', iniciada_en = COALESCE(iniciada_en, ?), "
            "finalizada_en = NULL, detalle_error = NULL "
            "WHERE ejecucion_id = ? AND sku = ? AND modelo = ? AND corte = ?",
            (iniciada_en, ejecucion_id, sku, modelo, corte),
        )


def task_stage_finished(
    conn: sqlite3.Connection,
    ejecucion_id: int,
    *,
    sku: str,
    modelo: str,
    corte: str,
    seed: int,
    stage: str,
    completed: bool,
    seconds: float | None,
    finished_at: str,
    error: dict[str, Any] | None,
) -> None:
    """Apply the end of one stage (L2 or L3) of a unit; replaying it changes nothing."""
    with conn:
        _ensure_task(conn, ejecucion_id, sku, modelo, corte, seed)
        key = (ejecucion_id, sku, modelo, corte)
        where = "WHERE ejecucion_id = ? AND sku = ? AND modelo = ? AND corte = ?"
        if not completed:
            conn.execute(
                "UPDATE tareas SET estado = 'fallida', detalle_error = ?, finalizada_en = ?, "
                f"tiempo_pared_s = ROUND(COALESCE(tiempo_l2_s, 0) + COALESCE(?, 0), 3) {where}"
                if stage == "L3"
                else "UPDATE tareas SET estado = 'fallida', detalle_error = ?, "
                f"finalizada_en = ?, tiempo_l2_s = ?, tiempo_pared_s = ? {where}",
                (
                    json.dumps(error, ensure_ascii=False) if error else None,
                    finished_at,
                    *((seconds,) if stage == "L3" else (seconds, seconds)),
                    *key,
                ),
            )
        elif stage == "L2":
            conn.execute(
                "UPDATE tareas SET tiempo_l2_s = ?, tiempo_pared_s = ?, "
                "estado = CASE WHEN estado = 'pendiente' THEN 'ejecutando' ELSE estado END "
                f"{where}",
                (seconds, seconds, *key),
            )
        else:
            conn.execute(
                "UPDATE tareas SET estado = 'exitosa', finalizada_en = ?, detalle_error = NULL, "
                f"tiempo_pared_s = ROUND(COALESCE(tiempo_l2_s, 0) + COALESCE(?, 0), 3) {where}",
                (finished_at, seconds, *key),
            )


def task_excluded(
    conn: sqlite3.Connection,
    ejecucion_id: int,
    *,
    sku: str,
    modelo: str,
    corte: str,
    seed: int,
    error: dict[str, Any],
) -> None:
    with conn:
        _ensure_task(conn, ejecucion_id, sku, modelo, corte, seed)
        conn.execute(
            "UPDATE tareas SET estado = 'no_ejecutable', detalle_error = ? "
            "WHERE ejecucion_id = ? AND sku = ? AND modelo = ? AND corte = ?",
            (json.dumps(error, ensure_ascii=False), ejecucion_id, sku, modelo, corte),
        )


def close_open_tasks(
    conn: sqlite3.Connection,
    ejecucion_id: int,
    *,
    interrupted: dict[str, Any],
    skipped: dict[str, Any],
) -> int:
    """After the engine is gone, no task may stay in flight.

    ``ejecutando`` becomes ``fallida`` (the unit was cut off) and ``pendiente`` becomes
    ``no_ejecutable`` (it never started). Returns how many rows changed.
    """
    with conn:
        a = conn.execute(
            "UPDATE tareas SET estado = 'fallida', detalle_error = ?, finalizada_en = ? "
            "WHERE ejecucion_id = ? AND estado = 'ejecutando'",
            (json.dumps(interrupted, ensure_ascii=False), now_iso(), ejecucion_id),
        ).rowcount
        b = conn.execute(
            "UPDATE tareas SET estado = 'no_ejecutable', detalle_error = ? "
            "WHERE ejecucion_id = ? AND estado = 'pendiente'",
            (json.dumps(skipped, ensure_ascii=False), ejecucion_id),
        ).rowcount
    return int(a) + int(b)


def task_counts(conn: sqlite3.Connection, ejecucion_id: int) -> dict[str, int]:
    rows = conn.execute(
        "SELECT estado, COUNT(*) FROM tareas WHERE ejecucion_id = ? GROUP BY estado",
        (ejecucion_id,),
    ).fetchall()
    return {estado: int(n) for estado, n in rows}
