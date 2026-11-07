"""El recorrido de una corrida: el inicio, los cinco pasos, el borrador y las rutas anteriores."""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from fastapi import Response
from fastapi.testclient import TestClient

from pred_platform.app.borrador import COOKIE, Draft, save_draft
from pred_platform.app.configuracion import FORMAT, FORMAT_VERSION
from pred_platform.app.main import create_app
from pred_platform.config import Settings
from pred_platform.contract.errors import ArtifactMissing
from pred_platform.data.fixtures import FixtureRepository
from tests import formulario_html
from tests.m3_publicada import publish
from tests.test_ui_review import Markup
from tests.test_worker import Env, _fixture_id
from tests.test_worker import env as env  # noqa: F401  (the fixture)

SOME_INGEST = "ab" * 32
FORM: dict[str, Any] = {
    "families": ["classical", "ml", "foundation"],
    "metric": "mae",
    "min_train": "40",
    "horizon": "2",
    "step": "14",
    "seasonality": "7",
    "trials": "1",
    "seed": "0",
    "workers": "2",
}


def _fixture_client(scenario: str = "normal", repo: Any = None) -> TestClient:
    app = create_app(
        Settings(
            data_root=Path("unused"),
            db_path=Path("unused/pred.db"),
            data_source="fixture",
            fixture_scenario=scenario,
        )
    )
    if repo is not None:
        app.state.repository = repo
    return TestClient(app)


def _dal_client(env: Env) -> TestClient:
    app = create_app(replace(env.settings, worker="off"))
    app.state.repository = env.repo()
    return TestClient(app)


def _text(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()


def _rail(html: str) -> list[tuple[str, str | None]]:
    """Each step of the rail as (state, link): no link when the step is locked."""
    rail = re.search(r'<nav class="pred-rail".*?</nav>', html, re.S)
    assert rail is not None
    items = re.findall(
        r'<li class="is-(\w+)[^"]*">\s*<(?:a|span) class="pred-rail__item"(?: href="([^"]+)")?',
        rail.group(0),
    )
    return [(state, href or None) for state, href in items]


def _footer(html: str) -> str:
    found = re.search(r'<nav class="pred-footnav".*?</nav>', html, re.S)
    assert found is not None
    return found.group(0)


def _main(html: str) -> str:
    found = re.search(r"<main.*?</main>", html, re.S)
    assert found is not None
    return _text(found.group(0))


# ------------------------------------------------------------------ las rutas anteriores
@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("/datos", "/corridas/nueva/datos"),
        (
            f"/datos?ingest_id={SOME_INGEST}&sku_class=smooth&page=2",
            f"/corridas/nueva/datos?ingest_id={SOME_INGEST}&sku_class=smooth&page=2",
        ),
        ("/datos?pendiente=abc", "/corridas/nueva/datos?pendiente=abc"),
        ("/ejecucion", "/corridas/1/ejecucion"),
        ("/ejecucion?ejecucion=latest", "/corridas/1/ejecucion"),
        (
            "/ejecucion?ejecucion=1&estado=fallida&page=2&tareas=todas",
            "/corridas/1/ejecucion?estado=fallida&page=2&tareas=todas",
        ),
        ("/ejecucion?ejecucion=99", "/corridas/99/ejecucion"),
        ("/ejecucion?lanzar=1", "/corridas/nueva/datos"),
        (
            f"/ejecucion?ingest_id={SOME_INGEST}&lanzar=1&families=ml&families=dl",
            f"/corridas/nueva/ajustes?ingest_id={SOME_INGEST}&families=ml&families=dl",
        ),
        ("/resultados", "/corridas/1/resultados"),
        (
            "/resultados?ejecucion=1&vista=sku&lista=familias&q=FIL&page=2",
            "/corridas/1/resultados?vista=sku&lista=familias&q=FIL&page=2",
        ),
        ("/resultados?vista=resumen&m3=m3-a", "/corridas/1/resultados?vista=resumen&m3=m3-a"),
    ],
)
def test_the_old_addresses_lead_to_their_step_with_the_same_query(old: str, new: str) -> None:
    response = _fixture_client().get(old, follow_redirects=False)
    assert (response.status_code, response.headers["location"]) == (307, new)


@pytest.mark.parametrize("surface", ["ejecucion", "resultados"])
@pytest.mark.parametrize("reference", ["number", "engine", "latest"])
def test_an_old_link_opens_the_run_it_names(env: Env, surface: str, reference: str) -> None:
    client = _dal_client(env)
    for seed in ("11", "12"):
        assert (
            client.post(
                "/ejecucion/lanzar", data={"ingest_id": env.ingest_id, **FORM, "seed": seed}
            ).status_code
            == 200
        )
    ((engine_id,),) = env.rows("SELECT run_id FROM ejecuciones WHERE id = 1")
    publish(
        env.db,
        ingesta_sha256=env.ingest_id,
        run_id_m2=engine_id,
        verdicts={"FIL-0001": ("smooth", "VALIDADO")},
    )
    wanted = {"number": "1", "engine": engine_id, "latest": "latest"}[reference]
    expected = "2" if reference == "latest" else "1"

    page = client.get(f"/{surface}?ejecucion={wanted}", follow_redirects=True)

    assert page.status_code == 200
    assert page.url.path == f"/corridas/{expected}/{surface}"
    assert f"Corrida #{expected} · semilla" in page.text
    # Only an absent reference chooses the most recent published evaluation.
    if surface == "resultados":
        product = client.get(f"/resultados/sku/FIL-0001?ejecucion={wanted}")
        # There is no SKU detail in these queued runs, but its page keeps the named run.
        assert product.status_code == 404
        assert product.url.path == f"/corridas/{expected}/resultados/producto/FIL-0001"
        assert f"Corrida #{expected} · semilla" in product.text
        default = client.get("/resultados", follow_redirects=True)
        assert default.url.path == "/corridas/1/resultados"
        assert "Corrida #1 · semilla 11" in default.text


