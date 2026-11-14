"""The compact Results group page, its missing-seed state and trace download."""

from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pred_platform.app.main import create_app
from pred_platform.config import Settings
from pred_platform.dal import grupo
from tests.m3_publicada import publish
from tests.test_group_evidence import SHA, sql
from tests.test_group_evidence import group_db as group_db  # noqa: F401
from tests.test_group_lumpy import lumpy_db as lumpy_db  # noqa: F401

CHOICES = {"semilla0": "seed-0", "semilla1": "seed-1", "semilla2": "seed-2"}


def client(db: Path) -> TestClient:
    return TestClient(
        create_app(
            Settings(
                db_path=db,
                data_root=db.parent / "data",
                data_source="dal",
                worker="off",
            )
        )
    )


def test_results_links_to_the_group_and_the_group_shows_both_computations(lumpy_db: Path) -> None:
    with client(lumpy_db) as browser:
        assert 'href="/resultados/grupo"' in browser.get("/resultados").text
        response = browser.get("/resultados/grupo", params=CHOICES)
        assert response.status_code == 200
        html = response.text
        assert "Significancia del grupo por clase" in html
        assert "población completa y común" in html
        assert "Rango completo:" in html and "Rango común:" in html
        assert "0,207" in html and "0,725" in html
        assert "no se restan" in html
        assert "semilla0=seed-0" in html and "semilla2=seed-2" in html
        assert "sin cambiar sus publicaciones" in html
        # Each publication reads by its time; the run and data identifiers stay in its title.
        assert ">Creada el 27 oct 2026, 5:00 a. m.</option>" in html
        assert "2026-10-27 10:00:00 UTC" in html
        assert 'title="Evaluación final seed-0 · huella de los datos ' in html
        assert "UTC (hora universal coordinada)" in html
        assert ">seed-0 ·" not in html and "Seasonal Naive" not in html
        assert "Fundacional frente a la línea base" in html and "Grumosa: población" in html
        assert "productos de la clase Grumosa." in html
        assert "Para la clase Grumosa incluye" in html
        assert "PUBLICADA" not in html
        assert "m3_metricas" not in html
        assert "m3_confirmaciones" not in html
        assert "m3_comparaciones" not in html
        assert "Regla del grupo final de evaluaciones (decisión 10)" in html
        assert 'title="ADR-03-012"' in html
        assert "Regla: ADR-03-012" not in html
        main = html.split("<main")[1].split("</main>")[0]
        assert "corridas" not in main
        assert "alfa_dm" not in main


def test_each_product_shows_the_three_directions_and_missing_test_causes(lumpy_db: Path) -> None:
    with client(lumpy_db) as browser:
        tested = browser.get("/resultados/grupo", params={**CHOICES, "sku": "X"}).text
        assert tested.count("negativo") == 4  # one rule explanation and three source rows.
        assert "Fuentes del SKU X" in tested
        assert "Mejora significativa en las tres semillas" in tested
        missing = browser.get("/resultados/grupo", params={**CHOICES, "sku": "Z"}).text
        assert "sin segunda familia" not in missing
        # A missing test reads by its message title; the engine's cause stays in the title.
        assert (
            'title="SARIMA sin candidato">El SKU quedó fuera de la comparación porque conservó '
            "menos de dos familias evaluables.</abbr>"
        ) in missing
        assert "eligibility.fewer_than_two_families" not in missing
        assert "Sin prueba del grupo: falta alguna prueba válida" in missing
        unknown = browser.get("/resultados/grupo", params={**CHOICES, "sku": "unknown"})
        assert unknown.status_code == 200
        assert "No hay un SKU «unknown»" in unknown.text


def test_each_product_names_a_uniform_worsening_apart_from_mixed_directions(
    group_db: Path,
) -> None:
    with client(group_db) as browser:
        worse = browser.get("/resultados/grupo", params={**CHOICES, "sku": "G"}).text
        assert "Empeora de forma significativa en las tres semillas" in worse
        mixed = browser.get("/resultados/grupo", params={**CHOICES, "sku": "C"}).text
        assert (
            "Diferencia significativa, pero la dirección cambia entre semillas (Otras direcciones)."
        ) in mixed
        assert "Empeora de forma significativa" not in mixed


