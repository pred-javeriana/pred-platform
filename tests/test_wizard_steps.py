"""Los pasos editables y sus registros, por las mismas rutas que usa el navegador."""

from __future__ import annotations

import json
import re
import sqlite3
from contextlib import closing
from datetime import datetime
from html import unescape
from typing import Any

import pytest

from pred_platform.data.fixtures import FixtureRepository
from tests.test_corridas import _fixture_client
from tests.test_ejecucion import FORM, _dal_client, _launch, _review, _text
from tests.test_ui_review import Markup
from tests.test_worker import Env
from tests.test_worker import env as env  # noqa: F401


def _execute(env: Env, sql: str, params: tuple[object, ...] = ()) -> None:
    with closing(sqlite3.connect(env.db)) as conn, conn:
        conn.execute(sql, params)


def _launch_payload(html: str) -> dict[str, Any]:
    found = re.search(r'<form[^>]*action="/ejecucion/lanzar".*?</form>', html, re.S)
    assert found is not None
    payload: dict[str, Any] = {}
    for name, value in re.findall(r'<input type="hidden" name="([^"]+)" value="([^"]*)"', found[0]):
        if name == "families":
            payload.setdefault(name, []).append(unescape(value))
        else:
            payload[name] = unescape(value)
    return payload


def test_copy_keeps_every_stored_setting_and_never_launches_or_resumes(env: Env) -> None:
    client = _dal_client(env)
    _launch(client, env, seasonality="14", trials="3", workers="7", seed="1")
    before = env.rows("SELECT estado, finalizada_en FROM ejecuciones")

    copied = client.post("/corridas/nueva/copiar", data={"copiar": "1"})

    assert copied.status_code == 200
    assert "Ajustes copiados de la corrida #1" in _text(copied.text)
    assert "Revise la semilla" in copied.text
    assert env.rows("SELECT estado, finalizada_en FROM ejecuciones") == before
    # The copy survives navigation without pressing Continuar first.
    html = client.get("/corridas/nueva/ajustes").text
    for name, expected in {
        "min_train": "40",
        "horizon": "2",
        "step": "14",
        "seasonality": "14",
        "trials": "3",
        "seed": "1",
        "workers": "7",
    }.items():
        assert re.search(rf'name="{name}"[^>]*value="{expected}"', html), name
    review = client.get("/corridas/nueva/revisar").text
    assert _launch_payload(review) == {
        "ingest_id": env.ingest_id,
        **FORM,
        "seasonality": "14",
        "trials": "3",
        "workers": "7",
        "seed": "1",
    }
    assert "Estos ajustes son los de la corrida #1" in _text(review)
    assert re.search(r"<button[^>]*disabled[^>]*>.*?Lanzar la corrida", review, re.S)
    assert 'href="/corridas/1/ejecucion"' in review


def test_copying_a_missing_run_does_not_replace_the_draft(env: Env) -> None:
    client = _dal_client(env)
    _review(client, env, seed="123")
    missing = client.post("/corridas/nueva/copiar", data={"copiar": "999"})
    assert missing.status_code == 404
    assert "No existe esa corrida para copiar." in missing.text
    assert _launch_payload(client.get("/corridas/nueva/revisar").text)["seed"] == "123"
    assert env.rows("SELECT COUNT(*) FROM ejecuciones") == [(0,)]


@pytest.mark.parametrize(
    "state", ["pendiente", "ejecutando", "detenida", "completada", "completada_con_fallos"]
)
def test_duplicate_review_and_replayed_launch_do_not_change_an_existing_run(
    env: Env, state: str
) -> None:
    client = _dal_client(env)
    _launch(client, env)
    _execute(
        env,
        "UPDATE ejecuciones SET estado=?, iniciada_en='2026-11-02T00:00:00Z', "
        "finalizada_en='2026-11-03T02:00:00Z' WHERE id=1",
        (state,),
    )
    before = env.rows("SELECT * FROM ejecuciones")
    # workers does not belong to a run's identity.
    review = _review(client, env, workers="7")
    assert "Estos ajustes son los de la corrida #1" in _text(review)
    assert re.search(r"<button[^>]*disabled[^>]*>.*?Lanzar la corrida", review, re.S)

    replayed = client.post(
        "/ejecucion/lanzar", data=_launch_payload(review), follow_redirects=False
    )
    assert (replayed.status_code, replayed.headers["location"]) == (
        303,
        "/corridas/1/ejecucion?aviso=existente",
    )
    assert env.rows("SELECT * FROM ejecuciones") == before


