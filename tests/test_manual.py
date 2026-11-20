"""El manual de usuario: una página aparte con enlace desde el inicio."""

import re

from fastapi.testclient import TestClient

from pred_platform.app.main import create_app

_STEPS = (
    "1. Cargar los datos",
    "2. Elegir los ajustes",
    "3. Revisar y lanzar",
    "4. Seguir la ejecución",
    "5. Leer los resultados",
)


def _visible_text(html: str) -> str:
    """El texto que lee la persona: sin etiquetas ni atributos (las direcciones no cuentan)."""
    return re.sub(r"<[^>]+>", " ", html)


def test_manual_page_loads_with_the_five_steps() -> None:
    response = TestClient(create_app()).get("/manual")
    assert response.status_code == 200
    html = response.text
    assert "<title>Manual de usuario · PRED</title>" in html
    assert "<h1>Manual de usuario</h1>" in html
    for step in _STEPS:
        assert step in html, step
    # El resumen de H9 va antes de los pasos.
    assert html.index("Un análisis de pronóstico toma") < html.index(_STEPS[0])
    # Marca «Manual de usuario» como la página actual, no el inicio.
    assert '<a class="pred-topbar__link" href="/manual" aria-current="page">' in html


def test_manual_page_says_analisis_de_pronostico_not_corrida() -> None:
    text = _visible_text(TestClient(create_app()).get("/manual").text)
    assert "análisis de pronóstico" in text
    assert "corrida" not in text.lower()


def test_home_links_to_the_manual() -> None:
    html = TestClient(create_app()).get("/").text
    assert html.count('href="/manual"') == 2  # la barra superior y el texto del inicio
    assert "manual de usuario" in html


def test_manual_link_is_in_every_page_topbar() -> None:
    client = TestClient(create_app())
    for path in ("/corridas/nueva/datos", "/manual"):
        assert 'href="/manual"' in client.get(path).text, path