def test_without_runs_the_old_addresses_start_a_new_run() -> None:
    client = _fixture_client("vacio")
    for old, new in (
        ("/ejecucion", "/corridas/nueva/datos"),
        ("/resultados", "/corridas/nueva/resultados"),
    ):
        response = client.get(old, follow_redirects=False)
        assert (response.status_code, response.headers["location"]) == (307, new)


def test_the_other_routes_of_each_surface_stay_where_they_were() -> None:
    client = _fixture_client()
    assert client.get("/ejecucion/estado").status_code == 286
    assert client.get("/resultados/sku/FIL-0001?ejecucion=1").status_code == 200


# --------------------------------------------------------------------------- el inicio
def test_the_home_lists_each_run_where_it_stands_with_its_next_action() -> None:
    html = _fixture_client("errores").get("/").text
    text = _main(html)

    # Newest first; a run that works shows its stage, progress and time running.
    assert text.index("#2 · semilla 42") < text.index("#1 · semilla 42")
    assert "Ejecutando · Ajuste de modelos 57,1 % · lleva" in text
    assert text.count("Seguir la corrida") == 2
    assert '<a class="pred-btn pred-btn--primary" href="/corridas/1/ejecucion">' in html
    # The launch moment reads in Bogotá time, with the exact UTC instant on hover.
    assert "lanzada 2 oct 2026, 1:00 a. m." in text
    assert 'title="2026-10-02 06:00:00 UTC (hora universal coordinada)"' in html


def test_the_home_without_runs_offers_a_new_one(env: Env) -> None:
    html = _dal_client(env).get("/").text
    text = _main(html)
    assert "Todavía no hay corridas." in text
    assert "Grupo de la tesis" not in text
    # The top bar, the header and the empty state all start a new run.
    assert html.count('href="/corridas/nueva/datos"') == 3


def test_without_the_capabilities_to_launch_a_new_run_is_disabled_and_says_why() -> None:
    client = _fixture_client("vacio")  # no family comparison and no saved selection yet
    for path in ("/", "/corridas/nueva/datos"):
        html = client.get(path).text
        assert 'href="/corridas/nueva/datos"><svg' not in html, path
        topbar = r'<a class="pred-btn [^"]*pred-topbar__new" role="link" aria-disabled'
        assert re.search(topbar, html), path
    text = _main(client.get("/").text)
    reasons = text.split("Por qué no se puede empezar una corrida nueva", 1)[1]
    assert "Comparación común entre familias: todavía no está implementada (G2, G3)." in reasons
    assert "Resultados de selección guardados: todavía no está implementada (G4)." in reasons
    assert "Lectura del panel de datos" not in reasons


class RunsUnreadable(FixtureRepository):
    def list_runs(self) -> Any:
        raise ArtifactMissing("No se encontró la base de datos de la plataforma.")


def test_the_home_says_so_when_the_runs_cannot_be_read() -> None:
    response = _fixture_client(repo=RunsUnreadable("normal")).get("/")
    assert response.status_code == 200
    text = _main(response.text)
    assert "No se encontró la base de datos de la plataforma." in text
    assert "platform.artifact_missing" in text
    assert "Todas las corridas" in response.text


def test_the_home_groups_the_seeds_that_share_data_and_settings(env: Env) -> None:
    client = _dal_client(env)
    for seed in ("0", "1"):
        launched = client.post(
            "/ejecucion/lanzar",
            data={"ingest_id": env.ingest_id, **FORM, "seed": seed},
            follow_redirects=False,
        )
        assert launched.status_code == 303
    # Another setting is another question: it does not join the group.
    client.post(
        "/ejecucion/lanzar",
        data={"ingest_id": env.ingest_id, **FORM, "seed": "2", "horizon": "3"},
        follow_redirects=False,
    )

    html = client.get("/").text
    text = _main(html)

    # Every run is listed, newest first.
    order = [text.index(f"#{run} · semilla {run - 1}") for run in (3, 2, 1)]
    assert order == sorted(order)
    group = text.split("Grupo de la tesis", 1)[1].split("Todas las corridas", 1)[0]
    assert "Semilla 0 · #1 En cola" in group
    assert "Semilla 1 · #2 En cola" in group
    assert "Semilla 2 Sin lanzar" in group
    # The joint evidence of the seeds is one link away.
    assert '<a href="/resultados/grupo">Ver el grupo de semillas</a>' in html
    assert text.count("En cola") >= 3


