"""Superficie /resultados: pestañas y detalle de un SKU (C2), lo que se ve y lo que se delega."""

from __future__ import annotations

import json
import re
import sqlite3
from contextlib import closing
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from pred_platform.app.main import create_app
from pred_platform.app.presentation import VERDICT_ORDER, describe_status
from pred_platform.app.seleccion import (
    RunAction,
    SelectionQuery,
    compact_config,
    error_title,
    exclusion_text,
    parse_selection_query,
    run_action,
)
from pred_platform.config import Settings
from pred_platform.contract import models
from pred_platform.contract.errors import ContractInvalid, InvalidQuery
from pred_platform.contract.resources import read_example
from pred_platform.data.fixtures import FixtureRepository
from tests.m3_publicada import LATE
from tests.test_seleccion import _run
from tests.test_ui_review import Markup
from tests.test_worker import Env
from tests.test_worker import env as env  # noqa: F401  (the fixture)

# What the reader sees for the two kinds of exclusion before the final evaluation; the cause the
# engine published stays apart, as technical detail.
LATE_LAUNCH = (
    "Empezó a venderse después de la fecha de corte: no tiene historia para entrenar y comparar."
)
TWO_FAMILIES_REASON = (
    "El SKU quedó fuera de la comparación porque conservó menos de dos familias evaluables."
)


class RecordingRepository(FixtureRepository):
    """Envuelve el fixture para afirmar qué se delegó al repositorio."""

    def __init__(self, scenario: str = "normal") -> None:
        super().__init__(scenario)
        self.list_calls: list[tuple[str, dict[str, Any]]] = []
        self.detail_calls: list[tuple[str, str, bool]] = []

    def list_sku_selections(self, run_id: str, **kwargs: Any) -> models.SkuSelectionList:
        self.list_calls.append((run_id, kwargs))
        return super().list_sku_selections(run_id, **kwargs)

    def get_sku_selection(
        self, run_id: str, sku_id: str, *, include_windows: bool = False
    ) -> models.SkuSelection:
        self.detail_calls.append((run_id, sku_id, include_windows))
        return super().get_sku_selection(run_id, sku_id, include_windows=include_windows)


def _client(scenario: str = "normal", repo: Any = None) -> tuple[TestClient, Any]:
    app = create_app(
        Settings(
            data_root=Path("unused"),
            db_path=Path("unused/pred.db"),
            data_source="fixture",
            fixture_scenario=scenario,
        )
    )
    repo = repo or RecordingRepository(scenario)
    app.state.repository = repo
    return TestClient(app), repo


def _text(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))


def _reading(html: str) -> str:
    """The page text as the reader sees it: inline markup such as a glossed term adds no space."""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", html))


_INTERVAL_NOTE = (
    "El intervalo de r reúne los valores compatibles con los datos de este producto según la "
    "prueba HLN-DM, sin el ajuste BH. La significancia de arriba sí aplica ese ajuste, así que "
    "las dos lecturas pueden no coincidir."
)


def _body(html: str) -> str:
    """The step body, without the step header that says what the step is for."""
    return re.sub(r'<header class="pred-stephead">.*?</header>', "", html, count=1, flags=re.S)


def _notice(html: str) -> str:
    """The text of the warning alert, as the reader sees it (inline markup adds no space)."""
    found = re.search(r'pred-alert--warning".*?<div>(.*?)</div>', html, re.S)
    assert found is not None
    return re.sub(r"<[^>]+>", "", found.group(1)).strip()


# --------------------------------------------------------------------------- la tabla
def test_without_m3_evidence_the_page_opens_on_the_family_list_one_column_per_family() -> None:
    client, _ = _client()
    response = client.get("/resultados")
    html = response.text

    assert response.status_code == 200
    assert 'aria-current="page">SKU</a>' in html
    assert 'aria-current="page">Resultados por familia</a>' in html
    for sku in ("FIL-0001", "FIL-0002", "FIL-0003", "FIL-0004"):
        assert f'href="/resultados/sku/{sku}?' in html
    for header in (
        "SKU",
        "Clase",
        "Campeón y veredicto",
        "Clásica",
        "Aprendizaje automático",
        "Aprendizaje profundo",
        "Fundacional",
    ):
        assert f'<th scope="col">{header}</th>' in html
    # The fixture's v1 run "1" has no M3 evaluation: no champion is invented from the legacy field.
    assert _text(html).count("Sin veredicto") == 4
    assert "Esta ejecución todavía no tiene evaluación final." in _text(html)
    assert 'aria-label="Sin dato"' in html


def test_filters_sort_and_paging_are_delegated_not_applied_in_the_view() -> None:
    client, repo = _client()
    client.get(
        "/resultados?sku_class=lumpy&family=classical&estado=exitosa&q=FIL&sort=valor"
        "&descending=1&page=2&size=1&ejecucion=1"
    )

    assert repo.list_calls[-1] == (
        "1",
        {
            "sku_class": "lumpy",
            "family": "classical",
            "estado": "exitosa",
            "q": "FIL",
            "sort": "valor",
            "descending": True,
            "page": 2,
            "size": 1,
        },
    )


def test_the_view_shows_what_the_repository_returned_even_if_it_ignores_the_filters() -> None:
    class Stubborn(RecordingRepository):
        def list_sku_selections(self, run_id: str, **kwargs: Any) -> models.SkuSelectionList:
            doc = super().list_sku_selections(run_id)  # unfiltered, on purpose
            return doc.model_copy(update={"items": doc.items[:1]})

    client, _ = _client(repo=Stubborn("normal"))
    html = client.get("/resultados?sku_class=lumpy").text

    assert "FIL-0001" in html and "FIL-0004" not in html  # no second filtering


def test_the_pager_keeps_the_filters_and_the_run() -> None:
    client, _ = _client()
    html = client.get("/resultados?q=FIL&size=1&ejecucion=1&sort=valor").text

    assert "Siguiente" in html
    link = re.search(r'href="(/resultados\?[^"]*page=2[^"]*)"', html)
    assert link is not None
    for part in ("vista=sku", "ejecucion=1", "q=FIL", "size=1", "sort=valor"):
        assert part in link.group(1)


def test_an_invalid_filter_is_a_warning_not_an_error_page() -> None:
    client, _ = _client()
    for url in ("/resultados?sort=price", "/resultados?page=abc", "/resultados?estado=completada"):
        response = client.get(url)
        assert response.status_code == 200
        assert "pred-alert--warning" in response.text
        assert "Traceback" not in response.text


def test_without_any_run_the_page_says_so() -> None:
    client, repo = _client("vacio")
    response = client.get("/resultados")
    html = response.text

    assert response.url.path == "/corridas/nueva/resultados"
    assert "Los resultados todavía no existen" in html
    # Nothing is chosen yet, so the way forward is the first step.
    first_step = '<a class="pred-btn pred-btn--primary" href="/corridas/nueva/datos">Ir a Datos</a>'
    assert first_step in html
    assert "<table" not in html
    assert repo.list_calls == []


# ------------------------------------------------------------------------- la acción
def _launch_action(html: str) -> str:
    """The «Nueva corrida con estos datos» link of the step header: secondary, because
    Revisar y lanzar owns launching."""
    found = re.search(
        r'<a class="pred-btn pred-btn--secondary"[^>]*>\s*<svg[^>]*><use href="#i-play">', html
    )
    assert found is not None
    return found.group(0)


def test_the_action_is_enabled_when_all_three_capabilities_are_available() -> None:
    client, _ = _client("normal")
    html = client.get("/resultados").text

    action = _launch_action(html)
    # It opens Ajustes of a new run with the ingest of the run shown.
    assert re.search(r'href="/corridas/nueva/ajustes\?ingest_id=[0-9a-f]+"', action)
    assert "Nueva corrida con estos datos" in html
    assert "aria-disabled" not in action
    assert "Por qué no se puede lanzar" not in html


class WithEmptyCapabilities(RecordingRepository):
    """A run under the capabilities of the empty scenario: parquet_read yes; comparison and
    persisted results no."""

    def get_capabilities(self) -> models.Capabilities:
        return FixtureRepository("vacio").get_capabilities()


def test_the_disabled_action_says_why() -> None:
    client, _ = _client(repo=WithEmptyCapabilities("normal"))
    html = client.get("/resultados").text

    action = _launch_action(html)
    assert 'aria-disabled="true"' in action and "href=" not in action.split(">", 1)[0]
    reasons = _text(html).split("Por qué no se puede lanzar", 1)[1]
    assert "Comparación común entre familias: todavía no está implementada (G2, G3)" in reasons
    assert "Resultados de selección guardados: todavía no está implementada (G4)" in reasons
    assert "Lectura del panel de datos" not in reasons


def test_run_action_lists_only_what_is_missing() -> None:
    def capabilities(**flags: bool) -> models.Capabilities:
        base = json.loads(read_example("capabilities.todo_disponible"))
        for item in base["items"]:
            if item["id"] in flags and not flags[item["id"]]:
                item.update(available=False, reason_code="stage_not_implemented", blocked_by=[])
        return models.Capabilities.model_validate(base)

    assert run_action(capabilities()) == RunAction(True, ())
    action = run_action(capabilities(parquet_read=False))
    assert not action.enabled
    assert action.missing == ("Lectura del panel de datos: la etapa aún no existe.",)


# --------------------------------------------------------------------- las otras pestañas
class WithoutChampion(RecordingRepository):
    """A run with its M2 selection, under capabilities whose champion stage does not exist."""

    def get_capabilities(self) -> models.Capabilities:
        return models.Capabilities.model_validate_json(read_example("capabilities.actual"))

    def list_m3_runs(self, **kwargs: Any) -> Any:
        raise AssertionError("no M3 evidence is read without the champion stage")


@pytest.mark.parametrize("vista", ["resumen", "clase", "errores"])
def test_summary_tabs_are_honestly_unavailable_until_the_champion_exists(vista: str) -> None:
    client, repo = _client(repo=WithoutChampion("normal"))
    html = client.get(f"/resultados?vista={vista}").text

    assert "Todavía no hay campeón por SKU." in html
    assert "stage_not_implemented" in html
    assert repo.list_calls == []  # nothing is read, nothing is invented
    # Only the SKU tab is offered, and it still opens the M2 selection.
    assert ">Resumen</a>" not in html and ">SKU</a>" in html


# ------------------------------------------------------------------------- el detalle
def test_the_detail_shows_four_families_with_evidence_trials_and_a_pending_champion() -> None:
    client, repo = _client("normal")
    # FIL-0001 appears in two examples; the scenario serves the lumpy one for FIL-0004.
    response = client.get("/resultados/sku/FIL-0001")
    text = _text(response.text)

    assert response.status_code == 200
    assert repo.detail_calls[-1] in (("latest", "FIL-0001", False), ("1", "FIL-0001", False))
    for family in ("Clásica", "Aprendizaje automático", "Aprendizaje profundo", "Fundacional"):
        assert family in text
    assert "Orden (p, d, q) (1, 2, 2)" in text
    assert "Hiperparámetros" in text
    assert "No optimiza: no hay estudio ni ensayos." in text
    assert "ADI · días entre ventas 1,000" in text
    assert "3 ensayos: 1 completados, 2 podados, 0 fallidos." in text


