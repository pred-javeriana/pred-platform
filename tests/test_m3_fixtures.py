"""The M3 read operations of the ``fixture`` source: the v2 examples behind ``DataRepository``."""

from __future__ import annotations

import json

import pytest
from jsonschema import Draft202012Validator

from pred_platform.contract import models_v2
from pred_platform.contract.errors import ArtifactMissing, InvalidQuery
from pred_platform.contract.loader import parse_as
from pred_platform.contract.resources import read_example, read_schema
from pred_platform.data.fixtures import FixtureRepository
from pred_platform.data.repository import DataRepository

RUN = "m3-real-02"


@pytest.fixture
def repo() -> DataRepository:
    return FixtureRepository("normal")


def test_the_run_list_selects_the_newest_publicada_and_announces_duplicates(
    repo: DataRepository,
) -> None:
    runs = repo.list_m3_runs(run_id_m2="m2-ejemplo")
    assert runs.source == "fixture" and runs.selected_run_id == RUN
    assert [r.run_id for r in runs.items] == ["m3-rechazada", "m3-real-02", "m3-real-01"]
    selected = runs.items[1]
    assert selected.aviso == "varias_publicadas_misma_identidad"
    assert selected.publicadas_misma_identidad == ["m3-real-02", "m3-real-01"]
    # Resolved over every match before paging.
    first = repo.list_m3_runs(size=1)
    assert [r.run_id for r in first.items] == ["m3-rechazada"] and first.selected_run_id == RUN
    assert first.page.total == 3
    assert [r.run_id for r in repo.list_m3_runs(estado="RECHAZADA").items] == ["m3-rechazada"]
    none = repo.list_m3_runs(run_id_m2="1")
    assert (none.availability.reason_code, none.items, none.selected_run_id) == (
        "no_data",
        [],
        None,
    )


def test_filtering_the_published_evidence_keeps_the_whole_run_funnel(
    repo: DataRepository,
) -> None:
    full = repo.get_m3_evidence(RUN)
    assert [r.veredicto for r in full.items] == [
        "EXPLORATORIO",
        "FALLO_TECNICO",
        "EVIDENCIA_INSUFICIENTE",
        "NO_EVALUABLE",
        "VALIDADO",
    ]
    one = repo.get_m3_evidence(RUN, skus=["SKU-VALIDADO"])
    expected = parse_as(models_v2.M3Evidence, read_example("m3_evidence.filtrada"))
    assert one.model_dump(exclude={"generated_at"}) == expected.model_dump(exclude={"generated_at"})
    assert one.funnel == full.funnel and one.late_launches == full.late_launches
    evaluated = repo.get_m3_evidence(RUN, grupo="evaluados")
    assert [r.veredicto for r in evaluated.items] == ["EXPLORATORIO", "VALIDADO"]
    assert evaluated.page.total == 2
    assert [r.sku for r in repo.get_m3_evidence(RUN, q="tardio").items] == []
    ordered = repo.get_m3_evidence(RUN, sort="razon_sn", descending=True)
    assert [r.razon_sn for r in ordered.items] == [1.1, 0.8, None, None, None]
    beyond = repo.get_m3_evidence(RUN, page=3, size=2)
    assert [r.sku for r in beyond.items] == ["SKU-VALIDADO"] and beyond.page.total == 5


def test_runs_that_are_not_published_or_unknown_never_expose_evidence(
    repo: DataRepository,
) -> None:
    rejected = repo.get_m3_evidence("m3-rechazada")
    assert rejected.availability.reason_code == "not_published" and rejected.items == []
    # A known run without an example of this document answers with its metadata only.
    units = repo.get_m3_units("m3-rechazada", page=2)
    assert units.availability.reason_code == "not_published"
    assert (units.page.number, units.page.total) == (2, 0)
    unknown = repo.get_m3_metrics("otra")
    assert unknown.availability.reason_code == "no_data" and unknown.run is None
    assert repo.get_m3_run_status("otra").availability.reason_code == "no_data"
    with pytest.raises(ArtifactMissing, match="m3-real-01"):
        repo.get_m3_evidence("m3-real-01")
    status = repo.get_m3_run_status("m3-real-01")
    assert status.run is not None and status.progress is None


def test_the_errors_scenario_serves_a_publication_without_its_metadata() -> None:
    repo = FixtureRepository("errores")
    doc = repo.get_m3_evidence(RUN, skus=["SKU-VALIDADO"])
    assert doc.availability.reason_code == "metadata_missing"
    assert doc.funnel is None and doc.items == []
    assert repo.get_m3_failures(RUN).items[0].modelo == ""


