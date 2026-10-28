"""Superficie /ejecucion (C4): lanzar, seguir, detener y reanudar, con fixtures y DAL real."""

from __future__ import annotations

import json
import re
import sqlite3
import threading
from contextlib import closing
from dataclasses import replace
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
from pred_platform.contract.errors import InvalidQuery
from pred_platform.dal import ejecuciones as dal
from pred_platform.data.fixtures import FixtureRepository
from pred_platform.worker.engine import build_command
from tests.test_worker import Env, _scenario, _wait
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


# -------------------------------------------------------------------- con los fixtures
def test_the_active_run_shows_its_state_progress_settings_and_tasks() -> None:
    client, repo = _fixture_client("normal")
    response = client.get("/ejecucion")
    text = _text(response.text)

    assert response.status_code == 200 and repo.status_calls[-1][0] == "latest"
    assert "Ejecución #1" in text and "Ejecutando" in text
    assert "4 / 7 tareas" in text and "57.1 %" in text
    assert "Mínimo de entrenamiento 60 días" in text and "Corte t* 2026-06-29" in text
    for state in ("Pendiente", "Ejecutando", "Exitosa"):
        assert state in text
    assert "FIL-0001" in text and "classical:sarima" in text


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
    assert "Lo informa el motor al terminar la corrida" in response.text
    assert "recorte None" not in response.text


def test_every_task_state_has_icon_text_and_colour_and_errors_have_a_next_step() -> None:
    client, _ = _fixture_client("errores")
    html = client.get("/ejecucion").text

    for modifier in ("neutral", "info", "ok", "err", "warn"):
        assert f"pred-status--{modifier}" in html
    text = _text(html)
    assert "Fallida" in text and "No Ejecutable" in text
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
    missing = client.get("/ejecucion?ejecucion=99")
    assert missing.status_code == 200 and "Traceback" not in missing.text


def test_without_runs_or_ingestions_the_screen_guides_the_next_step() -> None:
    client, _ = _fixture_client("vacio")
    text = _text(client.get("/ejecucion").text)

    assert "No existe una ejecución activa." in text
    assert "Todavía no hay ninguna ingesta aceptada" in text


def test_the_write_actions_are_disabled_and_explained_with_fixtures() -> None:
    client, repo = _fixture_client("normal")
    html = client.get("/ejecucion").text

    assert "Nueva ejecución" in html
    ingest_id = next(
        item.ingest_id for item in repo.list_ingests().items if item.status == "accepted"
    )
    launch_html = client.get(f"/ejecucion?ingest_id={ingest_id}").text
    assert re.search(r'<button class="pred-btn pred-btn--primary"[^>]*disabled', launch_html)
    assert "Lanzar está deshabilitado. Estás viendo datos de prueba" in launch_html
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
    assert fragment.status_code == 286 and "pred-nav" not in fragment.text


# -------------------------------------------------------------------------- con el DAL real
def _dal_client(env: Env, **overrides: Any) -> TestClient:
    settings = replace(env.settings, **{"worker": "off", **overrides})
    app = create_app(settings)
    app.state.repository = env.repo()
    return TestClient(app)


