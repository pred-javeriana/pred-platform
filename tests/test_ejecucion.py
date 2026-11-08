"""Superficie /ejecucion (C4): lanzar, seguir, detener y reanudar, con fixtures y DAL real."""

from __future__ import annotations

import hashlib
import json
import os
import re
import signal
import sqlite3
import subprocess
import sys
import threading
from collections.abc import Iterator
from contextlib import closing, contextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from pred_platform.app.ejecucion import (
    ExecutionQuery,
    default_form_values,
    error_action,
    parse_execution_query,
    parse_launch_form,
    run_actions,
)
from pred_platform.app.main import create_app
from pred_platform.config import Settings
from pred_platform.contract import models
from pred_platform.contract.errors import ArtifactMissing, InvalidQuery
from pred_platform.dal import ejecuciones as dal
from pred_platform.data.fixtures import FixtureRepository
from pred_platform.worker import engine
from pred_platform.worker.bucle import Worker
from pred_platform.worker.candado import WorkerLock
from pred_platform.worker.engine import build_command
from pred_platform.worker.errores import run_stopped_error
from tests.test_worker import _FAKE, Env, _escribir_progreso, _guardar_origen, _scenario, _wait
from tests.test_worker import env as env  # noqa: F401  (the fixture)


class RecordingRepository(FixtureRepository):
    def __init__(self, scenario: str = "normal") -> None:
        super().__init__(scenario)
        self.status_calls: list[tuple[str, dict[str, Any]]] = []

    def get_run_status(self, run_id: str, **kwargs: Any) -> models.RunStatus:
        self.status_calls.append((run_id, kwargs))
        return super().get_run_status(run_id, **kwargs)


def _fixture_client(scenario: str = "normal") -> tuple[TestClient, RecordingRepository]:
    app = create_app(
        Settings(
            data_root=Path("unused"),
            db_path=Path("unused/pred.db"),
            data_source="fixture",
            fixture_scenario=scenario,
        )
    )
    repo = RecordingRepository(scenario)
    app.state.repository = repo
    return TestClient(app), repo


def _text(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))


def _run_state_tone(html: str) -> str:
    """The colour modifier of the run's «Detenida» state, in Ejecución or in Resultados."""
    found = re.findall(
        r'<span class="pred-status pred-status--(\w+)"[^>]*>\s*<svg[^>]*><use href="#i-stop">'
        r'</use></svg>\s*<span class="pred-status__label">Detenida</span>',
        html,
    )
    assert len(set(found)) == 1, found
    return found[0]


# -------------------------------------------------------------------- con los fixtures
def test_the_active_run_shows_its_state_progress_settings_and_tasks() -> None:
    client, repo = _fixture_client("normal")
    response = client.get("/ejecucion")
    text = _text(response.text)

    assert response.status_code == 200 and repo.status_calls[0][0] == "latest"
    assert "Ejecución #1" in text and "Ejecutando" in text
    assert "4 / 7 tareas" in text and "57,1 %" in text
    assert "Mínimo de entrenamiento 60 días" in text and "Fecha de corte ( t* ) 29 jun 2026" in text
    for state in ("Pendiente", "Ejecutando", "Exitosa"):
        assert state in text
    # While the run works, the list keeps to the tasks worth a look; all of them are one link away.
    assert "Ver las 7 tareas" in text
    every = _text(client.get("/ejecucion?tareas=todas").text)
    assert "FIL-0001 Clásica SARIMA Exitosa" in every


def test_the_family_and_model_cells_never_break_a_word() -> None:
    client, _ = _fixture_client("normal")
    html = client.get("/ejecucion?tareas=todas").text

    # The model reads by its name; the engine id it came from is not shown as a code.
    cells = r'<td class="pred-nowrap">Cl[^<]*</td>\s*<td class="pred-nowrap" title="[^"]+">'
    assert re.search(cells + r"SARIMA</td>", html)
    assert html.count('<td class="pred-nowrap"') >= 2 * 7


def test_pending_evaluation_settings_show_the_engine_handoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, repo = _fixture_client()
    doc = repo.get_run_status("latest")
    data = doc.model_dump()
    data["settings"]["aggregation"] = None
    data["settings"]["trim"] = None
    pending = models.RunStatus.model_validate(data)
    monkeypatch.setattr(repo, "get_run_status", lambda *args, **kwargs: pending)
    response = client.get("/ejecucion")
    assert response.status_code == 200
    assert "Lo informa el motor al terminar la ejecución" in response.text
    assert "recorte None" not in response.text


def test_every_task_state_has_icon_text_and_colour_and_errors_have_a_next_step() -> None:
    client, _ = _fixture_client("errores")
    html = client.get("/ejecucion").text

    for modifier in ("neutral", "info", "ok", "err", "warn"):
        assert f"pred-status--{modifier}" in html
    text = _text(html)
    assert "Fallida" in text and "No ejecutable" in text
    assert "Etapa" in text and re.search(r"\b[a-z]+\.[a-z_]+\b", text)
    assert "Traceback" not in text


def test_filters_pages_and_the_run_are_delegated_to_the_repository() -> None:
    client, repo = _fixture_client("normal")
    client.get("/ejecucion?ejecucion=1&estado=fallida&sku=FIL-0002&family=ml&page=2&size=10")

    assert repo.status_calls[-1] == (
        "1",
        {"estado": "fallida", "sku": "FIL-0002", "family": "ml", "page": 2, "size": 10},
    )


def test_an_invalid_filter_is_a_warning_and_an_unknown_run_is_not_a_500() -> None:
    client, _ = _fixture_client("normal")
    for url in ("/ejecucion?estado=lenta", "/ejecucion?page=abc"):
        response = client.get(url)
        assert response.status_code == 200 and "pred-alert--warning" in response.text
    # An unknown run is a page that says so: the old link leads to its step, which answers 404.
    missing = client.get("/ejecucion?ejecucion=99")
    assert missing.url.path == "/corridas/99/ejecucion"
    assert missing.status_code == 404 and "Traceback" not in missing.text
    assert "No existe la corrida «99»." in missing.text


def test_without_runs_or_ingestions_the_screen_guides_the_next_step() -> None:
    client, _ = _fixture_client("vacio")
    # Without runs, the old Ejecución link starts a new run at Datos, with the upload first.
    first = client.get("/ejecucion")
    assert first.url.path == "/corridas/nueva/datos"
    assert 'id="titulo-carga">Cargar un archivo<' in first.text
    # Ajustes says what is missing and cannot continue.
    ajustes = client.get("/corridas/nueva/ajustes").text
    assert "Todavía no hay ningún archivo de datos aceptado" in _text(ajustes)
    assert "Primero cargue un archivo válido en Datos." in ajustes
    assert re.search(r'<button class="pred-btn pred-btn--secondary[^"]*"[^>]*disabled', ajustes)


def test_the_write_actions_are_disabled_and_explained_with_fixtures() -> None:
    client, repo = _fixture_client("normal")
    html = client.get("/ejecucion").text

    assert 'href="/corridas/nueva/datos"' in html and "Nueva corrida" in html
    ingest_id = next(
        item.ingest_id
        for item in repo.list_ingests().items
        if item.status == "accepted" and item.ingest_id
    )
    settings_html = client.get(f"/ejecucion?ingest_id={ingest_id}").text
    assert "Lanzar está deshabilitado. Está viendo datos de prueba" in settings_html
    # Ajustes still saves the draft (nothing is written); Lanzar, in Revisar, stays off.
    saved = client.post(
        "/corridas/nueva/ajustes",
        data={"ingest_id": ingest_id, **default_form_values()},
        follow_redirects=False,
    )
    assert saved.headers["location"] == "/corridas/nueva/revisar"
    launch_html = client.get("/corridas/nueva/revisar").text
    assert re.search(r'<button class="pred-btn pred-btn--primary[^"]*"[^>]*disabled', launch_html)
    assert "Lanzar está deshabilitado. Está viendo datos de prueba" in launch_html
    assert re.search(
        r'<button class="pred-btn pred-btn--secondary"[^>]*disabled[^>]*>\s*<svg[^>]*>'
        r'<use href="#i-stop"',
        html,
    )
    assert "Detener y reanudar están deshabilitados" in html
    response = client.post("/ejecucion/detener", data={"ejecucion": "1"})
    assert response.status_code == 200 and "datos de prueba" in response.text
    assert (
        client.post(
            "/ejecucion/lanzar",
            data={
                "ingest_id": "a" * 64,
                "families": "ml",
                "min_train": "40",
                "horizon": "7",
                "step": "7",
                "seasonality": "7",
                "seed": "0",
                "workers": "2",
                "metric": "mae",
            },
        ).status_code
        == 200
    )


def test_polling_is_off_with_fixtures_and_the_fragment_stops_it() -> None:
    client, _ = _fixture_client("normal")
    page = client.get("/ejecucion").text
    fragment = client.get("/ejecucion/estado")

    assert 'hx-trigger="none"' in page and "every 2s" not in page
    assert fragment.status_code == 286 and "pred-topbar" not in fragment.text


def _task_table(html: str) -> str:
    """The rows of the task list, as the reader sees them."""
    found = re.search(r"<tbody>(.*?)</tbody>", html.split('id="tareas-foco"', 1)[1], re.S)
    assert found is not None
    return _text(found.group(1))


def test_while_a_run_works_the_task_list_shows_the_failures_first_and_links_the_rest() -> None:
    client, repo = _fixture_client("errores")  # running, 5 tasks, one of each state
    html = client.get("/ejecucion").text

    # The list keeps to what needs a look, so progress and steps stay in view.
    assert "1 tarea falló hasta ahora." in _text(html)
    rows = _task_table(html)
    assert "Fallida" in rows
    for other in ("Pendiente", "Exitosa", "No ejecutable"):
        assert other not in rows
    assert re.search(
        r'<a href="/ejecucion\?[^"]*tareas=todas[^"]*#tareas">Ver las 5 tareas</a>', html
    )
    assert repo.status_calls[-1][1].get("estado") == "fallida"


def test_without_failures_the_task_list_shows_what_runs_now() -> None:
    client, repo = _fixture_client("normal")
    html = client.get("/ejecucion").text

    assert "Ninguna tarea ha fallado. Se ejecutan ahora:" in _text(html)
    rows = _task_table(html)
    assert "Ejecutando" in rows and "Exitosa" not in rows and "Pendiente" not in rows
    assert repo.status_calls[-1][1].get("estado") == "ejecutando"


def test_a_running_run_without_tasks_yet_says_so_instead_of_offering_zero_tasks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, repo = _fixture_client("normal")
    data = repo.get_run_status("latest").model_dump()
    data["tasks"] = []
    data["page"]["total"] = 0
    data["progress"]["tareas_total"] = 0
    data["progress"]["tareas_por_estado"] = dict.fromkeys(data["progress"]["tareas_por_estado"], 0)
    data["progress"]["avance_pct"] = 0.0
    empty = models.RunStatus.model_validate(data)
    monkeypatch.setattr(repo, "get_run_status", lambda *args, **kwargs: empty)
    text = _text(client.get("/ejecucion").text)

    assert "Todavía no hay tareas en esta ejecución." in text
    assert "Ver las 0 tareas" not in text and "Ver la tarea" not in text


