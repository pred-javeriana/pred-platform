"""Superficie /resultados, vista «Por SKU» y detalle (C2): lo que se ve y lo que se delega."""

from __future__ import annotations

import json
import re
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
from pred_platform.contract.errors import InvalidQuery
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


def test_summary_tabs_say_when_the_champion_exists_but_the_view_does_not() -> None:
    client, _ = _client("normal")  # capabilities.todo_disponible
    html = client.get("/resultados?vista=clase").text

    assert "Esta vista todavía no está construida." in html
    assert "Todavía no hay campeón por SKU." not in html


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
        "Excluidos antes de M3 1",
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
    assert "RMSE campeón" not in text and "2026-03-15" not in text
    [link] = re.findall(r'href="([^"]*reserva=1[^"]*)"', html)
    assert "m3=m3-real-1" in link
