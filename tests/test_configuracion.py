"""Exportar e importar la configuración de una ejecución (RF-CON-07, tarea A3 de la 4.5).

Exportar describe una corrida guardada; importar solo rellena el formulario de lanzamiento y nunca
crea una ejecución. La reproducibilidad es que pedir otra vez los mismos datos y ajustes resuelve
a la misma corrida: su identidad es ``run_id_for``, que no cuenta los procesos en paralelo.
"""

from __future__ import annotations

import json
import re
from typing import Any

import pytest
from fastapi.testclient import TestClient

from pred_platform.app.configuracion import (
    FORMAT,
    FORMAT_VERSION,
    MAX_BYTES,
    config_filename,
    parse_config,
)
from pred_platform.worker.service import LaunchSettings, run_id_for
from tests.test_ejecucion import (
    FORM,
    _dal_client,
    _fixture_client,
    _launch,
    _text,
)
from tests.test_worker import Env
from tests.test_worker import env as env  # noqa: F401  (the fixture)

EXPORT = "/ejecucion/configuracion.json"
IMPORT = "/ejecucion/configuracion"
SHA = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


def _document(**changes: Any) -> dict[str, Any]:
    """Un archivo válido, como lo exportaría la plataforma, con los cambios que la prueba pida."""
    document: dict[str, Any] = {
        "formato": FORMAT,
        "schema_version": FORMAT_VERSION,
        "run_id": "r-0123456789abcdef",
        "identidad": "r-0123456789abcdef",
        "ingesta_sha256": SHA,
        "parametros": {
            "min_train": 40,
            "horizon": 2,
            "step": 14,
            "seasonality": 7,
            "metric": "mae",
            "families": ["classical", "ml"],
            "trials": 1,
            "seed": 17,
            "workers": 2,
        },
        "versiones": {"plataforma": "0.1.0"},
    }
    document.update(changes)
    return document


def _raw(document: Any) -> bytes:
    return json.dumps(document).encode("utf-8")


def _import(client: TestClient, content: bytes, name: str = "configuracion.json"):
    return client.post(IMPORT, files={"archivo": (name, content, "application/json")})


def _exported(client: TestClient) -> dict[str, Any]:
    response = client.get(f"{EXPORT}?ejecucion=1")
    assert response.status_code == 200
    return response.json()


# ------------------------------------------------------------------------------- exportar
def test_the_export_describes_the_stored_run_and_nothing_local(env: Env) -> None:
    client = _dal_client(env)
    _launch(client, env)

    response = client.get(f"{EXPORT}?ejecucion=1")
    document = response.json()

    assert response.headers["content-type"] == "application/json"
    run_id = document["run_id"]
    assert response.headers["content-disposition"] == (
        f'attachment; filename="pred-configuracion-{run_id}.json"'
    )
    assert (document["formato"], document["schema_version"]) == (FORMAT, FORMAT_VERSION)
    assert document["ingesta_sha256"] == env.ingest_id
    assert re.fullmatch(r"[0-9a-f]{64}", document["ingesta_sha256"])
    assert document["parametros"] == {
        "min_train": 40,
        "horizon": 2,
        "step": 14,
        "seasonality": 7,
        "metric": "mae",
        "families": ["classical", "ml", "foundation"],
        "trials": 1,
        "seed": 17,
        "workers": 2,
    }
    assert set(document["versiones"]) == {
        "plataforma",
        "contrato_v1",
        "contrato_v2",
        "formato_parametros",
    }
    # Lo que es de esta instalación no viaja: ni el id local ni fechas.
    assert "ejecucion_id" not in document and "iniciada_en" not in document
    assert document["identidad"] == run_id_for(
        env.ingest_id,
        LaunchSettings(
            min_train=40,
            horizon=2,
            step=14,
            seasonality=7,
            metric="mae",
            families=("classical", "ml", "foundation"),
            trials=1,
            seed=17,
            workers=2,
        ),
    )


def test_the_export_defaults_to_the_latest_run_and_a_missing_one_is_a_404(env: Env) -> None:
    client = _dal_client(env)
    assert client.get(EXPORT).status_code == 404  # no run yet
    _launch(client, env)

    assert client.get(EXPORT).json() == client.get(f"{EXPORT}?ejecucion=1").json()
    missing = client.get(f"{EXPORT}?ejecucion=99")
    assert missing.status_code == 404 and "99" in missing.text
    assert not missing.headers["content-type"].startswith("application/json")


def test_with_fixtures_there_is_no_real_configuration_to_export() -> None:
    client, _ = _fixture_client("normal")
    response = client.get(EXPORT)

    assert response.status_code == 409
    assert "datos de prueba" in response.text
    assert "configuracion.json" not in client.get("/ejecucion").text