def test_a_working_run_shows_what_it_is_doing_and_that_it_is_still_alive() -> None:
    client, _ = _fixture_client("normal")
    html = client.get("/ejecucion").text

    pulse = re.search(
        r'<div class="pred-run__pulse is-fija" id="pulso-ejecucion"[^>]*>(.*?)</div>', html, re.S
    )
    assert pulse is not None
    assert 'data-pred-started="' not in pulse.group(0)
    assert "no se actualiza sola; recárguela para ver el avance" in _text(pulse.group(1))
    text = _text(pulse.group(1))
    assert text.strip().startswith("Trabajando")
    assert re.search(r"Lleva \S+", text)
    # Una vista sin sondeo no simula actividad con el anillo ni con las etapas.
    assert "pred-progress__sweep" not in html and 'data-pred-tween="avance-pct"' in html


def test_execution_state_in_stage_l3_uses_stage_qualified_skus_terminados_tween(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, repo = _fixture_client("normal")
    status = repo.get_run_status("latest")
    assert status.progress is not None
    updated_progress = status.progress.model_copy(
        update={"etapa_actual": "L3", "skus_total": 7, "skus_terminados": 3}
    )
    updated = status.model_copy(update={"progress": updated_progress})
    monkeypatch.setattr(repo, "get_run_status", lambda *args, **kwargs: updated)
    response = client.get("/ejecucion/estado")
    assert response.status_code in (200, 286)
    assert 'data-pred-tween="skus-terminados-L3"' in response.text


def test_a_page_that_does_not_poll_does_not_say_when_it_read_the_progress() -> None:
    # Fixtures (and a read-only copy) never read the progress again: a «read now» line would
    # turn into «the server does not answer» after 12 s, about a server that is fine.
    client, _ = _fixture_client("normal")
    html = client.get("/ejecucion").text

    assert 'hx-trigger="none"' in html and 'id="pulso-ejecucion"' in html
    assert 'id="pulso-lectura"' not in html and "avance leído" not in html
    time_line = re.search(r'<p class="pred-run__pulse-time">(.*?)</p>', html, re.S)
    assert time_line is not None
    assert not _text(time_line.group(1)).strip().endswith("·")


def test_the_launch_form_lists_the_families_each_class_admits() -> None:
    client, repo = _fixture_client("normal")
    ingest_id = next(
        item.ingest_id for item in repo.list_ingests().items if item.status == "accepted"
    )
    html = client.get(f"/ejecucion?ingest_id={ingest_id}").text

    hint = re.search(r'id="families-hint">(.*?)</ul>', html, re.S)
    assert hint is not None
    items = [
        _text(item).strip() for item in re.findall(r"<li[^>]*>(.*?)</li>", hint.group(1), re.S)
    ]
    assert items == [
        "Suave Clásica Aprendizaje automático Aprendizaje profundo Fundacional",
        "Errática Clásica Aprendizaje automático Aprendizaje profundo Fundacional",
        "Intermitente Clásica Aprendizaje automático Fundacional",
        "Grumosa Clásica Fundacional",
    ]
    # Each class explains itself on demand; the families are short tags, not a sentence.
    assert 'title="Vende casi todos los días, en cantidades parecidas."' in hint.group(1)
    assert hint.group(1).count('class="pred-tag"') == 4 + 4 + 3 + 2


# -------------------------------------------------------------------------- con el DAL real
def _dal_client(env: Env, **overrides: Any) -> TestClient:
    settings = replace(env.settings, **{"worker": "off", **overrides})
    app = create_app(settings)
    app.state.repository = env.repo()
    return TestClient(app)


# Grumosa admite solo Clásica y Fundacional: con las dos, cada SKU de la ingesta de
# prueba (smooth, intermittent y lumpy) tiene al menos dos familias que comparar.
FORM: dict[str, Any] = {
    "families": ["classical", "ml", "foundation"],
    "metric": "mae",
    "min_train": "40",
    "horizon": "2",
    "step": "7",
    "seasonality": "7",
    "trials": "1",
    "seed": "17",
    "workers": "2",
}


def _launch(client: TestClient, env: Env, **overrides: Any):
    return client.post(
        "/ejecucion/lanzar",
        data={"ingest_id": env.ingest_id, **FORM, **overrides},
        follow_redirects=False,
    )


def _review(client: TestClient, env: Env, **overrides: Any) -> str:
    """Saves the settings of a new run as a person does (Ajustes, Continuar) and opens Revisar."""
    saved = client.post(
        "/corridas/nueva/ajustes",
        data={"ingest_id": env.ingest_id, **FORM, **overrides},
        follow_redirects=False,
    )
    assert (saved.status_code, saved.headers["location"]) == (303, "/corridas/nueva/revisar")
    return client.get("/corridas/nueva/revisar").text


def test_launching_from_the_form_queues_a_run_with_those_parameters(env: Env) -> None:
    client = _dal_client(env)

    response = _launch(client, env)

    assert response.status_code == 303
    assert response.headers["location"] == "/corridas/1/ejecucion?aviso=en_cola"
    ((estado, seed, parametros),) = env.rows(
        "SELECT e.estado, e.seed, c.parametros FROM ejecuciones e "
        "JOIN configuraciones c ON c.id = e.configuracion_id"
    )
    assert (estado, seed) == ("pendiente", 17)
    import json

    parsed = json.loads(parametros)
    assert parsed["families"] == ["classical", "ml", "foundation"] and parsed["trials"] == 1
    assert (parsed["min_train"], parsed["horizon"], parsed["step"], parsed["metric"]) == (
        40,
        2,
        7,
        "mae",
    )
    page = client.get(response.headers["location"]).text
    assert "La ejecución quedó en cola" in page
    # No worker in this app: the page says who must start it, without an environment variable.
    assert "Pendiente" in _text(page) and "make worker" in page and "PRED_WORKER" not in page
    assert "no procesa ejecuciones por sí sola" in _text(page)


@pytest.mark.parametrize("state", ["detenida", "completada_con_fallos"])
def test_launching_identical_settings_does_not_resume_a_published_run(env: Env, state: str) -> None:
    client = _dal_client(env)
    assert _launch(client, env).status_code == 303
    with closing(dal.connect(env.db)) as conn, conn:
        conn.execute(
            "UPDATE ejecuciones SET estado = ?, finalizada_en = '2026-11-01T12:00:00+00:00'",
            (state,),
        )
    before = env.rows("SELECT * FROM ejecuciones")
    again = _launch(client, env)
    assert again.headers["location"] == "/corridas/1/ejecucion?aviso=existente"
    assert env.rows("SELECT * FROM ejecuciones") == before


@pytest.mark.parametrize("state", ["ejecutando", "completada"])
def test_resumed_elapsed_time_is_labelled_from_first_start(env: Env, state: str) -> None:
    client = _dal_client(env)
    assert _launch(client, env).status_code == 303
    with closing(dal.connect(env.db)) as conn, conn:
        conn.execute(
            "UPDATE ejecuciones SET estado = ?, intento = 2, "
            "iniciada_en = '2026-11-01T12:00:00+00:00'",
            (state,),
        )
    html = client.get("/ejecucion?ejecucion=1").text
    assert "Desde el primer inicio" in _text(html)
    assert "<dt>Duración</dt>" not in html
    assert "<dt>Tiempo transcurrido</dt>" not in html


@pytest.mark.parametrize("seconds, polling", [(119, True), (120, False), (180, False)])
def test_stopped_open_tasks_stop_polling_after_two_minutes(
    env: Env, seconds: int, polling: bool
) -> None:
    client = _dal_client(env)
    assert _launch(client, env).status_code == 303
    with closing(dal.connect(env.db)) as conn, conn:
        run = dal.claim_pending(conn)
        assert run is not None and run.t_star is not None
        dal.task_started(
            conn,
            run.id,
            sku="FIL-0001",
            modelo="ml:lightgbm",
            corte=run.t_star,
            seed=run.seed,
            iniciada_en=dal.now_iso(),
        )
        conn.execute(
            "UPDATE ejecuciones SET estado = 'detenida', finalizada_en = ?",
            ((datetime.now(UTC) - timedelta(seconds=seconds)).isoformat(),),
        )
    response = client.get("/ejecucion/estado?ejecucion=1")
    assert response.status_code == (200 if polling else 286)
    if not polling:
        note = "Las tareas abiertas se cerrarán cuando vuelva el procesador de ejecuciones."
        assert note in response.text
    assert env.rows("SELECT estado FROM tareas") == [("ejecutando",)]


@pytest.mark.parametrize("state", ["completada", "completada_con_fallos"])
def test_engine_terminal_state_keeps_polling_until_worker_finishes(env: Env, state: str) -> None:
    client = _dal_client(env)
    assert _launch(client, env).status_code == 303
    with closing(dal.connect(env.db)) as conn, conn:
        conn.execute("UPDATE ejecuciones SET estado = ?, pid = 12345", (state,))
    owner = WorkerLock.for_database(env.db)
    assert owner.acquire()
    try:
        response = client.get("/ejecucion/estado?ejecucion=1")
        assert response.status_code == 200
        assert 'data-pred-poll="true"' in response.text
        with closing(dal.connect(env.db)) as conn:
            dal.finish_run(conn, 1, estado=state)
        settled = client.get("/ejecucion/estado?ejecucion=1")
        assert settled.status_code == 286
        assert f'data-pred-run-state="{state}"' in settled.text
    finally:
        owner.release()


@pytest.mark.parametrize("state", ["completada", "completada_con_fallos"])
def test_terminal_run_stops_polling_after_its_separate_worker_and_engine_exit(
    env: Env, state: str
) -> None:
    client = _dal_client(env)
    assert _launch(client, env).status_code == 303
    process = subprocess.Popen(
        [sys.executable, "-m", "pred_platform.worker", "--once"],
        env={
            **os.environ,
            "PRED_DATA_ROOT": str(env.settings.data_root),
            "PRED_DB_PATH": str(env.db),
            "PRED_ENGINE_CMD": f"{sys.executable} {_FAKE}",
            "FAKE_ENGINE_SCENARIO": "publishes_before_exit",
            "FAKE_ENGINE_TERMINAL_STATE": state,
            "FAKE_ENGINE_PUBLISH_EXIT_DELAY": "5",
        },
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    pid = None
    try:
        _wait(
            lambda: bool(env.rows("SELECT pid FROM ejecuciones WHERE estado = ?", state)),
            what="engine publication before worker cleanup",
        )
        [(pid,)] = env.rows("SELECT pid FROM ejecuciones")
        assert pid is not None and engine.engine_owner(pid, env.db) == "this"
        process.kill()
        process.wait(timeout=10)
        # Sin procesador, el motor todavía activo debe mantener el seguimiento.
        assert client.get("/ejecucion/estado?ejecucion=1").status_code == 200
        _wait(
            lambda: engine.engine_owner(pid, env.db) == "none",
            what="orphan engine exit",
        )

        def assert_terminal_without_writes() -> None:
            before = env.db.read_bytes()
            fragment = client.get("/ejecucion/estado?ejecucion=1")
            assert fragment.status_code == 286
            assert 'data-pred-poll="false"' in fragment.text
            assert f'data-pred-run-state="{state}"' in fragment.text
            for path, region in (
                ("/", r'<ul class="pred-runs">(.*?)</ul>'),
                ("/corridas/1/ejecucion", r'<div class="pred-run__identity">(.*?)</div>'),
                ("/resultados?ejecucion=1", r'<dl class="pred-context__facts">(.*?)</dl>'),
            ):
                response = client.get(path)
                assert response.status_code == 200
                shown = re.search(region, response.text, re.S)
                assert shown is not None
                labels = re.findall(r'pred-status__label">\s*([^<]+)', shown.group(1))
                assert labels and all(label.startswith("Completada") for label in labels), path
            assert env.db.read_bytes() == before

        assert_terminal_without_writes()
        # Un proceso ajeno que reutilice el PID tampoco vuelve activa la ejecución.
        with closing(dal.connect(env.db)) as conn, conn:
            conn.execute("UPDATE ejecuciones SET pid = ?", (os.getpid(),))
        assert_terminal_without_writes()
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=10)
        if pid is not None and engine.engine_owner(pid, env.db) == "this":
            engine.terminate(pid)


@pytest.mark.parametrize("parameter", ["estado", "family"])
def test_unknown_filter_keeps_the_run_and_all_tasks(parameter: str) -> None:
    client, _ = _fixture_client()
    response = client.get(f"/ejecucion?{parameter}=bogus")
    assert "Ese filtro no existe; se muestran todas las tareas." in response.text
    assert "Ejecución #1" in _text(response.text)
    assert "7 tareas coinciden con la consulta." in _text(response.text)
    assert "Valor desconocido" not in response.text


@pytest.mark.parametrize("failure", [ArtifactMissing, InvalidQuery])
def test_failed_focus_read_falls_back_to_complete_task_list(
    env: Env, monkeypatch: pytest.MonkeyPatch, failure: type[Exception]
) -> None:
    app = create_app(replace(env.settings, worker="off"))
    client = TestClient(app)
    assert _launch(client, env).status_code == 303
    with closing(dal.connect(env.db)) as conn, conn:
        run = dal.claim_pending(conn)
        assert run is not None and run.t_star is not None
        dal.plan_tasks(
            conn,
            run.id,
            corte=run.t_star,
            seed=run.seed,
            units=[(f"SKU-{i:03}", "ml:lightgbm") for i in range(60)],
        )
        conn.execute(
            "UPDATE tareas SET estado = 'fallida', detalle_error = ? WHERE sku = 'SKU-000'",
            (json.dumps(run_stopped_error()),),
        )
    repo = app.state.repository
    original = repo.get_run_status

    def read(run_id: str, **kwargs: Any) -> models.RunStatus:
        if kwargs.get("size") == 5 and kwargs.get("estado"):
            raise failure("lectura temporalmente no disponible")
        return original(run_id, **kwargs)

    monkeypatch.setattr(repo, "get_run_status", read)
    html = client.get("/ejecucion").text
    assert "tareas fallaron hasta ahora" not in _text(html)
    assert "60 tareas coinciden con la consulta." in _text(html)
    assert "Tareas de la ejecución filtrada" in html
    assert 'aria-label="Paginación de tareas"' in html
    assert "Página 1 de 2" in _text(html) and "Siguiente" in html


def test_origin_timeout_does_not_expose_command(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _dal_client(env)

    def timeout(*args: Any, **kwargs: Any) -> None:
        raise subprocess.TimeoutExpired(["/private/engine", "origen"], 60)

    monkeypatch.setattr("pred_platform.worker.engine.subprocess.run", timeout)
    response = _launch(client, env)
    message = (
        "No se pudo consultar al motor el origen de los datos. Intente de nuevo en unos minutos."
    )
    assert message in response.text
    assert "Command" not in response.text and "/private/engine" not in response.text


def test_launch_refuses_when_no_sku_has_enough_history(env: Env) -> None:
    response = _launch(_dal_client(env), env, min_train="400", step="14")
    assert response.status_code == 200
    assert "Con estos ajustes ningún SKU tiene historia suficiente" in response.text
    assert "se necesitan 444 días antes del corte" in response.text
    assert 'id="min_train"' in response.text and 'aria-invalid="true"' in response.text
    assert env.rows("SELECT COUNT(*) FROM ejecuciones") == [(0,)]


def test_launch_accepts_the_exact_history_required(env: Env) -> None:
    # 49 + 2 + 3 * 7 = 72, los días que tienen los SKU de esta ingesta.
    response = _launch(_dal_client(env), env, min_train="49")
    assert response.status_code == 303
    assert env.rows("SELECT COUNT(*) FROM ejecuciones") == [(1,)]


def test_the_launch_form_has_no_data_origin_option(env: Env) -> None:
    client = _dal_client(env)
    html = client.get(f"/ejecucion?ingest_id={env.ingest_id}").text
    assert 'action="/corridas/nueva/ajustes"' in html  # the form is there
    review = _review(client, env)
    assert 'action="/ejecucion/lanzar"' in review
    for page in (html, review):
        assert "data_origin" not in page and "Origen de los datos" not in page
        assert "Sintéticos" not in page


def test_ejecutar_muestra_la_etapa_y_el_avance_de_m3(env: Env) -> None:
    client = _dal_client(env)
    assert _launch(client, env).status_code == 303
    with closing(dal.connect(env.db)) as conn:
        run = dal.claim_pending(conn)
        assert run is not None
        dal.set_launch(conn, run.id, pid=os.getpid(), ruta_corrida=f"runs/{run.run_id}")
    assert run.run_id is not None

    def fragmento() -> str:
        # Lo que la página vuelve a pedir cada 2 s: el dato cambia sin recargar.
        return _text(client.get("/ejecucion/estado?ejecucion=1").text)

    _escribir_progreso(
        env,
        run.run_id,
        {"event": "run_started", "attempt": 1},
        {"event": "stage_started", "stage": "L2"},
    )
    assert "Trabajando en Ajuste de modelos" in fragmento()
    assert "Ajuste de modelos ·" not in fragmento()  # sin plan de la etapa no hay contador
    # L3 cierra M2 (L2 y L3); M3 es solo L4.
    _escribir_progreso(env, run.run_id, {"event": "stage_started", "stage": "L3"})
    assert "Trabajando en Validación con ventanas" in fragmento()
    _escribir_progreso(
        env,
        run.run_id,
        {"event": "stage_started", "stage": "L4"},
        {"event": "planned", "stage": "L4", "units": [{"sku_id": s} for s in "ABC"]},
        {"event": "unit_finished", "stage": "L4", "sku_id": "A", "state": "completada"},
        {"event": "unit_finished", "stage": "L4", "sku_id": "B", "state": "completada"},
    )
    texto = fragmento()
    assert "Trabajando en Evaluación final." in texto
    assert "2 de 3 productos terminados en esta etapa" in texto
    # The working line counts the elapsed time and says when progress was read.
    assert "Lleva " in texto and "avance leído" in texto
    page = client.get("/ejecucion?ejecucion=1").text
    assert "La etapa analítica actual no está informada" not in page


def test_ejecutar_muestra_las_tareas_que_terminaron_m2(env: Env) -> None:
    client = _dal_client(env)
    assert _launch(client, env).status_code == 303
    with closing(dal.connect(env.db)) as conn:
        run = dal.claim_pending(conn)
        assert run is not None and run.t_star is not None
        dal.task_stage_finished(
            conn,
            run.id,
            sku="FIL-0001",
            modelo="classical:sarima",
            corte=run.t_star,
            seed=run.seed,
            stage="L3",
            completed=True,
            seconds=1.0,
            finished_at="2026-10-27T12:00:00+00:00",
            error=None,
        )
    etiqueta = "Por evaluar"

    # El resumen de estados la cuenta y el filtro por estado la encuentra.
    assert f"{etiqueta} 1" in _text(client.get("/ejecucion?ejecucion=1").text)
    filtrada = _text(client.get("/ejecucion?ejecucion=1&estado=finalizo_m2").text)
    assert "FIL-0001" in filtrada and etiqueta in filtrada


def test_a_form_launch_is_recorded_as_real_data_even_if_origin_is_posted(env: Env) -> None:
    # Sin declaración previa de la ingesta, y con un origen enviado a mano.
    with closing(dal.connect(env.db)) as conn:
        conn.execute("DELETE FROM m3_ingestas")
        conn.commit()
    response = _launch(_dal_client(env), env, data_origin="synthetic")
    assert response.status_code == 303
    with closing(dal.connect(env.db)) as conn:
        run = dal.get_run(conn, 1)
    assert run is not None and run.parametros["datos_sinteticos"] is False
    command = build_command(
        env.settings, run, env.settings.data_root / "processed/demo_canonico.parquet"
    )
    assert "--datos-reales" in command and "--datos-sinteticos" not in command


def test_form_cannot_contradict_an_existing_origin(env: Env, monkeypatch) -> None:
    monkeypatch.setenv("FAKE_ENGINE_ORIGIN_EXIT", "3")
    response = _launch(_dal_client(env), env)
    assert response.status_code == 200 and "origen incompatible del motor" in response.text
    assert env.rows("SELECT COUNT(*) FROM ejecuciones") == [(0,)]


def test_foundation_is_selectable_and_reaches_the_worker_and_results(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _scenario(monkeypatch, "ok")
    client = _dal_client(env)
    html = client.get(f"/ejecucion?ingest_id={env.ingest_id}").text
    control = re.search(r'<input[^>]*id="fam-foundation"[^>]*>', html)
    assert control is not None and "disabled" not in control.group(0)
    response = _launch(client, env, families=["classical", "foundation"], data_origin="real")
    assert response.status_code == 303
    ((parameters,),) = env.rows("SELECT parametros FROM configuraciones")
    assert json.loads(parameters)["families"] == ["classical", "foundation"]
    env.worker().run_next()
    assert ("foundation:chronos2",) in env.rows("SELECT DISTINCT modelo FROM tareas")
    detail = client.get("/resultados/sku/FIL-0001?ejecucion=1").text
    assert "Chronos-2" in detail and "Fundacional" in detail


def test_la_configuracion_encolada_espera_los_ajustes_del_motor(env: Env) -> None:
    client = _dal_client(env)
    assert _launch(client, env).status_code == 303
    text = _text(client.get("/ejecucion?ejecucion=1").text)
    assert "Lo informa el motor al terminar la ejecución" in text
    assert "None (recorte" not in text


def test_launching_the_same_run_again_says_it_already_exists(env: Env) -> None:
    client = _dal_client(env)
    _launch(client, env)

    again = _launch(client, env)

    assert again.headers["location"] == "/corridas/1/ejecucion?aviso=existente"
    assert env.rows("SELECT COUNT(*) FROM ejecuciones") == [(1,)]
    assert "Ya existía una ejecución" in client.get(again.headers["location"]).text


def test_the_form_reports_each_wrong_field_and_creates_nothing(env: Env) -> None:
    client = _dal_client(env)
    response = client.post(
        "/ejecucion/lanzar",
        data={
            "ingest_id": env.ingest_id,
            "metric": "mape",
            "min_train": "abc",
            "horizon": "0",
            "step": "7",
            "seasonality": "7",
            "trials": "-1",
            "seed": "0",
            "workers": "2",
        },
    )
    text = _text(response.text)

    assert response.status_code == 200
    assert "Mínimo de entrenamiento (días): escriba un número entero." in text
    assert "Horizonte (días): debe ser mayor que cero." in text
    assert "Configuraciones por SKU y familia: debe ser mayor que cero." in text
    assert "Elija al menos una familia de modelos." in text
    assert "Elija una de las métricas de la lista." in text
    assert 'value="abc"' in response.text  # what the operator typed is kept
    assert env.rows("SELECT COUNT(*) FROM ejecuciones") == [(0,)]


# Each SKU of the test ingest has 72 days. These settings need 43 days before the cut.
LONG_ENOUGH = {"min_train": "20", "step": "7"}


def test_launching_without_a_family_for_a_class_creates_nothing_and_says_which(
    env: Env,
) -> None:
    # The ingest has one smooth, one intermittent and one lumpy SKU; lumpy admits only
    # the classical and foundation families, intermittent also ml. Without any family for a
    # class, the engine's L2 fails for the whole run, so the form does not launch. It also names
    # the class that keeps one family, so one correction is enough.
    response = _launch(_dal_client(env), env, families=["ml", "dl"], **LONG_ENOUGH)
    text = _text(response.text)

    assert response.status_code == 200
    assert "1 SKU Grumosa queda sin modelo: agregue Clásica y Fundacional." in text
    assert "Sin ninguna familia para una clase, el motor no puede ejecutar." in text
    assert "1 SKU Intermitente queda con una sola familia" in text
    assert "FIL-0002" in text
    assert "Lanzar de todos modos" not in text
    assert env.rows("SELECT COUNT(*) FROM ejecuciones") == [(0,)]


def test_a_class_without_family_whose_skus_are_all_too_short_does_not_block_the_launch(
    env: Env,
) -> None:
    # Solo la clase suave alcanza 84 días; las demás quedan fuera antes de L2.
    ingest = _ingest_with(
        env,
        [
            {
                "sku_id": "FIL-0001",
                "n_periods": 200,
                "n_positive": 200,
                "adi": 1.0,
                "cv2": 0.1,
                "sku_class": "smooth",
            },
            {
                "sku_id": "FIL-0002",
                "n_periods": 72,
                "n_positive": 36,
                "adi": 2.0,
                "cv2": 0.1,
                "sku_class": "intermittent",
            },
            {
                "sku_id": "FIL-0003",
                "n_periods": 72,
                "n_positive": 36,
                "adi": 2.0,
                "cv2": 1.0,
                "sku_class": "lumpy",
            },
        ],
    )
    response = _launch(_dal_client(env), env, ingest_id=ingest, families=["ml", "dl"], step="14")
    text = _text(response.text)

    assert "queda sin modelo" not in text
    assert (
        "1 SKU Intermitente queda fuera por historia insuficiente con cualquier "
        "familia (menos de 84 días): FIL-0002 ." in text
    )
    assert "Lanzar de todos modos" in text


def test_one_family_for_a_class_warns_which_skus_lose_the_comparison(env: Env) -> None:
    # Con Clásica y ML, el SKU lumpy tiene una sola familia: el motor lo deja fuera de la
    # comparación (eligibility.fewer_than_two_families). El formulario lo avisa antes de lanzar.
    response = _launch(_dal_client(env), env, families=["classical", "ml"], **LONG_ENOUGH)
    text = _text(response.text)

    assert response.status_code == 200
    assert (
        "1 SKU Grumosa queda con una sola familia (Clásica): FIL-0003 . "
        "Para evaluarlo, agregue Fundacional." in text
    )
    assert '<span class="mono pred-nowrap">FIL-0003</span>' in response.text  # never split
    assert "si lanza así, esos SKU quedan fuera de la comparación y sin campeón." in text
    assert "Lanzar de todos modos" in text
    assert env.rows("SELECT COUNT(*) FROM ejecuciones") == [(0,)]


def test_a_sku_too_short_for_any_family_is_not_told_to_add_one(env: Env) -> None:
    ingest = _ingest_with(
        env,
        [
            {
                "sku_id": "FIL-0001",
                "n_periods": 200,
                "n_positive": 200,
                "adi": 1.0,
                "cv2": 0.1,
                "sku_class": "smooth",
            },
            {
                "sku_id": "FIL-0003",
                "n_periods": 72,
                "n_positive": 36,
                "adi": 2.0,
                "cv2": 1.0,
                "sku_class": "lumpy",
            },
        ],
    )
    response = _launch(
        _dal_client(env), env, ingest_id=ingest, families=["classical", "ml"], step="14"
    )
    text = _text(response.text)

    assert (
        "1 SKU Grumosa queda fuera por historia insuficiente con cualquier familia "
        "(menos de 84 días): FIL-0003 ." in text
    )
    assert "Para evaluarlos" not in text


def test_families_that_leave_no_class_two_say_the_run_elects_no_champion(env: Env) -> None:
    response = _launch(_dal_client(env), env, families=["classical"], **LONG_ENOUGH)
    text = _text(response.text)

    for sku in ("FIL-0001", "FIL-0002", "FIL-0003"):
        assert sku in text
    assert (
        "Con estas familias ningún SKU tiene dos familias que comparar: la ejecución no elige "
        "ningún campeón y puede detenerse en el ajuste de modelos." in text
    )
    assert "si lanza así, esos SKU quedan fuera" not in text
    assert "Lanzar de todos modos" in text


def _ingest_with(env: Env, metrics: list[dict[str, Any]]) -> str:
    """An accepted ingest with these topology rows, imported as the engine leaves it."""
    from pred_platform.dal.importacion import import_reports

    source = next((env.settings.data_root / "ingestas").glob(f"{env.ingest_id}.json"))
    report = json.loads(source.read_text())
    sha = "b" * 64
    report["ingest_id"] = report["source_file"]["sha256"] = sha
    report["validation"]["n_skus"] = report["published"]["n_skus"] = len(metrics)
    report["topology"]["metrics"] = metrics
    (env.settings.data_root / "ingestas" / f"{sha}.json").write_text(json.dumps(report))
    assert import_reports(env.db, env.settings.data_root).ok
    return sha


def test_a_class_larger_than_a_topology_page_is_named_whole_and_folded(env: Env) -> None:
    row = {"n_periods": 200, "n_positive": 50, "adi": 3.0, "cv2": 0.1}
    metrics = [{**row, "sku_id": "LIS-0001", "cv2": 0.0, "adi": 1.0, "sku_class": "smooth"}] + [
        {**row, "sku_id": f"INT-{i:04d}", "sku_class": "intermittent"} for i in range(551)
    ]
    ingest = _ingest_with(env, metrics)

    response = _launch(
        _dal_client(env), env, ingest_id=ingest, families=["classical", "dl"], **LONG_ENOUGH
    )
    text = _text(response.text)

    assert "551 SKU Intermitente quedan con una sola familia (Clásica)." in text
    assert "Ver los 551 SKU" in text
    assert "INT-0000" in text and "INT-0550" in text and " más" not in text


def test_the_refusal_counts_only_the_skus_long_enough_to_reach_l2(env: Env) -> None:
    row = {"n_periods": 200, "n_positive": 50, "adi": 3.0, "cv2": 2.0, "sku_class": "lumpy"}
    metrics = [
        {**row, "sku_id": "LIS-0001", "cv2": 0.0, "adi": 1.0, "sku_class": "smooth"},
        {**row, "sku_id": "GRU-0001"},
        {**row, "sku_id": "GRU-0002"},
        {**row, "sku_id": "GRU-0003", "n_periods": 24},  # shorter than the 43 days needed
    ]
    ingest = _ingest_with(env, metrics)

    response = _launch(
        _dal_client(env), env, ingest_id=ingest, families=["ml", "dl"], **LONG_ENOUGH
    )
    text = _text(response.text)

    assert "2 SKU Grumosa quedan sin modelo: agregue Clásica y Fundacional." in text
    assert "3 SKU Lumpy" not in text
    assert env.rows("SELECT COUNT(*) FROM ejecuciones") == [(0,)]


def test_the_operator_launches_anyway_after_the_warning(env: Env) -> None:
    client = _dal_client(env)
    warned = _launch(client, env, families=["classical", "ml"], **LONG_ENOUGH)
    key = re.search(r'name="confirmar" value="([^"]+)"', warned.text)
    assert key is not None

    response = _launch(
        client, env, families=["classical", "ml"], confirmar=key.group(1), **LONG_ENOUGH
    )

    assert response.status_code == 303
    ((parametros,),) = env.rows(
        "SELECT c.parametros FROM ejecuciones e JOIN configuraciones c ON c.id = e.configuracion_id"
    )
    assert json.loads(parametros)["families"] == ["classical", "ml"]


def test_a_confirmation_for_other_families_warns_again(env: Env) -> None:
    client = _dal_client(env)
    warned = _launch(client, env, families=["classical", "ml"], **LONG_ENOUGH)
    key = re.search(r'name="confirmar" value="([^"]+)"', warned.text)
    assert key is not None

    response = _launch(
        client, env, families=["classical", "dl"], confirmar=key.group(1), **LONG_ENOUGH
    )
    text = _text(response.text)

    assert response.status_code == 200
    assert "queda con una sola familia (Clásica): FIL-0003 ." in text
    assert env.rows("SELECT COUNT(*) FROM ejecuciones") == [(0,)]


def test_repeated_families_in_a_crafted_post_count_once(env: Env) -> None:
    response = _launch(
        _dal_client(env), env, families=["classical", "classical", "dl", "foundation"]
    )

    assert response.status_code == 303
    ((parametros,),) = env.rows("SELECT parametros FROM configuraciones")
    assert json.loads(parametros)["families"] == ["classical", "dl", "foundation"]


def test_a_family_outside_the_list_is_a_field_error_in_plain_words(env: Env) -> None:
    response = _launch(_dal_client(env), env, families=["classical", "quantum"])
    text = _text(response.text)

    assert response.status_code == 200
    assert "Elija solo familias de la lista." in text
    assert "families debe" not in text and "'classical'" not in text
    assert env.rows("SELECT COUNT(*) FROM ejecuciones") == [(0,)]


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("seed", str(2**63), "Semilla: el número es demasiado grande."),
        (
            "trials",
            str(2**63),
            "Configuraciones por SKU y familia: el número es demasiado grande.",
        ),
        ("workers", str(2**63), "Procesos en paralelo: el número es demasiado grande."),
        ("seed", "9" * 4301, "Semilla: el número es demasiado grande."),
        (
            "trials",
            "9" * 4301,
            "Configuraciones por SKU y familia: el número es demasiado grande.",
        ),
        ("workers", "9" * 4301, "Procesos en paralelo: el número es demasiado grande."),
        ("seed", "+" + "9" * 4301, "Semilla: el número es demasiado grande."),
        ("seed", "\u0669" * 4301, "Semilla: el número es demasiado grande."),
        (
            "trials",
            "+" + "9" * 4301,
            "Configuraciones por SKU y familia: el número es demasiado grande.",
        ),
        (
            "trials",
            "\u0669" * 4301,
            "Configuraciones por SKU y familia: el número es demasiado grande.",
        ),
        ("seed", "-" + "9" * 4301, "Semilla: debe ser cero o más."),
        (
            "trials",
            "-" + "9" * 4301,
            "Configuraciones por SKU y familia: debe ser mayor que cero.",
        ),
    ],
)
def test_a_number_sqlite_cannot_store_is_a_field_error_not_a_server_error(
    env: Env, field: str, value: str, message: str
) -> None:
    client = TestClient(_dal_client(env).app, raise_server_exceptions=False)

    response = _launch(client, env, **{field: value})

    assert response.status_code == 200
    assert message in _text(response.text)
    assert env.rows("SELECT COUNT(*) FROM ejecuciones") == [(0,)]
    assert _launch(client, env, **{field: str(2**63 - 1)}).status_code == 303


def test_leading_zeros_do_not_make_a_number_too_big(env: Env) -> None:
    response = _launch(_dal_client(env), env, seed="0" * 20 + "42", trials="0" * 20 + "3")

    assert response.status_code == 303
    ((seed, parametros),) = env.rows(
        "SELECT e.seed, c.parametros FROM ejecuciones e "
        "JOIN configuraciones c ON c.id = e.configuracion_id"
    )
    assert (seed, json.loads(parametros)["trials"]) == (42, 3)


@pytest.mark.parametrize("ingest_id", ["deadbeef", "z" * 64, "../" * 3])
def test_a_malformed_ingest_id_is_a_form_error_not_a_server_error(env: Env, ingest_id: str) -> None:
    client = TestClient(_dal_client(env).app, raise_server_exceptions=False)

    response = _launch(client, env, ingest_id=ingest_id)

    assert response.status_code == 200
    assert "Elija un archivo de datos aceptado en Datos." in _text(response.text)
    assert env.rows("SELECT COUNT(*) FROM ejecuciones") == [(0,)]


def test_families_that_give_every_class_two_launch_as_today(env: Env) -> None:
    response = _launch(_dal_client(env), env, families=["classical", "dl", "foundation"])

    assert response.status_code == 303
    ((parametros,),) = env.rows(
        "SELECT c.parametros FROM ejecuciones e JOIN configuraciones c ON c.id = e.configuracion_id"
    )
    assert json.loads(parametros)["families"] == ["classical", "dl", "foundation"]


def test_an_ingestion_that_is_not_accepted_cannot_be_launched(env: Env) -> None:
    from tests.test_worker import _fixture_id

    response = _dal_client(env).post(
        "/ejecucion/lanzar", data={"ingest_id": _fixture_id("failed"), **FORM}
    )

    assert response.status_code == 200 and "no fue aceptada" in response.text
    assert env.rows("SELECT COUNT(*) FROM ejecuciones") == [(0,)]


def test_the_form_offers_only_accepted_ingestions_and_preselects_the_requested_one(
    env: Env,
) -> None:
    html = _dal_client(env).get(f"/ejecucion?ingest_id={env.ingest_id}").text

    options = re.findall(r'<option value="([0-9a-f]{64})"( selected)?', html)
    assert [o[0] for o in options] == [env.ingest_id]  # the failed one is not offered
    assert options[0][1] == " selected"
    assert "Lanzar está deshabilitado" not in html


@pytest.mark.parametrize("origin", [True, None], ids=["sinteticos", "sin-declarar"])
def test_reanudar_una_corrida_con_origen_no_real_lo_rechaza_y_lanzar_abre_otra(
    env: Env, monkeypatch: pytest.MonkeyPatch, origin: bool | None
) -> None:
    _scenario(monkeypatch, "ok")
    client = _dal_client(env)
    assert _launch(client, env).status_code == 303
    stopped = client.post("/ejecucion/detener", data={"ejecucion": "1"}, follow_redirects=False)
    assert stopped.headers["location"] == "/corridas/1/ejecucion?aviso=detenida"
    _guardar_origen(env, origin)

    response = client.post("/ejecucion/reanudar", data={"ejecucion": "1"}, follow_redirects=False)

    assert response.status_code == 200
    assert "La plataforma solo ejecuta datos reales" in _text(response.text)
    assert env.rows("SELECT estado FROM ejecuciones") == [("detenida",)]
    assert env.worker().run_next() is False  # nada quedó en cola
    ((raw,),) = env.rows("SELECT parametros FROM configuraciones")
    assert json.loads(raw)["datos_sinteticos"] is origin
    relaunched = _launch(client, env)
    assert relaunched.headers["location"] == "/corridas/2/ejecucion?aviso=en_cola"


def test_a_run_followed_stopped_and_resumed_from_the_screen(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _scenario(monkeypatch, "slow")
    client = _dal_client(env)
    _launch(client, env)
    thread = threading.Thread(target=env.worker().run_next)
    thread.start()
    _wait(
        lambda: env.rows("SELECT COUNT(*) FROM tareas WHERE estado = 'ejecutando'")[0][0] > 0,
        what="a unit running",
    )

    live = client.get("/ejecucion")
    assert "every 2s" in live.text and "Ejecutando" in _text(live.text)
    assert '<span id="pulso-lectura">avance leído ahora</span>' in live.text  # it polls
    assert client.get("/ejecucion/estado").status_code == 200  # keep polling
    assert re.search(
        r'<button class="pred-btn pred-btn--secondary"[^>]*>\s*<svg[^>]*><use href="#i-stop"',
        live.text,
    )

    stopped = client.post("/ejecucion/detener", data={"ejecucion": "1"}, follow_redirects=False)
    thread.join(timeout=20)
    assert stopped.headers["location"] == "/corridas/1/ejecucion?aviso=detenida"

    after = client.get(stopped.headers["location"])
    text = _text(after.text)
    assert "Detenida" in text and "Se pidió detener la ejecución." in text
    assert "Reanude la ejecución cuando quiera." in text  # the next step for platform.run_stopped
    assert "La ejecución se detuvo a petición del operador." in text
    assert client.get("/ejecucion/estado").status_code == 286  # nothing left to poll

    _scenario(monkeypatch, "ok")
    resumed = client.post("/ejecucion/reanudar", data={"ejecucion": "1"}, follow_redirects=False)
    assert resumed.headers["location"] == "/corridas/1/ejecucion?aviso=reanudada"
    env.worker().run_next()
    done = client.get("/ejecucion").text
    assert "Completada" in _text(done) and 'href="/corridas/1/resultados"' in done
    assert env.rows("SELECT COUNT(*) FROM tareas") == [(9,)]  # the same rows, no duplicates


def test_resuming_a_legacy_run_lets_the_engine_report_its_evaluation(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _scenario(monkeypatch, "unit_fails")
    client = _dal_client(env)
    _launch(client, env)
    env.worker().run_next()
    legacy = env.rows("SELECT id FROM configuraciones")[0][0]
    with closing(sqlite3.connect(env.db)) as conn:
        conn.execute(
            "UPDATE configuraciones SET parametros = json_set(parametros, '$.aggregation', "
            "'media', '$.trim', 0.2) WHERE id = ?",
            (legacy,),
        )
        conn.commit()
    before = env.rows("SELECT parametros FROM configuraciones")
    assert env.rows("SELECT estado FROM ejecuciones") == [("completada_con_fallos",)]

    resumed = client.post("/ejecucion/reanudar", data={"ejecucion": "1"}, follow_redirects=False)

    assert resumed.headers["location"] == "/corridas/1/ejecucion?aviso=reanudada"
    assert env.rows("SELECT parametros FROM configuraciones") == before
    monkeypatch.setenv("FAKE_ENGINE_EVALUATION", '{"aggregation":"mediana","trim":0.0}')
    env.worker().run_next()
    parameters = json.loads(env.rows("SELECT parametros FROM configuraciones")[0][0])
    assert (parameters["aggregation"], parameters["trim"]) == ("mediana", 0.0)


def test_a_run_with_failures_offers_to_retry_and_each_failure_has_a_next_step(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _scenario(monkeypatch, "unit_fails")
    client = _dal_client(env)
    _launch(client, env)
    env.worker().run_next()

    text = _text(client.get("/ejecucion").text)
    # Failed tasks do not make the run read as a failure: it completed, and a sentence says
    # how many tasks failed.
    assert "Completada con fallos" not in text and "Reintentar las tareas con fallos" in text
    assert re.search(r"(Falló|Fallaron) \d+ de \d+ tareas de modelos\.", text)
    # A finished run is not working any more: no pulse, no moving ring.
    html = client.get("/ejecucion").text
    assert 'id="pulso-ejecucion"' not in html and "pred-progress__sweep" not in html
    assert "El modelo no pudo ajustarse." in text
    assert "Pruebe con más ensayos o con otra familia" in text
    assert "Ver resultados" in text


def test_on_a_read_only_database_the_actions_are_off_and_say_why_not_a_server_error(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _scenario(monkeypatch, "unit_fails")
    _launch(_dal_client(env), env)
    env.worker().run_next()
    before = env.rows("SELECT estado, error_json FROM ejecuciones")
    env.db.chmod(0o444)
    try:
        client = TestClient(_dal_client(env).app, raise_server_exceptions=False)
        page = client.get("/ejecucion?ejecucion=1").text
        response = client.post("/ejecucion/reanudar", data={"ejecucion": "1"})
    finally:
        env.db.chmod(0o644)

    reason = (
        "La base de datos es de solo lectura: se pueden consultar los resultados, pero no "
        "lanzar, detener ni reanudar."
    )
    assert reason in _text(page)
    assert re.search(r'id="accion-reanudar">.*?<button [^>]*disabled', page, re.S)
    assert response.status_code == 200 and reason in _text(response.text)
    assert env.rows("SELECT estado, error_json FROM ejecuciones") == before


READ_ONLY = (
    "La base de datos es de solo lectura: se pueden consultar los resultados, pero no "
    "lanzar, detener ni reanudar."
)


def _resume_button(html: str) -> str:
    found = re.search(r'id="accion-reanudar">.*?<button ([^>]*)>', html, re.S)
    assert found is not None
    return "disabled" if "disabled" in found.group(1) else "enabled"


@contextmanager
def _read_only(env: Env, layout: str) -> Iterator[None]:
    """Make the database read-only the way an operator does it, and undo it afterwards."""
    folder = env.db.parent
    if layout == "file":
        env.db.chmod(0o444)
    else:  # a rollback-journal copy in a folder without write permission
        with closing(sqlite3.connect(env.db)) as conn:
            conn.execute("PRAGMA journal_mode=DELETE")
        folder.chmod(0o555)
    try:
        yield
    finally:  # what an operator does to make the copy writable again (README)
        folder.chmod(0o755)
        for path in folder.glob(env.db.name + "*"):
            path.chmod(0o644)


@pytest.mark.parametrize("layout", ["file", "folder"])
def test_a_database_made_read_only_while_the_app_runs_disables_the_actions_at_once(
    env: Env, monkeypatch: pytest.MonkeyPatch, layout: str
) -> None:
    _scenario(monkeypatch, "unit_fails")
    client = TestClient(_dal_client(env).app, raise_server_exceptions=False)
    _launch(client, env)
    env.worker().run_next()
    assert _resume_button(client.get("/ejecucion?ejecucion=1").text) == "enabled"
    before = env.rows("SELECT estado, error_json FROM ejecuciones")

    with _read_only(env, layout):
        page = client.get("/ejecucion?ejecucion=1").text
        resumed = client.post("/ejecucion/reanudar", data={"ejecucion": "1"})
        launched = _launch(client, env, seed="18")

    assert _resume_button(page) == "disabled" and READ_ONLY in _text(page)
    assert resumed.status_code == 200 and READ_ONLY in _text(resumed.text)
    assert launched.status_code == 200 and READ_ONLY in _text(launched.text)
    assert env.rows("SELECT estado, error_json FROM ejecuciones") == before


def test_a_copy_made_writable_while_the_app_runs_can_be_resumed_without_a_restart(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _scenario(monkeypatch, "unit_fails")
    _launch(_dal_client(env), env)
    env.worker().run_next()
    with _read_only(env, "file"):
        client = TestClient(_dal_client(env).app)
        assert _resume_button(client.get("/ejecucion?ejecucion=1").text) == "disabled"

    resumed = client.post("/ejecucion/reanudar", data={"ejecucion": "1"}, follow_redirects=False)

    assert resumed.status_code == 303 and "aviso=reanudada" in resumed.headers["location"]
    assert env.rows("SELECT estado FROM ejecuciones") == [("pendiente",)]


def test_a_write_the_database_refuses_as_read_only_says_why_not_a_server_error(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A read-only mount, or a copy made read-only between the check and the write, is refused
    # only by the write itself. The check is made to miss it, the way it misses a mount.
    _scenario(monkeypatch, "unit_fails")
    client = TestClient(_dal_client(env).app, raise_server_exceptions=False)
    _launch(client, env)
    env.worker().run_next()
    before = env.rows("SELECT estado, error_json FROM ejecuciones")
    monkeypatch.setattr("pred_platform.worker.control.database_read_only", lambda path: False)

    with _read_only(env, "file"):
        page = client.get("/ejecucion?ejecucion=1").text
        resumed = client.post("/ejecucion/reanudar", data={"ejecucion": "1"})
        stopped = client.post("/ejecucion/detener", data={"ejecucion": "1"})
        launched = _launch(client, env, seed="18")

    assert _resume_button(page) == "enabled"  # the check saw nothing
    for response in (resumed, stopped, launched):
        assert response.status_code == 200 and READ_ONLY in _text(response.text)
    assert env.rows("SELECT estado, error_json FROM ejecuciones") == before


def test_reading_a_read_only_copy_keeps_it_read_only_until_its_sidecars_are_writable(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    # SQLite gives the -wal and -shm it creates the database file's permissions.
    _scenario(monkeypatch, "unit_fails")
    client = _dal_client(env)
    _launch(client, env)
    env.worker().run_next()
    env.db.chmod(0o444)
    reader = sqlite3.connect(env.db)
    reader.execute("SELECT COUNT(*) FROM ejecuciones").fetchone()
    try:
        env.db.chmod(0o644)
        still = _resume_button(client.get("/ejecucion?ejecucion=1").text)
    finally:
        reader.close()
        for path in env.db.parent.glob(env.db.name + "*"):
            path.chmod(0o644)

    assert still == "disabled"
    assert _resume_button(client.get("/ejecucion?ejecucion=1").text) == "enabled"


def test_a_queued_run_in_a_read_only_copy_does_not_promise_to_start(env: Env) -> None:
    _launch(_dal_client(env), env)
    with _read_only(env, "file"):
        text = _text(_dal_client(env).get("/ejecucion?ejecucion=1").text)

    assert READ_ONLY in text
    assert "empezará en cuanto el worker" not in text and "make worker" not in text


@pytest.mark.parametrize(
    "reference", ["99999999999999999999999", "9" * 4301, "²", "٣", "-1"], ids=str.__len__
)
def test_an_edited_run_number_is_never_a_server_error(
    env: Env, monkeypatch: pytest.MonkeyPatch, reference: str
) -> None:
    _scenario(monkeypatch, "unit_fails")
    client = TestClient(_dal_client(env).app, raise_server_exceptions=False)
    _launch(client, env)
    env.worker().run_next()
    query = {"ejecucion": reference}

    # The old links lead to the steps of a run that does not exist: a page that says so.
    for old in ("/ejecucion", "/resultados"):
        page = client.get(old, params=query)
        assert page.status_code == 404 and "Ejecución #1" not in page.text
        assert "No existe la corrida" in page.text
    assert client.get("/ejecucion/estado", params=query).status_code in (200, 286)
    assert client.get("/ejecucion/configuracion.json", params=query).status_code == 404
    assert client.get("/resultados/exportar.csv", params=query).status_code == 404
    for action in ("reanudar", "detener"):
        response = client.post(f"/ejecucion/{action}", data=query)
        assert response.status_code == 400
        assert "La ejecución indicada no es válida." in _text(response.text)
    assert env.rows("SELECT estado FROM ejecuciones") == [("completada_con_fallos",)]


def test_a_failed_stage_is_named_and_can_be_resumed(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The engine marks any stage that raises as failed and not retryable, also for a cause that
    # passes: the run keeps Reanudar.
    _scenario(monkeypatch, "stage_fails")
    client = _dal_client(env)
    _launch(client, env)
    env.worker().run_next()

    page = client.get("/ejecucion?ejecucion=1").text

    text = _text(page)
    assert "La etapa «Validación con ventanas» falló." in text
    assert "etapa platform" not in page and "etapa «Plataforma»" not in page
    assert text.count("La etapa «Validación con ventanas» falló") == 1
    assert "ninguna unidad evaluó" in text
    assert _resume_button(page) == "enabled"


def _stopped_in_l2(env: Env, monkeypatch: pytest.MonkeyPatch, message: str) -> TestClient:
    """A run stopped because its whole L2 stage failed with the engine's ``message``."""
    _scenario(monkeypatch, "stage_fails")
    client = _dal_client(env)
    _launch(client, env)
    env.worker().run_next()
    error = {
        "code": "pipeline.stage_failed",
        "stage": "L2",
        "severity": "error",
        "detail": message,
        "retryable": False,
    }
    with closing(sqlite3.connect(env.db)) as conn, conn:
        conn.execute("UPDATE ejecuciones SET estado='detenida', error_json=?", (json.dumps(error),))
    return client


@pytest.mark.parametrize(
    "message",
    [
        "RouterConfigurationError: la politica '2.2.0-initial' no produjo decisiones",
        "L2 sin resultados (0 unidades, 3 SKU excluidos): FIL-0001: historia insuficiente",
    ],
)
def test_an_l2_failure_that_a_resume_repeats_is_not_offered_again(
    env: Env, monkeypatch: pytest.MonkeyPatch, message: str
) -> None:
    client = _stopped_in_l2(env, monkeypatch, message)
    page = client.get("/ejecucion?ejecucion=1").text

    assert 'id="accion-reanudar"' not in page
    assert "Reanudar no se ofrece: la ejecución fallaría igual." in _text(page)
    # Resultados reads the same rule: the run is not shown as one that could be resumed.
    assert _run_state_tone(page) == "err"
    assert _run_state_tone(client.get("/resultados?ejecucion=1").text) == "err"


def test_an_l2_failure_of_some_units_can_still_be_resumed(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    message = "L2 sin resultados (6 unidades, 0 SKU excluidos): FIL-0001/ml: MemoryError"
    client = _stopped_in_l2(env, monkeypatch, message)
    page = client.get("/ejecucion?ejecucion=1").text

    assert _resume_button(page) == "enabled"
    assert "fallaría igual" not in page
    assert _run_state_tone(page) == "warn"
    assert _run_state_tone(client.get("/resultados?ejecucion=1").text) == "warn"


def test_a_run_whose_families_compare_nothing_is_not_offered_again(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _scenario(monkeypatch, "stage_fails")
    client = _dal_client(env)
    warned = _launch(client, env, families=["classical"], **LONG_ENOUGH)
    key = re.search(r'name="confirmar" value="([^"]+)"', warned.text)
    assert key is not None
    _launch(client, env, families=["classical"], confirmar=key.group(1), **LONG_ENOUGH)
    env.worker().run_next()

    page = client.get("/ejecucion?ejecucion=1").text

    assert env.rows("SELECT estado FROM ejecuciones") == [("detenida",)]
    assert 'id="accion-reanudar"' not in page
    assert (
        "Reanudar no se ofrece: con las familias de esta ejecución ningún SKU tiene dos familias "
        "que comparar" in _text(page)
    )
    # Resultados reads the same rule: the run is not shown as one that could be resumed.
    assert _run_state_tone(page) == "err"
    assert _run_state_tone(client.get("/resultados?ejecucion=1").text) == "err"


def test_with_the_worker_off_a_queued_run_waits_for_make_worker_unless_another_worker_runs(
    env: Env,
) -> None:
    client = _dal_client(env)
    _launch(client, env)
    hint = "hasta que se inicie el procesador de ejecuciones."

    def raw_pages() -> tuple[str, str]:
        run = client.get("/corridas/1/ejecucion").text
        return run, _review(client, env, seed="18")

    def pages() -> tuple[str, str]:
        r, rev = raw_pages()
        return _text(r), _text(rev)

    raw_run, raw_review = raw_pages()
    run, review = pages()
    assert "debe iniciar el procesador de ejecuciones." in run
    assert hint in review
    assert "make worker" in raw_run and "make worker" in raw_review
    assert "make worker" not in re.sub(r"<details[^>]*>.*?</details>", "", raw_run, flags=re.DOTALL)
    assert "make worker" not in re.sub(
        r"<details[^>]*>.*?</details>", "", raw_review, flags=re.DOTALL
    )
    other = WorkerLock.for_database(env.db)  # a `make worker` beside the app
    assert other.acquire()
    try:
        run, review = pages()
    finally:
        other.release()

    assert "make worker" not in run and "make worker" not in review
    assert "En cola: empezará cuando termine la ejecución anterior." in run
    assert hint in pages()[1]  # the lock is free again: nothing runs the queue


def test_with_the_launch_form_open_launching_is_the_one_primary_action(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _scenario(monkeypatch, "ok")
    client = _dal_client(env)
    _launch(client, env)
    env.worker().run_next()

    def primary(html: str) -> list[str]:
        """The primary actions of the step body; the step footer (Siguiente) is apart."""
        body = html.split('<nav class="pred-footnav"', 1)[0]
        pattern = r'class="pred-btn pred-btn--primary[^"]*"[^>]*>(.*?)</(?:a|button)>'
        return [_text(label).strip() for label in re.findall(pattern, body, re.S)]

    assert primary(client.get("/ejecucion").text) == ["Ver resultados"]
    assert primary(client.get("/ejecucion/estado").text) == ["Ver resultados"]
    # A new run is launched from Revisar, where Lanzar is the one primary action.
    assert primary(_review(client, env, seed="18")) == ["Lanzar la corrida"]


def test_a_second_run_waits_in_the_queue_and_the_screen_says_so(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    other = WorkerLock.for_database(env.db)  # busy with an earlier run: the app's worker waits
    assert other.acquire()
    try:
        with _dal_client(env, worker="embedded") as client:
            _launch(client, env)

            text = _text(client.get("/ejecucion").text)
    finally:
        other.release()
    assert "En cola: empezará cuando termine la ejecución anterior." in text
    assert "make worker" not in text


def _wal_copy_in_a_read_only_folder(env: Env) -> sqlite3.Connection:
    """A WAL copy whose file, -wal and -shm are writable in a folder without write permission.

    The returned reader keeps the -wal and -shm files, which SQLite removes with the last
    connection; the caller closes it after making the folder writable again.
    """
    reader = sqlite3.connect(env.db)
    reader.execute("SELECT COUNT(*) FROM ejecuciones").fetchone()
    assert {p.name for p in env.db.parent.glob(env.db.name + "-*")} == {
        env.db.name + "-wal",
        env.db.name + "-shm",
    }
    env.db.parent.chmod(0o555)
    return reader


def test_an_embedded_worker_that_cannot_create_its_lock_waits_and_resumes(
    env: Env, caplog: pytest.LogCaptureFixture
) -> None:
    client = _dal_client(env)
    _launch(client, env)
    reader = _wal_copy_in_a_read_only_folder(env)
    app = create_app(replace(env.settings, worker="embedded"))
    try:
        with TestClient(app) as served:
            thread = app.state.worker_thread
            _wait(lambda: "no puede crear su candado" in caplog.text)
            assert thread.is_alive()
            assert env.rows("SELECT estado FROM ejecuciones") == [("pendiente",)]
            run_page = _text(served.get("/ejecucion?ejecucion=1").text)
            form = _text(_review(served, env, seed="5"))
            assert "procesador de ejecuciones de esta instalación se detuvo" not in run_page
            assert "procesador de ejecuciones de esta instalación se detuvo" not in form
            env.db.parent.chmod(0o755)
            _wait(lambda: env.rows("SELECT estado FROM ejecuciones") == [("completada",)])
    finally:
        env.db.parent.chmod(0o755)
        reader.close()

    assert not thread.is_alive()
    assert env.rows("SELECT estado, pid FROM ejecuciones") == [("completada", None)]


def test_a_stopped_embedded_worker_is_reported_in_the_run_and_launch_form(env: Env, monkeypatch):
    _launch(_dal_client(env), env)
    monkeypatch.setattr(Worker, "run_forever", lambda self, stop: None)
    app = create_app(replace(env.settings, worker="embedded"))
    with TestClient(app) as served:
        _wait(lambda: not app.state.worker_thread.is_alive())
        run_page = _text(served.get("/ejecucion?ejecucion=1").text)
        form = _text(_review(served, env, seed="5"))
    assert "el procesador de ejecuciones de esta instalación se detuvo" in run_page
    assert "empezará cuando termine" not in run_page and "worker" not in run_page
    assert "El procesador de ejecuciones de esta instalación se detuvo" in form
    assert env.rows("SELECT estado FROM ejecuciones") == [("pendiente",)]


def test_once_without_a_lock_file_logs_why_and_leaves_the_queue(
    env: Env, caplog: pytest.LogCaptureFixture
) -> None:
    _launch(_dal_client(env), env)
    reader = _wal_copy_in_a_read_only_folder(env)
    try:
        with pytest.raises(PermissionError):
            env.worker().drain()
    finally:
        env.db.parent.chmod(0o755)
        reader.close()

    assert "no puede crear su candado" in caplog.text
    assert env.rows("SELECT estado FROM ejecuciones") == [("pendiente",)]


def test_actions_on_a_bad_or_finished_run_are_explained_not_applied(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _scenario(monkeypatch, "ok")
    client = _dal_client(env)
    _launch(client, env)
    env.worker().run_next()

    stop = client.post("/ejecucion/detener", data={"ejecucion": "1"}, follow_redirects=False)
    assert stop.headers["location"].endswith("aviso=no_aplica")
    assert (
        "ya no estaba en un estado que admita esa acción"
        in client.get(stop.headers["location"]).text
    )
    assert client.post("/ejecucion/detener", data={"ejecucion": "xx"}).status_code == 400


def test_the_data_surface_continues_to_the_settings_of_that_ingestion(env: Env) -> None:
    client = _dal_client(env)
    html = client.get(f"/datos?ingest_id={env.ingest_id}").text
    assert f'<input type="hidden" name="ingest_id" value="{env.ingest_id}">' in html

    chosen = client.post(
        "/corridas/nueva/datos", data={"ingest_id": env.ingest_id}, follow_redirects=False
    )
    assert (chosen.status_code, chosen.headers["location"]) == (303, "/corridas/nueva/ajustes")
    settings = client.get("/corridas/nueva/ajustes").text
    assert re.search(rf'<option value="{env.ingest_id}"[^>]*selected', settings)


# ------------------------------------------------------------------------------ las ayudas
def test_the_default_form_is_valid_and_matches_the_engines_defaults() -> None:
    values = default_form_values()
    result = parse_launch_form(
        families=values["families"],
        metric=values["metric"],
        fields={
            k: values[k]
            for k in ("min_train", "horizon", "step", "seasonality", "trials", "seed", "workers")
        },
    )

    assert result.errors == {} and result.settings is not None
    assert (
        result.settings.families == ("classical", "ml", "dl", "foundation")
        and result.settings.trials is None
    )
    assert (result.settings.min_train, result.settings.workers) == (40, 2)


def test_run_actions_follow_the_state_and_the_reason_the_control_is_off() -> None:
    assert run_actions("ejecutando", reason=None).can_stop
    assert not run_actions("ejecutando", reason=None).can_resume
    assert run_actions("detenida", reason=None).can_resume
    assert run_actions("completada_con_fallos", reason=None).can_resume
    assert not run_actions("completada", reason=None).can_stop
    off = run_actions("ejecutando", reason="datos de prueba")
    assert not off.can_stop and off.disabled_reason == "datos de prueba"
    assert run_actions(None, reason=None) == run_actions(None, reason=None)


def test_the_execution_query_round_trips_and_rejects_a_bad_page() -> None:
    query = parse_execution_query(
        {"ejecucion": "3", "estado": "fallida", "sku": " X ", "page": "2"}
    )
    assert query == ExecutionQuery(ejecucion="3", estado="fallida", sku="X", page=2)
    assert query.query_string() == "ejecucion=3&estado=fallida&sku=X&page=2"
    assert parse_execution_query({}).query_string() == ""
    with pytest.raises(InvalidQuery):
        parse_execution_query({"size": "mucho"})


def test_known_errors_have_a_next_step_and_unknown_ones_do_not() -> None:
    assert "otra familia" in (error_action("model.fit_failed") or "")
    assert error_action("some.new_code") is None


def test_every_eligibility_exclusion_tells_what_to_do() -> None:
    for code in (
        "eligibility.no_admissible_history",
        "eligibility.calendar_gaps",
        "eligibility.no_reserved_observations",
        "eligibility.non_finite_demand",
        "eligibility.insufficient_history",
    ):
        assert error_action(code), code


def _set_run(env: Env, estado: str, error: dict[str, Any] | None) -> None:
    with closing(sqlite3.connect(env.db)) as conn:
        conn.execute(
            "UPDATE ejecuciones SET estado = ?, error_json = ?",
            (estado, json.dumps(error) if error else None),
        )
        conn.commit()


def test_a_run_stopped_by_an_error_that_retrying_cannot_fix_offers_no_resume(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _scenario(monkeypatch, "ok")
    client = _dal_client(env)
    assert _launch(client, env).status_code == 303
    _set_run(
        env,
        "detenida",
        {
            "code": "run.incompatible_config",
            "stage": "platform",
            "severity": "error",
            "detail": "error: run_id incompatible con la entrada o configuracion previa",
            "retryable": False,
        },
    )

    html = client.get("/ejecucion").text
    text = _text(html)

    assert 'id="accion-reanudar"' not in html and "Reanudar" not in text.replace(
        "Reanudar no se ofrece", ""
    )
    assert "La configuración no coincide con la ejecución guardada." in text
    assert "run_id incompatible con la entrada o configuracion previa" in text
    assert "Reanudar no se ofrece: la ejecución fallaría igual." in text
    # Nothing to resume: the state and the notice read as an error, here and in Resultados.
    assert 'pred-callout--err" id="error-ejecucion"' in html
    assert _run_state_tone(html) == "err"
    assert _run_state_tone(client.get("/resultados").text) == "err"


def test_reuploading_missing_input_allows_the_same_run_to_resume(env: Env) -> None:
    client = TestClient(create_app(env.settings))
    content = b"sku_id,timestamp,demand_qty,lead_time_days\nFIL-0001,2026-01-01,4,7\n"
    env = replace(env, ingest_id=hashlib.sha256(content).hexdigest())

    def upload() -> None:
        response = client.post(
            "/datos/cargar", files={"archivo": ("ventas.csv", content)}, follow_redirects=False
        )
        assert response.status_code == 303
        assert env.ingest_id in response.headers["location"]

    def drain() -> None:
        result = subprocess.run(
            [sys.executable, "-m", "pred_platform.worker", "--once"],
            env={
                **os.environ,
                "PRED_DATA_ROOT": str(env.settings.data_root),
                "PRED_DB_PATH": str(env.db),
                "PRED_ENGINE_CMD": f"{sys.executable} {_FAKE}",
                "FAKE_ENGINE_SCENARIO": "ok",
            },
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert result.returncode == 0, result.stderr

    upload()
    assert _launch(client, env).status_code == 303
    [(run_id,)] = env.rows("SELECT run_id FROM ejecuciones")
    parquet = env.settings.data_root / "processed/ventas.parquet"
    parquet.unlink()  # El archivo se perdió después del lanzamiento y antes de la toma.
    drain()
    assert "No se encontró el archivo de datos" in client.get("/corridas/1/ejecucion").text

    upload()
    assert parquet.is_file()
    before = env.rows("SELECT * FROM ejecuciones")
    assert "aviso=existente" in _launch(client, env).headers["location"]
    assert env.rows("SELECT * FROM ejecuciones") == before
    page = client.get("/corridas/1/ejecucion").text
    assert _resume_button(page) == "enabled"
    assert "reanude esta ejecución" in _text(page)
    resumed = client.post("/ejecucion/reanudar", data={"ejecucion": "1"}, follow_redirects=False)
    assert "aviso=reanudada" in resumed.headers["location"]
    drain()
    assert env.rows("SELECT run_id, estado, intento FROM ejecuciones") == [
        (run_id, "completada", 2)
    ]
    assert env.rows("SELECT COUNT(*) FROM configuraciones") == [(1,)]
    assert 'data-pred-run-state="completada"' in client.get("/corridas/1/ejecucion").text


def test_a_run_whose_engine_died_offers_to_resume_even_if_stored_as_not_retryable(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _scenario(monkeypatch, "ok")
    client = _dal_client(env)
    assert _launch(client, env).status_code == 303
    # Lo que guardaba el worker antes cuando el motor moría sin publicar corrida.json.
    _set_run(
        env,
        "detenida",
        {
            "code": "platform.artifact_missing",
            "stage": "platform",
            "severity": "error",
            "detail": "El motor terminó con código -9 sin publicar corrida.json de este intento.",
            "retryable": False,
        },
    )

    html = client.get("/ejecucion").text

    assert 'id="accion-reanudar"' in html
    assert "Reanudar no se ofrece" not in _text(html)
    # The page offers Reanudar, so the stop is a warning, not a final error (#126).
    assert 'pred-callout--warn" id="error-ejecucion"' in html
    assert _run_state_tone(html) == "warn"
    assert _run_state_tone(client.get("/resultados").text) == "warn"


@pytest.mark.skipif(sys.platform == "win32", reason="SIGKILL no existe en Windows")
def test_a_run_whose_engine_is_killed_offers_to_resume_and_resumes(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _scenario(monkeypatch, "slow")
    client = _dal_client(env)
    _launch(client, env)
    thread = threading.Thread(target=env.worker().run_next)
    thread.start()
    _wait(
        lambda: env.rows("SELECT COUNT(*) FROM tareas WHERE estado = 'ejecutando'")[0][0] > 0,
        what="a unit running",
    )
    pid = env.rows("SELECT pid FROM ejecuciones")[0][0]

    os.kill(pid, signal.SIGKILL)  # lo que hace un OOM kill o un kill manual
    thread.join(timeout=20)

    ((estado, error_json),) = env.rows("SELECT estado, error_json FROM ejecuciones")
    error = json.loads(error_json)
    assert (estado, error["code"], error["retryable"]) == (
        "detenida",
        "platform.artifact_missing",
        True,
    )
    html = client.get("/ejecucion").text
    assert 'id="accion-reanudar"' in html
    assert "Reanudar no se ofrece" not in _text(html)

    _scenario(monkeypatch, "ok")
    resumed = client.post("/ejecucion/reanudar", data={"ejecucion": "1"}, follow_redirects=False)
    assert resumed.headers["location"] == "/corridas/1/ejecucion?aviso=reanudada"
    env.worker().run_next()
    assert env.rows("SELECT estado FROM ejecuciones") == [("completada",)]
    assert "Completada" in _text(client.get("/ejecucion").text)


def test_a_run_stopped_by_the_operator_shows_why_and_offers_to_resume(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _scenario(monkeypatch, "ok")
    client = _dal_client(env)
    assert _launch(client, env).status_code == 303
    _set_run(env, "detenida", run_stopped_error())

    html = client.get("/ejecucion").text

    assert 'id="accion-reanudar"' in html and 'id="error-ejecucion"' in html
    assert "Reanude la ejecución cuando quiera." in _text(html)
    # The stored detail repeats the catalog title: the reason reads once.
    callout = _text(html.split('id="error-ejecucion"', 1)[1].split("</div>", 1)[0])
    assert callout.count("La ejecución se detuvo a petición del operador.") == 1


def test_after_stop_the_page_keeps_polling_until_the_open_tasks_are_closed(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _scenario(monkeypatch, "ok")
    client = _dal_client(env)
    assert _launch(client, env).status_code == 303
    env.worker().run_next()
    # Detenida, pero el worker todavía no cerró la tarea que corría: los conteos cambiarán.
    _set_run(env, "detenida", run_stopped_error())
    with closing(sqlite3.connect(env.db)) as conn:
        conn.execute(
            "UPDATE tareas SET estado = 'ejecutando', finalizada_en = NULL "
            "WHERE id = (SELECT MIN(id) FROM tareas)"
        )
        conn.commit()

    assert client.get("/ejecucion/estado").status_code == 200
    assert "every 2s" in client.get("/ejecucion").text

    with closing(sqlite3.connect(env.db)) as conn:  # lo que hace el worker al recoger el motor
        dal.close_open_tasks(conn, 1, interrupted=run_stopped_error(), skipped=run_stopped_error())
    assert client.get("/ejecucion/estado").status_code == 286


def test_an_ingest_declared_synthetic_is_listed_but_cannot_be_launched(env: Env) -> None:
    report = env.settings.data_root / "ingestas" / f"{env.ingest_id}.json"
    document = json.loads(report.read_text())
    document["source_file"]["datos_sinteticos"] = True
    report.write_text(json.dumps(document))
    client = _dal_client(env)

    html = client.get("/corridas/nueva/ajustes").text
    option = re.search(rf'<option value="{env.ingest_id}"[^>]*>[^<]*', html)
    assert option is not None and " disabled" in option.group(0)
    assert "no ejecutable: datos sintéticos" in option.group(0)

    response = _launch(client, env)
    assert response.status_code == 200
    assert "El archivo elegido declara datos sintéticos" in _text(response.text)
    assert env.rows("SELECT COUNT(*) FROM ejecuciones") == [(0,)]
    assert "Declara datos sintéticos: no ejecutable" in _text(client.get("/datos").text)


def test_an_older_ingest_beyond_500_still_shows_its_filename(env: Env) -> None:
    client = _dal_client(env)
    _launch(client, env)
    with closing(sqlite3.connect(env.db)) as conn:
        for i in range(500):
            conn.execute(
                "INSERT INTO ingestas (sha256, nombre_archivo, filas, skus, estado) "
                "VALUES (?, ?, 10, 1, 'accepted')",
                (f"{i:064x}", f"dummy_{i}.csv"),
            )
        conn.commit()
    page = client.get("/ejecucion?ejecucion=1").text
    assert "demo_canonico.csv</a>" in page
    assert f'<span class="mono">{env.ingest_id[:12]}</span>' not in page


def test_la_ayuda_de_fundacional_dice_que_la_ejecucion_se_detiene(env: Env) -> None:
    html = _dal_client(env).get(f"/corridas/nueva/ajustes?ingest_id={env.ingest_id}").text
    assert (
        "Necesita el modelo Chronos-2 instalado en el equipo del motor; "
        "si falta, la ejecución se detiene al empezar y ninguna familia se ejecuta." in html
    )
    assert "sus tareas fallan con un aviso" not in html


def test_la_ayuda_de_no_ejecutable_dice_que_la_causa_dice_por_que(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _scenario(monkeypatch, "ok")
    client = _dal_client(env)
    _launch(client, env)
    env.worker().run_next()
    with closing(sqlite3.connect(env.db)) as conn:
        conn.execute(
            "UPDATE tareas SET estado = 'no_ejecutable', "
            'detalle_error = \'{"code": "eligibility.fewer_than_two_families", "stage": "L2"}\' '
            "WHERE id = (SELECT MIN(id) FROM tareas)"
        )
        conn.commit()
    page = client.get("/ejecucion?ejecucion=1").text
    assert (
        "El motor no ejecutó o no comparó este modelo para este producto; "
        "la causa de la fila dice por qué." in page
    )
    assert "no es un error del sistema" not in page


def test_ejecucion_detenida_sin_tareas_con_reanudar_no_pide_revisar_motor_log(env: Env) -> None:
    client = _dal_client(env)
    _launch(client, env)
    _set_run(env, "detenida", run_stopped_error())
    html = client.get("/ejecucion?ejecucion=1").text
    assert "Reanudar" in html
    assert "Revise el registro del motor" not in html


def test_ejecucion_detenida_sin_nada_que_comparar_no_dice_reanude(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _dal_client(env)
    warned = _launch(client, env, families=["classical"], **LONG_ENOUGH)
    key = re.search(r'name="confirmar" value="([^"]+)"', warned.text)
    assert key is not None
    _launch(client, env, families=["classical"], confirmar=key.group(1), **LONG_ENOUGH)
    _set_run(env, "detenida", run_stopped_error())
    page = client.get("/ejecucion?ejecucion=1").text
    assert "Reanudar no se ofrece" in page
    assert "Reanude la ejecución cuando quiera." not in page


def test_avisos_de_ejecucion_sin_textos_tecnicos(env: Env) -> None:
    client = _dal_client(env)
    valid_cfg = json.dumps(
        {
            "formato": "pred-platform.configuracion-ejecucion",
            "schema_version": 1,
            "run_id": "r-7383b31d3eea7528",
            "identidad": "r-7383b31d3eea7528",
            "ingesta_sha256": env.ingest_id,
            "parametros": {
                "min_train": 40,
                "horizon": 2,
                "step": 7,
                "seasonality": 7,
                "metric": "rmse",
                "families": ["classical", "ml"],
                "trials": 5,
                "seed": 42,
                "workers": 1,
            },
            "versiones": {"plataforma": "0.1.0"},
        }
    ).encode("utf-8")
    resp = client.post(
        "/ejecucion/configuracion",
        files={"archivo": ("corrida.json", valid_cfg, "application/json")},
    )
    assert resp.status_code == 200
    assert "Configuración importada. Revise los valores y continúe para lanzarla." in _text(
        resp.text
    )
    assert "r-7383b31d3eea7528" not in resp.text

    missing_cfg = json.dumps(
        {
            "formato": "pred-platform.configuracion-ejecucion",
            "schema_version": 1,
            "run_id": "r-7383b31d3eea7528",
            "identidad": "r-7383b31d3eea7528",
            "ingesta_sha256": "ab4d31bd58790000000000000000000000000000000000000000000000000000",
            "parametros": {
                "families": ["classical", "ml"],
                "metric": "rmse",
                "min_train": 40,
                "horizon": 2,
                "step": 7,
                "seasonality": 7,
                "trials": 5,
                "seed": 42,
                "workers": 1,
            },
            "versiones": {"plataforma": "0.1.0"},
        }
    ).encode("utf-8")
    resp_missing = client.post(
        "/ejecucion/configuracion",
        files={"archivo": ("corrida.json", missing_cfg, "application/json")},
    )
    assert resp_missing.status_code == 200
    assert "Configuración importada, pero sus datos no están cargados en esta plataforma." in _text(
        resp_missing.text
    )
    assert "ab4d31bd5879" not in resp_missing.text
    assert "r-7383b31d3eea7528" not in resp_missing.text

    _launch(client, env)
    incompat_error = {
        "code": "run.incompatible_config",
        "detail": "error: run_id incompatible con la entrada o configuracion previa",
        "stage": "L2",
    }
    _set_run(env, "detenida", incompat_error)
    html = client.get("/ejecucion?ejecucion=1").text
    callout_html = re.search(
        r'<div class="pred-callout[^"]*" id="error-ejecucion">(.*?)</div>', html, re.S
    )
    assert callout_html is not None
    details_part = re.search(r"<details[^>]*>(.*?)</details>", callout_html.group(1), re.S)
    assert details_part is not None and "run_id incompatible" in details_part.group(1)
    outside_details = re.sub(r"<details[^>]*>.*?</details>", "", callout_html.group(1), flags=re.S)
    assert "run_id incompatible" not in outside_details
    assert "La configuración no coincide con la ejecución guardada." in outside_details


def test_ejecucion_detenida_por_database_busy_antes_de_tareas(env: Env) -> None:
    client = _dal_client(env)
    _launch(client, env)
    db_busy = {
        "code": "platform.database_busy",
        "detail": "database is locked",
        "stage": "platform",
        "retryable": True,
    }
    _set_run(env, "detenida", db_busy)
    html = client.get("/ejecucion?ejecucion=1").text
    assert "La base de datos estaba ocupada y no se pudo registrar la ejecución." in html
    assert "Espere un momento y reanude la ejecución." in html
    assert "Reanudar" in html
    assert "Revise el registro del motor" not in html

    partial = client.get("/ejecucion/estado?ejecucion=1").text
    assert "La base de datos estaba ocupada y no se pudo registrar la ejecución." in partial
    assert "Espere un momento y reanude la ejecución." in partial
    assert "Revise el registro del motor" not in partial


def test_ejecucion_completada_con_solo_no_ejecutable_muestra_frase_correcta(env: Env) -> None:
    client = _dal_client(env)
    _launch(client, env)
    with closing(sqlite3.connect(env.db)) as conn:
        conn.execute("UPDATE ejecuciones SET estado = 'completada_con_fallos' WHERE id = 1")
        conn.execute(
            "INSERT INTO tareas "
            "(ejecucion_id, sku, modelo, corte, seed, estado, detalle_error) VALUES "
            "(1, 'FIL-0001', 'classical:sarima', '2024-01-01', 42, 'exitosa', NULL), "
            "(1, 'FIL-0002', 'classical:sarima', '2024-01-01', 42, 'no_ejecutable', "
            '\'{"code": "eligibility.fewer_than_two_families", "stage": "L2"}\')'
        )
        conn.commit()
    page = client.get("/ejecucion?ejecucion=1").text
    text = _text(page)
    assert "Ninguna tarea falló; 1 no se pudo ejecutar por las condiciones de su producto." in text
    assert "Terminó; algunas tareas de modelos fallaron o no se pudieron ejecutar." in page


def test_seleccion_sku_en_ejecucion_detenida_muestra_texto_propio(env: Env) -> None:
    client = _dal_client(env)
    _launch(client, env)
    _set_run(env, "detenida", run_stopped_error())
    doc = env.repo().list_sku_selections("1")
    assert doc.availability.status == "unavailable"
    assert doc.availability.detail == "La ejecución se detuvo antes de dejar la selección por SKU."


def test_un_solo_sku_dice_evaluarlo_y_varios_evaluarlos() -> None:
    from pred_platform.app.ejecucion import exclusion_warning

    warn_single = exclusion_warning(
        families=["classical", "ml"],
        by_class={"smooth": 0, "intermittent": 0, "lumpy": 1, "erratic": 0},
        skus={"lumpy": []},
        needed=40,
    )
    assert any("Para evaluarlo, agregue" in (part.advice or "") for part in warn_single.parts)

    warn_multi = exclusion_warning(
        families=["classical", "ml"],
        by_class={"smooth": 0, "intermittent": 0, "lumpy": 2, "erratic": 0},
        skus={"lumpy": []},
        needed=40,
    )
    assert any("Para evaluarlos, agregue" in (part.advice or "") for part in warn_multi.parts)


def test_the_search_budget_field_says_it_applies_to_each_sku(env: Env) -> None:
    client = _dal_client(env)
    page = _text(client.get(f"/corridas/nueva/ajustes?ingest_id={env.ingest_id}").text)
    assert "Configuraciones por SKU y familia" in page
    assert "Vacío: las que cada familia prueba por defecto en cada SKU." in page
    refused = _text(_launch(client, env, trials="0").text)
    assert "Configuraciones por SKU y familia: debe ser mayor que cero." in refused
