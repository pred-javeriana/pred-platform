"""Read-only queries over the DAL, for the data layer's views (SRS 3.6: all SQL lives in ``dal``).

These functions never create or modify the database: the file is opened with ``mode=ro`` and only
when a query runs, so building the application does not need the database to exist. A missing
file, or a database that has not been initialised yet, reads as "no data" instead of failing.
A database still at schema version 1 (before the ingest report columns) reads those columns as
NULL, so a read never fails just because migrations have not run.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pred_platform.data.query import LATEST

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
    """A run by its ``ejecuciones.id`` (digits), by the engine's ``run_id`` or ``latest``.

    ``latest`` is the run with the highest id, i.e. the most recently queued one.
    """
    with _open_readonly(db_path) as conn:
        if conn is None or not _has_run_columns(conn):
            return None
        if reference == LATEST:
            row = conn.execute(
                f"SELECT {_RUN_COLUMNS} {_RUN_FROM} ORDER BY e.id DESC LIMIT 1"
            ).fetchone()
        elif reference.isdigit():
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


# ------------------------------------------------------------ selection per SKU (G4)
# First schema version with the selection tables (migration 0004).
_SELECTION_SCHEMA_VERSION = 4
_FAMILY_ORDER = (
    "CASE f.familia WHEN 'classical' THEN 0 WHEN 'ml' THEN 1 WHEN 'dl' THEN 2 ELSE 3 END"
)


@dataclass(frozen=True, slots=True)
class SelectionRow:
    """One SKU of a run in the selection table: its class and the brief of each family."""

    sku: str
    clase: str
    perfil: str
    familias: list[dict[str, Any]]


@dataclass(frozen=True, slots=True)
class SelectionDetail:
    """One SKU's stored selection; ``familias`` are shaped like the contract's ``FamilyEntry``."""

    sku: str
    clase: str
    perfil: str
    version_politica: str | None
    familias: list[dict[str, Any]]


def _has_selection_tables(conn: sqlite3.Connection) -> bool:
    return int(conn.execute("PRAGMA user_version").fetchone()[0]) >= _SELECTION_SCHEMA_VERSION


def count_selections(db_path: Path, ejecucion_id: int) -> int:
    """How many SKUs of the run have a stored selection."""
    with _open_readonly(db_path) as conn:
        if conn is None or not _has_selection_tables(conn):
            return 0
        return int(
            conn.execute(
                "SELECT COUNT(*) FROM selecciones WHERE ejecucion_id = ?", (ejecucion_id,)
            ).fetchone()[0]
        )


def list_selections(
    db_path: Path,
    ejecucion_id: int,
    *,
    sku_class: str | None,
    family: str | None,
    estado: str | None,
    q: str | None,
    sort: str,
    descending: bool,
    offset: int,
    limit: int,
) -> tuple[list[SelectionRow], int]:
    """A filtered, sorted page of the run's SKUs and the total *after* filtering.

    ``family`` and ``estado`` apply to the same family entry: a SKU matches when one of its
    families has that family and that state. ``sort`` is ``sku_id`` or ``valor`` (the best, i.e.
    smallest, aggregated value among the SKU's families); SKUs without one go last in either
    direction.
    """
    where = ["s.ejecucion_id = ?"]
    params: list[object] = [ejecucion_id]
    if sku_class:
        where.append("s.clase = ?")
        params.append(sku_class)
    if q and q.strip():
        where.append("s.sku LIKE ? ESCAPE '\\'")
        params.append(_like_pattern(q.strip()))
    if family or estado:
        inner = ["f.seleccion_id = s.id"]
        if family:
            inner.append("f.familia = ?")
            params.append(family)
        if estado:
            inner.append("f.estado = ?")
            params.append(estado)
        where.append(f"EXISTS (SELECT 1 FROM seleccion_familias f WHERE {' AND '.join(inner)})")
    clause = " AND ".join(where)
    direction = "DESC" if descending else "ASC"
    best = "(SELECT MIN(f.valor_agregado) FROM seleccion_familias f WHERE f.seleccion_id = s.id)"
    order = (
        f"s.sku {direction}"
        if sort == "sku_id"
        else f"{best} IS NULL, {best} {direction}, s.sku ASC"
    )
    with _open_readonly(db_path) as conn:
        if conn is None or not _has_selection_tables(conn):
            return [], 0
        (total,) = conn.execute(
            f"SELECT COUNT(*) FROM selecciones s WHERE {clause}", params
        ).fetchone()
        skus = conn.execute(
            "SELECT s.id, s.sku, s.clase, s.perfil FROM selecciones s "
            f"WHERE {clause} ORDER BY {order} LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ).fetchall()
        rows: list[SelectionRow] = []
        for selection_id, sku, clase, perfil in skus:
            briefs = conn.execute(
                "SELECT f.familia, f.estado, f.metrica_objetivo, f.valor_agregado "
                f"FROM seleccion_familias f WHERE f.seleccion_id = ? ORDER BY {_FAMILY_ORDER}",
                (selection_id,),
            ).fetchall()
            rows.append(
                SelectionRow(
                    sku,
                    clase,
                    perfil,
                    [
                        {
                            "family": familia,
                            "estado": state,
                            "metrica_objetivo": metric,
                            "valor": value,
                        }
                        for familia, state, metric, value in briefs
                    ],
                )
            )
    return rows, int(total)


def get_selection(
    db_path: Path, ejecucion_id: int, sku: str, *, include_windows: bool
) -> SelectionDetail | None:
    """The stored selection of one SKU, or ``None`` when the run has none for it."""
    with _open_readonly(db_path) as conn:
        if conn is None or not _has_selection_tables(conn):
            return None
        head = conn.execute(
            "SELECT id, clase, perfil, version_politica FROM selecciones "
            "WHERE ejecucion_id = ? AND sku = ?",
            (ejecucion_id, sku),
        ).fetchone()
        if head is None:
            return None
        selection_id, clase, perfil, policy = head
        families = conn.execute(
            "SELECT f.id, f.familia, f.estado, f.tarea_id, f.modelo, f.producido_por, "
            "f.exclusion_codigo, f.exclusion_detalle, f.evidencia_json, "
            "f.configuracion_pronostico_json, f.semilla_pronostico, f.error_json, "
            "f.ensayos_persistidos, f.metrica_objetivo, f.valor_agregado, f.completo, "
            "f.n_ventanas_evaluadas, f.n_ventanas_totales, f.alcance_seleccion, "
            f"f.comparacion_estadistica FROM seleccion_familias f WHERE f.seleccion_id = ? "
            f"ORDER BY {_FAMILY_ORDER}",
            (selection_id,),
        ).fetchall()
        entries = [_family_entry(conn, row, include_windows) for row in families]
    return SelectionDetail(sku, clase, perfil, policy, entries)


def _loads(text: str | None) -> Any:
    return None if text is None else json.loads(text)


def _family_entry(
    conn: sqlite3.Connection, row: tuple[Any, ...], include_windows: bool
) -> dict[str, Any]:
    (
        family_id,
        familia,
        estado,
        tarea_id,
        modelo,
        produced_by,
        exclusion_code,
        exclusion_detail,
        evidence,
        forecast_config,
        forecast_seed,
        error,
        has_trials,
        metric,
        value,
        complete,
        evaluated,
        total,
        scope,
        comparable,
    ) = row
    entry: dict[str, Any] = {
        "family": familia,
        "estado": estado,
        "exclusion": (
            {"reason_code": exclusion_code, "detail": exclusion_detail} if exclusion_code else None
        ),
        "modelo": modelo,
        "produced_by": produced_by,
        "evidence": _loads(evidence),
        "forecast_config": _loads(forecast_config),
        "forecast_seed": forecast_seed,
        "trials": None,
        "walk_forward": None,
        "error": _loads(error),
    }
    if has_trials:
        entry["trials"] = [
            {
                "id": trial_id,
                "configuracion": json.loads(configuration),
                "estado": state,
                "valor": trial_value,
                "n_ventanas": windows,
                "motivo": reason,
                "timestamp": instant,
            }
            for trial_id, configuration, state, trial_value, windows, reason, instant in (
                conn.execute(
                    "SELECT trial_id, configuracion_json, estado, valor, n_ventanas, motivo, "
                    "instante FROM ensayos WHERE seleccion_familia_id = ? ORDER BY orden",
                    (family_id,),
                )
            )
        ]
    if metric is not None:
        entry["walk_forward"] = {
            "metrica_objetivo": metric,
            "valor_agregado": value,
            "completo": bool(complete),
            "n_ventanas_evaluadas": evaluated,
            "n_ventanas_totales": total,
            "selection_scope": scope,
            "statistical_comparison_available": bool(comparable),
            "windows": _windows(conn, family_id, tarea_id) if include_windows else None,
        }
    return entry


def _windows(
    conn: sqlite3.Connection, family_id: int, tarea_id: int | None
) -> list[dict[str, Any]]:
    metrics: dict[int, dict[str, float]] = {}
    if tarea_id is not None:
        for index, name, number in conn.execute(
            "SELECT ventana, metrica, valor FROM metricas "
            "WHERE tarea_id = ? AND ventana IS NOT NULL",
            (tarea_id,),
        ):
            metrics.setdefault(index, {})[name] = number
    return [
        {
            "indice": index,
            "corte": cut,
            "inicio_train": train_start,
            "fin_train": train_end,
            "inicio_val": val_start,
            "fin_val": val_end,
            "metricas": metrics.get(index, {}),
            "duracion_s": seconds,
            "fallo": failure,
        }
        for index, cut, train_start, train_end, val_start, val_end, seconds, failure in (
            conn.execute(
                "SELECT indice, corte, inicio_train, fin_train, inicio_val, fin_val, duracion_s, "
                "fallo FROM ventanas_wf WHERE seleccion_familia_id = ? ORDER BY indice",
                (family_id,),
            )
        )
    ]


def get_topology_row(db_path: Path, ingest_sha256: str, sku: str) -> SeriesRow | None:
    """The demand metrics of one SKU in an ingestion, when its topology was stored."""
    with _open_readonly(db_path) as conn:
        if conn is None or not _has_report_columns(conn):
            return None
        row = conn.execute(
            "SELECT s.sku, s.n_obs, s.n_positive, s.adi, s.cv2, s.clase FROM series s "
            "JOIN ingestas i ON i.id = s.ingesta_id WHERE i.sha256 = ? AND s.sku = ?",
            (ingest_sha256, sku),
        ).fetchone()
    return None if row is None else SeriesRow(*row)
