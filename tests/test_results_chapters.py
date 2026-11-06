"""Los capítulos de Resultados a través de las rutas que abre el operador."""

from __future__ import annotations

import csv
import io
import math
import re
import sqlite3
import xml.etree.ElementTree as ET
from contextlib import closing
from datetime import datetime
from html import unescape
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.m3_publicada import publish, publish_confirmation
from tests.test_corridas import _dal_client, _fixture_client
from tests.test_resultados import WithoutChampion
from tests.test_worker import Env
from tests.test_worker import env as env  # noqa: F401

_FORM = {
    "families": ["classical", "ml", "dl"],
    "metric": "mae",
    "min_train": "40",
    "horizon": "2",
    "step": "14",
    "seasonality": "7",
    "trials": "1",
    "workers": "2",
}


def _text(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]*>", " ", html)).strip()


def _launch(env: Env, client: TestClient, seed: int):
    values = {"ingest_id": env.ingest_id, "seed": str(seed), **_FORM}
    response = client.post("/ejecucion/lanzar", data=values, follow_redirects=False)
    # Estos ajustes dejan una clase con una familia: el operador acepta el aviso en Revisar.
    confirmation = re.search(r'name="confirmar" value="([^"]+)"', response.text)
    assert response.status_code == 200 and confirmation is not None
    return client.post(
        "/ejecucion/lanzar",
        data={**values, "confirmar": unescape(confirmation.group(1))},
        follow_redirects=False,
    )


def _publish(env: Env, client: TestClient, seed: int, *, only_smooth: bool = False) -> str:
    launched = _launch(env, client, seed)
    assert launched.status_code == 303
    env.worker().run_next()
    [(number, engine_run)] = env.rows("SELECT id, run_id FROM ejecuciones ORDER BY id DESC LIMIT 1")
    run_id = f"m3-ui-{seed}"
    verdicts = {
        "FIL-0001": ("smooth", "VALIDADO"),
        "FIL-0002": ("smooth", "VALIDADO"),
        "FIL-0003": ("smooth", "EXPLORATORIO"),
    }
    if not only_smooth:
        verdicts.update(
            {"FIL-0004": ("erratic", "VALIDADO"), "FIL-0005": ("lumpy", "EVIDENCIA_INSUFICIENTE")}
        )
    publish(
        env.db,
        ingesta_sha256=env.ingest_id,
        run_id_m2=engine_run,
        run_id=run_id,
        verdicts=verdicts,
        significant=("FIL-0001",),
    )
    with closing(sqlite3.connect(env.db)) as conn, conn:
        conn.execute(
            "UPDATE m3_veredictos SET dm_significativa = 1, dm_estadistico = 2.9, "
            "dm_p_valor = 0.004, dm_p_ajustado = 0.02 WHERE run_id = ? AND sku = 'FIL-0003'",
            (run_id,),
        )
    significance: list[tuple[object, ...]] = [("smooth", 3, 1, 1, 1, 0, 0.98)]
    if not only_smooth:
        significance += [("erratic", 1, 0, 0, 0, 0, None), ("lumpy", 0, 0, 0, 0, 0, None)]
    publish_confirmation(
        env.db,
        run_id=run_id,
        significance=significance,
        confirmations=[
            (
                "smooth",
                "classical",
                "confirmado",
                None,
                0.01,
                0.03,
                0.05,
                3,
                0.8,
                0.75,
                0.85,
                None,
                "ml",
            )
        ],
        comparisons=[
            (
                "smooth",
                "ml",
                3,
                0.9,
                0.85,
                0.95,
                0.89,
                0.86,
                0.94,
                3,
                0.5,
                1.0,
                0.0,
                0.01,
                0.01,
                1,
                0,
                None,
            )
        ],
    )
    return str(number)


@pytest.fixture
def published(env: Env) -> tuple[TestClient, str]:
    client = _dal_client(env)
    return client, _publish(env, client, 0)


