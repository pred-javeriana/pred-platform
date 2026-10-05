"""The fixture data source: every operation, in every scenario."""

import hashlib

import pytest

from pred_platform.contract import models
from pred_platform.contract.errors import ArtifactMissing, InvalidQuery
from pred_platform.data.fixtures import FixtureRepository, sample_csv
from pred_platform.data.scenarios import SCENARIOS, get_scenario

RUN = "1"
UNKNOWN_SHA = "f" * 64


def total(doc: object) -> int:
    """``page.total`` of a document that must have a page."""
    page = getattr(doc, "page", None)
    assert page is not None
    return page.total


@pytest.fixture
def repo() -> FixtureRepository:
    return FixtureRepository("normal")


# ------------------------------------------------------------------------- scenarios
def test_there_are_three_scenarios_and_normal_is_the_default() -> None:
    assert sorted(SCENARIOS) == ["errores", "normal", "vacio"]
    assert FixtureRepository().scenario.name == "normal"


def test_an_unknown_scenario_lists_the_valid_ones() -> None:
    with pytest.raises(ValueError, match=r"\['errores', 'normal', 'vacio'\]"):
        get_scenario("otro")


def test_every_example_a_scenario_names_exists_and_validates() -> None:
    for scenario in SCENARIOS.values():
        repo = FixtureRepository(scenario.name)
        repo.get_capabilities()
        repo.list_ingests()
        repo.list_synthetic_runs()


def test_the_source_is_fixture(repo: FixtureRepository) -> None:
    assert repo.source == "fixture"
    assert repo.get_capabilities().source == "fixture"


def test_documents_are_independent_copies(repo: FixtureRepository) -> None:
    first = repo.list_ingests()
    first.items.clear()
    assert len(repo.list_ingests().items) == 2


# --------------------------------------------------------------------------- ingests
def test_list_ingests_pages_and_counts_everything(repo: FixtureRepository) -> None:
    doc = repo.list_ingests(page=2, size=1)
    assert [i.name for i in doc.items] == ["ventas_2025.csv"]
    assert (doc.page.number, doc.page.size, total(doc)) == (2, 1, 2)


def test_list_ingests_rejects_a_bad_page(repo: FixtureRepository) -> None:
    with pytest.raises(InvalidQuery):
        repo.list_ingests(page=0)
    with pytest.raises(InvalidQuery):
        repo.list_ingests(size=501)


def test_get_ingest_report_finds_each_scenario_report(repo: FixtureRepository) -> None:
    first_id = repo.list_ingests().items[0].ingest_id
    assert first_id is not None
    accepted = repo.get_ingest_report(first_id)
    assert accepted.status == "accepted"


def test_get_ingest_report_of_an_unknown_id_is_missing(repo: FixtureRepository) -> None:
    with pytest.raises(ArtifactMissing) as raised:
        repo.get_ingest_report(UNKNOWN_SHA)
    assert raised.value.to_error_info().code == "platform.artifact_missing"


def test_submit_ingest_by_scenario() -> None:
    normal = FixtureRepository("normal").submit_ingest("a.csv", b"x")
    assert normal.status == "accepted"
    errors = FixtureRepository("errores")
    assert errors.submit_ingest("a.csv", b"x").status == "needs_confirmation"
    assert errors.submit_ingest("a.csv", b"x", confirm_overwrite=True).status == "failed"


@pytest.mark.parametrize(("name", "content"), [("a.txt", b"x"), ("a.csv", b"")])
def test_submit_ingest_rejects_a_bad_call(
    repo: FixtureRepository, name: str, content: bytes
) -> None:
    with pytest.raises(InvalidQuery):
        repo.submit_ingest(name, content)


# ------------------------------------------------------------------------- topology
def _topology(repo: FixtureRepository, **kwargs: object) -> models.TopologyReport:
    ingest_id = repo.list_ingests().items[0].ingest_id
    return repo.get_topology_report(ingest_id, **kwargs)  # type: ignore[arg-type]


def test_topology_filters_by_class_and_keeps_the_full_summary(repo: FixtureRepository) -> None:
    everything = _topology(repo)
    smooth = _topology(repo, sku_class="smooth")
    assert smooth.items
    assert {row.sku_class for row in smooth.items} == {"smooth"}
    assert total(smooth) == len(smooth.items) < total(everything)
    assert smooth.summary == everything.summary


def test_topology_searches_by_sku_text(repo: FixtureRepository) -> None:
    doc = _topology(repo, q="fil-0002")
    assert [row.sku_id for row in doc.items] == ["FIL-0002"]


