"""Tests for the FastAPI application factory."""

import os
import re
import sqlite3
import subprocess
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path
from urllib.parse import urlparse

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient

from pred_platform.app.main import DatabaseUnavailable, create_app
from pred_platform.config import Settings
from pred_platform.dal.migrate import SchemaTooNew, load_migrations
from pred_platform.data.deps import get_repository
from pred_platform.data.repository import DataRepository

LATEST_SCHEMA = len(load_migrations())


@pytest.fixture()
def client() -> TestClient:
    return TestClient(create_app())


def test_health(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_answers_head_without_body(client: TestClient) -> None:
    response = client.head("/health")
    assert response.status_code == 200
    assert response.content == b""


def test_an_unknown_address_is_a_pred_page_in_a_browser_and_json_elsewhere(
    client: TestClient,
) -> None:
    page = client.get("/no-existe", headers={"Accept": "text/html,application/xhtml+xml"})
    assert page.status_code == 404
    assert "<h1>Página no encontrada</h1>" in page.text
    assert re.search(r'href="/">\s*Ir a Corridas\s*</a>', page.text)
    assert "Not Found" not in page.text
    # A probe or a script that does not ask for HTML keeps the JSON answer.
    probe = client.get("/no-existe")
    assert probe.status_code == 404 and probe.json() == {"detail": "Not Found"}


def test_root_is_the_corridas_home(client: TestClient) -> None:
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 200
    assert "<title>Corridas · PRED</title>" in response.text


def test_datos_is_spanish_light_themed_page(client: TestClient) -> None:
    html = client.get("/corridas/nueva/datos").text
    assert '<html lang="es" data-theme="light">' in html


def test_datos_links_only_host_relative_local_assets(client: TestClient) -> None:
    html = client.get("/corridas/nueva/datos").text
    references = re.findall(r'(?:href|src)="([^"]+)"', html)
    assert references, "the page should link its stylesheets and script"
    for ref in references:
        url = urlparse(ref)
        if url.path == "/" or url.path.startswith("/corridas/"):
            assert not url.scheme and not url.netloc, f"{ref} is not host-relative"
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

    assert _user_version(db) == LATEST_SCHEMA
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
    assert _user_version(settings.db_path) == LATEST_SCHEMA
    assert list(tmp_path.glob("*.bak")) == []


def _read_only_copy(tmp_path: Path, journal: str, *, wal_file: bool = False) -> Settings:
    """A migrated database copied with ``.backup`` into a folder without write permission.

    ``wal_file`` leaves an empty ``-wal`` file beside it, as a copy taken with ``cp`` would.
    """
    settings = Settings(data_root=tmp_path / "data", db_path=tmp_path / "pred.db")
    with TestClient(create_app(settings)):
        pass
    folder = tmp_path / "copia"
    folder.mkdir()
    copy = folder / "pred.db"
    with closing(sqlite3.connect(settings.db_path)) as source, closing(sqlite3.connect(copy)) as c:
        source.backup(c)
        c.execute(f"PRAGMA journal_mode={journal}")
    if wal_file:
        copy.with_name("pred.db-wal").touch()
    copy.chmod(0o444)
    folder.chmod(0o555)
    return Settings(data_root=tmp_path / "data", db_path=copy)


def test_a_database_path_that_names_a_folder_stops_startup_saying_so(tmp_path: Path) -> None:
    settings = Settings(data_root=tmp_path / "data", db_path=tmp_path)

    with (
        pytest.raises(DatabaseUnavailable, match="nombra la carpeta") as raised,
        TestClient(create_app(settings)),
    ):
        pass
    assert "journal_mode" not in str(raised.value)


def test_a_wal_copy_with_its_wal_file_in_a_read_only_folder_stops_startup_with_what_to_do(
    tmp_path: Path,
) -> None:
    settings = _read_only_copy(tmp_path, "WAL", wal_file=True)
    with (
        _locked_folder(settings.db_path.parent),
        pytest.raises(DatabaseUnavailable, match="PRAGMA journal_mode=DELETE"),
        TestClient(create_app(settings)),
    ):
        pass


@contextmanager
def _locked_folder(folder: Path) -> Iterator[None]:
    """A folder nobody can write, not even root: ``chattr +i`` where ``chmod`` is not enough."""
    folder.chmod(0o555)
    locked = False
    try:
        if os.access(folder, os.W_OK):  # root ignores the permission bits
            locked = subprocess.run(["chattr", "+i", str(folder)], check=False).returncode == 0
            if not locked or os.access(folder, os.W_OK):
                pytest.skip("no se pudo quitar el permiso de escritura de la carpeta")
        yield
    finally:
        if locked:
            subprocess.run(["chattr", "-i", str(folder)], check=False)
        folder.chmod(0o755)


def test_a_consolidated_wal_copy_in_a_read_only_folder_serves_without_writing(
    tmp_path: Path,
) -> None:
    settings = _read_only_copy(tmp_path, "WAL")
    folder = settings.db_path.parent
    before = settings.db_path.read_bytes()
    with _locked_folder(folder):
        with TestClient(create_app(settings)) as client:
            home = client.get("/")
            datos = client.get("/datos")
            ejecucion = client.post("/ejecucion/reanudar", data={"ejecucion": "1"})
        assert sorted(p.name for p in folder.iterdir()) == ["pred.db"]
    assert settings.db_path.read_bytes() == before
    assert home.status_code == 200
    assert datos.status_code == 200 and "no cargar uno nuevo" in datos.text
    assert ejecucion.status_code == 200 and "La base de datos es de solo lectura" in ejecucion.text


def test_an_outdated_wal_copy_in_a_read_only_folder_says_it_cannot_be_migrated(
    tmp_path: Path,
) -> None:
    settings = _read_only_copy(tmp_path, "WAL")
    folder = settings.db_path.parent
    folder.chmod(0o755)
    settings.db_path.chmod(0o644)
    with closing(sqlite3.connect(settings.db_path)) as conn:
        conn.execute(f"PRAGMA user_version = {LATEST_SCHEMA - 1}")
    settings.db_path.chmod(0o444)
    assert sorted(p.name for p in folder.iterdir()) == ["pred.db"]
    with (
        _locked_folder(folder),
        pytest.raises(DatabaseUnavailable, match="no se puede migrar"),
        TestClient(create_app(settings)),
    ):
        pass


def test_a_rollback_journal_copy_in_a_read_only_folder_serves_with_actions_off(
    tmp_path: Path,
) -> None:
    settings = _read_only_copy(tmp_path, "DELETE")
    with _locked_folder(settings.db_path.parent), TestClient(create_app(settings)) as client:
        ejecucion = client.post("/ejecucion/reanudar", data={"ejecucion": "1"})
        datos = client.get("/datos")

    assert ejecucion.status_code == 200 and "La base de datos es de solo lectura" in ejecucion.text
    assert datos.status_code == 200 and "no cargar uno nuevo" in datos.text