def test_seed_two_pending_never_displays_two_seed_numbers_as_final(lumpy_db: Path) -> None:
    sql(lumpy_db, "UPDATE m3_corridas SET estado='EN_CURSO' WHERE run_id='seed-2'")
    with client(lumpy_db) as browser:
        html = browser.get("/resultados/grupo").text
        assert "El grupo final requiere tres evaluaciones publicadas" in html
        assert "Significancia del grupo por clase" not in html
        assert 'value="seed-2"' not in html
        assert browser.get("/resultados/grupo.json").status_code == 400


def test_unpublished_or_wrong_seed_cannot_be_injected_through_a_shared_link(lumpy_db: Path) -> None:
    with client(lumpy_db) as browser:
        for value in ("unknown", "seed-2"):
            response = browser.get("/resultados/grupo", params={**CHOICES, "semilla0": value})
            assert response.status_code == 200
            assert "Elija una publicación de la semilla 0." in response.text
            assert "Significancia del grupo por clase" not in response.text
            json_resp = browser.get("/resultados/grupo.json", params={**CHOICES, "semilla0": value})
            assert json_resp.status_code == 400
            assert json_resp.json()["error"] == "Seleccione una corrida PUBLICADA de la semilla 0."


def test_trace_download_has_every_source_p_direction_and_window_key(lumpy_db: Path) -> None:
    with client(lumpy_db) as browser:
        response = browser.get("/resultados/grupo.json", params=CHOICES)
        assert response.status_code == 200
        assert (
            response.headers["content-disposition"] == 'attachment; filename="grupo-semillas.json"'
        )
        data = response.json()
        assert data["significance"]["complete"] is True
        x = next(p for p in data["significance"]["products"] if p["sku"] == "X")
        assert [s["run_id"] for s in x["sources"]] == ["seed-0", "seed-1", "seed-2"]
        assert [s["p_value"] for s in x["sources"]] == [0.04, 0.04, 0.04]
        assert x["p_max"] == 0.04
        pair = next(p for p in data["lumpy"]["products"] if p["sku"] == "X")
        assert pair["champion_id"] == "X/foundation/chronos2"
        assert pair["origins"][0] == "2026-04-14"
        assert pair["champion_loss"] == 30


@pytest.mark.parametrize("protocol", ["bad-json", "[]", '{"configuracion_m2": [1]}'])
def test_malformed_unselected_protocol_does_not_hide_valid_choices(
    lumpy_db: Path, protocol: str
) -> None:
    sql(lumpy_db, "UPDATE m3_protocolos SET protocolo=? WHERE run_id='seed-2'", (protocol,))
    with client(lumpy_db) as browser:
        response = browser.get("/resultados/grupo")
        assert response.status_code == 200
        assert 'role="alert"' not in response.text
        assert 'value="seed-0"' in response.text and 'value="seed-1"' in response.text
        assert 'value="seed-2"' not in response.text
        selected = browser.get("/resultados/grupo", params=CHOICES)
        assert "Elija una publicación de la semilla 2." in selected.text
        assert browser.get("/resultados/grupo.json", params=CHOICES).status_code == 400


def test_publications_are_not_silently_declared_to_be_the_final_group(lumpy_db: Path) -> None:
    with client(lumpy_db) as browser:
        assert "Significancia del grupo por clase" not in browser.get("/resultados/grupo").text
        assert browser.get("/resultados/grupo.json").status_code == 400


def test_fixture_mode_does_not_invent_a_final_group(tmp_path: Path) -> None:
    application = create_app(
        Settings(
            db_path=tmp_path / "unused.db",
            data_root=tmp_path / "data",
            data_source="fixture",
        )
    )
    with TestClient(application) as browser:
        html = browser.get("/resultados/grupo").text
        assert "Datos de prueba" in html
        assert "El grupo final requiere tres evaluaciones publicadas" in html
        assert browser.get("/resultados/grupo.json").status_code == 400
    assert not (tmp_path / "unused.db").exists()


def test_group_results_unselected_note_text(lumpy_db: Path) -> None:
    with client(lumpy_db) as browser:
        html = browser.get("/resultados/grupo").text
        assert "Las semillas 0 y 1 solas" not in html
        assert (
            "Elija una publicación para cada semilla. Dos semillas no forman el grupo final."
            in html
        )


