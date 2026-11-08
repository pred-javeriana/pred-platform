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


VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "wbr"}


class Markup(HTMLParser):
    """Recoge atributos semánticos sin depender del orden de atributos ni de píxeles."""

    def __init__(self, text: str) -> None:
        super().__init__()
        self.tags: list[tuple[str, dict[str, str | None]]] = []
        self.ancestry: list[tuple[str, tuple[str, ...]]] = []
        self._open: list[str] = []
        self.feed(text)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append((tag, dict(attrs)))
        self.ancestry.append((tag, tuple(self._open)))
        if tag not in VOID:
            self._open.append(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag in self._open:
            while self._open.pop() != tag:
                pass

    def select(self, tag: str) -> list[dict[str, str | None]]:
        return [attrs for found, attrs in self.tags if found == tag]

    def definition_list_misuse(self) -> list[str]:
        """Cada hijo de un ``dl`` que no es ``dt``, ``dd`` ni un ``div`` que agrupa solo ``dt`` y
        ``dd``: un lector de pantalla deja de leer ese texto como parte de la descripción."""
        found = []
        for tag, above in self.ancestry:
            if above[-1:] == ("dl",) and tag not in ("dt", "dd", "div"):
                found.append(f"{tag} en dl")
            if above[-2:] == ("dl", "div") and tag not in ("dt", "dd"):
                found.append(f"{tag} en div de dl")
        return found


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
    "path",
    [
        "/datos",
        f"/datos?ingest_id={ACCEPTED}",
        "/ejecucion",
        "/resultados",
        "/resultados/sku/FIL-0001",
    ],
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


@pytest.mark.parametrize("scenario", ["normal", "vacio", "errores"])
@pytest.mark.parametrize(
    "path", ["/datos", f"/datos?ingest_id={ACCEPTED}", "/ejecucion", "/resultados/sku/FIL-0001"]
)
def test_definition_lists_hold_only_terms_and_descriptions(scenario: str, path: str) -> None:
    page = Markup(client_for(scenario).get(path).text)
    assert page.definition_list_misuse() == []


def test_scrollable_charts_have_tabindex_and_aria_label() -> None:
    client = client_for("normal")
    datos_html = client.get(f"/datos?ingest_id={ACCEPTED}").text
    datos_page = Markup(datos_html)
    charts_datos = [
        attrs for _, attrs in datos_page.tags if "pred-chart" in (attrs.get("class") or "").split()
    ]
    assert any(
        c.get("tabindex") == "0"
        and c.get("role") == "region"
        and c.get("aria-label") == "Gráfico desplazable de clases de demanda"
        for c in charts_datos
    )

    env = build_templates(TEMPLATES, lambda path: f"/static/{path}").env
    tmpl = env.from_string(
        "{% if champion_svg %}"
        '<figure class="pred-chart" tabindex="0" role="region" '
        'aria-label="Gráfico desplazable de campeones por familia">'
        "{{ champion_svg | safe }}"
        "</figure>"
        "{% endif %}"
    )
    res_html = tmpl.render(champion_svg="<svg></svg>")
    res_page = Markup(res_html)
    charts_res = [
        attrs for _, attrs in res_page.tags if "pred-chart" in (attrs.get("class") or "").split()
    ]
    assert any(
        c.get("tabindex") == "0"
        and c.get("role") == "region"
        and c.get("aria-label") == "Gráfico desplazable de campeones por familia"
        for c in charts_res
    )


def test_results_class_and_family_cells_have_focusable_term_elements() -> None:
    client = client_for("normal")
    html = client.get("/resultados").text
    page = Markup(html)
    terms = [
        attrs
        for _, attrs in page.tags
        if attrs.get("tabindex") == "0"
        and "pred-term" in (attrs.get("class") or "").split()
        and attrs.get("title")
    ]
    assert len(terms) >= 2


def test_picker_options_and_selects_have_utc_titles() -> None:
    from datetime import UTC, datetime

    env = build_templates(TEMPLATES, lambda path: f"/static/{path}").env

    class ExecChoice:
        def __init__(self, id: str, estado: str, date: datetime) -> None:
            self.ejecucion_id = id
            self.estado = estado
            self.iniciada_en = date

    execs = [
        ExecChoice("1", "completada", datetime(2026, 10, 2, 6, 0, tzinfo=UTC)),
        ExecChoice(
            "2",
            "completada_con_fallos",
            datetime(2026, 10, 3, 12, 0, tzinfo=UTC),
        ),
    ]
    tmpl = env.from_string(
        '{% from "components/run_picker.html" import run_picker %}'
        '{{ run_picker(execs, "resumen", shown_id="1") }}'
    )
    picker_html = tmpl.render(execs=execs)
    picker_page = Markup(picker_html)
    options = [attrs for tag, attrs in picker_page.tags if tag == "option" and attrs.get("value")]
    assert len(options) == 2
    for opt in options:
        assert (opt.get("title") or "").endswith("UTC (hora universal coordinada)")

    client = client_for("normal")
    ejec_html = client.get(f"/ejecucion?ingest_id={ACCEPTED}").text
    assert 'id="ingest_id"' in ejec_html
    assert 'title="2026-10-02 06:28:40 UTC (hora universal coordinada)"' in ejec_html


def test_issue_111_funnel_code_breaks_anywhere_in_pred_css() -> None:
    css_path = TEMPLATES.parent / "static/css/pred.css"
    css = css_path.read_text(encoding="utf-8")
    assert ".pred-funnel .mono" in css
    block = css.split(".pred-funnel .mono", 1)[1].split("}", 1)[0]
    assert "overflow-wrap: anywhere" in block


def test_issue_115_ui_review_screenshots_are_present_and_valid() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    review_dir = repo_root / "docs/ui-review"
    expected = [
        "datos.png",
        "detalle-datos.png",
        "ejecucion.png",
        "lanzamiento.png",
        "resultados.png",
        "sku.png",
        "topologia.png",
    ]
    for name in expected:
        img_path = review_dir / name
        assert img_path.is_file(), f"Falta {name}"
        assert img_path.stat().st_size > 50000, f"{name} parece incompleto o vacío"


def test_issue_175_fact_lists_break_long_words_without_overflow() -> None:
    css_path = TEMPLATES.parent / "static/css/pred.css"
    css = css_path.read_text(encoding="utf-8")
    assert ":is(.pred-facts, .pred-context__facts) dd" in css
    dd_block = css.split(":is(.pred-facts, .pred-context__facts) dd", 1)[1].split("}", 1)[0]
    assert "overflow-wrap: anywhere" in dd_block
    assert ":is(.pred-facts, .pred-context__facts) dd > *" in css
    child_block = css.split(":is(.pred-facts, .pred-context__facts) dd > *", 1)[1].split("}", 1)[0]
    assert "min-width: 0" in child_block


def test_issue_178_import_config_field_stretches_to_button() -> None:
    css_path = TEMPLATES.parent / "static/css/pred.css"
    css = css_path.read_text(encoding="utf-8")
    assert "#importar-configuracion .pred-field" in css
    block = css.split("#importar-configuracion .pred-field", 1)[1].split("}", 1)[0]
    assert "flex: 1 1 24rem" in block
    assert "max-width: none" in block


def test_issue_179_launch_advanced_grid_aligns_bottom_and_uses_short_label() -> None:
    css_path = TEMPLATES.parent / "static/css/pred.css"
    css = css_path.read_text(encoding="utf-8")
    assert ".pred-launch__advanced .pred-launch__grid" in css
    block = css.split(".pred-launch__advanced .pred-launch__grid", 1)[1].split("}", 1)[0]
    assert "align-items: end" in block

    tmpl_path = TEMPLATES / "partials/execution_launch.html"
    tmpl_text = tmpl_path.read_text(encoding="utf-8")
    assert '"Configuraciones por SKU y familia"' in tmpl_text
    assert '"Configuraciones a probar por familia"' not in tmpl_text


def test_issue_182_help_terms_have_dotted_border_and_focus_content_in_css() -> None:
    css_path = TEMPLATES.parent / "static/css/pred.css"
    css = css_path.read_text(encoding="utf-8")
    assert ".pred-term" in css
    term_block = css.split(".pred-term", 1)[1].split("}", 1)[0]
    assert "border-bottom: 1px dotted" in term_block
    assert ":is(.pred-term, abbr[title]):is(:hover, :focus)::after" in css
    focus_block = css.split(":is(.pred-term, abbr[title]):is(:hover, :focus)::after", 1)[1].split(
        "}", 1
    )[0]
    assert 'content: " (" attr(title) ")"' in focus_block


def test_issue_185_docs_state_task_limit_and_code_placement() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    ui_flow = (repo_root / "docs/UI_FLOW.md").read_text(encoding="utf-8")
    assert (
        "Debajo se listan solo las tareas fallidas (o, si no hay, las que corren), hasta 5"
        in ui_flow
    )
    assert "no en `title`" in ui_flow.lower()

    design = (repo_root / "docs/DESIGN.md").read_text(encoding="utf-8")
    assert "not in `title`" in design.lower()

    readme = (repo_root / "docs/features/resultados/README.md").read_text(encoding="utf-8")
    assert "not in `title`" in readme.lower()


def test_issue_203_docs_state_seed_group_picks_requested_sku() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    readme = (repo_root / "docs/features/resultados/README.md").read_text(encoding="utf-8")
    assert (
        "except the seed-group page, which picks the one SKU the reader "
        "asked for from the computed group"
    ) in readme


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
    assert "<dt>Origen de los datos</dt><dd>No informado en esta vista</dd>" in html
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


def test_accepted_file_keeps_the_next_action_outside_optional_evidence() -> None:
    html = client_for().get(f"/corridas/nueva/datos?ingest_id={ACCEPTED}").text
    # Siguiente va al pie del paso, fuera de las pestañas de evidencia: ninguna pestaña lo oculta.
    footer = html.index('<nav class="pred-footnav"')
    assert html.index("Continuar a Ajustes", footer) > footer
    assert html.rindex("data-evidence-panel") < footer
    page = Markup(html)
    evidence = [attrs for _, attrs in page.tags if "data-evidence-panel" in attrs]
    evidence_ids = {"diagnostico-completo", "explorar-topologia", "carga", "historial"}
    assert {attrs["id"] for attrs in evidence} == evidence_ids
    assert all("hidden" not in attrs for attrs in evidence)  # Alternativa funcional sin JS.
    assert {
        attrs["data-evidence-target"]
        for attrs in page.select("a")
        if "data-evidence-target" in attrs
    } == evidence_ids


def test_circular_progress_preserves_accessible_values_and_stopped_run_meaning() -> None:
    html = client_for().get("/ejecucion").text
    progress = [attrs for _, attrs in Markup(html).tags if attrs.get("role") == "progressbar"]
    assert len(progress) == 1
    assert progress[0]["aria-valuenow"] == "57.1"
    assert progress[0]["aria-valuetext"] == "4 de 7 tareas · 57,1 %"
    repo = FixtureRepository("normal")
    status = repo.get_run_status("latest")
    assert status.ejecucion and status.progress
    stopped = status.model_copy(
        update={
            "ejecucion": status.ejecucion.model_copy(update={"estado": "detenida"}),
            "progress": status.progress.model_copy(
                update={
                    "tareas_total": 7,
                    "avance_pct": 100.0,
                    "tareas_por_estado": {"exitosa": 0, "fallida": 7},
                }
            ),
        }
    )

    class Stopped(FixtureRepository):
        def get_run_status(self, run_id: str, **kwargs: Any) -> models.RunStatus:
            return stopped

    html = client_for(repo=Stopped()).get("/ejecucion").text
    progress = [attrs for _, attrs in Markup(html).tags if attrs.get("role") == "progressbar"]
    assert progress[0]["aria-valuenow"] == "0.0"
    assert progress[0]["aria-label"] == "Tareas exitosas antes de detenerse"


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


def test_three_thousand_skus_stay_server_paged_with_one_column_per_family() -> None:
    repo = Portfolio()
    path = "/resultados?vista=sku&lista=familias&ejecucion=1&page=2&size=20&q=SKU"
    html = client_for(repo=repo).get(path).text
    assert repo.calls[-1]["page"] == 2 and repo.calls[-1]["size"] == 20
    page = Markup(html)
    # SKU, clase y campeona, y una columna por familia para comparar una familia entre SKU.
    assert len([a for a in page.select("th") if a.get("scope") == "col"]) == 3 + 4
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


@pytest.mark.parametrize("page", ["abc", "-1", "0", "99999999999999999999"])
def test_invalid_history_page_is_recoverable(page: str) -> None:
    repo = History()
    html = client_for(repo=repo).get(f"/datos?history_page={page}").text
    assert repo.calls == [(1, 20)]
    assert f"La página del historial «{page}» no existe; se muestra la primera." in unescape(html)


def test_filter_form_keeps_page_size_after_submission() -> None:
    for path in (
        "/resultados?vista=sku&lista=familias&size=20",
        "/ejecucion?size=20",
        f"/datos?ingest_id={ACCEPTED}&size=20",
    ):
        html = client_for().get(path).text
        assert any(
            a.get("name") == "size" and a.get("value") == "20" for a in Markup(html).select("input")
        )
    # Ordenar o volver a todos los estados conserva la sección elegida al recargar; en
    # Resultados los filtros están siempre a la vista, sobre la lista.
    html = client_for().get("/resultados?vista=sku&lista=familias&sort=valor").text
    assert 'id="filtros-seleccion"' in html and 'value="valor" selected' in html
    for path, disclosure in (
        ("/ejecucion?estado=", "detalle-tareas"),
        (f"/datos?ingest_id={ACCEPTED}&sort=adi", "explorar-topologia"),
    ):
        html = client_for().get(path).text
        tags = Markup(html).tags
        assert any(
            attrs.get("id") == disclosure and ("open" in attrs or "data-evidence-active" in attrs)
            for _, attrs in tags
        )


def test_result_navigation_remains_links_and_preserves_run() -> None:
    html = client_for().get("/resultados?ejecucion=1").text
    assert 'role="tab"' not in html
    hrefs = [unescape(a.get("href") or "") for a in Markup(html).select("a")]
    for chapter in ("resumen", "p1", "p2", "estabilidad", "productos"):
        assert any(h.startswith(f"/corridas/1/resultados/{chapter}") for h in hrefs)