# ---------------------------------------------------------------- una corrida lanzada
def test_a_launched_run_keeps_datos_ajustes_and_revisar_as_a_record() -> None:
    client = _fixture_client()
    for key, label in (("datos", "Datos"), ("ajustes", "Ajustes"), ("revisar", "Revisar y lanzar")):
        html = client.get(f"/corridas/1/{key}").text
        assert f"<title>{label} · Corrida #1 · PRED</title>" in html, key
        assert "Esta corrida ya se lanzó." in html, key
        assert "Corrida #1 · semilla 42" in html, key
        # Nothing can be changed: no upload, no settings form, no launch.
        assert 'type="file"' not in html, key
        assert 'action="/corridas/nueva/ajustes"' not in html, key
        assert 'action="/ejecucion/lanzar"' not in html, key
        assert _rail(html) == [
            ("done", "/corridas/1/datos"),
            ("done", "/corridas/1/ajustes"),
            ("done", "/corridas/1/revisar"),
            ("live", "/corridas/1/ejecucion"),
            ("todo", "/corridas/1/resultados"),
        ], key
    record = _main(client.get("/corridas/1/datos").text)
    assert "Archivo de esta corrida demo_canonico.csv" in record
    assert "2 familias · semilla 42" in client.get("/corridas/1/ajustes").text


def test_each_rail_step_reads_as_its_number_name_status_and_state() -> None:
    html = _fixture_client().get("/corridas/1/ejecucion").text
    rail = re.search(r'<nav class="pred-rail".*?</nav>', html, re.S)
    assert rail is not None
    # What a screen reader reads: the marks and the short names are hidden from it.
    shown = re.sub(r'<span [^>]*aria-hidden="true">.*?</span>', "", rail.group(0), flags=re.S)
    names = [_text(item) for item in re.findall(r'<a class="pred-rail__item".*?</a>', shown, re.S)]
    assert [name.split(" ", 2)[:2] for name in names] == [["Paso", str(n)] for n in range(1, 6)]
    assert names[3].startswith("Paso 4 Ejecución Aquí")
    assert names[3].endswith(": en curso")


def test_the_compact_step_date_keeps_the_exact_utc_time() -> None:
    html = _fixture_client().get("/corridas/1/revisar").text
    compact = re.search(r'<nav class="pred-rail-compact".*?</nav>', html, re.S)
    assert compact is not None
    dates = Markup(compact.group(0)).select("time")
    assert len(dates) == 1
    assert dates[0]["datetime"] == "2026-10-02T06:00:00+00:00"
    assert dates[0]["title"] == "2026-10-02 06:00:00 UTC (hora universal coordinada)"
    assert "Lanzada 2 oct 2026" in _text(compact.group(0))


@pytest.mark.parametrize("step", ["revisar", "ejecucion", "resultados"])
def test_a_locked_current_step_is_announced(step: str) -> None:
    html = _fixture_client().get(f"/corridas/nueva/{step}").text
    current = [
        attrs for attrs in Markup(html).select("span") if attrs.get("aria-current") == "step"
    ]
    assert len(current) == 1
    assert current[0]["class"] == "pred-rail__item"


def test_review_change_links_belong_to_their_descriptions(env: Env) -> None:
    client = _dal_client(env)
    client.post("/corridas/nueva/ajustes", data={"ingest_id": env.ingest_id, **FORM})
    html = client.get("/corridas/nueva/revisar").text
    page = Markup(html)
    assert page.definition_list_misuse() == []
    changes = re.findall(
        r'<dd[^>]*>.*?<a href="/corridas/nueva/(?:datos|ajustes)">Cambiar</a></dd>', html
    )
    assert len(changes) >= 4


def test_back_and_next_follow_the_steps_of_a_launched_run() -> None:
    client = _fixture_client()
    expected = {
        "datos": ("/", "/corridas/1/ajustes"),
        "ajustes": ("/corridas/1/datos", "/corridas/1/revisar"),
        "revisar": ("/corridas/1/ajustes", "/corridas/1/ejecucion"),
        "ejecucion": ("/corridas/1/revisar", "/corridas/1/resultados"),
        "resultados": ("/corridas/1/ejecucion", None),
    }
    for key, (back, nxt) in expected.items():
        footer = _footer(client.get(f"/corridas/1/{key}").text)
        links = re.findall(r'<a class="pred-btn pred-btn--(\w+) [^"]*" href="([^"]+)"', footer)
        assert links[0] == ("subtle", back), key
        assert (links[1] if len(links) > 1 else (None, None))[1] == nxt, key
    # While the final evaluation has not published, Resultados says so before it opens.
    footer = _footer(client.get("/corridas/1/ejecucion").text)
    assert "Parciales hasta que la evaluación final publique." in footer


def test_a_run_that_does_not_exist_is_a_404_on_every_step() -> None:
    client = _fixture_client()
    for key in ("datos", "ajustes", "revisar", "ejecucion", "resultados"):
        for corrida in ("99", "abc"):
            response = client.get(f"/corridas/{corrida}/{key}")
            assert response.status_code == 404, (corrida, key)
            assert f"No existe la corrida «{corrida}»." in response.text
            assert '<a class="pred-btn pred-btn--primary" href="/">Ir a Corridas</a>' in (
                response.text
            )