def test_common_population_causes_presentation(lumpy_db: Path) -> None:
    # Setup zero loss to produce perdida_cero:mediana as in test_group_lumpy.py
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
    sql(
        lumpy_db,
        "UPDATE m3_confirmaciones SET efecto_mediana_r="
        "CASE WHEN run_id='seed-1' THEN NULL ELSE .1 END",
    )
    sql(
        lumpy_db,
        "UPDATE m3_comparaciones SET mediana_razon=CASE WHEN run_id='seed-1' THEN NULL ELSE .2 END",
    )
    with client(lumpy_db) as browser:
        response = browser.get("/resultados/grupo", params=CHOICES)
        assert response.status_code == 200
        html = response.text
        assert "La mediana no se define: los valores centrales tienen pérdida cero" in html
        assert 'title="perdida_cero:mediana"' in html

    # Setup empty common valid population to produce sin_productos_comparables
    sql(lumpy_db, "UPDATE m3_veredictos SET modelos_evaluados='[]' WHERE run_id='seed-0'")
    sql(
        lumpy_db,
        "UPDATE m3_confirmaciones SET efecto_n_skus=0, efecto_mediana_r=NULL WHERE run_id='seed-0'",
    )
    sql(
        lumpy_db,
        "UPDATE m3_comparaciones "
        "SET n_skus=0, n_victorias=0, mediana_razon=NULL "
        "WHERE run_id='seed-0'",
    )
    with client(lumpy_db) as browser:
        response = browser.get("/resultados/grupo", params=CHOICES)
        assert response.status_code == 200
        html = response.text
        assert "Sin productos comparables en las tres semillas" in html
        assert 'title="sin_productos_comparables"' in html


def test_seed_options_same_minute_disambiguation_and_utc_title(lumpy_db: Path) -> None:
    # Insert another run for seed 2 in the same minute but 40 seconds later
    sql(
        lumpy_db,
        "INSERT INTO m3_corridas (run_id, ingesta_sha256, run_id_m2, identidad, datos_sinteticos, "
        "origen_declarado, estado, creado_en) VALUES "
        "('seed-2-bis', '288827faec31379965d21e86095066dbf0e9f1a098c46440db7f97858c2e6462', "
        "'m2-2-bis', 'identidad-seed-2-bis', 0, 1, 'PUBLICADA', '2026-10-27 10:00:40')",
    )
    # Give it a matching protocol for seed 2
    protocol = {
        "configuracion_m2": {"seed": 2},
        "bloques": {
            "origenes_seleccion": {"primero": "2026-01-01", "ultimo": "2026-02-01"},
            "origenes_prueba": {"primero": "2026-03-01", "ultimo": "2026-04-01"},
        },
    }
    sql(
        lumpy_db,
        "INSERT INTO m3_protocolos (run_id, protocolo, manifiesto, procedencia, "
        "reserva_abierta_en, n_unidades_previstas) VALUES ('seed-2-bis', ?, '{}', '{}', "
        "'2026-03-13 10:00:00', 100)",
        (json.dumps(protocol),),
    )
    with client(lumpy_db) as browser:
        html = browser.get("/resultados/grupo").text
        # Both options for seed 2 should have distinct text with seconds
        assert "Creada el 27 oct 2026, 5:00:00 a. m." in html
        assert "Creada el 27 oct 2026, 5:00:40 a. m." in html
        assert 'title="Evaluación final seed-2 · huella de los datos ' in html
        assert "2026-10-27 10:00:00 UTC (hora universal coordinada)" in html
        assert "2026-10-27 10:00:40 UTC (hora universal coordinada)" in html


def test_group_json_download_and_html_cause_wording(lumpy_db: Path) -> None:
    # Test missing confirmation cause in JSON vs HTML
    sql(lumpy_db, "DELETE FROM m3_comparaciones WHERE run_id='seed-2'")
    sql(lumpy_db, "DELETE FROM m3_confirmaciones WHERE run_id='seed-2'")
    with client(lumpy_db) as browser:
        resp_json = browser.get("/resultados/grupo.json", params=CHOICES)
        assert resp_json.status_code == 200
        assert resp_json.json()["lumpy"]["cause"] == "Falta la confirmación publicada de lumpy."

        resp_html = browser.get("/resultados/grupo", params=CHOICES)
        assert resp_html.status_code == 200
        assert "Falta la confirmación publicada de la demanda grumosa." in resp_html.text

    # Test no lumpy products cause in JSON vs HTML
    sql(lumpy_db, "UPDATE m3_veredictos SET sku_class='smooth'")
    sql(lumpy_db, "UPDATE m3_exclusiones SET sku_class='smooth'")
    with client(lumpy_db) as browser:
        resp_json = browser.get("/resultados/grupo.json", params=CHOICES)
        assert resp_json.status_code == 200
        assert resp_json.json()["lumpy"]["cause"] == "No hay productos lumpy en el grupo."

        resp_html = browser.get("/resultados/grupo", params=CHOICES)
        assert resp_html.status_code == 200
        assert "No hay productos de demanda grumosa en el grupo." in resp_html.text