def test_the_empty_scenario_has_no_m3_runs() -> None:
    repo = FixtureRepository("vacio")
    assert repo.list_m3_runs().availability.reason_code == "no_data"
    assert repo.get_m3_evidence(RUN).availability.reason_code == "no_data"


def test_every_auxiliary_operation_filters_the_published_rows(repo: DataRepository) -> None:
    metrics = repo.get_m3_metrics(RUN, metrica="razon_sn")
    assert metrics.items and {m.metrica for m in metrics.items} == {"razon_sn"}
    assert repo.get_m3_categories(RUN, sku_class="lumpy").page.total == 0
    assert repo.get_m3_categories(RUN).page.total == 1
    portfolio = repo.get_m3_portfolio(RUN, metrica="rmse")
    assert {p.metrica for p in portfolio.items} <= {"rmse"}
    forecasts = repo.get_m3_forecasts(RUN, h=1)
    assert {f.h for f in forecasts.items} == {1}
    assert repo.get_m3_failures(RUN, sku="nadie").items == []
    units = repo.get_m3_units(RUN, estado="fallida")
    assert [u.estado for u in units.items] == ["fallida"]


@pytest.mark.parametrize(
    ("operation", "query"),
    [
        ("get_m3_evidence", {"veredicto": "mantiene"}),
        ("get_m3_evidence", {"grupo": "otros"}),
        ("get_m3_evidence", {"sort": "smape"}),
        ("get_m3_metrics", {"metrica": "smape"}),
        ("get_m3_metrics", {"ventana": "ayer"}),
        ("get_m3_portfolio", {"metrica": "n_pares"}),
        ("get_m3_forecasts", {"h": 0}),
        ("get_m3_units", {"estado": "pendiente"}),
        ("get_m3_categories", {"familia": "arima"}),
        ("get_m3_observations", {"fecha": "2026-02-30"}),
        ("get_m3_champions", {"familia": "arima"}),
        ("get_m3_champions", {"sku_class": "dense"}),
    ],
)
def test_an_impossible_filter_is_rejected_not_ignored(
    repo: DataRepository, operation: str, query: dict[str, object]
) -> None:
    with pytest.raises(InvalidQuery):
        getattr(repo, operation)(RUN, **query)


def test_observations_and_champions_filter_page_and_conform(repo: DataRepository) -> None:
    observations = repo.get_m3_observations(RUN, sku="SKU-EXPLORATORIO")
    assert [(r.fecha, r.valor) for r in observations.items] == [
        ("2026-07-01", 5.0),
        ("2026-07-02", None),
        ("2026-07-03", 12.0),
    ]
    assert repo.get_m3_observations(RUN, fecha="2026-07-01").page.total == 2
    first = repo.get_m3_observations(RUN, size=2)
    assert [r.sku for r in first.items] == ["SKU-EXPLORATORIO"] * 2
    assert first.page.total == 6
    champions = repo.get_m3_champions(RUN)
    assert champions.items[-1].sku_class is None
    assert [
        (r.n_skus, r.n_evaluados, r.n_no_evaluados) for r in champions.items if r.sku_class is None
    ] == [(5, 2, 3)]
    assert repo.get_m3_champions(RUN, familia="ml").items == []
    assert all(
        r.sku_class == "smooth" for r in repo.get_m3_champions(RUN, sku_class="smooth").items
    )
    for kind, doc in (("m3_observations", observations), ("m3_champions", champions)):
        Draft202012Validator(read_schema(kind)).validate(json.loads(doc.model_dump_json()))
    for operation in ("get_m3_observations", "get_m3_champions"):
        with pytest.raises(TypeError):
            getattr(repo, operation)(RUN, unknown="x")
        assert getattr(repo, operation)("m3-rechazada").availability.reason_code == "not_published"
        assert getattr(repo, operation)("missing").availability.reason_code == "no_data"
    assert (
        FixtureRepository("errores").get_m3_observations(RUN).availability.reason_code
    ) == "metadata_missing"


def test_documents_are_independent_copies(repo: DataRepository) -> None:
    first = repo.get_m3_evidence(RUN)
    first.items.clear()
    assert len(repo.get_m3_evidence(RUN).items) == 5
    assert json.loads(read_example("m3_evidence.publicada"))["page"]["total"] == 5