# ------------------------------------------------------------------- la corrida nueva
def test_a_new_run_opens_the_steps_only_as_they_can_be_reached() -> None:
    client = _fixture_client()
    html = client.get("/corridas/nueva/datos").text
    assert _rail(html) == [
        ("todo", "/corridas/nueva/datos"),
        ("todo", "/corridas/nueva/ajustes"),
        ("locked", None),
        ("locked", None),
        ("locked", None),
    ]
    # Without a chosen file, Siguiente is off and says why.
    footer = _footer(html)
    assert re.search(r'<button [^>]*disabled aria-describedby="siguiente-motivo"', footer)
    assert 'id="siguiente-motivo">Elija un archivo para continuar.' in footer

    for key, heading, way in (
        ("revisar", "Primero elija los datos", "/corridas/nueva/datos"),
        ("ejecucion", "Todavía no hay corrida", "/corridas/nueva/datos"),
        ("resultados", "Los resultados todavía no existen", "/corridas/nueva/datos"),
    ):
        response = client.get(f"/corridas/nueva/{key}")
        assert response.status_code == 200, key
        assert heading in response.text, key
        assert f'<a class="pred-btn pred-btn--primary" href="{way}">' in response.text, key


def test_choosing_settings_and_launching_walks_the_draft_through_the_steps(env: Env) -> None:
    client = _dal_client(env)

    # Datos: the chosen file is the step's Siguiente.
    datos = client.get(f"/corridas/nueva/datos?ingest_id={env.ingest_id}").text
    assert f'<input type="hidden" name="ingest_id" value="{env.ingest_id}">' in _footer(datos)
    chosen = client.post(
        "/corridas/nueva/datos", data={"ingest_id": env.ingest_id}, follow_redirects=False
    )
    assert (chosen.status_code, chosen.headers["location"]) == (303, "/corridas/nueva/ajustes")
    assert COOKIE in chosen.headers["set-cookie"] and "HttpOnly" in chosen.headers["set-cookie"]

    # Ajustes: the draft keeps the file; Revisar opens once a file is chosen.
    ajustes = client.get("/corridas/nueva/ajustes").text
    assert f'<option value="{env.ingest_id}" selected>' in ajustes
    assert [state for state, _ in _rail(ajustes)] == ["done", "todo", "todo", "locked", "locked"]
    assert 'form="formulario-ajustes"' in _footer(ajustes)
    # Back to Datos without choosing again: the step shows the file of the draft.
    assert "Datos válidos" in client.get("/corridas/nueva/datos").text

    saved = client.post(
        "/corridas/nueva/ajustes",
        data={"ingest_id": env.ingest_id, **FORM, "seed": "17"},
        follow_redirects=False,
    )
    assert (saved.status_code, saved.headers["location"]) == (303, "/corridas/nueva/revisar")

    # Revisar: what will run, each row with its way back, and nothing launched yet.
    revisar = client.get("/corridas/nueva/revisar").text
    text = _main(revisar)
    assert "Compruebe antes de lanzar" in text
    assert revisar.count('<a href="/corridas/nueva/ajustes">Cambiar</a>') >= 3
    assert '<a href="/corridas/nueva/datos">Cambiar</a>' in revisar
    assert '<input type="hidden" name="seed" value="17">' in revisar
    assert [state for state, _ in _rail(revisar)] == ["done", "done", "todo", "locked", "locked"]
    assert env.rows("SELECT COUNT(*) FROM ejecuciones") == [(0,)]
    # Ajustes shows what was saved.
    assert re.search(r'name="seed"[^>]*value="17"', client.get("/corridas/nueva/ajustes").text)

    launched = client.post(
        "/ejecucion/lanzar",
        data={"ingest_id": env.ingest_id, **FORM, "seed": "17"},
        follow_redirects=False,
    )
    assert (launched.status_code, launched.headers["location"]) == (
        303,
        "/corridas/1/ejecucion?aviso=en_cola",
    )
    # Launching ends the draft: a new run starts again from Datos.
    assert f'{COOKIE}=""' in launched.headers["set-cookie"]
    assert "Primero elija los datos" in client.get("/corridas/nueva/revisar").text
    page = client.get(launched.headers["location"]).text
    assert "<title>Ejecución · Corrida #1 · PRED</title>" in page
    assert "La ejecución quedó en cola" in page


def test_a_file_that_cannot_run_is_not_kept_in_the_draft(env: Env) -> None:
    client = _dal_client(env)
    for ingest_id in (_fixture_id("failed"), "0" * 64):
        response = client.post(
            "/corridas/nueva/datos", data={"ingest_id": ingest_id}, follow_redirects=False
        )
        assert response.headers["location"] == f"/corridas/nueva/datos?ingest_id={ingest_id}"
        assert "set-cookie" not in response.headers
    missing = client.post("/corridas/nueva/datos", follow_redirects=False)
    assert (missing.status_code, missing.headers["location"]) == (303, "/corridas/nueva/datos")


def test_settings_that_cannot_run_stay_in_ajustes_with_the_reason(env: Env) -> None:
    client = _dal_client(env)
    response = client.post(
        "/corridas/nueva/ajustes",
        data={"ingest_id": env.ingest_id, **FORM, "horizon": "0"},
        follow_redirects=False,
    )
    assert response.status_code == 200
    assert "set-cookie" not in response.headers
    assert "<title>Ajustes · Corrida nueva · PRED</title>" in response.text
    assert "is-error" in response.text


