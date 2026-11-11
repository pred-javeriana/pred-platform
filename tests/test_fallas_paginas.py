"""Cada lugar que muestra un error lo presenta igual: causa, qué hacer, por qué falló y soporte.

El texto del motor (diccionarios, representaciones de Python, nombres de excepción y rutas
internas) solo puede aparecer dentro del detalle para soporte; ni en el texto de la página ni
en un ``title`` o un ``aria-label``. Las pruebas recorren las páginas reales con los escenarios
fixture y un error como el de las corridas anteriores al contrato 1.3.0.
"""

from __future__ import annotations

import html as html_lib
import re
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from pred_platform.app.main import create_app
from pred_platform.config import Settings
from pred_platform.contract import models
from pred_platform.data.fixtures import FixtureRepository
from tests.test_fallas import DETALLE_119187

# Trozos del texto del motor que nunca deben verse fuera del detalle para soporte.
_CRUDO = (
    "causas=",
    "motivos=",
    "<locals>",
    "pred_engine",
    "AjusteModeloError",
    "PipelineExecutionError",
    "raiz AR/MA de modulo",
    "umbral_top_",
    "sku_id=",
    "/data/",
)


def _client(scenario: str, repo: FixtureRepository | None = None) -> TestClient:
    app = create_app(
        Settings(
            data_root=Path("unused"),
            db_path=Path("unused/pred.db"),
            data_source="fixture",
            fixture_scenario=scenario,
        )
    )
    app.state.repository = repo or FixtureRepository(scenario)
    return TestClient(app)


def _soportes(html: str) -> list[str]:
    return [
        html_lib.unescape(texto)
        for texto in re.findall(r'<pre class="pred-soporte__texto">(.*?)</pre>', html, re.S)
    ]


def _fuera_de_soporte(html: str) -> str:
    """Lo que una persona lee o ve al pasar el puntero, sin el detalle para soporte."""
    resto = re.sub(r'<pre class="pred-soporte__texto">.*?</pre>', " ", html, flags=re.S)
    resto = re.sub(r"<(script|style)\b.*?</\1>", " ", resto, flags=re.S)
    atributos = " ".join(re.findall(r'(?:title|aria-label)="([^"]*)"', resto))
    texto = re.sub(r"<[^>]+>", " ", resto)
    return re.sub(r"\s+", " ", html_lib.unescape(f"{texto} {atributos}"))


def _sin_texto_crudo(html: str) -> str:
    visible = _fuera_de_soporte(html)
    for trozo in _CRUDO:
        assert trozo not in visible, trozo
    return visible


class _Ejecucion(FixtureRepository):
    """La ejecución 2 del escenario ``errores`` con un error de tarea o de ejecución a elección."""

    def __init__(self, *, tarea: dict[str, Any] | None, ejecucion: dict[str, Any] | None) -> None:
        super().__init__("errores")
        self._tarea = tarea
        self._ejecucion = ejecucion

    def get_run_status(self, run_id: str, **kwargs: Any) -> models.RunStatus:
        datos = super().get_run_status(run_id, **kwargs).model_dump(mode="json")
        fallidas = [t for t in datos["tasks"] if t["estado"] == "fallida"]
        if self._tarea is not None:
            for tarea in fallidas:
                tarea.update(family="classical", modelo="classical:sarima", error=self._tarea)
        if self._ejecucion is not None:
            datos["ejecucion"].update(estado="detenida", finalizada_en="2026-10-02T07:00:00Z")
        return models.RunStatus.model_validate(datos)

    def get_run_error(self, run_id: str) -> models.ErrorInfo | None:
        return None if self._ejecucion is None else models.ErrorInfo.model_validate(self._ejecucion)


_NO_VIABLE = {
    "code": "selection.no_viable_trial",
    "stage": "L2",
    "detail": DETALLE_119187,
    "source_exception": "NingunTrialCompletoError",
}


