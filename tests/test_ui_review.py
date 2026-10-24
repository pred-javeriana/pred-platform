"""Regresiones D2: semántica accesible, evidencia progresiva y listas acotadas."""

from __future__ import annotations

from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from pred_platform.app.main import create_app
from pred_platform.app.templating import build_templates
from pred_platform.config import Settings
from pred_platform.contract import models
from pred_platform.data.fixtures import FixtureRepository
from pred_platform.data.query import PageRequest, paginate

ACCEPTED = "b1831d4585b72d7c7bcb98ada7bae70e7b229449edf74a8a7a7f39e6552df56e"
TEMPLATES = Path(__file__).resolve().parents[1] / "src/pred_platform/app/templates"


class Markup(HTMLParser):
    """Recoge atributos semánticos sin depender del orden de atributos ni de píxeles."""

    def __init__(self, text: str) -> None:
        super().__init__()
        self.tags: list[tuple[str, dict[str, str | None]]] = []
        self.feed(text)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append((tag, dict(attrs)))

    def select(self, tag: str) -> list[dict[str, str | None]]:
        return [attrs for found, attrs in self.tags if found == tag]


def client_for(scenario: str = "normal", repo: Any = None) -> TestClient:
    app = create_app(
        Settings(
            data_root=Path("unused"),
            db_path=Path("unused/pred.db"),
            data_source="fixture",
            fixture_scenario=scenario,
            worker="off",
        )
    )
    if repo is not None:
        app.state.repository = repo
    return TestClient(app)


@pytest.mark.parametrize("scenario", ["normal", "vacio", "errores"])
@pytest.mark.parametrize(
    "path", ["/datos", "/ejecucion", "/resultados", "/resultados/sku/FIL-0001"]
)
def test_routes_keep_unique_ids_labels_and_skip_link(scenario: str, path: str) -> None:
    response = client_for(scenario).get(path)
    assert response.status_code in (200, 404)
    page = Markup(response.text)
    ids = [attrs["id"] for _, attrs in page.tags if attrs.get("id")]
    assert len(ids) == len(set(ids))
    assert any(a.get("href") == "#contenido" for a in page.select("a"))
    assert any(
        a.get("id") == "contenido" and a.get("tabindex") == "-1" for a in page.select("main")
    )
    labels = {attrs.get("for") for attrs in page.select("label")}
    for tag in ("input", "select"):
        for attrs in page.select(tag):
            if attrs.get("type") == "hidden":
                continue
            assert attrs.get("id") in labels
            for reference in (attrs.get("aria-describedby") or "").split():
                assert reference in ids
    for attrs in page.select("th"):
        assert attrs.get("scope") in ("col", "row")
    for _, attrs in page.tags:
        if attrs.get("class") == "pred-table-wrap":
            assert attrs.get("role") == "region"
            assert attrs.get("tabindex") == "0"
            assert attrs.get("aria-label")


@pytest.mark.parametrize("input_type", ["text", "select", "file", "checkbox"])
def test_error_and_hint_are_both_associated(input_type: str) -> None:
    env = build_templates(TEMPLATES, lambda path: f"/static/{path}").env
    html = env.from_string(
        '{% from "components/forms.html" import field %}'
        '{{ field("dato", "Dato", input_type=kind, hint="Ayuda", error="Corrija este dato", '
        'options=[("a", "A")], field_id="dato-unico") }}'
    ).render(kind=input_type)
    page = Markup(html)
    control = (page.select("input") or page.select("select"))[0]
    assert control["aria-invalid"] == "true"
    assert set((control.get("aria-describedby") or "").split()) == {
        "dato-unico-hint",
        "dato-unico-error",
    }
    assert {a.get("id") for a in page.select("p")} == {"dato-unico-hint", "dato-unico-error"}


def test_trials_and_configuration_start_closed_and_windows_are_requested() -> None:
    client = client_for()
    html = client.get("/resultados/sku/FIL-0001").text
    details = Markup(html).select("details")
    technical = [
        d for d in details if (d.get("id") or "").startswith(("ensayos-", "configuracion-"))
    ]
    assert technical and all("open" not in d for d in technical)
    assert "Evidencia retrospectiva" in html
    assert "Origen real o sintético: no informado" in html
    assert "Entrenamiento" not in html
    full = client.get("/resultados/sku/FIL-0001?ventanas=1").text
    requested = [
        d for d in Markup(full).select("details") if (d.get("id") or "").startswith("ventanas-")
    ]
    assert requested and all("open" in d for d in requested)
    for rendered in (html, full):
        for _, attrs in Markup(rendered).tags:
            if "pred-evidence-scroll" in (attrs.get("class") or "").split():
                assert not {"role", "aria-label", "tabindex"}.intersection(attrs)


