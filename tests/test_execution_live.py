"""Step 4 through its public page and polling routes."""

from __future__ import annotations

import html
import json
import os
import re
from contextlib import closing
from datetime import datetime
from typing import Any

import pytest

from pred_platform.dal import ejecuciones as dal
from pred_platform.dal import lectura
from pred_platform.data import run_history
from pred_platform.data.dal import DalRepository
from pred_platform.data.fixtures import FixtureRepository
from pred_platform.data.run_status import StageReference, StageTiming
from tests.test_ejecucion import _dal_client, _fixture_client, _launch, _text
from tests.test_worker import Env, _escribir_progreso
from tests.test_worker import env as env  # noqa: F401


def _snapshot(body: str) -> dict[str, Any]:
    found = re.search(r"data-pred-live='([^']+)'", body)
    assert found is not None
    return json.loads(html.unescape(found[1]))


def _running(env: Env):
    client = _dal_client(env)
    assert _launch(client, env).status_code == 303
    with closing(dal.connect(env.db)) as conn:
        run = dal.claim_pending(conn)
        assert run is not None
        dal.set_launch(conn, run.id, pid=os.getpid(), ruta_corrida=f"runs/{run.run_id}")
    return client, run


def _navigation(body: str, element_id: str) -> str:
    found = re.search(rf'<nav\b[^>]*\bid="{element_id}".*?</nav>', body, re.S)
    assert found is not None
    return _text(found[0])


