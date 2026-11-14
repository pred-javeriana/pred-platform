"""Lumpy ranges on fixed products, with hand-calculated window losses."""

from __future__ import annotations

import json
import math
import sqlite3
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path

import pytest

from pred_platform.dal.grupo import read_group
from pred_platform.dal.schema import create_db
from pred_platform.evidence.__main__ import main
from pred_platform.evidence.export import document
from tests.m3_publicada import publish
from tests.test_group_evidence import RUNS, SHA, sql


@pytest.fixture
def lumpy_db(tmp_path: Path) -> Path:
    db = tmp_path / "lumpy.db"
    create_db(db).close()
    sql(
        db,
        "INSERT INTO ingestas (sha256,nombre_archivo,filas,skus,ruta_parquet) "
        "VALUES (?, 'panel.csv', 100, 4, 'panel.parquet')",
        (SHA,),
    )
    for seed, run in enumerate(RUNS):
        verdicts = {sku: ("lumpy", "NO_EVALUABLE" if sku == "W" else "VALIDADO") for sku in "WXYZ"}
        if seed == 1:
            verdicts.pop("Z")
        publish(
            db,
            ingesta_sha256=SHA,
            run_id_m2=f"m2-{seed}",
            run_id=run,
            verdicts=verdicts,
            eligibility=(("Z", "lumpy", "SARIMA sin candidato"),) if seed == 1 else (),
        )
        with closing(sqlite3.connect(db)) as conn:
            protocol = json.loads(
                conn.execute(
                    "SELECT protocolo FROM m3_protocolos WHERE run_id=?", (run,)
                ).fetchone()[0]
            )
            protocol["configuracion_m2"] = {"seed": seed}
            conn.execute(
                "UPDATE m3_protocolos SET protocolo=? WHERE run_id=?", (json.dumps(protocol), run)
            )
            for sku in verdicts:
                models = [
                    {"candidato_id": f"{sku}/{family}/{model}", "familia": family, "modelo": model}
                    for family, model in (
                        ("foundation", "chronos2"),
                        ("classical", "sarima"),
                        ("seasonal_naive", "seasonal_naive"),
                    )
                ]
                conn.execute(
                    "UPDATE m3_veredictos SET familia_campeona='foundation',"
                    "candidato_campeon=?,modelos_evaluados=? WHERE run_id=? AND sku=?",
                    (f"{sku}/foundation/chronos2", json.dumps(models), run, sku),
                )
                foundation = {"W": 1.0, "X": 1.0, "Y": 3.0, "Z": 0.2}[sku]
                classical = {
                    "W": 1.0,
                    "X": (2.0, 4.0, 2.0)[seed],
                    "Y": (1.0, 3.0, 6.0)[seed],
                    "Z": 1.0,
                }[sku]
                baseline = 3.0 if sku == "Y" else 2.0
                for family, value in (
                    ("foundation", foundation),
                    ("classical", classical),
                    ("seasonal_naive", baseline),
                ):
                    candidate = next(m["candidato_id"] for m in models if m["familia"] == family)
                    for start, count in (
                        (date(2026, 3, 13), 26),
                        (date(2026, 4, 14), 29 if sku == "W" else 30),
                    ):
                        conn.executemany(
                            "INSERT INTO m3_metricas VALUES (?, ?, ?, ?, 'rmse', ?, NULL)",
                            [
                                (
                                    run,
                                    sku,
                                    candidate,
                                    (start + timedelta(days=i)).isoformat(),
                                    value,
                                )
                                for i in range(count)
                            ],
                        )
                    # Embargo and post-test rows must not change any effect.
                    for origin in ("2026-04-10", "2026-05-26"):
                        conn.execute(
                            "INSERT INTO m3_metricas VALUES (?, ?, ?, ?, 'rmse', ?, NULL)",
                            (run, sku, candidate, origin, 1e6 if family == "foundation" else 1),
                        )
            full_baseline = math.sqrt(0.5) if seed == 1 else 0.5
            conn.execute(
                "INSERT INTO m3_confirmaciones (run_id,sku_class,familia_campeona,estado,"
                "p_iut,p_holm,alfa,efecto_n_skus,efecto_mediana_r,contendiente) "
                "VALUES (?, 'lumpy', 'foundation', 'confirmado', .01,.01,.05,?,?,'classical')",
                (run, 2 if seed == 1 else 3, full_baseline),
            )
            conn.execute(
                "INSERT INTO m3_comparaciones (run_id,sku_class,competidora,n_skus,mediana_razon,"
                "n_victorias,p_wilcoxon) VALUES (?, 'lumpy','classical',?,.5,1,.01)",
                (run, 2 if seed == 1 else 3),
            )
            conn.commit()
    return db