def test_seed_group_page_wording_has_no_internal_jargon(lumpy_db: Path) -> None:
    # When runs do not share data (ingesta_sha256 mismatch)
    sql(
        lumpy_db,
        "UPDATE m3_corridas SET ingesta_sha256='different-sha' WHERE run_id='seed-2'",
    )
    with client(lumpy_db) as browser:
        html = browser.get("/resultados/grupo", params=CHOICES).text
        assert "Las evaluaciones elegidas no usan los mismos datos." in html
        assert "ingesta" not in html or "huella de los datos" in html
        assert "ADR-03-012" not in html

        resp_json = browser.get("/resultados/grupo.json", params=CHOICES)
        assert resp_json.status_code == 400
        assert resp_json.json()["error"] == "Las corridas no comparten la ingesta."


def _add_completed_executions(db: Path) -> None:
    with closing(sqlite3.connect(db)) as conn, conn:
        for seed in range(3):
            settings = {
                "min_train": 40,
                "horizon": 7,
                "step": 7,
                "metric": "rmse",
                "seasonality": 7,
                "families": ["classical", "foundation"],
            }
            config = conn.execute(
                "INSERT INTO configuraciones(version,descripcion,parametros) "
                "VALUES ((SELECT COALESCE(MAX(version),0)+1 FROM configuraciones),'grupo',?)",
                (json.dumps(settings),),
            ).lastrowid
            conn.execute(
                "INSERT INTO ejecuciones(ingesta_id,configuracion_id,seed,estado,run_id) "
                "VALUES ((SELECT id FROM ingestas WHERE sha256=?),?,?,'completada',?)",
                (SHA, config, seed, f"m2-{seed}"),
            )


def test_stability_chapter_shares_group_values_sources_and_explicit_selection(
    lumpy_db: Path,
) -> None:
    _add_completed_executions(lumpy_db)
    with client(lumpy_db) as browser:
        base = "/corridas/1/resultados/estabilidad"
        initial = browser.get(base, params={"m3": "seed-0"})
        assert initial.status_code == 200
        assert "Significancia del grupo por clase" not in initial.text
        assert "El grupo final requiere tres evaluaciones publicadas" in initial.text
        response = browser.get(base, params={"m3": "seed-0", **CHOICES, "sku": "X"})
        assert response.status_code == 200
        html = response.text
        assert "Significancia del grupo por clase" in html
        assert "Grumosa: población completa y común" in html
        assert "0,207" in html and "0,725" in html
        assert "Fuentes del SKU X" in html
        assert 'action="/corridas/1/resultados/estabilidad"' in html
        assert '<input type="hidden" name="m3" value="seed-0">' in html
        assert (
            "/resultados/grupo.json?semilla0=seed-0&amp;semilla1=seed-1&amp;semilla2=seed-2" in html
        )
        assert "No se suman descubrimientos de evaluaciones distintas" in html
        assert browser.get("/resultados/grupo.json", params=CHOICES).json()["significance"][
            "complete"
        ]


def test_stability_group_must_include_the_current_exact_publication(lumpy_db: Path) -> None:
    _add_completed_executions(lumpy_db)
    publish(
        lumpy_db,
        ingesta_sha256=SHA,
        run_id_m2="m2-0",
        run_id="other-current",
        verdicts={sku: ("lumpy", "VALIDADO") for sku in "WXYZ"},
    )
    with client(lumpy_db) as browser:
        response = browser.get(
            "/corridas/1/resultados/estabilidad", params={"m3": "other-current", **CHOICES}
        )
        assert response.status_code == 200
        assert "El grupo debe incluir la evaluación de la corrida mostrada" in response.text
        assert "Significancia del grupo por clase" not in response.text


