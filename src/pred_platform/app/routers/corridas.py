"""El inicio (``/``) y lo que comparten las rutas de los pasos de una corrida.

Cada paso lo sirve el router de su superficie: Datos (``data``), Ajustes, Revisar y Ejecución
(``execution``) y Resultados (``results``), en ``/corridas/<nueva|id>/<paso>``. Aquí viven el
inicio, la página de un paso bloqueado o de una corrida que no existe, y la redirección de las
rutas anteriores con su consulta.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from pred_platform.app.corridas import home
from pred_platform.app.pasos import Wizard
from pred_platform.data.deps import get_repository
from pred_platform.data.repository import DataRepository

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
def inicio(
    request: Request,
    repository: DataRepository = Depends(get_repository),  # noqa: B008
) -> HTMLResponse:
    return request.app.state.templates.TemplateResponse(
        request,
        "pages/corridas.html",
        {
            "home": home(repository),
            "at_home": True,
            "page_title": "Corridas",
            "source": repository.source,
        },
    )


def redirect_with_query(
    path: str, items: Iterable[tuple[str, str]], *, drop: Iterable[str] = ()
) -> RedirectResponse:
    """Lleva una ruta anterior a su paso, con la misma consulta (menos lo que el paso ya dice)."""
    dropped = set(drop)
    kept = [(key, value) for key, value in items if key not in dropped]
    return RedirectResponse(f"{path}?{urlencode(kept)}" if kept else path, status_code=307)


@dataclass(frozen=True, slots=True)
class Locked:
    """Un paso al que todavía no se llega, o una corrida que no existe."""

    title: str
    heading: str
    text: str
    actions: tuple[tuple[str, str, bool], ...] = ()


def render_locked(
    request: Request,
    locked: Locked,
    wizard: Wizard | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    context: dict[str, Any] = {"locked": locked, "wizard": wizard, "page_title": locked.title}
    return request.app.state.templates.TemplateResponse(  # type: ignore[no-any-return]
        request, "pages/paso_bloqueado.html", context, status_code=status_code
    )


def render_missing_run(request: Request, corrida: str) -> HTMLResponse:
    """404: la corrida pedida no existe (o el número no es válido)."""
    return render_locked(
        request,
        Locked(
            title="Corrida no encontrada",
            heading=f"No existe la corrida «{corrida}».",
            text="Puede que el enlace sea de otra instalación o que la corrida se haya borrado. "
            "En Corridas están todas las que existen.",
            actions=(("Ir a Corridas", "/", True),),
        ),
        status_code=404,
    )
