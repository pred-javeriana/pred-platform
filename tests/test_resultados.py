"""Superficie /resultados, vista «Por SKU» y detalle (C2): lo que se ve y lo que se delega."""

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
from tests.test_seleccion import _run
from tests.test_worker import Env
from tests.test_worker import env as env  # noqa: F401  (the fixture)


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


def _notice(html: str) -> str:
    """The text of the warning alert, as the reader sees it (inline markup adds no space)."""
    found = re.search(r'pred-alert--warning".*?<div>(.*?)</div>', html, re.S)
    assert found is not None
    return re.sub(r"<[^>]+>", "", found.group(1)).strip()


# --------------------------------------------------------------------------- la tabla
def test_the_table_shows_compact_decisions_and_family_evidence_on_demand() -> None:
    client, _ = _client()
    response = client.get("/resultados")
    html = response.text

    assert response.status_code == 200
    assert 'aria-current="page"' in html and "Por SKU" in html
    for sku in ("FIL-0001", "FIL-0002", "FIL-0003", "FIL-0004"):
        assert f'href="/resultados/sku/{sku}?' in html
        assert f"Ver familias de {sku}" in html
    for header in ("SKU", "Clase", "Campeón", "Evidencia por familia"):
        assert f'<th scope="col">{header}</th>' in html
    # The fixture's v1 run "1" has no M3 evaluation: no champion is invented from the legacy field.
    assert _text(html).count("Sin veredicto M3") == 4
    assert "Esta ejecución todavía no tiene evaluación M3." in _text(html)
    assert 'aria-label="Sin dato"' in html


def test_filters_sort_and_paging_are_delegated_not_applied_in_the_view() -> None:
    client, repo = _client()
    client.get(
        "/resultados?sku_class=lumpy&family=classical&estado=exitosa&q=FIL&sort=valor"
        "&descending=1&page=2&size=1&ejecucion=7"
    )

    assert repo.list_calls[-1] == (
        "7",
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
    client, _ = _client("vacio")
    html = client.get("/resultados").text

    assert "Todavía no hay selección por SKU." in html
    assert "<table" not in html


# ------------------------------------------------------------------------- la acción
def test_the_action_is_enabled_when_all_three_capabilities_are_available() -> None:
    client, _ = _client("normal")
    html = client.get("/resultados").text

    action = re.search(r'<a class="pred-btn pred-btn--primary"[^>]*>', html)
    assert action is not None and 'href="/ejecucion"' in action.group(0)
    assert "aria-disabled" not in action.group(0)
    assert "La acción está deshabilitada" not in html


def test_the_disabled_action_says_why() -> None:
    client, _ = _client("vacio")  # parquet_read yes; comparison and persisted results no
    html = client.get("/resultados").text

    disabled = re.search(r'<a class="pred-btn pred-btn--primary"[^>]*aria-disabled="true"', html)
    assert disabled is not None and "href=" not in disabled.group(0)
    assert "La acción está deshabilitada" in html
    assert "Comparación común entre familias: todavía no está implementada (G2, G3)" in html
    assert "Resultados de selección guardados: todavía no está implementada (G4)" in html
    assert "Lectura del Parquet" not in _text(html).split("Falta:")[1]


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
    assert action.missing == ("Lectura del Parquet: la etapa aún no existe.",)


# --------------------------------------------------------------------- las otras pestañas
@pytest.mark.parametrize("vista", ["resumen", "clase"])
def test_summary_tabs_are_honestly_unavailable_until_the_champion_exists(vista: str) -> None:
    client, repo = _client("vacio")  # its capabilities say the champion stage does not exist
    html = client.get(f"/resultados?vista={vista}").text

    assert "Todavía no hay campeón por SKU." in html
    assert "stage_not_implemented" in html
    assert repo.list_calls == []  # nothing is read, nothing is invented


# ------------------------------------------------------------------------- el detalle
def test_the_detail_shows_four_families_with_evidence_trials_and_a_pending_champion() -> None:
    client, repo = _client("normal")
    # FIL-0001 appears in two examples; the scenario serves the lumpy one for FIL-0004.
    response = client.get("/resultados/sku/FIL-0001")
    text = _text(response.text)

    assert response.status_code == 200
    assert repo.detail_calls[-1] == ("latest", "FIL-0001", False)
    for family in ("Clásica", "ML", "DL", "Fundacional"):
        assert family in text
    assert "Orden (p, d, q) (1, 2, 2)" in text
    assert "Hiperparámetros" in text
    assert "No optimiza: no hay estudio ni ensayos." in text
    assert "ADI 1,000" in text
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
        r'<span class="pred-status pred-status--pruned">.*?</span>\s*</span>', html, re.S
    )
    failed = re.search(
        r'<span class="pred-status pred-status--err">.*?</span>\s*</span>', html, re.S
    )
    assert pruned is not None and "Podada" in pruned.group(0) and "#i-prune" in pruned.group(0)
    assert failed is not None and "Fallida" in failed.group(0) and "#i-x" in failed.group(0)
    text = _text(html)
    assert "poda_semantica:prediccion_nula" in text
    assert "ajuste_fallido: AjusteModeloError: no converge" in text
    assert "Un ensayo podado no es un fallo" in text