@pytest.mark.parametrize(
    "damage",
    [
        "DELETE FROM m3_comparaciones WHERE run_id='seed-2' AND sku_class='lumpy'",
        "UPDATE m3_metricas SET valor=-1 WHERE run_id='seed-2' AND sku='X'",
        "UPDATE m3_metricas SET valor=1e999 WHERE run_id='seed-2' AND sku='X'",
        "UPDATE m3_confirmaciones SET efecto_mediana_r=.1 WHERE run_id='seed-2'",
    ],
)
def test_invalid_lumpy_evidence_preserves_significance(lumpy_db: Path, damage: str) -> None:
    sql(lumpy_db, damage)
    with client(lumpy_db) as browser:
        page = browser.get("/resultados/grupo", params=CHOICES)
        assert page.status_code == 200
        assert "Significancia del grupo por clase" in page.text
        assert "La evidencia de la demanda grumosa no es válida:" in page.text
        section = page.text.split('id="lumpy-del-grupo"')[1].split("</section>")[0]
        # Neither internal family codes nor run IDs reach the visible cause.
        for code in ("lumpy", "classical", "seasonal_naive", "seed-2"):
            assert code not in section
        response = browser.get("/resultados/grupo.json", params=CHOICES)
        assert response.status_code == 200
        assert response.json()["significance"]["complete"] is True
        assert response.json()["lumpy"]["effects"] == []


def test_champion_change_keeps_each_published_seed_effect(lumpy_db: Path) -> None:
    sql(lumpy_db, "UPDATE m3_confirmaciones SET familia_campeona='classical' WHERE run_id='seed-2'")
    with client(lumpy_db) as browser:
        page = browser.get("/resultados/grupo", params=CHOICES).text
        lumpy = page.split('id="lumpy-del-grupo"', 1)[1].split("</section>", 1)[0]
        assert "la clase no es estable" in lumpy
        assert "Medianas publicadas por evaluación" in lumpy
        assert "Fundacional" in lumpy and "Clásica" in lumpy
        assert "0,707" in lumpy and lumpy.count("0,500") >= 2
        effects = browser.get("/resultados/grupo.json", params=CHOICES).json()["lumpy"]
        assert len(effects["effects"]) == 6
        assert effects["ranges"] == [] and effects["products"] == []
        assert all(e["common_median"] is None for e in effects["effects"])
        baseline = [e for e in effects["effects"] if e["comparison"] == "seasonal_naive"]
        assert [e["champion"] for e in baseline] == ["foundation", "foundation", "classical"]
        assert [e["full_count"] for e in baseline] == [3, 2, 3]


def test_baseline_champion_without_a_published_effect_keeps_the_page_and_download(
    lumpy_db: Path,
) -> None:
    # The engine writes this row when the lumpy champion is the baseline: no effect and no
    # comparison against SARIMA.
    sql(
        lumpy_db,
        "UPDATE m3_confirmaciones SET familia_campeona='seasonal_naive',estado='no_aplica',"
        "causa='campeon_es_linea_base',p_iut=NULL,p_holm=NULL,efecto_n_skus=NULL,"
        "efecto_mediana_r=NULL,contendiente=NULL WHERE run_id='seed-2'",
    )
    sql(lumpy_db, "DELETE FROM m3_comparaciones WHERE run_id='seed-2'")
    with client(lumpy_db) as browser:
        page = browser.get("/resultados/grupo", params=CHOICES)
        assert page.status_code == 200
        assert "Significancia del grupo por clase" in page.text
        assert "la clase no es estable" in page.text
        download = browser.get("/resultados/grupo.json", params=CHOICES)
        assert download.status_code == 200
        lumpy = download.json()["lumpy"]
        assert {e["seed"] for e in lumpy["effects"]} == {0, 1}
        assert download.json()["significance"]["complete"] is True


def test_sources_list_all_publications_and_mark_the_selected_one(lumpy_db: Path) -> None:
    sql(
        lumpy_db,
        "INSERT INTO m3_corridas SELECT 'development-0',ingesta_sha256,run_id_m2,identidad,"
        "datos_sinteticos,estado,causa_rechazo,creado_en,origen_declarado "
        "FROM m3_corridas WHERE run_id='seed-0'",
    )
    sql(
        lumpy_db,
        "INSERT INTO m3_protocolos SELECT 'development-0',ejecucion_id,protocolo,manifiesto,"
        "procedencia,reserva_abierta_en,n_unidades_previstas FROM m3_protocolos "
        "WHERE run_id='seed-0'",
    )
    with client(lumpy_db) as browser:
        page = browser.get("/resultados/grupo", params=CHOICES).text
        assert "La plataforma no distingue una corrida final de una de desarrollo." in page
        sources = page.split("Evaluaciones y fuentes de cada número", 1)[1].split("</details>")[0]
        assert "development-0" in sources and "seed-0</span> (elegida)" in sources