def test_execution_live_region_is_small_and_pause_control_is_outside_fragment() -> None:
    html = client_for().get("/ejecucion").text
    page = Markup(html)
    bodies = [a for _, a in page.tags if a.get("id") == "ejecucion-cuerpo"]
    assert bodies[0].get("aria-live") == "off"
    assert [a for _, a in page.tags if a.get("aria-live") == "polite"] == [
        a for _, a in page.tags if a.get("id") == "ejecucion-anuncio"
    ]
    assert html.index('id="pausar-actualizacion"') < html.index('id="ejecucion-cuerpo"')
    fragment = client_for().get("/ejecucion/estado").text
    assert 'id="pausar-actualizacion"' not in fragment
    links = Markup(fragment).select("a")
    assert any("estado=fallida" in (a.get("href") or "") for a in links)
    assert any("estado=no_ejecutable" in (a.get("href") or "") for a in links)


class Portfolio(FixtureRepository):
    """Simula un contrato paginado grande sin ejecutar modelos ni fabricar métricas."""

    def __init__(self) -> None:
        super().__init__("normal")
        self.calls: list[dict[str, Any]] = []

    def list_sku_selections(self, run_id: str, **kwargs: Any) -> models.SkuSelectionList:
        self.calls.append(kwargs)
        base = super().list_sku_selections(run_id)
        rows = [
            base.items[0].model_copy(update={"sku_id": f"SKU-{'X' * 160}-{i:04}"})
            for i in range(3000)
        ]
        items, page = paginate(rows, PageRequest(kwargs["page"], kwargs["size"]))
        return base.model_copy(update={"items": items, "page": page})


def test_three_thousand_skus_stay_server_paged_with_four_primary_columns() -> None:
    repo = Portfolio()
    html = client_for(repo=repo).get("/resultados?ejecucion=1&page=2&size=20&q=SKU").text
    assert repo.calls[-1]["page"] == 2 and repo.calls[-1]["size"] == 20
    page = Markup(html)
    assert len([a for a in page.select("th") if a.get("scope") == "col"]) == 4
    assert len([a for a in page.select("th") if a.get("scope") == "row"]) == 20
    assert "3.000 SKU" in html and "-0020" in html and "-0019" not in html
    hrefs = [unescape(a.get("href") or "") for a in page.select("a")]
    assert any(
        "page=3" in h and "size=20" in h and "q=SKU" in h and "ejecucion=1" in h for h in hrefs
    )


class History(FixtureRepository):
    def __init__(self) -> None:
        super().__init__("normal")
        self.calls: list[tuple[int, int]] = []

    def list_ingests(self, *, page: int = 1, size: int = 50) -> models.IngestList:
        self.calls.append((page, size))
        base = super().list_ingests()
        rows = [base.items[0].model_copy(update={"name": f"ingesta-{i:03}.csv"}) for i in range(45)]
        items, paging = paginate(rows, PageRequest(page, size))
        return base.model_copy(update={"items": items, "page": paging})


def test_history_has_independent_server_paging_and_keeps_selected_ingest() -> None:
    repo = History()
    html = client_for(repo=repo).get(f"/datos?ingest_id={ACCEPTED}&history_page=2").text
    assert repo.calls == [(2, 20)]
    assert "ingesta-020.csv" in html and "ingesta-019.csv" not in html
    hrefs = [unescape(a.get("href") or "") for a in Markup(html).select("a")]
    assert any("history_page=3" in h and f"ingest_id={ACCEPTED}" in h for h in hrefs)


@pytest.mark.parametrize("page", ["abc", "-1", "0"])
def test_invalid_history_page_is_recoverable(page: str) -> None:
    repo = History()
    html = client_for(repo=repo).get(f"/datos?history_page={page}").text
    assert repo.calls == [(1, 20)]
    assert "La página del historial debe ser un entero mayor que cero" in html


def test_filter_form_keeps_page_size_after_submission() -> None:
    for path in (
        "/resultados?size=20",
        "/ejecucion?size=20",
        f"/datos?ingest_id={ACCEPTED}&size=20",
    ):
        html = client_for().get(path).text
        assert any(
            a.get("name") == "size" and a.get("value") == "20" for a in Markup(html).select("input")
        )


def test_result_navigation_remains_links_and_preserves_run() -> None:
    html = client_for().get("/resultados?ejecucion=1").text
    assert 'role="tab"' not in html
    hrefs = [unescape(a.get("href") or "") for a in Markup(html).select("a")]
    for vista in ("resumen", "clase", "sku"):
        assert any(f"vista={vista}" in h and "ejecucion=1" in h for h in hrefs)
