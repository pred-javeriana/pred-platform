"""Real reads of the ingestion side from the DAL (G1): list, report and topology."""

from __future__ import annotations

import json
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator
from pydantic import BaseModel

from pred_platform.app.main import create_app
from pred_platform.config import Settings
from pred_platform.contract.errors import ArtifactMissing
from pred_platform.contract.resources import read_schema
from pred_platform.dal.importacion import import_reports
from pred_platform.dal.migrate import apply_migrations, load_migrations
from pred_platform.dal.schema import create_db
from pred_platform.data import capabilities
from pred_platform.data.dal import DalRepository

_FIXTURES = Path(__file__).parent / "fixtures" / "motor"
ACCEPTED = json.loads((_FIXTURES / "ingesta.accepted.json").read_text())["ingest_id"]
FAILED = json.loads((_FIXTURES / "ingesta.failed.json").read_text())["ingest_id"]
REJECTED = json.loads((_FIXTURES / "ingesta.rejected.json").read_text())["ingest_id"]
CONFLICT = json.loads((_FIXTURES / "ingesta.needs_confirmation.json").read_text())["ingest_id"]


def _place(root: Path, label: str) -> None:
    source = _FIXTURES / f"ingesta.{label}.json"
    ingest_id = json.loads(source.read_text())["ingest_id"]
    folder = root / "ingestas"
    folder.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, folder / f"{ingest_id}.json")


@pytest.fixture
def repo(tmp_path: Path) -> DalRepository:
    root = tmp_path / "data"
    for label in ("accepted", "failed", "rejected", "needs_confirmation"):
        _place(root, label)
    db = tmp_path / "pred.db"
    create_db(db).close()
    result = import_reports(db, root)
    assert result.ok and result.imported == 4
    return DalRepository(db, Path("data"))


def _conforms(kind: str, document: BaseModel) -> None:
    """The document validates against the contract's published JSON Schema."""
    validator = Draft202012Validator(
        read_schema(kind), format_checker=Draft202012Validator.FORMAT_CHECKER
    )
    data: Any = json.loads(document.model_dump_json())
    assert [e.message for e in validator.iter_errors(data)] == []


# ---------------------------------------------------------------------------- list
def test_the_list_shows_every_ingestion_with_its_status(repo: DalRepository) -> None:
    doc = repo.list_ingests()

    assert doc.availability.status == "available"
    assert {i.ingest_id for i in doc.items} == {ACCEPTED, FAILED, REJECTED, CONFLICT}
    by_id = {i.ingest_id: i for i in doc.items}
    assert by_id[ACCEPTED].status == "accepted"
    assert by_id[ACCEPTED].parquet_path == "data/processed/demo_canonico.parquet"
    assert by_id[ACCEPTED].rows == 270 and by_id[ACCEPTED].n_skus == 3
    assert by_id[ACCEPTED].published_at is not None
    assert by_id[FAILED].status == "failed" and by_id[FAILED].parquet_path is None
    assert by_id[CONFLICT].status == "needs_confirmation"
    _conforms("ingest_list", doc)


def test_the_list_pages_over_the_real_total(repo: DalRepository) -> None:
    doc = repo.list_ingests(page=2, size=3)
    assert len(doc.items) == 1
    assert (doc.page.number, doc.page.size, doc.page.total) == (2, 3, 4)


# --------------------------------------------------------------------------- report
def test_the_accepted_report_rebuilds_from_the_dal(repo: DalRepository) -> None:
    report = repo.get_ingest_report(ACCEPTED)

    assert report.source == "dal" and report.availability.status == "available"
    assert (report.status, report.source_file.name, report.source_file.row_count) == (
        "accepted",
        "demo_canonico.csv",
        270,
    )
    assert report.deposit is not None
    assert (report.deposit.raw_path, report.deposit.status, report.deposit.would_overwrite) == (
        "data/raw/demo_canonico.csv",
        "deposited",
        False,
    )
    assert report.validation is not None and report.validation.n_skus == 3
    assert report.published is not None
    assert report.published.parquet_path == "data/processed/demo_canonico.parquet"
    assert report.published.columns[-1] == "sku_class"
    assert report.quality_log == [] and report.error is None
    _conforms("ingest_report", report)


def test_the_rejected_report_keeps_the_actionable_header_diagnostic(repo: DalRepository) -> None:
    report = repo.get_ingest_report(REJECTED)

    assert report.status == "rejected"
    assert report.header_diagnostic is not None
    assert report.header_diagnostic.status == "rejected"
    entry = report.header_diagnostic.entries[0]
    assert (entry.field, entry.action) == ("timestamp", "Renombrar 'Date' a 'timestamp'")
    assert report.error is not None and report.error.code == "ingest.header_rejected"
    assert report.published is None
    _conforms("ingest_report", report)


