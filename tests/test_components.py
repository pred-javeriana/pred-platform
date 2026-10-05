"""Renderizado de los componentes B2: variantes, estados y ausencia de CDN."""

from pathlib import Path

from fastapi.templating import Jinja2Templates

from pred_platform.app.presentation import describe_status, format_count

_TEMPLATES = Path(__file__).parent.parent / "src" / "pred_platform" / "app" / "templates"


def _templates() -> Jinja2Templates:
    templates = Jinja2Templates(directory=str(_TEMPLATES))
    templates.env.globals["static_url"] = lambda path: f"/static/{path}"
    return templates


def _catalog_html() -> str:
    context = {
        "active_surface": None,
        "task_statuses": [
            describe_status("task", code)
            for code in ("pendiente", "ejecutando", "exitosa", "fallida", "no_ejecutable")
        ],
        "trial_statuses": [
            describe_status("trial", code)
            for code in ("pendiente", "corriendo", "completado", "podado", "fallido")
        ],
        "verdict_statuses": [
            describe_status("verdict", code) for code in ("mantiene", "parcial", "falla")
        ],
        "progress_completed": format_count(821),
        "progress_total": format_count(1284),
        "progress_percent": 64,
        "metric_items": [
            {"label": "SKU", "value": "110", "tone": "data"},
            {"label": "Intermitentes", "value": "98", "tone": None},
            {"label": "Lumpy", "value": "12", "tone": None},
            {"label": "Candidatos", "value": "203", "tone": None},
        ],
        "tab_items": [
            {"id": "resumen", "label": "Resumen", "href": "#resumen"},
            {"id": "clase", "label": "Por clase", "href": "#clase"},
            {"id": "sku", "label": "Por SKU", "href": "#sku"},
        ],
    }
    return _templates().get_template("pages/catalog.html").render(context)


def test_catalog_renders_every_task_and_trial_label() -> None:
    html = _catalog_html()
    for label in ("pendiente", "ejecutando", "exitosa", "fallida", "no ejecutable"):
        assert label in html
    assert "podada" in html
    assert html.count("podada") >= 1
    assert "i-prune" in html
    assert "se sostiene parcialmente" in html


def test_catalog_does_not_paint_pruned_as_failure_class() -> None:
    html = _catalog_html()
    assert "pred-status--pruned" in html
    pruned_at = html.index("podada")
    nearby = html[max(0, pruned_at - 200) : pruned_at + 20]
    assert "pred-status--err" not in nearby


def test_catalog_includes_progress_counts_and_percent() -> None:
    html = _catalog_html()
    assert "821 / 1.284 tareas" in html
    assert "64 %" in html
    assert 'data-pred-progress="64"' in html


def test_catalog_has_no_cdn() -> None:
    html = _catalog_html()
    assert "https://" not in html
    assert "cdn" not in html.lower()