def _choose(client: TestClient, ingest_id: str) -> None:
    chosen = client.post("/corridas/nueva/datos", data={"ingest_id": ingest_id})
    assert chosen.url.path == "/corridas/nueva/ajustes"


def _draft_cookie(ingest_id: str) -> str:
    response = Response()
    save_draft(response, Draft(ingest_id=ingest_id))
    found = re.search(rf"{COOKIE}=([^;]+)", response.headers["set-cookie"])
    assert found is not None
    return found.group(1)


def test_leaving_ajustes_by_atras_keeps_what_was_typed(env: Env) -> None:
    client = _dal_client(env)
    _choose(client, env.ingest_id)
    ajustes = client.get("/corridas/nueva/ajustes").text
    # Atrás sends the form with its destination and without the browser's validation.
    back = re.search(r'<button[^>]*name="destino"[^>]*>', _footer(ajustes))
    assert back is not None
    assert 'form="formulario-ajustes"' in back.group(0) and 'value="datos"' in back.group(0)
    assert "formnovalidate" in back.group(0)

    left = client.post(
        "/corridas/nueva/ajustes",
        data={"ingest_id": env.ingest_id, **FORM, "seed": "149", "destino": "datos"},
        follow_redirects=False,
    )

    assert (left.status_code, left.headers["location"]) == (303, "/corridas/nueva/datos")
    assert COOKIE in left.headers["set-cookie"]
    assert re.search(r'name="seed"[^>]*value="149"', client.get("/corridas/nueva/ajustes").text)
    revisar = client.get("/corridas/nueva/revisar").text
    assert '<input type="hidden" name="seed" value="149">' in revisar
    assert '<input type="hidden" name="families" value="foundation">' in revisar
    assert env.rows("SELECT COUNT(*) FROM ejecuciones") == [(0,)]


def test_a_value_left_unfinished_is_kept_but_revisar_still_checks_it(env: Env) -> None:
    client = _dal_client(env)
    _choose(client, env.ingest_id)
    left = client.post(
        "/corridas/nueva/ajustes",
        data={"ingest_id": env.ingest_id, **FORM, "horizon": "0", "destino": "datos"},
        follow_redirects=False,
    )
    assert left.headers["location"] == "/corridas/nueva/datos"
    assert re.search(r'name="horizon"[^>]*value="0"', client.get("/corridas/nueva/ajustes").text)
    # Ajustes is not done until «Continuar» validates it, and Revisar does not launch it.
    ajustes = client.get("/corridas/nueva/ajustes").text
    assert [state for state, _ in _rail(ajustes)][:2] == ["done", "todo"]
    revisar = client.get("/corridas/nueva/revisar").text
    assert "Estos ajustes no se pueden lanzar" in revisar
    # The rail's Revisar link checks like «Continuar»: an error stays in Ajustes, unsaved.
    to_revisar = client.post(
        "/corridas/nueva/ajustes",
        data={"ingest_id": env.ingest_id, **FORM, "horizon": "0", "destino": "revisar"},
        follow_redirects=False,
    )
    assert to_revisar.status_code == 200 and "is-error" in to_revisar.text
    assert "set-cookie" not in to_revisar.headers


_AJUSTES_FORM = "formulario-ajustes"
_CONTINUAR = {"form": _AJUSTES_FORM, "class": "pred-btn pred-btn--primary pred-btn--large"}


def test_enter_in_an_ajustes_field_continues_like_continuar(env: Env) -> None:
    client = _dal_client(env)
    _choose(client, env.ingest_id)
    page = client.get("/corridas/nueva/ajustes").text

    # Enter in a field submits through the form's default button: Continuar, never Atrás.
    enter = formulario_html.submit(page, _AJUSTES_FORM, typed={"horizon": "5"})
    assert "destino" not in enter
    forward = client.post("/corridas/nueva/ajustes", data=enter, follow_redirects=False)
    assert (forward.status_code, forward.headers["location"]) == (303, "/corridas/nueva/revisar")
    assert (
        '<input type="hidden" name="horizon" value="5">'
        in client.get(forward.headers["location"]).text
    )

    # An invalid value and Enter: Ajustes says what is wrong and saves nothing.
    page = client.get("/corridas/nueva/ajustes").text
    invalid = formulario_html.submit(page, _AJUSTES_FORM, typed={"horizon": "0"})
    stays = client.post("/corridas/nueva/ajustes", data=invalid, follow_redirects=False)
    assert stays.status_code == 200 and "set-cookie" not in stays.headers
    assert "<title>Ajustes · Corrida nueva · PRED</title>" in stays.text
    assert "Horizonte (días): debe ser mayor que cero." in _text(stays.text)


