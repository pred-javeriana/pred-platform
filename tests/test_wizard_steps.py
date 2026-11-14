"""Los pasos editables y sus registros, por las mismas rutas que usa el navegador."""

from __future__ import annotations

import base64
import json
import re
import sqlite3
from contextlib import closing
from datetime import datetime
from html import unescape
from typing import Any

import pytest
from fastapi.testclient import TestClient

from pred_platform.app.borrador import COOKIE
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


@pytest.mark.parametrize("step", ["ajustes", "revisar"])
@pytest.mark.parametrize("reported", [False, True])
def test_records_keep_the_window_aggregation(env: Env, step: str, reported: bool) -> None:
    client = _dal_client(env)
    _launch(client, env)
    if reported:
        _execute(
            env,
            "UPDATE configuraciones SET parametros=json_set(parametros, "
            "'$.aggregation', 'media', '$.trim', 0.2)",
        )
    html = _text(client.get(f"/corridas/1/{step}").text)
    assert "Resumen de las ventanas" in html
    assert (
        "Media (recorte del 20 % en cada extremo)"
        if reported
        else "Lo informa el motor al terminar la ejecución"
    ) in html


def test_copy_keeps_every_stored_setting_and_never_launches_or_resumes(env: Env) -> None:
    client = _dal_client(env)
    _launch(client, env, seasonality="14", trials="3", workers="7", seed="1")
    before = env.rows("SELECT estado, finalizada_en FROM ejecuciones")

    copied = client.post("/corridas/nueva/copiar", data={"copiar": "1"})

    assert copied.status_code == 200
    assert "Ajustes copiados de la corrida #1" in _text(copied.text)
    assert "Cambie la semilla y continúe" in copied.text
    assert env.rows("SELECT estado, finalizada_en FROM ejecuciones") == before
    # The copy survives navigation without pressing Continuar first.
    html = client.get("/corridas/nueva/ajustes").text
    for name, expected in {
        "min_train": "40",
        "horizon": "2",
        "step": "7",
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
    missing = client.post(
        "/corridas/nueva/copiar", data={"copiar": "999", "draft_rev": _stamp(client)}
    )
    assert "No se pudieron copiar los ajustes de la corrida #999: no existe esa corrida." in (
        _text(missing.text)
    )
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


def test_matrix_previews_exclusions_and_the_saved_edit_reaches_revisar(env: Env) -> None:
    client = _dal_client(env)
    typed = {
        "ingest_id": env.ingest_id,
        **FORM,
        "families": ["classical", "ml"],
        "seed": "2",
        "min_train": "20",
        "step": "7",
    }
    response = client.post(
        "/corridas/nueva/ajustes/comprobar", data=typed, headers={"HX-Request": "true"}
    )
    assert response.status_code == 200
    # The preview only redraws the matrix; the page saves each edit with destino=ajustes.
    assert "set-cookie" not in response.headers
    client.post("/corridas/nueva/ajustes", data={**typed, "destino": "ajustes"})
    assert 'aria-label="Familias por tipo de demanda"' in response.text
    assert (
        "1 SKU Grumosa queda con una sola familia (Clásica): FIL-0003 . "
        "Para evaluarlo, agregue Fundacional." in _text(response.text)
    )
    assert '<span class="mono pred-nowrap">FIL-0003</span>' in response.text
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
            ("trials", "Configuraciones por SKU y familia", "6"),
        ):
            if step == "ajustes":
                assert re.search(rf'name="{name}"[^>]*value="{value}"[^>]* disabled', html)
            else:
                assert re.search(rf'{label}</dt><dd><span class="mono">{value}</span>', html), (
                    step,
                    label,
                )
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


def test_review_of_a_launched_run_does_not_list_the_run_as_an_earlier_run(env: Env) -> None:
    client = _dal_client(env)
    _launch(client, env, seed="0")
    _finish_with_progress(
        env,
        _event("stage_started", "L2", "2026-11-02T23:00:00Z")
        + _event("stage_finished", "L2", "2026-11-03T01:30:00Z"),
    )
    text = _text(client.get("/corridas/1/revisar").text)
    assert "Datos y ajustes lanzados" in text
    assert "Cómo fueron las corridas anteriores" in text
    assert "Qué va a pasar" not in text
    assert "Todavía no hay corridas terminadas con estos datos" in text
    assert "2 h 30 min" not in text
    # The draft still shows it: from a new draft, run #1 is an earlier run.
    assert "2 h 30 min" in _text(_review(client, env, seed="2"))