def test_a_failed_family_shows_its_error_and_an_unrunnable_one_its_state() -> None:
    client, _ = _client("errores")
    text = _text(client.get("/resultados/sku/FIL-0001").text)

    assert "Fallida" in text and "No Ejecutable" in text
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
    back = re.search(r'class="pred-back" href="([^"]*)"', detail)
    assert back is not None
    assert "sku_class=lumpy" in back.group(1) and "sort=valor" in back.group(1)


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
    assert "Ejecución #1" in text and "Completada con fallos" in text
    assert "La acción está deshabilitada" not in text  # the DAL has all three capabilities

    detail = client.get("/resultados/sku/FIL-0002?ventanas=1")
    detail_text = _text(detail.text)
    assert detail.status_code == 200
    assert "El modelo no pudo ajustarse." in detail_text  # model.fit_failed, from the failed ml
    assert "ajuste_fallido" in detail_text and "Podada" in detail_text and "Fallida" in detail_text
    assert "Entrenamiento" in detail_text  # windows of the classical family that did succeed
    assert "disperso y estable" in detail_text


def test_the_real_dal_answers_for_a_run_that_has_not_finished(env: Env) -> None:
    from tests.test_worker import SMALL, enqueue_run

    enqueue_run(env.settings, env.ingest_id, SMALL)
    text = _text(_dal_client(env).get("/resultados").text)

    assert "Todavía no hay selección por SKU." in text
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


def test_texts_keep_the_engine_detail_and_fall_back_to_the_code() -> None:
    assert exclusion_text("series_too_short", "la serie termina antes de t*").startswith(
        "La serie del SKU es demasiado corta"
    )
    assert "la serie termina antes de t*" in exclusion_text(
        "series_too_short", "la serie termina antes de t*"
    )
    assert exclusion_text("future_reason", None) == "future_reason"
    assert exclusion_text("not_configured", "La corrida se configuro solo con classical.") == (
        "La corrida se configuro solo con classical."
    )
    assert exclusion_text("not_in_policy_matrix", None).startswith("La política de selección")
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


