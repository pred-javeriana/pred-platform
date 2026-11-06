"""One read-only snapshot of explicitly selected M3 seed publications."""

from __future__ import annotations

import json
import math
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from pred_platform.dal.evidencia import _utc
from pred_platform.dal.lectura import _open_readonly
from pred_platform.evidence.group import calculate_significance
from pred_platform.evidence.lumpy import LumpyEvidence, calculate_lumpy
from pred_platform.evidence.models import (
    EvidenceInvalid,
    Exclusion,
    GroupEvidence,
    GroupRunOption,
    GroupSignificance,
    RunEvidence,
    Verdict,
)


def read_significance(
    db_path: Path, run_ids: Sequence[str], *, preview: bool = False
) -> GroupSignificance:
    """Read only PUBLICADA real-data runs; never migrate, publish or change rows."""
    if len(run_ids) != (2 if preview else 3) or len(set(run_ids)) != len(run_ids):
        raise EvidenceInvalid("Seleccione una corrida distinta por semilla.")
    with _snapshot(db_path) as conn:
        return calculate_significance(tuple(_run(conn, r) for r in run_ids), preview=preview)


def read_group(db_path: Path, run_ids: Sequence[str], *, preview: bool = False) -> GroupEvidence:
    """Both computations from the same read-only snapshot, without a cache or sidecar."""
    if len(run_ids) != (2 if preview else 3) or len(set(run_ids)) != len(run_ids):
        raise EvidenceInvalid("Seleccione una corrida distinta por semilla.")
    with _snapshot(db_path) as conn:
        runs = tuple(sorted((_run(conn, r) for r in run_ids), key=lambda r: r.seed))
        significance = calculate_significance(runs, preview=preview)
        return GroupEvidence(significance, _lumpy(conn, runs))


@contextmanager
def _snapshot(db_path: Path) -> Iterator[sqlite3.Connection]:
    with _open_readonly(db_path) as conn:
        if conn is None:
            raise EvidenceInvalid("La base de datos no existe.")
        conn.row_factory = sqlite3.Row
        conn.execute("BEGIN")
        try:
            yield conn
        except (
            sqlite3.Error,
            ValidationError,
            KeyError,
            TypeError,
            ValueError,
            ArithmeticError,
        ) as exc:
            if isinstance(exc, EvidenceInvalid):
                raise
            raise EvidenceInvalid(f"La evidencia del grupo no es válida: {exc}") from exc


def list_group_runs(db_path: Path) -> tuple[GroupRunOption, ...]:
    """Published real-data choices only; incomplete or legacy protocols are not seed evidence."""
    if not db_path.is_file():
        return ()
    with _snapshot(db_path) as conn:
        rows = conn.execute(
            "SELECT c.run_id,c.run_id_m2,c.ingesta_sha256,c.creado_en,p.protocolo "
            "FROM m3_corridas c JOIN m3_protocolos p ON p.run_id=c.run_id "
            "WHERE c.estado='PUBLICADA' AND c.datos_sinteticos=0 AND c.origen_declarado=1 "
            "ORDER BY c.creado_en DESC,c.run_id"
        )
        options = []
        for row in rows:
            protocol = _object(row["protocolo"])
            config = protocol.get("configuracion_m2", {})
            if not isinstance(config, dict):
                raise EvidenceInvalid("La configuración M2 publicada no es un objeto.")
            seed = config.get("seed")
            if type(seed) is int and seed in (0, 1, 2) and "bloques" in protocol:
                options.append(
                    GroupRunOption(
                        row["run_id"],
                        seed,
                        row["ingesta_sha256"],
                        row["run_id_m2"],
                        _utc(row["creado_en"]),
                    )
                )
        return tuple(options)


def _object(text: str) -> dict[str, Any]:
    value = json.loads(text)
    if not isinstance(value, dict):
        raise EvidenceInvalid("Se esperaba un objeto JSON en los metadatos publicados.")
    return value


