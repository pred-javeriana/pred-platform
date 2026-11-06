"""The compact Results group page, its missing-seed state and trace download."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from pred_platform.app.main import create_app
from pred_platform.config import Settings
from tests.test_group_evidence import group_db as group_db  # noqa: F401
from tests.test_group_evidence import sql
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


def test_each_product_shows_the_three_directions_and_missing_test_causes(lumpy_db: Path) -> None:
    with client(lumpy_db) as browser:
        tested = browser.get("/resultados/grupo", params={**CHOICES, "sku": "X"}).text
        assert tested.count("negativo") == 4  # one rule explanation and three source rows.
        assert "Fuentes del SKU X" in tested
        assert "Mejora significativa en las tres semillas" in tested
        missing = browser.get("/resultados/grupo", params={**CHOICES, "sku": "Z"}).text
        assert "sin segunda familia" not in missing
        assert "eligibility.fewer_than_two_families: SARIMA sin candidato" in missing
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
        assert "Diferencia significativa sin mejora uniforme" in mixed
        assert "Empeora de forma significativa" not in mixed


def test_seed_two_pending_never_displays_two_seed_numbers_as_final(lumpy_db: Path) -> None:
    sql(lumpy_db, "UPDATE m3_corridas SET estado='EN_CURSO' WHERE run_id='seed-2'")
    with client(lumpy_db) as browser:
        html = browser.get("/resultados/grupo").text
        assert "El grupo final requiere tres corridas publicadas" in html
        assert "Significancia del grupo por clase" not in html
        assert 'value="seed-2"' not in html
        assert browser.get("/resultados/grupo.json").status_code == 400


def test_unpublished_or_wrong_seed_cannot_be_injected_through_a_shared_link(lumpy_db: Path) -> None:
    with client(lumpy_db) as browser:
        for value in ("unknown", "seed-2"):
            response = browser.get("/resultados/grupo", params={**CHOICES, "semilla0": value})
            assert response.status_code == 200
            assert "Seleccione una corrida PUBLICADA de la semilla 0" in response.text
            assert "Significancia del grupo por clase" not in response.text
            assert (
                browser.get(
                    "/resultados/grupo.json", params={**CHOICES, "semilla0": value}
                ).status_code
                == 400
            )


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


def test_malformed_protocol_is_a_visible_problem_not_a_server_error(lumpy_db: Path) -> None:
    sql(lumpy_db, "UPDATE m3_protocolos SET protocolo='bad-json' WHERE run_id='seed-2'")
    with client(lumpy_db) as browser:
        response = browser.get("/resultados/grupo")
        assert response.status_code == 200
        assert 'role="alert"' in response.text
        assert "Significancia del grupo por clase" not in response.text


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
        assert "El grupo final requiere tres corridas publicadas" in html
        assert browser.get("/resultados/grupo.json").status_code == 400
    assert not (tmp_path / "unused.db").exists()
