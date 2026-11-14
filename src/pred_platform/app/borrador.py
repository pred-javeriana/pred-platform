"""El borrador de una corrida nueva: el archivo y los ajustes elegidos antes de lanzar.

Vive en una cookie de sesión del navegador, así que Atrás, Siguiente y la barra de pasos
conservan lo elegido, y el borrador se descarta al lanzar. No es un secreto ni una decisión: es
lo que la persona escribió, y lanzar lo vuelve a validar con la regla del worker
(``ejecucion.parse_launch_form``). Una cookie ilegible o con otra forma se trata como un borrador
vacío.

El borrador lleva una revisión que sube con cada escritura y una marca propia de esa escritura.
Cada página de Ajustes recuerda la revisión y la marca que vio, y el servidor rechaza un guardado
o un envío cuando no son las del borrador vigente: una pestaña vieja nunca pisa una copia, una
importación o una edición hechas después, ni siquiera en la misma revisión. Una cookie de antes de
la revisión sigue valiendo como la revisión 0 sin marca.

Lanzar no borra la cookie: deja un borrador vacío marcado como lanzado, con una revisión más. La
cuenta no vuelve a 0, y una pestaña de Ajustes que quedó abierta puede guardar lo que se escriba
después como un borrador nuevo (no hay una elección más nueva que perder); Ajustes queda por
revisar.

Una respuesta que llega tarde no puede borrar una cookie más nueva, porque el navegador aplica su
``Set-Cookie`` cuando llega. Por eso el guardado automático escribe una cookie propia por escritura
(``pred_borrador_a<rev>_<marca>``) y el borrador vigente es la de mayor revisión; con la misma
revisión gana la cookie principal, que solo escriben las acciones que cambian de página
(Siguiente, Atrás, copiar, importar). Una respuesta tardía de una página ya abandonada deja, como
mucho, una cookie con una revisión que ya no gana.

Dos guardados automáticos que parten de la misma revisión (dos pestañas, o la página que se fue y
la recargada) escriben la misma revisión con marcas distintas, y ninguno sabe cuál llegó último.
El borrador vigente no elige uno en silencio: muestra uno de los dos sin marca, así que ninguna de
las dos páginas coincide con él, y la siguiente acción de cada una recibe 409 y el aviso de
recargar. La página recargada lleva la marca vacía y sigue desde ahí.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
from dataclasses import dataclass, field, replace
from typing import Any
from uuid import uuid4

from fastapi import Request, Response

from pred_platform.app.ejecucion import default_form_values

COOKIE = "pred_borrador"
_AUTOSAVE = COOKIE + "_a"
_PATH = "/"
_MAX_REV = 2**31
_TOKEN = re.compile(r"[0-9a-f]{12}")
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
    # Sube con cada escritura; 0 es un borrador que nunca se guardó (o de antes de la revisión).
    rev: int = 0
    # Identifica la escritura: dos escrituras de la misma revisión no se confunden.
    token: str = ""
    # El borrador se descartó al lanzar: no queda una elección que proteger.
    launched: bool = False

    @property
    def stamp(self) -> str:
        """Lo que una página recuerda del borrador que vio y devuelve con lo que envía."""
        return f"{self.rev}.{self.token}"

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


def _parse(raw: str) -> Draft | None:
    try:
        data = json.loads(base64.urlsafe_b64decode(raw.encode("ascii") + b"==="))
    except (ValueError, binascii.Error, UnicodeError):
        return None
    if not isinstance(data, dict):
        return None
    ingest = data.get("ingest_id")
    rev = data.get("rev")
    token = data.get("token")
    return Draft(
        ingest_id=ingest if isinstance(ingest, str) and _SHA256.fullmatch(ingest) else None,
        values=_clean(data.get("values")),
        settings_ready=data.get("settings_ready") is True,
        rev=rev if type(rev) is int and 0 < rev < _MAX_REV else 0,
        token=token if isinstance(token, str) and _TOKEN.fullmatch(token) else "",
        launched=data.get("launched") is True,
    )


def _autosave_cookies(request: Request) -> list[str]:
    return [name for name in request.cookies if name.startswith(_AUTOSAVE)]


def read_draft(request: Request) -> Draft:
    """El borrador vigente: el de mayor revisión; con la misma, el de la cookie principal.

    Dos guardados automáticos de la misma revisión, sin la principal a esa altura, empatan: el
    borrador queda sin marca para que ninguna de las dos páginas siga como si fuera la vigente.
    """
    found: list[tuple[bool, Draft]] = []
    for name in (COOKIE, *_autosave_cookies(request)):
        parsed = _parse(request.cookies.get(name) or "")
        if parsed is not None:
            found.append((name == COOKIE, parsed))
    if not found:
        return Draft()
    main, draft = max(found, key=lambda item: (item[1].rev, item[0], item[1].token))
    tokens = {other.token for is_main, other in found if not is_main and other.rev == draft.rev}
    return replace(draft, token="") if not main and len(tokens) > 1 else draft


def next_revision(request: Request, draft: Draft) -> Draft:
    """El borrador como la elección más nueva: una revisión más que la vigente, con marca nueva."""
    return replace(
        draft,
        rev=min(read_draft(request).rev + 1, _MAX_REV - 1),
        token=uuid4().hex[:12],
        launched=False,
    )


def is_stale(request: Request, page_stamp: str | None) -> bool:
    """Si la página que envía no vio el borrador vigente: otra página lo escribió después.

    Una marca ilegible se rechaza. Sin marca (una página dibujada antes de que existiera) solo vale
    mientras el borrador sigue en la revisión 0, como una cookie de antes de la revisión: después
    de cualquier escritura no hay cómo saber si la página la vio.
    """
    current = read_draft(request)
    if current.launched:
        # Lanzar descartó el borrador: ninguna elección más nueva se perdería, así que la página
        # que sigue abierta puede guardar lo que se escriba después como un borrador nuevo.
        return False
    if page_stamp is None or not page_stamp.strip():
        return current.rev > 0
    return page_stamp != current.stamp


def _encode(draft: Draft) -> str:
    payload = json.dumps(
        {
            "ingest_id": draft.ingest_id,
            "values": _clean(draft.values),
            "settings_ready": draft.settings_ready,
            "rev": draft.rev,
            "token": draft.token,
            "launched": draft.launched,
        },
        separators=(",", ":"),
    )
    return base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii").rstrip("=")


def _expire(response: Response, name: str) -> None:
    response.delete_cookie(name, path=_PATH, httponly=True, samesite="lax")


def save_draft(
    request: Request, response: Response, draft: Draft, *, autosave: bool = False
) -> None:
    """Escribe ``draft`` (ya con su revisión, ``next_revision``) en la respuesta.

    El guardado automático escribe su cookie de la revisión y no toca la principal; cualquier otro
    guardado reemplaza la principal y retira las de guardado automático que ya no valen.
    """
    value = _encode(draft)
    stale = _autosave_cookies(request)
    if autosave:
        name = f"{_AUTOSAVE}{draft.rev}_{draft.token}"
        # Sin fecha de vencimiento: dura lo que la sesión del navegador.
        response.set_cookie(name, value, path=_PATH, httponly=True, samesite="lax")
        stale = [other for other in stale if other != name]
    else:
        response.set_cookie(COOKIE, value, path=_PATH, httponly=True, samesite="lax")
    for other in stale:
        _expire(response, other)


def discard_draft(request: Request, response: Response) -> None:
    """Descarta el borrador al lanzar sin volver a la revisión 0.

    Queda un borrador vacío con una revisión más, marcado como lanzado: la cuenta de revisiones
    sigue subiendo, así que una respuesta tardía de antes de lanzar no gana a lo que se escriba
    después, y una pestaña de Ajustes que sigue abierta puede guardar una edición posterior.
    """
    empty = next_revision(request, Draft())
    save_draft(request, response, replace(empty, launched=True))