def test_the_page_offers_the_export_for_the_shown_run(env: Env) -> None:
    client = _dal_client(env)
    _launch(client, env)

    html = client.get("/ejecucion?ejecucion=1").text
    assert 'href="/ejecucion/configuracion.json?ejecucion=1"' in html
    assert "Exportar configuración (JSON)" in _text(html)


# ----------------------------------------------------------------- la ida y vuelta
def test_exporting_and_importing_gives_back_the_same_run(env: Env) -> None:
    client = _dal_client(env)
    _launch(client, env)
    document = _exported(client)
    before = (
        env.rows("SELECT COUNT(*) FROM ejecuciones")[0]
        + env.rows("SELECT COUNT(*) FROM configuraciones")[0]
    )

    page = _import(client, _raw(document))

    assert page.status_code == 200
    text = _text(page.text)
    assert "Configuración importada" in text and "Revise los valores y lance" in text
    # El formulario trae exactamente los ajustes de la corrida exportada.
    for field, value in (("horizon", "2"), ("step", "14"), ("trials", "1"), ("seed", "17")):
        assert re.search(rf'name="{field}"[^>]*value="{value}"', page.text), field
    assert 'value="mae" selected' in page.text
    assert re.search(r'value="classical"[^>]*checked', page.text)
    assert re.search(r'value="ml"[^>]*checked', page.text)
    assert not re.search(r'value="dl"[^>]*checked', page.text)
    assert f'<option value="{env.ingest_id}" selected>' in page.text
    # Importar no creó nada.
    after = (
        env.rows("SELECT COUNT(*) FROM ejecuciones")[0]
        + env.rows("SELECT COUNT(*) FROM configuraciones")[0]
    )
    assert after == before

    # Lanzar lo importado resuelve a la misma corrida: no se crea otra.
    imported = parse_config(_raw(document))
    assert imported.values is not None
    again = client.post(
        "/ejecucion/lanzar",
        data={**{k: v for k, v in imported.values.items()}, "ingest_id": env.ingest_id},
        follow_redirects=False,
    )
    assert again.headers["location"] == "/ejecucion?ejecucion=1&aviso=existente"
    assert env.rows("SELECT COUNT(*) FROM ejecuciones") == [(1,)]


def test_the_identity_does_not_count_the_parallel_processes(env: Env) -> None:
    client = _dal_client(env)
    _launch(client, env)
    document = _exported(client)
    other = _document(
        ingesta_sha256=env.ingest_id,
        parametros={**document["parametros"], "workers": 7},
    )

    imported = parse_config(_raw(other))
    assert imported.values is not None and imported.values["workers"] == "7"
    again = client.post(
        "/ejecucion/lanzar",
        data={**imported.values, "ingest_id": env.ingest_id},
        follow_redirects=False,
    )
    # Con otros procesos en paralelo sigue siendo la misma corrida.
    assert again.headers["location"] == "/ejecucion?ejecucion=1&aviso=existente"
    assert env.rows("SELECT COUNT(*) FROM ejecuciones") == [(1,)]


def test_a_changed_setting_is_another_run_so_the_file_pins_the_settings(env: Env) -> None:
    client = _dal_client(env)
    _launch(client, env)
    document = _exported(client)
    changed = _document(
        ingesta_sha256=env.ingest_id, parametros={**document["parametros"], "seed": 99}
    )
    imported = parse_config(_raw(changed))
    assert imported.values is not None

    again = client.post(
        "/ejecucion/lanzar",
        data={**imported.values, "ingest_id": env.ingest_id},
        follow_redirects=False,
    )
    assert again.headers["location"] == "/ejecucion?ejecucion=2&aviso=en_cola"


# ----------------------------------------------------------------------------- la ingesta
def test_a_file_from_another_ingestion_imports_the_settings_but_chooses_no_data(
    env: Env,
) -> None:
    client = _dal_client(env)
    other_sha = "a" * 64
    page = _import(client, _raw(_document(ingesta_sha256=other_sha)))

    text = _text(page.text)
    assert page.status_code == 200
    assert "no están cargados en esta plataforma" in text and "aaaaaaaaaaaa" in text
    assert re.search(r'name="horizon"[^>]*value="2"', page.text)  # los ajustes sí se importan
    # Ninguna ingesta queda elegida: hay que escoger y la opción vacía lo dice.
    assert '<option value="" selected disabled>Elija un archivo de datos</option>' in page.text
    assert f'<option value="{env.ingest_id}" selected>' not in page.text
    assert env.rows("SELECT COUNT(*) FROM ejecuciones") == [(0,)]


def test_launching_without_choosing_the_ingestion_is_refused(env: Env) -> None:
    client = _dal_client(env)
    page = client.post("/ejecucion/lanzar", data={**FORM, "ingest_id": ""})
    assert "Elija el archivo de datos que se va a ejecutar." in page.text
    assert env.rows("SELECT COUNT(*) FROM ejecuciones") == [(0,)]


