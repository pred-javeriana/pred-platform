"""Tests for the FastAPI application factory."""

import re
import sqlite3
from contextlib import closing
from pathlib import Path
from urllib.parse import urlparse

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient

from pred_platform.app.main import create_app
from pred_platform.config import Settings
from pred_platform.dal.migrate import SchemaTooNew
from pred_platform.data.deps import get_repository
from pred_platform.data.repository import DataRepository


@pytest.fixture()
def client() -> TestClient:
    return TestClient(create_app())


def test_health(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_root_redirects_to_datos(client: TestClient) -> None:
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "/datos"


def test_datos_is_spanish_light_themed_page(client: TestClient) -> None:
    html = client.get("/datos").text
    assert '<html lang="es" data-theme="light">' in html


def test_datos_links_only_host_relative_local_assets(client: TestClient) -> None:
    html = client.get("/datos").text
    references = re.findall(r'(?:href|src)="([^"]+)"', html)
    assert references, "the page should link its stylesheets and script"
    for ref in references:
        url = urlparse(ref)
        if url.path in {"/datos", "/ejecucion", "/resultados"}:
            continue
        if url.path.startswith("#") or not url.path:
            continue
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


# ------------------------------------------------------------------ schema migration on start
def _user_version(path: Path) -> int:
    with closing(sqlite3.connect(path)) as conn:
        return conn.execute("PRAGMA user_version").fetchone()[0]


def test_starting_the_server_with_the_real_source_creates_and_migrates_the_database(
    tmp_path: Path,
) -> None:
    db = tmp_path / "not_yet" / "pred.db"
    application = create_app(Settings(data_root=tmp_path, db_path=db))
    assert not db.exists()  # building the app is not starting it

    with TestClient(application) as client:  # entering the client runs the lifespan
        assert client.get("/health").status_code == 200

    assert _user_version(db) == 1
    assert [p.name for p in db.parent.glob("*.bak")] == []  # a new database is not backed up


def test_starting_with_fixtures_never_touches_the_disk(tmp_path: Path) -> None:
    db = tmp_path / "not_yet" / "pred.db"
    settings = Settings(data_root=tmp_path, db_path=db, data_source="fixture")
    with TestClient(create_app(settings)) as client:
        assert client.get("/health").status_code == 200
    assert not db.parent.exists()


def test_starting_with_a_database_newer_than_the_code_fails_clearly(tmp_path: Path) -> None:
    db = tmp_path / "pred.db"
    with closing(sqlite3.connect(db)) as conn:
        conn.execute("PRAGMA user_version = 99")
    before = db.read_bytes()

    with (
        pytest.raises(SchemaTooNew, match="version 99"),
        TestClient(create_app(Settings(data_root=tmp_path, db_path=db))),
    ):
        pass

    assert db.read_bytes() == before


def test_starting_twice_does_not_migrate_or_back_up_again(tmp_path: Path) -> None:
    settings = Settings(data_root=tmp_path, db_path=tmp_path / "pred.db")
    for _ in range(2):
        with TestClient(create_app(settings)):
            pass
    assert _user_version(settings.db_path) == 1
    assert list(tmp_path.glob("*.bak")) == []
