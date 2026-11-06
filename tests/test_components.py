"""Renderizado de los componentes B2: variantes, estados y ausencia de CDN."""

from pathlib import Path

from fastapi.testclient import TestClient

from pred_platform.app.main import create_app
from pred_platform.config import Settings


def _catalog_html() -> str:
    """El catálogo tal como lo sirve la aplicación, con sus filtros y formatos."""
    app = create_app(
        Settings(
            data_root=Path("unused"),
            db_path=Path("unused/pred.db"),
            data_source="fixture",
            worker="off",
        )
    )
    response = TestClient(app).get("/_dev/componentes")
    assert response.status_code == 200
    return response.text


def test_catalog_renders_every_task_and_trial_label() -> None:
    html = _catalog_html()
    for label in ("Pendiente", "Ejecutando", "Exitosa", "Fallida", "No ejecutable"):
        assert label in html
    assert "Podada" in html
    assert html.count("Podada") >= 1
    assert "i-prune" in html
    # The five verdicts read by their Spanish label; the contract code stays out of sight.
    for verdict in (
        "Validado",
        "Exploratorio",
        "Evidencia insuficiente",
        "No evaluable",
        "Error técnico",
    ):
        assert verdict in html
    assert "se sostiene" not in html


def test_catalog_does_not_paint_pruned_as_failure_class() -> None:
    html = _catalog_html()
    assert "pred-status--pruned" in html
    pruned_at = html.index("Podada")
    nearby = html[max(0, pruned_at - 200) : pruned_at + 20]
    assert "pred-status--err" not in nearby


def test_catalog_includes_progress_counts_and_percent() -> None:
    html = _catalog_html()
    assert "821 / 1.284 tareas" in html
    assert "64,0 %" in html
    assert 'data-pred-progress="64"' in html


def test_catalog_has_no_cdn() -> None:
    html = _catalog_html()
    assert "https://" not in html
    assert "cdn" not in html.lower()
