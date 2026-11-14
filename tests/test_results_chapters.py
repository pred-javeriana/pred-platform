"""Los capítulos de Resultados a través de las rutas que abre el operador."""

from __future__ import annotations

import csv
import io
import json
import math
import re
import sqlite3
import xml.etree.ElementTree as ET
from contextlib import closing
from html import unescape

import pytest
from fastapi.testclient import TestClient

from tests.m3_publicada import publish, publish_confirmation
from tests.test_corridas import _dal_client, _fixture_client
from tests.test_resultados import WithoutChampion
from tests.test_worker import Env
from tests.test_worker import env as env  # noqa: F401

_FORM = {
    "families": ["classical", "ml", "dl"],
    "metric": "mae",
    "min_train": "20",
    "horizon": "2",
    "step": "7",
    "seasonality": "7",
    "trials": "1",
    "workers": "2",
}


def _text(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]*>", " ", html)).strip()


def _launch(env: Env, client: TestClient, seed: int, *, trials: str = "1"):
    values = {"ingest_id": env.ingest_id, "seed": str(seed), **_FORM, "trials": trials}
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
    _publish_evaluation(env, engine_run, f"m3-ui-{seed}", seed, only_smooth=only_smooth)
    return str(number)


def _publish_evaluation(
    env: Env,
    engine_run: str,
    run_id: str,
    seed: int,
    *,
    only_smooth: bool = False,
    creado_en: str = "2026-10-27 10:00:00",
) -> None:
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
        seed=seed,
        creado_en=creado_en,
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
    # The context row names the declared origin in agreement with «Datos».
    assert "Datos reales (declarados)" in _text(html)
    # Every chapter's context row counts the products, even where the chapter reads no funnel.
    assert re.search(r'pred-context__compact-fact">\d+ productos<', html)


def test_the_product_detail_shares_the_chapter_links_and_keeps_the_chosen_evaluation(
    published: tuple[TestClient, str],
) -> None:
    """El capítulo 2 del detalle abre la verificación producto a producto (#299)."""
    client, number = published
    html = client.get(f"/corridas/{number}/resultados/producto/FIL-0001?m3=m3-ui-0").text
    nav = re.search(r'<nav class="pred-chapters".*?</nav>', html, re.S)
    assert nav is not None
    chapter_two = re.search(
        r'<a href="([^"]+)"[^>]*><span class="pred-chapters__number">2<', nav.group()
    )
    assert chapter_two is not None
    assert chapter_two.group(1) == f"/corridas/{number}/resultados/p2?m3=m3-ui-0"
    assert "vista=errores" not in nav.group()
    for target in ("resumen", "p1", "estabilidad", "exportar"):
        assert f'/resultados/{target}?m3=m3-ui-0"' in nav.group()
    assert '/resultados/productos?m3=m3-ui-0" aria-current="page"' in nav.group()


@pytest.mark.parametrize(
    ("chapter", "singular", "plural"),
    [
        ("p1", "1 producto comparable", "1 productos comparables"),
        ("p2", "0 de 1 producto ", "0 de 1 productos"),
        ("estabilidad", "1 tipo con campeona confirmada", "1 tipos con campeona"),
        ("resumen", "0 de 1 producto ", "0 de 1 productos"),
        ("resumen", "Respaldada para 1 producto ", "Respaldada para 1 productos"),
    ],
)
def test_a_count_of_one_reads_in_singular(
    published: tuple[TestClient, str], chapter: str, singular: str, plural: str
) -> None:
    """Errática tiene un solo producto comparable y probado, solo Suave se confirma y un solo
    producto tiene mejora verificada (#310, #328)."""
    client, number = published
    text = _text(client.get(f"/corridas/{number}/resultados/{chapter}?m3=m3-ui-0").text)
    assert singular in text
    assert plural not in text


@pytest.mark.parametrize("chapter", ["p1", "p2"])
@pytest.mark.parametrize(("only_smooth", "types"), [(False, 3), (True, 1)])
def test_the_indicator_row_shares_its_width_among_the_published_types(
    env: Env, chapter: str, only_smooth: bool, types: int
) -> None:
    """Sin columnas vacías cuando la evaluación publica menos de cuatro tipos (#314)."""
    client = _dal_client(env)
    number = _publish(env, client, 0, only_smooth=only_smooth)
    html = client.get(f"/corridas/{number}/resultados/{chapter}?m3=m3-ui-0").text
    row = re.search(
        r'<dl class="pred-results-kpis pred-results-kpis--types" style="--n: (\d)">'
        r"(.*?)</dl>",
        html,
        re.S,
    )
    assert row is not None
    assert int(row.group(1)) == types == len(re.findall(r"<dt>", row.group(2)))


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