def test_una_tarea_fallida_en_ejecucion_explica_la_busqueda_sin_texto_crudo() -> None:
    client = _client("errores", _Ejecucion(tarea=_NO_VIABLE, ejecucion=None))
    html = client.get("/ejecucion?ejecucion=2&tareas=todas").text

    visible = _sin_texto_crudo(html)
    assert "Ninguna configuración terminó la búsqueda para este SKU." in visible
    assert "Por qué falló" in visible
    assert (
        "De 34 configuraciones probadas, 28 fallaron y 6 se descartaron antes de terminar."
        in visible
    )
    assert "Ejemplo: en la ventana 4, SARIMA(1, 0, 0)x(2, 1, 1, 7) dejó una raíz AR/MA" in visible
    assert any(DETALLE_119187 in texto for texto in _soportes(html))
    assert "Copiar detalle" in visible


def test_el_error_de_la_ejecucion_explica_la_causa_sin_texto_crudo() -> None:
    error = {
        "code": "pipeline.stage_failed",
        "stage": "L2",
        "detail": "PipelineExecutionError: fallo L2 en /data/runs/r1/l2.parquet",
        "source_exception": "PipelineExecutionError",
    }
    client = _client("errores", _Ejecucion(tarea=None, ejecucion=error))
    html = client.get("/ejecucion?ejecucion=2").text

    callout = re.search(r'id="error-ejecucion".*?Detalle para soporte', html, re.S)
    assert callout is not None
    _sin_texto_crudo(html)
    assert any("Código: pipeline.stage_failed" in texto for texto in _soportes(html))
    assert any(error["detail"] in texto for texto in _soportes(html))


def test_la_familia_fallida_de_un_sku_explica_la_busqueda_sin_texto_crudo() -> None:
    html = _client("errores").get("/resultados/sku/FIL-0001").text

    visible = _sin_texto_crudo(html)
    assert "Por qué falló" in visible
    assert "De 3 configuraciones probadas, 1 falló y 2 se descartaron antes de terminar." in visible
    assert any("Código: selection.no_viable_trial" in texto for texto in _soportes(html))


def test_una_familia_excluida_de_un_sku_lleva_el_texto_del_motor_a_soporte() -> None:
    html = _client("errores").get("/resultados/sku/FIL-0001").text

    _sin_texto_crudo(html)
    assert any(
        "Código: model.series_too_short" in texto
        and "La serie no alcanza el minimo de la familia." in texto
        for texto in _soportes(html)
    )


def test_la_ingesta_fallida_en_datos_muestra_la_causa_sin_texto_crudo(tmp_path: Path) -> None:
    app = create_app(
        Settings(
            data_root=tmp_path,
            db_path=tmp_path / "pred.db",
            data_source="fixture",
            fixture_scenario="errores",
        )
    )
    client = TestClient(app)
    first = client.post(
        "/datos/cargar",
        files={"archivo": ("panel.csv", b"sku_id,timestamp,demand_qty\nA,2024-01-01,1\n")},
        follow_redirects=False,
    )
    token = re.search(r"pendiente=([^&]+)", first.headers["location"])
    assert token is not None
    confirmed = client.post(
        "/datos/cargar",
        data={"pendiente": token.group(1), "confirm_overwrite": "true"},
        follow_redirects=False,
    )
    html = client.get(confirmed.headers["location"]).text

    visible = _sin_texto_crudo(html)
    assert "No fue posible procesar el archivo" in visible
    assert "La fila 43 tiene un valor inválido en lead_time_days." in visible
    assert any("Código: ingest.schema_barrier" in texto for texto in _soportes(html))


@pytest.mark.parametrize("url", ["/ejecucion?ejecucion=99", "/resultados/sku/NO-EXISTE"])
def test_una_pagina_de_error_lleva_su_detalle_a_soporte(url: str) -> None:
    response = _client("normal").get(url)

    assert response.status_code == 404
    _sin_texto_crudo(response.text)
    assert "Traceback" not in response.text
