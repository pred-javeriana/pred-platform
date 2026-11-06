"""Exportar e importar la configuración de una ejecución (RF-CON-07, tarea A3 de la 4.5).

El archivo es JSON con formato y versión propios. Exportar describe una corrida ya guardada;
importar solo lee el archivo y devuelve los valores del formulario de lanzamiento, que se validan
con la misma regla que el lanzamiento (``parse_launch_form``): nunca crea una ejecución. Pedir
después los mismos datos y ajustes resuelve a la misma corrida, porque su identidad es
``run_id_for`` (que no cuenta los procesos en paralelo). La plataforma no importa el motor ni
registra su versión: el archivo fija los ajustes, y el mismo resultado exige además el mismo
código del motor.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from pred_platform.app.ejecucion import parse_launch_form
from pred_platform.dal.lectura import RunDetail
from pred_platform.worker.service import LaunchSettings, run_id_for

FORMAT = "pred-platform.configuracion-ejecucion"
FORMAT_VERSION = 1
MAX_BYTES = 64 * 1024

_TOP_KEYS = frozenset(
    {
        "formato",
        "schema_version",
        "run_id",
        "identidad",
        "ingesta_sha256",
        "parametros",
        "versiones",
    }
)
_INT_PARAMS = ("min_train", "horizon", "step", "seasonality", "seed", "workers")
_PARAM_KEYS = frozenset({*_INT_PARAMS, "metric", "families", "trials"})
_SHA256 = re.compile(r"[0-9a-f]{64}")


class ConfigExportError(Exception):
    """La configuración guardada de la ejecución no se puede exportar (el mensaje dice por qué)."""


def _launch_settings(parametros: Mapping[str, Any]) -> LaunchSettings:
    """Los ajustes guardados de una ejecución, con la misma validación que el lanzamiento."""
    defaults = LaunchSettings()
    try:
        return LaunchSettings(
            min_train=parametros.get("min_train", defaults.min_train),
            horizon=parametros.get("horizon", defaults.horizon),
            step=parametros.get("step", defaults.step),
            seasonality=parametros.get("seasonality", defaults.seasonality),
            metric=parametros.get("metric", defaults.metric),
            families=tuple(parametros.get("families") or defaults.families),
            trials=parametros.get("trials"),
            seed=parametros.get("seed", defaults.seed),
            workers=parametros.get("workers", defaults.workers),
        )
    except (ValueError, TypeError) as exc:
        raise ConfigExportError(
            f"Los ajustes guardados de la ejecución no son válidos: {exc}"
        ) from exc


def export_config(detail: RunDetail, versions: Mapping[str, object]) -> dict[str, Any]:
    """El documento de una ejecución guardada: sus ajustes, su ingesta y las versiones que la leen.

    ``identidad`` es la que la plataforma usa para reconocer la misma corrida; ``run_id`` es el
    guardado, que puede llevar el sufijo de una generación posterior del motor.
    """
    try:
        stored = json.loads(detail.parametros)
    except json.JSONDecodeError as exc:
        raise ConfigExportError("Los ajustes guardados de la ejecución no son JSON.") from exc
    if not isinstance(stored, dict):
        raise ConfigExportError("Los ajustes guardados de la ejecución no son un objeto.")
    launch = _launch_settings(stored)
    parametros = launch.to_parametros()
    return {
        "formato": FORMAT,
        "schema_version": FORMAT_VERSION,
        "run_id": detail.run_id,
        "identidad": run_id_for(detail.ingest_sha256, launch),
        "ingesta_sha256": detail.ingest_sha256,
        "parametros": {key: parametros[key] for key in sorted(_PARAM_KEYS)},
        "versiones": dict(versions),
    }


def config_filename(run_id: str | None) -> str:
    """El nombre del archivo: la corrida que describe, limpia para una cabecera HTTP."""
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", run_id or "sin-run-id")
    return f"pred-configuracion-{safe}.json"


@dataclass(frozen=True, slots=True)
class ConfigImport:
    """Lo que se leyó de un archivo: valores para el formulario o las causas de rechazo."""

    values: dict[str, Any] | None = None
    ingest_sha256: str | None = None
    source_run_id: str | None = None
    errors: list[str] = field(default_factory=list)


def _int(value: object) -> bool:
    return type(value) is int


def _check_parametros(parametros: object) -> list[str]:
    if not isinstance(parametros, dict):
        return ["«parametros» debe ser un objeto."]
    errors: list[str] = []
    missing = sorted(_PARAM_KEYS - parametros.keys())
    unknown = sorted(parametros.keys() - _PARAM_KEYS)
    if missing:
        errors.append(f"Faltan parámetros: {', '.join(missing)}.")
    if unknown:
        errors.append(f"Parámetros desconocidos: {', '.join(unknown)}.")
    for key in _INT_PARAMS:
        if key in parametros and not _int(parametros[key]):
            errors.append(f"«{key}» debe ser un número entero.")
    if (
        "trials" in parametros
        and parametros["trials"] is not None
        and not _int(parametros["trials"])
    ):
        errors.append("«trials» debe ser un número entero o null.")
    if "metric" in parametros and not isinstance(parametros["metric"], str):
        errors.append("«metric» debe ser texto.")
    families = parametros.get("families")
    if "families" in parametros and (
        not isinstance(families, list) or not all(isinstance(f, str) for f in families)
    ):
        errors.append("«families» debe ser una lista de textos.")
    return errors


def parse_config(raw: bytes) -> ConfigImport:
    """Lee un archivo de configuración; no escribe nada ni lanza ninguna ejecución."""
    if not raw:
        return ConfigImport(errors=["El archivo está vacío."])
    if len(raw) > MAX_BYTES:
        return ConfigImport(errors=[f"El archivo pesa más de {MAX_BYTES // 1024} KB."])
    try:
        document = json.loads(raw.decode("utf-8"))
    except ValueError:
        return ConfigImport(errors=["El archivo no es JSON válido en UTF-8."])
    if not isinstance(document, dict):
        return ConfigImport(errors=["El archivo debe contener un objeto JSON."])
    if document.get("formato") != FORMAT:
        return ConfigImport(
            errors=[f"No es una configuración de PRED: «formato» debe ser {FORMAT}."]
        )
    version = document.get("schema_version")
    if not _int(version) or version != FORMAT_VERSION:
        return ConfigImport(
            errors=[
                f"El archivo usa la versión {version!r} del formato; "
                f"esta plataforma lee la {FORMAT_VERSION}."
            ]
        )
    errors: list[str] = []
    unknown = sorted(document.keys() - _TOP_KEYS)
    if unknown:
        errors.append(f"Campos desconocidos: {', '.join(unknown)}.")
    sha = document.get("ingesta_sha256")
    if not isinstance(sha, str) or not _SHA256.fullmatch(sha):
        errors.append("«ingesta_sha256» debe ser un SHA-256 de 64 caracteres hexadecimales.")
    parametros = document.get("parametros")
    errors += _check_parametros(parametros)
    if errors:
        return ConfigImport(errors=errors)
    assert isinstance(parametros, dict) and isinstance(sha, str)
    form = parse_launch_form(
        families=parametros["families"],
        metric=parametros["metric"],
        fields={
            **{key: str(parametros[key]) for key in _INT_PARAMS},
            "trials": "" if parametros["trials"] is None else str(parametros["trials"]),
        },
    )
    if form.errors:
        return ConfigImport(errors=list(form.errors.values()))
    source = document.get("run_id")
    return ConfigImport(
        values=form.values,
        ingest_sha256=sha,
        source_run_id=source if isinstance(source, str) else None,
    )