def test_an_unknown_chapter_returns_to_the_step_entry_with_its_query(
    published: tuple[TestClient, str],
) -> None:
    client, number = published
    response = client.get(
        f"/corridas/{number}/resultados/nada?m3=m3-ui-0&tag=a&tag=b", follow_redirects=False
    )
    assert response.status_code == 307
    assert response.headers["location"] == f"/corridas/{number}/resultados?m3=m3-ui-0&tag=a&tag=b"
    page = client.get(response.headers["location"])
    assert page.url.path == f"/corridas/{number}/resultados/resumen"
    assert "No existe la corrida" not in _text(page.text)


def test_export_is_the_last_chapter_with_one_footer_and_links_bound_to_the_evaluation(
    published: tuple[TestClient, str],
) -> None:
    client, number = published
    products = client.get(f"/corridas/{number}/resultados/productos?m3=m3-ui-0").text
    next_link = f'href="/corridas/{number}/resultados/exportar?m3=m3-ui-0">Siguiente: Exportar'
    assert next_link in products
    html = client.get(f"/corridas/{number}/resultados/exportar?m3=m3-ui-0").text
    # One footer, shaped like the other steps' footer: the export partial adds none of its own.
    footer = re.findall(r'<nav class="[^"]*" aria-label="Navegación entre [^"]*"', html)
    assert footer == [
        '<nav class="pred-footnav pred-chapter-footer" aria-label="Navegación entre capítulos"'
    ]
    back_link = (
        f'href="/corridas/{number}/resultados/productos?m3=m3-ui-0">'
        '<svg aria-hidden="true"><use href="#i-arrow-left"></use></svg>Atrás: Productos'
    )
    assert back_link in html
    # Every icon is a sprite reference, not an empty placeholder.
    assert "pred-icon i-" not in html
    assert html.count('<use href="#i-download">') == 3
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
    html = client.get(path).text
    text = _text(html)
    # The context row picks the run by seed and day, named as the prototype names it.
    picker = re.search(r'<select[^>]*id="ejecucion-mostrada"[^>]*>', html)
    assert picker is not None and 'aria-label="Elegir la corrida"' in picker.group()
    assert re.search(r">#\d+ · semilla 2 · \d+ \w+ \d{4}</option>", html)
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


def _log_l4(env: Env, number: str, *events: tuple[str, str, str]) -> None:
    """Append L4 events, as the engine writes them, to the run's own progress log."""
    [(folder,)] = env.rows("SELECT ruta_corrida FROM ejecuciones WHERE id = ?", number)
    log = env.settings.data_root / folder / "progreso.jsonl"
    with log.open("a", encoding="utf-8") as handle:
        handle.write("{broken line\n")
        for kind, moment, state in events:
            handle.write(
                json.dumps({"event": kind, "t": moment, "stage": "L4", "state": state}) + "\n"
            )


@pytest.mark.parametrize(
    "chapter,lista",
    [
        ("resumen", ""),
        ("p1", ""),
        ("p2", ""),
        ("estabilidad", ""),
        ("productos", ""),
        ("productos", "familias"),
        ("exportar", ""),
    ],
)
def test_publication_time_is_l4_end_in_bogota_and_keeps_exact_utc_on_hover(
    published: tuple[TestClient, str],
    env: Env,
    chapter: str,
    lista: str,
) -> None:
    client, number = published
    # The evaluation was created at 10:00 UTC, two seconds after its L4 started.
    _log_l4(
        env,
        number,
        ("stage_started", "2026-10-27T09:59:58+00:00", ""),
        ("stage_finished", "2026-10-27T14:36:00.5+00:00", "completed"),
    )
    params = {"m3": "m3-ui-0"}
    if lista:
        params["lista"] = lista
    response = client.get(f"/corridas/{number}/resultados/{chapter}", params=params)
    assert response.status_code == 200
    text = _text(response.text)
    assert "Evaluación publicada 27 oct 2026, 9:36 a. m." in text
    assert 'title="2026-10-27 14:36:00.500000 UTC' in response.text
    rail = re.search(r'<nav class="pred-rail".*?</nav>', response.text, re.S)
    assert rail is not None
    # The rail shows the Bogotá day; its title keeps the exact L4 end.
    assert "Publicados 27 oct 2026" in _text(rail.group())
    assert "2026-10-27 14:36:00.500000 UTC" in rail.group()
    # The group chooser below names each option by its creation time; the page header must not.
    assert "27 oct 2026, 5:00 a. m." not in text.split("Grupo final de semillas", 1)[0]


