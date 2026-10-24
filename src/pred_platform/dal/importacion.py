"""Import the engine's ingest reports into the DAL (TASK-UI-1.0-A3-G1).

The engine leaves one file per ingestion, ``<data_root>/ingestas/<sha256>.json`` (schema
``pred-engine.ingesta/1``, section 17 of the frontend <-> engine contract). This module validates
those files and upserts them into ``ingestas``, ``bitacora_calidad`` and ``series``. Views never
read the files: they read the DAL.

    uv run python -m pred_platform.dal.importacion            # import what is new
    uv run python -m pred_platform.dal.importacion --status   # compare files and database
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from collections.abc import Sequence
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from pred_platform.contract.models import Severidad, SkuClass

REPORTS_DIR = "ingestas"
SUPPORTED_MAJOR = 1
# First schema version that has the report and topology columns (migration 0002).
REQUIRED_SCHEMA_VERSION = 2
# The published Parquet columns of the engine's output contract 1.4.
PANEL_COLUMNS = ["sku_id", "timestamp", "demand_qty", "lead_time_days", "sku_class"]

_SCHEMA_ID = re.compile(r"^pred-engine\.ingesta/(\d+)$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# The engine's severities -> the DAL's vocabulary (`bitacora_calidad.severidad`).
_SEVERITY: dict[str, Severidad] = {"info": "info", "warning": "advertencia", "error": "error"}


class ReportImportError(Exception):
    """A report file cannot be imported. ``path`` names the file."""

    def __init__(self, path: Path | None, message: str) -> None:
        self.path = path
        where = f"{path.name}: " if path is not None else ""
        super().__init__(f"{where}{message}")


class _Format(BaseModel):
    # Extra fields are ignored so a newer minor version of the file still imports.
    model_config = ConfigDict(extra="ignore")


class SourceFile(_Format):
    datos_sinteticos: bool | None = Field(default=None, strict=True)
    name: str = Field(min_length=1)
    sha256: str
    row_count: int = Field(ge=0)


class Deposit(_Format):
    raw_path: str
    status: Literal["deposited", "already_present_identical", "already_present_different"]
    would_overwrite: bool


class DiagnosticEntry(_Format):
    field: str
    severity: Literal["error", "info"] = "error"
    message: str
    action: str | None = None


class HeaderDiagnostic(_Format):
    status: Literal["accepted", "rejected"]
    entries: list[DiagnosticEntry] = Field(default_factory=list)


class Validation(_Format):
    rows_validated: int = Field(ge=0)
    rows_daily_panel: int = Field(ge=0)
    n_skus: int = Field(ge=0)


class Published(_Format):
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    parquet_path: str
    rows: int = Field(ge=0)
    n_skus: int = Field(ge=0)
    columns: list[str]


class QualityEntry(_Format):
    severity: Literal["info", "warning", "error"]
    code: str
    message: str
    row_index: int | None = Field(default=None, ge=0)
    column: str | None = None


class ErrorDetail(_Format):
    code: str
    stage: str
    detail: str | None = None
    field: str | None = None
    row_index: int | None = None
    column: str | None = None
    source_exception: str | None = None
    retryable: bool = False


class Reserve(_Format):
    t_star: str
    first_reserved: str
    last_observed: str
    reserved_days: int = Field(ge=1)
    fraction: float


class SkuMetrics(_Format):
    sku_id: str = Field(min_length=1)
    n_periods: int = Field(ge=1)
    n_positive: int = Field(ge=0)
    adi: float = Field(gt=0)
    cv2: float = Field(ge=0)
    sku_class: SkuClass


class Thresholds(_Format):
    adi: float
    cv2: float


class Topology(_Format):
    thresholds: Thresholds
    metrics: list[SkuMetrics]


class EngineIngestFile(_Format):
    """Contents of ``ingestas/<sha256>.json`` as the platform understands it."""

    schema_id: str = Field(alias="schema")
    written_at: str
    ingest_id: str
    status: Literal["accepted", "rejected", "failed", "needs_confirmation"]
    source_file: SourceFile
    deposit: Deposit | None = None
    header_diagnostic: HeaderDiagnostic | None = None
    validation: Validation | None = None
    published: Published | None = None
    quality_log: list[QualityEntry] = Field(default_factory=list)
    error: ErrorDetail | None = None
    reserve: Reserve | None = None
    topology: Topology | None = None

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    @model_validator(mode="after")
    def _coherent(self) -> EngineIngestFile:
        if not _SHA256.match(self.ingest_id) or not _SHA256.match(self.source_file.sha256):
            raise ValueError("ingest_id y source_file.sha256 deben ser SHA-256 en minúsculas")
        if self.ingest_id != self.source_file.sha256:
            raise ValueError("ingest_id debe ser el SHA-256 del archivo de origen")
        if self.status == "accepted":
            if self.published is None or self.topology is None or self.reserve is None:
                raise ValueError("una ingesta aceptada exige published, topology y reserve")
            if self.published.columns != PANEL_COLUMNS:
                raise ValueError(f"published.columns debe ser {PANEL_COLUMNS}")
        if self.status == "needs_confirmation" and not (
            self.deposit is not None and self.deposit.would_overwrite
        ):
            raise ValueError("needs_confirmation exige deposit.would_overwrite = true")
        if self.status == "failed" and self.error is None:
            raise ValueError("una ingesta fallida exige error")
        if self.reserve is not None and not all(
            _ISO_DATE.match(v)
            for v in (self.reserve.t_star, self.reserve.first_reserved, self.reserve.last_observed)
        ):
            raise ValueError("las fechas de reserve deben ser AAAA-MM-DD")
        if self.topology is not None:
            skus = [m.sku_id for m in self.topology.metrics]
            if len(skus) != len(set(skus)):
                raise ValueError("topology.metrics repite un SKU")
        return self


@dataclass(frozen=True, slots=True)
class ImportResult:
    imported: int = 0
    failed: tuple[ReportImportError, ...] = field(default=())

    @property
    def ok(self) -> bool:
        return not self.failed


def parse_report(path: Path) -> EngineIngestFile:
    """Read and validate one report file.

    Raises:
        ReportImportError: unreadable JSON, another major version, or invalid content.
    """
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ReportImportError(path, f"no se pudo leer el informe ({exc})") from exc
    if not isinstance(raw, dict):
        raise ReportImportError(path, "el informe debe ser un objeto JSON")
    match = _SCHEMA_ID.match(str(raw.get("schema", "")))
    if match is None:
        raise ReportImportError(path, "falta el campo schema `pred-engine.ingesta/<major>`")
    if int(match.group(1)) != SUPPORTED_MAJOR:
        raise ReportImportError(
            path,
            f"versión mayor {match.group(1)} no soportada; la plataforma entiende "
            f"{SUPPORTED_MAJOR}",
        )
    try:
        report = EngineIngestFile.model_validate(raw)
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(p) for p in first["loc"]) or "(raíz)"
        raise ReportImportError(path, f"{where}: {first['msg']}") from exc
    if path.stem != report.ingest_id:
        raise ReportImportError(path, "el nombre del archivo no coincide con ingest_id")
    return report


def _schema_version(conn: sqlite3.Connection) -> int:
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def _upsert(conn: sqlite3.Connection, report: EngineIngestFile) -> None:
    deposit, published, reserve = report.deposit, report.published, report.reserve
    topology, validation = report.topology, report.validation
    # With "keep both" the file was deposited under a versioned name; show that one so two
    # ingestions of the same original name can be told apart.
    kept_both = deposit is not None and deposit.status == "deposited" and deposit.would_overwrite
    values = {
        "sha256": report.ingest_id,
        "nombre_archivo": Path(deposit.raw_path).name
        if kept_both and deposit is not None
        else report.source_file.name,
        "filas": report.source_file.row_count,
        "skus": validation.n_skus if validation else 0,
        "estado": report.status,
        "estado_deposito": deposit.status if deposit else None,
        "deposito_colision": None if deposit is None else int(deposit.would_overwrite),
        "ruta_raw": deposit.raw_path if deposit else None,
        "ruta_parquet": published.parquet_path if published else None,
        "filas_validadas": validation.rows_validated if validation else None,
        "filas_panel": validation.rows_daily_panel if validation else None,
        "t_star": reserve.t_star if reserve else None,
        "umbral_adi": topology.thresholds.adi if topology else None,
        "umbral_cv2": topology.thresholds.cv2 if topology else None,
        "diagnostico_json": (
            report.header_diagnostic.model_dump_json() if report.header_diagnostic else None
        ),
        "error_json": report.error.model_dump_json() if report.error else None,
        "publicada_en": report.written_at,
    }
    columns = ", ".join(values)
    marks = ", ".join(f":{name}" for name in values)
    updates = ", ".join(f"{name} = excluded.{name}" for name in values if name != "sha256")
    conn.execute(
        f"INSERT INTO ingestas ({columns}) VALUES ({marks}) "
        f"ON CONFLICT(sha256) DO UPDATE SET {updates}",
        values,
    )
    ingesta_id = conn.execute(
        "SELECT id FROM ingestas WHERE sha256 = ?", (report.ingest_id,)
    ).fetchone()[0]

    from pred_platform.dal.procedencia import resolver_procedencia

    try:
        resolver_procedencia(
            conn,
            report.ingest_id,
            report.source_file.datos_sinteticos,
            ingesta_ref_m1=published.sha256 if published is not None else None,
        )
    except ValueError as exc:
        raise ReportImportError(None, str(exc)) from exc

    # Everything below belongs to this ingestion alone: replace it as a unit.
    conn.execute("DELETE FROM bitacora_calidad WHERE ingesta_id = ?", (ingesta_id,))
    conn.executemany(
        "INSERT INTO bitacora_calidad (ingesta_id, severidad, codigo, mensaje, fila, columna) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [
            (
                ingesta_id,
                _SEVERITY[entry.severity],
                entry.code,
                entry.message,
                entry.row_index,
                entry.column,
            )
            for entry in report.quality_log
        ],
    )
    conn.execute("DELETE FROM series WHERE ingesta_id = ?", (ingesta_id,))
    if topology is not None:
        conn.executemany(
            "INSERT INTO series (ingesta_id, sku, n_obs, adi, cv2, n_positive, clase) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (ingesta_id, m.sku_id, m.n_periods, m.adi, m.cv2, m.n_positive, m.sku_class)
                for m in topology.metrics
            ],
        )


def import_reports(db_path: Path, data_root: Path) -> ImportResult:
    """Import every report under ``<data_root>/ingestas`` into the database.

    One bad file does not stop the others: it is returned in ``failed``. Importing the same
    report again leaves the same rows (upsert by SHA-256, the ingestion's series are replaced).

    Raises:
        ReportImportError: the database is missing or its schema is older than migration 0002.
    """
    folder = data_root / REPORTS_DIR
    files = sorted(folder.glob("*.json")) if folder.is_dir() else []
    if not files:
        return ImportResult()
    if not db_path.is_file():
        raise ReportImportError(None, f"no existe la base de datos {db_path}; migre primero")

    imported = 0
    failed: list[ReportImportError] = []
    with closing(sqlite3.connect(db_path, timeout=30)) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        if _schema_version(conn) < REQUIRED_SCHEMA_VERSION:
            raise ReportImportError(
                None, f"la base está en la versión {_schema_version(conn)}; migre primero"
            )
        for path in files:
            try:
                report = parse_report(path)
                with conn:  # one transaction per report
                    _upsert(conn, report)
            except ReportImportError as exc:
                if exc.path is None:
                    exc = ReportImportError(path, str(exc))
                failed.append(exc)
                continue
            except sqlite3.Error as exc:
                failed.append(ReportImportError(path, f"error de base de datos: {exc}"))
                continue
            imported += 1
    return ImportResult(imported=imported, failed=tuple(failed))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m pred_platform.dal.importacion",
        description="Import the engine's ingest reports into the PRED database.",
    )
    parser.add_argument("--db", type=Path, help="database file (default: PRED_DB_PATH)")
    parser.add_argument("--data-root", type=Path, help="data root (default: PRED_DATA_ROOT)")
    parser.add_argument("--status", action="store_true", help="only compare, change nothing")
    args = parser.parse_args(argv)

    from pred_platform.config import Settings  # local: keep the DAL free of app settings
    from pred_platform.dal.migrate import MigrationError, migrate

    try:
        settings = Settings.from_env()
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    db_path = args.db or settings.db_path
    data_root = args.data_root or settings.data_root

    folder = data_root / REPORTS_DIR
    files = sorted(folder.glob("*.json")) if folder.is_dir() else []
    if args.status:
        known: set[str] = set()
        if db_path.is_file():
            with closing(sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True)) as c:
                try:
                    known = {r[0] for r in c.execute("SELECT sha256 FROM ingestas")}
                except sqlite3.OperationalError:
                    known = set()
        pending = [f.stem for f in files if f.stem not in known]
        print(f"{len(files)} report(s) in {folder}; {len(pending)} not in the database yet")
        return 0

    try:
        migrate(db_path)
        result = import_reports(db_path, data_root)
    except (MigrationError, ReportImportError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"imported {result.imported} report(s) from {folder}")
    for failure in result.failed:
        print(f"skipped: {failure}", file=sys.stderr)
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
