"""Tests for the FastAPI application factory."""

import re
from pathlib import Path
from urllib.parse import urlparse

import pytest
from fastapi.testclient import TestClient

from pred_platform.app.main import create_app
from pred_platform.config import Settings


@pytest.fixture()
def client() -> TestClient:
    return TestClient(create_app())


def test_health(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_index_returns_html(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "PRED" in response.text
    assert "construcción" in response.text


def test_index_is_spanish_light_themed_page(client: TestClient) -> None:
    html = client.get("/").text
    assert '<html lang="es" data-theme="light">' in html


def test_index_links_only_host_relative_local_assets(client: TestClient) -> None:
    html = client.get("/").text
    references = re.findall(r'(?:href|src)="([^"]+)"', html)
    assert references, "the page should link its stylesheets and script"
    for ref in references:
        url = urlparse(ref)
        assert not url.scheme and not url.netloc, f"{ref} is not host-relative"
        assert url.path.startswith("/static/"), ref
        assert client.get(url.path).status_code == 200, ref


def test_settings_are_available_on_the_app() -> None:
    settings = Settings(data_root=Path("somewhere"), db_path=Path("somewhere/x.db"))
    assert create_app(settings).state.settings is settings
