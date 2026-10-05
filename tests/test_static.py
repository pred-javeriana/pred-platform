"""Tests for the vendored static assets: served locally, pinned, and free of external resources."""

import hashlib
import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pred_platform.app.main import create_app

_APP_DIR = Path(__file__).parent.parent / "src" / "pred_platform" / "app"
_STATIC_DIR = _APP_DIR / "static"
_LOCK = json.loads((_STATIC_DIR / "vendor.lock.json").read_text(encoding="utf-8"))["files"]

_ASSETS = [
    "css/pico.min.css",
    "css/pred.css",
    "js/htmx.min.js",
    "fonts/Archivo-Variable.woff2",
    "fonts/Archivo-Italic.woff2",
    "fonts/JetBrainsMono-Variable.woff2",
]

_TEMPLATES = sorted(p.name for p in (_APP_DIR / "templates").glob("*.html"))

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
    own_files = {"css/pred.css", "js/pred.js"}
    shipped = {
        p.relative_to(_STATIC_DIR).as_posix()
        for p in _STATIC_DIR.rglob("*")
        if p.is_file() and p.suffix in {".css", ".js", ".woff2"}
    }
    assert shipped - own_files == set(_LOCK)


@pytest.mark.parametrize(
    "path", ["css/pico.min.css", "css/pred.css", "js/htmx.min.js", "js/pred.js"]
)
def test_static_code_has_no_external_references(path: str) -> None:
    text = (_STATIC_DIR / path).read_text(encoding="utf-8")
    assert not _EXTERNAL_URL.search(text), f"{path} references an external resource"


@pytest.mark.parametrize("template", _TEMPLATES)
def test_templates_have_no_external_references(template: str) -> None:
    text = (_APP_DIR / "templates" / template).read_text(encoding="utf-8")
    assert not _EXTERNAL_URL.search(text), f"{template} references an external resource"
