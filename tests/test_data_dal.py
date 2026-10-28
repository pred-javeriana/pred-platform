"""The ``dal`` data source: honest about what is stored, explicit about what is blocked."""

import json
import sqlite3
from pathlib import Path

import pytest

from pred_platform.contract import CONTRACT_VERSION, models
from pred_platform.contract.errors import (
    ArtifactMissing,
    ContractInvalid,
    EngineUnavailable,
    InvalidQuery,
)
from pred_platform.contract.loader import parse_as
from pred_platform.dal.schema import create_db
from pred_platform.data import capabilities
from pred_platform.data.dal import DalRepository

SHA_A = "a" * 64
SHA_B = "b" * 64
RUN = "1"


def _insert(conn: sqlite3.Connection, sha: str, name: str, rows: int, skus: int) -> None:
    conn.execute(
        "INSERT INTO ingestas (sha256, nombre_archivo, filas, skus) VALUES (?, ?, ?, ?)",
        (sha, name, rows, skus),
    )
    conn.commit()


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "pred.db"
    conn = create_db(path)
    _insert(conn, SHA_A, "demo.csv", 1200, 4)
    _insert(conn, SHA_B, "ventas.csv", 52000, 40)
    conn.close()
    return path


@pytest.fixture
def repo(db_path: Path) -> DalRepository:
    return DalRepository(db_path)


@pytest.fixture
def empty_repo(tmp_path: Path) -> DalRepository:
    path = tmp_path / "empty.db"
    create_db(path).close()
    return DalRepository(path)