@pytest.mark.parametrize("key", ["sku_id", "adi", "cv2", "n_positive"])
def test_topology_sorts_in_both_directions(repo: FixtureRepository, key: str) -> None:
    ascending = [getattr(r, key) for r in _topology(repo, sort=key).items]
    descending = [getattr(r, key) for r in _topology(repo, sort=key, descending=True).items]
    assert ascending == sorted(ascending)
    assert descending == sorted(descending, reverse=True)


def test_topology_pages_after_filtering(repo: FixtureRepository) -> None:
    doc = _topology(repo, size=3, page=2)
    assert len(doc.items) == 1
    assert total(doc) == 4


def test_topology_rejects_bad_parameters(repo: FixtureRepository) -> None:
    with pytest.raises(InvalidQuery):
        _topology(repo, sku_class="Smooth")
    with pytest.raises(InvalidQuery):
        _topology(repo, sort="price")


def test_topology_of_an_unknown_ingest_is_missing(repo: FixtureRepository) -> None:
    with pytest.raises(ArtifactMissing):
        repo.get_topology_report(UNKNOWN_SHA)


# ---------------------------------------------------------------------- run status
def test_run_status_filters_tasks_but_keeps_the_whole_progress(repo: FixtureRepository) -> None:
    everything = repo.get_run_status(RUN)
    done = repo.get_run_status(RUN, estado="exitosa")
    assert {t.estado for t in done.tasks} == {"exitosa"}
    assert total(done) == len(done.tasks) < total(everything)
    assert done.progress == everything.progress


def test_run_status_filters_by_sku_and_family(repo: FixtureRepository) -> None:
    first = repo.get_run_status(RUN).tasks[0]
    by_sku = repo.get_run_status(RUN, sku=first.sku)
    assert by_sku.tasks and {t.sku for t in by_sku.tasks} == {first.sku}
    assert first.family is not None
    by_family = repo.get_run_status(RUN, family=first.family)
    assert {t.family for t in by_family.tasks} == {first.family}


def test_run_status_pages(repo: FixtureRepository) -> None:
    doc = repo.get_run_status(RUN, size=3, page=2)
    assert len(doc.tasks) == 3
    assert total(doc) == 7


def test_run_status_rejects_an_engine_state_name(repo: FixtureRepository) -> None:
    with pytest.raises(InvalidQuery):
        repo.get_run_status(RUN, estado="completada")


def test_run_status_of_an_unknown_run_is_missing(repo: FixtureRepository) -> None:
    with pytest.raises(ArtifactMissing):
        repo.get_run_status("999")


def test_the_errors_scenario_serves_every_task_state_in_run_2() -> None:
    repo = FixtureRepository("errores")
    assert total(repo.get_run_status(RUN)) == 7  # run 1 is still the normal one
    doc = repo.get_run_status("2")
    assert {t.estado for t in doc.tasks} == {
        "pendiente",
        "ejecutando",
        "exitosa",
        "fallida",
        "no_ejecutable",
    }
    assert all(t.error is not None for t in doc.tasks if t.estado == "fallida")


# -------------------------------------------------------------------- sku selection
def test_selection_list_filters_and_sorts(repo: FixtureRepository) -> None:
    everything = repo.list_sku_selections(RUN)
    assert total(everything) == len(everything.items) == 4
    smooth = repo.list_sku_selections(RUN, sku_class="smooth")
    assert {row.sku_class for row in smooth.items} == {"smooth"}
    searched = repo.list_sku_selections(RUN, q="0003")
    assert [row.sku_id for row in searched.items] == ["FIL-0003"]
    reverse = repo.list_sku_selections(RUN, sort="sku_id", descending=True)
    assert [r.sku_id for r in reverse.items] == sorted(
        (r.sku_id for r in everything.items), reverse=True
    )


def test_selection_list_filters_by_family_and_state(repo: FixtureRepository) -> None:
    everything = repo.list_sku_selections(RUN)
    family = everything.items[0].families[0].family
    filtered = repo.list_sku_selections(RUN, family=family)
    assert filtered.items
    assert all(any(f.family == family for f in row.families) for row in filtered.items)
    by_state = repo.list_sku_selections(RUN, estado="exitosa")
    assert all(any(f.estado == "exitosa" for f in row.families) for row in by_state.items)


def test_selection_list_sorts_by_best_value_with_missing_last(repo: FixtureRepository) -> None:
    rows = repo.list_sku_selections(RUN, sort="valor").items

    def best(row: models.SkuSelectionRow) -> float | None:
        values = [f.valor for f in row.families if f.valor is not None]
        return min(values) if values else None

    present = [best(r) for r in rows if best(r) is not None]
    assert present == sorted(present)  # type: ignore[type-var]


