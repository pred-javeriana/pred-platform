"""Consulta de la evidencia publicada por M3 (RF-CON-05): clase, familia campeona y veredicto.

Esta consulta es de M3, no de la selección de M2: la lista sale de ``get_m3_evidence``, que filtra,
ordena y pagina en el servidor. Aquí solo se lee la query HTTP y se vuelve a escribir para los
enlaces; los valores desconocidos los rechaza el repositorio (``InvalidQuery``), no se ignoran. Los
enlaces conservan siempre la corrida M3 resuelta, nunca «la más nueva» (contrato v2, sección 2).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

from pred_platform.contract.errors import InvalidQuery
from pred_platform.data.query import LATEST, int_param

# El orden por defecto de la evidencia de M3 es el SKU; el repositorio acepta las claves de
# ``m3_query.EVIDENCE_SORT`` y rechaza las demás.
DEFAULT_SORT = "sku"
PRODUCT_PAGE_SIZE = 25
TEST_RESULTS = {
    "mejora": "Mejora verificada",
    "sin_diferencia": "Sin diferencia demostrada",
    "peor": "Peor que la línea base",
    "sin_prueba": "Sin prueba",
}


@dataclass(frozen=True, slots=True)
class ConsultaQuery:
    """Parámetros que se delegan en ``get_m3_evidence``, sin filtrar en el cliente."""

    ejecucion: str = LATEST
    # La corrida M3 exacta; vacía, la PUBLICADA más nueva que resuelve el repositorio.
    m3: str | None = None
    sku_class: str | None = None
    familia_campeona: str | None = None
    veredicto: str | None = None
    q: str | None = None
    prueba: str | None = None
    sort: str = DEFAULT_SORT
    descending: bool = False
    page: int = 1
    size: int = PRODUCT_PAGE_SIZE

    def as_kwargs(self) -> dict[str, Any]:
        return {
            "q": self.q,
            "sku_class": self.sku_class,
            "veredicto": self.veredicto,
            "familia_campeona": self.familia_campeona,
            "sort": self.sort,
            "descending": self.descending,
            "page": self.page,
            "size": self.size,
        }

    def query_string(self, **overrides: object) -> str:
        """Query string de la lista de veredictos; conserva filtros, ejecución y corrida M3."""
        values: dict[str, object] = {
            "vista": "sku",
            "ejecucion": "" if self.ejecucion == LATEST else self.ejecucion,
            "m3": self.m3 or "",
            "sku_class": self.sku_class or "",
            "familia_campeona": self.familia_campeona or "",
            "veredicto": self.veredicto or "",
            "q": self.q or "",
            "prueba": self.prueba or "",
            "sort": "" if self.sort == DEFAULT_SORT else self.sort,
            "descending": "1" if self.descending else "",
            "page": self.page if self.page > 1 else "",
            "size": "" if self.size == PRODUCT_PAGE_SIZE else self.size,
        }
        values.update(overrides)
        return urlencode(
            [
                (k, "1" if v is True else str(v))
                for k, v in values.items()
                if v not in ("", None, False)
            ]
        )

    def detail_query(self, **overrides: object) -> str:
        """Lo que el detalle de un SKU necesita para volver a esta consulta sin perder nada."""
        values: dict[str, object] = {
            "ejecucion": "" if self.ejecucion == LATEST else self.ejecucion,
            "m3": self.m3 or "",
            "volver": self.query_string(),
        }
        values.update(overrides)
        return urlencode([(k, str(v)) for k, v in values.items() if v not in ("", None, False)])


def parse_consulta_query(params: Mapping[str, str]) -> ConsultaQuery:
    """Lee la query HTTP; los valores desconocidos los rechaza el repositorio (``InvalidQuery``)."""
    prueba = params.get("prueba") or None
    if prueba is not None and prueba not in TEST_RESULTS:
        raise InvalidQuery("El resultado de la prueba no es válido: «" + prueba + "».")
    return ConsultaQuery(
        prueba=prueba,
        ejecucion=(params.get("ejecucion") or "").strip() or LATEST,
        m3=(params.get("m3") or "").strip() or None,
        sku_class=params.get("sku_class") or None,
        familia_campeona=params.get("familia_campeona") or None,
        veredicto=params.get("veredicto") or None,
        q=params.get("q") or None,
        sort=params.get("sort") or DEFAULT_SORT,
        descending=params.get("descending") in {"1", "true", "on"},
        page=int_param(params.get("page"), 1, "page"),
        size=int_param(params.get("size"), PRODUCT_PAGE_SIZE, "size"),
    )
