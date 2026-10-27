"""The queries of the M3 read operations (contract v2, section 7), shared by both data sources.

Each ``*_query`` function validates the caller's filters and returns an ``M3Query``: exact matches,
sets of allowed values, an optional case-insensitive search and the order, which always ends with
the operation's natural key. ``FixtureRepository`` applies it to the examples and
``DalRepository`` turns it into SQL, so the filter rules, the two verdict groups and the order have
one owner. A filter value that cannot exist is rejected (``InvalidQuery``), never ignored.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any, get_args

from pred_platform.contract import models_v2
from pred_platform.contract.errors import InvalidQuery
from pred_platform.data.query import DEFAULT_SIZE, PageRequest, check_choice, check_sort

VERDICTS: tuple[str, ...] = get_args(models_v2.Veredicto)
# The two presentation groups of the contract (section 4): evaluated and not evaluated.
GROUPS: dict[str, tuple[str, ...]] = {
    "evaluados": ("VALIDADO", "EXPLORATORIO"),
    "no_evaluados": ("FALLO_TECNICO", "NO_EVALUABLE", "EVIDENCIA_INSUFICIENTE"),
}
SKU_CLASSES: tuple[str, ...] = get_args(models_v2.SkuClass)
FAMILIES: tuple[str, ...] = get_args(models_v2.Family)
RUN_STATES: tuple[str, ...] = get_args(models_v2.EstadoM3)
STORED_METRICS: tuple[str, ...] = get_args(models_v2.MetricaPersistida)
PORTFOLIO_METRICS: tuple[str, ...] = get_args(models_v2.Metrica)
UNIT_STATES: tuple[str, ...] = ("confirmada", "fallida")
AGGREGATED_WINDOW = "agregada"

# Sort keys of the evidence list; every order ends with the SKU, the natural key.
EVIDENCE_SORT: tuple[str, ...] = (
    "sku",
    "sku_class",
    "veredicto",
    "razon_sn",
    "n_ventanas",
    "dm_p_ajustado",
)


@dataclass(frozen=True, slots=True)
class M3Query:
    """A validated filter, order and page over the rows of one M3 operation.

    ``exact`` and ``any_of`` name row fields (the published SQL column names); ``contains`` is a
    case-insensitive substring search on one field. ``order`` lists ``(field, descending)``;
    a ``None`` value sorts last in either direction.
    """

    page: PageRequest
    exact: Mapping[str, object] = field(default_factory=dict)
    any_of: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    contains: tuple[str, str] | None = None
    order: tuple[tuple[str, bool], ...] = ()

    def matches(self, row: Any) -> bool:
        """Whether a contract row (attributes named like the fields) passes the filter."""
        if any(getattr(row, name) != value for name, value in self.exact.items()):
            return False
        if any(getattr(row, name) not in values for name, values in self.any_of.items()):
            return False
        if self.contains is not None:
            name, needle = self.contains
            return needle.casefold() in str(getattr(row, name)).casefold()
        return True


def _exact(**values: object) -> dict[str, object]:
    return {name: value for name, value in values.items() if value is not None and value != ""}


def _text(value: str | None) -> str | None:
    return value.strip() if value is not None and value.strip() else None


def _iso_date(name: str, value: str | None) -> str | None:
    if not value:
        return None
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise InvalidQuery(f"{name} must be a date AAAA-MM-DD, got {value!r}") from exc
    if len(value) != 10:
        raise InvalidQuery(f"{name} must be a date AAAA-MM-DD, got {value!r}")
    return value


def _positive(name: str, value: int | None) -> int | None:
    if value is None:
        return None
    if type(value) is not int or value < 1:
        raise InvalidQuery(f"{name} must be an integer >= 1, got {value!r}")
    return value


def runs_query(
    *,
    identidad: str | None = None,
    ingesta_sha256: str | None = None,
    estado: str | None = None,
    page: int = 1,
    size: int = DEFAULT_SIZE,
) -> M3Query:
    """Runs, newest first (``creado_en DESC, run_id DESC``), the order that picks ``latest``.

    The M2 reference is not a row filter: each source resolves it to ``run_id_m2`` first.
    """
    return M3Query(
        page=PageRequest(page, size),
        exact=_exact(
            identidad=identidad,
            ingesta_sha256=_text(ingesta_sha256),
            estado=check_choice("estado", estado, RUN_STATES),
        ),
        order=(("creado_en", True), ("run_id", True)),
    )


def evidence_query(
    *,
    skus: Sequence[str] | None = None,
    q: str | None = None,
    sku_class: str | None = None,
    veredicto: str | None = None,
    grupo: str | None = None,
    familia_campeona: str | None = None,
    sort: str = "sku",
    descending: bool = False,
    page: int = 1,
    size: int = DEFAULT_SIZE,
) -> M3Query:
    """Verdicts by exact SKUs or a SKU search, class, verdict, group and champion family."""
    key = check_sort(sort, EVIDENCE_SORT)
    group = check_choice("grupo", grupo, tuple(GROUPS))
    any_of: dict[str, tuple[str, ...]] = {}
    if skus is not None:
        any_of["sku"] = tuple(dict.fromkeys(skus))
    if group is not None:
        any_of["veredicto"] = GROUPS[group]
    needle = _text(q)
    order = ((key, descending),) + ((("sku", False),) if key != "sku" else ())
    return M3Query(
        page=PageRequest(page, size),
        exact=_exact(
            sku_class=check_choice("sku_class", sku_class, SKU_CLASSES),
            veredicto=check_choice("veredicto", veredicto, VERDICTS),
            familia_campeona=check_choice("familia_campeona", familia_campeona, FAMILIES),
        ),
        any_of=any_of,
        contains=("sku", needle) if needle else None,
        order=order,
    )


def metrics_query(
    *,
    sku: str | None = None,
    candidato_id: str | None = None,
    ventana: str | None = None,
    metrica: str | None = None,
    page: int = 1,
    size: int = DEFAULT_SIZE,
) -> M3Query:
    window = ventana if ventana == AGGREGATED_WINDOW else _iso_date("ventana", ventana)
    return M3Query(
        page=PageRequest(page, size),
        exact=_exact(
            sku=_text(sku),
            candidato_id=_text(candidato_id),
            ventana=window,
            metrica=check_choice("metrica", metrica, STORED_METRICS),
        ),
        order=(("sku", False), ("candidato_id", False), ("ventana", False), ("metrica", False)),
    )


def categories_query(
    *,
    sku_class: str | None = None,
    familia: str | None = None,
    page: int = 1,
    size: int = DEFAULT_SIZE,
) -> M3Query:
    """Categories by class and by the family the category selected."""
    return M3Query(
        page=PageRequest(page, size),
        exact=_exact(
            sku_class=check_choice("sku_class", sku_class, SKU_CLASSES),
            familia_campeona=check_choice("familia", familia, FAMILIES),
        ),
        order=(("sku_class", False),),
    )


def portfolio_query(
    *,
    sku_class: str | None = None,
    familia: str | None = None,
    metrica: str | None = None,
    page: int = 1,
    size: int = DEFAULT_SIZE,
) -> M3Query:
    return M3Query(
        page=PageRequest(page, size),
        exact=_exact(
            sku_class=check_choice("sku_class", sku_class, SKU_CLASSES),
            familia=check_choice("familia", familia, FAMILIES),
            metrica=check_choice("metrica", metrica, PORTFOLIO_METRICS),
        ),
        order=(("sku_class", False), ("familia", False), ("metrica", False)),
    )


def forecasts_query(
    *,
    sku: str | None = None,
    candidato_id: str | None = None,
    origen: str | None = None,
    h: int | None = None,
    page: int = 1,
    size: int = DEFAULT_SIZE,
) -> M3Query:
    return M3Query(
        page=PageRequest(page, size),
        exact=_exact(
            sku=_text(sku),
            candidato_id=_text(candidato_id),
            origen=_iso_date("origen", origen),
            h=_positive("h", h),
        ),
        order=(("sku", False), ("candidato_id", False), ("origen", False), ("h", False)),
    )


def failures_query(
    *,
    sku: str | None = None,
    candidato_id: str | None = None,
    page: int = 1,
    size: int = DEFAULT_SIZE,
) -> M3Query:
    return M3Query(
        page=PageRequest(page, size),
        exact=_exact(sku=_text(sku), candidato_id=_text(candidato_id)),
        order=(("candidato_id", False),),
    )


def units_query(
    *,
    sku: str | None = None,
    candidato_id: str | None = None,
    origen: str | None = None,
    estado: str | None = None,
    page: int = 1,
    size: int = DEFAULT_SIZE,
) -> M3Query:
    return M3Query(
        page=PageRequest(page, size),
        exact=_exact(
            sku=_text(sku),
            candidato_id=_text(candidato_id),
            origen=_iso_date("origen", origen),
            estado=check_choice("estado", estado, UNIT_STATES),
        ),
        order=(("sku", False), ("candidato_id", False), ("origen", False)),
    )


def ordered[T](rows: Sequence[T], query: M3Query) -> list[T]:
    """``rows`` in the query's order: a stable sort per key, last key first, ``None`` last."""
    result = list(rows)
    for name, descending in reversed(query.order):
        present = [row for row in result if getattr(row, name) is not None]
        missing = [row for row in result if getattr(row, name) is None]
        result = sorted(present, key=lambda row: getattr(row, name), reverse=descending) + missing
    return result
