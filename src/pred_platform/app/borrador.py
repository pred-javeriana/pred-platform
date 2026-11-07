"""El borrador de una corrida nueva: el archivo y los ajustes elegidos antes de lanzar.

Vive en el servidor, en un archivo por sesión del navegador (``DraftStore``); la cookie de sesión
solo lleva el identificador. Así Atrás, Siguiente y la barra de pasos conservan lo elegido, y el
borrador se descarta al lanzar. No es un secreto ni una decisión: es lo que la persona escribió, y
lanzar lo vuelve a validar con la regla del worker (``ejecucion.parse_launch_form``). Una cookie
ilegible o sin borrador se trata como un borrador vacío.

El borrador tiene una revisión que sube con cada escritura. El guardado automático de Ajustes
escribe solo si la página aún tiene la última revisión; una página que ya se dejó, o cuya
respuesta llega tarde, no puede reemplazar una elección más nueva: la cookie nunca lleva valores,
así que una respuesta atrasada no tiene nada que escribir en el navegador.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import threading
import time
import weakref
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import Request, Response

from pred_platform.app.ejecucion import default_form_values

COOKIE = "pred_borrador"
_PATH = "/"
_SHA256 = re.compile(r"[0-9a-f]{64}")
# El identificador de sesión es también el nombre del archivo: solo esta forma, nunca una ruta.
_SESSION = re.compile(r"[0-9a-f]{32}")
_TTL = 30 * 24 * 3600.0
_PRUNE_EVERY = 3600.0
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
    # Sube con cada escritura; 0 es un borrador que nunca se guardó.
    rev: int = 0

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


def _parse(data: object, rev: int) -> Draft:
    if not isinstance(data, dict):
        return Draft()
    ingest = data.get("ingest_id")
    return Draft(
        ingest_id=ingest if isinstance(ingest, str) and _SHA256.fullmatch(ingest) else None,
        values=_clean(data.get("values")),
        settings_ready=data.get("settings_ready") is True,
        rev=rev,
    )


class DraftStore:
    """Los borradores guardados, uno por sesión del navegador.

    Son archivos pequeños en ``directory``, así que sobreviven a reiniciar la aplicación, y vencen a
    los 30 días sin cambios. Sin ``directory`` (o si no se puede escribir en él) viven en una
    carpeta temporal que se borra al cerrar la aplicación.
    Una sola aplicación los usa a la vez: el candado ordena las escrituras de sus hilos.
    """

    def __init__(
        self,
        directory: Path | None = None,
        *,
        ttl: float = _TTL,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._directory = directory or self._temporary()
        self._ttl = ttl
        self._clock = clock
        self._lock = threading.Lock()
        self._pruned = clock()

    def read(self, session: str) -> Draft:
        with self._lock:
            return self._read(session)

    def write(self, session: str, draft: Draft) -> Draft:
        """Guarda el borrador como la elección más nueva y devuelve su revisión."""
        with self._lock:
            return self._write(session, draft)

    def autosave(
        self, session: str, rev: int, change: Callable[[Draft], Draft]
    ) -> tuple[bool, Draft]:
        """Aplica ``change`` solo si el borrador sigue en la revisión ``rev`` que vio la página.

        Devuelve si se guardó y el borrador que quedó: con otra revisión no cambia nada, porque
        una elección más nueva ya lo reemplazó.
        """
        with self._lock:
            current = self._read(session)
            if current.rev != rev:
                return False, current
            return True, self._write(session, change(current))

    def discard(self, session: str) -> None:
        with self._lock:
            self._path(session).unlink(missing_ok=True)

    def _temporary(self) -> Path:
        directory = Path(tempfile.mkdtemp(prefix="pred-borradores-"))
        weakref.finalize(self, shutil.rmtree, directory, ignore_errors=True)
        return directory

    def _path(self, session: str) -> Path:
        return self._directory / f"{session}.json"

    def _read(self, session: str) -> Draft:
        try:
            data = json.loads(self._path(session).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return Draft()
        rev = data.get("rev") if isinstance(data, dict) else None
        return _parse(data, rev if isinstance(rev, int) and rev > 0 else 0)

    def _write(self, session: str, draft: Draft) -> Draft:
        stored = replace(draft, values=_clean(draft.values), rev=self._read(session).rev + 1)
        payload = json.dumps(
            {
                "rev": stored.rev,
                "ingest_id": stored.ingest_id,
                "values": stored.values,
                "settings_ready": stored.settings_ready,
            },
            separators=(",", ":"),
        )
        try:
            self._put(session, payload)
        except OSError:
            # Una carpeta de datos de solo lectura sigue sirviendo el recorrido: el borrador pasa
            # a una carpeta temporal y dura lo que la aplicación.
            self._directory = self._temporary()
            self._put(session, payload)
        self._prune()
        return stored

    def _put(self, session: str, payload: str) -> None:
        self._directory.mkdir(parents=True, exist_ok=True)
        pending = self._directory / f"{uuid4().hex}.tmp"
        pending.write_text(payload, encoding="utf-8")
        os.replace(pending, self._path(session))

    def _prune(self) -> None:
        now = self._clock()
        if now - self._pruned < _PRUNE_EVERY:
            return
        self._pruned = now
        for path in self._directory.iterdir():
            try:
                if now - path.stat().st_mtime > self._ttl:
                    path.unlink(missing_ok=True)
            except OSError:
                continue


def _store(request: Request) -> DraftStore:
    store: DraftStore = request.app.state.drafts
    return store


def _cookie_session(request: Request) -> str | None:
    raw = request.cookies.get(COOKIE)
    return raw if raw and _SESSION.fullmatch(raw) else None


def _session(request: Request) -> str:
    """La sesión de esta petición: la de la cookie o, sin ella, una nueva (una por petición)."""
    existing = _cookie_session(request) or getattr(request.state, "draft_session", None)
    if existing:
        return str(existing)
    request.state.draft_session = uuid4().hex
    return str(request.state.draft_session)


def read_draft(request: Request) -> Draft:
    session = _cookie_session(request)
    return Draft() if session is None else _store(request).read(session)


def write_draft(request: Request, draft: Draft) -> Draft:
    """Guarda ``draft`` como la elección más nueva; falta ``bind_session`` en la respuesta."""
    return _store(request).write(_session(request), draft)


def bind_session(request: Request, response: Response, *, only_new: bool = False) -> None:
    """Pone la cookie de sesión (con ``only_new``, solo si el navegador aún no tiene una). Es
    siempre el mismo identificador, así que una respuesta que llega tarde no cambia el borrador al
    que apunta el navegador."""
    if only_new and _cookie_session(request) is not None:
        return
    # Sin expiración: dura lo que la sesión del navegador.
    response.set_cookie(COOKIE, _session(request), path=_PATH, httponly=True, samesite="lax")


def save_draft(request: Request, response: Response, draft: Draft) -> Draft:
    stored = write_draft(request, draft)
    bind_session(request, response)
    return stored


def autosave_draft(
    request: Request, rev: str | None, change: Callable[[Draft], Draft]
) -> tuple[bool, Draft]:
    """El guardado automático de Ajustes: ``rev`` es la revisión que la página vio al cargar o al
    guardar por última vez. Sin sesión o con otra revisión no se guarda nada."""
    session = _cookie_session(request)
    if session is None or rev is None or not rev.isascii() or not rev.isdigit():
        return False, Draft()
    return _store(request).autosave(session, int(rev), change)


def clear_draft(request: Request, response: Response) -> None:
    if (session := _cookie_session(request)) is not None:
        _store(request).discard(session)
    response.delete_cookie(COOKIE, path=_PATH, httponly=True, samesite="lax")