def test_an_older_publication_does_not_inherit_a_later_attempts_l4_end(
    published: tuple[TestClient, str],
    env: Env,
) -> None:
    client, number = published
    _log_l4(
        env,
        number,
        ("stage_started", "2026-10-27T09:59:58+00:00", ""),
        ("stage_finished", "2026-10-27T11:00:00+00:00", "failed"),
        ("stage_started", "2026-10-28T09:00:00+00:00", ""),
        ("stage_finished", "2026-10-28T14:36:00+00:00", "completed"),
    )
    html = client.get(f"/corridas/{number}/resultados/resumen?m3=m3-ui-0").text
    # Neither the failed attempt that held its creation nor a later attempt dates it.
    assert "Evaluación publicada Fecha de publicación no informada" in _text(html)
    shown = _text(html).split("Evaluación publicada", 1)[1]
    assert "28 oct 2026, 9:36 a. m." not in shown and "27 oct 2026, 6:00 a. m." not in shown


def test_stage_times_follow_the_execution_reader_rules_for_every_attempt(
    published: tuple[TestClient, str],
    env: Env,
) -> None:
    client, number = published
    # A clock that went back: this L4 «ended» before it started and dates nothing.
    _log_l4(
        env,
        number,
        ("stage_started", "2026-10-27T09:59:58+00:00", ""),
        ("stage_finished", "2026-10-27T09:00:00+00:00", "completed"),
    )
    repo = env.repo()
    timings = repo.get_stage_timings(number)
    current = repo.get_run_status(number, size=1).stage_timings  # type: ignore[attr-defined]
    # Results reads every attempt, Ejecución the current one; the same L4 has the same end.
    l4 = [t for t in timings if t.code == "L4"][-1]
    assert l4 == next(t for t in current if t.code == "L4")
    assert l4.finished_at is None and l4.finish_state == "completed"
    html = client.get(f"/corridas/{number}/resultados/resumen?m3=m3-ui-0").text
    assert "Evaluación publicada Fecha de publicación no informada" in _text(html)


def test_without_a_progress_log_the_publication_time_is_unknown_not_the_creation(
    published: tuple[TestClient, str],
    env: Env,
) -> None:
    client, number = published
    [(folder,)] = env.rows("SELECT ruta_corrida FROM ejecuciones WHERE id = ?", number)
    (env.settings.data_root / folder / "progreso.jsonl").unlink()
    html = client.get(f"/corridas/{number}/resultados/resumen?m3=m3-ui-0").text
    assert "Evaluación publicada Fecha de publicación no informada" in _text(html)
    assert "27 oct 2026, 5:00 a. m." not in _text(html).split("Evaluación publicada", 1)[1]


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


def _sql(env: Env, statement: str, *args: object) -> None:
    with closing(sqlite3.connect(env.db)) as conn, conn:
        conn.execute(statement, args)


def _three_seeds(env: Env, client: TestClient) -> list[str]:
    return [_publish(env, client, seed, only_smooth=True) for seed in range(3)]


@pytest.mark.parametrize(
    ("table", "assignment", "where"),
    [
        (
            "m3_corridas",
            "identidad = json_set(identidad, '$.codigo_version', '0000000')",
            "run_id = 'm3-ui-2'",
        ),
        (
            "m3_protocolos",
            "protocolo = json_set(protocolo, '$.politica.tolerancia_empate', 0.5)",
            "run_id = 'm3-ui-2'",
        ),
        (
            "m3_protocolos",
            "protocolo = json_set(protocolo, '$.bloques.dias_reserva', 81)",
            "run_id = 'm3-ui-2'",
        ),
        ("m3_veredictos", "sku_class = 'erratic'", "run_id = 'm3-ui-2' AND sku = 'FIL-0003'"),
    ],
    ids=["code", "policy", "blocks", "population"],
)
def test_stability_needs_runs_the_final_group_accepts_not_only_the_same_settings(
    env: Env, table: str, assignment: str, where: str
) -> None:
    client = _dal_client(env)
    first, *_ = _three_seeds(env, client)
    _sql(env, f"UPDATE {table} SET {assignment} WHERE {where}")
    stability = _text(client.get(f"/corridas/{first}/resultados/estabilidad").text)
    summary = _text(client.get(f"/corridas/{first}/resultados/resumen").text)
    for text in (stability, summary):
        assert "La misma campeona gana y se confirma en las tres semillas" not in text
    assert "Sin responder: faltan corridas publicadas" in stability
    assert "2 de 3 confirmadas" in stability
    assert "Semillas con resultados 2 de 3" in summary
    assert "Pendiente" in stability


