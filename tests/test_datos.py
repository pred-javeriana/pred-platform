"""Superficie /datos: carga C1, topología C3 y degradación honesta en DAL."""

from pathlib import Path
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient

from pred_platform.app.main import create_app
from pred_platform.config import Settings
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
    assert "pred-nav" in html
    assert "Aún no se han cargado datos." not in html
    assert "Cuando la carga esté conectada" not in html
    assert "Administración" not in html
    assert "Iniciar sesión" not in html
    assert "Datos de prueba" in html
    assert "Carga un archivo para comenzar." in html


def test_vacio_distinguishes_empty_history_from_missing_topology(tmp_path: Path) -> None:
    client, _ = _client(tmp_path, "vacio")
    html = client.get("/datos").text
    assert "Todavía no hay ingestas registradas." in html
    assert "Carga un archivo para comenzar." in html
    assert "No hay SKU que coincidan" not in html
    assert "La caracterización todavía no está disponible." not in html


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
    assert "Topología de demanda" in html
    assert "Continuar a Ejecución" in html
    assert 'href="/ejecucion"' in html


def test_rejected_history_shows_actionable_diagnosis_without_topology(tmp_path: Path) -> None:
    client, repo = _client(tmp_path, "normal")
    html = client.get(f"/datos?ingest_id={REJECTED}").text
    assert "El archivo necesita correcciones" in html
    assert "demand_qty" in html
    assert "Renombre la columna" in html
    assert "Corrige el archivo" in html or "Corrija el archivo" in html
    assert "Topología de demanda" not in html
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
    assert "Topología de demanda" not in html
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
    assert "Topología de demanda" not in failed


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


def test_dal_submit_is_unavailable_not_500(tmp_path: Path) -> None:
    app = create_app(Settings(data_root=tmp_path, db_path=tmp_path / "pred.db", data_source="dal"))
    html = TestClient(app).post("/datos/cargar", files={"archivo": _CSV}).text
    assert "Carga temporalmente no disponible." in html
    assert "G3" in html
    assert "Error inesperado" not in html


def test_dal_missing_ingest_report_is_unavailable_not_500(tmp_path: Path) -> None:
    app = create_app(Settings(data_root=tmp_path, db_path=tmp_path / "pred.db", data_source="dal"))
    response = TestClient(app).get(f"/datos?ingest_id={ACCEPTED}")
    assert response.status_code == 200
    assert "no disponible" in response.text.lower() or "todavía no está disponible" in response.text
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
    assert "Smooth" in html
    assert 'role="img"' in html
    assert "<title>" in html
    assert "<desc>" in html


def test_filter_miss_has_specific_empty_message(tmp_path: Path) -> None:
    client, _ = _client(tmp_path, "normal")
    html = client.get(f"/datos?ingest_id={ACCEPTED}&q=NO-EXISTE").text
    assert "No hay SKU que coincidan con los filtros seleccionados." in html
    assert "Carga un archivo para comenzar." not in html


def test_vacio_accepted_upload_shows_topology_unavailable(tmp_path: Path) -> None:
    client, repo = _client(tmp_path, "vacio")
    response = client.post("/datos/cargar", files={"archivo": _CSV}, follow_redirects=True)
    html = response.text
    assert "Datos válidos" in html
    assert "La caracterización todavía no está disponible." in html
    assert "G1" in html
    assert "Continuar a Ejecución" not in html
    assert repo.topology_calls


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
