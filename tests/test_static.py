"""Tests for the vendored static assets: served locally, pinned, and free of external resources."""

import hashlib
import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pred_platform.app.charts import FAMILY_COLORS
from pred_platform.app.main import create_app

_APP_DIR = Path(__file__).parent.parent / "src" / "pred_platform" / "app"
_STATIC_DIR = _APP_DIR / "static"
_LOCK = json.loads((_STATIC_DIR / "vendor.lock.json").read_text(encoding="utf-8"))["files"]

_OWN_FILES = {
    "css/pred.css",
    "css/live.css",
    "css/results.css",
    "css/wizard_steps.css",
    "js/pred.js",
    "js/live.js",
    "js/charts.js",
    "js/wizard_steps.js",
}

_ASSETS = [
    "css/pico.min.css",
    "js/htmx.min.js",
    "fonts/Archivo-Variable.woff2",
    "fonts/Archivo-Italic.woff2",
    "fonts/JetBrainsMono-Variable.woff2",
    "favicon.svg",
] + sorted(_OWN_FILES)

_TEMPLATES = sorted(
    p.relative_to(_APP_DIR / "templates").as_posix()
    for p in (_APP_DIR / "templates").rglob("*.html")
)

# A network fetch: an absolute (or protocol-relative) URL used as a resource reference.
_EXTERNAL_URL = re.compile(
    r"""
    (?:src|href)\s*=\s*["'](?:https?:)?//    # <script src="//cdn...">, <link href="https://...">
    | url\(\s*["']?(?:https?:)?//             # url(https://...)
    | @import
    """,
    re.VERBOSE,
)


@pytest.fixture()
def client() -> TestClient:
    return TestClient(create_app())


@pytest.mark.parametrize("path", _ASSETS)
def test_asset_is_served_locally(client: TestClient, path: str) -> None:
    response = client.get(f"/static/{path}")
    assert response.status_code == 200
    assert len(response.content) > 0


@pytest.mark.parametrize("path", [p for p in _ASSETS if p.endswith(".woff2")])
def test_fonts_are_woff2(client: TestClient, path: str) -> None:
    assert client.get(f"/static/{path}").content[:4] == b"wOF2"


def test_missing_asset_is_404(client: TestClient) -> None:
    assert client.get("/static/css/does-not-exist.css").status_code == 404


@pytest.mark.parametrize("path", sorted(_LOCK))
def test_vendored_file_matches_lock(path: str) -> None:
    entry = _LOCK[path]
    digest = hashlib.sha256((_STATIC_DIR / path).read_bytes()).hexdigest()
    assert digest == entry["sha256"], f"{path} differs from vendor.lock.json"
    assert (_STATIC_DIR / entry["license_file"]).is_file(), f"missing license for {path}"


def test_lock_covers_every_third_party_file() -> None:
    shipped = {
        p.relative_to(_STATIC_DIR).as_posix()
        for p in _STATIC_DIR.rglob("*")
        if p.is_file() and p.suffix in {".css", ".js", ".woff2"}
    }
    assert shipped - _OWN_FILES == set(_LOCK)


@pytest.mark.parametrize(
    "path",
    ["css/pico.min.css", "js/htmx.min.js"] + sorted(_OWN_FILES),
)
def test_static_code_has_no_external_references(path: str) -> None:
    text = (_STATIC_DIR / path).read_text(encoding="utf-8")
    assert not _EXTERNAL_URL.search(text), f"{path} references an external resource"


@pytest.mark.parametrize("template", _TEMPLATES)
def test_templates_have_no_external_references(template: str) -> None:
    text = (_APP_DIR / "templates" / template).read_text(encoding="utf-8")
    assert not _EXTERNAL_URL.search(text), f"{template} references an external resource"


@pytest.mark.parametrize("family", ["classical", "ml", "dl", "foundation"])
def test_family_color_tokens_match_the_chart_colors(family: str) -> None:
    css = (_STATIC_DIR / "css" / "pred.css").read_text(encoding="utf-8")
    token = re.search(rf"--family-{family}:\s*(#[0-9a-fA-F]{{6}});", css)
    assert token is not None
    assert token.group(1).lower() == FAMILY_COLORS[family].lower()


def test_each_asset_address_carries_a_version_of_its_bytes(client: TestClient) -> None:
    """Una actualización cambia la dirección de cada hoja y script que cambió de bytes, así el
    navegador no reutiliza la copia anterior en caché (#329)."""
    page = client.get("/datos").text
    for path in ("css/pico.min.css", "css/pred.css", "js/htmx.min.js", "js/pred.js"):
        digest = hashlib.sha256((_STATIC_DIR / path).read_bytes()).hexdigest()[:12]
        assert f'"/static/{path}?v={digest}"' in page, path


def test_the_version_follows_the_file_when_it_changes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from pred_platform.app import main

    asset = tmp_path / "css" / "x.css"
    asset.parent.mkdir()
    asset.write_text("a {}", encoding="utf-8")
    monkeypatch.setattr(main, "_STATIC_DIR", tmp_path)
    before = main._static_version("css/x.css")
    asset.write_text("a { color: red; }", encoding="utf-8")
    assert main._static_version("css/x.css") != before