def test_the_table_shows_the_champion_and_the_five_verdict_labels_m3_published(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run(env, monkeypatch)
    _publish_for_the_run(env)
    client = _dal_client(env)

    html = client.get("/resultados").text
    text = _text(html)
    assert "Corrida M3 m3-real-1 PUBLICADA" in text
    assert "FIL-0001/classical/sarima" in text and "VALIDADO" in text
    assert "FIL-0002/classical/sarima" in text and "EXPLORATORIO" in text
    assert "Excluido antes de M3: lanzado despues del corte" in text
    assert "Origen de los datos: reales (declarado)" in text
    assert "se sostiene" not in text and "Sin veredicto M3" not in text
    # Detail links, filters and pages keep the resolved M3 run, not «the newest».
    assert "/resultados/sku/FIL-0001?ejecucion=&amp;m3=m3-real-1" not in html
    assert 'href="/resultados/sku/FIL-0001?m3=m3-real-1&amp;volver=' in html
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
    assert "Decisión publicada por M3 Clásica" in text
    assert "VALIDADO Evaluado · FIL-0001/classical/sarima" in text
    assert "VALIDADO: datos reales, ventanas suficientes y r &lt; 1 (descriptivo)" in text
    assert "Ventanas válidas 40 (mínimo n_min 30)" in text
    assert "r frente a Seasonal Naive 0,800" in text
    assert "p ajustado (BH) 0,080" in text and "Significativa no" in text
    assert "VALIDADO no equivale a significativo" in text
    assert "Corrida M3 m3-real-1" in text and env.ingest_id in text
    assert "ventanas=1" in page.text and "m3=m3-real-1&amp;ventanas=1" in page.text

    late = _text(client.get("/resultados/sku/FIL-0003").text)
    assert "Excluido antes de M3" in late and "lanzado despues del corte" in late
    assert "corte t* 2026-03-13" in late


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

    text = _text(client.get("/resultados").text)
    assert f"FIL-0003 Lumpy (Grumosa) Excluido antes de M3: {ONE_ROUTED_CAUSE}" in text
    assert "Excluido antes de M3: lanzado despues del corte" in text
    assert "Sin veredicto M3" not in text

    detail = _text(client.get("/resultados/sku/FIL-0003").text)
    assert "Excluido antes de M3" in detail and ONE_ROUTED_CAUSE in detail
    assert "No es un veredicto ni un fallo técnico." in detail
    # The cut t* explains a late launch, not an exclusion decided in M2.
    assert "corte t*" not in detail
    assert "corte t* 2026-03-13" in _text(client.get("/resultados/sku/FIL-0002").text)


def test_a_test_that_did_not_run_shows_its_cause_not_a_p_value(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run(env, monkeypatch)
    _publish_for_the_run(env, origin="sintetico", verdicts={"FIL-0001": ("smooth", "EXPLORATORIO")})
    text = _text(_dal_client(env).get("/resultados/sku/FIL-0001").text)
    assert "Prueba HLN-DM sin prueba: datos_sinteticos" in text
    assert "p ajustado" not in text
    assert "Origen de los datos: sintéticos o aumentados (declarado)" in text


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
        r"Clásica sarima 0,800 .*ML lightgbm 0,900 .*Seasonal Naive seasonal_naive 1,000",
        _ranking(text),
    )


def test_the_ranking_marks_the_published_champion_even_if_another_model_has_lower_r(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run(env, monkeypatch)
    _publish_for_the_run(env, extra_models={"FIL-0001": [("ml", "lightgbm", 0.7)]})
    ranking = _ranking(_text(_dal_client(env).get("/resultados/sku/FIL-0001").text))

    assert re.search(
        r"ML lightgbm 0,700 .*Clásica sarima 0,800 Campeón "
        r".*Seasonal Naive seasonal_naive 1,000 Línea base",
        ranking,
    )
    assert ranking.count("Campeón") == 1


def test_a_model_without_r_goes_last_as_not_computable(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run(env, monkeypatch)
    _publish_for_the_run(env, extra_models={"FIL-0001": [("dl", "mlp", None)]})
    ranking = _ranking(_text(_dal_client(env).get("/resultados/sku/FIL-0001").text))

    assert "Seasonal Naive seasonal_naive 1,000 Línea base DL mlp no calculable" in ranking
    assert "mlp 0,000" not in ranking


def test_several_publications_of_one_identity_are_announced_and_can_be_chosen(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run(env, monkeypatch)
    _publish_for_the_run(env, run_id="m3-real-0", creado_en="2026-10-26 10:00:00")
    _publish_for_the_run(env, verdicts={"FIL-0001": ("smooth", "EXPLORATORIO")})
    client = _dal_client(env)

    html = client.get("/resultados").text
    # The contract's literal, with the run id kept on one line.
    assert _notice(html) == (
        "Hay varias corridas PUBLICADA con esta identidad. Se muestra m3-real-1."
    )
    assert '<span class="mono pred-nowrap">m3-real-1</span>' in html
    assert 'href="/resultados?vista=sku&amp;m3=m3-real-0"' in html
    older = client.get("/resultados?m3=m3-real-0").text
    assert _notice(older).endswith("Se muestra m3-real-0.") and "VALIDADO" in _text(older)


def test_an_m3_run_of_another_execution_is_refused_and_an_unpublished_one_explained(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run(env, monkeypatch)
    _publish_for_the_run(env, estado="EVALUADA")
    client = _dal_client(env)

    text = _text(client.get("/resultados").text)
    assert "Esta ejecución todavía no tiene una corrida M3 PUBLICADA." in text
    assert "La corrida M3 más reciente, m3-real-1, está EVALUADA." in text
    assert "Sin veredicto M3" in text

    other = client.get("/resultados?m3=m3-ajena").text
    assert "La corrida M3 pedida no corresponde a esta ejecución." in _text(other)
    assert 'pred-status__label">VALIDADO' not in other


def test_a_publication_without_its_exclusion_metadata_shows_why_there_are_no_verdicts(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run(env, monkeypatch)
    _publish_for_the_run(env, publish_m4=False)
    html = _dal_client(env).get("/resultados").text
    text = _text(html)
    assert "La publicación M3 no tiene sus metadatos de exclusión." in text
    assert "no significa cero exclusiones" in text
    assert 'pred-status__label">VALIDADO' not in html


# ------------------------------------------------------------------ el resumen de la corrida
_SUMMARY_VERDICTS = {
    "FIL-0001": ("smooth", "VALIDADO"),
    "FIL-0002": ("intermittent", "EXPLORATORIO"),
    "FIL-0004": ("lumpy", "EVIDENCIA_INSUFICIENTE"),
}


def _summary(env: Env) -> str:
    """The run summary as the reader sees it, from its first heading on."""
    text = _text(_dal_client(env).get("/resultados?vista=resumen").text)
    assert "Embudo de la corrida" in text
    return text.split("Embudo de la corrida", 1)[1]


def test_the_summary_shows_the_funnel_m3_published(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run(env, monkeypatch)
    _publish_for_the_run(env, verdicts=_SUMMARY_VERDICTS)  # FIL-0003 is launched after the cut
    summary = _summary(env)

    for expected in (
        "Productos de la corrida 4",
        "Lanzados después del corte 1",
        "Con veredicto M3 3",
        "Evaluados 2",
        "No evaluados 1",
        "Evaluados VALIDADO 1",
        "Evaluados EXPLORATORIO 1",
        "No evaluados FALLO_TECNICO 0",
        "No evaluados NO_EVALUABLE 0",
        "No evaluados EVIDENCIA_INSUFICIENTE 1",
    ):
        assert expected in summary


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
    summary = _summary(env).split("Grupo Veredicto SKU", 1)[0]

    # 3 products = 1 launched after the cut + 1 excluded by M2 + 1 with a verdict.
    for expected in (
        "Productos de la corrida 3",
        "Lanzados después del corte 1",
        "Excluidos por menos de dos familias evaluables (M2) 1",
        "Con veredicto M3 1",
    ):
        assert expected in summary


def test_a_run_published_before_m3_listed_m2_exclusions_says_they_are_not_reported(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run(env, monkeypatch)
    _publish_for_the_run(env, verdicts=_SUMMARY_VERDICTS, eligibility=None)
    summary = _summary(env)

    assert "Excluidos por menos de dos familias evaluables (M2) No informado" in summary


def test_the_summary_counts_the_dm_test_over_the_run_and_its_causes(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run(env, monkeypatch)
    _publish_for_the_run(env, verdicts=_SUMMARY_VERDICTS, significant=("FIL-0001",))
    summary = _summary(env)

    assert "Prueba Diebold-Mariano" in summary
    dm = summary.split("Prueba Diebold-Mariano", 1)[1]
    for expected in (
        "Con prueba HLN-DM 2",
        "Significativas después de BH 1",
        "No significativas 1",
        "Sin prueba 1",
        "veredicto:EVIDENCIA_INSUFICIENTE 1",
    ):
        assert expected in dm


def test_the_dm_counts_cover_every_page_of_verdicts(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run(env, monkeypatch)
    many = {f"SKU-{i:04d}": ("smooth", "VALIDADO") for i in range(501)}
    _publish_for_the_run(env, verdicts=many)
    dm = _summary(env).split("Prueba Diebold-Mariano", 1)[1]

    assert "Con prueba HLN-DM 501" in dm
    assert "No significativas 501" in dm


def test_the_summary_without_a_published_m3_run_says_why_not_zero(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run(env, monkeypatch)  # M2 finished; M3 published nothing
    text = _text(_dal_client(env).get("/resultados?vista=resumen").text)

    assert "Esta ejecución todavía no tiene evaluación M3." in text
    assert "Embudo de la corrida" not in text
    assert "Prueba Diebold-Mariano" not in text


def test_the_summary_of_an_unavailable_publication_shows_its_cause_not_zeros(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run(env, monkeypatch)
    _publish_for_the_run(env, publish_m4=False)
    text = _text(_dal_client(env).get("/resultados?vista=resumen").text)

    assert "La publicación M3 no tiene sus metadatos de exclusión." in text
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
    assert "No fue posible leer la evidencia M3." in text and "justificacion" in text
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
        ("errores", "get_m3_run_status", 1),
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
    assert "No fue posible leer la evidencia M3." in text and "fila inválida" in text


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

    assert "Smooth (Suave) Clásica 0,820 0,800 0,700 0,900 3 4 1" in profiles
    assert "Smooth (Suave) Seasonal Naive 1,000 1,000 1,000 1,000 3 3 0" in profiles
    assert "Intermittent (Intermitente) Clásica — — — — 1 1 1" in profiles
    assert "1,500 1,500 1,200 1,800" not in profiles  # the RMSE rows


def test_choosing_another_metric_shows_its_profile_and_keeps_the_run(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run(env, monkeypatch)
    _publish_for_the_run(env, extra_portfolio=_R_PROFILES)
    html = _dal_client(env).get("/resultados?vista=errores&metrica=rmse").text
    profiles = _text(html).split("Perfiles de error por clase y familia", 1)[1]

    assert "Smooth (Suave) Clásica 1,500 1,500 1,200 1,800 2 2 0" in profiles
    assert "0,820" not in profiles  # the r rows
    [mae] = re.findall(r'href="([^"]*metrica=mae[^"]*)"', html)
    assert "vista=errores" in mae and "m3=m3-real-1" in mae


def test_the_error_profiles_without_a_published_m3_run_say_why_not_zero(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run(env, monkeypatch)  # M2 finished; M3 published nothing
    text = _text(_dal_client(env).get("/resultados?vista=errores").text)

    assert "Esta ejecución todavía no tiene evaluación M3." in text
    assert "Perfiles de error por clase y familia" not in text


def test_the_error_profiles_of_a_rejected_m3_run_show_its_cause(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run(env, monkeypatch)
    _publish_for_the_run(env, estado="RECHAZADA")
    text = _text(_dal_client(env).get("/resultados?vista=errores&m3=m3-real-1").text)

    assert "La corrida M3 no está publicada." in text
    assert "Perfiles de error por clase y familia" not in text


def test_an_unknown_metric_is_explained_not_a_server_error(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run(env, monkeypatch)
    _publish_for_the_run(env, extra_portfolio=_R_PROFILES)
    page = _dal_client(env).get("/resultados?vista=errores&metrica=smape")
    text = _text(page.text)

    assert page.status_code == 200
    assert "La consulta M3 no es válida." in text
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

    assert "2026-03-15 1,200 1,500 0,800" in reserve
    assert reserve.index("2026-03-15") < reserve.index("2026-03-22")


def test_a_window_without_r_shows_its_cause_and_the_count_by_cause(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run(env, monkeypatch)
    _publish_for_the_run(env, windows=_WINDOWS)
    reserve = _reserve(env)

    assert "2026-03-22 0,400 0,000 no calculable: rmse_linea_base_cero" in reserve
    assert "1 ventana sin r: rmse_linea_base_cero" in reserve


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


# ---------------------------------------------------- el panel lateral de las otras pestañas
@pytest.mark.parametrize("vista", ["resumen", "errores"])
def test_the_side_panel_names_the_m3_run_and_the_data_origin(
    env: Env, monkeypatch: pytest.MonkeyPatch, vista: str
) -> None:
    _run(env, monkeypatch)
    _publish_for_the_run(env)
    text = _text(_dal_client(env).get(f"/resultados?vista={vista}").text)

    assert "Corrida M3 m3-real-1 PUBLICADA" in text
    assert "Origen de los datos: reales (declarado)" in text


@pytest.mark.parametrize("vista", ["resumen", "errores"])
def test_the_side_panel_announces_duplicates_with_links_that_keep_the_tab(
    env: Env, monkeypatch: pytest.MonkeyPatch, vista: str
) -> None:
    _run(env, monkeypatch)
    _publish_for_the_run(env, run_id="m3-real-0", creado_en="2026-10-26 10:00:00")
    _publish_for_the_run(env, verdicts={"FIL-0001": ("smooth", "EXPLORATORIO")})
    html = _dal_client(env).get(f"/resultados?vista={vista}").text

    assert _notice(html) == (
        "Hay varias corridas PUBLICADA con esta identidad. Se muestra m3-real-1."
    )
    assert f'href="/resultados?vista={vista}&amp;m3=m3-real-0"' in html


@pytest.mark.parametrize("vista", ["resumen", "errores"])
def test_without_an_m3_run_the_side_panel_invents_no_origin_and_the_cause_shows_once(
    env: Env, monkeypatch: pytest.MonkeyPatch, vista: str
) -> None:
    _run(env, monkeypatch)
    text = _text(_dal_client(env).get(f"/resultados?vista={vista}").text)

    assert "Origen de los datos: no informado en esta vista" in text
    assert text.count("Esta ejecución todavía no tiene evaluación M3.") == 1


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

    assert "Smooth (Suave) Clásica Menor mediana de r 0,800 campeona — — —" in by_class
    assert "Clase Campeona Motivo Clásica ML DL Fundacional Smooth" in by_class
    assert "Intermittent (Intermitente) Clásica" in by_class


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
        "SKU excluidos de la selección 1 Ver SKU FIL-0004: sin_razon_calculable:classical"
        in by_class
    )
    assert "Familias fuera DL: no_entregada" in by_class
    assert (
        "Veredictos VALIDADO 1 · EXPLORATORIO 0 · FALLO_TECNICO 0 · NO_EVALUABLE 0 · "
        "EVIDENCIA_INSUFICIENTE 1"
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
    assert re.search(r"<dt>SKU comparados</dt>" + count.format(3000), section)
    assert re.search(r"<dt>SKU excluidos de la selección</dt>" + count.format(40), section)
    tags = _DetailsTags()
    tags.feed(section)
    assert len(tags.attrs) == 2 and all("open" not in attrs for attrs in tags.attrs)
    assert section.count("<summary>Ver SKU</summary>") == 2
    visible = re.sub(r"<details.*?</details>", "", section, flags=re.S)
    assert "SKU-02999" not in visible and "EXC-00039" not in visible
    assert "SKU-02999" in section and "EXC-00039: sin_razon_calculable:classical" in section


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
    ("smooth", "classical", "confirmado", None, 0.00002, 0.00006, 0.05,
     412, 0.842, 0.801, 0.889, None, "ml"),
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
    assert (
        "Clase Campeona Estado p IUT p Holm r frente a Seasonal Naive SKU "
        "Smooth (Suave) Clásica Confirmado < 0,001 < 0,001 0,842 [0,801; 0,889] 412 "
        "Erratic (Errática) Clásica Sin comparables pocos_skus:ml — — 0,913 [0,861; 0,968] 40 "
        "Intermittent (Intermitente) Clásica No confirmado p_holm>0.05; intervalo_no_bajo_1:ml "
        "0,081 0,162 0,874 [0,812; 0,947] 96 "
        "Lumpy (Grumosa) Clásica Sin confirmación publicada para esta clase."
    ) in text


def test_by_class_lists_every_competitor_with_its_effect_sizes_and_tests(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run(env, monkeypatch)
    _publish_confirmed(env)
    text = _confirmation(env)

    smooth = text.split("Smooth (Suave): Clásica frente a cada competidora", 1)[1]
    assert smooth.startswith(" Mejor familia sin la fundacional: ML. ")
    assert (
        "Competidora SKU Mediana de la razón Hodges-Lehmann Victorias de la campeona "
        "p Wilcoxon p IUT "
        "ML 412 0,912 [0,871; 0,955] 0,905 268 (65,0 %) [60,3 %; 69,5 %] < 0,001 < 0,001 "
        "Fundacional 410 0,958 [0,921; 0,991] 0,951 231 (56,3 %) [51,5 %; 61,1 %] < 0,001 < 0,001"
    ) in smooth
    # A zero loss leaves Hodges-Lehmann undefined and the interval open to infinity.
    assert (
        "Fundacional perdida_cero:hodges_lehmann 95 0,881 [0,000; ∞] — 58 (61,1 %) "
        "[51,1 %; 70,1 %] 0,031 0,031"
    ) in text
    # Too few SKUs: only the count, the median and the wins, and the published cause.
    assert "ML pocos_skus:7<10 7 0,950 — 4 (57,1 %) — —" in text
    assert "Lumpy (Grumosa): " not in text


def test_by_class_shows_the_bh_and_by_counts_of_each_class(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run(env, monkeypatch)
    _publish_confirmed(env)
    html = _dal_client(env).get("/resultados?vista=clase").text
    section = html.split('id="significancia-por-clase"', 1)[1].split("</section>", 1)[0]

    assert (
        "Clase SKU probados Mejores (BH) Peores (BH) Mejores (BY) Peores (BY) Nivel FCR "
        "Smooth (Suave) 410 37 4 29 3 99,55 % "
        "Erratic (Errática) 40 2 0 1 0 99,75 % "
        "Intermittent (Intermitente) 96 0 0 0 0 —"
    ) in _text(section)


def test_by_class_says_when_the_run_was_published_before_the_confirmation(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run(env, monkeypatch)
    _publish_for_the_run(env, eligibility=None)  # no reserve blocks: an older engine
    html = _dal_client(env).get("/resultados?vista=clase").text
    text = _text(html)

    assert "Smooth (Suave) Clásica Menor mediana de r 0,800" in text
    assert "M3 no publicó la confirmación del campeón para esta corrida." in text
    assert "p Holm" not in text
    assert 'id="significancia-por-clase"' not in html


def test_an_invalid_confirmation_row_is_a_visible_problem_and_the_champions_stay(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run(env, monkeypatch)
    smooth_ml = _COMPARISONS[0]
    inverted = (*smooth_ml[:4], smooth_ml[5], smooth_ml[4], *smooth_ml[6:])  # upper < lower
    _publish_confirmed(env, comparisons=[inverted])
    html = _dal_client(env).get("/resultados?vista=clase").text
    text = _text(html)

    assert "Smooth (Suave) Clásica Menor mediana de r 0,800" in text
    assert "No fue posible leer la evidencia M3." in text
    assert "ic_mediana: los extremos están invertidos" in text
    assert 'id="significancia-por-clase"' not in html


def test_by_class_without_a_published_run_or_with_a_rejected_one_shows_the_cause(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run(env, monkeypatch)
    client = _dal_client(env)
    text = _text(client.get("/resultados?vista=clase").text)
    assert "Esta ejecución todavía no tiene evaluación M3." in text
    assert "Campeón por clase" not in text

    _publish_for_the_run(env, estado="RECHAZADA")
    rejected = _text(client.get("/resultados?vista=clase&m3=m3-real-1").text)
    assert "La corrida M3 no está publicada." in rejected
    assert "Campeón por clase" not in rejected
