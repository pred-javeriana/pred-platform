"""Filtering, sorting and paging for the list operations (contract, section 10).

Every list is filtered, sorted and paged on the server (RNF-DES-04). These helpers are shared by
the repositories so both sources behave the same way.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass
from typing import Any

from pred_platform.contract.errors import InvalidQuery
from pred_platform.contract.models import Page

DEFAULT_SIZE = 50
MAX_SIZE = 500
# A page past this one cannot exist (500 rows each) and its offset would overflow SQLite's INTEGER.
MAX_PAGE = 10**9

# A run reference may be this alias instead of an id: the most recent run. A view that does not
# know which run to show asks for ``LATEST``; the repository resolves it.
LATEST = "latest"


@dataclass(frozen=True, slots=True)
class PageRequest:
    """Which page of a list is wanted."""

    number: int = 1
    size: int = DEFAULT_SIZE

    def __post_init__(self) -> None:
        if type(self.number) is not int or not 1 <= self.number <= MAX_PAGE:
            limit = f"{MAX_PAGE:,}".replace(",", ".")
            raise InvalidQuery(
                f"La página debe ser un número entero de 1 a {limit}; llegó «{self.number}»."
            )
        if type(self.size) is not int or not 1 <= self.size <= MAX_SIZE:
            raise InvalidQuery(
                f"El tamaño de página debe ser un número entero de 1 a {MAX_SIZE}; "
                f"llegó «{self.size}»."
            )


def paginate[T](items: Sequence[T], request: PageRequest) -> tuple[list[T], Page]:
    """The wanted slice of ``items`` and its ``Page``; ``total`` counts all of ``items``."""
    start = (request.number - 1) * request.size
    page = Page(number=request.number, size=request.size, total=len(items))
    return list(items[start : start + request.size]), page


# The Spanish name of each query parameter, for the messages a view shows as they are.
_LABELS = {
    "sku_class": "clase de demanda",
    "family": "familia",
    "familia": "familia",
    "familia_campeona": "familia campeona",
    "estado": "estado",
    "grupo": "grupo",
    "veredicto": "veredicto",
    "metrica": "métrica",
    "ventana": "ventana",
    "fecha": "fecha",
    "origen": "origen",
    "h": "horizonte h",
    "page": "página",
    "size": "tamaño de página",
    "history_page": "página del historial",
}


def label(name: str) -> str:
    """The Spanish name of the query parameter ``name`` (the name itself when it has none)."""
    return _LABELS.get(name, name)


def _options(allowed: Collection[str]) -> str:
    return ", ".join(sorted(allowed))


def int_param(raw: str | None, default: int, name: str) -> int:
    """The integer in a query parameter, ``default`` when it is absent or empty."""
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise InvalidQuery(
            f"El parámetro {label(name)} debe ser un número entero; llegó «{raw}»."
        ) from exc


def check_sort(sort: str, allowed: Collection[str]) -> str:
    """``sort`` if it is one of ``allowed``; otherwise an ``InvalidQuery`` listing the options."""
    if sort not in allowed:
        raise InvalidQuery(f"Orden desconocido «{sort}»; use uno de: {_options(allowed)}.")
    return sort


def check_choice(name: str, value: str | None, allowed: Collection[str]) -> str | None:
    """``value`` if it is empty or one of ``allowed``; otherwise an ``InvalidQuery``.

    A filter with a value that cannot exist is a bug in the caller, not an empty result.
    """
    if value is None or value == "":
        return None
    if value not in allowed:
        raise InvalidQuery(
            f"Valor desconocido para {label(name)}: «{value}»; use uno de: {_options(allowed)}."
        )
    return value


def sort_items[T](
    items: Sequence[T], key: Callable[[T], Any], *, descending: bool = False
) -> list[T]:
    """Sort by ``key``; items whose key is ``None`` go last in either direction."""
    present = [item for item in items if key(item) is not None]
    missing = [item for item in items if key(item) is None]
    return sorted(present, key=key, reverse=descending) + missing


def contains_text(needle: str | None, *haystacks: str | None) -> bool:
    """Case-insensitive substring match; an empty or missing ``needle`` matches everything."""
    if not needle or not needle.strip():
        return True
    wanted = needle.strip().casefold()
    return any(text is not None and wanted in text.casefold() for text in haystacks)