def test_selection_list_rejects_bad_parameters(repo: FixtureRepository) -> None:
    for kwargs in ({"sku_class": "x"}, {"family": "x"}, {"estado": "x"}, {"sort": "x"}):
        with pytest.raises(InvalidQuery):
            repo.list_sku_selections(RUN, **kwargs)  # type: ignore[arg-type]


def test_selection_detail_hides_windows_unless_asked(repo: FixtureRepository) -> None:
    plain = repo.get_sku_selection(RUN, "FIL-0001")
    full = repo.get_sku_selection(RUN, "FIL-0001", include_windows=True)
    walks = [f.walk_forward for f in plain.families if f.walk_forward]
    assert walks and all(w.windows is None for w in walks)
    assert any(f.walk_forward and f.walk_forward.windows for f in full.families)


def test_selection_detail_of_an_unknown_sku_is_missing(repo: FixtureRepository) -> None:
    with pytest.raises(ArtifactMissing):
        repo.get_sku_selection(RUN, "FIL-9999")
    with pytest.raises(ArtifactMissing):
        repo.get_sku_selection("999", "FIL-0001")


def test_the_errors_scenario_serves_the_failed_family_for_fil_0001() -> None:
    detail = FixtureRepository("errores").get_sku_selection(RUN, "FIL-0001")
    failed = [f for f in detail.families if f.estado == "fallida"]
    assert failed and failed[0].error is not None


# ------------------------------------------------------------------------- verdicts
def test_verdicts_have_items_in_normal(repo: FixtureRepository) -> None:
    doc = repo.get_validation_verdicts(RUN)
    assert doc.availability.status == "available"
    assert doc.items


def test_verdicts_are_unavailable_in_the_empty_scenario() -> None:
    doc = FixtureRepository("vacio").get_validation_verdicts(RUN)
    assert doc.availability.status == "unavailable"
    assert doc.availability.reason_code == "stage_not_implemented"


# ---------------------------------------------------------------- the empty scenario
def test_the_empty_scenario_answers_with_unavailable_documents() -> None:
    repo = FixtureRepository("vacio")
    assert repo.list_ingests().items == []
    assert repo.get_run_status(RUN).availability.status == "unavailable"
    assert repo.list_sku_selections(RUN).availability.status == "unavailable"
    assert repo.list_synthetic_runs().items == []
    assert repo.get_capabilities().availability.status == "available"


def test_the_empty_scenario_has_no_documents_by_id() -> None:
    repo = FixtureRepository("vacio")
    with pytest.raises(ArtifactMissing):
        repo.get_ingest_report(UNKNOWN_SHA)
    with pytest.raises(ArtifactMissing):
        repo.get_sku_selection(RUN, "FIL-0001")
    with pytest.raises(ArtifactMissing):
        repo.get_synthetic_run("fase0_x")


# ------------------------------------------------------------------ synthetic runs
def test_synthetic_runs_list_and_page(repo: FixtureRepository) -> None:
    doc = repo.list_synthetic_runs(size=1)
    assert len(doc.items) == 1
    assert total(doc) == 2


def test_the_declared_artifact_matches_the_downloaded_file(repo: FixtureRepository) -> None:
    for item in repo.list_synthetic_runs().items:
        run = repo.get_synthetic_run(item.run_ref)
        download = repo.download_synthetic_artifact(item.run_ref)
        assert download.content == sample_csv()
        assert hashlib.sha256(download.content).hexdigest() == run.artifact.sha256
        assert run.log.artefacto_sha256 == run.artifact.sha256
        assert run.log.row_count == run.artifact.rows == download.content.count(b"\n") - 1
        assert (download.file_name, download.media_type) == (
            run.artifact.file_name,
            run.artifact.media_type,
        )


def test_the_list_items_agree_with_the_download_too(repo: FixtureRepository) -> None:
    item = repo.list_synthetic_runs().items[0]
    assert item.artifact.sha256 == hashlib.sha256(sample_csv()).hexdigest()
    assert item.log.row_count == item.artifact.rows


def test_the_sample_csv_has_the_four_canonical_columns() -> None:
    header, *rows = sample_csv().decode().splitlines()
    assert header == "sku_id,timestamp,demand_qty,lead_time_days"
    assert len(rows) == 12
    assert sample_csv() == sample_csv()


def test_a_synthetic_run_of_an_unknown_ref_is_missing(repo: FixtureRepository) -> None:
    with pytest.raises(ArtifactMissing):
        repo.get_synthetic_run("nope")
    with pytest.raises(ArtifactMissing):
        repo.download_synthetic_artifact("nope")
