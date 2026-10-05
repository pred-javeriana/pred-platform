"""Humo del shell: tres superficies, navegación activa y /health."""

from fastapi.testclient import TestClient

from pred_platform.app.main import create_app


def test_three_surfaces_render_and_highlight_navigation() -> None:
    client = TestClient(create_app())
    cases = (
        ("/datos", "Datos", "PRED — Datos"),
        ("/ejecucion", "Ejecución", "PRED — Ejecución"),
        ("/resultados", "Resultados", "PRED — Resultados"),
    )
    for path, label, title in cases:
        response = client.get(path)
        assert response.status_code == 200, path
        html = response.text
        assert title in html
        assert f'href="{path}"' in html
        assert 'aria-current="page"' in html
        assert label in html
        assert "Administración" not in html
        assert "Iniciar sesión" not in html
        assert ">Panel<" not in html
        assert ">Monitoreo<" not in html
        assert ">Validación<" not in html
        assert ">Configuración<" not in html


def test_health_is_unchanged() -> None:
    response = TestClient(create_app()).get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_execution_fragment_does_not_repeat_the_shell() -> None:
    html = TestClient(create_app()).get("/ejecucion/estado").text
    assert "pred-nav" not in html
    assert "G3" in html


def test_catalog_is_not_in_product_navigation() -> None:
    client = TestClient(create_app())
    nav = client.get("/datos").text
    assert "/_dev/componentes" not in nav
    catalog = client.get("/_dev/componentes")
    assert catalog.status_code == 200
    assert "podada" in catalog.text
    assert "Catálogo de componentes" in catalog.text