def test_full_and_fixed_population_medians_and_ranges_are_not_subtracted(lumpy_db: Path) -> None:
    result = read_group(lumpy_db, RUNS).lumpy
    assert result.cause is None
    assert result.common_competing == ("W", "X", "Y")
    base = [e for e in result.effects if e.comparison == "seasonal_naive"]
    classical = [e for e in result.effects if e.comparison == "classical"]
    assert [e.full_count for e in base] == [3, 2, 3]
    assert [e.full_median for e in base] == pytest.approx([0.5, math.sqrt(0.5), 0.5])
    assert [e.common_count for e in base] == [2, 2, 2]
    assert [e.common_median for e in base] == pytest.approx([math.sqrt(0.5)] * 3)
    # Confirmation is the median in log space. Even samples use the geometric
    # middle, not the arithmetic median of ratios (.75 for [.5, 1]).
    assert [e.common_median for e in classical] == pytest.approx([math.sqrt(1.5), 0.5, 0.5])
    ranges = {r.comparison: r for r in result.ranges}
    assert ranges["seasonal_naive"].full_range == pytest.approx(math.sqrt(0.5) - 0.5)
    assert ranges["seasonal_naive"].common_range == 0
    # The full-population range can be smaller than the common-population range.
    # Subtracting them would invent a negative "population part".
    assert ranges["classical"].full_range == 0
    assert ranges["classical"].common_range == pytest.approx(math.sqrt(1.5) - 0.5)


def test_every_ratio_keeps_its_source_window_keys_and_missing_window_cause(lumpy_db: Path) -> None:
    result = read_group(lumpy_db, RUNS).lumpy
    x = next(
        p
        for p in result.products
        if p.seed == 0 and p.sku == "X" and p.comparison == "seasonal_naive"
    )
    assert x.champion_id == "X/foundation/chronos2"
    assert x.comparison_id == "X/seasonal_naive/seasonal_naive"
    assert (x.origins[0], x.origins[-1], len(x.origins)) == ("2026-04-14", "2026-05-13", 30)
    assert (x.champion_loss, x.comparison_loss) == (30, 120)
    assert x.log_ratio == pytest.approx(math.log(0.5))
    assert {p.cause for p in result.products if p.sku == "W"} == {"pocas_ventanas_comunes:29<30"}
    assert not any(p.seed == 1 and p.sku == "Z" for p in result.products)


def test_full_population_reconciliation_refuses_unexplained_differences(lumpy_db: Path) -> None:
    sql(lumpy_db, "UPDATE m3_confirmaciones SET efecto_mediana_r=.1 WHERE run_id='seed-2'")
    result = read_group(lumpy_db, RUNS)
    assert result.significance.complete is True
    assert result.lumpy.cause is not None and "reproducen" in result.lumpy.cause
    assert result.lumpy.effects == ()


def test_changed_champion_retains_the_confirmation_a_real_engine_wrote(lumpy_db: Path) -> None:
    from tests.test_resultados import _ENGINE_CONFIRMATIONS

    # Reuse the captured engine row, not a recomputed or rounded median.
    row = next(row for row in _ENGINE_CONFIRMATIONS if row[0] == "lumpy")
    sql(
        lumpy_db,
        "UPDATE m3_confirmaciones SET familia_campeona=?,efecto_n_skus=?,efecto_mediana_r=? "
        "WHERE run_id='seed-2'",
        (row[1], row[7], row[8]),
    )
    result = read_group(lumpy_db, RUNS).lumpy
    effect = next(e for e in result.effects if e.seed == 2 and e.comparison == "seasonal_naive")
    assert effect.champion == "classical"
    assert effect.full_count == 3 and effect.full_median == 0.90076244944326556
    assert effect.common_median is None and effect.common_cause == "campeona_distinta"
    assert result.ranges == () and result.products == ()


def test_changed_champion_says_the_class_changes_only_when_champions_differ(
    lumpy_db: Path,
) -> None:
    sql(lumpy_db, "UPDATE m3_confirmaciones SET familia_campeona='classical' WHERE run_id='seed-2'")
    assert read_group(lumpy_db, RUNS).lumpy.cause == (
        "La campeona de la demanda grumosa cambia entre evaluaciones: la clase no es estable. "
        "Se muestran las medianas publicadas de cada evaluación."
    )
    # The same non-foundation champion in every seed is stable (ADR-03-012), not a change.
    sql(lumpy_db, "UPDATE m3_confirmaciones SET familia_campeona='classical'")
    assert read_group(lumpy_db, RUNS).lumpy.cause == (
        "La campeona de la demanda grumosa no es Fundacional en ninguna evaluación. "
        "Se muestran las medianas publicadas de cada evaluación."
    )


def test_two_seed_lumpy_preview_uses_only_those_two_populations(lumpy_db: Path) -> None:
    result = read_group(lumpy_db, RUNS[:2], preview=True)
    assert result.significance.complete is False
    assert len(result.lumpy.effects) == 4
    assert len(result.lumpy.ranges) == 2