def test_pruned_and_failed_trials_look_different_and_keep_their_reason() -> None:
    base = json.loads(read_example("sku_selection.smooth_cuatro_familias"))
    classical = base["families"][0]
    classical["trials"] = [
        {
            "id": "t-0",
            "configuracion": {"p": 1},
            "estado": "completado",
            "valor": 1.5,
            "n_ventanas": 4,
            "timestamp": "2026-10-14T16:19:02+00:00",
        },
        {
            "id": "t-1",
            "configuracion": {"p": 2},
            "estado": "podado",
            "valor": 2.5,
            "n_ventanas": 1,
            "motivo": "poda_semantica:prediccion_nula",
            "timestamp": "2026-10-14T16:19:03+00:00",
        },
        {
            "id": "t-2",
            "configuracion": {"p": 3},
            "estado": "fallido",
            "valor": None,
            "n_ventanas": 1,
            "motivo": "ventana=1 ajuste_fallido: AjusteModeloError: no converge",
            "timestamp": "2026-10-14T16:19:04+00:00",
        },
    ]

    class OneDoc(RecordingRepository):
        def get_sku_selection(self, run_id: str, sku_id: str, **kwargs: Any) -> models.SkuSelection:
            return models.SkuSelection.model_validate(base)

    client, _ = _client(repo=OneDoc("normal"))
    html = client.get("/resultados/sku/FIL-0001").text

    pruned = re.search(
        r'<span class="pred-status pred-status--pruned"[^>]*>.*?</span>\s*</span>', html, re.S
    )
    failed = re.search(
        r'<span class="pred-status pred-status--err"[^>]*>.*?</span>\s*</span>', html, re.S
    )
    assert pruned is not None and "Podada" in pruned.group(0) and "#i-prune" in pruned.group(0)
    assert failed is not None and "Fallida" in failed.group(0) and "#i-x" in failed.group(0)
    # Each reason reads as a short sentence; the engine's own text stays one hover away.
    text = _text(html)
    assert "Detenido: el pronóstico salió nulo" in text
    assert "El modelo no pudo ajustarse (ventana 1)" in text
    assert 'title="poda_semantica:prediccion_nula"' in html
    assert 'title="ventana=1 ajuste_fallido: AjusteModeloError: no converge"' in html
    assert "Un ensayo podado no es un fallo" in text


def test_a_failed_family_shows_its_error_and_an_unrunnable_one_its_state() -> None:
    client, _ = _client("errores")
    text = _text(client.get("/resultados/sku/FIL-0001").text)

    assert "Fallida" in text and "No ejecutable" in text
    assert "Etapa" in text and re.search(r"[a-z]+\.[a-z_]+", text)
    assert "Traceback" not in text


def test_the_policy_exclusions_are_explained_with_their_reason() -> None:
    client, _ = _client("normal")
    text = _text(client.get("/resultados/sku/FIL-0004").text)

    assert "Excluida" in text
    # The engine's own sentence explains a policy exclusion; no second sentence is stacked on it.
    assert "La politica 2.2.0-initial no asigna ml a la clase lumpy." in text
    assert "La política de selección no asigna" not in text


def test_windows_are_only_requested_and_shown_on_demand() -> None:
    client, repo = _client("normal")
    plain = client.get("/resultados/sku/FIL-0001?ejecucion=1").text
    full = client.get("/resultados/sku/FIL-0001?ejecucion=1&ventanas=1").text

    assert repo.detail_calls[-2:] == [("1", "FIL-0001", False), ("1", "FIL-0001", True)]
    assert "Entrenamiento" not in plain and "Ver las 9 ventanas" in plain
    assert "Entrenamiento" in full and "Ocultar las ventanas" in full
    assert '<th scope="col">mae</th>' in full and '<th scope="col">rmse</th>' in full


def test_the_detail_goes_back_to_the_same_list() -> None:
    client, _ = _client()
    listing = client.get("/resultados?sku_class=lumpy&sort=valor").text
    href = re.search(r'href="(/resultados/sku/FIL-0004\?[^"]*)"', listing)
    assert href is not None

    detail = client.get(href.group(1).replace("&amp;", "&")).text
    crumbs = re.search(r'<nav class="pred-breadcrumb".*?</nav>', detail, re.S)
    assert crumbs is not None
    back = re.search(r'href="([^"]*)">SKU</a>', crumbs.group(0))
    assert back is not None
    assert "sku_class=lumpy" in back.group(1) and "sort=valor" in back.group(1)
    assert 'aria-current="page"><span class="mono">FIL-0004</span>' in crumbs.group(0)


def test_an_unknown_sku_is_a_clear_404_not_a_traceback() -> None:
    client, _ = _client()
    response = client.get("/resultados/sku/NO-EXISTE")

    assert response.status_code == 404
    assert "No encontramos la selección de este SKU." in response.text
    assert "Traceback" not in response.text


def test_no_value_is_computed_or_filtered_in_the_browser() -> None:
    client, _ = _client()
    for url in ("/resultados", "/resultados/sku/FIL-0001?ventanas=1"):
        html = client.get(url).text
        assert "<script" not in html.replace('<script src="/static', "")  # only the shell's files
        assert "onclick" not in html and "oninput" not in html


# ------------------------------------------------------------------------ con el DAL real
def _dal_client(env: Env) -> TestClient:
    app = create_app(
        Settings(data_root=env.settings.data_root, db_path=env.db, data_source="dal", worker="off")
    )
    app.state.repository = env.repo()
    return TestClient(app)