def test_polled_navigation_moves_from_queue_to_published_without_reloading(
    env: Env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _dal_client(env)
    assert _launch(client, env).status_code == 303
    queued = client.get("/corridas/1/ejecucion").text
    assert "En cola" in _navigation(queued, "barra-pasos")
    assert "Parciales" in _navigation(queued, "navegacion-pasos")
    first = client.get("/ejecucion/estado?ejecucion=1")
    assert first.status_code == 200
    assert 'id="barra-pasos"' in first.text and 'hx-swap-oob="true"' in first.text
    monkeypatch.setenv("FAKE_ENGINE_SCENARIO", "m3_ok")
    assert env.worker().run_next()
    unpublished = client.get("/ejecucion/estado?ejecucion=1")
    assert unpublished.status_code == 286
    assert "Sin evaluación final publicada" in _navigation(unpublished.text, "barra-pasos")
    # Only the data step, which polling does not reread, keeps its old markup.
    assert "Lanzada · en cola" in _navigation(queued, "barra-pasos")
    assert "Lanzada · en cola" not in _navigation(unpublished.text, "barra-pasos")
    assert re.search(r'id="paso-revisar"(?! hx-preserve)', unpublished.text)
    assert re.search(r'id="paso-datos" hx-preserve="true"', unpublished.text)
    assert "Parciales" not in _navigation(unpublished.text, "navegacion-pasos")
    # The engine double announces publication but does not write M3 rows. Supply the
    # published repository documents separately, as when the real engine commits its evidence.
    publication = FixtureRepository()
    runs = publication.list_m3_runs()
    runs = runs.model_copy(
        update={"items": [item.model_copy(update={"run_id_m2": "1"}) for item in runs.items]}
    )
    monkeypatch.setattr(DalRepository, "list_m3_runs", lambda self, **kwargs: runs)
    monkeypatch.setattr(
        DalRepository,
        "get_m3_evidence",
        lambda self, *a, **kw: publication.get_m3_evidence(*a, **kw),
    )
    final = client.get("/ejecucion/estado?ejecucion=1")
    assert final.status_code == 286
    assert "Terminó en" in _navigation(final.text, "barra-pasos")
    assert "Terminó en" in _navigation(final.text, "resumen-paso")
    assert "Publicados" in _navigation(final.text, "barra-pasos")
    assert "Parciales" not in _navigation(final.text, "navegacion-pasos")
    assert 'id="paso-despues"' in final.text and "Ya están publicados." in final.text
    assert "pred-topbar" not in final.text


def test_polled_navigation_tracks_each_stage_without_reading_navigation_again(
    env: Env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, run = _running(env)

    def forbidden(*args, **kwargs):
        pytest.fail("Polling must reuse its status, not reread run navigation")

    monkeypatch.setattr("pred_platform.app.routers.execution.run_facts", forbidden)
    monkeypatch.setattr(lectura, "task_causes", forbidden)
    monkeypatch.setattr(lectura, "list_runs", forbidden)
    for code, number in (("L2", 2), ("L3", 3), ("L4", 4)):
        _escribir_progreso(env, run.run_id, {"event": "stage_started", "stage": code})
        response = client.get("/ejecucion/estado?ejecucion=1")
        assert response.status_code == 200
        assert f"Etapa {number} de 4" in _navigation(response.text, "barra-pasos")
        assert f"Etapa {number} de 4" in _navigation(response.text, "resumen-paso")
        assert 'id="paso-datos"' in response.text and 'hx-preserve="true"' in response.text


def test_polled_navigation_follows_a_resumed_run_from_queue_to_its_stage(env: Env) -> None:
    client = _dal_client(env)
    assert _launch(client, env).status_code == 303
    stop = client.post("/ejecucion/detener", data={"ejecucion": "1"}, follow_redirects=False)
    assert stop.status_code == 303
    assert "Detenida" in _navigation(client.get("/corridas/1/ejecucion").text, "barra-pasos")
    resume = client.post("/ejecucion/reanudar", data={"ejecucion": "1"}, follow_redirects=False)
    assert resume.headers["location"] == "/corridas/1/ejecucion?aviso=reanudada"
    assert "En cola" in _navigation(client.get(resume.headers["location"]).text, "barra-pasos")
    with closing(dal.connect(env.db)) as conn:
        run = dal.claim_pending(conn)
        assert run is not None
        dal.set_launch(conn, run.id, pid=os.getpid(), ruta_corrida=f"runs/{run.run_id}")
    _escribir_progreso(env, run.run_id, {"event": "stage_started", "stage": "L2"})
    polled = client.get("/ejecucion/estado?ejecucion=1")
    assert polled.status_code == 200
    for element_id in ("barra-pasos", "resumen-paso"):
        navigation = _navigation(polled.text, element_id)
        assert "Etapa 2 de 4" in navigation
        assert "En cola" not in navigation and "Detenida" not in navigation


def test_step_four_keeps_one_poll_and_no_new_repository_reads() -> None:
    client, repo = _fixture_client("errores")
    page = client.get("/corridas/1/ejecucion")
    assert page.status_code == 200
    assert 'id="desde-ultima-visita"' in page.text
    assert page.text.count('src="/static/js/live.js') == 1
    assert page.text.count('hx-get="/ejecucion/estado') == 1
    assert "Puede cerrar esta página" in page.text
    assert page.text.index('id="ejecucion-activa"') < page.text.index('id="titulo-causas"')
    repo.status_calls.clear()
    fragment = client.get("/ejecucion/estado?ejecucion=1")
    assert fragment.status_code == 286  # fixture mode never polls a worker
    assert len(repo.status_calls) == 2  # original status and the existing focused list
    assert repo.status_calls[1] == ("1", {"estado": "ejecutando", "size": 5})
    assert "pred-topbar" not in fragment.text
    repo.status_calls.clear()
    assert client.get("/ejecucion/estado?ejecucion=1&tareas=todas").status_code == 286
    assert len(repo.status_calls) == 1


def test_stage_history_is_loaded_once_per_page_and_never_by_polling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, repo = _fixture_client()
    calls = []

    def history(run_id: str):
        calls.append(run_id)
        return [
            StageReference(
                id="8",
                seed=0,
                timings=[
                    StageTiming(
                        code="L2",
                        started_at=datetime.fromisoformat("2026-11-04T20:00:00Z"),
                        finished_at=datetime.fromisoformat("2026-11-04T20:10:00Z"),
                    )
                ],
            )
        ]

    monkeypatch.setattr(repo, "get_run_stage_history", history)
    page = client.get("/corridas/1/ejecucion")
    assert page.status_code == 200 and calls == ["1"]
    assert "Semilla 0" in page.text and "Antes: 10 min" in page.text
    assert "pred-stage-reference" in page.text
    calls.clear()
    for _ in range(3):
        assert client.get("/ejecucion/estado?ejecucion=1").status_code == 286
    assert calls == []


def test_history_is_cached_and_excludes_runs_with_other_settings(
    env: Env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(run_history, "monotonic", lambda: 1000)
    client, _ = _running(env)
    for seed, metric in ((18, "mae"), (19, "rmse")):
        assert _launch(client, env, seed=str(seed), metric=metric).status_code == 303
    with closing(dal.connect(env.db)) as conn:
        refs = conn.execute("SELECT id, run_id FROM ejecuciones WHERE id IN (2,3)").fetchall()
        for run_id, engine_id in refs:
            conn.execute(
                "UPDATE ejecuciones SET estado='completada', ruta_corrida=?, finalizada_en=? "
                "WHERE id=?",
                (f"runs/{engine_id}", "2026-11-04T20:10:00Z", run_id),
            )
            _escribir_progreso(
                env,
                engine_id,
                {"event": "stage_started", "stage": "L2", "t": "2026-11-04T20:00:00Z"},
                {"event": "stage_finished", "stage": "L2", "t": "2026-11-04T20:10:00Z"},
            )
        conn.commit()
    original = lectura.list_runs
    reads = []

    def listing(path):
        reads.append(path)
        return original(path)

    assert _launch(client, env, seed="20", metric="mae").status_code == 303
    monkeypatch.setattr(lectura, "list_runs", listing)
    for _ in range(2):
        body = client.get("/corridas/4/ejecucion").text
        assert "Semilla 18" in body and "Semilla 19" not in body
    assert len(reads) == 1
    # "Antes" lists earlier runs only: never a later run, never the run itself.
    assert "Semilla 18" not in client.get("/corridas/1/ejecucion").text
    assert "pred-stage-reference" not in client.get("/corridas/2/ejecucion").text
    reads.clear()
    assert client.get("/ejecucion/estado?ejecucion=1").status_code == 200
    assert reads == []


def test_global_cause_groups_are_not_the_first_five_failures_or_the_filter(
    env: Env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, run = _running(env)
    with closing(dal.connect(env.db)) as conn:
        for n, (model, code) in enumerate(
            [("classical:sarima", "selection.no_viable_trial")] * 7
            + [("ml:lightgbm", "selection.no_viable_trial")] * 3
            + [("dl:mlp", "model.degenerate_forecast")] * 2
        ):
            dal.task_stage_finished(
                conn,
                run.id,
                sku=f"sku-{n:02}",
                modelo=model,
                corte="2026-06-29",
                seed=run.seed,
                stage="L2",
                completed=False,
                seconds=1.0,
                finished_at="2026-11-04T21:00:00Z",
                error={"code": code, "stage": "L2", "severity": "error"},
            )
    for query in ("", "&estado=fallida&family=dl&size=1", "&page=2&size=2"):
        response = client.get("/corridas/1/ejecucion?ejecucion=1" + query)
        assert response.status_code == 200
        group_table = response.text.split('aria-label="Causas de las tareas sin pronóstico"')[
            1
        ].split("</table>")[0]
        assert re.search(
            r"Clásica.*Aprendizaje automático|Aprendizaje automático.*Clásica", _text(group_table)
        )
        assert '<td class="num">10</td>' in group_table
        assert '<td class="num">2</td>' in group_table
        assert group_table.count("selection.no_viable_trial") == 1
        assert _snapshot(response.text)["counts"]["fallida"] == 12
    full = client.get("/corridas/1/ejecucion?tareas=todas&size=25")
    assert '<details class="pred-details" id="lista-tareas" open>' in full.text
    assert all(f"sku-{n:02}" in full.text for n in range(12))
    assert 'id="causas-corrida" hx-preserve="true"' in full.text
    assert "Causas leídas" in full.text and "Actualizar causas" in full.text
    # The time ends in "a. m." or "p. m.", so a following period would double it.
    assert re.search(r"Causas leídas <time[^>]*>[^<]*</time> · Este resumen", full.text)

    def no_cause_query(*args):
        pytest.fail("Polling must not aggregate causes")

    monkeypatch.setattr(lectura, "task_causes", no_cause_query)
    fragment = client.get("/ejecucion/estado?ejecucion=1")
    assert fragment.status_code == 200
    assert "selection.no_viable_trial" not in fragment.text.split('id="lista-tareas"')[0]


def test_stage_durations_and_product_progress_follow_the_log_and_retry(env: Env) -> None:
    client, run = _running(env)
    _escribir_progreso(
        env,
        run.run_id,
        {"event": "run_started", "t": "2026-11-04T20:00:00Z"},
        {"event": "stage_started", "stage": "L1", "t": "2026-11-04T20:00:00Z"},
        {"event": "stage_finished", "stage": "L1", "t": "2026-11-04T20:02:00Z"},
        {"event": "stage_started", "stage": "L4", "t": "2026-11-04T20:05:00Z"},
        {"event": "planned", "stage": "L4", "units": [{"sku_id": s} for s in "ABC"]},
        {"event": "unit_finished", "stage": "L4", "sku_id": "A"},
        {"event": "unit_finished", "stage": "L4", "sku_id": "A"},
    )
    response = client.get("/ejecucion/estado?ejecucion=1")
    snapshot = _snapshot(response.text)
    assert snapshot["skus"] == 1
    assert snapshot["stages"][0]["duration"] == "2 min"
    assert snapshot["stages"][0]["finished_at"] == "2026-11-04T20:02:00+00:00"
    assert "1 de 3 productos terminados" in _text(response.text)
    assert "Siga el conteo de productos" in response.text
    _escribir_progreso(
        env,
        run.run_id,
        {"event": "run_started", "t": "2026-11-04T22:00:00Z"},
        {"event": "stage_started", "stage": "L2", "t": "2026-11-04T22:00:00Z"},
        {"event": "planned", "stage": "L2", "units": [{"sku_id": "Z"}]},
    )
    retry = _snapshot(client.get("/ejecucion/estado?ejecucion=1").text)
    assert retry["stage"] == "L2" and retry["skus"] == 0
    assert retry["stages"][0]["duration"] is None
    assert retry["stages"][1]["started_at"] == "2026-11-04T22:00:00+00:00"


@pytest.mark.parametrize(
    "bad", ["{broken", "null", "[]", '{"event":"planned","stage":"L2","units":null}']
)
def test_partial_or_invalid_log_records_do_not_break_the_page(env: Env, bad: str) -> None:
    client, run = _running(env)
    path = env.settings.data_root / "runs" / run.run_id / "progreso.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"event":"stage_started","stage":"L2","t":"not-a-time"}\n' + bad)
    response = client.get("/ejecucion/estado?ejecucion=1")
    assert response.status_code == 200
    assert _snapshot(response.text)["stage"] == "L2"
    assert _snapshot(response.text)["stages"][1]["duration"] is None
    assert "Trabajando en Ajuste de modelos" in _text(response.text)


def test_finished_and_stopped_pages_do_not_claim_to_be_working(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, repo = _fixture_client()
    status = repo.get_run_status("1")
    for state in ("completada", "detenida"):
        assert status.ejecucion is not None
        status.ejecucion.estado = state
        monkeypatch.setattr(repo, "get_run_status", lambda *a, **kw: status)
        body = client.get("/corridas/1/ejecucion").text
        assert "pulso-ejecucion" not in body and "pred-progress__sweep" not in body
        assert "Puede cerrar esta página" not in body
        assert ("Reanudar" if state == "detenida" else "Ver resultados") in body