def test_a_queued_relaunch_of_a_seed_does_not_hide_its_published_evaluation(env: Env) -> None:
    client = _dal_client(env)
    first, *_ = _three_seeds(env, client)
    assert _launch(env, client, 1, trials="2").status_code == 303
    html = client.get(f"/corridas/{first}/resultados/estabilidad").text
    text = _text(html)
    assert "La misma campeona gana y se confirma en las tres semillas" in text
    assert "3 de 3 confirmadas" in text
    assert "Sin resultados" not in text and "Pendiente" not in text
    # The published seed 1 keeps its own run, not the queued one launched later.
    assert re.search(r"Semilla 1 · #2", text)
    summary = _text(client.get(f"/corridas/{first}/resultados/resumen").text)
    assert "Semillas con resultados 3 de 3" in summary


def test_a_confirmation_that_cannot_be_read_is_not_reported_as_absent(env: Env) -> None:
    client = _dal_client(env)
    first, *_ = _three_seeds(env, client)
    _sql(env, "UPDATE m3_confirmaciones SET p_holm = 5 WHERE run_id = 'm3-ui-0'")
    for chapter in ("resumen", "p2", "estabilidad"):
        html = client.get(f"/corridas/{first}/resultados/{chapter}").text
        text = _text(html)
        assert "no es válida" in text, chapter
        for absence in (
            "no publicó pruebas",
            "Sin confirmación publicada",
            "Sin efecto publicado",
            "Sin prueba publicada",
            "0 con mejora verificada",
        ):
            assert absence not in text, (chapter, absence)
    summary = _text(client.get(f"/corridas/{first}/resultados/resumen").text)
    assert "No se pudo leer" in summary
    assert "Con veredicto" in summary and "Evaluados" in summary  # the readable evidence stays
    verification = _text(client.get(f"/corridas/{first}/resultados/p2").text)
    assert "No se pudieron leer las pruebas por producto" in verification
    assert "Validado no es lo mismo que significativo" in verification
    stability = _text(client.get(f"/corridas/{first}/resultados/estabilidad").text)
    assert "No se pudo leer la confirmación" in stability
    assert "Sin responder: no se pudo leer una confirmación" in stability
    assert "Respaldada" not in stability.split("Pregunta 3")[-1].split("Campeona por semilla")[0]


def _histogram_counts(html: str) -> dict[str, list[int]]:
    """Per class, the sum of each outcome column of the closed histogram table."""
    table = re.search(r'aria-label="Distribución de r por tipo y prueba".*?</table>', html, re.S)
    assert table is not None
    header = re.findall(r'<th scope="col">([^<]+)</th>', table.group())
    assert header[-1] == "Sin prueba"
    totals: dict[str, list[int]] = {}
    for label, counts in re.findall(
        r'<tr><th scope="row">([^<]+)</th><td>[^<]*</td>((?:<td class="num">\d+</td>)+)</tr>',
        table.group(),
    ):
        row = [int(n) for n in re.findall(r">(\d+)<", counts)]
        previous = totals.setdefault(label, [0] * len(row))
        totals[label] = [a + b for a, b in zip(previous, row, strict=True)]
    return totals


def test_a_product_with_r_and_no_test_stays_in_the_histogram_and_its_table(
    published: tuple[TestClient, str], env: Env
) -> None:
    client, number = published
    # FIL-0004 is the one erratic product: it has r but no test, with its published cause.
    _sql(
        env,
        "UPDATE m3_veredictos SET dm_estadistico = NULL, dm_p_valor = NULL, "
        "dm_p_ajustado = NULL, dm_alfa = NULL, dm_significativa = NULL, "
        "dm_causa = 'datos_sinteticos' WHERE run_id = 'm3-ui-0' AND sku = 'FIL-0004'",
    )
    html = client.get(f"/corridas/{number}/resultados/p2").text
    totals = _histogram_counts(html)
    # Every product with a published r is counted once: three smooth, one erratic.
    assert {label: sum(row) for label, row in totals.items()} == {
        "Suave": 3,
        "Errática": 1,
        "Grumosa": 0,
        "Intermitente": 0,
    }
    assert totals["Errática"] == [0, 0, 0, 1]
    assert totals["Suave"] == [1, 1, 1, 0]
    # The untested bin has its own colour in the chart, and its tooltip counts it.
    assert 'fill="#ffffff" class="stack-part"' in html
    assert "1 sin prueba" in html


