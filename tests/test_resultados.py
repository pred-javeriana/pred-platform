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
    assert "mae 1,546" in _text(html)
    assert "No informado" in html
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
    html = client.get("/resultados?vista=resumen").text

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
    assert pruned is not None and "podada" in pruned.group(0) and "#i-prune" in pruned.group(0)
    assert failed is not None and "fallida" in failed.group(0) and "#i-x" in failed.group(0)
    text = _text(html)
    assert "poda_semantica:prediccion_nula" in text
    assert "ajuste_fallido: AjusteModeloError: no converge" in text
    assert "Un ensayo podado no es un fallo" in text


def test_a_failed_family_shows_its_error_and_an_unrunnable_one_its_state() -> None:
    client, _ = _client("errores")
    text = _text(client.get("/resultados/sku/FIL-0001").text)

    assert "fallida" in text and "no ejecutable" in text
    assert "Etapa" in text and re.search(r"[a-z]+\.[a-z_]+", text)
    assert "Traceback" not in text


def test_the_policy_exclusions_are_explained_with_their_reason() -> None:
    client, _ = _client("normal")
    text = _text(client.get("/resultados/sku/FIL-0004").text)

    assert "excluida" in text
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
    assert "Ejecución #1" in text and "completada con fallos" in text
    assert "La acción está deshabilitada" not in text  # the DAL has all three capabilities

    detail = client.get("/resultados/sku/FIL-0002?ventanas=1")
    detail_text = _text(detail.text)
    assert detail.status_code == 200
    assert "El modelo no pudo ajustarse." in detail_text  # model.fit_failed, from the failed ml
    assert "ajuste_fallido" in detail_text and "podada" in detail_text and "fallida" in detail_text
    assert "Entrenamiento" in detail_text  # windows of the classical family that did succeed
    assert "disperso y estable" in detail_text


def test_the_real_dal_answers_for_a_run_that_has_not_finished(env: Env) -> None:
    from tests.test_worker import SMALL, enqueue_run

    enqueue_run(env.db, env.ingest_id, SMALL)
    text = _text(_dal_client(env).get("/resultados").text)

    assert "Todavía no hay selección por SKU." in text
    assert "cuando termine la ejecución" in text
    assert "Ejecución #1" in text and "pendiente" in text


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
