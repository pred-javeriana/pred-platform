"""Tests for the FastAPI application factory."""

import re
from pathlib import Path
from urllib.parse import urlparse

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient

from pred_platform.app.main import create_app
from pred_platform.config import Settings
from pred_platform.data.deps import get_repository
from pred_platform.data.repository import DataRepository


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


def test_the_repository_follows_the_configured_source(tmp_path: Path) -> None:
    real = create_app(Settings(data_root=tmp_path, db_path=tmp_path / "pred.db"))
    assert real.state.repository.source == "dal"
    sample = create_app(
        Settings(
            data_root=tmp_path,
            db_path=tmp_path / "pred.db",
            data_source="fixture",
            fixture_scenario="vacio",
        )
    )
    assert sample.state.repository.source == "fixture"
    assert sample.state.repository.scenario.name == "vacio"


def test_booting_with_the_real_source_does_not_create_the_database(tmp_path: Path) -> None:
    db = tmp_path / "pred.db"
    client = TestClient(create_app(Settings(data_root=tmp_path, db_path=db)))
    assert client.get("/health").status_code == 200
    assert not db.exists()


def test_a_view_can_depend_on_the_repository(tmp_path: Path) -> None:
    application = create_app(
        Settings(data_root=tmp_path, db_path=tmp_path / "x.db", data_source="fixture")
    )

    @application.get("/_probe")
    def probe(repository: DataRepository = Depends(get_repository)) -> dict[str, object]:  # noqa: B008
        listing = repository.list_ingests()
        return {"source": repository.source, "total": listing.page.total}

    body = TestClient(application).get("/_probe").json()
    assert body == {"source": "fixture", "total": 2}