def test_review_shows_how_long_the_last_run_on_these_data_took_only_when_all_stages_are(
    env: Env,
) -> None:
    client = _dal_client(env)
    _launch(client, env, seed="0")
    _finish_with_progress(
        env,
        "".join(
            _event(kind, stage, f"2026-11-02T23:00:{i * 2 + n:02d}Z")
            for i, stage in enumerate(("L1", "L2", "L3", "L4"))
            for n, kind in enumerate(("stage_started", "stage_finished"))
        ),
    )
    text = _text(_review(client, env, seed="2"))
    assert (
        "La corrida #1 con estos datos tardó 4 s. Con otros ajustes puede tardar distinto." in text
    )
    (env.settings.data_root / "runs/earlier/progreso.jsonl").write_text(
        _event("stage_started", "L2", "2026-11-02T23:00:00Z")
        + _event("stage_finished", "L2", "2026-11-02T23:00:05Z"),
        encoding="utf-8",
    )
    assert "con estos datos tardó" not in _text(client.get("/corridas/nueva/revisar").text)


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


def _draft_cookie(
    ingest_id: str | None, *, rev: int | None = None, token: str | None = None, **values: str
) -> str:
    """A draft cookie as the browser stores it; without ``rev`` it is the pre-revision format."""
    payload: dict[str, Any] = {
        "ingest_id": ingest_id,
        "values": {**FORM, **values},
        "settings_ready": True,
    }
    if rev is not None:
        payload.update(rev=rev, token=token)
    return base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")


def _shown(client: TestClient, path: str = "/corridas/nueva/ajustes") -> tuple[str, str]:
    html = client.get(path).text
    found = {
        name: re.search(rf'name="{name}"[^>]*value="([^"]*)"', html) for name in ("seed", "horizon")
    }
    assert found["seed"] and found["horizon"]
    return found["seed"].group(1), found["horizon"].group(1)


def _stamp(client: TestClient) -> str:
    found = re.search(
        r'name="draft_rev" value="([^"]+)"', client.get("/corridas/nueva/ajustes").text
    )
    assert found is not None
    return found.group(1)


def _autosave(client: TestClient, env: Env, stamp: str | None, **changes: str) -> Any:
    data = {"ingest_id": env.ingest_id, **FORM, **changes, "destino": "ajustes"}
    if stamp is not None:
        data["draft_rev"] = stamp
    return client.post("/corridas/nueva/ajustes", data=data, headers={"X-Pred-Autosave": "true"})


def test_a_draft_cookie_from_before_the_revision_still_loads(env: Env) -> None:
    client = _dal_client(env)
    client.cookies.set(COOKIE, _draft_cookie(env.ingest_id, seed="73", horizon="2"))

    assert _shown(client) == ("73", "2")
    page = client.get("/corridas/nueva/ajustes").text
    assert re.search(rf'<option value="{env.ingest_id}" selected', page)
    assert _shown(client, "/corridas/nueva/revisar") == ("73", "2")

    # It keeps working: an autosave over it raises the revision and keeps the chosen file.
    saved = _autosave(client, env, _stamp(client), seed="74")
    assert saved.status_code == 200 and _shown(client) == ("74", "2")
    assert re.search(
        rf'<option value="{env.ingest_id}" selected', client.get("/corridas/nueva/ajustes").text
    )


def test_autosave_writes_its_own_cookie_and_never_the_main_one(env: Env) -> None:
    client = _dal_client(env)
    client.cookies.set(COOKIE, _draft_cookie(env.ingest_id, seed="73"))

    saved = _autosave(client, env, _stamp(client), seed="74")

    written = [value.split("=", 1)[0] for value in saved.headers.get_list("set-cookie")]
    first = saved.headers["X-Pred-Draft-Rev"].replace(".", "_")
    assert written == [f"{COOKIE}_a{first}"]
    # The next save retires the earlier autosave cookie; each name says its revision and mark.
    again = _autosave(client, env, saved.headers["X-Pred-Draft-Rev"], seed="75")
    changed = [value.split("=", 1)[0] for value in again.headers.get_list("set-cookie")]
    second = again.headers["X-Pred-Draft-Rev"].replace(".", "_")
    assert second.startswith("2_")
    assert changed == [f"{COOKIE}_a{second}", f"{COOKIE}_a{first}"]
    assert _shown(client) == ("75", "2")