def test_after_a_rail_exit_and_browser_back_continuar_goes_to_revisar(env: Env) -> None:
    client = _dal_client(env)
    _choose(client, env.ingest_id)
    page = client.get("/corridas/nueva/ajustes").text
    typed = {"horizon": "4"}

    # A rail step: the script sends the form with that step in a temporary hidden field.
    rail = formulario_html.submit(page, _AJUSTES_FORM, typed=typed)
    rail["destino"] = ["datos"]
    left = client.post("/corridas/nueva/ajustes", data=rail, follow_redirects=False)
    assert left.headers["location"] == "/corridas/nueva/datos"

    # Browser Back shows the same Ajustes page with what was typed; the destination of the
    # earlier exit travelled only with that submit, so Continuar and Enter go forward.
    for button in (_CONTINUAR, None):
        data = formulario_html.submit(page, _AJUSTES_FORM, typed=typed, button=button)
        assert "destino" not in data
        forward = client.post("/corridas/nueva/ajustes", data=data, follow_redirects=False)
        assert forward.headers["location"] == "/corridas/nueva/revisar"
    revisar = client.get("/corridas/nueva/revisar").text
    assert '<input type="hidden" name="horizon" value="4">' in revisar

    # Atrás is still the way back, with what was typed.
    back = formulario_html.submit(
        page, _AJUSTES_FORM, typed={"seed": "7"}, button={"value": "datos"}
    )
    assert back["destino"] == ["datos"]
    assert client.post("/corridas/nueva/ajustes", data=back).url.path == "/corridas/nueva/datos"
    assert re.search(r'name="seed"[^>]*value="7"', client.get("/corridas/nueva/ajustes").text)


def test_the_rail_script_leaves_no_destination_in_the_page() -> None:
    """The rail sends its destination in a field the script removes right after ``submit()``
    (which reads the form at once); a field left in the form would ride along with Continuar
    and Enter after browser Back."""
    script = (Path(__file__).parent.parent / "src/pred_platform/app/static/js/pred.js").read_text()
    handler = script.split('closest(".pred-rail a', 1)[1].split("});", 1)[0]
    assert "formulario.submit();\n    destino.remove();" in handler
    assert handler.count("appendChild(") == 1


def test_a_link_that_chooses_a_file_puts_it_in_the_draft(env: Env) -> None:
    client = _dal_client(env)
    client.cookies.set(COOKIE, _draft_cookie(SOME_INGEST))

    ajustes = client.get(f"/corridas/nueva/ajustes?ingest_id={env.ingest_id}")

    assert COOKIE in ajustes.headers["set-cookie"]
    assert f'<option value="{env.ingest_id}" selected>' in ajustes.text
    # Datos, the rail and Revisar read the file the page shows, not the earlier draft.
    assert [state for state, _ in _rail(ajustes.text)][0] == "done"
    revisar = client.get("/corridas/nueva/revisar").text
    assert f'<input type="hidden" name="ingest_id" value="{env.ingest_id}">' in revisar
    assert SOME_INGEST not in revisar


def test_a_link_with_a_file_that_cannot_run_leaves_the_draft_alone(env: Env) -> None:
    client = _dal_client(env)
    _choose(client, env.ingest_id)
    for ingest_id in (SOME_INGEST, _fixture_id("failed")):
        response = client.get(f"/corridas/nueva/ajustes?ingest_id={ingest_id}")
        assert "set-cookie" not in response.headers
    revisar = client.get("/corridas/nueva/revisar").text
    assert f'<input type="hidden" name="ingest_id" value="{env.ingest_id}">' in revisar


def test_an_imported_configuration_is_kept_in_the_draft(env: Env) -> None:
    client = _dal_client(env)
    document = {
        "formato": FORMAT,
        "schema_version": FORMAT_VERSION,
        "run_id": "r-0123456789abcdef",
        "identidad": "r-0123456789abcdef",
        "ingesta_sha256": env.ingest_id,
        "parametros": {
            "min_train": 40,
            "horizon": 2,
            "step": 14,
            "seasonality": 7,
            "metric": "mae",
            "families": ["classical", "ml"],
            "trials": 1,
            "seed": 149,
            "workers": 2,
        },
        "versiones": {"plataforma": "0.1.0"},
    }
    imported = client.post(
        "/ejecucion/configuracion",
        files={"archivo": ("c.json", json.dumps(document).encode(), "application/json")},
    )
    assert imported.status_code == 200 and COOKIE in imported.headers["set-cookie"]

    # Leaving Ajustes and coming back keeps the import; Revisar lists it.
    client.get("/corridas/nueva/datos")
    assert re.search(r'name="seed"[^>]*value="149"', client.get("/corridas/nueva/ajustes").text)
    revisar = client.get("/corridas/nueva/revisar").text
    assert '<input type="hidden" name="seed" value="149">' in revisar
    assert f'<input type="hidden" name="ingest_id" value="{env.ingest_id}">' in revisar
    assert env.rows("SELECT COUNT(*) FROM ejecuciones") == [(0,)]


@pytest.mark.parametrize("cookie", ["no-es-base64", "eyJpbmdlc3RfaWQiOiAxfQ", "W10"])
def test_an_unreadable_draft_is_an_empty_one(cookie: str) -> None:
    client = _fixture_client()
    client.cookies.set(COOKIE, cookie)
    response = client.get("/corridas/nueva/revisar")
    assert response.status_code == 200
    assert "Primero elija los datos" in response.text


def test_copiar_de_with_fixture_source_warns_it_cannot_copy() -> None:
    client = _fixture_client()
    response = client.get("/corridas/nueva/ajustes?copiar_de=1")
    assert response.status_code == 200
    html = response.text
    assert "pred-alert--warning" in html
    assert "No se pudieron copiar los ajustes de la corrida #1" in html
    assert "la plataforma funciona con datos de prueba" in html


