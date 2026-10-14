"""Read-only queries over the DAL, for the data layer's views (SRS 3.6: all SQL lives in ``dal``).

These functions never create or modify the database: the file is opened with ``mode=ro`` and only
when a query runs, so building the application does not need the database to exist. A missing
file, or a database that has not been initialised yet, reads as "no data" instead of failing.
A database still at schema version 1 (before the ingest report columns) reads those columns as
NULL, so a read never fails just because migrations have not run.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# First schema version with the ingest report and topology columns (migration 0002).
_REPORT_SCHEMA_VERSION = 2

_TOPOLOGY_SORT_COLUMNS = {"sku_id": "sku", "adi": "adi", "cv2": "cv2", "n_positive": "n_positive"}


@dataclass(frozen=True, slots=True)
class IngestRow:
    """One row of ``ingestas`` for a listing, in the DAL's own vocabulary."""

    sha256: str
    nombre_archivo: str
    filas: int
    skus: int
    estado: str | None = None
    ruta_parquet: str | None = None
    publicada_en: str | None = None


@dataclass(frozen=True, slots=True)
class IngestDetail:
    """Everything stored about one ingestion (``ingestas``)."""

    id: int
    sha256: str
    nombre_archivo: str
    filas: int
    skus: int
    estado: str | None
    estado_deposito: str | None
    deposito_colision: int | None
    ruta_raw: str | None
    ruta_parquet: str | None
    filas_validadas: int | None
    filas_panel: int | None
    t_star: str | None
    umbral_adi: float | None
    umbral_cv2: float | None
    diagnostico_json: str | None
    error_json: str | None
    publicada_en: str | None


@dataclass(frozen=True, slots=True)
class QualityRow:
    """One row of ``bitacora_calidad``."""

    severidad: str
    codigo: str
    mensaje: str
    fila: int | None
    columna: str | None


@dataclass(frozen=True, slots=True)
class SeriesRow:
    """One SKU of an ingestion (``series``) with its demand-topology metrics."""

    sku: str
    n_obs: int
    n_positive: int | None
    adi: float | None
    cv2: float | None
    clase: str | None


@contextmanager
def _open_readonly(db_path: Path) -> Iterator[sqlite3.Connection | None]:
    """A read-only connection, or ``None`` when the file does not exist."""
    if not db_path.is_file():
        yield None
        return
    conn = sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True)
    try:
        yield conn
    finally:
        conn.close()


def _is_missing_table(error: sqlite3.OperationalError) -> bool:
    return "no such table" in str(error)


def _has_report_columns(conn: sqlite3.Connection) -> bool:
    return int(conn.execute("PRAGMA user_version").fetchone()[0]) >= _REPORT_SCHEMA_VERSION


