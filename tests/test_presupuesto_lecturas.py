"""Presupuesto de lecturas por página: conexiones y sentencias SQLite y archivos que abre una carga.

Los topes son los de hoy con los datos de prueba (``tests.test_worker.env``). Una página que vuelve
a leer lo que ya leyó los supera; si una lectura nueva hace falta, se sube su tope aquí con el
motivo. No hay caché: cada lectura se hace una vez y la reutiliza quien la necesita.
"""

from __future__ import annotations

import builtins
import io
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from pred_platform.data.dal import DalRepository
from tests.test_ejecucion import FORM, LONG_ENOUGH, _dal_client, _launch, _scenario, _text
from tests.test_worker import Env
from tests.test_worker import env as env  # noqa: F401  (the fixture)

_RUN_ROW = "FROM ejecuciones e JOIN configuraciones c"


@dataclass
class Reads:
    """Lo que leyó una solicitud: conexiones a la base, sentencias y archivos de datos abiertos."""

    connections: int = 0
    statements: list[str] = field(default_factory=list)
    files: list[str] = field(default_factory=list)

    def count(self, fragment: str) -> int:
        return sum(fragment in statement for statement in self.statements)


@contextmanager
def _recording(monkeypatch: pytest.MonkeyPatch, data_root: Path) -> Iterator[Reads]:
    reads = Reads()
    connect, real_open = sqlite3.connect, io.open

    def traced(*args: Any, **kwargs: Any) -> sqlite3.Connection:
        reads.connections += 1
        conn = connect(*args, **kwargs)
        conn.set_trace_callback(reads.statements.append)
        return conn

    def opened(file: Any, *args: Any, **kwargs: Any) -> Any:
        if str(file).startswith(str(data_root)):
            reads.files.append(Path(file).name)
        return real_open(file, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(sqlite3, "connect", traced)
        patch.setattr(io, "open", opened)
        patch.setattr(builtins, "open", opened)
        yield reads


def _get(client: TestClient, env: Env, monkeypatch: pytest.MonkeyPatch, url: str) -> Reads:
    with _recording(monkeypatch, env.settings.data_root) as reads:
        response = client.get(url)
    assert response.status_code in (200, 286), (url, response.status_code)
    return reads


def _completed(env: Env, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    _scenario(monkeypatch, "ok")
    client = _dal_client(env)
    assert _launch(client, env).status_code == 303
    assert env.worker().run_next()
    return client


# ------------------------------------------------------------------------- Ejecución
def test_polling_a_run_reads_its_row_once(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _completed(env, monkeypatch)

    reads = _get(client, env, monkeypatch, "/ejecucion/estado?ejecucion=1")

    # El intento y el estado salen de la misma fila; el error guardado se lee aparte.
    assert reads.count(_RUN_ROW) == 2
    assert reads.connections <= 10 and len(reads.statements) <= 26, reads
    assert reads.files.count("progreso.jsonl") <= 1


def test_the_page_of_a_completed_run_reuses_its_status_for_the_steps_and_causes(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _completed(env, monkeypatch)

    reads = _get(client, env, monkeypatch, "/corridas/1/ejecucion")

    # Una sola lectura del estado sirve a la página, a la barra de pasos y a las causas: antes la
    # barra leía el estado, las ingestas y las publicaciones otra vez (20 conexiones y 51
    # sentencias en frío). Las filas de ejecuciones: el estado, el error guardado y, en frío, la
    # lista de corridas anteriores de «Antes», que se guarda un minuto.
    assert reads.count(_RUN_ROW) == 3
    assert reads.count("FROM tareas WHERE") == 3  # la página, su total y los conteos
    assert reads.count("GROUP BY estado") == 1
    assert not reads.count("FROM ingestas ORDER BY")  # la lista de ingestas no hace falta
    # Sin tareas fallidas ni no ejecutables, los conteos ya dicen que no hay causas.
    assert not reads.count("estado IN ('fallida', 'no_ejecutable')")
    assert reads.connections <= 11 and len(reads.statements) <= 28, reads
    assert reads.files.count("progreso.jsonl") <= 1
    again = _get(client, env, monkeypatch, "/corridas/1/ejecucion")
    assert again.count(_RUN_ROW) == 2 and again.connections <= 10, again


# ---------------------------------------------------------------- Datos, Ajustes y Revisar
_TOPOLOGY_PAGE = "SELECT sku, n_obs, n_positive, adi, cv2, clase FROM series"
_INGEST_LIST = "FROM ingestas ORDER BY id DESC"


def _post(
    client: TestClient, env: Env, monkeypatch: pytest.MonkeyPatch, url: str, data: dict[str, Any]
) -> Reads:
    with _recording(monkeypatch, env.settings.data_root) as reads:
        response = client.post(url, data=data, follow_redirects=False)
    assert response.status_code == 200, (url, response.status_code)
    return reads


def test_settings_check_the_launch_rule_once_and_list_the_files_once(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _dal_client(env)

    opened = _get(client, env, monkeypatch, f"/corridas/nueva/ajustes?ingest_id={env.ingest_id}")
    # La lista de archivos, para el enlace, la regla y la barra de pasos; los SKU por clase, para
    # la regla y la matriz; el mapa completo, para la historia. Antes: 14 conexiones.
    assert opened.count(_INGEST_LIST) == 1
    assert opened.count(_TOPOLOGY_PAGE) == 2  # la clase de cada SKU (size=1) y el mapa completo
    assert opened.connections <= 8 and len(opened.statements) <= 19, opened

    checked = _post(
        client, env, monkeypatch, "/corridas/nueva/ajustes/comprobar",
        {"ingest_id": env.ingest_id, **FORM},
    )  # fmt: skip
    # «Comprobar» aplica la regla una vez y la página no la vuelve a aplicar. Antes: 20.
    assert checked.count(_TOPOLOGY_PAGE) == 3, checked
    assert checked.connections <= 12 and len(checked.statements) <= 29, checked


def test_a_launch_that_needs_confirmation_reuses_the_whole_map_and_its_check(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _dal_client(env)

    reads = _post(
        client, env, monkeypatch, "/ejecucion/lanzar",
        {"ingest_id": env.ingest_id, **FORM, **LONG_ENOUGH, "families": ["classical"]},
    )  # fmt: skip

    # Con una sola familia, las tres clases quedan sin dos que comparar: sus SKU salen del mapa
    # completo ya leído y Revisar muestra el aviso sin comprobar otra vez. Antes: 39 conexiones.
    assert reads.count(_TOPOLOGY_PAGE) == 2, reads
    assert reads.connections <= 11 and len(reads.statements) <= 26, reads


def test_the_data_step_reuses_a_table_page_that_already_holds_the_whole_map(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _dal_client(env)

    reads = _get(client, env, monkeypatch, f"/corridas/nueva/datos?ingest_id={env.ingest_id}")

    assert reads.count(_TOPOLOGY_PAGE) == 1  # la tabla ya trae los tres SKU. Antes: 2
    assert reads.connections <= 7 and len(reads.statements) <= 16, reads
    filtered = _get(
        client,
        env,
        monkeypatch,
        f"/corridas/nueva/datos?ingest_id={env.ingest_id}&sku_class=smooth",
    )
    assert filtered.count(_TOPOLOGY_PAGE) == 2  # una tabla filtrada no es el mapa


def test_the_settings_record_of_a_run_does_not_load_earlier_durations(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _completed(env, monkeypatch)

    settings = _get(client, env, monkeypatch, "/corridas/1/ajustes")
    review = _get(client, env, monkeypatch, "/corridas/1/revisar")

    # Ajustes no muestra «Antes»: no lee los registros de las corridas anteriores. Las corridas
    # guardadas se listan una vez por página. Antes: dos veces, 15 conexiones.
    for reads in (settings, review):
        assert reads.count("ORDER BY e.id DESC") == 1, reads
        assert reads.connections <= 14 and len(reads.statements) <= 33, reads


# ------------------------------------------------------------------------- Resultados
def test_the_summary_of_three_published_seeds_reads_each_run_and_evaluation_once(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.test_corridas import _dal_client as results_client
    from tests.test_results_chapters import _three_seeds

    client = results_client(env)
    first, *_ = _three_seeds(env, client)
    page = _text(client.get(f"/corridas/{first}/resultados/resumen").text)
    assert "Semillas con resultados 3 de 3" in page
    calls: list[str] = []
    for name in ("get_run_status", "list_m3_runs", "get_m3_run"):
        original = getattr(DalRepository, name)

        def counted(self: DalRepository, *args: Any, _name=name, _fn=original, **kw: Any) -> Any:
            calls.append(_name)
            return _fn(self, *args, **kw)

        monkeypatch.setattr(DalRepository, name, counted)

    reads = _get(client, env, monkeypatch, f"/corridas/{first}/resultados/resumen")

    # El estado de cada ejecución publicada se lee una vez, al elegir las publicadas, y lo reciben
    # la barra de pasos, el encabezado y las semillas. La corrida M3 de cada semilla sale de la
    # lista de esa ejecución y sirve a «otras publicaciones». Antes: 7, 7 y 6 lecturas, 239
    # sentencias.
    assert calls.count("get_run_status") == 3
    assert calls.count("list_m3_runs") == 4  # las publicadas, la abierta y una por semilla
    assert calls.count("get_m3_run") == 1  # la evaluación de la corrida abierta
    assert len(reads.statements) <= 159, len(reads.statements)
