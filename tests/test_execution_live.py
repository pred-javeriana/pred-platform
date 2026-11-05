"""Step 4 through its public page and polling routes."""

from __future__ import annotations

import html
import json
import os
import re
from contextlib import closing
from typing import Any

import pytest

from pred_platform.dal import ejecuciones as dal
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
    assert repo.status_calls[1] == ("1", {"estado": "fallida", "size": 5})
    assert "pred-topbar" not in fragment.text
    repo.status_calls.clear()
    assert client.get("/ejecucion/estado?ejecucion=1&tareas=todas").status_code == 286
    assert len(repo.status_calls) == 1


def test_global_cause_groups_are_not_the_first_five_failures_or_the_filter(env: Env) -> None:
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
        response = client.get("/ejecucion/estado?ejecucion=1" + query)
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