def _stability_chart(html: str) -> str:
    start = html.index("Campeona frente a la línea base")
    return html[start : html.index("</figure>", start)]


def test_zero_and_infinite_effects_keep_their_range_and_off_scale_symbols(env: Env) -> None:
    client = _dal_client(env)
    first, second, third = _three_seeds(env, client)
    _sql(
        env, "UPDATE m3_confirmaciones SET efecto_mediana_r = ? WHERE run_id = 'm3-ui-1'", math.inf
    )
    _sql(env, "UPDATE m3_confirmaciones SET efecto_mediana_r = 0.0 WHERE run_id = 'm3-ui-2'")
    chart = _stability_chart(client.get(f"/corridas/{first}/resultados/estabilidad").text)
    assert "Rango ∞" in chart and "Rango 0,8000" not in chart
    assert "0,800 / ∞ / 0,000" in _text(chart)
    # Seeds 1 and 2 sit off the logarithmic scale: one arrow each, at its own edge.
    assert len(re.findall(r"l-?8,-5 v10 z", chart)) == 2
    assert "l8,-5 v10 z" in chart and "l-8,-5 v10 z" in chart
    # The summary of the run whose effect is infinite names it instead of calling it absent.
    summary = _text(client.get(f"/corridas/{second}/resultados/resumen").text)
    assert "Sin límite (r = ∞)" in summary and not re.search(r"\binf\b", summary)
    assert "Sin efecto publicado" not in summary
    chapter = _text(client.get(f"/corridas/{second}/resultados/p1").text)
    assert "Sin límite" in chapter and not re.search(r"\binf\b", chapter)


def test_the_run_picker_keeps_the_chapter_on_every_chapter(env: Env) -> None:
    client = _dal_client(env)
    first, second, _ = _three_seeds(env, client)
    for chapter in ("resumen", "p1", "p2", "estabilidad", "productos", "exportar"):
        html = client.get(f"/corridas/{first}/resultados/{chapter}").text
        form = re.search(r'<form[^>]*id="elegir-ejecucion".*?</form>', html, re.S)
        assert form is not None, chapter
        hidden = dict(
            re.findall(r'<input type="hidden" name="(\w+)" value="([^"]*)"', form.group())
        )
        opened = client.get(
            "/resultados", params={**hidden, "ejecucion": second}, follow_redirects=True
        )
        assert opened.status_code == 200
        assert opened.url.path == f"/corridas/{second}/resultados/{chapter}", chapter


@pytest.mark.parametrize(
    ("reason", "comparable", "wording"),
    [
        ("sin_skus_comparables", "[]", "no hubo productos comparables"),
        ("compuerta_linea_base", None, "ninguna familia supera a la línea base"),
    ],
)
@pytest.mark.parametrize("chapter", ["resumen", "p1"])
def test_a_baseline_kept_by_default_is_not_called_the_best_forecast(
    published: tuple[TestClient, str],
    env: Env,
    reason: str,
    comparable: str | None,
    wording: str,
    chapter: str,
) -> None:
    client, number = published
    _sql(
        env,
        "UPDATE m3_categorias SET familia_campeona = 'seasonal_naive', motivo = ?, "
        "skus_comparables = COALESCE(?, skus_comparables) WHERE run_id = 'm3-ui-0'",
        reason,
        comparable,
    )
    text = _text(client.get(f"/corridas/{number}/resultados/{chapter}").text)
    assert not re.search(r"pronostica mejor (los|\d)", text)
    assert wording in text
    assert "Respaldada en esta corrida" not in text


def _second_evaluation(
    env: Env, number: str, run_id: str, *, seed: int, code: str | None = None
) -> None:
    """A newer published evaluation of the same execution; ``code`` makes it another version."""
    [(engine_run,)] = env.rows("SELECT run_id FROM ejecuciones WHERE id = ?", int(number))
    _publish_evaluation(
        env, engine_run, run_id, seed, only_smooth=True, creado_en="2026-10-30 10:00:00"
    )
    if code is not None:
        _set_code(env, run_id, code)


def _set_code(env: Env, run_id: str, code: str) -> None:
    _sql(
        env,
        "UPDATE m3_corridas SET identidad = json_set(identidad, '$.codigo_version', ?) "
        "WHERE run_id = ?",
        code,
        run_id,
    )