def test_missing_confirmation_is_reported_not_as_zero_effect(lumpy_db: Path) -> None:
    sql(lumpy_db, "DELETE FROM m3_comparaciones WHERE run_id='seed-2'")
    sql(lumpy_db, "DELETE FROM m3_confirmaciones WHERE run_id='seed-2'")
    result = read_group(lumpy_db, RUNS)
    assert result.lumpy.cause == "Falta la confirmación publicada de lumpy."
    assert result.lumpy.effects == ()


def test_selection_not_test_data_chooses_the_family_representative(lumpy_db: Path) -> None:
    with closing(sqlite3.connect(lumpy_db)) as conn:
        for run in RUNS:
            models = json.loads(
                conn.execute(
                    "SELECT modelos_evaluados FROM m3_veredictos WHERE run_id=? AND sku='X'", (run,)
                ).fetchone()[0]
            )
            models.append(
                {"candidato_id": "X/foundation/other", "familia": "foundation", "modelo": "other"}
            )
            conn.execute(
                "UPDATE m3_veredictos SET modelos_evaluados=? WHERE run_id=? AND sku='X'",
                (json.dumps(models), run),
            )
            conn.execute(
                "INSERT INTO m3_metricas VALUES (?, 'X', 'X/foundation/other', "
                "'2026-03-13', 'rmse', 10, NULL)",
                (run,),
            )
            conn.executemany(
                "INSERT INTO m3_metricas VALUES (?, 'X', 'X/foundation/other', ?, 'rmse', 0, NULL)",
                [(run, (date(2026, 4, 14) + timedelta(days=i)).isoformat()) for i in range(30)],
            )
        conn.commit()
    assert {p.champion_id for p in read_group(lumpy_db, RUNS).lumpy.products if p.sku == "X"} == {
        "X/foundation/chronos2"
    }


def test_zero_loss_is_not_dropped_or_replaced_with_an_epsilon(lumpy_db: Path) -> None:
    sql(
        lumpy_db,
        "UPDATE m3_metricas SET valor=0 WHERE sku IN ('X','Y') "
        "AND candidato_id LIKE '%/foundation/%' AND ventana>='2026-04-14' "
        "AND ventana<='2026-05-25'",
    )
    sql(lumpy_db, "UPDATE m3_confirmaciones SET efecto_mediana_r=0")
    sql(lumpy_db, "UPDATE m3_comparaciones SET mediana_razon=0")
    result = read_group(lumpy_db, RUNS)
    assert {e.common_median for e in result.lumpy.effects} == {0.0}
    assert all(r.common_range == 0 for r in result.lumpy.ranges)
    data = document(result)
    x = next(p for p in data["lumpy"]["products"] if p["sku"] == "X")
    assert x["log_ratio"] == "-infinito"
    json.dumps(data, allow_nan=False)


def test_opposite_zero_loss_limits_keep_the_undefined_common_median(lumpy_db: Path) -> None:
    sql(
        lumpy_db,
        "UPDATE m3_metricas SET valor=0 WHERE sku='X' "
        "AND candidato_id LIKE '%/foundation/%' AND ventana>='2026-04-14' "
        "AND ventana<='2026-05-25'",
    )
    sql(
        lumpy_db,
        "UPDATE m3_metricas SET valor=0 WHERE sku='Y' "
        "AND (candidato_id LIKE '%/seasonal_naive/%' OR candidato_id LIKE '%/classical/%') "
        "AND ventana>='2026-04-14' AND ventana<='2026-05-25'",
    )
    # Full sample: [0, 0.1, inf] vs SN, [0, 0.2, inf] vs SARIMA.
    # The seed-1 full sample and every fixed common sample have just [0, inf].
    sql(
        lumpy_db,
        "UPDATE m3_confirmaciones SET efecto_mediana_r="
        "CASE WHEN run_id='seed-1' THEN NULL ELSE .1 END",
    )
    sql(
        lumpy_db,
        "UPDATE m3_comparaciones SET mediana_razon=CASE WHEN run_id='seed-1' THEN NULL ELSE .2 END",
    )
    result = read_group(lumpy_db, RUNS).lumpy
    assert all(e.common_median is None for e in result.effects)
    assert {e.common_cause for e in result.effects} == {"perdida_cero:mediana"}
    assert all(r.common_range is None and r.cause == "rango_no_definido" for r in result.ranges)


def test_cli_reads_the_group_and_labels_a_preview_without_writing(lumpy_db: Path, capsys) -> None:
    args = ["--db", str(lumpy_db), "--run", "seed-0", "--run", "seed-1"]
    assert main([*args, "--preview"]) == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview["complete"] is False and preview["significance"]["complete"] is False
    assert all("worsened" in p for p in preview["significance"]["products"])
    assert main(args) == 1
    assert "Seleccione" in capsys.readouterr().err
    assert main([*args, "--run", "seed-2"]) == 0
    final = json.loads(capsys.readouterr().out)
    assert final["complete"] is True and final["significance"]["complete"] is True