@pytest.mark.parametrize("metric", ["rmse", "mae", "smape", "mase"])
@pytest.mark.parametrize("trials", ["", "2"])
def test_review_launches_exactly_the_values_it_displays(env: Env, metric: str, trials: str) -> None:
    client = _dal_client(env)
    chosen = {**FORM, "metric": metric, "trials": trials, "seed": "2", "workers": "3"}
    saved = client.post(
        "/corridas/nueva/ajustes",
        data={"ingest_id": env.ingest_id, **chosen},
        follow_redirects=False,
    )
    assert saved.status_code == 303
    review = client.get(saved.headers["location"]).text
    assert _launch_payload(review) == {"ingest_id": env.ingest_id, **chosen}
    response = client.post(
        "/ejecucion/lanzar", data=_launch_payload(review), follow_redirects=False
    )
    assert response.headers["location"] == "/corridas/1/ejecucion?aviso=en_cola"
    ((raw,),) = env.rows("SELECT parametros FROM configuraciones")
    params = json.loads(raw)
    assert {name: params[name] for name in ("metric", "trials", "seed", "workers")} == {
        "metric": metric,
        "trials": int(trials) if trials else None,
        "seed": 2,
        "workers": 3,
    }


def test_matrix_previews_exclusions_and_preserves_the_draft_without_launching(env: Env) -> None:
    client = _dal_client(env)
    response = client.post(
        "/corridas/nueva/ajustes/comprobar",
        data={"ingest_id": env.ingest_id, **FORM, "families": ["classical", "ml"], "seed": "2"},
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 200
    assert 'aria-label="Familias por tipo de demanda"' in response.text
    assert "FIL-0003" in _text(response.text)
    assert "queda con una sola familia (Clásica)" in _text(response.text)
    assert "Fundacional" in _text(response.text)
    assert env.rows("SELECT COUNT(*) FROM ejecuciones") == [(0,)]
    review = client.get("/corridas/nueva/revisar").text
    assert _launch_payload(review)["seed"] == "2"
    assert _launch_payload(review)["families"] == ["classical", "ml"]
    # The warning, not the matrix preview, asks for launch confirmation.
    payload = _launch_payload(review)
    assert "confirmar" in payload
    launched = client.post("/ejecucion/lanzar", data=payload, follow_redirects=False)
    assert launched.headers["location"] == "/corridas/1/ejecucion?aviso=en_cola"


def test_import_populates_the_draft_but_does_not_launch(env: Env) -> None:
    client = _dal_client(env)
    _launch(client, env)
    document = client.get("/ejecucion/configuracion.json?ejecucion=1").json()
    document["parametros"]["seed"] = 2
    response = client.post(
        "/ejecucion/configuracion", files={"archivo": ("ajustes.json", json.dumps(document))}
    )
    assert response.status_code == 200
    review = client.get("/corridas/nueva/revisar").text
    assert _launch_payload(review)["seed"] == "2"
    assert "Estos ajustes son los de la corrida" not in _text(review)
    assert env.rows("SELECT COUNT(*) FROM ejecuciones") == [(1,)]


def test_records_include_trials_workers_and_seed_without_edit_or_launch_controls(env: Env) -> None:
    client = _dal_client(env)
    _launch(client, env, trials="6", workers="7", seed="2")
    for step in ("ajustes", "revisar"):
        html = client.get(f"/corridas/1/{step}").text
        assert "Esta corrida ya se lanzó" in _text(html)
        for name, label, value in (
            ("seed", "Semilla", "2"),
            ("workers", "Procesos en paralelo", "7"),
            ("trials", "Configuraciones a probar por familia", "6"),
        ):
            if step == "ajustes":
                assert re.search(rf'name="{name}"[^>]*value="{value}"[^>]* disabled', html)
            else:
                assert re.search(rf"<dt>{label}</dt><dd[^>]*>{value}</dd>", html), (step, label)
        markup = Markup(html)
        ids = {attrs["id"] for _, attrs in markup.tags if "id" in attrs}
        assert all(attrs["for"] in ids for attrs in markup.select("label") if "for" in attrs)
        assert 'type="checkbox"' not in html
        assert 'action="/ejecucion/lanzar"' not in html
        assert 'href="/ejecucion/configuracion.json?ejecucion=1"' in html


def test_file_timestamps_keep_fractional_seconds_in_the_exact_utc_hover() -> None:
    class FractionalIngest(FixtureRepository):
        def list_ingests(self, **kwargs: Any):
            result = super().list_ingests(**kwargs)
            stamp = datetime.fromisoformat("2026-11-02T03:23:49.610830+00:00")
            return result.model_copy(
                update={
                    "items": [
                        item.model_copy(update={"published_at": stamp}) for item in result.items
                    ]
                }
            )

    html = _fixture_client(repo=FractionalIngest("normal")).get("/corridas/nueva/datos").text
    assert "1 nov 2026, 10:23 p. m." in _text(html)
    assert 'title="2026-11-02 03:23:49.610830 UTC (hora universal coordinada)"' in html


def _event(kind: str, stage: str, time: str) -> str:
    return json.dumps({"event": kind, "stage": stage, "t": time}) + "\n"


def _finish_with_progress(env: Env, trace: str) -> None:
    folder = env.settings.data_root / "runs" / "earlier"
    folder.mkdir(parents=True)
    (folder / "progreso.jsonl").write_text(trace, encoding="utf-8")
    _execute(
        env,
        "UPDATE ejecuciones SET estado='completada', iniciada_en='2026-11-02T23:00:00Z', "
        "finalizada_en='2026-11-03T02:00:00Z', ruta_corrida='runs/earlier' WHERE id=1",
    )


def test_review_shows_recorded_stage_durations_and_local_dates_not_an_estimate(env: Env) -> None:
    client = _dal_client(env)
    _launch(client, env, seed="0")
    _finish_with_progress(
        env,
        _event("stage_started", "L2", "2026-11-02T23:00:00Z")
        + _event("stage_finished", "L2", "2026-11-03T01:30:00Z")
        + _event("stage_started", "L3", "2026-11-03T01:30:00Z")
        + _event("stage_finished", "L3", "2026-11-03T02:00:00Z")
        + "{broken\n"
        + _event("stage_started", "L4", "2026-11-03T02:00:00Z"),
    )
    html = _review(client, env, seed="2")
    text = _text(html)
    assert "Qué va a pasar" in text
    assert "PRED no estima cuánto falta" in text
    assert "2 h 30 min" in text and "30 min" in text
    assert "Sin duración publicada" in text  # L1 and L4 have no complete pair
    assert "2 nov 2026, 9:00 p. m." in text
    assert "2026-11-03 02:00:00 UTC" in html
    assert 'href="/corridas/1/ejecucion"' in html


def test_review_does_not_invent_durations_for_missing_or_reset_traces(env: Env) -> None:
    client = _dal_client(env)
    _launch(client, env, seed="0")
    _finish_with_progress(
        env,
        _event("stage_started", "L2", "2026-11-02T23:00:00Z")
        + _event("stage_finished", "L2", "2026-11-03T01:30:00Z")
        + json.dumps({"event": "run_started"})
        + "\n"
        + _event("stage_started", "L2", "2026-11-03T01:31:00Z"),
    )
    html = _review(client, env, seed="2")
    assert "2 h 30 min" not in _text(html)
    assert "no tienen una traza de etapas disponible" in _text(html)
    (env.settings.data_root / "runs/earlier/progreso.jsonl").unlink()
    assert "no tienen una traza de etapas disponible" in _text(
        client.get("/corridas/nueva/revisar").text
    )


def test_selecting_a_file_shows_its_data_and_preserves_it_when_the_rail_opens_settings(
    env: Env,
) -> None:
    client = _dal_client(env)
    response = client.post(
        "/corridas/nueva/datos",
        data={"ingest_id": env.ingest_id, "mostrar": "true"},
        follow_redirects=False,
    )
    assert response.headers["location"] == f"/corridas/nueva/datos?ingest_id={env.ingest_id}"
    assert "Datos válidos" in client.get(response.headers["location"]).text
    settings = client.get("/corridas/nueva/ajustes").text
    assert re.search(rf'<option value="{env.ingest_id}" selected(?: |>).*?</option>', settings)
    assert env.rows("SELECT COUNT(*) FROM ejecuciones") == [(0,)]


def test_data_step_offers_validated_files_and_its_summary_keeps_all_four_types(env: Env) -> None:
    client = _dal_client(env)
    page = client.get(f"/corridas/nueva/datos?ingest_id={env.ingest_id}")
    assert page.status_code == 200
    assert 'aria-label="Archivos validados"' in page.text
    assert "Cargar un archivo nuevo" in _text(page.text)
    assert "Familias que lo admiten" in _text(page.text)
    assert "Fecha de corte" in _text(page.text)
    assert "Total" in _text(page.text)
    assert "Distribución ADI–CV² de la demanda" in page.text
    assert "ADI = 1,32" in page.text and "CV² = 0,49" in page.text
    assert 'action="/datos/cargar"' in page.text
    for label in ("Suave", "Errática", "Intermitente", "Grumosa"):
        assert label in _text(page.text)