@pytest.mark.parametrize("chapter", ["resumen", "p1", "p2", "estabilidad", "productos", "exportar"])
def test_each_chapter_is_server_rendered_and_preserves_the_exact_evaluation(
    published: tuple[TestClient, str],
    chapter: str,
) -> None:
    client, number = published
    response = client.get(f"/corridas/{number}/resultados/{chapter}?m3=m3-ui-0")
    assert response.status_code == 200
    html = response.text
    nav = re.search(r'<nav class="pred-chapters".*?</nav>', html, re.S)
    assert nav is not None
    assert 'aria-label="Capítulos de resultados"' in nav.group()
    for target in ("resumen", "p1", "p2", "estabilidad", "productos", "exportar"):
        assert f"/corridas/{number}/resultados/{target}?m3=m3-ui-0" in nav.group()
    assert f'/resultados/{chapter}?m3=m3-ui-0" aria-current="page"' in nav.group()
    assert 'src="http' not in html
    assert "data-tip=" in html or chapter in ("resumen", "exportar")
    assert "Prototipo" not in _text(html)


@pytest.mark.parametrize(
    ("vista", "chapter", "anchor"),
    [
        ("clase", "p1", ""),
        ("errores", "p1", "#perfiles-error"),
        ("sku", "productos", ""),
        ("consulta", "productos", ""),
        ("exportar", "exportar", ""),
    ],
)
def test_old_bookmarks_keep_every_filter_and_repeated_query_value(
    published: tuple[TestClient, str],
    vista: str,
    chapter: str,
    anchor: str,
) -> None:
    client, number = published
    query = f"vista={vista}&m3=m3-ui-0&sku_class=smooth&sort=razon_sn&page=2&tag=a&tag=b"
    response = client.get(f"/resultados?ejecucion={number}&{query}", follow_redirects=False)
    assert response.status_code == 307
    assert (
        response.headers["location"] == f"/corridas/{number}/resultados/{chapter}?{query}{anchor}"
    )


def test_export_is_the_last_chapter_with_one_footer_and_links_bound_to_the_evaluation(
    published: tuple[TestClient, str],
) -> None:
    client, number = published
    products = client.get(f"/corridas/{number}/resultados/productos?m3=m3-ui-0").text
    next_link = f'href="/corridas/{number}/resultados/exportar?m3=m3-ui-0"> Siguiente: Exportar'
    assert next_link in re.sub(r"\s+", " ", products)
    html = client.get(f"/corridas/{number}/resultados/exportar?m3=m3-ui-0").text
    footer = re.findall(r'<nav class="pred-[a-z-]*" aria-label="Navegación entre [^"]*"', html)
    assert footer == ['<nav class="pred-chapter-footer" aria-label="Navegación entre capítulos"']
    back_link = f'href="/corridas/{number}/resultados/productos?m3=m3-ui-0"> Atrás: Productos'
    assert back_link in re.sub(r"\s+", " ", html)
    assert "Siguiente:" not in _text(html)
    assert f"/resultados/exportar.csv?ejecucion={number}&amp;m3=m3-ui-0" in html
    assert f"/resultados/exclusiones.csv?ejecucion={number}&amp;m3=m3-ui-0" in html
    # The chapter link exports every product; the filtered export stays on Productos.
    assert "Respeta los filtros de Productos" not in html
    assert "de todos los productos de la evaluación" in _text(html)


def test_the_summary_answers_from_published_rows_and_keeps_the_counts_visible(
    published: tuple[TestClient, str],
) -> None:
    client, number = published
    html = client.get(f"/corridas/{number}/resultados/resumen").text
    text = _text(html)
    assert "Pregunta de investigación · Tesis, «Introducción»" in text
    assert "de una empresa a partir de sus datos históricos" in text
    assert "No concluyente" in text  # only one of three types has a confirmation
    assert "Respaldada en esta corrida" not in text
    assert 'aria-label="Respuesta por tipo de demanda"' in html
    assert "1 de 4 productos probados (25,0 %)" in text
    assert "20,0 a 20,0 % menos" in text
    assert "Productos de la ejecución 5" in text
    assert "Con veredicto 5" in text
    assert "Evaluados 4" in text
    disclosure = re.search(
        r"<details[^>]*>\s*<summary>Campeona de cada producto, por familia</summary>", html
    )
    assert disclosure is not None and "open" not in disclosure.group()


