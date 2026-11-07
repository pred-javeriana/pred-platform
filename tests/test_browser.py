"""Ejecuta pred.js y HTMX en Chromium real, sin sustituir el DOM ni el reloj de la aplicación.

Requiere Node >= 22 y Chromium (CHROME_BIN permite elegir el binario). CI exige ambos;
una máquina de desarrollo sin navegador omite esta prueba.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import threading
import time
from contextlib import closing
from pathlib import Path

import pytest
import uvicorn
from fastapi import FastAPI

from pred_platform.app.main import create_app
from pred_platform.config import Settings
from pred_platform.dal import ejecuciones as dal
from tests.test_ejecucion import _dal_client, _launch
from tests.test_worker import Env
from tests.test_worker import env as env  # noqa: F401


def _serve(app: FastAPI) -> tuple[uvicorn.Server, threading.Thread, str]:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen()
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        if not thread.is_alive() or time.monotonic() > deadline:
            server.should_exit = True
            sock.close()
            raise RuntimeError("El servidor de la prueba no arrancó")
        time.sleep(0.01)
    return server, thread, f"http://127.0.0.1:{port}"


def test_execution_javascript_in_a_real_browser(env: Env) -> None:
    chrome = os.environ.get("CHROME_BIN") or next(
        (
            path
            for name in ("chromium", "chromium-browser", "google-chrome")
            if (path := shutil.which(name))
        ),
        None,
    )
    node = shutil.which("node")
    if not chrome or not node:
        if os.environ.get("PRED_BROWSER_REQUIRED"):
            pytest.fail("CI requiere Chromium y Node >= 22 para ejecutar pred.js")
        pytest.skip("Requiere Chromium y Node >= 22")

    live = _dal_client(env)
    assert _launch(live, env).status_code == 303
    with closing(dal.connect(env.db)) as conn:
        assert dal.claim_pending(conn) is not None
    fixture_app = create_app(
        Settings(data_source="fixture", data_root=env.settings.data_root, db_path=env.db)
    )
    servers = []
    try:
        fixture_server = _serve(fixture_app)
        servers.append(fixture_server)
        live_server = _serve(live.app)  # type: ignore[arg-type]
        servers.append(live_server)
        result = subprocess.run(
            [node, "--test", str(Path(__file__).parent / "browser" / "pred.test.mjs")],
            env={
                **os.environ,
                "CHROME_BIN": chrome,
                "PRED_FIXTURE_URL": fixture_server[2],
                "PRED_LIVE_URL": live_server[2],
            },
            capture_output=True,
            text=True,
            timeout=90,
        )
        assert result.returncode == 0, result.stdout + result.stderr
    finally:
        for server, thread, _ in servers:
            server.should_exit = True
            thread.join(timeout=10)
            assert not thread.is_alive(), "El servidor de prueba no se cerró"
