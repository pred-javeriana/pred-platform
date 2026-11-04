"""Hand-checked final-group evidence, read through its public database entry point."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from pred_platform.dal.grupo import read_significance
from pred_platform.dal.schema import create_db
from pred_platform.evidence.models import EvidenceInvalid
from tests.m3_publicada import publish

SHA = "e" * 64
RUNS = ("seed-0", "seed-1", "seed-2")


def sql(db: Path, statement: str, args: tuple = ()) -> None:
    with closing(sqlite3.connect(db)) as conn:
        conn.execute(statement, args)
        conn.commit()


@pytest.fixture
def group_db(tmp_path: Path) -> Path:
    db = tmp_path / "group.db"
    create_db(db).close()
    sql(
        db,
        "INSERT INTO ingestas (sha256,nombre_archivo,filas,skus,ruta_parquet) "
        "VALUES (?, 'panel.csv', 100, 8, 'panel.parquet')",
        (SHA,),
    )
    smooth = {sku: ("smooth", "VALIDADO") for sku in "ABCDEFG"}
    lumpy = {sku: ("lumpy", "VALIDADO") for sku in "XY"}
    for seed, run in enumerate(RUNS):
        verdicts = {**smooth, **lumpy}
        excluded = ()
        if seed == 1:
            verdicts.pop("F")
            excluded = (("F", "smooth", "sin segunda familia"),)
        publish(
            db,
            ingesta_sha256=SHA,
            run_id_m2=f"m2-{seed}",
            run_id=run,
            verdicts=verdicts,
            eligibility=excluded,
        )
        with closing(sqlite3.connect(db)) as conn:
            protocol = json.loads(
                conn.execute(
                    "SELECT protocolo FROM m3_protocolos WHERE run_id=?", (run,)
                ).fetchone()[0]
            )
            protocol["configuracion_m2"] = {"seed": seed, "horizon": 7}
            conn.execute(
                "UPDATE m3_protocolos SET protocolo=? WHERE run_id=?", (json.dumps(protocol), run)
            )
            values = {
                "A": ((0.001, 0.004, 0.002)[seed], -3.0),
                "B": ((0.01, 0.02, 0.015)[seed], -2.0),
                "C": ((0.03, 0.01, 0.02)[seed], 2.0 if seed == 1 else -2.0),
                "D": ((0.8, 0.9, 0.7)[seed], -0.1),
                "G": (0.01, 3.0),
                "X": (0.03, -2.0),
                "Y": (0.9, -0.1),
            }
            for sku, (p_value, statistic) in values.items():
                conn.execute(
                    "UPDATE m3_veredictos SET dm_p_valor=?,dm_estadistico=? "
                    "WHERE run_id=? AND sku=?",
                    (p_value, statistic, run, sku),
                )
            if seed == 2:
                conn.execute(
                    "UPDATE m3_veredictos SET dm_p_valor=NULL,dm_p_ajustado=NULL,"
                    "dm_estadistico=NULL,dm_significativa=NULL,dm_causa='pocas_ventanas' "
                    "WHERE run_id=? AND sku='E'",
                    (run,),
                )
            conn.commit()
    return db


def test_largest_raw_p_then_one_bh_per_class_and_all_three_directions(group_db: Path) -> None:
    result = read_significance(group_db, RUNS[::-1])
    assert result.complete and result.alpha == 0.05
    assert [r.seed for r in result.runs] == [0, 1, 2]
    products = {p.sku: p for p in result.products}
    # smooth m=5: p_max sorted .004, .01, .02, .03, .9.
    # BH is min_{j>=rank}(m*p_j/j), capped at 1. No direction filter.
    for sku, p_max, adjusted in (
        ("A", 0.004, 0.02),
        ("G", 0.01, 0.025),
        ("B", 0.02, 1 / 30),
        ("C", 0.03, 0.0375),
        ("D", 0.9, 0.9),
        ("X", 0.03, 0.06),
        ("Y", 0.9, 0.9),
    ):
        assert products[sku].p_max == p_max
        assert products[sku].p_adjusted == pytest.approx(adjusted)
    assert products["A"].improved is True
    assert products["B"].improved is True
    assert products["C"].significant is True and products["C"].improved is False
    assert products["G"].significant is True and products["G"].improved is False
    assert products["X"].significant is False  # BH is not pooled with smooth.
    assert [s.direction for s in products["C"].sources] == ["negativo", "positivo", "negativo"]
    assert [
        (c.sku_class, c.tested, c.untested, c.significant, c.improved, c.worsened, c.mixed)
        for c in result.classes
    ] == [
        ("lumpy", 2, 0, 0, 0, 0, 0),
        ("smooth", 5, 2, 4, 2, 1, 1),
    ]


def test_missing_tests_keep_the_source_run_and_published_cause(group_db: Path) -> None:
    products = {p.sku: p for p in read_significance(group_db, RUNS).products}
    assert products["E"].sources[2].cause == "pocas_ventanas"
    assert products["E"].sources[2].run_id == "seed-2"
    assert products["F"].sources[1].cause == (
        "eligibility.fewer_than_two_families: sin segunda familia"
    )
    assert products["F"].sources[0].p_value == 0.04
    for sku in "EF":
        p = products[sku]
        assert (p.p_max, p.p_adjusted, p.significant, p.improved) == (None,) * 4


def test_two_seed_preview_is_explicit_and_never_a_final_group(group_db: Path) -> None:
    preview = read_significance(group_db, RUNS[:2], preview=True)
    assert preview.complete is False
    with pytest.raises(EvidenceInvalid):
        read_significance(group_db, RUNS[:2])
    with pytest.raises(EvidenceInvalid):
        read_significance(group_db, RUNS[1:], preview=True)
    with pytest.raises(EvidenceInvalid):
        read_significance(group_db, (RUNS[0], RUNS[0], RUNS[2]))


@pytest.mark.parametrize("state", ["EN_CURSO", "EVALUADA", "SELECCIONADA"])
def test_unpublished_rows_never_enter_the_calculation(group_db: Path, state: str) -> None:
    sql(group_db, "UPDATE m3_corridas SET estado=? WHERE run_id='seed-2'", (state,))
    with pytest.raises(EvidenceInvalid, match="PUBLICADA"):
        read_significance(group_db, RUNS)


@pytest.mark.parametrize(
    "change",
    [
        "UPDATE m3_corridas SET origen_declarado=0 WHERE run_id='seed-2'",
        "UPDATE m3_corridas SET datos_sinteticos=1 WHERE run_id='seed-2'",
        "UPDATE m3_protocolos SET protocolo=json_set(protocolo,'$.configuracion_m2.seed',1) "
        "WHERE run_id='seed-2'",
        "UPDATE m3_protocolos SET protocolo=json_set(protocolo,'$.politica.alfa_dm',0.1) "
        "WHERE run_id='seed-2'",
        "UPDATE m3_protocolos SET protocolo=json_set(protocolo,'$.bloques.horizonte',14) "
        "WHERE run_id='seed-2'",
        "UPDATE m3_corridas SET identidad=json_set(identidad,'$.codigo_version','other') "
        "WHERE run_id='seed-2'",
        "UPDATE m3_veredictos SET dm_p_valor=1.1 WHERE run_id='seed-2' AND sku='A'",
        "UPDATE m3_veredictos SET dm_alfa=0.1 WHERE run_id='seed-2' AND sku='A'",
        "DELETE FROM m3_veredictos WHERE run_id='seed-2' AND sku='A'",
    ],
)
def test_incompatible_or_invalid_sources_are_rejected(group_db: Path, change: str) -> None:
    sql(group_db, change)
    with pytest.raises(EvidenceInvalid):
        read_significance(group_db, RUNS)


def test_zero_p_ties_and_equality_at_alpha_are_kept(group_db: Path) -> None:
    for run in RUNS:
        for sku, p in (("X", 0.0), ("Y", 0.05)):
            sql(
                group_db,
                "UPDATE m3_veredictos SET dm_p_valor=? WHERE run_id=? AND sku=?",
                (p, run, sku),
            )
    products = {p.sku: p for p in read_significance(group_db, RUNS).products}
    assert products["X"].p_adjusted == 0
    assert products["Y"].p_adjusted == 0.05 and products["Y"].significant is True
    sql(group_db, "UPDATE m3_veredictos SET dm_p_valor=.05 WHERE sku_class='lumpy'")
    assert {
        p.p_adjusted for p in read_significance(group_db, RUNS).products if p.sku_class == "lumpy"
    } == {0.05}


def test_read_changes_no_rows_or_schema_and_never_creates_a_missing_database(
    group_db: Path,
    tmp_path: Path,
) -> None:
    before = hashlib.sha256(group_db.read_bytes()).hexdigest()
    read_significance(group_db, RUNS)
    assert hashlib.sha256(group_db.read_bytes()).hexdigest() == before
    missing = tmp_path / "absent.db"
    with pytest.raises(EvidenceInvalid):
        read_significance(missing, RUNS)
    assert not missing.exists()