def _group_status(client: TestClient, *runs: str) -> int:
    choices = {f"semilla{seed}": run for seed, run in enumerate(runs)}
    return client.get("/resultados/grupo.json", params=choices).status_code


def test_stability_picks_the_accepted_publication_when_a_newer_one_of_the_seed_is_not(
    env: Env,
) -> None:
    client = _dal_client(env)
    first, second, _ = _three_seeds(env, client)
    _second_evaluation(env, second, "m3-ui-1-newer", seed=1, code="0000000")
    assert _group_status(client, "m3-ui-0", "m3-ui-1", "m3-ui-2") == 200
    assert _group_status(client, "m3-ui-0", "m3-ui-1-newer", "m3-ui-2") == 400
    text = _text(client.get(f"/corridas/{first}/resultados/estabilidad").text)
    assert "La misma campeona gana y se confirma en las tres semillas" in text
    assert "3 de 3 confirmadas" in text and "Pendiente" not in text
    summary = _text(client.get(f"/corridas/{first}/resultados/resumen").text)
    assert "Semillas con resultados 3 de 3" in summary


def test_stability_picks_the_newest_accepted_publication_of_a_seed(env: Env) -> None:
    client = _dal_client(env)
    first, second, _ = _three_seeds(env, client)
    _second_evaluation(env, second, "m3-ui-1-newer", seed=1)
    _set_code(env, "m3-ui-1", "0000000")
    assert _group_status(client, "m3-ui-0", "m3-ui-1", "m3-ui-2") == 400
    assert _group_status(client, "m3-ui-0", "m3-ui-1-newer", "m3-ui-2") == 200
    text = _text(client.get(f"/corridas/{first}/resultados/estabilidad").text)
    assert "La misma campeona gana y se confirma en las tres semillas" in text
    assert "3 de 3 confirmadas" in text


def test_an_older_publication_without_class_confirmation_keeps_its_product_tests(
    published: tuple[TestClient, str], env: Env
) -> None:
    client, number = published
    # The engine fixed the class confirmation with the protocol's reserve blocks.
    _sql(
        env,
        "UPDATE m3_protocolos SET protocolo = json_remove(protocolo, '$.bloques') "
        "WHERE run_id = 'm3-ui-0'",
    )
    summary = _text(client.get(f"/corridas/{number}/resultados/resumen").text)
    verification_html = client.get(f"/corridas/{number}/resultados/p2").text
    verification = _text(verification_html)
    for text in (summary, verification):
        assert "no publicó pruebas" not in text
        assert "1 de 4 productos probados (25,0 %)" in text
    assert (
        "Suave 3 1 1 — — — 1 Errática 1 0 0 — — — 0" in verification
    )  # BY and FCR were never published
    assert "sin la confirmación por clase" in verification
    assert "1 con mejora verificada" in verification
    assert "2 sin diferencia demostrada" in verification
    assert "1 peores que la línea base" in verification
    # The filtered Productos page and the CSV list exactly the products the chapters count.
    for result, count in (("mejora", 1), ("sin_diferencia", 2), ("peor", 1)):
        page = client.get(f"/corridas/{number}/resultados/productos?prueba={result}").text
        assert len(re.findall(r'aria-label="Ver evidencia del SKU ([^"]+)"', page)) == count
        csv_text = client.get(
            f"/resultados/exportar.csv?ejecucion={number}&m3=m3-ui-0&prueba={result}"
        ).text
        assert len(list(csv.DictReader(io.StringIO(csv_text.lstrip("﻿"))))) == count


def test_published_seed_without_a_class_champion_is_not_pending(env: Env) -> None:
    client = _dal_client(env)
    first, *_ = [_publish(env, client, seed) for seed in range(3)]
    _sql(env, "DELETE FROM m3_categorias WHERE run_id = 'm3-ui-1' AND sku_class = 'erratic'")
    _sql(env, "DELETE FROM m3_confirmaciones WHERE run_id = 'm3-ui-1' AND sku_class = 'erratic'")
    html = client.get(f"/corridas/{first}/resultados/estabilidad").text
    text = _text(html)
    row = next(row for row in re.findall(r"<tr>.*?</tr>", html, re.S) if "Errática" in row)
    assert "Semilla 1 · #2 Publicada" in text
    assert "Sin campeona publicada" in _text(row)
    assert "Pendiente" not in row
    assert "Faltan corridas" not in row
    assert "No confirmada en las tres" in _text(row)
    assert "No concluyente" in text
