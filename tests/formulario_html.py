"""Envía un formulario de una página como lo hace el navegador, sin JavaScript.

Lee el HTML servido, reúne los controles cuyo dueño es el formulario (dentro de él o con
``form="<id>"``), en orden de árbol, y arma la lista de envío de HTML: un control deshabilitado no
va; una casilla solo si está marcada; un ``<select>`` con su opción elegida que no esté
deshabilitada. Enter en un campo usa el botón por defecto (el primer botón de envío del
formulario); un clic en un botón envía su ``name``/``value``. Así una prueba recorre la página
servida como una persona, no un POST armado a mano.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from html.parser import HTMLParser


@dataclass
class Control:
    tag: str
    attrs: dict[str, str]
    options: list[dict[str, str]] = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.attrs.get("name", "")

    @property
    def kind(self) -> str:
        default = "submit" if self.tag == "button" else "text"
        return self.attrs.get("type", default).lower()

    @property
    def is_submit(self) -> bool:
        return self.tag in ("button", "input") and self.kind == "submit"


class _Controls(HTMLParser):
    def __init__(self, form_id: str) -> None:
        super().__init__(convert_charrefs=True)
        self.form_id = form_id
        self.inside = 0  # profundidad dentro del <form id=form_id>
        self.controls: list[Control] = []
        self._select: Control | None = None

    def _owned(self, attrs: dict[str, str]) -> bool:
        if "form" in attrs:
            return attrs["form"] == self.form_id
        return self.inside > 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._start(tag, {key: value if value is not None else "" for key, value in attrs})

    def _start(self, tag: str, attrs: dict[str, str]) -> None:
        if tag == "form":
            if self.inside or attrs.get("id") == self.form_id:
                self.inside += 1
            return
        if tag == "option" and self._select is not None:
            self._select.options.append(attrs)
            return
        if tag in ("input", "button", "select", "textarea") and self._owned(attrs):
            control = Control(tag, attrs)
            self.controls.append(control)
            if tag == "select":
                self._select = control

    def handle_endtag(self, tag: str) -> None:
        if tag == "form" and self.inside:
            self.inside -= 1
        elif tag == "select":
            self._select = None


def controls(html: str, form_id: str) -> list[Control]:
    parser = _Controls(form_id)
    parser.feed(html)
    return parser.controls


def submit(
    html: str,
    form_id: str,
    *,
    typed: dict[str, str] | None = None,
    button: dict[str, str] | None = None,
) -> dict[str, list[str]]:
    """Los datos que envía el navegador. ``typed`` cambia el valor de campos de texto o número
    por su nombre. ``button`` elige el botón pulsado por sus atributos (por ejemplo
    ``{"value": "datos"}``); sin él, Enter en un campo: el botón por defecto."""
    found = controls(html, form_id)
    submits = [c for c in found if c.is_submit and "disabled" not in c.attrs]
    if button is None:
        submitter = submits[0] if submits else None  # el botón por defecto
    else:
        submitter = next(c for c in submits if all(c.attrs.get(k) == v for k, v in button.items()))
    data: dict[str, list[str]] = {}
    for control in found:
        if not control.name or "disabled" in control.attrs:
            continue
        if control.is_submit or control.kind in ("button", "reset", "file"):
            continue
        if control.kind in ("checkbox", "radio"):
            if "checked" in control.attrs:
                data.setdefault(control.name, []).append(control.attrs.get("value", "on"))
            continue
        if control.tag == "select":
            chosen = [o for o in control.options if "selected" in o] or control.options[:1]
            for option in chosen:
                if "disabled" not in option:
                    data.setdefault(control.name, []).append(option.get("value", ""))
            continue
        value = (typed or {}).get(control.name, control.attrs.get("value", ""))
        data.setdefault(control.name, []).append(value)
    if submitter is not None and submitter.name:
        data.setdefault(submitter.name, []).append(submitter.attrs.get("value", ""))
    return data