def test_the_failed_report_names_the_row_and_column(repo: DalRepository) -> None:
    report = repo.get_ingest_report(FAILED)

    assert report.status == "failed"
    assert report.error is not None
    assert (report.error.code, report.error.column) == ("ingest.schema_barrier", "lead_time_days")
    assert report.error.row_index is not None
    assert report.quality_log[0].row_index == report.error.row_index
    assert report.quality_log[0].severity == "error"
    _conforms("ingest_report", report)


def test_a_name_conflict_waits_for_confirmation(repo: DalRepository) -> None:
    report = repo.get_ingest_report(CONFLICT)

    assert report.status == "needs_confirmation"
    assert report.deposit is not None
    assert report.deposit.would_overwrite is True
    assert report.deposit.status == "already_present_different"
    assert report.quality_log[0].severity == "advertencia"
    assert report.published is None
    _conforms("ingest_report", report)


# ------------------------------------------------------------------------ topology
def test_the_topology_summarises_the_whole_ingestion(repo: DalRepository) -> None:
    doc = repo.get_topology_report(ACCEPTED)

    assert doc.availability.status == "available"
    assert doc.t_star == "2026-03-13"
    assert doc.thresholds is not None
    assert (doc.thresholds.adi, doc.thresholds.cv2) == (1.32, 0.49)
    assert doc.summary.n_skus == 3
    assert doc.summary.by_class == {"smooth": 1, "intermittent": 1, "erratic": 0, "lumpy": 1}
    assert [m.sku_id for m in doc.items] == ["FIL-0001", "FIL-0002", "FIL-0003"]
    assert doc.items[1].model_dump() == {
        "sku_id": "FIL-0002",
        "n_periods": 72,
        "n_positive": 24,
        "adi": 3.0,
        "cv2": 0.0,
        "sku_class": "intermittent",
    }
    assert doc.page.total == 3
    _conforms("topology_report", doc)


def test_the_topology_filters_in_sql_but_keeps_the_full_summary(repo: DalRepository) -> None:
    everything = repo.get_topology_report(ACCEPTED)
    lumpy = repo.get_topology_report(ACCEPTED, sku_class="lumpy")

    assert [m.sku_id for m in lumpy.items] == ["FIL-0003"]
    assert lumpy.page.total == 1
    assert lumpy.summary == everything.summary


def test_the_topology_searches_sku_text_case_insensitively_and_literally(
    repo: DalRepository,
) -> None:
    assert [m.sku_id for m in repo.get_topology_report(ACCEPTED, q=" fil-0003 ").items] == [
        "FIL-0003"
    ]
    assert repo.get_topology_report(ACCEPTED, q="%").items == []  # % is not a wildcard
    assert repo.get_topology_report(ACCEPTED, q="_").items == []


@pytest.mark.parametrize(
    ("sort", "descending", "first"),
    [
        ("sku_id", True, "FIL-0003"),
        ("adi", False, "FIL-0001"),
        ("cv2", True, "FIL-0003"),
        ("n_positive", False, "FIL-0002"),
    ],
)
def test_the_topology_sorts_by_whitelisted_keys(
    repo: DalRepository, sort: str, descending: bool, first: str
) -> None:
    doc = repo.get_topology_report(ACCEPTED, sort=sort, descending=descending)
    assert doc.items[0].sku_id == first


def test_the_topology_pages_after_filtering(repo: DalRepository) -> None:
    doc = repo.get_topology_report(ACCEPTED, size=2, page=2)
    assert [m.sku_id for m in doc.items] == ["FIL-0003"]
    assert (doc.page.number, doc.page.size, doc.page.total) == (2, 2, 3)
    assert repo.get_topology_report(ACCEPTED, size=2, page=5).items == []


@pytest.mark.parametrize("ingest_id", [FAILED, REJECTED, CONFLICT])
def test_an_ingestion_that_was_not_accepted_has_no_topology(
    repo: DalRepository, ingest_id: str
) -> None:
    doc = repo.get_topology_report(ingest_id)

    assert doc.availability.status == "unavailable"
    assert doc.availability.reason_code == "prerequisite_missing"
    assert "no fue aceptada" in (doc.availability.detail or "")
    assert doc.items == [] and doc.summary.n_skus == 0
    assert doc.thresholds is None
    _conforms("topology_report", doc)


