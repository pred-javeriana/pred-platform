"""Superficie /datos: carga C1, topología C3 y degradación honesta en DAL."""

import re
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from pred_platform.app.datos import PendingUpload, PendingUploads
from pred_platform.app.main import create_app
from pred_platform.config import Settings
from pred_platform.contract import models
from pred_platform.contract.loader import parse_as
from pred_platform.contract.resources import read_example
from pred_platform.data.fixtures import FixtureRepository

ACCEPTED = "b1831d4585b72d7c7bcb98ada7bae70e7b229449edf74a8a7a7f39e6552df56e"
REJECTED = "962673a533b53ce2229d5b3947e1f27df2135ef050ce86c835ac7d9dc1d26bb4"
_CSV = (
    "demo.csv",
    b"sku_id,timestamp,demand_qty,lead_time_days\nFIL-0001,2026-01-01,1,7\n",
    "text/csv",
)


class RecordingRepository(FixtureRepository):
    """Envuelve el fixture para afirmar qué se delegó, sin inventar datos."""

    def __init__(self, scenario: str = "normal") -> None:
        super().__init__(scenario)
        self.submit_calls: list[dict[str, object]] = []
        self.topology_calls: list[dict[str, object]] = []
        self.report_calls: list[str] = []

    def submit_ingest(self, file_name: str, content: bytes, *, confirm_overwrite: bool = False):
        self.submit_calls.append(
            {
                "file_name": file_name,
                "content": content,
                "confirm_overwrite": confirm_overwrite,
            }
        )
        return super().submit_ingest(file_name, content, confirm_overwrite=confirm_overwrite)

    def get_ingest_report(self, ingest_id: str):
        self.report_calls.append(ingest_id)
        return super().get_ingest_report(ingest_id)

    def get_topology_report(self, ingest_id: str, **kwargs: object):
        self.topology_calls.append({"ingest_id": ingest_id, **kwargs})
        return super().get_topology_report(ingest_id, **kwargs)  # type: ignore[arg-type]


def _client(tmp_path: Path, scenario: str = "normal") -> tuple[TestClient, RecordingRepository]:
    app = create_app(
        Settings(
            data_root=tmp_path,
            db_path=tmp_path / "pred.db",
            data_source="fixture",
            fixture_scenario=scenario,
        )
    )
    repo = RecordingRepository(scenario)
    app.state.repository = repo
    return TestClient(app), repo


