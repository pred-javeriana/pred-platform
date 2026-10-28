"""Read M3's published evidence from the shared database, without evaluating models.

The engine owns the M3 schema; the platform applies its migrations and reads what M3 published.
Each read opens one read-only snapshot: the run's state and every row come from the same
transaction, so a publication between two statements cannot mix states. Rows other than the run's
metadata are returned only for a PUBLICADA run, for every table, not only the verdicts.

Rows keep the SQL names. They are already decoded into the shapes of contract v2 (bits as
booleans, JSON columns as values, SQLite times as UTC) and carry the run's ``ingesta_sha256``;
run-level rows carry ``sku: None``. ``data/dal_m3.py`` validates them as v2 documents. Filters and
order come from ``data/m3_query.M3Query`` and become SQL here, so lists are filtered, sorted and
paged by SQLite.

A missing database, or one that predates M3, reads as "no such run".
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pred_platform.dal.lectura import _open_readonly
from pred_platform.data.m3_query import M3Query

Kind = Literal[
    "evidence",
    "metrics",
    "categories",
    "portfolio",
    "forecasts",
    "failures",
    "units",
    "observations",
    "champions",
]

_POLICY_KEYS = ("version", "n_min", "n_min_dm", "alfa_dm")
_VERDICT_BITS = ("pierde_frente_a_linea_base", "comparacion_incompleta", "dm_significativa")
_CATEGORY_JSON = (
    "medianas_r",
    "skus_comparables",
    "excluidos",
    "familias_excluidas",
    "conteos",
    "porcentajes",
)


@dataclass(frozen=True, slots=True)
class RunsPage:
    """A page of runs (``M3Run`` fields), the total of the query and the newest PUBLICADA one."""

    rows: list[dict[str, Any]]
    total: int
    selected_run_id: str | None


@dataclass(frozen=True, slots=True)
class StatusRead:
    """A run's metadata and its durable progress, without any evidence."""

    run: dict[str, Any] | None
    progress: dict[str, Any] | None


@dataclass(frozen=True, slots=True)
class PublishedRead:
    """The run and, when it is PUBLICADA, one page of rows and the total of the query.

    ``t_star`` and ``late_launches`` come from M3's SQLite publication in the same snapshot.
    ``verdict_counts`` counts the run's verdicts for the funnel. Observations also read the cut
    to distinguish old publications from an empty reserve.
    """

    run: dict[str, Any] | None
    rows: list[dict[str, Any]]
    total: int
    t_star: str | None = None
    late_launches: list[dict[str, Any]] | None = None
    verdict_counts: dict[str, int] | None = None


@dataclass(frozen=True, slots=True)
class _Table:
    name: str
    per_sku: bool
    decode: Callable[[dict[str, Any], dict[str, Any]], None] | None = None


def _utc(text: str | None) -> datetime | None:
    """A time SQLite or the engine stored; ``datetime('now')`` has no zone and is UTC."""
    if text is None:
        return None
    moment = datetime.fromisoformat(text.replace(" ", "T", 1))
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def _bit(value: int | None) -> bool | None:
    return None if value is None else bool(value)


def _verdict(row: dict[str, Any], run: dict[str, Any]) -> None:
    row.setdefault("origen_declarado", run["origen_declarado"])  # views before migration 0009
    for name in ("datos_sinteticos", "origen_declarado", *_VERDICT_BITS):
        row[name] = _bit(row[name])
    row["n_min"] = run["policy"]["n_min"] if run["policy"] is not None else None
    trace = {"run_id": row["run_id"], "sku": row["sku"], "ingesta_sha256": row["ingesta_sha256"]}
    row["modelos_evaluados"] = [
        {**trace, **model} for model in json.loads(row["modelos_evaluados"])
    ]


def _category(row: dict[str, Any], run: dict[str, Any]) -> None:
    row["adverso"] = bool(row["adverso"])
    for name in _CATEGORY_JSON:
        row[name] = json.loads(row[name])


def _unit(row: dict[str, Any], run: dict[str, Any]) -> None:
    row["emitido_en"] = _utc(row["emitido_en"])


_TABLES: dict[Kind, _Table] = {
    "evidence": _Table("evidencia_publicada", True, _verdict),
    "metrics": _Table("m3_metricas", True),
    "categories": _Table("m3_categorias", False, _category),
    "portfolio": _Table("m3_cartera", False),
    "forecasts": _Table("m3_pronosticos", True),
    "failures": _Table("m3_fallos", True),
    "units": _Table("m3_unidades", True, _unit),
    "observations": _Table("m3_observaciones", True),
    "champions": _Table("m3_campeones", False),
}


