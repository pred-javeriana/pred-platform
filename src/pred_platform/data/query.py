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


@dataclass(frozen=True, slots=True)
class PageRequest:
    """Which page of a list is wanted."""

    number: int = 1
    size: int = DEFAULT_SIZE

    def __post_init__(self) -> None:
        if type(self.number) is not int or self.number < 1:
            raise InvalidQuery(f"page must be an integer >= 1, got {self.number!r}")
        if type(self.size) is not int or not 1 <= self.size <= MAX_SIZE:
            raise InvalidQuery(f"size must be an integer from 1 to {MAX_SIZE}, got {self.size!r}")


def paginate[T](items: Sequence[T], request: PageRequest) -> tuple[list[T], Page]:
    """The wanted slice of ``items`` and its ``Page``; ``total`` counts all of ``items``."""
    start = (request.number - 1) * request.size
    page = Page(number=request.number, size=request.size, total=len(items))
    return list(items[start : start + request.size]), page


def check_sort(sort: str, allowed: Collection[str]) -> str:
    """``sort`` if it is one of ``allowed``; otherwise an ``InvalidQuery`` listing the options."""
    if sort not in allowed:
        raise InvalidQuery(f"unknown sort key {sort!r}; use one of {sorted(allowed)}")
    return sort


def check_choice(name: str, value: str | None, allowed: Collection[str]) -> str | None:
    """``value`` if it is empty or one of ``allowed``; otherwise an ``InvalidQuery``.

    A filter with a value that cannot exist is a bug in the caller, not an empty result.
    """
    if value is None or value == "":
        return None
    if value not in allowed:
        raise InvalidQuery(f"unknown {name} {value!r}; use one of {sorted(allowed)}")
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