def test_selection_confirmation_and_profiles_keep_exact_values_in_closed_tables(
    published: tuple[TestClient, str],
) -> None:
    client, number = published
    html = client.get(f"/corridas/{number}/resultados/p1?metrica=rmse").text
    assert 'data-tip="0,800"' in html
    assert 'data-tip="0,900"' in html
    assert 'title="0,89"' in html
    assert 'title="[0,86; 0,94]"' in html
    assert 'title="[0,5; 1,0]"' in html
    for label in (
        "Ver el detalle de la selección",
        "Ver las cifras de la prueba",
        "Ver las cifras de los perfiles de error",
        "Ver las cifras de los bloques",
    ):
        tag = re.search(r"<details[^>]*>\s*<summary>" + label + r"</summary>", html)
        assert tag is not None and "open" not in tag.group()
    assert 'aria-label="Qué es cada marca"' in html
    assert "Fechas objetivo de la reserva" in html
    assert "Hodges-Lehmann" in html and "Victorias de la campeona" in html


def test_verification_counts_all_products_including_the_unproven_and_untested(
    published: tuple[TestClient, str],
) -> None:
    client, number = published
    html = client.get(f"/corridas/{number}/resultados/p2").text
    text = _text(html)
    assert "Suave 3 1 1 1 0 98 %" in text
    assert "Errática 1 0 0 0 0" in text
    assert "Grumosa 0 0 0 0 0" in text
    assert 'data-label="Sin prueba · Grumosa"' in html
    assert "Validado no es lo mismo que significativo" in text
    assert "Sin diferencia demostrada no significa equivalencia" in text
    for label in ("mejora", "sin_diferencia", "peor"):
        assert f"prueba={label}&amp;m3=m3-ui-0" in html


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        ("mejora", ["FIL-0001"]),
        ("sin_diferencia", ["FIL-0002", "FIL-0004"]),
        ("peor", ["FIL-0003"]),
        ("sin_prueba", ["FIL-0005"]),
    ],
)
def test_the_test_filter_and_csv_use_the_same_published_direction_not_r_or_the_verdict(
    published: tuple[TestClient, str],
    result: str,
    expected: list[str],
) -> None:
    client, number = published
    html = client.get(f"/corridas/{number}/resultados/productos?prueba={result}&size=1").text
    products = re.findall(r'aria-label="Ver evidencia del SKU ([^"]+)"', html)
    assert products == expected[:1]
    if len(expected) > 1:
        page2 = re.search(r'href="([^"]*prueba=[^"]*page=2[^"]*)"', html)
        assert page2 is not None
        second = client.get(unescape(page2.group(1))).text
        assert re.findall(r'aria-label="Ver evidencia del SKU ([^"]+)"', second) == expected[1:]
    response = client.get(
        f"/resultados/exportar.csv?ejecucion={number}&m3=m3-ui-0&prueba={result}&size=1"
    )
    assert response.status_code == 200
    rows = list(csv.DictReader(io.StringIO(response.text.lstrip("\ufeff"))))
    assert [row["sku"] for row in rows] == expected


def test_an_unknown_test_filter_is_a_visible_warning(published: tuple[TestClient, str]) -> None:
    client, number = published
    response = client.get(f"/corridas/{number}/resultados/productos?prueba=inventada")
    assert response.status_code == 200
    assert "El resultado de la prueba no es válido" in _text(response.text)


@pytest.mark.parametrize("chapter", ["p1", "p2", "estabilidad", "productos"])
def test_chart_coordinates_are_finite_even_when_a_published_interval_is_unbounded(
    published: tuple[TestClient, str],
    env: Env,
    chapter: str,
) -> None:
    client, number = published
    with closing(sqlite3.connect(env.db)) as conn, conn:
        conn.execute(
            "UPDATE m3_comparaciones SET ic_mediana_inferior = 0, ic_mediana_superior = ?",
            (math.inf,),
        )
    html = client.get(f"/corridas/{number}/resultados/{chapter}").text
    charts = re.findall(r'<svg class="pred-result-svg.*?</svg>', html, re.S)
    assert charts
    for chart in charts:
        root = ET.fromstring(chart)
        for element in root.iter():
            for name in ("x", "x1", "x2", "y", "y1", "y2", "width", "height", "cx", "cy"):
                if name in element.attrib:
                    assert math.isfinite(float(element.attrib[name])), (
                        chapter,
                        name,
                        element.attrib[name],
                    )