def test_initial_render_replaces_placeholder_and_keeps_navigation(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    response = client.get("/datos")
    html = response.text
    assert response.status_code == 200
    assert "Cargar y validar" in html
    assert 'type="file"' in html
    assert "pred-topbar" in html and "pred-rail" in html
    assert "Aún no se han cargado datos." not in html
    assert "Cuando la carga esté conectada" not in html
    assert "Administración" not in html
    assert "Iniciar sesión" not in html
    assert "Datos de prueba" in html
    # Sin ingesta elegida, cargar es la acción principal y dice qué columnas espera el motor.
    assert 'id="titulo-carga">Cargar un archivo<' in html
    for column in ("sku_id", "timestamp", "demand_qty", "lead_time_days"):
        assert f'<span class="mono">{column}</span>' in html


def test_the_expected_columns_are_open_both_as_first_upload_and_as_another_file(
    tmp_path: Path,
) -> None:
    client, _ = _client(tmp_path)
    first = client.get("/datos").text
    another = client.get(f"/datos?ingest_id={ACCEPTED}#carga").text
    assert 'id="titulo-carga">Cargar un archivo<' in first
    assert "Cargar otro archivo" in another
    # Folded, the list left the right half of the upload card empty.
    for html in (first, another):
        assert re.search(r'<details [^>]*id="columnas-csv" open>', html)


@pytest.mark.parametrize("scenario", ["normal", "vacio", "errores"])
def test_the_visible_text_of_datos_has_no_developer_words(tmp_path: Path, scenario: str) -> None:
    client, repo = _client(tmp_path, scenario)
    ingest = repo.list_ingests().items[0].ingest_id if scenario != "vacio" else None
    for path in ("/corridas/nueva/datos", f"/corridas/nueva/datos?ingest_id={ingest}"):
        if ingest is None and "ingest_id" in path:
            continue
        html = client.get(path).text
        # The technical detail stays one click away, folded: it is not the visible text.
        shown = re.sub(r"<details.*?</details>", " ", html, flags=re.S)
        text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", shown))
        assert not re.search(r"ingesta|SHA-256", text, re.I), (scenario, path)


def test_the_file_identifier_is_in_a_folded_technical_detail(tmp_path: Path) -> None:
    client, repo = _client(tmp_path)
    ingest = repo.list_ingests().items[0].ingest_id
    html = client.get(f"/corridas/nueva/datos?ingest_id={ingest}").text
    found = re.search(
        r"<details[^>]*><summary>Detalle técnico</summary>.*?SHA-256.*?</details>", html, re.S
    )
    assert found is not None and str(ingest) in found.group(0)


def test_vacio_distinguishes_empty_history_from_missing_topology(tmp_path: Path) -> None:
    client, _ = _client(tmp_path, "vacio")
    html = client.get("/datos").text
    assert "Todavía no hay archivos cargados." in html
    assert 'id="titulo-carga">Cargar un archivo<' in html
    assert "No hay SKU que coincidan" not in html
    assert "La caracterización todavía no está disponible." not in html
    # El historial no queda como subsección de «Cargar un archivo» en el esquema de títulos.
    headings = re.findall(r"<h([1-4])[^>]*>([^<]*)<", html)
    history = headings.index(("3", "Historial de archivos cargados"))
    assert headings[history - 1] == ("2", "Archivos ya cargados")


def test_accepted_upload_delegates_and_exposes_ingest_id(tmp_path: Path) -> None:
    client, repo = _client(tmp_path, "normal")
    response = client.post("/datos/cargar", files={"archivo": _CSV}, follow_redirects=False)
    assert response.status_code == 303
    location = response.headers["location"]
    assert ACCEPTED in location
    assert repo.submit_calls
    assert repo.submit_calls[0]["confirm_overwrite"] is False
    html = client.get(location).text
    assert "Datos válidos" in html
    assert "Filas validadas" in html
    assert ACCEPTED[:12] in html
    assert "Clases de demanda" in html
    # Siguiente elige el archivo para la corrida nueva y lleva a Ajustes.
    assert '<form method="post" action="/corridas/nueva/datos" class="pred-footnav__form">' in html
    assert f'<input type="hidden" name="ingest_id" value="{ACCEPTED}">' in html
    assert "Continuar a Ajustes" in html


def test_rejected_history_shows_actionable_diagnosis_without_topology(tmp_path: Path) -> None:
    client, repo = _client(tmp_path, "normal")
    html = client.get(f"/datos?ingest_id={REJECTED}").text
    # A file that cannot run is not offered as the step's Siguiente.
    assert 'id="siguiente-motivo">Elija un archivo válido para continuar.' in html
    assert "El archivo necesita correcciones" in html
    assert "demand_qty" in html
    assert "Renombre la columna" in html
    assert "Corrige el archivo" in html or "Corrija el archivo" in html
    assert "Clases de demanda" not in html
    assert repo.topology_calls == []


def test_needs_confirmation_then_confirm_overwrite(tmp_path: Path) -> None:
    client, repo = _client(tmp_path, "errores")
    first = client.post("/datos/cargar", files={"archivo": _CSV}, follow_redirects=False)
    assert first.status_code == 303
    location = first.headers["location"]
    token = parse_qs(urlparse(location).query)["pendiente"][0]
    html = client.get(location).text
    assert "Se requiere confirmación" in html
    assert "Confirmar carga" in html
    assert "Cancelar" in html
    assert "Clases de demanda" not in html
    assert repo.submit_calls[0]["confirm_overwrite"] is False

    confirmed = client.post(
        "/datos/cargar",
        data={"pendiente": token, "confirm_overwrite": "true"},
        follow_redirects=False,
    )
    assert confirmed.status_code == 303
    assert repo.submit_calls[-1]["confirm_overwrite"] is True
    failed = client.get(confirmed.headers["location"]).text
    assert "No fue posible procesar el archivo" in failed
    assert "ingest.schema_barrier" in failed
    # El error sale en español con la fila y la columna, y con lo que hay que hacer (G6).
    assert "La fila 43 tiene un valor inválido en lead_time_days." in failed
    assert "Corríjalo en el archivo y vuelva a cargarlo." in failed
    assert "Clases de demanda" not in failed


def test_cancel_drops_the_pending_upload_so_it_can_no_longer_be_confirmed(
    tmp_path: Path,
) -> None:
    client, repo = _client(tmp_path, "errores")
    first = client.post("/datos/cargar", files={"archivo": _CSV}, follow_redirects=False)
    token = parse_qs(urlparse(first.headers["location"]).query)["pendiente"][0]
    assert len(client.app.state.pending_uploads) == 1  # type: ignore[attr-defined]

    cancelled = client.post("/datos/cancelar", data={"pendiente": token}, follow_redirects=False)
    assert (cancelled.status_code, cancelled.headers["location"]) == (303, "/corridas/nueva/datos")
    assert len(client.app.state.pending_uploads) == 0  # type: ignore[attr-defined]

    late = client.post("/datos/cargar", data={"pendiente": token, "confirm_overwrite": "true"})
    assert "La confirmación expiró. Vuelva a cargar el archivo." in late.text
    assert [call["confirm_overwrite"] for call in repo.submit_calls] == [False]


def test_pending_uploads_are_bounded_in_number_and_time() -> None:
    now = [0.0]
    store = PendingUploads(ttl=3600, limit=5, clock=lambda: now[0])
    report = parse_as(models.IngestReport, read_example("ingest_report.accepted"))
    tokens = []
    for index in range(6):
        now[0] = float(index)
        tokens.append(store.put(PendingUpload(f"{index}.csv", b"x" * 10, report)))

    assert len(store) == 5 and store.report(tokens[0]) is None  # the oldest one went first
    held = store.pop(tokens[5])
    assert held is not None and (held.file_name, held.content) == ("5.csv", b"x" * 10)
    assert store.pop(tokens[5]) is None  # a confirmation is used once

    now[0] = 5 + 3601.0
    assert len(store) == 0 and store.report(tokens[1]) is None


def test_local_validation_errors_stay_on_the_form(tmp_path: Path) -> None:
    client, repo = _client(tmp_path)
    empty = client.post("/datos/cargar")
    assert empty.status_code == 200
    assert "Seleccione un archivo CSV." in empty.text
    assert repo.submit_calls == []

    txt = client.post(
        "/datos/cargar",
        files={"archivo": ("notas.txt", b"hola", "text/plain")},
    )
    assert "extensión .csv" in txt.text
    assert repo.submit_calls == []


def test_dal_submit_without_an_engine_is_unavailable_not_500(tmp_path: Path) -> None:
    settings = Settings(
        data_root=tmp_path,
        db_path=tmp_path / "pred.db",
        data_source="dal",
        engine_command=("/no/such/pred-engine",),
    )
    html = TestClient(create_app(settings)).post("/datos/cargar", files={"archivo": _CSV}).text
    assert "El motor no está disponible." in html
    assert "No se pudo lanzar el motor" in html
    assert "Error inesperado" not in html


def test_dal_missing_ingest_report_is_unavailable_not_500(tmp_path: Path) -> None:
    app = create_app(Settings(data_root=tmp_path, db_path=tmp_path / "pred.db", data_source="dal"))
    response = TestClient(app).get(f"/datos?ingest_id={ACCEPTED}")
    assert response.status_code == 200
    assert "No se encontró el resultado esperado." in response.text
    assert "Traceback" not in response.text


def test_topology_filters_are_delegated(tmp_path: Path) -> None:
    client, repo = _client(tmp_path, "normal")
    html = client.get(
        f"/datos?ingest_id={ACCEPTED}&sku_class=smooth&q=FIL-0001&sort=adi&descending=1&page=1"
    ).text
    assert repo.topology_calls
    call = repo.topology_calls[-1]
    assert call["ingest_id"] == ACCEPTED
    assert call["sku_class"] == "smooth"
    assert call["q"] == "FIL-0001"
    assert call["sort"] == "adi"
    assert call["descending"] is True
    assert "FIL-0001" in html
    assert "Suave" in html
    assert 'role="img"' in html
    assert "<title>" in html
    assert "<desc>" in html


def test_filter_miss_has_specific_empty_message(tmp_path: Path) -> None:
    client, _ = _client(tmp_path, "normal")
    html = client.get(f"/datos?ingest_id={ACCEPTED}&q=NO-EXISTE").text
    assert "No hay SKU que coincidan con los filtros seleccionados." in html
    assert 'id="titulo-carga"' not in html  # con una ingesta, cargar otra es una pestaña


def test_vacio_accepted_upload_shows_topology_unavailable(tmp_path: Path) -> None:
    client, repo = _client(tmp_path, "vacio")
    response = client.post("/datos/cargar", files={"archivo": _CSV}, follow_redirects=True)
    html = response.text
    assert "Datos válidos" in html
    assert "La caracterización todavía no está disponible." in html
    assert "G1" in html
    assert "Continuar a Ejecución" not in html
    assert repo.topology_calls


def test_unavailable_topology_without_thresholds_hides_threshold_rows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, repo = _client(tmp_path)
    topology = parse_as(models.TopologyReport, read_example("topology_report.no_aceptada"))
    monkeypatch.setattr(repo, "get_topology_report", lambda *args, **kwargs: topology)
    response = client.get(f"/datos?ingest_id={ACCEPTED}")
    assert response.status_code == 200
    assert "Umbral ADI" not in response.text and "Umbral CV²" not in response.text
    assert "pred-chart__threshold" not in response.text


def test_invalid_sort_is_not_http_500(tmp_path: Path) -> None:
    client, _ = _client(tmp_path, "normal")
    response = client.get(f"/datos?ingest_id={ACCEPTED}&sort=price")
    assert response.status_code == 200
    assert "Traceback" not in response.text


def test_router_does_not_import_dal_or_engine() -> None:
    source = Path("src/pred_platform/app/routers/data.py").read_text(encoding="utf-8")
    assert "pred_platform.dal" not in source
    assert "pred-engine" not in source
    assert "sqlite" not in source.lower()
    helpers = Path("src/pred_platform/app/datos.py").read_text(encoding="utf-8")
    assert "pred_platform.dal" not in helpers