# --------------------------------------------------------------------------- SQL
def _quoted(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({_quoted(table)})")}


def _has(conn: sqlite3.Connection, name: str) -> bool:
    return (
        conn.execute("SELECT 1 FROM sqlite_master WHERE name = ?", (name,)).fetchone() is not None
    )


def _like(text: str) -> str:
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _where(
    query: M3Query, columns: set[str], fixed: list[tuple[str, Any]], alias: str = ""
) -> tuple[str, list[Any]]:
    """fixed equalities and the query's filters as a WHERE clause and its parameters.

    A field the table does not have is a programming error, never a silently ignored filter.
    """
    clauses: list[str] = []
    params: list[Any] = []

    def column(name: str) -> str:
        if name not in columns:
            raise ValueError(f"unknown M3 column {name!r}")
        return alias + _quoted(name)

    for name, value in fixed:
        clauses.append(f"{column(name)} = ?")
        params.append(value)

    for name, value in query.exact.items():
        clauses.append(f"{column(name)} = ?")
        params.append(value)
    for name, values in query.any_of.items():
        if not values:
            clauses.append("0")
            continue
        clauses.append(f"{column(name)} IN ({', '.join('?' for _ in values)})")
        params.extend(values)
    if query.contains is not None:
        name, needle = query.contains
        clauses.append(f"{column(name)} LIKE ? ESCAPE '\\'")
        params.append(_like(needle))
    return (" WHERE " + " AND ".join(clauses) if clauses else ""), params


def _order_by(query: M3Query, columns: set[str], alias: str = "") -> str:
    """The query's order; NULL goes last in either direction, as in the fixtures."""
    parts = []
    for name, descending in query.order:
        if name not in columns:
            raise ValueError(f"unknown M3 column {name!r}")
        column = alias + _quoted(name)
        parts.append(f"({column} IS NULL), {column}{' DESC' if descending else ''}")
    return " ORDER BY " + ", ".join(parts) if parts else ""


def _select_page(
    conn: sqlite3.Connection,
    table: str,
    query: M3Query,
    fixed: list[tuple[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    columns = _columns(conn, table)
    where, params = _where(query, columns, fixed)
    total = int(conn.execute(f"SELECT COUNT(*) FROM {_quoted(table)}{where}", params).fetchone()[0])
    offset = (query.page.number - 1) * query.page.size
    rows = conn.execute(
        f"SELECT * FROM {_quoted(table)}{where}{_order_by(query, columns)} LIMIT ? OFFSET ?",
        [*params, query.page.size, offset],
    ).fetchall()
    return [dict(row) for row in rows], total


# --------------------------------------------------------------------------- runs
def _run_select(conn: sqlite3.Connection) -> str:
    # Before migration 0008 every stored origin was declared, the column's default.
    declarado = (
        "c.origen_declarado"
        if "origen_declarado" in _columns(conn, "m3_corridas")
        else "1 AS origen_declarado"
    )
    return (
        "SELECT c.run_id, c.ingesta_sha256, c.run_id_m2, c.identidad, c.datos_sinteticos, "
        f"{declarado}, c.estado, c.causa_rechazo, c.creado_en, p.protocolo, "
        "p.n_unidades_previstas "
        "FROM m3_corridas c LEFT JOIN m3_protocolos p ON p.run_id = c.run_id"
    )


def _policy(protocolo: str | None) -> dict[str, Any] | None:
    """The frozen selection policy of the run's protocol (``protocolo.politica``)."""
    if protocolo is None:
        return None
    politica = json.loads(protocolo).get("politica")
    if not isinstance(politica, dict):
        return None
    return {key: politica.get(key) for key in _POLICY_KEYS}


def _run(conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    """``M3Run`` fields of one ``m3_corridas`` row, with every PUBLICADA run of its identity."""
    published = [
        r[0]
        for r in conn.execute(
            "SELECT run_id FROM m3_corridas WHERE estado = 'PUBLICADA' AND identidad = ? "
            "ORDER BY creado_en DESC, run_id DESC",
            (row["identidad"],),
        )
    ]
    return {
        "run_id": row["run_id"],
        "sku": None,
        "ingesta_sha256": row["ingesta_sha256"],
        "run_id_m2": row["run_id_m2"],
        "identidad": row["identidad"],
        "datos_sinteticos": bool(row["datos_sinteticos"]),
        "origen_declarado": bool(row["origen_declarado"]),
        "estado": row["estado"],
        "causa_rechazo": row["causa_rechazo"],
        "creado_en": _utc(row["creado_en"]),
        "policy": _policy(row["protocolo"]),
        "publicadas_misma_identidad": published,
        "aviso": "varias_publicadas_misma_identidad" if len(published) > 1 else None,
    }


def _snapshot(conn: sqlite3.Connection) -> bool:
    """Start the read snapshot; ``False`` when the database has no M3 schema."""
    conn.execute("BEGIN")
    if not _has(conn, "m3_corridas"):
        return False
    conn.row_factory = sqlite3.Row
    return True


def _m2_run_id(conn: sqlite3.Connection, reference: str) -> str | None:
    """The engine's run id an M2 reference names: a platform run id (digits) or the id itself."""
    if reference.isdigit():
        found = conn.execute(
            "SELECT run_id FROM ejecuciones WHERE id = ?", (int(reference),)
        ).fetchone()
        if found is not None:
            return found[0]
    return reference


def list_runs(db_path: Path, run_id_m2: str | None, query: M3Query) -> RunsPage:
    """Runs matching the query (and the M2 reference), newest first, from one snapshot."""
    with _open_readonly(db_path) as conn:
        if conn is None or not _snapshot(conn):
            return RunsPage([], 0, None)
        fixed: list[tuple[str, Any]] = []
        if run_id_m2 is not None:
            resolved = _m2_run_id(conn, run_id_m2)
            if resolved is None:  # a platform run the engine never started
                return RunsPage([], 0, None)
            fixed.append(("run_id_m2", resolved))
        columns = _columns(conn, "m3_corridas")
        where, params = _where(query, columns, fixed, alias="c.")
        total = int(
            conn.execute(f"SELECT COUNT(*) FROM m3_corridas c{where}", params).fetchone()[0]
        )
        order = _order_by(query, columns, alias="c.")
        # latest: the newest PUBLICADA match, resolved over every match before paging.
        published, published_params = _where(
            query, columns, [*fixed, ("estado", "PUBLICADA")], alias="c."
        )
        selected = conn.execute(
            f"SELECT c.run_id FROM m3_corridas c{published}{order} LIMIT 1", published_params
        ).fetchone()
        offset = (query.page.number - 1) * query.page.size
        rows = conn.execute(
            f"{_run_select(conn)}{where}{order} LIMIT ? OFFSET ?",
            [*params, query.page.size, offset],
        ).fetchall()
        return RunsPage([_run(conn, row) for row in rows], total, selected[0] if selected else None)


def _find_run(conn: sqlite3.Connection, run_id: str) -> sqlite3.Row | None:
    return conn.execute(f"{_run_select(conn)} WHERE c.run_id = ?", (run_id,)).fetchone()


def run_status(db_path: Path, run_id: str) -> StatusRead:
    """One run's metadata and durable progress (resolved units), never its metrics."""
    with _open_readonly(db_path) as conn:
        if conn is None or not _snapshot(conn):
            return StatusRead(None, None)
        row = _find_run(conn, run_id)
        if row is None:
            return StatusRead(None, None)
        run = _run(conn, row)
        counts = dict(
            conn.execute(
                "SELECT estado, COUNT(*) FROM m3_unidades WHERE run_id = ? GROUP BY estado",
                (run_id,),
            ).fetchall()
        )
        confirmed, failed = counts.get("confirmada", 0), counts.get("fallida", 0)
        progress = {
            "run_id": run_id,
            "sku": None,
            "ingesta_sha256": run["ingesta_sha256"],
            "n_unidades_previstas": row["n_unidades_previstas"],
            "n_resueltas": confirmed + failed,
            "n_confirmadas": confirmed,
            "n_fallidas": failed,
        }
        return StatusRead(run, progress)


def read_published(db_path: Path, run_id: str, kind: Kind, query: M3Query) -> PublishedRead:
    """One page of a PUBLICADA run's rows of ``kind``; only the run's metadata otherwise."""
    table = _TABLES[kind]
    with _open_readonly(db_path) as conn:
        if conn is None or not _snapshot(conn):
            return PublishedRead(None, [], 0)
        row = _find_run(conn, run_id)
        if row is None:
            return PublishedRead(None, [], 0)
        run = _run(conn, row)
        if run["estado"] != "PUBLICADA":
            return PublishedRead(run, [], 0)
        t_star = None
        late_launches = None
        if kind in ("evidence", "observations") and _has(conn, "m3_cortes"):
            cut = conn.execute(
                "SELECT t_star FROM m3_cortes WHERE run_id = ?", (run_id,)
            ).fetchone()
            if cut is not None:
                t_star = cut[0]
                if kind == "evidence":
                    late_launches = [
                        dict(item)
                        for item in conn.execute(
                            "SELECT sku, causa FROM m3_lanzamientos_tardios "
                            "WHERE run_id = ? ORDER BY sku",
                            (run_id,),
                        )
                    ]
        if not _has(conn, table.name):
            return PublishedRead(run, [], 0, t_star, late_launches)
        rows, total = _select_page(conn, table.name, query, [("run_id", run_id)])
        for item in rows:
            item["ingesta_sha256"] = run["ingesta_sha256"]
            if not table.per_sku:
                item["sku"] = None
            if table.decode is not None:
                table.decode(item, run)
        counts = None
        if kind == "evidence":
            counts = dict(
                conn.execute(
                    "SELECT veredicto, COUNT(*) FROM m3_veredictos WHERE run_id = ? "
                    "GROUP BY veredicto",
                    (run_id,),
                ).fetchall()
            )
        return PublishedRead(run, rows, total, t_star, late_launches, counts)
