"""Cargar un CSV desde el navegador: el motor lo ingiere (``classify``) y el informe llega al DAL.

El motor falso (``tests/fake_engine.py``) deja el informe según una marca en el contenido del
archivo: BADHEADER, BADVALUE, CONFLICT, BOOM o SLOW.
"""

from __future__ import annotations

import hashlib
import inspect
import os
import re
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from pred_platform.app.main import create_app
from pred_platform.contract.errors import EngineUnavailable, InvalidQuery
from pred_platform.data.factory import build_repository
from pred_platform.worker import ingesta
from pred_platform.worker.ingesta import IngestLaunchError, safe_name, submit_csv
from tests.test_worker import Env
from tests.test_worker import env as env  # noqa: F401  (the fixture)

HEADER = b"sku_id,timestamp,demand_qty,lead_time_days\n"


def _csv(marker: str = "") -> bytes:
    return HEADER + b"FIL-0001,2026-01-01,4,7\n" + (f"# {marker}\n".encode() if marker else b"")


def _sha(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


# --------------------------------------------------------------------------- el ejecutor
def test_an_accepted_csv_ends_as_a_readable_report_and_leaves_no_temporary_file(env: Env) -> None:
    repo = build_repository(env.settings)
    content = _csv("ok")

    report = repo.submit_ingest("ventas.csv", content)

    assert report.status == "accepted" and report.ingest_id == _sha(content)
    assert report.source_file.name == "ventas.csv"
    assert report.published is not None and report.published.parquet_path.endswith(".parquet")
    assert repo.get_topology_report(report.ingest_id).availability.status == "available"
    assert report.ingest_id in {i.ingest_id for i in repo.list_ingests().items}
    assert list((env.settings.data_root / "staging").iterdir()) == []


def test_a_csv_with_wrong_headers_is_a_rejected_report_not_an_error(env: Env) -> None:
    report = build_repository(env.settings).submit_ingest("raro.csv", _csv("BADHEADER"))

    assert report.status == "rejected"
    assert report.error is not None and report.error.code == "ingest.header_rejected"


def test_a_csv_with_invalid_values_is_a_failed_report_with_its_error(env: Env) -> None:
    report = build_repository(env.settings).submit_ingest("malo.csv", _csv("BADVALUE"))

    assert report.status == "failed"
    assert report.error is not None and report.error.code == "ingest.schema_barrier"


def test_a_name_conflict_asks_for_confirmation_and_then_keeps_both_files(env: Env) -> None:
    repo = build_repository(env.settings)
    content = _csv("CONFLICT")

    first = repo.submit_ingest("demo.csv", content)
    assert first.status == "needs_confirmation"
    assert first.deposit is not None and first.deposit.would_overwrite is True
    assert first.published is None

    second = repo.submit_ingest("demo.csv", content, confirm_overwrite=True)
    assert second.status == "accepted" and second.ingest_id == first.ingest_id
    assert second.deposit is not None
    assert second.deposit.raw_path.endswith(f"demo-{_sha(content)[:8]}.csv")


def test_the_same_file_twice_is_the_same_ingestion(env: Env) -> None:
    repo = build_repository(env.settings)
    one = repo.submit_ingest("v.csv", _csv("ok"))
    two = repo.submit_ingest("v.csv", _csv("ok"))

    assert one.ingest_id == two.ingest_id
    assert env.rows("SELECT COUNT(*) FROM ingestas WHERE sha256 = ?", one.ingest_id) == [(1,)]


def test_a_missing_engine_is_a_retryable_unavailable_error(env: Env) -> None:
    settings = replace(env.settings, engine_command=("/no/such/pred-engine",))

    with pytest.raises(EngineUnavailable, match="No se pudo lanzar el motor") as raised:
        build_repository(settings).submit_ingest("a.csv", _csv())

    assert raised.value.to_error_info().retryable is True


def test_an_engine_that_dies_without_a_report_says_so(env: Env) -> None:
    with pytest.raises(EngineUnavailable, match="sin dejar el informe"):
        build_repository(env.settings).submit_ingest("a.csv", _csv("BOOM"))


def test_a_stale_report_of_the_same_file_is_not_mistaken_for_the_new_one(env: Env) -> None:
    repo = build_repository(env.settings)
    content = _csv("BOOM")
    old = env.settings.data_root / "ingestas" / f"{_sha(content)}.json"
    old.parent.mkdir(exist_ok=True)
    old.write_text("{}")
    os.utime(old, (1, 1))  # written long before this upload

    with pytest.raises(EngineUnavailable, match="sin dejar el informe"):
        repo.submit_ingest("a.csv", content)


def test_an_engine_that_hangs_times_out(env: Env) -> None:
    with pytest.raises(IngestLaunchError, match="tardó más de 1 s"):
        submit_csv(env.settings, "a.csv", _csv("SLOW"), timeout=1)


def test_the_thesis_catalog_size_is_within_the_upload_limit_and_the_timeout_waits_for_it() -> None:
    assert ingesta.MAX_BYTES == 256 * 1024 * 1024
    assert ingesta.DEFAULT_TIMEOUT_S == 900.0
    assert inspect.signature(submit_csv).parameters["timeout"].default == 900.0


def _padded(size: int) -> bytes:
    head = _csv("ok")
    return head + b"#" + b"x" * (size - len(head) - 2) + b"\n"


def test_a_file_of_exactly_the_limit_is_accepted_and_one_byte_more_is_not(env: Env) -> None:
    repo = build_repository(env.settings)
    at_limit = _padded(ingesta.MAX_BYTES)
    assert len(at_limit) == ingesta.MAX_BYTES

    assert repo.submit_ingest("catalogo.csv", at_limit).status == "accepted"
    with pytest.raises(InvalidQuery, match="límite de 256 MB"):
        repo.submit_ingest("catalogo.csv", at_limit + b"x")


def test_the_name_is_reduced_to_its_base_and_must_be_a_csv() -> None:
    assert safe_name("../../etc/passwd.csv") == "passwd.csv"
    assert safe_name("C:\\datos\\ventas.CSV") == "ventas.CSV"
    for bad in ("", "..", "notas.txt", "ventas"):
        with pytest.raises(ValueError):
            safe_name(bad)


def test_bad_uploads_are_invalid_queries(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = build_repository(env.settings)
    with pytest.raises(InvalidQuery, match=".csv"):
        repo.submit_ingest("notas.txt", b"x")
    with pytest.raises(InvalidQuery, match="vacío"):
        repo.submit_ingest("a.csv", b"")
    monkeypatch.setattr(ingesta, "MAX_BYTES", 10)
    with pytest.raises(InvalidQuery, match="límite"):
        repo.submit_ingest("a.csv", _csv())


# ------------------------------------------------------------------------------- por HTTP
def _client(env: Env) -> TestClient:
    app = create_app(replace(env.settings, worker="off"))
    app.state.repository = build_repository(env.settings)
    return TestClient(app)


def _upload(client: TestClient, content: bytes, name: str = "ventas.csv"):
    return client.post(
        "/datos/cargar", files={"archivo": (name, content, "text/csv")}, follow_redirects=False
    )


def test_uploading_in_the_browser_leads_to_the_diagnosis_and_the_topology(env: Env) -> None:
    client = _client(env)
    content = _csv("ok")

    response = _upload(client, content)
    assert response.status_code == 303
    assert response.headers["location"] == f"/datos?ingest_id={_sha(content)}"

    page = client.get(response.headers["location"]).text
    assert "Datos válidos" in page and "Topología de demanda" in page
    assert "Continuar a Ejecución" in page


def test_a_conflicting_upload_goes_through_the_confirmation_step(env: Env) -> None:
    client = _client(env)
    content = _csv("CONFLICT")

    first = _upload(client, content, "demo.csv")
    token = re.search(r"pendiente=([0-9a-f]+)", first.headers["location"])
    assert first.status_code == 303 and token is not None
    page = client.get(first.headers["location"]).text
    assert "Se requiere confirmación" in page

    confirmed = client.post(
        "/datos/cargar",
        data={"pendiente": token.group(1), "confirm_overwrite": "1"},
        follow_redirects=False,
    )
    assert confirmed.headers["location"] == f"/datos?ingest_id={_sha(content)}"
    assert "Datos válidos" in client.get(confirmed.headers["location"]).text


def test_an_upload_the_engine_cannot_take_shows_an_unavailable_state_not_a_500(env: Env) -> None:
    app = create_app(replace(env.settings, worker="off", engine_command=("/no/such/x",)))
    app.state.repository = build_repository(replace(env.settings, engine_command=("/no/such/x",)))

    response = TestClient(app).post(
        "/datos/cargar", files={"archivo": ("a.csv", _csv(), "text/csv")}
    )

    assert response.status_code == 200
    assert "No se pudo lanzar el motor" in response.text
    assert "Reintentar" in response.text


def test_a_rejected_upload_shows_the_engine_reason(env: Env) -> None:
    client = _client(env)
    response = _upload(client, _csv("BADHEADER"), "raro.csv")
    page = client.get(response.headers["location"]).text

    assert "El archivo necesita correcciones" in page
    assert "Traceback" not in page


def test_an_oversized_file_is_refused_before_the_engine_runs(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ingesta, "MAX_BYTES", 10)
    response = _client(env).post("/datos/cargar", files={"archivo": ("a.csv", _csv(), "text/csv")})

    assert response.status_code == 200 and "límite" in response.text
    assert env.rows("SELECT COUNT(*) FROM ingestas") == [(2,)]  # nothing new was ingested