def test_copiar_de_with_nonexistent_run_warns_it_does_not_exist(env: Env) -> None:
    client = _dal_client(env)
    response = client.get("/corridas/nueva/ajustes?copiar_de=999")
    assert response.status_code == 200
    html = response.text
    assert "pred-alert--warning" in html
    assert "No se pudieron copiar los ajustes de la corrida #999" in html
    assert "no existe esa corrida" in html


def test_copiar_de_with_corrupt_config_warns_with_reason(env: Env) -> None:
    client = _dal_client(env)
    client.post(
        "/ejecucion/lanzar",
        data={"ingest_id": env.ingest_id, **FORM, "seed": "23"},
        follow_redirects=False,
    )
    with sqlite3.connect(env.db) as conn:
        conn.execute("UPDATE configuraciones SET parametros = 'not-valid-json' WHERE id = 1")
        conn.commit()
    response = client.get("/corridas/nueva/ajustes?copiar_de=1")
    assert response.status_code == 200
    html = response.text
    assert "pred-alert--warning" in html
    assert "No se pudieron copiar los ajustes de la corrida #1" in html
    assert "Los ajustes guardados de la ejecución no son JSON" in html


def test_copiar_de_valid_run_populates_ajustes_and_informs_user(env: Env) -> None:
    client = _dal_client(env)
    launched = client.post(
        "/ejecucion/lanzar",
        data={"ingest_id": env.ingest_id, **FORM, "seed": "47", "metric": "rmse"},
        follow_redirects=False,
    )
    assert launched.status_code == 303
    response = client.get("/corridas/nueva/ajustes?copiar_de=1")
    assert response.status_code == 200
    html = response.text
    assert "pred-alert--info" in html
    assert "Ajustes copiados de la corrida #1. Cambie la semilla y continúe." in html
    assert re.search(r'name="seed"[^>]*value="47"', html)
    assert '<option value="rmse" selected>' in html
    revisar = client.get("/corridas/nueva/revisar").text
    assert '<input type="hidden" name="seed" value="47">' in revisar


# ------------------------------------------------- lo que el recorrido promete, de punta a punta
def _launch(client: TestClient, env: Env, *seeds: str) -> None:
    for seed in seeds:
        launched = client.post(
            "/ejecucion/lanzar",
            data={"ingest_id": env.ingest_id, **FORM, "seed": seed},
            follow_redirects=False,
        )
        assert launched.status_code == 303


def _set_run(env: Env, run: int, estado: str, started: str | None = "2026-10-02T06:00:00Z") -> None:
    with sqlite3.connect(env.db) as conn:
        conn.execute(
            "UPDATE ejecuciones SET estado = ?, iniciada_en = ? WHERE id = ?",
            (estado, started, run),
        )


def _rail_subs(html: str) -> list[str]:
    rail = re.search(r'<nav class="pred-rail".*?</nav>', html, re.S)
    assert rail is not None
    return [
        _text(sub)
        for sub in re.findall(
            r'<span class="pred-rail__sub">.*?</span></span>', rail.group(0), re.S
        )
    ]


def _ingest_of_the_fixture() -> str:
    (first, *_) = FixtureRepository().list_ingests().items
    assert first.ingest_id is not None
    return first.ingest_id


@pytest.mark.parametrize(
    ("old", "run", "tab", "kept"),
    [
        (
            "/ejecucion?ejecucion=1&estado=fallida&page=2&tareas=todas",
            "1",
            None,
            r'<option value="fallida" selected>',
        ),
        (
            "/resultados?ejecucion=1&vista=sku&lista=familias&q=FIL&page=2",
            "1",
            "sku",
            r'name="q"[^>]*value="FIL"',
        ),
        ("/resultados?vista=resumen&m3=m3-a", "1", "resumen", None),
        ("/resultados?ejecucion=latest&vista=clase", "1", "clase", None),
    ],
)
def test_an_old_address_shows_its_run_tab_and_filters(
    old: str, run: str, tab: str | None, kept: str | None
) -> None:
    page = _fixture_client().get(old, follow_redirects=True)

    assert page.status_code == 200
    assert f"Corrida #{run} · semilla" in page.text
    if tab is not None:
        current = [
            attrs["href"] or ""
            for attrs in Markup(page.text).select("a")
            if attrs.get("aria-current") == "page"
            and f"/corridas/{run}/resultados?" in (attrs["href"] or "")
        ]
        assert [f"vista={tab}" in href for href in current] == [True]
    if kept is not None:
        assert re.search(kept, page.text)


def test_the_old_datos_address_keeps_the_chosen_file_and_filter() -> None:
    ingest = _ingest_of_the_fixture()
    page = _fixture_client().get(
        f"/datos?ingest_id={ingest}&sku_class=smooth", follow_redirects=True
    )
    assert page.url.path == "/corridas/nueva/datos"
    assert re.search(r'<option value="smooth" selected>', page.text)
    assert ingest in page.text