def test_two_autosaves_of_the_same_revision_ask_both_pages_to_reload(env: Env) -> None:
    """Refs #320: two pages save from revision 1 and their answers cross. The browser keeps both
    cookies; neither page is the current draft any more, so both are told on their next save."""
    client = _dal_client(env)
    client.cookies.set(COOKIE, _draft_cookie(env.ingest_id, rev=1, token="a" * 12, seed="60"))
    start = _stamp(client)
    first = _autosave(client, env, start, seed="61")
    tab_a = first.headers["X-Pred-Draft-Rev"]
    # Tab B saved from the same revision: its cookie arrives too, with its own name.
    tab_b = "2." + "f" * 12
    client.cookies.set(
        f"{COOKIE}_a2_{'f' * 12}", _draft_cookie(env.ingest_id, rev=2, token="f" * 12, seed="62")
    )
    assert len([name for name in client.cookies if name.startswith(f"{COOKIE}_a2_")]) == 2

    current = _stamp(client)
    assert current == "2."
    for stamp, seed in ((tab_a, "63"), (tab_b, "64")):
        refused = _autosave(client, env, stamp, seed=seed)
        assert refused.status_code == 409 and "set-cookie" not in refused.headers
        assert refused.headers["X-Pred-Draft-Rev"] == current
    # Ajustes and Revisar agree on what is shown; the reloaded page goes on from there.
    shown = _shown(client)
    assert shown[0] in {"61", "62"} and _shown(client, "/corridas/nueva/revisar") == shown
    saved = _autosave(client, env, current, seed="65")
    assert saved.status_code == 200 and saved.headers["X-Pred-Draft-Rev"].startswith("3.")
    assert _shown(client)[0] == "65"
    expired = [c.split(";")[0] for c in saved.headers.get_list("set-cookie") if "Max-Age=0" in c]
    assert sorted(expired) == sorted(
        f'{COOKIE}_a{stamp.replace(".", "_")}=""' for stamp in (tab_a, tab_b)
    )


def test_the_current_draft_is_the_highest_revision_and_a_tie_goes_to_the_main_cookie(
    env: Env,
) -> None:
    client = _dal_client(env)
    main = _draft_cookie(env.ingest_id, rev=5, token="a" * 12, seed="61")
    client.cookies.set(COOKIE, main)

    client.cookies.set(
        f"{COOKIE}_a4", _draft_cookie(env.ingest_id, rev=4, token="b" * 12, seed="99")
    )
    assert _shown(client)[0] == "61"
    client.cookies.set(
        f"{COOKIE}_a5", _draft_cookie(env.ingest_id, rev=5, token="c" * 12, seed="98")
    )
    assert _shown(client)[0] == "61"
    client.cookies.set(
        f"{COOKIE}_a6", _draft_cookie(env.ingest_id, rev=6, token="d" * 12, seed="55")
    )
    assert _shown(client)[0] == "55"
    client.cookies.set(f"{COOKIE}_a7", "no-es-un-borrador")
    assert _shown(client)[0] == "55"


def test_a_page_that_is_not_on_the_current_draft_is_refused_everywhere(env: Env) -> None:
    client = _dal_client(env)
    _launch(client, env, seed="61")
    # Launching left its own (empty) draft: this test starts from another one.
    client.cookies.clear()
    client.cookies.set(COOKIE, _draft_cookie(env.ingest_id, rev=3, token="a" * 12, seed="44"))
    old = _stamp(client)
    # Another page copies, so the first page's stamp is no longer the draft's.
    assert (
        client.post("/corridas/nueva/copiar", data={"copiar": "1", "draft_rev": old}).status_code
        == 200
    )
    assert _shown(client) == ("61", "2")

    stale = _autosave(client, env, old, seed="99")
    assert stale.status_code == 409 and "set-cookie" not in stale.headers
    assert stale.headers["X-Pred-Draft-Rev"] == _stamp(client)
    # The refused autosave gets the guide of the draft as it is (Refs #306).
    assert re.search(r'id="conflicto-borrador"(?![^>]*hidden)', stale.text)
    assert 'id="barra-pasos"' in stale.text and "semilla 61" in _text(stale.text)

    refused = [
        client.post("/corridas/nueva/copiar", data={"copiar": "1", "draft_rev": old}),
        client.post(
            "/corridas/nueva/ajustes",
            data={"ingest_id": env.ingest_id, **FORM, "seed": "99", "draft_rev": old},
        ),
        client.post(
            "/corridas/nueva/ajustes",
            data={
                "ingest_id": env.ingest_id,
                **FORM,
                "seed": "99",
                "draft_rev": old,
                "destino": "datos",
            },
            follow_redirects=False,
        ),
        client.post(
            "/ejecucion/configuracion",
            data={"draft_rev": old},
            files={"archivo": ("ajustes.json", b"{}", "application/json")},
        ),
    ]
    for response in refused:
        assert response.status_code == 409 and "set-cookie" not in response.headers
        assert re.search(r'id="conflicto-borrador"(?![^>]*hidden)', response.text)
        assert 'href="/corridas/nueva/ajustes"' in response.text
    assert _shown(client) == ("61", "2")

    # The page the refusal shows carries the current stamp, so the person can go on from it.
    assert _autosave(client, env, _stamp(client), seed="55").status_code == 200
    assert _shown(client)[0] == "55"


