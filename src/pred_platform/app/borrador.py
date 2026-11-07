"""El borrador de una corrida nueva: el archivo y los ajustes elegidos antes de lanzar.

Vive en una cookie de sesión del navegador, así que Atrás, Siguiente y la barra de pasos
conservan lo elegido, y el borrador se descarta al lanzar. No es un secreto ni una decisión: es
lo que la persona escribió, y lanzar lo vuelve a validar con la regla del worker
(``ejecucion.parse_launch_form``). Una cookie ilegible o con otra forma se trata como un borrador
vacío.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
from dataclasses import dataclass, field, replace
from typing import Any

from fastapi import Request, Response

from pred_platform.app.ejecucion import default_form_values

COOKIE = "pred_borrador"
_PATH = "/"
_SHA256 = re.compile(r"[0-9a-f]{64}")
# Los campos del formulario de lanzamiento que guarda el borrador, todos como texto.
_TEXT_FIELDS = (
    "metric",
    "min_train",
    "horizon",
    "step",
    "seasonality",
    "trials",
    "seed",
    "workers",
)
_MAX_TEXT = 32


@dataclass(frozen=True, slots=True)
class Draft:
    ingest_id: str | None = None
    values: dict[str, Any] = field(default_factory=default_form_values)
    # Si Ajustes se completó con «Continuar».
    settings_ready: bool = False

    def with_ingest(self, ingest_id: str) -> Draft:
        """Otro archivo pide revisar los ajustes de nuevo; el mismo conserva Ajustes completo."""
        ready = self.settings_ready and ingest_id == self.ingest_id
        return replace(self, ingest_id=ingest_id, settings_ready=ready)

    def with_settings(self, ingest_id: str, values: dict[str, Any]) -> Draft:
        return Draft(ingest_id=ingest_id, values=_clean(values), settings_ready=True)

    def with_values(self, ingest_id: str | None, values: dict[str, Any]) -> Draft:
        """Lo escrito en Ajustes sin validar todavía (al salir por Atrás, la barra de pasos o al
        importar una configuración). Ajustes sigue completo solo si nada cambió."""
        cleaned = _clean(values)
        unchanged = ingest_id == self.ingest_id and cleaned == self.values
        return Draft(
            ingest_id=ingest_id, values=cleaned, settings_ready=self.settings_ready and unchanged
        )


def _clean(raw: object) -> dict[str, Any]:
    """Solo los campos conocidos, con su forma; lo demás toma el valor por defecto."""
    values = default_form_values()
    if not isinstance(raw, dict):
        return values
    families = raw.get("families")
    if isinstance(families, list) and all(isinstance(f, str) for f in families):
        values["families"] = [f[:_MAX_TEXT] for f in families][:8]
    for name in _TEXT_FIELDS:
        value = raw.get(name)
        if isinstance(value, str):
            values[name] = value[:_MAX_TEXT]
    return values


def read_draft(request: Request) -> Draft:
    raw = request.cookies.get(COOKIE)
    if not raw:
        return Draft()
    try:
        data = json.loads(base64.urlsafe_b64decode(raw.encode("ascii") + b"==="))
    except (ValueError, binascii.Error, UnicodeError):
        return Draft()
    if not isinstance(data, dict):
        return Draft()
    ingest = data.get("ingest_id")
    return Draft(
        ingest_id=ingest if isinstance(ingest, str) and _SHA256.fullmatch(ingest) else None,
        values=_clean(data.get("values")),
        settings_ready=data.get("settings_ready") is True,
    )


def save_draft(response: Response, draft: Draft) -> None:
    payload = json.dumps(
        {
            "ingest_id": draft.ingest_id,
            "values": _clean(draft.values),
            "settings_ready": draft.settings_ready,
        },
        separators=(",", ":"),
    )
    value = base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii").rstrip("=")
    # Sin fecha de vencimiento: dura lo que la sesión del navegador.
    response.set_cookie(COOKIE, value, path=_PATH, httponly=True, samesite="lax")


def clear_draft(response: Response) -> None:
    response.delete_cookie(COOKIE, path=_PATH, httponly=True, samesite="lax")