def _run(conn: sqlite3.Connection, run_id: str) -> RunEvidence:
    row = conn.execute(
        "SELECT c.*, p.protocolo FROM m3_corridas c "
        "LEFT JOIN m3_protocolos p ON p.run_id = c.run_id WHERE c.run_id = ?",
        (run_id,),
    ).fetchone()
    if row is None or row["estado"] != "PUBLICADA":
        raise EvidenceInvalid(f"La corrida {run_id} no está PUBLICADA.")
    if row["datos_sinteticos"] != 0 or row["origen_declarado"] != 1:
        raise EvidenceInvalid(f"La corrida {run_id} no declara datos reales.")
    protocol = _object(row["protocolo"])
    seed = protocol["configuracion_m2"]["seed"]
    if type(seed) is not int:
        raise EvidenceInvalid(f"La corrida {run_id} no tiene una semilla entera publicada.")
    if "bloques" not in protocol:
        raise EvidenceInvalid(f"La corrida {run_id} no publica los bloques de la reserva.")
    for block in ("origenes_seleccion", "origenes_prueba"):
        limits = protocol["bloques"][block]
        if date.fromisoformat(limits["primero"]) > date.fromisoformat(limits["ultimo"]):
            raise EvidenceInvalid("Los límites del bloque publicado están invertidos.")
    minimum = protocol["politica"]["n_min_dm"]
    if type(minimum) is not int or minimum < 1:
        raise EvidenceInvalid("n_min_dm no es un entero positivo.")
    verdicts = []
    for record in conn.execute(
        "SELECT * FROM m3_veredictos WHERE run_id = ? ORDER BY sku", (run_id,)
    ):
        data = dict(record)
        data["modelos_evaluados"] = json.loads(data["modelos_evaluados"])
        verdicts.append(Verdict.model_validate(data))
    exclusions = tuple(
        Exclusion.model_validate(dict(record))
        for record in conn.execute(
            "SELECT * FROM m3_exclusiones WHERE run_id = ? ORDER BY sku", (run_id,)
        )
    )
    return RunEvidence(
        run_id,
        seed,
        row["ingesta_sha256"],
        row["run_id_m2"],
        _object(row["identidad"]),
        protocol,
        tuple(verdicts),
        exclusions,
    )


def _lumpy(conn: sqlite3.Connection, runs: Sequence[RunEvidence]) -> LumpyEvidence:
    windows: dict[tuple[str, str, str], dict[str, float]] = {}
    published: dict[tuple[str, str], tuple[int, float | None]] = {}
    champions: dict[str, str] = {}
    for run in runs:
        confirmation = conn.execute(
            "SELECT familia_campeona,efecto_n_skus,efecto_mediana_r "
            "FROM m3_confirmaciones WHERE run_id=? AND sku_class='lumpy'",
            (run.run_id,),
        ).fetchone()
        if confirmation is not None:
            champions[run.run_id] = confirmation["familia_campeona"]
            published[run.run_id, "seasonal_naive"] = (
                confirmation["efecto_n_skus"],
                confirmation["efecto_mediana_r"],
            )
        comparison = conn.execute(
            "SELECT n_skus,mediana_razon FROM m3_comparaciones "
            "WHERE run_id=? AND sku_class='lumpy' AND competidora='classical'",
            (run.run_id,),
        ).fetchone()
        if comparison is not None:
            published[run.run_id, "classical"] = (comparison["n_skus"], comparison["mediana_razon"])
        # The IN subquery gives SQLite a small SKU set and indexed (run, SKU)
        # metric reads. Do not scan every metric or any forecast row of the run.
        rows = conn.execute(
            "SELECT sku,candidato_id,ventana,valor FROM m3_metricas "
            "WHERE run_id=? AND sku IN "
            "(SELECT sku FROM m3_veredictos WHERE run_id=? AND sku_class='lumpy') "
            "AND metrica='rmse' AND ventana<>'agregada' AND valor IS NOT NULL "
            "ORDER BY sku,candidato_id,ventana",
            (run.run_id, run.run_id),
        )
        for row in rows:
            origin = row["ventana"]
            date.fromisoformat(origin)
            value = row["valor"]
            if not isinstance(value, (float, int)) or not math.isfinite(value) or value < 0:
                raise EvidenceInvalid("Una ventana lumpy tiene RMSE no finito o negativo.")
            windows.setdefault((run.run_id, row["sku"], row["candidato_id"]), {})[origin] = value
    return calculate_lumpy(runs, windows, published, champions)