@pytest.mark.parametrize("stamp", [None, ""])
def test_a_page_without_a_stamp_only_saves_over_a_draft_from_before_the_revision(
    env: Env, stamp: str | None
) -> None:
    """Refs #321: a page drawn before the stamp existed is compared too."""
    client = _dal_client(env)
    client.cookies.set(COOKIE, _draft_cookie(env.ingest_id, seed="21"))
    assert _autosave(client, env, stamp, seed="77").status_code == 200
    assert _shown(client)[0] == "77"

    client.cookies.clear()
    client.cookies.set(COOKIE, _draft_cookie(env.ingest_id, rev=3, token="a" * 12, seed="21"))
    assert _autosave(client, env, stamp, seed="78").status_code == 409
    assert _shown(client)[0] == "21"


@pytest.mark.parametrize("stamp", [None, ""])
def test_a_submit_without_a_stamp_cannot_replace_a_later_copy(env: Env, stamp: str | None) -> None:
    """Refs #321: tab A saved, tab B copied, then a page without ``draft_rev`` edits and
    continues: both are refused and the copy stays the draft."""
    client = _dal_client(env)
    _launch(client, env, seed="99", horizon="5")
    assert _autosave(client, env, _stamp(client), seed="21").status_code == 200
    copied = client.post(
        "/corridas/nueva/copiar", data={"copiar": "1", "draft_rev": _stamp(client)}
    )
    assert copied.status_code == 200
    assert _shown(client) == ("99", "5")

    data = {"ingest_id": env.ingest_id, **FORM, "seed": "33"}
    if stamp is not None:
        data["draft_rev"] = stamp
    assert _autosave(client, env, stamp, seed="33").status_code == 409
    refused = client.post("/corridas/nueva/ajustes", data=data, follow_redirects=False)
    assert refused.status_code == 409 and "set-cookie" not in refused.headers
    assert _shown(client) == ("99", "5")
    assert _shown(client, "/corridas/nueva/revisar") == ("99", "5")


def test_an_unreadable_stamp_is_refused(env: Env) -> None:
    client = _dal_client(env)
    response = _autosave(client, env, "x.y", seed="77")
    assert response.status_code == 409 and "set-cookie" not in response.headers


def test_the_conflict_notice_is_hidden_until_there_is_a_conflict(env: Env) -> None:
    page = _dal_client(env).get("/corridas/nueva/ajustes").text
    assert re.search(r'id="conflicto-borrador" hidden', page)


def _rail_done(html: str, step: str) -> bool:
    found = re.search(rf'<li class="([^"]*)" id="paso-{step}"', html)
    assert found is not None
    return "is-done" in found.group(1).split()


def test_a_settings_tab_left_open_keeps_editing_after_another_tab_launches(env: Env) -> None:
    """Refs #306: launching discards the draft but keeps counting revisions, so the Ajustes tab
    left open saves a later edit as a new draft, and the guide asks to review Ajustes again."""
    client = _dal_client(env)
    _review(client, env, seed="75")
    page = client.get("/corridas/nueva/ajustes").text
    assert _rail_done(page, "ajustes")
    open_tab = _stamp(client)
    before = int(open_tab.split(".")[0])

    review = client.get("/corridas/nueva/revisar").text
    launched = client.post(
        "/ejecucion/lanzar", data=_launch_payload(review), follow_redirects=False
    )
    assert launched.status_code == 303
    # The draft is empty after launching, but its revision did not go back to 0.
    after = _stamp(client)
    assert int(after.split(".")[0]) == before + 1
    assert not _rail_done(client.get("/corridas/nueva/ajustes").text, "ajustes")

    saved = _autosave(client, env, open_tab, seed="99")
    assert saved.status_code == 200
    assert not _rail_done(saved.text, "ajustes")
    assert _shown(client) == ("99", "2")
    assert re.search(
        rf'<option value="{env.ingest_id}" selected', client.get("/corridas/nueva/ajustes").text
    )
    # Once the open tab wrote a new draft, another stale page is refused again.
    assert _autosave(client, env, open_tab, seed="98").status_code == 409
    assert _shown(client) == ("99", "2")