def test_a_missing_run_is_named_and_offers_the_runs_with_results(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    empty = _dal_client(env).get("/resultados?ejecucion=99")
    assert empty.url.path == "/corridas/99/resultados" and empty.status_code == 404
    assert "No existe la corrida «99»." in _text(empty.text)
    assert '<a class="pred-btn pred-btn--primary" href="/">Ir a Corridas</a>' in empty.text

    _run(env, monkeypatch)
    _publish_for_the_run(env)
    client = _dal_client(env)
    html = client.get("/resultados?vista=clase&ejecucion=99").text
    # Corridas, where it points, lists the run with results and opens them.
    assert "No existe la corrida «99»." in _text(html)
    assert 'href="/corridas/1/resultados"' in client.get("/").text


def test_the_real_dal_feeds_the_table_and_the_detail(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run(env, monkeypatch, "unit_fails")
    client = _dal_client(env)

    page = client.get("/resultados")
    text = _text(page.text)
    assert page.status_code == 200
    for sku in ("FIL-0001", "FIL-0002", "FIL-0003"):
        assert sku in text
    # A run whose model tasks partly failed still finished: the state says so, and the line
    # beside it says what failed.
    assert "Ejecución #1" in text and "Completada" in text and "Completada con fallos" not in text
    assert re.search(r"(Falló|Fallaron) \d+ de \d+ tareas de modelos\.", text)
    assert "La acción está deshabilitada" not in text  # the DAL has all three capabilities

    detail = client.get("/resultados/sku/FIL-0002?ventanas=1")
    detail_text = _text(detail.text)
    assert detail.status_code == 200
    assert "El modelo no pudo ajustarse." in detail_text  # model.fit_failed, from the failed ml
    assert "Podada" in detail_text and "Fallida" in detail_text
    assert "ajuste_fallido" in detail.text  # the engine's reason, kept in the title
    assert "Entrenamiento" in detail_text  # windows of the classical family that did succeed
    assert 'title="Perfil de demanda: ' in detail.text and "disperso y estable" in detail.text


def test_the_real_dal_answers_for_a_run_that_has_not_finished(env: Env) -> None:
    from tests.test_worker import SMALL, enqueue_run

    enqueue_run(env.settings, env.ingest_id, SMALL)
    text = _text(_dal_client(env).get("/resultados").text)

    assert "Todavía no hay resultados por familia." in text
    assert "cuando termine la ejecución" in text
    assert "Ejecución #1" in text and "Pendiente" in text


# --------------------------------------------------------------------------- las ayudas
def test_the_query_defaults_to_the_latest_run_and_round_trips_through_the_url() -> None:
    assert parse_selection_query({}).ejecucion == "latest"
    query = parse_selection_query(
        {"ejecucion": "3", "sku_class": "lumpy", "descending": "1", "sort": "valor", "page": "2"}
    )
    assert query == SelectionQuery(
        ejecucion="3", sku_class="lumpy", sort="valor", descending=True, page=2
    )
    again = parse_selection_query(
        dict(
            pair.split("=")
            for pair in query.query_string().split("&")
            if not pair.startswith("vista")
        )
    )
    assert again == query
    assert "ejecucion" not in SelectionQuery().query_string()  # latest is implicit


def test_a_non_numeric_page_is_an_invalid_query() -> None:
    with pytest.raises(InvalidQuery):
        parse_selection_query({"page": "dos"})


def test_exclusion_texts_are_platform_sentences_without_engine_codes() -> None:
    # The engine detail (codes, figures) is shown apart, as technical detail; the sentence is ours.
    assert exclusion_text("series_too_short", "dl", "smooth") == (
        "La historia de este producto es demasiado corta para esta familia."
    )
    assert exclusion_text("not_configured", "ml", None) == (
        "La ejecución se lanzó sin la familia Aprendizaje automático."
    )
    assert exclusion_text("not_in_policy_matrix", "dl", "lumpy") == (
        "La política de selección no usa la familia Aprendizaje profundo para productos de "
        "demanda grumosa."
    )
    # An unknown code never reaches the screen as a code.
    assert exclusion_text("future_reason", "ml", "smooth") == (
        "Esta familia no compitió en este producto."
    )
    assert error_title("model.fit_failed") == "El modelo no pudo ajustarse."
    assert error_title("some.new_code") == "some.new_code"
    assert (
        compact_config({"lr": 0.012512806369135352, "n": 3, "ok": True}) == "lr=0,01251, n=3, ok=sí"
    )
    assert compact_config(None) == "—" and compact_config({}) == "—"


# ------------------------------------------------------------ la decisión publicada por M3
def _publish_for_the_run(env: Env, **options: Any) -> str:
    """Publish an M3 evaluation of the platform's run 1, with FIL-0003 launched after the cut."""
    from tests.m3_publicada import publish

    [(run_id_m2,)] = env.rows("SELECT run_id FROM ejecuciones WHERE id = 1")
    options.setdefault("late", ("FIL-0003",))
    options.setdefault(
        "verdicts",
        {"FIL-0001": ("smooth", "VALIDADO"), "FIL-0002": ("intermittent", "EXPLORATORIO")},
    )
    publish(env.db, ingesta_sha256=env.ingest_id, run_id_m2=run_id_m2, **options)
    return run_id_m2


@pytest.mark.parametrize(
    ("bounds", "shown", "exact"),
    [
        (
            (0.6193202626195218, 0.8990134844073785, None),
            "[0,619; 0,899]",
            "[0,6193202626195218; 0,8990134844073785]",
        ),
        ((0.0, None, None), "[0,000; +∞)", "[0,0; +∞)"),
        ((0.2, 0.8, 1.4), "[0,200; 0,800] o [1,400; +∞)", "[0,2; 0,8] o [1,4; +∞)"),
        (
            (0.0, 1.23441, 1.23449),
            "[0,000; 1,2344] o [1,2345; +∞)",
            "[0,0; 1,23441] o [1,23449; +∞)",
        ),
    ],
)
def test_sku_shows_the_published_interval_beside_r(
    env: Env,
    monkeypatch: pytest.MonkeyPatch,
    bounds: tuple[float, float | None, float | None],
    shown: str,
    exact: str,
) -> None:
    _run(env, monkeypatch)
    _publish_for_the_run(env)
    with closing(sqlite3.connect(env.db)) as conn:
        conn.execute(
            "INSERT INTO m3_intervalos_r (run_id, sku, nivel, inferior, superior, inferior_cola) "
            "VALUES ('m3-real-1', 'FIL-0001', 0.95, ?, ?, ?)",
            bounds,
        )
        conn.commit()
    html = _dal_client(env).get("/resultados/sku/FIL-0001?m3=m3-real-1").text
    assert Markup(html).definition_list_misuse() == []
    kpis = html.split('id="numeros-decisivos"', 1)[1].split("</dl>", 1)[0]
    assert "r frente a la línea base" in _text(kpis) and "0,800" in kpis
    assert re.search(
        r'frente a la línea base</dt>\s*<dd>\s*<span title="0,8">\s*0,800\s*</span>\s*<span',
        kpis,
    )
    assert f"Intervalo de r {shown}" in _text(kpis)
    assert f'title="{exact}"' in kpis
    assert _INTERVAL_NOTE in _reading(html)
    other = _dal_client(env).get("/resultados/sku/FIL-0002?m3=m3-real-1").text
    assert "Intervalo de r no publicado." in other
    assert shown not in other


def test_sku_distinguishes_the_unadjusted_interval_from_bh_significance(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run(env, monkeypatch)
    _publish_for_the_run(env)
    with closing(sqlite3.connect(env.db)) as conn:
        conn.execute(
            "UPDATE m3_veredictos SET dm_p_valor = 0.048225135846995754, "
            "dm_p_ajustado = 0.059122274889045062, dm_alfa = 0.05, dm_significativa = 0 "
            "WHERE run_id = 'm3-real-1' AND sku = 'FIL-0001'"
        )
        conn.execute(
            "INSERT INTO m3_intervalos_r (run_id, sku, nivel, inferior, superior) "
            "VALUES ('m3-real-1', 'FIL-0001', 0.95, 0.6216964707141519, 0.995087542992538)"
        )
        conn.commit()
    response = _dal_client(env).get("/resultados/sku/FIL-0001?m3=m3-real-1")
    assert response.status_code == 200
    text = _text(response.text)
    assert "Intervalo de r [0,622; 0,995]" in text
    assert "Significancia No significativa" in text
    assert "p-valor 0,048" in text and "p ajustado (BH) 0,059" in text
    assert _INTERVAL_NOTE in _reading(response.text)


def test_invalid_interval_does_not_hide_the_sku_decision(
    env: Env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _run(env, monkeypatch)
    _publish_for_the_run(env)
    with closing(sqlite3.connect(env.db)) as conn:
        conn.execute("PRAGMA ignore_check_constraints = ON")
        conn.execute(
            "INSERT INTO m3_intervalos_r (run_id, sku, nivel, inferior, superior) "
            "VALUES ('m3-real-1', 'FIL-0001', 0.95, 0.9, 0.1)"
        )
        conn.commit()
    response = _dal_client(env).get("/resultados/sku/FIL-0001?m3=m3-real-1")
    assert response.status_code == 200
    assert "No fue posible leer el intervalo de r." in response.text
    assert "Intervalo de r no publicado." not in response.text
    assert "VALIDADO" in response.text and "0,800" in response.text


def test_database_error_reading_intervals_does_not_fail_the_sku_page(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run(env, monkeypatch)
    _publish_for_the_run(env)
    with closing(sqlite3.connect(env.db)) as conn:
        conn.execute("DROP TABLE m3_intervalos_r")
        conn.execute("CREATE TABLE m3_intervalos_r (run_id TEXT, sku TEXT)")
        conn.commit()
    response = _dal_client(env).get("/resultados/sku/FIL-0001?m3=m3-real-1")
    assert response.status_code == 200
    assert "No fue posible leer el intervalo de r." in response.text
    assert "Intervalo de r no publicado." not in response.text
    assert "VALIDADO" in response.text and "0,800" in response.text


def test_non_finite_bound_in_intervals_is_rejected(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run(env, monkeypatch)
    _publish_for_the_run(env)
    with closing(sqlite3.connect(env.db)) as conn:
        conn.execute("PRAGMA ignore_check_constraints = ON")
        conn.execute(
            "INSERT INTO m3_intervalos_r (run_id, sku, nivel, inferior, superior) "
            "VALUES ('m3-real-1', 'FIL-0001', 0.95, 0.5, 9e999)"
        )
        conn.commit()
    response = _dal_client(env).get("/resultados/sku/FIL-0001?m3=m3-real-1")
    assert response.status_code == 200
    assert "No fue posible leer el intervalo de r." in response.text
    assert "Intervalo de r no publicado." not in response.text
    assert "VALIDADO" in response.text and "0,800" in response.text


def test_sku_interval_pins_the_m3_run(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.m3_publicada import publish

    _run(env, monkeypatch)
    run_id_m2 = _publish_for_the_run(env)
    publish(
        env.db,
        ingesta_sha256=env.ingest_id,
        run_id_m2=run_id_m2,
        verdicts={"FIL-0001": ("smooth", "VALIDADO")},
        run_id="m3-real-2",
    )
    with closing(sqlite3.connect(env.db)) as conn:
        conn.execute(
            "INSERT INTO m3_intervalos_r (run_id, sku, nivel, inferior, superior) "
            "VALUES ('m3-real-1', 'FIL-0001', 0.95, 0.2, 0.8)"
        )
        conn.commit()
    client = _dal_client(env)
    response_b = client.get("/resultados/sku/FIL-0001?m3=m3-real-2")
    assert response_b.status_code == 200
    assert "Intervalo de r no publicado." in response_b.text
    assert "[0,200; 0,800]" not in response_b.text

    response_a = client.get("/resultados/sku/FIL-0001?m3=m3-real-1")
    assert response_a.status_code == 200
    assert "Intervalo de r [0,200; 0,800]" in _text(response_a.text)


def test_sku_page_with_absent_intervals_table_shows_not_published(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run(env, monkeypatch)
    _publish_for_the_run(env)
    with closing(sqlite3.connect(env.db)) as conn:
        conn.execute("DROP TABLE m3_intervalos_r")
        conn.commit()
    response = _dal_client(env).get("/resultados/sku/FIL-0001?m3=m3-real-1")
    assert response.status_code == 200
    assert "Intervalo de r no publicado." in response.text
    assert "No fue posible leer el intervalo de r." not in response.text
    assert "VALIDADO" in response.text and "0,800" in response.text


def test_the_table_shows_the_champion_and_the_five_verdict_labels_m3_published(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run(env, monkeypatch)
    _publish_for_the_run(env)
    client = _dal_client(env)

    html = client.get("/resultados?vista=sku&lista=familias").text
    text = _text(html)
    assert "Evaluación final Publicada ·" in text
    # The champion cell names the family and the model; SKU and family are not repeated.
    assert "FIL-0001 Suave Clásica · SARIMA Validado" in text
    assert "FIL-0002 Intermitente Clásica · SARIMA Exploratorio" in text
    assert f"Excluido antes de evaluar: {LATE_LAUNCH}" in text
    assert f'title="Causa publicada: {LATE}"' in html
    assert "Origen de los datos Reales (declarado)" in text
    body = _text(_body(html))
    assert "se sostiene" not in body and "Sin veredicto" not in body
    # Detail links, filters and pages keep the shown run and the resolved M3 run, not «the newest».
    assert "/resultados/sku/FIL-0001?ejecucion=&amp;m3=m3-real-1" not in html
    assert 'href="/resultados/sku/FIL-0001?ejecucion=1&amp;m3=m3-real-1&amp;volver=' in html
    assert '<input type="hidden" name="m3" value="m3-real-1">' in html


def test_the_detail_shows_the_verdict_its_numbers_and_significance_apart(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run(env, monkeypatch)
    _publish_for_the_run(env)
    client = _dal_client(env)

    page = client.get("/resultados/sku/FIL-0001?m3=m3-real-1")
    text = _text(page.text)
    assert page.status_code == 200
    assert "Decisión de la evaluación final Clásica" in text
    # The verdict reads by its label and its one-line meaning; the candidate id stays a tooltip.
    assert (
        "Validado Se equivoca menos que la línea base, con datos reales y evidencia suficiente. "
        "Evaluado · modelo SARIMA"
    ) in text
    assert 'title="Candidato FIL-0001/classical/sarima"' in page.text
    assert (
        "Se equivocó menos que la línea base (r = 0,800) en 40 ventanas de prueba, con datos "
        "reales."
    ) in text
    assert "Ventanas de prueba válidas 40 mínimo 30" in text
    assert "r frente a la línea base 0,800" in text
    assert "p ajustado (BH) 0,080" in text and "Significativa no" in text
    assert "«Validado» no equivale a significativo" in text
    # The two yes/no facts sit in the row of decisive numbers, and the test figures in a row of
    # facts, instead of narrow lists that left the right half of the sheet empty.
    numbers = _text(page.text.split('id="numeros-decisivos"', 1)[1].split("</dl>", 1)[0])
    assert "Pierde frente a la línea base no Comparación incompleta no" in numbers
    test = page.text.split('id="significancia"', 1)[1].split("</dl>", 1)[0]
    assert '<div><dt>p ajustado (BH)</dt><dd class="mono">0,080</dd></div>' in test
    assert "Identificador de la evaluación final m3-real-1" in text and env.ingest_id in text
    assert "ventanas=1" in page.text and "m3=m3-real-1&amp;ventanas=1" in page.text

    late = _text(client.get("/resultados/sku/FIL-0003").text)
    assert "Excluido antes de evaluar" in late and LATE_LAUNCH in late
    assert "Fecha de corte: 13 mar 2026" in late
    assert LATE in late  # the engine's cause, under «Detalle técnico»


@pytest.mark.parametrize(
    "url",
    ["/resultados", "/resultados/sku/FIL-0001", "/resultados/sku/FIL-0001?m3=m3-real-1"],
)
def test_naming_the_m3_run_of_a_page_never_counts_its_units(
    env: Env, monkeypatch: pytest.MonkeyPatch, url: str
) -> None:
    # A real run has millions of units: counting them cost every page over a second.
    _run(env, monkeypatch)
    _publish_for_the_run(env)
    client = _dal_client(env)
    statements: list[str] = []
    connect = sqlite3.connect

    def traced(*args: Any, **kwargs: Any) -> sqlite3.Connection:
        conn = connect(*args, **kwargs)
        conn.set_trace_callback(statements.append)
        return conn

    monkeypatch.setattr(sqlite3, "connect", traced)

    response = client.get(url)

    assert response.status_code == 200 and "m3-real-1" in response.text
    assert any("m3_corridas" in statement for statement in statements)
    assert not [statement for statement in statements if "m3_unidades" in statement]


def test_a_product_m2_excluded_from_the_competition_shows_its_published_cause(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.m3_publicada import ONE_ROUTED_CAUSE

    _run(env, monkeypatch)
    # Only the classical family is routed to FIL-0003: the two-family rule excludes it.
    _publish_for_the_run(
        env,
        late=("FIL-0002",),
        eligibility=(("FIL-0003", "lumpy", ONE_ROUTED_CAUSE),),
        verdicts={"FIL-0001": ("smooth", "VALIDADO")},
    )
    client = _dal_client(env)

    html = client.get("/resultados?vista=sku&lista=familias").text
    text = _text(html)
    assert f"FIL-0003 Grumosa Excluido antes de evaluar: {TWO_FAMILIES_REASON}" in text
    assert f'title="Causa publicada: {ONE_ROUTED_CAUSE}"' in html
    assert f"Excluido antes de evaluar: {LATE_LAUNCH}" in text
    assert "Sin veredicto" not in text

    detail = _text(client.get("/resultados/sku/FIL-0003").text)
    assert "Excluido antes de evaluar" in detail and TWO_FAMILIES_REASON in detail
    assert ONE_ROUTED_CAUSE in detail  # the engine's cause, under «Detalle técnico»
    assert "No es un veredicto ni un error técnico." in detail
    # The cut t* explains a late launch, not an exclusion decided in M2.
    assert "Fecha de corte" not in detail
    assert "Fecha de corte: 13 mar 2026" in _text(client.get("/resultados/sku/FIL-0002").text)


def test_a_test_that_did_not_run_shows_its_cause_not_a_p_value(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run(env, monkeypatch)
    _publish_for_the_run(env, origin="sintetico", verdicts={"FIL-0001": ("smooth", "EXPLORATORIO")})
    text = _text(_dal_client(env).get("/resultados/sku/FIL-0001").text)
    assert "Prueba HLN-DM sin prueba: datos sintéticos" in text
    assert "p ajustado" not in text
    assert "Origen de los datos Sintéticos o aumentados (declarado)" in text


@pytest.mark.parametrize("vista", ["resumen", "sku"])
def test_a_finished_run_says_what_it_left_and_reads_the_funnel_once(
    env: Env, monkeypatch: pytest.MonkeyPatch, vista: str
) -> None:
    _run(env, monkeypatch, "unit_fails")
    _publish_for_the_run(
        env,
        verdicts={
            "FIL-0001": ("smooth", "VALIDADO"),
            "FIL-0002": ("intermittent", "FALLO_TECNICO"),
        },
    )
    repo = env.repo()
    sizes: list[int | None] = []
    original = repo.get_m3_evidence

    def spy(run_id: str, **kwargs: Any) -> Any:
        sizes.append(kwargs.get("size"))
        return original(run_id, **kwargs)

    monkeypatch.setattr(repo, "get_m3_evidence", spy)
    client = _dal_client(env)
    client.app.state.repository = repo  # type: ignore[attr-defined]
    html = client.get(f"/resultados?vista={vista}").text

    outcome = re.search(r'<p class="pred-context__outcome"[^>]*>(.*?)</p>', html, re.S)
    assert outcome is not None
    # Plain words: what failed, what still got a verdict, and what a technical failure means.
    assert re.fullmatch(
        r"(Falló|Fallaron) \d+ de \d+ tareas de modelos\. Aun así, 2 de 3 productos tienen "
        r"veredicto; el otro se excluyó antes de evaluar, con su causa\. 1 tiene el veredicto "
        r"«Error técnico»: ningún modelo lo pronosticó\.",
        outcome.group(1).strip(),
    )
    # The context bar reuses the funnel the tab already read: no extra one-row evidence read.
    assert 1 not in sizes


def test_an_exploratory_verdict_says_in_one_sentence_why_it_is_not_validated(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run(env, monkeypatch)
    _publish_for_the_run(env, verdicts={"FIL-0001": ("smooth", "EXPLORATORIO")})  # r = 1,1
    html = _dal_client(env).get("/resultados/sku/FIL-0001").text
    reason = re.search(r'<p class="pred-decision__why" id="justificacion">(.*?)</p>', html)

    assert reason is not None
    assert reason.group(1) == "No se equivocó menos que la línea base (r = 1,100)."


def _ranking(text: str) -> str:
    """The evaluated-models table of a SKU detail, as the reader sees it."""
    assert "Modelos evaluados en la reserva" in text
    return text.split("Modelos evaluados en la reserva", 1)[1].split("Comparación por familia")[0]


def test_the_detail_ranks_the_evaluated_models_by_r_with_the_baseline(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run(env, monkeypatch)
    _publish_for_the_run(env, extra_models={"FIL-0001": [("ml", "lightgbm", 0.9)]})
    text = _text(_dal_client(env).get("/resultados/sku/FIL-0001").text)

    assert re.search(
        r"Clásica SARIMA 0,800 .*Aprendizaje automático LightGBM 0,900 "
        r".*Línea base estacional Ingenuo estacional 1,000",
        _ranking(text),
    )


def test_the_ranking_marks_the_published_champion_even_if_another_model_has_lower_r(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run(env, monkeypatch)
    _publish_for_the_run(env, extra_models={"FIL-0001": [("ml", "lightgbm", 0.7)]})
    ranking = _ranking(_text(_dal_client(env).get("/resultados/sku/FIL-0001").text))

    assert re.search(
        r"Aprendizaje automático LightGBM 0,700 .*Clásica SARIMA 0,800 Campeón "
        r".*Línea base estacional Ingenuo estacional 1,000 Línea base",
        ranking,
    )
    assert ranking.count("Campeón") == 1


def test_a_model_without_r_goes_last_as_not_computable(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run(env, monkeypatch)
    _publish_for_the_run(env, extra_models={"FIL-0001": [("dl", "mlp", None)]})
    ranking = _ranking(_text(_dal_client(env).get("/resultados/sku/FIL-0001").text))

    assert (
        "Línea base estacional Ingenuo estacional 1,000 Línea base Aprendizaje profundo MLP no "
        "calculable"
    ) in ranking
    assert "MLP 0,000" not in ranking


def test_several_publications_of_one_identity_are_announced_and_can_be_chosen(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run(env, monkeypatch)
    _publish_for_the_run(env, run_id="m3-real-0", creado_en="2026-10-26 10:00:00")
    _publish_for_the_run(env, verdicts={"FIL-0001": ("smooth", "EXPLORATORIO")})
    client = _dal_client(env)

    html = client.get("/resultados").text
    # Each publication reads by its date; its id stays in the title, for audit.
    assert _notice(html) == (
        "Hay varias evaluaciones publicadas para los mismos datos y ajustes. Se muestra la "
        "publicada el 27 oct 2026, 5:00 a. m."
    )
    assert 'title="Evaluación m3-real-1 · 2026-10-27 10:00:00 UTC' in html
    other = re.search(
        r'<a href="/resultados\?vista=resumen&amp;ejecucion=1&amp;m3=m3-real-0" '
        r'title="Evaluación m3-real-0">([^<]*)</a>',
        html,
    )
    assert other is not None and other.group(1) == "la publicada el 26 oct 2026, 5:00 a. m."
    older = client.get("/resultados?vista=sku&m3=m3-real-0").text
    assert _notice(older).endswith("Se muestra la publicada el 26 oct 2026, 5:00 a. m.")
    # The period of «a. m.» closes the sentence; no sentence ends in «m..».
    families = client.get("/resultados?vista=sku&lista=familias&m3=m3-real-0").text
    assert "publicada el 26 oct 2026, 5:00 a. m. Ver distribución de campeones" in _text(families)
    assert re.search(r">la publicada el 27 oct 2026, 5:00 a\. m\.</a>\s*</p>", older)
    assert all("m.." not in re.sub(r"<[^>]+>", "", page) for page in (html, older, families))
    assert "FIL-0001 Suave Clásica SARIMA Validado 0,800" in _text(older)


def test_an_m3_run_of_another_execution_is_refused_and_an_unpublished_one_explained(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run(env, monkeypatch)
    _publish_for_the_run(env, estado="EVALUADA")
    client = _dal_client(env)

    text = _text(client.get("/resultados").text)
    assert "Esta ejecución todavía no tiene una evaluación publicada." in text
    assert "La evaluación más reciente está evaluada, sin publicar." in text
    assert "Sin veredicto" in text

    other = client.get("/resultados?m3=m3-ajena").text
    assert "La evaluación pedida no corresponde a esta ejecución." in _text(other)
    assert 'pred-status__label">Validado' not in other


def test_a_publication_without_its_exclusion_metadata_shows_why_there_are_no_verdicts(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run(env, monkeypatch)
    _publish_for_the_run(env, publish_m4=False)
    html = _dal_client(env).get("/resultados").text
    text = _text(html)
    assert "La evaluación publicada no trae la lista de productos excluidos." in text
    assert "no significa cero exclusiones" in text
    assert 'pred-status__label">Validado' not in html


# ------------------------------------------------------------------ el resumen de la corrida
_SUMMARY_VERDICTS = {
    "FIL-0001": ("smooth", "VALIDADO"),
    "FIL-0002": ("intermittent", "EXPLORATORIO"),
    "FIL-0004": ("lumpy", "EVIDENCIA_INSUFICIENTE"),
}


def _summary(env: Env) -> str:
    """The run summary as the reader sees it, from the funnel on."""
    text = _text(_dal_client(env).get("/resultados?vista=resumen").text)
    assert "Embudo de la ejecución" in text
    return text.split("Embudo de la ejecución", 1)[1]


def _funnel(env: Env) -> str:
    """The funnel rows alone, from the products of the run to the last verdict."""
    return _summary(env).split("Evaluados: Validado y Exploratorio.", 1)[0]


def test_the_summary_shows_the_funnel_m3_published(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run(env, monkeypatch)
    _publish_for_the_run(env, verdicts=_SUMMARY_VERDICTS)  # FIL-0003 is launched after the cut
    funnel = _funnel(env)
    page = _dal_client(env).get("/resultados?vista=resumen").text
    assert Markup(page).definition_list_misuse() == []

    # Each row subtracts from or splits the one above it.
    assert (
        "Productos de la ejecución 4 Empezaron a venderse después de la fecha de corte − 1 "
        "Excluidos antes de evaluar − 0 Con veredicto 3 Evaluados 2"
    ) in funnel
    # Each verdict reads by its label and its one-line meaning, then its count.
    rows = funnel.split("Con veredicto 3 Evaluados 2", 1)[1]
    for code, count in zip(VERDICT_ORDER, (1, 1, 0, 0, 1), strict=True):
        shown = describe_status("verdict", code)
        assert f"{shown.label} {shown.hint} {count}" in rows
    assert rows.index("Exploratorio") < rows.index("No evaluados 1") < rows.index("Error técnico")


def test_the_summary_counts_the_products_m2_excluded_with_fewer_than_two_families(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.m3_publicada import TWO_FAMILIES_CAUSE

    _run(env, monkeypatch)
    _publish_for_the_run(  # FIL-0003 is launched after the cut
        env,
        verdicts={"FIL-0001": ("smooth", "VALIDADO")},
        eligibility=(("FIL-0004", "lumpy", TWO_FAMILIES_CAUSE),),
    )
    funnel = _funnel(env)

    # 3 products = 1 launched after the cut + 1 excluded by M2 + 1 with a verdict.
    assert (
        "Productos de la ejecución 3 Empezaron a venderse después de la fecha de corte − 1 "
        "Excluidos antes de evaluar − 1 "
        "El SKU quedó fuera de la comparación porque conservó menos de dos familias evaluables. 1 "
        "Con veredicto 1"
    ) in funnel


def test_the_summary_splits_the_m2_exclusions_by_their_published_code(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.m3_publicada import TWO_FAMILIES_CAUSE

    _run(env, monkeypatch)
    short = "historia admisible insuficiente: 40 dias, se requieren 85"
    _publish_for_the_run(
        env,
        late=(),
        verdicts={"FIL-0001": ("smooth", "VALIDADO")},
        eligibility=(
            ("FIL-0002", "intermittent", short, "eligibility.insufficient_history"),
            ("FIL-0003", "lumpy", short, "eligibility.insufficient_history"),
            ("FIL-0004", "lumpy", TWO_FAMILIES_CAUSE),
        ),
    )
    funnel = _funnel(env)

    # One label for every eligibility exclusion, and one row per code under it, the most
    # frequent first; the code itself stays in the row's title.
    assert (
        "Excluidos antes de evaluar − 3 "
        "La historia es demasiado corta para entrenar y evaluar. 2 "
        "El SKU quedó fuera de la comparación porque conservó menos de dos familias evaluables. 1 "
        "Con veredicto 1"
    ) in funnel
    html = _dal_client(env).get("/resultados?vista=resumen").text
    assert '<th scope="row" title="Código eligibility.insufficient_history">' in html
    assert "menos de dos familias evaluables (M2)" not in funnel


def test_a_run_published_before_m3_listed_m2_exclusions_says_they_are_not_reported(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run(env, monkeypatch)
    _publish_for_the_run(env, verdicts=_SUMMARY_VERDICTS, eligibility=None)
    funnel = _funnel(env)

    assert "Excluidos antes de evaluar No informado Con veredicto 3" in funnel


def test_the_summary_counts_the_dm_test_over_the_run_and_its_causes(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run(env, monkeypatch)
    _publish_for_the_run(env, verdicts=_SUMMARY_VERDICTS, significant=("FIL-0001",))
    summary = _summary(env)

    assert "Prueba Diebold-Mariano" in summary
    dm = summary.split("Prueba Diebold-Mariano", 1)[1]
    for expected in (
        "Con prueba 2",
        "Significativas tras el ajuste BH 1",
        "No significativas 1",
        "Sin prueba 1",
        "El veredicto es «Evidencia insuficiente», que no lleva prueba 1",
    ):
        assert expected in dm


def test_the_dm_counts_cover_every_page_of_verdicts(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run(env, monkeypatch)
    many = {f"SKU-{i:04d}": ("smooth", "VALIDADO") for i in range(501)}
    _publish_for_the_run(env, verdicts=many)
    dm = _summary(env).split("Prueba Diebold-Mariano", 1)[1]

    assert "Con prueba 501" in dm
    assert "No significativas 501" in dm


def test_the_summary_without_a_published_m3_run_says_why_not_zero(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run(env, monkeypatch)  # M2 finished; M3 published nothing
    text = _text(_dal_client(env).get("/resultados?vista=resumen").text)

    assert "Esta ejecución todavía no tiene evaluación final." in text
    assert "Embudo de la ejecución" not in text
    assert "Prueba Diebold-Mariano" not in text


def test_the_summary_of_an_unavailable_publication_shows_its_cause_not_zeros(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run(env, monkeypatch)
    _publish_for_the_run(env, publish_m4=False)
    text = _text(_dal_client(env).get("/resultados?vista=resumen").text)

    assert "La evaluación publicada no trae la lista de productos excluidos." in text
    assert "no significa cero exclusiones" in text
    assert "Prueba Diebold-Mariano" not in text


# ---------------------------------------------------- errores de lectura en las pestañas M3
def test_a_stored_verdict_that_breaks_the_contract_shows_its_error_in_the_summary(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sqlite3

    _run(env, monkeypatch)
    _publish_for_the_run(env, verdicts=_SUMMARY_VERDICTS)
    with sqlite3.connect(env.db) as conn:  # SQLite accepts it; the contract does not
        conn.execute("UPDATE m3_veredictos SET justificacion = ''")
    client = TestClient(_dal_client(env).app, raise_server_exceptions=False)

    response = client.get("/resultados?vista=resumen")

    assert response.status_code == 200
    text = _text(response.text)
    assert "No fue posible leer la evaluación." in text and "justificacion" in text
    assert "Prueba Diebold-Mariano" not in text


class _FailingRead:
    """The real DAL, except that one read raises the contract error a broken row gives."""

    def __init__(self, repository: Any, operation: str, from_page: int = 1) -> None:
        self._repository, self._operation, self._from_page = repository, operation, from_page

    def __getattr__(self, name: str) -> Any:
        read = getattr(self._repository, name)
        if name != self._operation:
            return read

        def failing(*args: Any, **kwargs: Any) -> Any:
            if kwargs.get("page", 1) >= self._from_page:
                raise ContractInvalid(f"{name}: fila inválida")
            return read(*args, **kwargs)

        return failing


@pytest.mark.parametrize(
    ("vista", "operation", "from_page"),
    [
        ("resumen", "list_m3_runs", 1),
        ("resumen", "get_m3_evidence", 2),
        ("clase", "list_m3_runs", 1),
        ("clase", "get_m3_categories", 1),
        ("errores", "get_m3_run", 1),
        ("errores", "get_m3_portfolio", 1),
    ],
)
def test_a_read_error_in_an_m3_tab_is_a_visible_problem_not_a_server_error(
    env: Env, monkeypatch: pytest.MonkeyPatch, vista: str, operation: str, from_page: int
) -> None:
    _run(env, monkeypatch)
    many = {f"SKU-{i:04d}": ("smooth", "VALIDADO") for i in range(501)}  # two evidence pages
    _publish_for_the_run(env, verdicts=many)
    client = _dal_client(env)
    client.app.state.repository = _FailingRead(env.repo(), operation, from_page)  # type: ignore[attr-defined]

    response = TestClient(client.app, raise_server_exceptions=False).get(
        f"/resultados?vista={vista}"
    )

    assert response.status_code == 200
    text = _text(response.text)
    assert "No fue posible leer la evaluación." in text and "fila inválida" in text


# ------------------------------------------------------------------- los perfiles de error
_R_PROFILES = [
    ("smooth", "classical", "razon_sn", 3, 4, 3, 1, 0.82, 0.8, 0.7, 0.9),
    ("smooth", "seasonal_naive", "razon_sn", 3, 3, 3, 0, 1.0, 1.0, 1.0, 1.0),
    ("intermittent", "classical", "razon_sn", 1, 1, 0, 1, None, None, None, None),
]


def _profiles(env: Env, query: str = "") -> str:
    """The error-profiles tab as the reader sees it, from its heading on."""
    text = _text(_dal_client(env).get(f"/resultados?vista=errores{query}").text)
    assert "Perfiles de error por clase y familia" in text
    return text.split("Perfiles de error por clase y familia", 1)[1]


def test_the_error_profiles_show_r_by_class_and_family_as_published(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run(env, monkeypatch)
    _publish_for_the_run(env, extra_portfolio=_R_PROFILES)
    profiles = _profiles(env)

    assert "Suave Clásica 0,820 0,800 0,700 0,900 3 4 1" in profiles
    assert "Suave Línea base estacional 1,000 1,000 1,000 1,000 3 3 0" in profiles
    assert "Intermitente Clásica — — — — 1 1 1" in profiles
    assert "1,500 1,500 1,200 1,800" not in profiles  # the RMSE rows


def test_choosing_another_metric_shows_its_profile_and_keeps_the_run(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run(env, monkeypatch)
    _publish_for_the_run(env, extra_portfolio=_R_PROFILES)
    html = _dal_client(env).get("/resultados?vista=errores&metrica=rmse").text
    profiles = _text(html).split("Perfiles de error por clase y familia", 1)[1]

    assert "Suave Clásica 1,500 1,500 1,200 1,800 2 2 0" in profiles
    assert "0,820" not in profiles  # the r rows
    [mae] = re.findall(r'href="([^"]*metrica=mae[^"]*)"', html)
    assert "vista=errores" in mae and "m3=m3-real-1" in mae


def test_the_error_profiles_without_a_published_m3_run_say_why_not_zero(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run(env, monkeypatch)  # M2 finished; M3 published nothing
    text = _text(_dal_client(env).get("/resultados?vista=errores").text)

    assert "Esta ejecución todavía no tiene evaluación final." in text
    assert "Perfiles de error por clase y familia" not in text


def test_the_error_profiles_of_a_rejected_m3_run_show_its_cause(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run(env, monkeypatch)
    _publish_for_the_run(env, estado="RECHAZADA")
    text = _text(_dal_client(env).get("/resultados?vista=errores&m3=m3-real-1").text)

    assert "La evaluación no está publicada." in text
    assert "Perfiles de error por clase y familia" not in text


def test_an_unknown_metric_is_explained_not_a_server_error(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run(env, monkeypatch)
    _publish_for_the_run(env, extra_portfolio=_R_PROFILES)
    page = _dal_client(env).get("/resultados?vista=errores&metrica=smape")
    text = _text(page.text)

    assert page.status_code == 200
    assert "La consulta no es válida." in text
    assert "Perfiles de error por clase y familia" not in text


# ------------------------------------------------------------ las ventanas de la reserva
_WINDOWS = {
    "FIL-0001": [
        ("2026-03-15", 1.2, 1.5, 0.8, None),
        ("2026-03-22", 0.4, 0.0, None, "rmse_linea_base_cero"),
    ]
}


def _reserve(env: Env, query: str = "?reserva=1") -> str:
    """The reserve-windows table of FIL-0001, as the reader sees it."""
    text = _text(_dal_client(env).get(f"/resultados/sku/FIL-0001{query}").text)
    assert "Ventanas de la reserva" in text
    return text.split("Ventanas de la reserva", 1)[1].split("Comparación por familia")[0]


def test_the_detail_lists_the_reserve_windows_of_the_champion_and_the_baseline(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run(env, monkeypatch)
    _publish_for_the_run(env, windows=_WINDOWS)
    reserve = _reserve(env)

    assert "15 mar 2026 1,200 1,500 0,800" in reserve
    assert reserve.index("15 mar 2026") < reserve.index("22 mar 2026")


def test_a_window_without_r_shows_its_cause_and_the_count_by_cause(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run(env, monkeypatch)
    _publish_for_the_run(env, windows=_WINDOWS)
    reserve = _reserve(env)

    cause = "la línea base no tuvo error (RMSE 0)"
    assert f"22 mar 2026 0,400 0,000 no calculable: {cause}" in reserve
    assert f"1 ventana sin r: {cause}." in reserve


def test_the_reserve_windows_are_only_read_on_demand(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run(env, monkeypatch)
    _publish_for_the_run(env, windows=_WINDOWS)
    html = _dal_client(env).get("/resultados/sku/FIL-0001?m3=m3-real-1").text
    text = _text(html)

    assert "Ver las 40 ventanas de la reserva" in text
    assert "RMSE campeón" not in text
    assert "Ventanas de la reserva de FIL-0001" not in html
    [link] = re.findall(r'href="([^"]*reserva=1[^"]*)"', html)
    assert "m3=m3-real-1" in link


# --------------------------------------------- la barra de contexto de las otras pestañas
@pytest.mark.parametrize("vista", ["resumen", "errores"])
def test_the_context_bar_names_the_m3_run_and_the_data_origin(
    env: Env, monkeypatch: pytest.MonkeyPatch, vista: str
) -> None:
    _run(env, monkeypatch)
    _publish_for_the_run(env)
    html = _dal_client(env).get(f"/resultados?vista={vista}").text
    text = _text(html)

    # The evaluation reads by its state and its date in Bogotá time; its id and the exact UTC
    # instant stay in titles, for traceability.
    assert "Evaluación final Publicada · 27 oct 2026, 5:00 a. m." in text
    assert "UTC" not in text
    assert 'title="Evaluación m3-real-1"' in html
    assert "Origen de los datos Reales (declarado)" in text


@pytest.mark.parametrize("vista", ["resumen", "errores"])
def test_the_context_bar_announces_duplicates_with_links_that_keep_the_tab(
    env: Env, monkeypatch: pytest.MonkeyPatch, vista: str
) -> None:
    _run(env, monkeypatch)
    _publish_for_the_run(env, run_id="m3-real-0", creado_en="2026-10-26 10:00:00")
    _publish_for_the_run(env, verdicts={"FIL-0001": ("smooth", "EXPLORATORIO")})
    html = _dal_client(env).get(f"/resultados?vista={vista}").text

    assert _notice(html) == (
        "Hay varias evaluaciones publicadas para los mismos datos y ajustes. Se muestra la "
        "publicada el 27 oct 2026, 5:00 a. m."
    )
    assert f'href="/resultados?vista={vista}&amp;ejecucion=1&amp;m3=m3-real-0"' in html


@pytest.mark.parametrize("vista", ["resumen", "errores"])
def test_without_an_m3_run_the_context_bar_invents_no_origin_and_the_cause_shows_once(
    env: Env, monkeypatch: pytest.MonkeyPatch, vista: str
) -> None:
    _run(env, monkeypatch)
    text = _text(_dal_client(env).get(f"/resultados?vista={vista}").text)

    assert "Origen de los datos No informado en esta vista" in text
    assert text.count("Esta ejecución todavía no tiene evaluación final.") == 1


# --------------------------------------------------------------------------- por clase
def _by_class(env: Env, query: str = "") -> str:
    text = _text(_dal_client(env).get(f"/resultados?vista=clase{query}").text)
    assert "Campeón por clase" in text
    return text.split("Campeón por clase", 1)[1]


def test_by_class_shows_the_champion_its_reason_and_each_family_median(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run(env, monkeypatch)
    _publish_for_the_run(env)
    by_class = _by_class(env)

    assert "Suave Clásica Menor mediana de r 0,800 campeona — — —" in by_class
    assert (
        "Clase Campeona Motivo Clásica Aprendizaje automático Aprendizaje profundo Fundacional "
        "Suave"
    ) in by_class
    assert "Intermitente Clásica" in by_class


def test_by_class_details_compared_and_excluded_skus_left_out_families_and_verdicts(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run(env, monkeypatch)
    _publish_for_the_run(
        env,
        verdicts={
            "FIL-0001": ("smooth", "VALIDADO"),
            "FIL-0004": ("smooth", "EVIDENCIA_INSUFICIENTE"),
        },
        excluded_from_class={"FIL-0004": "sin_razon_calculable:classical"},
    )
    by_class = _by_class(env)

    assert "SKU comparados 1 Ver SKU FIL-0001" in by_class
    assert (
        "SKU excluidos de la selección 1 Ver SKU FIL-0004 : sin r calculable para Clásica"
        in by_class
    )
    assert "Familias fuera Aprendizaje profundo: no entregó resultados" in by_class
    assert (
        "Veredictos Validado 1 · Exploratorio 0 · Error técnico 0 · No evaluable 0 · "
        "Evidencia insuficiente 1"
    ) in by_class


class _DetailsTags(HTMLParser):
    """The attributes of every ``<details>`` start tag, whatever their order or spelling."""

    def __init__(self) -> None:
        super().__init__()
        self.attrs: list[dict[str, str | None]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "details":
            self.attrs.append(dict(attrs))


def test_by_class_shows_the_sku_counts_and_keeps_thousands_of_ids_inside_closed_details(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run(env, monkeypatch)
    _publish_for_the_run(env)
    compared = [f"SKU-{n:05d}" for n in range(3000)]
    excluded = {f"EXC-{n:05d}": "sin_razon_calculable:classical" for n in range(40)}
    with closing(sqlite3.connect(env.db)) as conn, conn:
        conn.execute(
            "UPDATE m3_categorias SET skus_comparables = ?, excluidos = ? "
            "WHERE sku_class = 'smooth'",
            (json.dumps(compared), json.dumps(excluded)),
        )
    html = _dal_client(env).get("/resultados?vista=clase").text

    section = html.split('id="detalle-por-clase"', 1)[1].split("<h3>", 2)[1]
    count = r'<dd><span class="mono">{}</span>\s*<details'
    assert re.search(r"<dt>SKU comparados</dt>" + count.format(re.escape("3.000")), section)
    assert re.search(r"<dt>SKU excluidos de la selección</dt>" + count.format(40), section)
    tags = _DetailsTags()
    tags.feed(section)
    assert len(tags.attrs) == 2 and all("open" not in attrs for attrs in tags.attrs)
    assert section.count("<summary>Ver SKU</summary>") == 2
    visible = re.sub(r"<details.*?</details>", "", section, flags=re.S)
    assert "SKU-02999" not in visible and "EXC-00039" not in visible
    assert "SKU-02999" in section
    assert '<span class="mono">EXC-00039</span>: sin r calculable para Clásica' in section


# ------------------------------------------------------------ la confirmación del campeón
INF = float("inf")
_CLASSES = {
    "FIL-0001": ("smooth", "VALIDADO"),
    "FIL-0002": ("intermittent", "EXPLORATORIO"),
    "FIL-0004": ("erratic", "VALIDADO"),
    "FIL-0005": ("lumpy", "VALIDADO"),
}
# Smooth confirms; intermittent fails Holm and one interval; erratic has too few SKUs against ML;
# lumpy has no confirmation row.
_SIGNIFICANCE = [
    ("smooth", 410, 37, 4, 29, 3, 0.9955),
    ("intermittent", 96, 0, 0, 0, 0, None),
    ("erratic", 40, 2, 0, 1, 0, 0.9975),
]
# fmt: off
_CONFIRMATIONS = [
    ("smooth", "classical", "confirmado", None, 0.0004, 0.0008, 0.05,
     412, 0.842, 0.801, 0.889, None, "classical"),
    ("intermittent", "classical", "no_confirmado", "p_holm>0.05; intervalo_no_bajo_1:ml",
     0.081, 0.162, 0.05, 96, 0.874, 0.812, 0.947, None, None),
    ("erratic", "classical", "sin_comparables", "pocos_skus:ml", None, None, 0.05,
     40, 0.913, 0.861, 0.968, None, None),
]
_COMPARISONS = [
    ("smooth", "ml", 412, 0.912, 0.871, 0.955, 0.905, 0.884, 0.927, 268, 0.603, 0.695,
     21040.5, 0.00002, 0.00002, 31, 9, None),
    ("smooth", "foundation", 410, 0.958, 0.921, 0.991, 0.951, 0.93, 0.972, 231, 0.515, 0.611,
     33012.0, 0.0004, 0.0004, 12, 7, None),
    ("intermittent", "ml", 96, 0.973, 0.921, 1.024, 0.968, 0.94, 0.997, 52, 0.443, 0.637,
     2050.0, 0.081, 0.081, 2, 1, None),
    ("intermittent", "foundation", 95, 0.881, 0.0, INF, None, None, None, 58, 0.511, 0.701,
     1610.0, 0.031, 0.031, 3, 0, "perdida_cero:hodges_lehmann"),
    ("erratic", "ml", 7, 0.95, None, None, None, None, None, 4, None, None,
     None, None, None, None, None, "pocos_skus:7<10"),
    ("erratic", "foundation", 38, 0.902, 0.84, 0.97, 0.9, 0.86, 0.95, 25, 0.5, 0.79,
     180.0, 0.012, 0.012, 2, 0, None),
]
# fmt: on


def _publish_confirmed(env: Env, **rows: Any) -> None:
    from tests.m3_publicada import publish_confirmation

    _publish_for_the_run(env, verdicts=_CLASSES)
    publish_confirmation(
        env.db,
        significance=rows.get("significance", _SIGNIFICANCE),
        confirmations=rows.get("confirmations", _CONFIRMATIONS),
        comparisons=rows.get("comparisons", _COMPARISONS),
    )


def _confirmation(env: Env) -> str:
    """The champion confirmation section, as the reader sees it."""
    html = _dal_client(env).get("/resultados?vista=clase").text
    assert 'id="confirmacion-del-campeon"' in html
    section = html.split('id="confirmacion-del-campeon"', 1)[1].split("</section>", 1)[0]
    return unescape(_text(section))


def test_by_class_shows_each_class_confirmation_state_holm_p_and_effect(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run(env, monkeypatch)
    _publish_confirmed(env)
    text = _confirmation(env)

    assert "α = 0,05; intervalos al 95 %." in text
    # The method stays one click away, folded, so it does not push the table down.
    assert re.search(
        r'<details class="pred-details pred-details--inline" id="metodo-confirmacion">'
        r"<summary>Cómo se decide la confirmación</summary>\s*<p[^>]*>La razón de un SKU es",
        _dal_client(env).get("/resultados?vista=clase").text,
    )
    assert (
        "Clase Campeona Estado p IUT p Holm Mediana de r frente a la línea base (IC) "
        "SKU comparados "
        "Suave Clásica Confirmado < 0,001 < 0,001 0,842 [0,801; 0,889] 412 "
        "Errática Clásica Sin comparables pocos SKU frente a Aprendizaje automático — — "
        "0,913 [0,861; 0,968] 40 "
        "Intermitente Clásica No confirmado p Holm > 0,05; "
        "intervalo no bajo 1 frente a Aprendizaje automático 0,081 0,162 0,874 [0,812; 0,947] 96 "
        "Grumosa Clásica Sin confirmación publicada para esta clase."
    ) in text


def test_by_class_lists_every_competitor_with_its_effect_sizes_and_tests(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run(env, monkeypatch)
    _publish_confirmed(env)
    text = _confirmation(env)

    smooth = text.split("Suave: Clásica frente a cada competidora", 1)[1]
    # The engine stores the champion as its own contender when it is not Fundacional.
    assert "Mejor familia sin la fundacional" not in text
    assert (
        "Competidora SKU Mediana de la razón Hodges-Lehmann Victorias de la campeona "
        "p Wilcoxon p IUT "
        "Aprendizaje automático 412 0,912 [0,871; 0,955] 0,905 268 (65,0 %) [60,3 %; 69,5 %] < "
        "0,001 "
        "< 0,001 "
        "Fundacional 410 0,958 [0,921; 0,991] 0,951 231 (56,3 %) [51,5 %; 61,1 %] < 0,001 < 0,001"
    ) in smooth
    # A zero loss leaves Hodges-Lehmann undefined and the interval open to infinity.
    assert (
        "Fundacional sin Hodges-Lehmann por pérdidas cero 95 0,881 [0,000; ∞] — 58 (61,1 %) "
        "[51,1 %; 70,1 %] 0,031 0,031"
    ) in text
    # Too few SKUs: only the count, the median and the wins, and the published cause.
    assert "Aprendizaje automático 7 SKU (mínimo 10) 7 0,950 — 4 (57,1 %) — —" in text
    assert "Grumosa: " not in text


def test_a_fundacional_champion_names_the_best_family_without_it_and_reads_every_cause(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run(env, monkeypatch)
    # fmt: off
    _publish_confirmed(
        env,
        confirmations=[
            ("smooth", "foundation", "no_confirmado",
             "p_holm>0.05; intervalo_no_bajo_1:classical,ml",
             0.081, 0.162, 0.05, 412, 0.842, 0.801, 0.889, None, "ml"),
            ("intermittent", "classical", "sin_comparables", "pocos_skus:dl,foundation,ml",
             None, None, 0.05, 96, None, None, None, "perdida_cero:mediana_r,ic_mediana",
             "classical"),
            ("erratic", "seasonal_naive", "no_aplica", "campeon_es_linea_base", None, None, 0.05,
             None, None, None, None, None, None),
        ],
        comparisons=[
            ("smooth", "classical", 412, 0.973, 0.921, 1.024, 0.968, 0.94, 0.997, 190, 0.41,
             0.51, 2050.0, 0.081, 0.081, 2, 1, None),
            ("smooth", "ml", 412, 0.912, 0.871, 1.002, 0.905, 0.884, 0.927, 268, 0.603, 0.695,
             21040.5, 0.00002, 0.00002, 31, 9, None),
            ("intermittent", "dl", 3, None, None, None, None, None, None, 1, None, None,
             None, None, None, None, None, "pocos_skus:3<10; perdida_cero:mediana_razon"),
        ],
    )
    # fmt: on
    html = _dal_client(env).get("/resultados?vista=clase").text
    text = _confirmation(env)

    assert (
        "Suave Fundacional No confirmado p Holm > 0,05; "
        "intervalo no bajo 1 frente a Clásica y Aprendizaje automático 0,081 0,162 0,842 [0,801; "
        "0,889] 412 "
        "Errática Línea base estacional No aplica la campeona es la línea base — — — — "
        "Intermitente Clásica Sin comparables pocos SKU frente a Aprendizaje profundo, "
        "Fundacional y Aprendizaje automático — — — sin mediana de r e intervalo de la mediana "
        "por pérdidas cero "
        "96 "
    ) in text
    smooth = text.split("Suave: Fundacional frente a cada competidora", 1)[1]
    assert smooth.startswith(" Mejor familia sin la fundacional: Aprendizaje automático. ")
    intermittent = text.split("Intermitente: Clásica frente a cada competidora")[1]
    assert not intermittent.startswith(" Mejor familia")
    assert (
        "Aprendizaje profundo 3 SKU (mínimo 10); sin mediana de la razón por pérdidas cero 3 —"
        in intermittent
    )
    # The champion never breaks inside its name; a cause breaks only between words.
    assert '<td class="pred-nowrap">Fundacional</td>' in html
    assert (
        '<span class="pred-subvalue pred-cause">p Holm &gt; 0,05; '
        "intervalo no bajo 1 frente a Clásica y Aprendizaje automático</span>"
    ) in html


@pytest.mark.parametrize(
    ("estado", "code", "expected"),
    [
        ("no_aplica", "datos_sinteticos", "No aplica datos sintéticos"),
        ("no_aplica", "origen_desconocido", "No aplica origen de los datos desconocido"),
        (
            "sin_comparables",
            "sin_familias_competidoras",
            "Sin comparables sin familias competidoras",
        ),
    ],
)
def test_each_class_cause_without_arguments_reads_in_spanish(
    env: Env, monkeypatch: pytest.MonkeyPatch, estado: str, code: str, expected: str
) -> None:
    _run(env, monkeypatch)
    _publish_confirmed(
        env,
        confirmations=[
            (
                "smooth",
                "classical",
                estado,
                code,
                None,
                None,
                0.05,
                None,
                None,
                None,
                None,
                None,
                None,
            ),
        ],
        comparisons=[],
    )

    assert f"Suave Clásica {expected} — —" in _confirmation(env)


def test_by_class_shows_the_bh_and_by_counts_of_each_class(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run(env, monkeypatch)
    _publish_confirmed(env)
    html = _dal_client(env).get("/resultados?vista=clase").text
    section = html.split('id="significancia-por-clase"', 1)[1].split("</section>", 1)[0]

    assert (
        "Clase SKU probados Mejores (BH) Peores (BH) Mejores (BY) Peores (BY) Nivel FCR "
        "Suave 410 37 4 29 3 99,55 % "
        "Errática 40 2 0 1 0 99,75 % "
        "Intermitente 96 0 0 0 0 —"
    ) in _text(section)


def test_an_fcr_level_below_one_never_shows_as_100_percent(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run(env, monkeypatch)
    # Level 1 - R * q / m with q = 0,05 and R = 1: two decimals reach 100,00 from m = 1000 on.
    _publish_confirmed(
        env,
        significance=[
            ("smooth", 2500, 1, 0, 1, 0, 0.99998),
            ("erratic", 999, 1, 0, 0, 0, 1 - 0.05 / 999),
            ("intermittent", 1001, 1, 0, 0, 0, 1 - 0.05 / 1001),
        ],
    )
    html = _dal_client(env).get("/resultados?vista=clase").text
    section = html.split('id="significancia-por-clase"', 1)[1].split("</section>", 1)[0]
    text = _text(section)

    assert "Suave 2.500 1 0 1 0 99,998 %" in text
    assert "Errática 999 1 0 0 0 99,99 %" in text
    assert "Intermitente 1.001 1 0 0 0 99,995 %" in text
    assert "100 %" not in text
    assert '<td class="num" title="0,99998">99,998 %</td>' in section


def test_values_near_one_or_alpha_keep_the_decimals_that_show_their_side(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run(env, monkeypatch)
    # fmt: off
    _publish_confirmed(
        env,
        confirmations=[
            ("smooth", "classical", "no_confirmado", "p_holm>0.05", 0.0496, 0.0504, 0.05,
             412, 0.9998, 0.9996, 1.0003, None, None),
        ],
        comparisons=[
            ("smooth", "ml", 412, 0.9996, 0.9992, 0.9999, 0.9997, 0.9993, 1.0001, 230, 0.51,
             0.605, 38000.0, 0.0496, 0.0496, 3, 1, None),
            ("smooth", "foundation", 410, 0.958, 0.921, 0.991, 0.951, 0.93, 0.972, 231, 0.515,
             0.611, 33012.0, 0.0494, 0.0494, 12, 7, None),
        ],
    )
    # fmt: on
    text = _confirmation(env)

    # Rounded to three decimals, 0,9996 and 0,9999 would read 1,000 and 0,0496 would read 0,050.
    assert (
        "Suave Clásica No confirmado p Holm > 0,05 0,0496 0,0504 0,9998 [0,9996; 1,0003] 412"
    ) in text
    # 0,9992 keeps three decimals: 0,999 is still below 1.
    assert (
        "Aprendizaje automático 412 0,9996 [0,999; 0,9999] 0,9997 230 (55,8 %) [51,0 %; 60,5 %] "
        "0,0496 0,0496"
    ) in text
    # Values away from 1 and from α keep three decimals.
    assert (
        "Fundacional 410 0,958 [0,921; 0,991] 0,951 231 (56,3 %) [51,5 %; 61,1 %] 0,049 0,049"
    ) in text
    # Each rounded figure carries the exact stored value as its title.
    html = _dal_client(env).get("/resultados?vista=clase").text
    for cell in (
        '<td class="num" title="0,0496">0,0496</td>',
        '<td class="num" title="0,0504">0,0504</td>',
        '<td class="num" title="0,9996">0,9996<span class="pred-subvalue" '
        'title="[0,9992; 0,9999]">[0,999; 0,9999]</span></td>',
        '<td class="num" title="0,9997">0,9997</td>',
        '<span class="pred-subvalue" title="[0,9996; 1,0003]">[0,9996; 1,0003]</span>',
    ):
        assert cell in html


def test_win_shares_at_the_edges_read_as_the_engine_stored_them(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run(env, monkeypatch)
    # Wilson bounds exactly as the engine computes them (alfa 0,05): unclipped past 1 and 0.
    # fmt: off
    _publish_confirmed(
        env,
        confirmations=[
            ("smooth", "classical", "no_confirmado", "p_holm>0.05; intervalo_no_bajo_1:foundation",
             1.0, 1.0, 0.05, 2037, 0.84, 0.8, 0.88, None, None),
        ],
        comparisons=[
            ("smooth", "ml", 16, 0.81, 0.74, 0.88, 0.82, 0.75, 0.89, 16,
             0.8063923194655637, 1.0000000000000002, 0.0, 0.0004, 0.0004, 5, 0, None),
            ("smooth", "dl", 2000, 0.62, 0.6, 0.64, 0.63, 0.61, 0.65, 1999,
             0.9971731374967338, 0.9999117322692944, 1.0, 0.0, 0.0, 950, 0, None),
            ("smooth", "foundation", 21, 1.21, 1.1, 1.33, 1.2, 1.1, 1.3, 0,
             -1.3877787807814457e-17, 0.154639018924847, 231.0, 0.00006, 1.0, 0, 9, None),
        ],
    )
    # fmt: on
    html = _dal_client(env).get("/resultados?vista=clase").text
    text = _confirmation(env)

    assert "No fue posible leer" not in _text(html)
    assert "16 (100,0 %) [80,6 %; 100,0 %]" in text
    assert "0 (0,0 %) [0,0 %; 15,5 %]" in text
    # Below 100 % never reads 100,0 %.
    assert "1.999 (99,95 %) [99,7 %; 99,99 %]" in text
    assert 'title="[0,8063923194655637; 1,0000000000000002]">[80,6 %; 100,0 %]</span>' in html
    assert 'title="[0,9971731374967338; 0,9999117322692944]">[99,7 %; 99,99 %]</span>' in html
    assert 'id="significancia-por-clase"' in html


# The rows a real pred-engine run wrote for a 30-SKU real panel, copied as stored in its SQLite:
# most classes have too few SKUs to test, and the two comparisons with a test carry their Wilson
# bounds, Hodges-Lehmann and p-values exactly as the engine computed them.
# fmt: off
_ENGINE_SIGNIFICANCE = [
    ("erratic", 3, 2, 0, 1, 0, 0.96666666666666667),
    ("intermittent", 12, 11, 0, 9, 0, 0.95416666666666661),
    ("lumpy", 3, 0, 0, 0, 0, None),
    ("smooth", 12, 12, 0, 11, 0, 0.95),
]
_ENGINE_CONFIRMATIONS = [
    ("erratic", "foundation", "sin_comparables", "pocos_skus:classical,dl,ml", None, None, 0.05,
     3, 0.68940409402190339, 0.64256447304750242, 0.77453622435114089, None, "dl"),
    ("intermittent", "foundation", "sin_comparables", "pocos_skus:classical", None, None, 0.05,
     12, 0.77675056700061673, 0.71276016740413239, 0.81123271580353462, None, "ml"),
    ("lumpy", "classical", "sin_comparables", "pocos_skus:foundation", None, None, 0.05,
     3, 0.90076244944326556, 0.82470911799879143, 1.0498537904246341, None, "classical"),
    ("smooth", "foundation", "sin_comparables", "pocos_skus:classical", None, None, 0.05,
     12, 0.71841465158115903, 0.6879512377598076, 0.75778964218378597, None, "ml"),
]
_ENGINE_COMPARISONS = [
    ("erratic", "classical", 2, 0.83049217639358242, None, None, None, None, None, 2,
     None, None, None, None, None, None, None, "pocos_skus:2<10"),
    ("erratic", "dl", 3, 0.67783734454997624, None, None, None, None, None, 3,
     None, None, None, None, None, None, None, "pocos_skus:3<10"),
    ("erratic", "ml", 3, 0.80464309794110644, None, None, None, None, None, 3,
     None, None, None, None, None, None, None, "pocos_skus:3<10"),
    ("intermittent", "classical", 9, 0.79190552284821714, None, None, None, None, None, 9,
     None, None, None, None, None, None, None, "pocos_skus:9<10"),
    ("intermittent", "ml", 12, 0.90155776219860939, 0.83313247684710801, 0.94309163941109031,
     0.87183116883934941, 0.74118029718353629, 0.9327957734307285, 12,
     0.75750599334475921, 1.0, 0.0, 0.00048828125, 0.00048828125, 6, 0, None),
    ("lumpy", "foundation", 3, 1.1324642564328855, None, None, None, None, None, 0,
     None, None, None, None, None, None, None, "pocos_skus:3<10"),
    ("smooth", "classical", 9, 0.83512254145329135, None, None, None, None, None, 9,
     None, None, None, None, None, None, None, "pocos_skus:9<10"),
    ("smooth", "dl", 12, 0.80895842411212326, 0.77366387761875188, 0.84611383890919423,
     0.81421126571762426, 0.74250001172403146, 0.86419194085698248, 11,
     0.64612008885888295, 0.98513490559508288, 1.0, 0.0009765625, 0.0009765625, 10, 0, None),
    ("smooth", "ml", 12, 0.86601461503938881, 0.81097304248752256, 0.88919161368325472,
     0.81685184335370453, 0.72125159094510583, 0.9180458077361785, 12,
     0.75750599334475921, 1.0, 0.0, 0.00048828125, 0.00048828125, 7, 0, None),
]
# fmt: on


def test_by_class_reads_the_rows_a_real_engine_run_wrote(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run(env, monkeypatch)
    _publish_confirmed(
        env,
        significance=_ENGINE_SIGNIFICANCE,
        confirmations=_ENGINE_CONFIRMATIONS,
        comparisons=_ENGINE_COMPARISONS,
    )
    html = _dal_client(env).get("/resultados?vista=clase").text
    text = _confirmation(env)
    significance = _text(html.split('id="significancia-por-clase"', 1)[1].split("</section>")[0])

    assert "No fue posible leer" not in _text(html)
    assert (
        "Suave Fundacional Sin comparables pocos SKU frente a Clásica — — "
        "0,718 [0,688; 0,758] 12 "
        "Errática Fundacional Sin comparables pocos SKU frente a Clásica, Aprendizaje profundo y "
        "Aprendizaje automático — — "
        "0,689 [0,643; 0,775] 3 "
        "Intermitente Fundacional Sin comparables pocos SKU frente a Clásica — — "
        "0,777 [0,713; 0,811] 12 "
        "Grumosa Clásica Sin comparables pocos SKU frente a Fundacional — — "
        "0,901 [0,825; 1,050] 3"
    ) in text
    assert (
        "Clásica 9 SKU (mínimo 10) 9 0,835 — 9 (100,0 %) — — "
        "Aprendizaje automático 12 0,866 [0,811; 0,889] 0,817 12 (100,0 %) [75,8 %; 100,0 %] < "
        "0,001 < 0,001 "
        "Aprendizaje profundo 12 0,809 [0,774; 0,846] 0,814 11 (91,7 %) [64,6 %; 98,5 %] < 0,001 "
        "< 0,001"
    ) in text
    assert 'title="[0,7575059933447592; 1,0]">[75,8 %; 100,0 %]</span>' in html
    assert 'title="[0,646120088858883; 0,9851349055950829]">[64,6 %; 98,5 %]</span>' in html
    assert (
        "Suave 12 12 0 11 0 95 % Errática 3 2 0 1 0 96,67 % "
        "Intermitente 12 11 0 9 0 95,42 % Grumosa 3 0 0 0 0 —"
    ) in significance


def test_by_class_says_when_the_run_was_published_before_the_confirmation(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run(env, monkeypatch)
    _publish_for_the_run(env, eligibility=None)  # no reserve blocks: an older engine
    html = _dal_client(env).get("/resultados?vista=clase").text
    text = _text(html)

    assert "Suave Clásica Menor mediana de r 0,800" in text
    assert "La evaluación final no publicó la confirmación del campeón para esta ejecución." in text
    assert "p Holm" not in text
    assert 'id="significancia-por-clase"' not in html


def test_an_invalid_confirmation_row_is_a_visible_problem_and_the_champions_stay(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run(env, monkeypatch)
    smooth_ml = _COMPARISONS[0]
    inverted = (*smooth_ml[:4], smooth_ml[5], smooth_ml[4], *smooth_ml[6:])  # upper < lower
    _publish_confirmed(env, comparisons=[inverted, *_COMPARISONS[1:]])
    html = _dal_client(env).get("/resultados?vista=clase").text
    text = _text(html)

    assert "Suave Clásica Menor mediana de r 0,800" in text
    assert "No fue posible leer la evaluación." in text
    assert "ic_mediana: los extremos están invertidos" in text
    assert 'id="significancia-por-clase"' not in html


# fmt: off
@pytest.mark.parametrize(
    ("smooth", "reason"),
    [
        # Confirmed although its Holm p is above alfa.
        (("smooth", "classical", "confirmado", None, 0.0004, 0.8, 0.05,
          412, 0.842, 0.801, 0.889, None, "classical"),
         "un campeón confirmado tiene un p de Holm mayor que alfa"),
        # A class IUT p below the largest competitor p (0,0004).
        (("smooth", "classical", "confirmado", None, 0.00002, 0.00006, 0.05,
          412, 0.842, 0.801, 0.889, None, "classical"),
         "el p IUT de la clase no es el mayor p IUT de sus competidoras, todas probadas"),
    ],
)
# fmt: on
def test_an_incoherent_confirmation_is_a_visible_problem_never_a_confirmed_row(
    env: Env, monkeypatch: pytest.MonkeyPatch, smooth: tuple[object, ...], reason: str
) -> None:
    _run(env, monkeypatch)
    _publish_confirmed(env, confirmations=[smooth, *_CONFIRMATIONS[1:]])
    html = _dal_client(env).get("/resultados?vista=clase").text
    text = _text(html)

    assert "No fue posible leer la evaluación." in text
    assert reason in text
    assert "Confirmado" not in _confirmation(env)


def test_a_value_out_of_its_range_is_explained_in_spanish(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run(env, monkeypatch)
    smooth_ml = list(_COMPARISONS[0])
    smooth_ml[13] = 1.2  # p_wilcoxon
    _publish_confirmed(env, comparisons=[tuple(smooth_ml), *_COMPARISONS[1:]])
    text = _text(_dal_client(env).get("/resultados?vista=clase").text)

    assert "p_wilcoxon: debe ser menor o igual que 1" in text
    assert "Input should" not in text


def test_by_class_without_a_published_run_or_with_a_rejected_one_shows_the_cause(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run(env, monkeypatch)
    client = _dal_client(env)
    text = _text(client.get("/resultados?vista=clase").text)
    assert "Esta ejecución todavía no tiene evaluación final." in text
    assert "Campeón por clase" not in text

    _publish_for_the_run(env, estado="RECHAZADA")
    rejected = _text(client.get("/resultados?vista=clase&m3=m3-real-1").text)
    assert "La evaluación no está publicada." in rejected
    assert "Campeón por clase" not in rejected