def test_untested_products_and_their_causes_are_listed_without_a_sku_query(lumpy_db: Path) -> None:
    with client(lumpy_db) as browser:
        page = browser.get("/resultados/grupo", params=CHOICES).text
        assert "SKU sin prueba del grupo y su causa (2)" in page
        untested = page.split("SKU sin prueba del grupo y su causa", 1)[1].split("</details>")[0]
        assert ">Z</th>" in untested and ">W</th>" in untested
        assert 'title="SARIMA sin candidato"' in untested
        assert "menos de dos familias evaluables" in untested
        assert "el veredicto es «No evaluable», que no lleva prueba" in untested


def test_positive_in_every_seed_without_significance_is_not_worse(group_db: Path) -> None:
    sql(group_db, "UPDATE m3_veredictos SET dm_estadistico=0.1 WHERE sku='D'")
    result = grupo.read_group(group_db, tuple(CHOICES.values())).significance
    product = next(p for p in result.products if p.sku == "D")
    assert [s.direction for s in product.sources] == ["positivo"] * 3
    assert product.significant is False and product.worsened is False
    smooth = next(c for c in result.classes if c.sku_class == "smooth")
    assert (smooth.significant, smooth.improved, smooth.worsened, smooth.mixed) == (4, 2, 1, 1)
    with client(group_db) as browser:
        page = browser.get("/resultados/grupo", params={**CHOICES, "sku": "D"}).text
        assert "Sin diferencia significativa del grupo." in page
        assert "Empeora de forma significativa" not in page


def test_download_identifies_worsened_products(group_db: Path) -> None:
    with client(group_db) as browser:
        response = browser.get("/resultados/grupo.json", params=CHOICES)
    assert response.status_code == 200
    products = {p["sku"]: p for p in response.json()["significance"]["products"]}
    assert products["G"]["worsened"] is True
    assert products["C"]["worsened"] is False
    assert products["E"]["worsened"] is None


@pytest.mark.parametrize("concurrent", [False, True])
@pytest.mark.parametrize("journal", ["DELETE", "WAL"])
def test_repeated_requests_share_work_but_database_writes_invalidate_it(
    lumpy_db: Path, monkeypatch: pytest.MonkeyPatch, concurrent: bool, journal: str
) -> None:
    calls = []
    original = grupo._run

    def counted(conn, run_id):
        calls.append(run_id)
        return original(conn, run_id)

    monkeypatch.setattr(grupo, "_run", counted)
    with closing(sqlite3.connect(lumpy_db)) as writer, client(lumpy_db) as browser:
        writer.execute(f"PRAGMA journal_mode={journal}")

        def request(_):
            return browser.get("/resultados/grupo", params={**CHOICES, "sku": "X"})

        if concurrent:
            with ThreadPoolExecutor(max_workers=2) as pool:
                responses = list(pool.map(request, range(2)))
        else:
            responses = [request(0), request(1)]
        assert all(r.status_code == 200 for r in responses)
        assert calls == list(CHOICES.values())
        before = lumpy_db.stat().st_mtime_ns
        writer.execute("UPDATE m3_veredictos SET dm_estadistico=-dm_estadistico WHERE sku='X'")
        writer.commit()
        if journal == "WAL":
            assert lumpy_db.stat().st_mtime_ns == before  # only the WAL changed.
        updated = request(2)
        assert "Empeora de forma significativa en las tres semillas." in updated.text
        assert calls == list(CHOICES.values()) * 2


def test_an_empty_wal_deleted_without_writes_keeps_the_saved_result(
    lumpy_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = []
    original = grupo._run

    def counted(conn, run_id):
        calls.append(run_id)
        return original(conn, run_id)

    monkeypatch.setattr(grupo, "_run", counted)
    wal = Path(f"{lumpy_db}-wal")
    with client(lumpy_db) as browser:

        def request():
            return browser.get("/resultados/grupo", params={**CHOICES, "sku": "X"})

        with closing(sqlite3.connect(lumpy_db)) as other:
            other.execute("SELECT COUNT(*) FROM m3_corridas").fetchone()
            assert wal.stat().st_size == 0
            assert request().status_code == 200
        # The last connection to close deletes the empty WAL; nothing was written.
        assert not wal.exists()
        assert request().status_code == 200
        assert calls == list(CHOICES.values())