def test_reloading_a_copy_link_keeps_the_later_edits(env: Env) -> None:
    """Refs #319: the copy link saves once and redirects; reloading, Back or a restored session
    ask for the redirected address, which writes nothing."""
    client = _dal_client(env)
    _launch(client, env, seed="99", horizon="5")
    linked = client.get("/corridas/nueva/ajustes?copiar_de=1", follow_redirects=False)
    assert (linked.status_code, linked.headers["location"]) == (
        303,
        "/corridas/nueva/ajustes?aviso=copiado&corrida=1",
    )
    assert COOKIE in linked.headers["set-cookie"]
    page = client.get(linked.headers["location"])
    assert "Ajustes copiados de la corrida #1" in _text(page.text)
    assert "set-cookie" not in page.headers
    stamp = re.search(r'name="draft_rev" value="([^"]+)"', page.text)
    assert stamp is not None
    assert _autosave(client, env, stamp.group(1), seed="91", horizon="4").status_code == 200

    again = client.get(linked.headers["location"])
    assert "set-cookie" not in again.headers
    assert _shown(client, linked.headers["location"]) == ("91", "4")
    assert _shown(client, "/corridas/nueva/revisar") == ("91", "4")


@pytest.mark.parametrize("action", ["copy", "import"])
def test_copy_and_import_answer_with_a_redirect_that_writes_nothing(env: Env, action: str) -> None:
    """Refs #319: reloading the result of the copy or import form asks for Ajustes again
    instead of sending the form with its old stamp (a false conflict)."""
    client = _dal_client(env)
    _launch(client, env, seed="99", horizon="5")
    if action == "copy":
        sent = client.post(
            "/corridas/nueva/copiar",
            data={"copiar": "1", "draft_rev": _stamp(client)},
            follow_redirects=False,
        )
        target = "/corridas/nueva/ajustes?aviso=copiado&corrida=1"
        notice = "Ajustes copiados de la corrida #1"
    else:
        document = client.get("/ejecucion/configuracion.json?ejecucion=1").json()
        sent = client.post(
            "/ejecucion/configuracion",
            data={"draft_rev": _stamp(client)},
            files={"archivo": ("ajustes.json", json.dumps(document))},
            follow_redirects=False,
        )
        target = "/corridas/nueva/ajustes?aviso=importado"
        notice = "Configuración importada"
    assert (sent.status_code, sent.headers["location"]) == (303, target)
    page = client.get(target)
    assert notice in _text(page.text)
    assert not re.search(r'id="conflicto-borrador"(?![^>]*hidden)', page.text)
    stamp = re.search(r'name="draft_rev" value="([^"]+)"', page.text)
    assert stamp is not None
    assert _autosave(client, env, stamp.group(1), seed="91", horizon="5").status_code == 200
    assert "set-cookie" not in client.get(target).headers
    assert _shown(client, target) == ("91", "5")


def test_a_link_with_the_file_already_in_the_draft_writes_nothing(env: Env) -> None:
    """Refs #319: reloading ``?ingest_id=`` does not raise the revision, so a pending save from
    the page left and other Ajustes tabs stay current."""
    client = _dal_client(env)
    client.cookies.set(COOKIE, _draft_cookie(env.ingest_id, rev=3, token="a" * 12, seed="44"))
    stamp = _stamp(client)
    page = client.get(f"/corridas/nueva/ajustes?ingest_id={env.ingest_id}")
    assert page.status_code == 200 and "set-cookie" not in page.headers
    assert _autosave(client, env, stamp, seed="45").status_code == 200


def test_an_unknown_notice_shows_nothing(env: Env) -> None:
    client = _dal_client(env)
    for query in ("aviso=copiado&corrida=<b>", "aviso=otro", "aviso=copiado"):
        page = client.get(f"/corridas/nueva/ajustes?{query}").text
        assert "Ajustes copiados" not in page and "<b>" not in page