def test_the_rail_says_what_the_saved_draft_holds(env: Env) -> None:
    client = _dal_client(env)
    _choose(client, env.ingest_id)
    before = _rail_subs(client.get("/corridas/nueva/ajustes").text)
    assert before[0].startswith("demo_canonico.csv")
    assert before[1] == "4 familias · semilla 0"  # the defaults

    client.post(
        "/corridas/nueva/ajustes",
        data={"ingest_id": env.ingest_id, **FORM, "seed": "17", "families": ["classical", "ml"]},
    )

    for path in ("/corridas/nueva/ajustes", "/corridas/nueva/revisar", "/corridas/nueva/datos"):
        subs = _rail_subs(client.get(path).text)
        assert subs[0].startswith("demo_canonico.csv"), path
        assert subs[1] == "2 familias · semilla 17", path


def test_the_rail_follows_the_run_while_ejecucion_polls(env: Env) -> None:
    client = _dal_client(env)
    _launch(client, env, "0")
    page = client.get("/corridas/1/ejecucion")
    assert 'id="barra-pasos"' in page.text
    assert _rail_subs(page.text)[3] == "En cola"

    # The run starts: the poll answers the new rail and the narrow summary beside the body.
    _set_run(env, 1, "ejecutando")
    poll = client.get("/ejecucion/estado?ejecucion=1")
    assert poll.status_code == 200
    assert 'hx-swap-oob="true"' in poll.text
    assert _rail_subs(poll.text)[3].startswith("Trabajando")
    assert re.search(r'<nav class="pred-rail-compact" id="resumen-paso"[^>]*hx-swap-oob', poll.text)

    # The run ends: the last answer stops the polling and closes the step in the rail.
    _set_run(env, 1, "completada")
    done = client.get("/ejecucion/estado?ejecucion=1")
    assert done.status_code == 286
    assert _rail_subs(done.text)[3] == "Terminó"


def test_the_home_says_a_queued_run_has_not_started(env: Env) -> None:
    client = _dal_client(env)
    _launch(client, env, "0")

    home = _main(client.get("/").text)
    assert "en cola, todavía sin empezar" in home
    assert "lanzada —" not in home
    assert _rail_subs(client.get("/corridas/1/ejecucion").text)[2] == "Lanzada · en cola"
    # Once it starts, the date reads as before.
    _set_run(env, 1, "ejecutando")
    assert "lanzada 2 oct 2026" in _main(client.get("/").text)
    assert _rail_subs(client.get("/corridas/1/ejecucion").text)[2].startswith("Lanzada 2 oct 2026")


def test_the_home_lists_the_run_in_progress_before_the_queued_ones(env: Env) -> None:
    client = _dal_client(env)
    _launch(client, env, "0", "1", "2")
    _set_run(env, 2, "ejecutando")

    text = _main(client.get("/").text)

    order = [text.index(f"#{run} · semilla {run - 1}") for run in (2, 3, 1)]
    assert order == sorted(order)


def test_the_home_says_when_a_published_run_had_failed_tasks(env: Env) -> None:
    client = _dal_client(env)
    _launch(client, env, "0", "1")
    ((engine_id,),) = env.rows("SELECT run_id FROM ejecuciones WHERE id = 1")
    publish(
        env.db, ingesta_sha256=env.ingest_id, run_id_m2=engine_id,
        verdicts={"FIL-0001": ("smooth", "VALIDADO")},
    )  # fmt: skip
    for number in (1, 2):
        _set_run(env, number, "completada_con_fallos")
    with sqlite3.connect(env.db) as conn:
        failure = json.dumps({"code": "platform.candidate_failed", "stage": "L3"})
        for model, estado, error in (
            ("classical:a", "fallida", failure),
            ("ml:b", "exitosa", None),
        ):
            conn.execute(
                "INSERT INTO tareas (ejecucion_id, sku, modelo, corte, seed, estado, detalle_error)"
                " VALUES (1, 'FIL-0001', ?, '2026-03-13', 0, ?, ?)",
                (model, estado, error),
            )

    html = client.get("/").text
    published = html.split("#1 · semilla 0", 1)[1].split("#2", 1)[0]
    text = _text(published)

    assert "Completada con fallos · resultados listos" in text
    assert "Falló 1 de 2 tareas de modelos. Aun así, el producto tiene veredicto." in text
    assert "Completada · resultados listos" not in text
    assert "pred-status--warn" in published
    # The run whose evaluation is not published keeps its own wording.
    assert "Completada · sin evaluación final publicada" in _text(html.split("#2", 1)[1])


@pytest.mark.parametrize("step", ["datos", "ajustes", "revisar"])
def test_the_record_steps_of_a_launched_run_do_not_promise_a_summary(step: str) -> None:
    html = _fixture_client().get(f"/corridas/1/{step}").text
    assert "nada empieza hasta que lo confirme" not in html
    assert "Verá un resumen antes de empezar" not in html
    assert "El avance está en Ejecución y los resultados, en Resultados." in html


def test_the_scenario_errores_has_the_selections_of_its_current_run() -> None:
    client = _fixture_client("errores")
    page = client.get("/resultados", follow_redirects=True)
    assert page.url.path == "/corridas/2/resultados"
    sku = client.get("/corridas/2/resultados?vista=sku")
    assert sku.status_code == 200
    assert "No hay selecciones para la ejecución" not in sku.text
    assert "FIL-0001" in sku.text
    assert client.get("/corridas/1/resultados?vista=sku").status_code == 200