# ------------------------------------------------------------------------------- rechazos
@pytest.mark.parametrize(
    ("content", "reason"),
    [
        (b"", "El archivo está vacío."),
        (b"no es json", "no es JSON válido"),
        (b"\xff\xfe\x00", "no es JSON válido"),
        (b"[1, 2]", "debe contener un objeto JSON"),
        (_raw({"hola": 1}), "No es una configuración de PRED"),
        (_raw(_document(formato="otro")), "No es una configuración de PRED"),
        (_raw(_document(schema_version=2)), "usa la versión 2 del formato"),
        (_raw(_document(schema_version="1")), "esta plataforma lee la 1"),
        (_raw(_document(extra=1)), "Campos desconocidos: extra."),
        (_raw(_document(ingesta_sha256="abc")), "SHA-256 de 64 caracteres"),
        (_raw(_document(ingesta_sha256="A" * 64)), "SHA-256 de 64 caracteres"),
        (_raw(_document(parametros=[])), "«parametros» debe ser un objeto."),
        (b"x" * (MAX_BYTES + 1), "pesa más de 64 KB"),
    ],
)
def test_a_file_that_is_not_a_valid_configuration_is_refused_with_its_cause(
    env: Env, content: bytes, reason: str
) -> None:
    client = _dal_client(env)
    page = _import(client, content)

    assert page.status_code == 400
    text = _text(page.text)
    assert "No se importó la configuración." in text and reason in text
    assert env.rows("SELECT COUNT(*) FROM ejecuciones") == [(0,)]


def _with(**changes: Any) -> bytes:
    base = _document()
    base["parametros"] = {**base["parametros"], **changes}
    return _raw(base)


@pytest.mark.parametrize(
    ("content", "reason"),
    [
        (_with(horizon=0), "Horizonte (días): debe ser mayor que cero."),
        (_with(horizon="2"), "«horizon» debe ser un número entero."),
        (_with(horizon=2.5), "«horizon» debe ser un número entero."),
        (_with(horizon=True), "«horizon» debe ser un número entero."),
        (_with(trials="1"), "«trials» debe ser un número entero o null."),
        (_with(metric=3), "«metric» debe ser texto."),
        (_with(metric="mape"), "Elija una de las métricas de la lista."),
        (_with(families="classical"), "«families» debe ser una lista de textos."),
        (_with(families=[]), "Elija al menos una familia de modelos."),
        (_with(families=["classical", "prophet"]), "Elija solo familias de la lista."),
        (_with(velocidad=1), "Parámetros desconocidos: velocidad."),
        (_with(seed=-1), "Semilla: debe ser cero o más."),
        (_with(seed=2**63), "Semilla: el número es demasiado grande."),
        (
            _with(seed="SEED").replace(b'"SEED"', b"1" * 4301),
            "El archivo no es JSON válido en UTF-8.",
        ),
    ],
)
def test_wrong_parameters_are_refused_by_the_same_rule_as_a_launch(
    env: Env, content: bytes, reason: str
) -> None:
    page = _import(_dal_client(env), content)

    assert page.status_code == 400
    assert reason in _text(page.text)


def test_a_missing_parameter_is_named(env: Env) -> None:
    document = _document()
    del document["parametros"]["seed"], document["parametros"]["trials"]
    page = _import(_dal_client(env), _raw(document))

    assert page.status_code == 400
    assert "Faltan parámetros: seed, trials." in _text(page.text)


def test_a_null_trials_means_the_engines_budget(env: Env) -> None:
    document = _document()
    document["parametros"]["trials"] = None
    imported = parse_config(_raw(document))

    assert imported.values is not None and imported.values["trials"] == ""
    page = _import(_dal_client(env), _raw(document))
    assert page.status_code == 200 and re.search(r'name="trials"[^>]*value=""', page.text)


def test_importing_with_no_file_is_refused(env: Env) -> None:
    page = _dal_client(env).post(IMPORT)
    assert page.status_code == 400 and "El archivo está vacío." in _text(page.text)


def test_importing_still_fills_the_form_with_fixtures_but_launching_stays_off() -> None:
    client, _ = _fixture_client("normal")
    page = _import(client, _raw(_document()))

    assert page.status_code == 200
    assert re.search(r'name="horizon"[^>]*value="2"', page.text)
    assert "Lanzar está deshabilitado" in page.text


# ----------------------------------------------------------------------------- unitarias
def test_the_filename_cannot_break_the_header() -> None:
    assert config_filename('a/b"c d\r\n') == "pred-configuracion-a_b_c_d__.json"
    assert config_filename(None) == "pred-configuracion-sin-run-id.json"


def test_the_import_form_is_on_the_launch_screen(env: Env) -> None:
    html = _dal_client(env).get(f"/ejecucion?ingest_id={env.ingest_id}").text
    assert 'action="/ejecucion/configuracion"' in html
    assert 'enctype="multipart/form-data"' in html
    assert 'accept=".json,application/json"' in html
