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

from tests.test_ejecucion import _dal_client, _launch, _scenario
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