FORM: dict[str, Any] = {
    "families": ["classical", "ml"],
    "metric": "mae",
    "min_train": "40",
    "horizon": "2",
    "step": "14",
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


def test_launching_from_the_form_queues_a_run_with_those_parameters(env: Env) -> None:
    client = _dal_client(env)

    response = _launch(client, env)

    assert response.status_code == 303
    assert response.headers["location"] == "/ejecucion?ejecucion=1&aviso=en_cola"
    ((estado, seed, parametros),) = env.rows(
        "SELECT e.estado, e.seed, c.parametros FROM ejecuciones e "
        "JOIN configuraciones c ON c.id = e.configuracion_id"
    )
    assert (estado, seed) == ("pendiente", 17)
    import json

    parsed = json.loads(parametros)
    assert parsed["families"] == ["classical", "ml"] and parsed["trials"] == 1
    assert (parsed["min_train"], parsed["horizon"], parsed["step"], parsed["metric"]) == (
        40,
        2,
        14,
        "mae",
    )
    page = client.get(response.headers["location"]).text
    assert "La ejecución quedó en cola" in page
    assert "Pendiente" in _text(page) and "PRED_WORKER=off" in page  # no worker in this app


@pytest.mark.parametrize(
    ("origin", "stored", "flag"),
    [
        ("real", False, "--datos-reales"),
        ("synthetic", True, "--datos-sinteticos"),
        ("unknown", None, None),
    ],
)
def test_form_origin_reaches_the_existing_worker_command(
    env: Env, origin: str, stored: bool | None, flag: str | None
) -> None:
    response = _launch(_dal_client(env), env, data_origin=origin)
    assert response.status_code == 303
    with closing(dal.connect(env.db)) as conn:
        run = dal.get_run(conn, 1)
    assert run is not None and run.parametros["datos_sinteticos"] is stored
    command = build_command(
        env.settings, run, env.settings.data_root / "processed/demo_canonico.parquet"
    )
    if flag is None:
        assert "--datos-reales" not in command and "--datos-sinteticos" not in command
    else:
        assert flag in command
        opposite = "--datos-sinteticos" if flag == "--datos-reales" else "--datos-reales"
        assert opposite not in command


def test_form_rejects_an_invalid_origin_and_keeps_valid_origin_after_other_errors(env: Env) -> None:
    client = _dal_client(env)
    invalid = _launch(client, env, data_origin="inventado")
    assert "Elija uno de los orígenes de la lista." in invalid.text
    assert 'id="data_origin"' in invalid.text and 'aria-invalid="true"' in invalid.text
    retry = _launch(client, env, data_origin="real", horizon="0")
    assert '<option value="real" selected>' in retry.text
    assert env.rows("SELECT COUNT(*) FROM ejecuciones") == [(0,)]


def test_form_cannot_contradict_an_existing_origin(env: Env, monkeypatch) -> None:
    monkeypatch.setenv("FAKE_ENGINE_ORIGIN_EXIT", "3")
    response = _launch(_dal_client(env), env, data_origin="synthetic")
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
    response = _launch(client, env, families=["foundation"], data_origin="real")
    assert response.status_code == 303
    ((parameters,),) = env.rows("SELECT parametros FROM configuraciones")
    assert json.loads(parameters)["families"] == ["foundation"]
    env.worker().run_next()
    assert env.rows("SELECT DISTINCT modelo FROM tareas") == [("foundation:chronos2",)]
    detail = client.get("/resultados/sku/FIL-0001?ejecucion=1").text
    assert "foundation:chronos2" in detail and "Fundacional" in detail


def test_la_configuracion_encolada_espera_los_ajustes_del_motor(env: Env) -> None:
    client = _dal_client(env)
    assert _launch(client, env).status_code == 303
    text = _text(client.get("/ejecucion?ejecucion=1").text)
    assert "Lo informa el motor al terminar la corrida" in text
    assert "None (recorte" not in text


def test_launching_the_same_run_again_says_it_already_exists(env: Env) -> None:
    client = _dal_client(env)
    _launch(client, env)

    again = _launch(client, env)

    assert again.headers["location"] == "/ejecucion?ejecucion=1&aviso=existente"
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
    assert "Ensayos por estudio: debe ser mayor que cero." in text
    assert "Elija al menos una familia de modelos." in text
    assert "Elija una de las métricas de la lista." in text
    assert 'value="abc"' in response.text  # what the operator typed is kept
    assert env.rows("SELECT COUNT(*) FROM ejecuciones") == [(0,)]


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
    assert client.get("/ejecucion/estado").status_code == 200  # keep polling
    assert re.search(
        r'<button class="pred-btn pred-btn--secondary"[^>]*>\s*<svg[^>]*><use href="#i-stop"',
        live.text,
    )

    stopped = client.post("/ejecucion/detener", data={"ejecucion": "1"}, follow_redirects=False)
    thread.join(timeout=20)
    assert stopped.headers["location"] == "/ejecucion?ejecucion=1&aviso=detenida"

    after = client.get(stopped.headers["location"])
    text = _text(after.text)
    assert "Detenida" in text and "Se pidió detener la ejecución." in text
    assert "Reanude la ejecución cuando quiera." in text  # the next step for platform.run_stopped
    assert "La ejecución se detuvo a petición del operador." in text
    assert client.get("/ejecucion/estado").status_code == 286  # nothing left to poll

    _scenario(monkeypatch, "ok")
    resumed = client.post("/ejecucion/reanudar", data={"ejecucion": "1"}, follow_redirects=False)
    assert resumed.headers["location"] == "/ejecucion?ejecucion=1&aviso=reanudada"
    env.worker().run_next()
    done = client.get("/ejecucion").text
    assert "Completada" in _text(done) and 'href="/resultados?ejecucion=1"' in done
    assert env.rows("SELECT COUNT(*) FROM tareas") == [(6,)]  # the same rows, no duplicates


def test_resuming_a_legacy_run_lets_the_engine_report_its_evaluation(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _scenario(monkeypatch, "unit_fails")
    client = _dal_client(env)
    _launch(client, env, families=["classical", "ml"])
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

    assert resumed.headers["location"] == "/ejecucion?ejecucion=1&aviso=reanudada"
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
    _launch(client, env, families=["classical", "ml"])
    env.worker().run_next()

    text = _text(client.get("/ejecucion").text)
    assert "Completada con fallos" in text and "Reintentar las tareas con fallos" in text
    assert "El modelo no pudo ajustarse." in text
    assert "Pruebe con más ensayos o con otra familia" in text
    assert "Ver resultados" in text


def test_a_second_run_waits_in_the_queue_and_the_screen_says_so(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _dal_client(env, worker="embedded")
    _launch(client, env)

    text = _text(client.get("/ejecucion").text)
    assert "empezará en cuanto el worker" in text and "PRED_WORKER=off" not in text


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


def test_the_data_surface_continues_to_the_execution_of_that_ingestion(env: Env) -> None:
    client = _dal_client(env)
    html = client.get(f"/datos?ingest_id={env.ingest_id}").text

    assert f'href="/ejecucion?ingest_id={env.ingest_id}"' in html


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
    assert result.settings.families == ("classical", "ml", "dl") and result.settings.trials is None
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
