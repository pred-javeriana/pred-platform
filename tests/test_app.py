"""Tests for the FastAPI application factory."""

import pytest
from fastapi.testclient import TestClient

from pred_platform.app.main import create_app


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
