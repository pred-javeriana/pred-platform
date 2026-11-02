"""M3's published evidence read by the ``dal`` source as contract v2 documents (M4, section 4.1).

Reads through ``DalRepository`` and the SQL reader: publication-gated, one snapshot, typed rows
that keep the run, the SKU and the ingest SHA, and a funnel built from counts and the late-launch
rows M3 publishes in SQLite, never from the visible page.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from pred_platform.config import Settings
from pred_platform.contract import M3_CONTRACT_VERSION
from pred_platform.contract.errors import ContractInvalid, InvalidQuery
from pred_platform.contract.resources import read_schema
from pred_platform.dal import evidencia
from pred_platform.dal.lectura import _open_readonly
from pred_platform.dal.migrate import apply_migrations, load_migrations, migrate
from pred_platform.data import m3_query
from pred_platform.data.dal import DalRepository
from pred_platform.data.repository import M3DateRange, M3ReserveBlocks
from pred_platform.worker.engine import launch
from tests.m3_publicada import (
    BLOCKS,
    LATE,
    TWO_FAMILIES,
    TWO_FAMILIES_CAUSE,
    identity,
    publish,
    publish_confirmation,
)

SHA = "c" * 64
PARQUET = "processed/panel.parquet"
RUN = "m3-real-1"
M2 = "m2-run-1"
FIVE = {
    "SKU-A": ("smooth", "VALIDADO"),
    "SKU-B": ("smooth", "EXPLORATORIO"),
    "SKU-C": ("smooth", "EVIDENCIA_INSUFICIENTE"),
    "SKU-D": ("lumpy", "NO_EVALUABLE"),
    "SKU-E": ("lumpy", "FALLO_TECNICO"),
}


@pytest.fixture
def root(tmp_path: Path) -> Path:
    data = tmp_path / "data"
    migrate(tmp_path / "pred.db")
    with closing(sqlite3.connect(tmp_path / "pred.db")) as conn:
        conn.execute(
            "INSERT INTO ingestas (sha256, nombre_archivo, filas, skus, ruta_parquet) "
            "VALUES (?, 'panel.csv', 100, 7, ?)",
            (SHA, PARQUET),
        )
        conn.commit()
    (data / PARQUET).parent.mkdir(parents=True)
    (data / PARQUET).write_bytes(b"unused published artifact")
    return tmp_path


def _db(root: Path) -> Path:
    return root / "pred.db"


def _repo(root: Path) -> DalRepository:
    return DalRepository(_db(root), root / "data")


def _publish(root: Path, **options: Any) -> None:
    options.setdefault("verdicts", FIVE)
    options.setdefault("late", ("SKU-TARDIO-1", "SKU-TARDIO-2"))
    publish(_db(root), ingesta_sha256=SHA, run_id_m2=M2, **options)


def _sql(root: Path, statement: str, *params: object) -> None:
    with closing(sqlite3.connect(_db(root))) as conn:
        conn.execute(statement, params)
        conn.commit()


def _conforms(document: Any, kind: str) -> None:
    validator = Draft202012Validator(
        read_schema(kind), format_checker=Draft202012Validator.FORMAT_CHECKER
    )
    data = json.loads(document.model_dump_json())
    assert [e.message for e in validator.iter_errors(data)] == []


# ----------------------------------------------------------------------- evidence
def test_published_evidence_keeps_the_five_verdicts_their_numbers_and_identity(
    root: Path,
) -> None:
    _publish(root)
    doc = _repo(root).get_m3_evidence(RUN)

    _conforms(doc, "m3_evidence")
    assert (doc.source, doc.schema_version, doc.availability.status) == (
        "dal",
        M3_CONTRACT_VERSION,
        "available",
    )
    assert doc.run is not None and doc.run.policy is not None
    assert (doc.run.run_id, doc.run.run_id_m2, doc.run.estado) == (RUN, M2, "PUBLICADA")
    assert doc.run.policy.n_min == 30 and doc.run.identidad == identity()
    assert doc.run.creado_en.isoformat() == "2026-10-27T10:00:00+00:00"
    verdicts = {row.sku: row for row in doc.items}
    assert {sku: row.veredicto for sku, row in verdicts.items()} == {
        sku: verdict for sku, (_, verdict) in FIVE.items()
    }
    validated = verdicts["SKU-A"]
    assert (validated.familia_campeona, validated.candidato_campeon) == (
        "classical",
        "SKU-A/classical/sarima",
    )
    assert (validated.n_ventanas, validated.n_min, validated.razon_sn) == (40, 30, 0.8)
    assert (validated.dm_p_ajustado, validated.dm_significativa, validated.dm_causa) == (
        0.08,
        False,
        None,
    )
    assert (validated.datos_sinteticos, validated.origen_declarado) == (False, True)
    failed = verdicts["SKU-E"]
    assert (failed.candidato_campeon, failed.dm_causa, failed.dm_p_valor) == (
        None,
        "veredicto:FALLO_TECNICO",
        None,
    )
    for row in doc.items:
        assert (row.run_id, row.ingesta_sha256) == (RUN, SHA)
        assert {(m.run_id, m.sku, m.ingesta_sha256) for m in row.modelos_evaluados} == {
            (RUN, row.sku, SHA)
        }


def test_the_funnel_reads_late_launches_from_sqlite_after_the_parquet_is_deleted(
    root: Path,
) -> None:
    _publish(root)
    (root / "data" / PARQUET).unlink()
    doc = _repo(root).get_m3_evidence(RUN, skus=["SKU-A"])

    assert doc.funnel is not None and doc.late_launches is not None
    funnel = doc.funnel
    assert (funnel.total_productos, funnel.excluded_count, funnel.tras_exclusiones) == (7, 2, 5)
    assert (funnel.no_evaluados, funnel.evaluados) == (3, 2)
    assert funnel.conteos.model_dump() == dict.fromkeys(
        ("FALLO_TECNICO", "NO_EVALUABLE", "EVIDENCIA_INSUFICIENTE", "VALIDADO", "EXPLORATORIO"), 1
    )
    assert (funnel.sku, funnel.run_id, funnel.ingesta_sha256) == (None, RUN, SHA)
    late = doc.late_launches
    assert late.t_star == "2026-03-13"
    assert [(row.sku, row.causa) for row in late.items] == [
        ("SKU-TARDIO-1", LATE),
        ("SKU-TARDIO-2", LATE),
    ]
    # The page is filtered; the funnel still describes the whole run.
    assert [row.sku for row in doc.items] == ["SKU-A"] and doc.page.total == 1


def test_the_funnel_counts_and_lists_the_products_m2_excluded_from_the_competition(
    root: Path,
) -> None:
    _publish(root, eligibility=(("SKU-UNA-FAMILIA", "lumpy", TWO_FAMILIES_CAUSE),))
    doc = _repo(root).get_m3_evidence(RUN, skus=["SKU-A"])

    _conforms(doc, "m3_evidence")
    assert doc.funnel is not None and doc.eligibility_exclusions is not None
    funnel = doc.funnel
    assert (funnel.total_productos, funnel.tras_exclusiones) == (8, 5)
    assert (funnel.excluded_count, funnel.eligibility_excluded_count) == (2, 1)
    excluded = doc.eligibility_exclusions
    assert (excluded.run_id, excluded.sku, excluded.ingesta_sha256) == (RUN, None, SHA)
    assert excluded.excluded_count == 1
    assert [
        (row.sku, row.sku_class, row.codigo, row.causa, row.run_id, row.ingesta_sha256)
        for row in excluded.items
    ] == [("SKU-UNA-FAMILIA", "lumpy", TWO_FAMILIES, TWO_FAMILIES_CAUSE, RUN, SHA)]
    # Neither a verdict nor a late launch: the page and the late launches do not change.
    assert [row.sku for row in doc.items] == ["SKU-A"] and doc.page.total == 1
    assert doc.late_launches is not None and doc.late_launches.excluded_count == 2


def test_a_run_without_eligibility_exclusions_reports_zero_not_unknown(root: Path) -> None:
    _publish(root)
    doc = _repo(root).get_m3_evidence(RUN)
    assert doc.funnel is not None and doc.funnel.eligibility_excluded_count == 0
    assert doc.eligibility_exclusions is not None and doc.eligibility_exclusions.items == []


def test_the_reserve_blocks_are_the_limits_m3_fixed_in_the_protocol(root: Path) -> None:
    _publish(root)

    blocks = _repo(root).get_m3_reserve_blocks(RUN)

    assert blocks == M3ReserveBlocks(
        selection_origins=M3DateRange("2026-03-13", "2026-04-07", 26),
        embargo_origins=M3DateRange("2026-04-08", "2026-04-13", 6),
        test_origins=M3DateRange("2026-04-14", "2026-05-25", 42),
        selection_dates=M3DateRange("2026-03-14", "2026-04-14", 32),
        test_dates=M3DateRange("2026-04-15", "2026-06-01", 48),
    )
    assert _repo(root).get_m3_reserve_blocks("m3-desconocida") is None


def test_a_run_evaluated_before_the_blocks_has_no_block_limits(root: Path) -> None:
    _publish(root, eligibility=None)

    assert _repo(root).get_m3_reserve_blocks(RUN) is None


def test_block_limits_that_break_their_shape_are_a_contract_error(root: Path) -> None:
    _publish(root)
    broken = {**BLOCKS, "fechas_prueba": {"n": 0, "primero": "2026-04-15", "ultimo": None}}
    _sql(
        root,
        "UPDATE m3_protocolos SET protocolo = json_set(protocolo, '$.bloques', json(?))",
        json.dumps(broken),
    )

    with pytest.raises(ContractInvalid, match="bloques"):
        _repo(root).get_m3_reserve_blocks(RUN)


# fmt: off
_CONFIRMED = {
    "significance": [("smooth", 3, 1, 0, 1, 0, 0.983)],
    "confirmations": [
        ("smooth", "classical", "no_confirmado", "p_holm>0.05", 0.3, 0.3, 0.05,
         3, 0.8, 0.0, float("inf"), None, None),
    ],
    "comparisons": [
        ("smooth", "ml", 3, 0.0, 0.0, float("inf"), None, None, None, 2, 0.21, 0.94,
         1.0, 0.3, 0.3, 0, 0, "perdida_cero:hodges_lehmann"),
    ],
}
# fmt: on


def test_the_champion_confirmation_is_read_only_from_a_published_run_with_blocks(
    root: Path,
) -> None:
    _publish(root, estado="SELECCIONADA")
    publish_confirmation(_db(root), **_CONFIRMED)
    assert _repo(root).get_m3_confirmation(RUN) is None

    _sql(root, "UPDATE m3_corridas SET estado = 'PUBLICADA'")
    confirmation = _repo(root).get_m3_confirmation(RUN)

    assert confirmation is not None and confirmation.alfa == 0.05
    [smooth] = confirmation.clases
    [ml] = smooth.comparaciones
    # Zero losses are evidence: a ratio of 0 and an interval open to infinity survive the read.
    assert (ml.mediana_razon, ml.ic_mediana_superior, smooth.efecto_ic_superior) == (
        0.0,
        float("inf"),
        float("inf"),
    )
    assert [row.nivel_fcr for row in confirmation.significancia] == [0.983]
    assert _repo(root).get_m3_confirmation("m3-desconocida") is None


def test_a_run_evaluated_before_the_blocks_has_no_champion_confirmation(root: Path) -> None:
    _publish(root, eligibility=None)

    assert _repo(root).get_m3_confirmation(RUN) is None


def test_a_confirmation_that_breaks_its_rules_is_a_contract_error(root: Path) -> None:
    _publish(root)
    publish_confirmation(_db(root), **_CONFIRMED)
    _sql(root, "UPDATE m3_comparaciones SET p_wilcoxon = 1.5")  # the schema does not bound p

    with pytest.raises(ContractInvalid, match=r"clases\.0\.comparaciones\.0\.p_wilcoxon"):
        _repo(root).get_m3_confirmation(RUN)


def test_a_run_published_before_m3_listed_m2_exclusions_does_not_report_them_as_zero(
    root: Path,
) -> None:
    _publish(root, eligibility=None)
    doc = _repo(root).get_m3_evidence(RUN)

    _conforms(doc, "m3_evidence")
    assert doc.availability.status == "available" and doc.funnel is not None
    assert doc.funnel.eligibility_excluded_count is None and doc.eligibility_exclusions is None
    assert (doc.funnel.total_productos, doc.funnel.excluded_count) == (7, 2)


def test_missing_cutoff_metadata_is_not_zero_exclusions(root: Path) -> None:
    _publish(root, publish_m4=False)
    repo = _repo(root)

    doc = repo.get_m3_evidence(RUN)
    assert (doc.availability.status, doc.availability.reason_code) == (
        "unavailable",
        "metadata_missing",
    )
    assert doc.availability.detail and "no significa cero exclusiones" in doc.availability.detail
    assert doc.items == [] and doc.funnel is None and doc.late_launches is None
    assert doc.eligibility_exclusions is None
    assert doc.run is not None and doc.run.estado == "PUBLICADA"
    # The auxiliary evidence of the same PUBLICADA run is still complete and available.
    assert repo.get_m3_metrics(RUN).availability.status == "available"

    observations = repo.get_m3_observations(RUN)
    _conforms(observations, "m3_observations")
    assert observations.availability.reason_code == "metadata_missing"
    assert observations.availability.detail == (
        "La corrida se publicó antes de que M3 publicara la demanda observada; "
        "vuelva a ejecutarla para ver el gráfico."
    )
    assert observations.items == [] and observations.page.total == 0
    assert repo.get_m3_champions(RUN).availability.status == "available"


def test_malformed_published_cut_is_a_contract_error(root: Path) -> None:
    _publish(root)
    _sql(root, "UPDATE m3_cortes SET t_star = '13/03/2026'")
    with pytest.raises(ContractInvalid, match="t_star"):
        _repo(root).get_m3_evidence(RUN)


def test_a_late_launch_cause_that_is_not_the_published_one_is_rejected(root: Path) -> None:
    _publish(root)
    with closing(sqlite3.connect(_db(root))) as conn:
        conn.execute("PRAGMA ignore_check_constraints = ON")
        conn.execute("UPDATE m3_lanzamientos_tardios SET causa = 'otra causa'")
        conn.commit()
    with pytest.raises(ContractInvalid, match="late_launches"):
        _repo(root).get_m3_evidence(RUN)


def test_observations_keep_nulls_dates_filters_and_paging(root: Path) -> None:
    _publish(root)
    repo = _repo(root)
    observations = repo.get_m3_observations(RUN, sku="SKU-A")
    _conforms(observations, "m3_observations")
    assert observations.availability.status == "available"
    assert [(row.fecha, row.valor) for row in observations.items] == [
        ("2026-03-15", 4.0),
        ("2026-03-22", None),
        ("2026-03-23", 0.0),
    ]
    assert {(row.run_id, row.sku, row.ingesta_sha256) for row in observations.items} == {
        (RUN, "SKU-A", SHA)
    }
    assert repo.get_m3_observations(RUN, fecha="2026-03-22").page.total == 5
    assert repo.get_m3_observations(RUN, sku="missing").items == []
    page = repo.get_m3_observations(RUN, size=2, page=2)
    assert [(row.sku, row.fecha) for row in page.items] == [
        ("SKU-A", "2026-03-23"),
        ("SKU-B", "2026-03-15"),
    ]
    assert page.page.total == 15
    assert repo.get_m3_observations(RUN, size=2, page=20).items == []
    with pytest.raises(InvalidQuery):
        repo.get_m3_observations(RUN, fecha="2026-02-30")
    unknown: dict[str, Any] = {"unknown": "x"}
    with pytest.raises(TypeError):
        repo.get_m3_observations(RUN, **unknown)


def test_champions_read_class_and_portfolio_counts_without_recalculation(root: Path) -> None:
    _publish(root)
    repo = _repo(root)
    champions = repo.get_m3_champions(RUN)
    _conforms(champions, "m3_champions")
    assert [
        (r.sku_class, r.familia, r.n_skus, r.n_evaluados, r.n_no_evaluados) for r in champions.items
    ] == [
        ("lumpy", "classical", 2, 0, 2),
        ("smooth", "classical", 3, 2, 1),
        (None, "classical", 5, 2, 3),
    ]
    assert {r.sku for r in champions.items} == {None}
    assert repo.get_m3_champions(RUN, sku_class="smooth").items == [champions.items[1]]
    assert repo.get_m3_champions(RUN, familia="ml").items == []
    assert repo.get_m3_champions(RUN, page=3, size=1).items == [champions.items[2]]
    bad_queries: list[dict[str, Any]] = [{"sku_class": "invalid"}, {"familia": "invalid"}]
    for query in bad_queries:
        with pytest.raises(InvalidQuery):
            repo.get_m3_champions(RUN, **query)
    unknown: dict[str, Any] = {"unknown": "x"}
    with pytest.raises(TypeError):
        repo.get_m3_champions(RUN, **unknown)


def test_empty_exclusions_need_a_published_cut_not_a_parquet(root: Path) -> None:
    _publish(root, late=())
    (root / "data" / PARQUET).unlink()
    doc = _repo(root).get_m3_evidence(RUN)
    assert doc.availability.status == "available"
    assert doc.late_launches is not None and doc.late_launches.items == []
    assert doc.funnel is not None and doc.funnel.excluded_count == 0


def test_filters_groups_order_and_pages_are_applied_by_sqlite(root: Path) -> None:
    _publish(root)
    repo = _repo(root)

    def skus(**query: Any) -> list[str]:
        return [row.sku for row in repo.get_m3_evidence(RUN, **query).items]

    assert skus(grupo="evaluados") == ["SKU-A", "SKU-B"]
    assert skus(grupo="no_evaluados") == ["SKU-C", "SKU-D", "SKU-E"]
    assert skus(veredicto="VALIDADO") == ["SKU-A"]
    assert skus(sku_class="lumpy") == ["SKU-D", "SKU-E"]
    assert skus(q="sku-d") == ["SKU-D"]
    assert skus(q="%") == [] and skus(q="_") == []
    assert skus(skus=["SKU-E", "SKU-A", "SKU-Z"]) == ["SKU-A", "SKU-E"]
    assert skus(skus=[]) == []
    assert skus(familia_campeona="seasonal_naive") == []
    # r ascending, the SKUs without r last in either direction, ties broken by SKU.
    assert skus(sort="razon_sn") == ["SKU-A", "SKU-B", "SKU-C", "SKU-D", "SKU-E"]
    assert skus(sort="razon_sn", descending=True) == ["SKU-B", "SKU-A", "SKU-C", "SKU-D", "SKU-E"]
    second = repo.get_m3_evidence(RUN, size=2, page=2)
    assert [r.sku for r in second.items] == ["SKU-C", "SKU-D"] and second.page.total == 5
    beyond = repo.get_m3_evidence(RUN, size=2, page=9)
    assert beyond.items == [] and beyond.page.total == 5
    assert beyond.funnel is not None and beyond.funnel.tras_exclusiones == 5
    bad: list[dict[str, Any]] = [
        {"veredicto": "mantiene"},
        {"grupo": "todos"},
        {"sort": "valor"},
        {"size": 501},
    ]
    for query in bad:
        with pytest.raises(InvalidQuery):
            repo.get_m3_evidence(RUN, **query)


def test_unknown_and_synthetic_origins_are_never_read_as_real_data(root: Path) -> None:
    _publish(
        root,
        origin="desconocido",
        verdicts={"SKU-A": ("smooth", "EXPLORATORIO")},
    )
    _publish(
        root,
        run_id="m3-sintetica",
        origin="sintetico",
        configuracion="config-2",
        verdicts={"SKU-A": ("smooth", "EXPLORATORIO")},
    )
    repo = _repo(root)
    [unknown] = repo.get_m3_evidence(RUN).items
    [synthetic] = repo.get_m3_evidence("m3-sintetica").items
    assert (unknown.datos_sinteticos, unknown.origen_declarado, unknown.dm_causa) == (
        False,
        False,
        "origen_desconocido",
    )
    assert (synthetic.datos_sinteticos, synthetic.origen_declarado, synthetic.dm_causa) == (
        True,
        True,
        "datos_sinteticos",
    )
    assert unknown.dm_p_valor is None and synthetic.dm_p_valor is None


# ------------------------------------------------------------------ publication gate
def test_the_sql_reader_hides_rows_until_the_run_is_published(root: Path) -> None:
    _publish(root, estado="SELECCIONADA")
    query = m3_query.metrics_query(size=500)

    read = evidencia.read_published(_db(root), RUN, "metrics", query)

    assert read.run is not None and read.run["estado"] == "SELECCIONADA"
    assert read.rows == [] and read.total == 0

    _sql(root, "UPDATE m3_corridas SET estado = 'PUBLICADA'")
    published = evidencia.read_published(_db(root), RUN, "metrics", query)
    assert published.rows and published.total == len(published.rows)


def test_an_unpublished_run_shows_its_progress_and_metadata_but_no_evidence(root: Path) -> None:
    _publish(root, estado="EN_CURSO")
    repo = _repo(root)

    status = repo.get_m3_run_status(RUN)
    _conforms(status, "m3_run_status")
    assert status.availability.status == "available"
    assert status.run is not None and status.run.estado == "EN_CURSO"
    assert status.progress is not None
    progress = status.progress
    assert (progress.n_unidades_previstas, progress.n_resueltas) == (20, 18)
    assert (progress.n_confirmadas, progress.n_fallidas) == (17, 1)
    for doc in (
        repo.get_m3_evidence(RUN),
        repo.get_m3_metrics(RUN),
        repo.get_m3_categories(RUN),
        repo.get_m3_portfolio(RUN),
        repo.get_m3_observations(RUN),
        repo.get_m3_champions(RUN),
        repo.get_m3_forecasts(RUN),
        repo.get_m3_failures(RUN),
        repo.get_m3_units(RUN),
    ):
        assert (doc.availability.status, doc.availability.reason_code) == (
            "unavailable",
            "not_published",
        )
        assert doc.items == [] and doc.page.total == 0
        assert doc.run is not None and doc.run.run_id == RUN
        assert (
            doc.availability.detail
            == f"La corrida M3 {RUN} está EN_CURSO; su evidencia aparece al publicarse."
        )

    _sql(root, "UPDATE m3_corridas SET estado = 'PUBLICADA'")
    assert len(repo.get_m3_evidence(RUN).items) == 5


def test_a_rejected_run_names_its_cause_and_never_its_rows(root: Path) -> None:
    _publish(root, estado="RECHAZADA")
    doc = _repo(root).get_m3_evidence(RUN)
    assert doc.availability.reason_code == "not_published" and doc.items == []
    assert doc.run is not None and doc.run.causa_rechazo == "manifiesto incompatible con la ingesta"
    assert doc.availability.detail == (
        f"La corrida M3 {RUN} fue RECHAZADA: manifiesto incompatible con la ingesta"
    )


def test_an_unknown_run_is_no_data(root: Path) -> None:
    repo = _repo(root)
    for doc in (repo.get_m3_evidence("otra"), repo.get_m3_units("otra")):
        assert doc.availability.reason_code == "no_data" and doc.run is None
    assert repo.get_m3_run_status("otra").run is None


def test_publication_during_the_read_keeps_one_snapshot(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _publish(root, estado="SELECCIONADA")
    published = False

    @contextmanager
    def publishing_reader(path: Path) -> Any:
        with _open_readonly(path) as conn:
            assert conn is not None

            def publish_after_the_state_is_read(sql: str) -> None:
                nonlocal published
                if not published and sql.startswith("SELECT run_id FROM m3_corridas WHERE"):
                    published = True
                    with closing(sqlite3.connect(_db(root))) as writer:
                        writer.execute("UPDATE m3_corridas SET estado = 'PUBLICADA'")
                        writer.commit()

            conn.set_trace_callback(publish_after_the_state_is_read)
            yield conn

    monkeypatch.setattr(evidencia, "_open_readonly", publishing_reader)
    during = _repo(root).get_m3_evidence(RUN)
    assert published
    # The snapshot started before the writer committed: the run and its rows agree.
    assert during.availability.reason_code == "not_published" and during.items == []
    assert during.run is not None and during.run.estado == "SELECCIONADA"
    monkeypatch.setattr(evidencia, "_open_readonly", _open_readonly)
    assert len(_repo(root).get_m3_evidence(RUN).items) == 5


def test_cut_and_late_launches_share_the_verdict_snapshot(
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _publish(root)
    changed = False

    @contextmanager
    def racing_reader(path: Path) -> Any:
        with _open_readonly(path) as conn:
            assert conn is not None

            def change_publication(sql: str) -> None:
                nonlocal changed
                if not changed and sql.startswith("SELECT t_star FROM m3_cortes"):
                    changed = True
                    with closing(sqlite3.connect(_db(root))) as writer:
                        writer.execute("UPDATE m3_cortes SET t_star = '2026-03-12'")
                        writer.execute("DELETE FROM m3_lanzamientos_tardios")
                        writer.execute(
                            "INSERT INTO m3_lanzamientos_tardios VALUES (?, 'OTHER', ?)",
                            (RUN, LATE),
                        )
                        writer.commit()

            conn.set_trace_callback(change_publication)
            yield conn

    monkeypatch.setattr(evidencia, "_open_readonly", racing_reader)
    during = _repo(root).get_m3_evidence(RUN)
    assert changed and during.late_launches is not None and during.funnel is not None
    assert during.late_launches.t_star == "2026-03-13"
    assert [r.sku for r in during.late_launches.items] == ["SKU-TARDIO-1", "SKU-TARDIO-2"]
    assert during.funnel.total_productos == 7
    monkeypatch.setattr(evidencia, "_open_readonly", _open_readonly)
    after = _repo(root).get_m3_evidence(RUN)
    assert after.late_launches is not None and after.funnel is not None
    assert after.late_launches.t_star == "2026-03-12"
    assert [r.sku for r in after.late_launches.items] == ["OTHER"]
    assert after.funnel.total_productos == 6


def test_the_status_reads_schema_and_rows_from_one_snapshot_during_an_upgrade(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "platform.db"
    migrate(db, load_migrations()[:7])  # version 7: no origin declaration marker yet

    def upgrade_and_store_unknown_origin() -> None:
        with closing(sqlite3.connect(db)) as writer:
            apply_migrations(writer, load_migrations())
            writer.execute(
                "INSERT INTO m3_corridas (run_id, ingesta_sha256, run_id_m2, identidad, "
                "datos_sinteticos, origen_declarado, estado) "
                "VALUES ('m3-race', ?, 'm2-race', '{}', 0, 0, 'EN_CURSO')",
                (SHA,),
            )
            writer.commit()

    @contextmanager
    def racing_open(path: Path) -> Any:
        with _open_readonly(path) as conn:
            assert conn is not None

            def trace(sql: str) -> None:
                # The engine upgrades and writes between the schema probe and the row read.
                if sql.startswith("SELECT c.run_id"):
                    conn.set_trace_callback(None)
                    upgrade_and_store_unknown_origin()

            conn.set_trace_callback(trace)
            yield conn

    monkeypatch.setattr(evidencia, "_open_readonly", racing_open)
    assert DalRepository(db).get_m3_run_status("m3-race").run is None
    monkeypatch.setattr(evidencia, "_open_readonly", _open_readonly)
    run = DalRepository(db).get_m3_run_status("m3-race").run
    assert run is not None and (run.datos_sinteticos, run.origen_declarado) == (False, False)


def test_reads_never_write_and_missing_or_old_databases_have_no_runs(root: Path) -> None:
    _publish(root)
    before = _db(root).read_bytes()
    repo = _repo(root)
    repo.get_m3_evidence(RUN)
    repo.list_m3_runs()
    repo.get_m3_units(RUN)
    assert _db(root).read_bytes() == before
    with evidencia._open_readonly(_db(root)) as conn:
        assert conn is not None
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            conn.execute("UPDATE m3_corridas SET estado = 'EN_CURSO'")

    missing = root / "missing.db"
    assert DalRepository(missing).list_m3_runs().availability.reason_code == "no_data"
    assert DalRepository(missing).get_m3_evidence(RUN).run is None
    assert not missing.exists()
    with closing(sqlite3.connect(root / "old.db")) as conn:
        conn.execute("CREATE TABLE sentinel (value INTEGER)")
    assert DalRepository(root / "old.db").get_m3_run_status(RUN).run is None


def test_a_corrupt_database_is_an_error_not_missing_evidence(tmp_path: Path) -> None:
    corrupt = tmp_path / "corrupt.db"
    corrupt.write_bytes(b"not a sqlite database")
    with pytest.raises(sqlite3.DatabaseError, match="not a database"):
        DalRepository(corrupt).get_m3_evidence(RUN)


# ----------------------------------------------------------------------------- runs
def test_the_newest_publicada_is_selected_and_duplicates_are_announced(root: Path) -> None:
    _publish(root, creado_en="2026-10-26 10:00:00", run_id="m3-real-0")
    _publish(root, creado_en="2026-10-27 10:00:00")
    # A newer rejected run of another configuration does not displace the publication.
    _publish(
        root,
        run_id="m3-rechazada",
        estado="RECHAZADA",
        configuracion="config-2",
        creado_en="2026-10-28 10:00:00",
    )
    repo = _repo(root)

    runs = repo.list_m3_runs(run_id_m2=M2)
    _conforms(runs, "m3_runs")
    assert [r.run_id for r in runs.items] == ["m3-rechazada", RUN, "m3-real-0"]
    assert runs.selected_run_id == RUN
    current = runs.items[1]
    assert current.publicadas_misma_identidad == [RUN, "m3-real-0"]
    assert current.aviso == "varias_publicadas_misma_identidad"
    assert runs.items[0].aviso is None and runs.items[0].publicadas_misma_identidad == []
    # The newest PUBLICADA is resolved over every match, not over the visible page.
    first = repo.list_m3_runs(run_id_m2=M2, size=1)
    assert [r.run_id for r in first.items] == ["m3-rechazada"] and first.selected_run_id == RUN
    assert first.page.total == 3
    assert [r.run_id for r in repo.list_m3_runs(estado="RECHAZADA").items] == ["m3-rechazada"]
    assert repo.list_m3_runs(identidad=identity("config-2")).page.total == 1
    assert repo.list_m3_runs(ingesta_sha256=SHA).page.total == 3
    empty = repo.list_m3_runs(run_id_m2="otra")
    assert (empty.availability.reason_code, empty.selected_run_id) == ("no_data", None)
    with pytest.raises(InvalidQuery):
        repo.list_m3_runs(estado="publicada")


def test_a_platform_run_id_resolves_to_the_engine_run(root: Path) -> None:
    _publish(root)
    _sql(
        root,
        "INSERT INTO configuraciones (version, parametros) VALUES (1, '{}')",
    )
    _sql(
        root,
        "INSERT INTO ejecuciones (id, ingesta_id, configuracion_id, seed, estado, run_id) "
        "VALUES (7, 1, 1, 0, 'completada', ?)",
        M2,
    )
    _sql(
        root,
        "INSERT INTO ejecuciones (id, ingesta_id, configuracion_id, seed, estado) "
        "VALUES (8, 1, 1, 0, 'pendiente')",
    )
    repo = _repo(root)
    assert repo.list_m3_runs(run_id_m2="7").selected_run_id == RUN
    assert repo.list_m3_runs(run_id_m2=M2).selected_run_id == RUN
    assert repo.list_m3_runs(run_id_m2="8").availability.reason_code == "no_data"


# ------------------------------------------------------------------ auxiliary tables
def test_metrics_keep_me_r_and_null_values_with_their_cause(root: Path) -> None:
    _publish(root)
    repo = _repo(root)

    champion = repo.get_m3_metrics(RUN, sku="SKU-A", candidato_id="SKU-A/classical/sarima")
    _conforms(champion, "m3_metrics")
    assert {(m.metrica, m.valor) for m in champion.items} == {
        ("mae", 1.2),
        ("rmse", 1.5),
        ("mase", 0.9),
        ("me", -0.2),
        ("razon_sn", 0.8),
    }
    assert {m.ventana for m in champion.items} == {"agregada"}
    [missing] = repo.get_m3_metrics(
        RUN, sku="SKU-C", metrica="razon_sn", candidato_id="SKU-C/classical/sarima"
    ).items
    assert (missing.valor, missing.causa) == (None, "sin_ventanas_comunes")
    assert repo.get_m3_metrics(RUN, ventana="2026-03-14").items == []
    with pytest.raises(InvalidQuery):
        repo.get_m3_metrics(RUN, metrica="smape")
    with pytest.raises(InvalidQuery):
        repo.get_m3_metrics(RUN, ventana="14/03/2026")


def test_categories_portfolio_forecasts_failures_and_units_keep_their_published_shape(
    root: Path,
) -> None:
    _publish(root)
    repo = _repo(root)

    categories = repo.get_m3_categories(RUN)
    _conforms(categories, "m3_categories")
    smooth = categories.items[1]
    assert [c.sku_class for c in categories.items] == ["lumpy", "smooth"]
    assert (smooth.sku, smooth.familia_campeona, smooth.medianas_r["classical"]) == (
        None,
        "classical",
        0.8,
    )
    assert smooth.skus_comparables == ["SKU-A", "SKU-B", "SKU-C"]
    assert smooth.conteos.VALIDADO == 1 and smooth.familias_excluidas == {"dl": "no_entregada"}
    assert repo.get_m3_categories(RUN, sku_class="lumpy").page.total == 1

    portfolio = repo.get_m3_portfolio(RUN, familia="seasonal_naive")
    _conforms(portfolio, "m3_portfolio")
    assert [(p.sku_class, p.sku, p.n_calculables) for p in portfolio.items] == [
        ("lumpy", None, 2),
        ("smooth", None, 2),
    ]

    forecasts = repo.get_m3_forecasts(RUN, sku="SKU-A", h=1, origen="2026-03-21")
    _conforms(forecasts, "m3_forecasts")
    assert [(f.candidato_id, f.fecha) for f in forecasts.items] == [
        ("SKU-A/classical/sarima", "2026-03-22"),
        ("SKU-A/seasonal_naive/seasonal_naive", "2026-03-22"),
    ]

    failures = repo.get_m3_failures(RUN)
    _conforms(failures, "m3_failures")
    assert [(f.sku, f.codigo_error) for f in failures.items] == [("SKU-E", "pronostico_invalido")]

    units = repo.get_m3_units(RUN, estado="fallida")
    _conforms(units, "m3_units")
    [unit] = units.items
    assert (unit.sku, unit.causa, unit.semilla) == ("SKU-E", "pronostico_invalido", 0)
    assert unit.emitido_en is not None and unit.emitido_en.tzinfo is not None
    for doc in (categories, portfolio, forecasts, failures, units):
        assert {(row.run_id, row.ingesta_sha256) for row in doc.items} == {(RUN, SHA)}


# --------------------------------------------------------------------- the engine child
def test_engine_child_receives_the_configured_central_database(tmp_path: Path, monkeypatch) -> None:
    db = tmp_path / "central.db"
    log = tmp_path / "child.log"
    monkeypatch.setenv("PRED_DB_PATH", "/wrong-database.db")
    settings = Settings(data_root=tmp_path / "data", db_path=db)
    process = launch(
        [sys.executable, "-c", "import os; print(os.environ['PRED_DB_PATH'])"],
        settings=settings,
        log_path=log,
    )
    assert process.wait(timeout=10) == 0
    assert log.read_text().strip() == str(db)