@pytest.mark.parametrize("column", ["adi", "cv2", "n_positive"])
def test_una_metrica_nula_no_se_inventa_ni_cuenta_en_la_pagina(repo: DalRepository, column):
    with closing(sqlite3.connect(repo.db_path)) as conn, conn:
        conn.execute(f"UPDATE series SET {column}=NULL WHERE sku='FIL-0001'")
    doc = repo.get_topology_report(ACCEPTED, size=1)
    assert doc.page.total == 2 and len(doc.items) == 1
    assert doc.items[0].sku_id == "FIL-0002"
    assert repo.get_topology_report(ACCEPTED, q="FIL-0001").page.total == 0
    assert repo.get_topology_report(ACCEPTED, page=2, size=1).items[0].sku_id == "FIL-0003"
    _conforms("topology_report", doc)


def test_las_metricas_cero_se_conservan(repo: DalRepository):
    with closing(sqlite3.connect(repo.db_path)) as conn, conn:
        conn.execute("UPDATE series SET adi=0.2, cv2=0, n_positive=0 WHERE sku='FIL-0001'")
    [row] = repo.get_topology_report(ACCEPTED, q="FIL-0001").items
    assert (row.adi, row.cv2, row.n_positive) == (0.2, 0.0, 0)


@pytest.mark.parametrize("column", ["umbral_adi", "umbral_cv2"])
def test_no_suplanta_un_umbral_ausente(repo: DalRepository, column):
    with closing(sqlite3.connect(repo.db_path)) as conn, conn:
        conn.execute(f"UPDATE ingestas SET {column}=NULL WHERE sha256=?", (ACCEPTED,))
    doc = repo.get_topology_report(ACCEPTED)
    assert doc.thresholds is None and doc.items == []
    assert doc.availability.status == "unavailable"
    _conforms("topology_report", doc)


def test_la_pagina_de_una_ingesta_rechazada_oculta_los_umbrales(repo: DalRepository):
    app = create_app(
        Settings(data_root=repo.db_path.parent / "data", db_path=repo.db_path, worker="off")
    )
    app.state.repository = repo
    response = TestClient(app).get(f"/datos?ingest_id={REJECTED}")
    assert response.status_code == 200
    assert "Umbral ADI" not in response.text and "Umbral CV²" not in response.text
    assert 'class="pred-chart__threshold"' not in response.text
    assert "El archivo necesita correcciones" in response.text
    assert "Topología de demanda" not in response.text


def test_la_pagina_de_una_ingesta_sin_umbral_no_lo_inventa(repo: DalRepository):
    with closing(sqlite3.connect(repo.db_path)) as conn, conn:
        conn.execute("UPDATE ingestas SET umbral_adi=NULL WHERE sha256=?", (ACCEPTED,))
    app = create_app(
        Settings(data_root=repo.db_path.parent / "data", db_path=repo.db_path, worker="off")
    )
    app.state.repository = repo
    response = TestClient(app).get(f"/datos?ingest_id={ACCEPTED}")
    assert response.status_code == 200
    assert "Umbral ADI" not in response.text and "Umbral CV²" not in response.text
    assert 'class="pred-chart__threshold"' not in response.text


# ----------------------------------------------------------------- compatibility
def test_the_capabilities_now_declare_the_ingestion_side_available(repo: DalRepository) -> None:
    by_id = {c.id: c for c in repo.get_capabilities().items}
    assert by_id["ingest_report_persisted"].available is True
    assert by_id["topology_persisted"].available is True
    assert by_id["parquet_read"].available is True
    assert capabilities.blocked_by("topology_persisted") == ()


def test_a_database_still_at_version_1_reads_without_failing(tmp_path: Path) -> None:
    db = tmp_path / "old.db"
    with closing(sqlite3.connect(db)) as conn:
        apply_migrations(conn, load_migrations()[:1])
        conn.execute(
            "INSERT INTO ingestas (sha256, nombre_archivo, filas, skus) VALUES (?, 'v.csv', 9, 1)",
            ("d" * 64,),
        )
        conn.commit()
    repo = DalRepository(db, Path("data"))

    listing = repo.list_ingests()
    assert [(i.name, i.status, i.parquet_path) for i in listing.items] == [("v.csv", None, None)]
    with pytest.raises(ArtifactMissing, match="se cargó antes"):
        repo.get_ingest_report("d" * 64)
    assert repo.get_topology_report("d" * 64).availability.reason_code == "prerequisite_missing"
