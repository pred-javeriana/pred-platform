"""Humo del shell: el inicio, los cinco pasos con su barra, la navegación y /health."""

from fastapi.testclient import TestClient

from pred_platform.app.main import create_app

_FORBIDDEN = (
    "Administración",
    "Iniciar sesión",
    ">Panel<",
    ">Monitoreo<",
    ">Validación<",
    ">Configuración<",
)


def test_home_renders_with_corridas_highlighted() -> None:
    response = TestClient(create_app()).get("/")
    assert response.status_code == 200
    html = response.text
    assert "<title>Corridas · PRED</title>" in html
    assert '<a class="pred-topbar__link" href="/" aria-current="page">Corridas</a>' in html
    assert 'href="/corridas/nueva/datos"' in html
    # El inicio no es un paso: no tiene barra de pasos ni pie de navegación.
    assert "pred-rail" not in html
    assert "pred-footnav" not in html
    for text in _FORBIDDEN:
        assert text not in html


def test_each_step_of_a_new_run_renders_inside_the_shell() -> None:
    client = TestClient(create_app())
    cases = (
        ("datos", "Datos", "Paso 1 de 5"),
        ("ajustes", "Ajustes", "Paso 2 de 5"),
        ("revisar", "Revisar y lanzar", "Paso 3 de 5"),
        ("ejecucion", "Ejecución", "Paso 4 de 5"),
        ("resultados", "Resultados", "Paso 5 de 5"),
    )
    for key, label, eyebrow in cases:
        response = client.get(f"/corridas/nueva/{key}")
        assert response.status_code == 200, key
        html = response.text
        assert f"<title>{label} · Corrida nueva · PRED</title>" in html, key
        assert (
            '<nav class="pred-rail" id="barra-pasos" aria-label="Pasos de la corrida">' in html
        ), key
        # Below 900 px this summary replaces the rail.
        assert '<nav class="pred-rail-compact" id="resumen-paso" ' in html, key
        assert 'aria-label="Paso actual de la corrida">' in html, key
        assert eyebrow in html, key
        assert f"<h1>{label}</h1>" in html, key
        # La barra superior nombra la corrida y no marca Corridas como página actual.
        assert "Corrida nueva (sin lanzar)" in html, key
        assert 'aria-current="page"' not in html, key
        for text in _FORBIDDEN:
            assert text not in html, key


def test_health_is_unchanged() -> None:
    response = TestClient(create_app()).get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_execution_fragment_does_not_repeat_the_shell() -> None:
    html = TestClient(create_app()).get("/ejecucion/estado").text
    assert "pred-topbar" not in html
    assert "pred-rail" not in html
    # With the real DAL and no run launched yet, the surface says so (G3 no longer blocks it).
    assert "No existe una ejecución activa" in html
    assert "G3" not in html


def test_catalog_is_not_in_product_navigation() -> None:
    client = TestClient(create_app())
    nav = client.get("/corridas/nueva/datos").text
    assert "/_dev/componentes" not in nav
    catalog = client.get("/_dev/componentes")
    assert catalog.status_code == 200
    assert "Podada" in catalog.text
    assert "Catálogo de componentes" in catalog.text