def test_stability_requires_all_three_seeds_and_only_compares_the_same_settings(env: Env) -> None:
    client = _dal_client(env)
    first = _publish(env, client, 0, only_smooth=True)
    _publish(env, client, 1, only_smooth=True)
    path = f"/corridas/{first}/resultados/estabilidad"
    text = _text(client.get(path).text)
    assert "Sin responder: faltan corridas publicadas" in text
    assert "2 de 3 confirmadas" in text
    _publish(env, client, 2, only_smooth=True)
    text = _text(client.get(path).text)
    assert "La misma campeona gana y se confirma en las tres semillas" in text
    assert "3 de 3 confirmadas" in text
    assert "No concluyente" not in text


def test_a_run_without_publication_locks_results_and_the_rail(env: Env) -> None:
    client = _dal_client(env)
    response = _launch(env, client, 0)
    assert response.status_code == 303
    html = client.get("/corridas/1/resultados/p1").text
    assert "Los resultados todavía no existen" in _text(html)
    assert "Los resultados todavía no existen" in _text(client.get("/corridas/1/resultados").text)
    # An old Productos address keeps its partial reading instead of the locked entry.
    old = client.get("/corridas/1/resultados?vista=sku&q=FIL", follow_redirects=False)
    assert old.headers["location"] == "/corridas/1/resultados/productos?vista=sku&q=FIL"
    assert "Los resultados todavía no existen" not in _text(
        client.get(old.headers["location"]).text
    )
    assert 'aria-label="Capítulos de resultados"' not in html
    assert 'href="/corridas/1/ejecucion"' in html
    execution = client.get("/corridas/1/ejecucion").text
    rail = re.search(r'<nav class="pred-rail".*?</nav>', execution, re.S)
    assert rail is not None
    assert 'href="/corridas/1/resultados"' not in rail.group()
    assert "Se abre cuando la evaluación final publique" in _text(rail.group())


def test_missing_capability_keeps_the_family_reading_available() -> None:
    response = _fixture_client(repo=WithoutChampion()).get(
        "/corridas/1/resultados/productos?lista=familias"
    )
    assert response.status_code == 200
    assert "Clásica" in _text(response.text)
    assert 'href="/resultados/sku/FIL-0001?' in response.text
    assert 'aria-label="Capítulos de resultados"' in response.text


@pytest.mark.parametrize(
    ("bounds", "shown"),
    [
        ((0.6193202626195218, 0.8990134844073785, None), "[0,619; 0,899]"),
        ((0.0, None, None), "[0,000; +∞)"),
        ((0.2, 0.8, 1.4), "[0,200; 0,800] o [1,400; +∞)"),
    ],
)
def test_product_mini_chart_keeps_the_published_interval_separate_from_bh(
    published: tuple[TestClient, str],
    env: Env,
    bounds: tuple[float, float | None, float | None],
    shown: str,
) -> None:
    client, number = published
    with closing(sqlite3.connect(env.db)) as conn, conn:
        conn.execute(
            "INSERT INTO m3_intervalos_r (run_id, sku, nivel, inferior, superior, inferior_cola) "
            "VALUES ('m3-ui-0', 'FIL-0001', 0.95, ?, ?, ?)",
            bounds,
        )
    response = client.get(f"/corridas/{number}/resultados/productos?q=FIL-0001&m3=m3-ui-0")
    assert response.status_code == 200
    assert shown in response.text
    assert "Intervalo sin ajuste BH: " + shown in response.text
    assert "Mejora verificada" in _text(response.text)
    assert "La prueba y el p ajustado se muestran aparte" in _text(response.text)