def list_ingestas(db_path: Path, *, offset: int, limit: int) -> tuple[list[IngestRow], int]:
    """A page of ingests, newest first, and how many there are in total."""
    with _open_readonly(db_path) as conn:
        if conn is None:
            return [], 0
        try:
            (total,) = conn.execute("SELECT COUNT(*) FROM ingestas").fetchone()
            extra = (
                "estado, ruta_parquet, publicada_en"
                if _has_report_columns(conn)
                else "NULL, NULL, NULL"
            )
            rows = conn.execute(
                f"SELECT sha256, nombre_archivo, filas, skus, {extra} FROM ingestas "
                "ORDER BY id DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        except sqlite3.OperationalError as error:
            if _is_missing_table(error):
                return [], 0
            raise
    return [IngestRow(*row) for row in rows], total


def ingesta_existe(db_path: Path, sha256: str) -> bool:
    """Whether an ingest with this SHA-256 is stored."""
    return get_ingesta(db_path, sha256) is not None


def get_ingesta(db_path: Path, sha256: str) -> IngestDetail | None:
    """The stored ingestion with this SHA-256, or ``None``."""
    with _open_readonly(db_path) as conn:
        if conn is None:
            return None
        row: tuple[Any, ...] | None
        try:
            if _has_report_columns(conn):
                row = conn.execute(
                    "SELECT id, sha256, nombre_archivo, filas, skus, estado, estado_deposito, "
                    "deposito_colision, ruta_raw, ruta_parquet, filas_validadas, filas_panel, "
                    "t_star, umbral_adi, umbral_cv2, diagnostico_json, error_json, publicada_en "
                    "FROM ingestas WHERE sha256 = ?",
                    (sha256,),
                ).fetchone()
            else:
                basic = conn.execute(
                    "SELECT id, sha256, nombre_archivo, filas, skus FROM ingestas WHERE sha256 = ?",
                    (sha256,),
                ).fetchone()
                row = None if basic is None else (*basic, *((None,) * 13))
        except sqlite3.OperationalError as error:
            if _is_missing_table(error):
                return None
            raise
    return None if row is None else IngestDetail(*row)


def quality_log(db_path: Path, ingesta_id: int) -> list[QualityRow]:
    """The quality log of one ingestion, in insertion order."""
    with _open_readonly(db_path) as conn:
        if conn is None:
            return []
        try:
            rows = conn.execute(
                "SELECT severidad, codigo, mensaje, fila, columna FROM bitacora_calidad "
                "WHERE ingesta_id = ? ORDER BY id",
                (ingesta_id,),
            ).fetchall()
        except sqlite3.OperationalError as error:
            if _is_missing_table(error):
                return []
            raise
    return [QualityRow(*row) for row in rows]


def _like_pattern(text: str) -> str:
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def topology_rows(
    db_path: Path,
    ingesta_id: int,
    *,
    sku_class: str | None,
    q: str | None,
    sort: str,
    descending: bool,
    offset: int,
    limit: int,
) -> tuple[list[SeriesRow], int]:
    """A filtered, sorted page of the ingestion's SKUs and the total *after* filtering.

    ``sort`` must be a key of the topology sort whitelist (``sku_id``, ``adi``, ``cv2``,
    ``n_positive``); rows without a value go last in either direction.
    """
    column = _TOPOLOGY_SORT_COLUMNS[sort]
    direction = "DESC" if descending else "ASC"
    where = ["ingesta_id = ?", "clase IS NOT NULL"]
    params: list[object] = [ingesta_id]
    if sku_class:
        where.append("clase = ?")
        params.append(sku_class)
    if q and q.strip():
        where.append("sku LIKE ? ESCAPE '\\'")
        params.append(_like_pattern(q.strip()))
    clause = " AND ".join(where)
    with _open_readonly(db_path) as conn:
        if conn is None or not _has_report_columns(conn):
            return [], 0
        (total,) = conn.execute(f"SELECT COUNT(*) FROM series WHERE {clause}", params).fetchone()
        rows = conn.execute(
            "SELECT sku, n_obs, n_positive, adi, cv2, clase FROM series "
            f"WHERE {clause} ORDER BY {column} IS NULL, {column} {direction}, sku ASC "
            "LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ).fetchall()
    return [SeriesRow(*row) for row in rows], total


def topology_counts(db_path: Path, ingesta_id: int) -> dict[str, int]:
    """SKU count per demand class over the whole ingestion (not just a page)."""
    with _open_readonly(db_path) as conn:
        if conn is None or not _has_report_columns(conn):
            return {}
        rows = conn.execute(
            "SELECT clase, COUNT(*) FROM series WHERE ingesta_id = ? AND clase IS NOT NULL "
            "GROUP BY clase",
            (ingesta_id,),
        ).fetchall()
    return {clase: int(count) for clase, count in rows}


# ------------------------------------------------------------------- runs and tasks (G3)
@dataclass(frozen=True, slots=True)
class RunDetail:
    """One row of ``ejecuciones`` with its configuration and ingestion."""

    id: int
    run_id: str | None
    estado: str
    seed: int
    iniciada_en: str | None
    finalizada_en: str | None
    configuracion_id: int
    configuracion_version: int
    parametros: str
    ingest_sha256: str
    reserva_json: str | None
    error_json: str | None


@dataclass(frozen=True, slots=True)
class TaskRow:
    """One row of ``tareas``."""

    id: int
    sku: str | None
    modelo: str
    corte: str
    estado: str
    seed: int
    tiempo_pared_s: float | None
    detalle_error: str | None
    iniciada_en: str | None
    finalizada_en: str | None


_RUN_COLUMNS = (
    "e.id, e.run_id, e.estado, e.seed, e.iniciada_en, e.finalizada_en, e.configuracion_id, "
    "c.version, c.parametros, i.sha256, e.reserva_json, e.error_json"
)
_RUN_FROM = (
    "FROM ejecuciones e JOIN configuraciones c ON c.id = e.configuracion_id "
    "JOIN ingestas i ON i.id = e.ingesta_id"
)


def _has_run_columns(conn: sqlite3.Connection) -> bool:
    return int(conn.execute("PRAGMA user_version").fetchone()[0]) >= 3


def count_runs(db_path: Path) -> int:
    """How many runs are stored (0 when there is no database or it predates migration 0003)."""
    with _open_readonly(db_path) as conn:
        if conn is None or not _has_run_columns(conn):
            return 0
        return int(conn.execute("SELECT COUNT(*) FROM ejecuciones").fetchone()[0])


def get_run(db_path: Path, reference: str) -> RunDetail | None:
    """A run by its ``ejecuciones.id`` (digits) or by the engine's ``run_id``."""
    with _open_readonly(db_path) as conn:
        if conn is None or not _has_run_columns(conn):
            return None
        if reference.isdigit():
            row = conn.execute(
                f"SELECT {_RUN_COLUMNS} {_RUN_FROM} WHERE e.id = ?", (int(reference),)
            ).fetchone()
        else:
            row = conn.execute(
                f"SELECT {_RUN_COLUMNS} {_RUN_FROM} WHERE e.run_id = ?", (reference,)
            ).fetchone()
    return None if row is None else RunDetail(*row)


def task_state_counts(db_path: Path, ejecucion_id: int) -> dict[str, int]:
    """Tasks per state over the whole run (not just a page)."""
    with _open_readonly(db_path) as conn:
        if conn is None:
            return {}
        rows = conn.execute(
            "SELECT estado, COUNT(*) FROM tareas WHERE ejecucion_id = ? GROUP BY estado",
            (ejecucion_id,),
        ).fetchall()
    return {estado: int(n) for estado, n in rows}


def list_tasks(
    db_path: Path,
    ejecucion_id: int,
    *,
    estado: str | None,
    sku: str | None,
    family: str | None,
    offset: int,
    limit: int,
) -> tuple[list[TaskRow], int]:
    """A filtered page of the run's tasks (by SKU, then model) and the total after filtering."""
    where = ["ejecucion_id = ?"]
    params: list[object] = [ejecucion_id]
    if estado:
        where.append("estado = ?")
        params.append(estado)
    if sku and sku.strip():
        where.append("sku = ?")
        params.append(sku.strip())
    if family:
        where.append("modelo LIKE ?")
        params.append(f"{family}:%")
    clause = " AND ".join(where)
    with _open_readonly(db_path) as conn:
        if conn is None:
            return [], 0
        (total,) = conn.execute(f"SELECT COUNT(*) FROM tareas WHERE {clause}", params).fetchone()
        rows = conn.execute(
            "SELECT id, sku, modelo, corte, estado, seed, tiempo_pared_s, detalle_error, "
            f"iniciada_en, finalizada_en FROM tareas WHERE {clause} "
            "ORDER BY sku, modelo LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ).fetchall()
    return [TaskRow(*row) for row in rows], int(total)
