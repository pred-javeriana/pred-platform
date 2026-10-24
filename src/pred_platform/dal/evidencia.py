"""Read M3's published evidence from the shared database without evaluating models.

The engine owns the M3 schema. The platform applies its versioned migrations
and reads precalculated verdicts, metrics, portfolio distributions and forecasts.
An unfinished run never appears in ``read_m3_evidence``.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from pred_platform.dal.lectura import _open_readonly


def read_m3_evidence(
    db_path: Path,
    run_id: str,
    *,
    sku: str | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Published evidence by M2 or M3 run id; all values come directly from SQLite.

    With ``sku``, return only the SKU-scoped tables. Portfolio distributions
    remain run-scoped and are available through the unfiltered call.
    Materializes the full matching run(s) in one read snapshot. An M2 alias
    can match several M3 runs; choose a specific M3 id and SKU to bound reads.
    Missing databases and databases predating M3 return an empty mapping.
    """
    with _open_readonly(db_path) as conn:
        if conn is None:
            return {}
        conn.execute("BEGIN")
        if not conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name = 'evidencia_publicada'"
        ).fetchone():
            return {}
        conn.row_factory = sqlite3.Row
        result = {}
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name GLOB 'm3_*' ORDER BY name"
        ).fetchall()
        for (table,) in tables:
            quoted = '"' + table.replace('"', '""') + '"'
            columns = {row[1] for row in conn.execute(f"PRAGMA table_info({quoted})")}
            if "run_id" not in columns or (sku is not None and "sku" not in columns):
                continue
            sql = (
                f"SELECT * FROM {quoted} WHERE run_id IN ("
                "SELECT run_id FROM m3_corridas WHERE estado = 'PUBLICADA' "
                "AND (run_id = ? OR run_id_m2 = ?))"
            )
            params = [run_id, run_id]
            if sku is not None:
                sql += " AND sku = ?"
                params.append(sku)
            result[table] = [dict(row) for row in conn.execute(sql + " ORDER BY rowid", params)]
        return result


def m3_run_status(db_path: Path, run_id: str) -> list[dict[str, Any]]:
    """Run state and durable progress, without exposing unfinished metrics.

    The schema probe and the rows share one read snapshot, so an upgrade between them
    cannot label a newer row with the older schema's declared-origin fallback.
    """
    with _open_readonly(db_path) as conn:
        if conn is None:
            return []
        conn.execute("BEGIN")
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'm3_protocolos'").fetchone():
            return []
        # Before migration 0008 every stored origin was declared, the column's default.
        declarado = (
            "c.origen_declarado"
            if any(
                row[1] == "origen_declarado"
                for row in conn.execute("PRAGMA table_info(m3_corridas)")
            )
            else "1 AS origen_declarado"
        )
        conn.row_factory = sqlite3.Row
        return [
            dict(row)
            for row in conn.execute(
                "SELECT c.run_id, c.run_id_m2, c.estado, c.causa_rechazo, c.datos_sinteticos, "
                f"{declarado}, "
                "p.n_unidades_previstas, "
                "(SELECT COUNT(*) FROM m3_unidades u WHERE u.run_id = c.run_id) AS n_resueltas, "
                "(SELECT COUNT(*) FROM m3_unidades u WHERE u.run_id = c.run_id "
                "AND u.estado = 'confirmada') AS n_confirmadas, "
                "(SELECT COUNT(*) FROM m3_unidades u WHERE u.run_id = c.run_id "
                "AND u.estado = 'fallida') AS n_fallidas "
                "FROM m3_corridas c LEFT JOIN m3_protocolos p ON p.run_id = c.run_id "
                "WHERE c.run_id = ? OR c.run_id_m2 = ? ORDER BY c.creado_en, c.run_id",
                (run_id, run_id),
            )
        ]