@pytest.mark.parametrize(
    "chapter,lista",
    [
        ("resumen", ""),
        ("p1", ""),
        ("p2", ""),
        ("estabilidad", ""),
        ("productos", ""),
        ("productos", "familias"),
    ],
)
def test_publication_time_is_l4_end_in_bogota_and_keeps_exact_utc_on_hover(
    published: tuple[TestClient, str],
    monkeypatch: pytest.MonkeyPatch,
    chapter: str,
    lista: str,
) -> None:
    client, number = published
    app = client.app
    assert isinstance(app, FastAPI)
    repository = app.state.repository
    original = repository.get_run_status

    def timed_status(run_id: str, **kwargs: Any):
        status = original(run_id, **kwargs)
        return status.model_copy(
            update={
                "stage_timings": [
                    SimpleNamespace(
                        code="L4",
                        started_at=datetime.fromisoformat("2026-10-27T09:00:00+00:00"),
                        finished_at=datetime.fromisoformat("2026-10-27T14:36:00+00:00"),
                    )
                ]
            }
        )

    monkeypatch.setattr(repository, "get_run_status", timed_status)
    params = {"m3": "m3-ui-0"}
    if lista:
        params["lista"] = lista
    response = client.get(f"/corridas/{number}/resultados/{chapter}", params=params)
    assert response.status_code == 200
    assert "Evaluación publicada 27 oct 2026, 9:36 a. m." in _text(response.text)
    assert 'title="2026-10-27 14:36:00 UTC' in response.text
    assert "Publicados" in _text(response.text)
    assert "27 oct 2026, 5:00 a. m." not in _text(response.text)


def test_summary_does_not_infer_one_model_for_a_type_without_a_published_candidate(
    published: tuple[TestClient, str],
    env: Env,
) -> None:
    client, number = published
    initial = client.get(f"/corridas/{number}/resultados/resumen").text
    assert "Clásica (SARIMA)" in initial
    with closing(sqlite3.connect(env.db)) as conn, conn:
        conn.execute(
            "UPDATE m3_veredictos SET candidato_campeon=NULL, modelos_evaluados='[]' "
            "WHERE run_id='m3-ui-0' AND sku_class='erratic'"
        )
    html = client.get(f"/corridas/{number}/resultados/resumen").text
    assert "Clásica pronostica mejor los 3 tipos" in _text(html)
    assert "Clásica (SARIMA)" not in html


def test_an_older_publication_does_not_inherit_a_later_attempts_l4_end(
    published: tuple[TestClient, str],
    env: Env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, number = published
    app = client.app
    assert isinstance(app, FastAPI)
    repository = app.state.repository
    original = repository.get_run_status

    def later_attempt(run_id: str, **kwargs: Any):
        return original(run_id, **kwargs).model_copy(
            update={
                "stage_timings": [
                    SimpleNamespace(
                        code="L4",
                        started_at=datetime.fromisoformat("2026-10-28T09:00:00+00:00"),
                        finished_at=datetime.fromisoformat("2026-10-28T14:36:00+00:00"),
                    )
                ]
            }
        )

    monkeypatch.setattr(repository, "get_run_status", later_attempt)
    html = client.get(f"/corridas/{number}/resultados/resumen?m3=m3-ui-0").text
    assert "Evaluación publicada Fecha de publicación no informada" in _text(html)
    assert "28 oct 2026, 9:36 a. m." not in _text(html).split("Evaluación publicada", 1)[1]


def test_product_list_defaults_to_25_rows_and_preserves_the_exact_run_on_page_two(
    published: tuple[TestClient, str],
    env: Env,
) -> None:
    client, number = published
    [(engine_run,)] = env.rows("SELECT run_id FROM ejecuciones WHERE id = ?", int(number))
    publish(
        env.db,
        ingesta_sha256=env.ingest_id,
        run_id_m2=engine_run,
        run_id="m3-many",
        configuracion="config-many",
        creado_en="2026-10-28 10:00:00",
        verdicts={f"SKU-{i:02}": ("smooth", "VALIDADO") for i in range(26)},
    )
    first = client.get(f"/corridas/{number}/resultados/productos?m3=m3-many").text
    assert len(re.findall(r'aria-label="Ver evidencia del SKU ([^"]+)"', first)) == 25
    assert "Página 1 de 2" in _text(first)
    second = re.search(r'href="([^"]*m3=m3-many[^"]*page=2[^"]*)"', first)
    assert second is not None
    html = client.get(unescape(second.group(1))).text
    assert re.findall(r'aria-label="Ver evidencia del SKU ([^"]+)"', html) == ["SKU-25"]
    assert "Página 2 de 2" in _text(html)