# ------------------------------------------------------------------------ no database
def test_building_the_repository_does_not_touch_the_disk(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "pred.db"
    DalRepository(path)
    assert not path.exists() and not path.parent.exists()


def test_reading_never_creates_the_database(tmp_path: Path) -> None:
    path = tmp_path / "pred.db"
    doc = DalRepository(path).list_ingests()
    assert doc.availability.reason_code == "no_data"
    assert not path.exists()


def test_a_database_without_tables_reads_as_no_data(tmp_path: Path) -> None:
    path = tmp_path / "raw.db"
    sqlite3.connect(path).close()
    repo = DalRepository(path)
    assert repo.list_ingests().items == []
    with pytest.raises(ArtifactMissing):
        repo.get_topology_report(SHA_A)


def test_reading_does_not_modify_the_database(db_path: Path) -> None:
    before = db_path.read_bytes()
    repo = DalRepository(db_path)
    repo.list_ingests()
    repo.get_topology_report(SHA_A)
    assert db_path.read_bytes() == before


# ------------------------------------------------------------------------ list_ingests
def test_list_ingests_reads_the_stored_rows_newest_first(repo: DalRepository) -> None:
    doc = repo.list_ingests()
    assert doc.source == "dal"
    assert doc.availability.status == "available"
    assert [(i.ingest_id, i.name, i.rows, i.n_skus) for i in doc.items] == [
        (SHA_B, "ventas.csv", 52000, 40),
        (SHA_A, "demo.csv", 1200, 4),
    ]
    assert all(i.status is None and i.parquet_path is None for i in doc.items)
    assert doc.page.total == 2


def test_list_ingests_pages_and_counts_all(repo: DalRepository) -> None:
    doc = repo.list_ingests(page=2, size=1)
    assert [i.ingest_id for i in doc.items] == [SHA_A]
    assert (doc.page.number, doc.page.size, doc.page.total) == (2, 1, 2)
    assert repo.list_ingests(page=3, size=1).items == []


def test_list_ingests_of_an_empty_database_is_unavailable_no_data(
    empty_repo: DalRepository,
) -> None:
    doc = empty_repo.list_ingests()
    assert doc.availability.status == "unavailable"
    assert doc.availability.reason_code == "no_data"
    assert doc.availability.blocked_by == []
    assert doc.items == [] and doc.page.total == 0


def test_list_ingests_rejects_a_bad_page(repo: DalRepository) -> None:
    with pytest.raises(InvalidQuery):
        repo.list_ingests(page=0)


def test_the_listing_is_a_valid_contract_document(repo: DalRepository) -> None:
    doc = repo.list_ingests()
    assert parse_as(models.IngestList, doc.model_dump_json()) == doc
    assert doc.schema_version == CONTRACT_VERSION


# ---------------------------------------------------------------------- capabilities
def test_capabilities_come_from_the_registry(repo: DalRepository) -> None:
    doc = repo.get_capabilities()
    assert doc.source == "dal"
    assert {c.id for c in doc.items} == set(capabilities.REGISTRY)
    for item in doc.items:
        state = capabilities.REGISTRY[item.id]
        assert (item.available, item.reason_code, tuple(item.blocked_by)) == (
            state.available,
            state.reason_code,
            state.blocked_by,
        )


def test_the_platform_does_not_claim_engine_only_capabilities() -> None:
    # The engine reads the Parquet because the worker launches it on the ingestion (G3); the
    # Phase 0 log still has no reader in the platform (G8).
    assert capabilities.REGISTRY["parquet_read"].available
    assert not capabilities.REGISTRY["synthetic_log_read"].available
    assert capabilities.blocked_by("synthetic_log_read") == ("G8",)


def test_an_available_capability_has_no_unavailable_form() -> None:
    with pytest.raises(ValueError, match="is available"):
        capabilities.unavailable("parquet_read", "x")


# ----------------------------------------------- unavailable documents name their gap
def _gap(doc: models.Documento) -> tuple[str | None, list[str]]:
    assert doc.availability.status == "unavailable"
    return doc.availability.reason_code, doc.availability.blocked_by


def test_topology_of_an_ingest_loaded_before_the_report_existed_is_unavailable(
    repo: DalRepository,
) -> None:
    # The fixture rows were inserted without a report (estado NULL): there is no topology.
    doc = repo.get_topology_report(SHA_A)
    assert _gap(doc) == ("prerequisite_missing", [])
    assert "antes de que la plataforma guardara" in (doc.availability.detail or "")
    assert doc.ingest_id == SHA_A
    assert doc.thresholds is not None
    assert (doc.thresholds.adi, doc.thresholds.cv2) == (1.32, 0.49)
    assert doc.items == [] and doc.summary.n_skus == 0
    assert set(doc.summary.by_class) == {"smooth", "intermittent", "erratic", "lumpy"}


def test_topology_of_an_ingest_that_is_not_stored_is_missing(repo: DalRepository) -> None:
    with pytest.raises(ArtifactMissing):
        repo.get_topology_report("c" * 64)


@pytest.mark.parametrize("bad", ["abc", "A" * 64, "", "g" * 64])
def test_topology_rejects_a_malformed_ingest_id(repo: DalRepository, bad: str) -> None:
    with pytest.raises(InvalidQuery):
        repo.get_topology_report(bad)


def test_run_status_without_any_run_is_an_empty_state(repo: DalRepository) -> None:
    # Nothing has been launched: no data yet, and no gap blocks it (the worker exists, G3).
    doc = repo.get_run_status(RUN)
    assert _gap(doc) == ("no_data", [])
    assert doc.tasks == [] and doc.ejecucion is None


def test_selection_list_without_any_run_is_an_empty_state(repo: DalRepository) -> None:
    # The selection is real (G4): with nothing launched there is no data, and no gap blocks it.
    doc = repo.list_sku_selections(RUN)
    assert _gap(doc) == ("no_data", [])
    assert doc.items == [] and doc.run_id == RUN


def test_m3_reads_are_available_and_answer_no_data_without_m3_runs(repo: DalRepository) -> None:
    # M3's champion and verdicts are read (contract v2): an absent run is no data, not a gap.
    assert capabilities.REGISTRY["champion_selection"].available
    assert capabilities.REGISTRY["retrospective_validation"].available
    runs = repo.list_m3_runs(run_id_m2=RUN)
    evidence = repo.get_m3_evidence("m3-ninguna")
    for doc in (runs, evidence, repo.get_m3_run_status("m3-ninguna")):
        assert (doc.availability.status, doc.availability.reason_code) == (
            "unavailable",
            "no_data",
        )
        assert doc.availability.detail
    assert runs.items == [] and runs.selected_run_id is None
    assert evidence.run is None and evidence.funnel is None


def test_synthetic_list_is_unavailable_and_blocked_by_g8(repo: DalRepository) -> None:
    doc = repo.list_synthetic_runs()
    assert _gap(doc) == ("not_implemented", ["G8"])
    assert doc.items == []


def test_every_unavailable_document_matches_the_registry(repo: DalRepository) -> None:
    pairs = [
        (repo.list_synthetic_runs(), "synthetic_log_read"),
    ]
    for doc, capability in pairs:
        state = capabilities.REGISTRY[capability]  # type: ignore[index]
        assert doc.availability.reason_code == state.reason_code
        assert tuple(doc.availability.blocked_by) == state.blocked_by
        assert doc.availability.detail


def test_unavailable_documents_are_valid_contract_documents(repo: DalRepository) -> None:
    for doc in (
        repo.get_topology_report(SHA_A),
        repo.get_run_status(RUN),
        repo.list_sku_selections(RUN),
        repo.list_synthetic_runs(),
        repo.get_capabilities(),
    ):
        assert parse_as(type(doc), json.loads(doc.model_dump_json())) == doc


def test_filters_are_still_validated_while_unavailable(repo: DalRepository) -> None:
    with pytest.raises(InvalidQuery):
        repo.get_topology_report(SHA_A, sku_class="Smooth")
    with pytest.raises(InvalidQuery):
        repo.get_run_status(RUN, estado="completada")
    with pytest.raises(InvalidQuery):
        repo.list_sku_selections(RUN, sort="price")
    with pytest.raises(InvalidQuery):
        repo.list_synthetic_runs(size=0)


# --------------------------------------------------- documents with no empty form
@pytest.mark.parametrize(
    ("call", "gap"),
    [
        (lambda r: r.get_synthetic_run("fase0_x"), "G8"),
        (lambda r: r.download_synthetic_artifact("fase0_x"), "G8"),
    ],
)
def test_documents_without_an_empty_form_are_missing_and_name_the_gap(
    repo: DalRepository, call: object, gap: str
) -> None:
    with pytest.raises(ArtifactMissing) as raised:
        call(repo)  # type: ignore[operator]
    assert gap in raised.value.blocked_by
    assert gap in (raised.value.to_error_info().detail or "")


def test_an_ingest_loaded_before_the_report_existed_has_no_report(repo: DalRepository) -> None:
    with pytest.raises(ArtifactMissing, match="se cargó antes"):
        repo.get_ingest_report(SHA_A)
    with pytest.raises(ArtifactMissing, match="No existe la ingesta"):
        repo.get_ingest_report("c" * 64)


def test_submit_ingest_needs_the_engine(repo: DalRepository) -> None:
    # A repository built without an ingest runner cannot take uploads; no open gap blocks it.
    with pytest.raises(EngineUnavailable) as raised:
        repo.submit_ingest("a.csv", b"x")
    assert raised.value.blocked_by == ()
    info = raised.value.to_error_info()
    assert (info.code, info.retryable) == ("platform.engine_unavailable", True)


def test_the_source_is_dal(repo: DalRepository) -> None:
    assert repo.source == "dal"
    assert repo.get_run_status(RUN).source == "dal"


def test_a_corrupt_database_is_not_hidden(tmp_path: Path) -> None:
    path = tmp_path / "bad.db"
    path.write_bytes(b"this is not a sqlite database" * 50)
    with pytest.raises(sqlite3.DatabaseError):
        DalRepository(path).list_ingests()


def test_contract_invalid_is_what_a_broken_document_raises() -> None:
    with pytest.raises(ContractInvalid):
        parse_as(models.IngestList, '{"items": []}')
