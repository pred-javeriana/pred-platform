"""Series publicadas del campeón para Resultados; sin selección ni cálculos estadísticos."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace

from pred_platform.app.resultados_m3 import M3Decision
from pred_platform.contract import models_v2
from pred_platform.contract.errors import DataLayerError, InvalidQuery
from pred_platform.data.query import MAX_SIZE
from pred_platform.data.repository import DataRepository, M3ReserveBlocks


@dataclass(frozen=True, slots=True)
class ForecastPoint:
    """Punto publicado; un valor ausente conserva el hueco de la serie."""

    fecha: str
    valor: float | None
    origen: str | None = None


@dataclass(frozen=True, slots=True)
class ChampionForecastSeries:
    """Información lista para dibujar, independiente de los documentos del repositorio."""

    sku: str
    candidate: str | None = None
    family: str | None = None
    observed: tuple[ForecastPoint, ...] = ()
    forecast: tuple[ForecastPoint, ...] = ()
    blocks: M3ReserveBlocks | None = None
    problem: str | None = None

    @property
    def date_range(self) -> tuple[str, str] | None:
        dates = [p.fecha for p in (*self.observed, *self.forecast)]
        return (min(dates), max(dates)) if dates else None


def _all_pages[T: models_v2.M3Observations | models_v2.M3Forecasts](
    read: Callable[[int], T],
) -> list[T]:
    """Lee todas las páginas con el tamaño efectivo que responde el repositorio."""
    first = read(1)
    pages = [first]
    if first.availability.status == "available":
        for number in range(2, -(-first.page.total // first.page.size) + 1):
            pages.append(read(number))
    return pages


def champion_forecast(
    repository: DataRepository, decision: M3Decision, sku: str
) -> ChampionForecastSeries:
    """Lee únicamente el candidato del veredicto en la corrida M3 ya resuelta, con h=1."""
    series = ChampionForecastSeries(sku)
    verdict = decision.verdicts.get(sku)
    if decision.run is None or verdict is None or sku in decision.excluded:
        return replace(
            series, problem="No hay un veredicto M3 con pronóstico campeón para este SKU."
        )
    series = replace(series, candidate=verdict.candidato_campeon, family=verdict.familia_campeona)
    if not series.candidate:
        return replace(series, problem="M3 no publicó un candidato campeón para este SKU.")
    run_id = decision.run.run_id
    try:
        observations = _all_pages(
            lambda page: repository.get_m3_observations(run_id, sku=sku, page=page, size=MAX_SIZE)
        )
    except (DataLayerError, InvalidQuery):
        return replace(series, problem="La demanda observada publicada no está disponible.")
    if any(doc.availability.status != "available" for doc in observations):
        return replace(series, problem="La demanda observada publicada no está disponible.")
    observed = tuple(
        ForecastPoint(row.fecha, row.valor)
        for row in sorted((row for doc in observations for row in doc.items), key=lambda r: r.fecha)
    )
    if not any(p.valor is not None for p in observed):
        return replace(series, problem="No hay valores de demanda observada disponibles.")
    candidate = series.candidate
    try:
        forecasts = _all_pages(
            lambda page: repository.get_m3_forecasts(
                run_id, sku=sku, candidato_id=candidate, h=1, page=page, size=MAX_SIZE
            )
        )
    except (DataLayerError, InvalidQuery):
        return replace(series, problem="El pronóstico publicado del campeón no está disponible.")
    if any(doc.availability.status != "available" for doc in forecasts):
        return replace(series, problem="El pronóstico publicado del campeón no está disponible.")
    forecast = tuple(
        ForecastPoint(row.fecha, row.valor, row.origen)
        for row in sorted(
            (row for doc in forecasts for row in doc.items), key=lambda r: (r.fecha, r.origen)
        )
    )
    if not any(p.valor is not None for p in forecast):
        return replace(
            series, problem="No hay pronósticos utilizables del campeón con horizonte h=1."
        )
    return replace(series, observed=observed, forecast=forecast, blocks=decision.blocks)
