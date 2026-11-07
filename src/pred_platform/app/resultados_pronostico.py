"""Series publicadas del campeón para Resultados; sin selección ni cálculos estadísticos."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

from pred_platform.app.presentation import describe_status, family_label, format_decimal
from pred_platform.app.resultados_m3 import M3Decision
from pred_platform.contract import models_v2
from pred_platform.contract.errors import DataLayerError, InvalidQuery
from pred_platform.dal.lectura import _open_readonly
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
    alt_candidate: str | None = None
    alt_family: str | None = None
    alt_model: str | None = None
    alt_r: float | None = None
    alt_forecast: tuple[ForecastPoint, ...] = ()

    @property
    def date_range(self) -> tuple[str, str] | None:
        dates = [p.fecha for p in (*self.observed, *self.forecast, *self.alt_forecast)]
        return (min(dates), max(dates)) if dates else None


@dataclass(frozen=True, slots=True)
class DemandBlockStats:
    n: int
    zero: int
    zero_pct: float
    mean: float | None
    max: float | None
    fc: float | None
    alt_fc: float | None = None

    @property
    def n_days(self) -> int:
        return self.n

    @property
    def n_zero_days(self) -> int:
        return self.zero

    @property
    def zero_share(self) -> float:
        return (self.zero / self.n) if self.n > 0 else 0.0

    @property
    def mean_demand(self) -> float | None:
        return self.mean

    @property
    def max_demand(self) -> float | None:
        return self.max

    @property
    def mean_forecast_champ(self) -> float | None:
        return self.fc

    @property
    def mean_forecast_alt(self) -> float | None:
        return self.alt_fc


@dataclass(frozen=True, slots=True)
class ProductDemandBlocks:
    selection: DemandBlockStats
    test: DemandBlockStats


def compute_demand_blocks(
    series: ChampionForecastSeries,
) -> ProductDemandBlocks | None:
    """Estadísticas descriptivas de demanda y pronósticos por bloque para un producto."""
    if (
        series.blocks is None
        or series.blocks.selection_dates.start is None
        or series.blocks.selection_dates.end is None
        or series.blocks.test_dates.start is None
        or series.blocks.test_dates.end is None
        or not series.observed
    ):
        return None

    b_sel = (series.blocks.selection_dates.start, series.blocks.selection_dates.end)
    b_test = (series.blocks.test_dates.start, series.blocks.test_dates.end)

    def _block_stats(start: str, end: str) -> DemandBlockStats:
        obs_vals = [
            p.valor for p in series.observed if start <= p.fecha <= end and p.valor is not None
        ]
        n = len(obs_vals)
        zero = sum(1 for v in obs_vals if v == 0)
        zero_pct = (zero / n * 100.0) if n > 0 else 0.0
        mean = (sum(obs_vals) / n) if n > 0 else None
        max_val = max(obs_vals) if n > 0 else None

        fc_vals = [
            p.valor for p in series.forecast if start <= p.fecha <= end and p.valor is not None
        ]
        fc_mean = (sum(fc_vals) / len(fc_vals)) if fc_vals else None

        alt_fc_mean: float | None = None
        if series.alt_forecast:
            alt_vals = [
                p.valor
                for p in series.alt_forecast
                if start <= p.fecha <= end and p.valor is not None
            ]
            alt_fc_mean = (sum(alt_vals) / len(alt_vals)) if alt_vals else None

        return DemandBlockStats(
            n=n,
            zero=zero,
            zero_pct=zero_pct,
            mean=mean,
            max=max_val,
            fc=fc_mean,
            alt_fc=alt_fc_mean,
        )

    return ProductDemandBlocks(
        selection=_block_stats(*b_sel),
        test=_block_stats(*b_test),
    )


def best_other_model(verdict: models_v2.M3Verdict) -> models_v2.EvaluatedModel | None:
    """El mejor modelo de un producto cuando superó al campeón (r estrictamente menor)."""
    if verdict.razon_sn is None or not verdict.modelos_evaluados:
        return None
    candidates = [
        m
        for m in verdict.modelos_evaluados
        if m.razon_sn is not None and m.candidato_id != verdict.candidato_campeon
    ]
    if not candidates:
        return None
    best = min(candidates, key=lambda m: m.razon_sn if m.razon_sn is not None else float("inf"))
    if best.razon_sn is not None and best.razon_sn < verdict.razon_sn:
        return best
    return None


def product_outcome_sentence(verdict: models_v2.M3Verdict | None) -> str:
    """Frase concluyente sobre el desempeño del campeón frente a la línea base.

    Reporte 9.1 ítem 6: los productos peores que la línea base muestran todos sus datos
    sin añadir frases explicativas ni causas especulativas.
    """
    if verdict is None or verdict.razon_sn is None:
        status = describe_status("verdict", verdict.veredicto) if verdict else None
        hint = status.hint.lower() if status and status.hint else "no se pudo evaluar"
        return f"Este producto no se pudo evaluar: {hint}"

    fam = family_label(verdict.familia_campeona)
    r = verdict.razon_sn

    if verdict.dm_significativa and verdict.dm_estadistico is not None:
        if verdict.dm_estadistico < 0:
            diff = (1.0 - r) * 100.0
            return (
                f"{fam} se equivocó {format_decimal(diff, 1)} % menos que la línea base en este"
                " producto, y la prueba confirma la diferencia."
            )
        if verdict.dm_estadistico > 0:
            diff = (r - 1.0) * 100.0
            return (
                f"{fam} se equivocó {format_decimal(diff, 1)} % más que la línea base en este"
                " producto, y la prueba confirma que es peor."
            )

    if r < 1.0:
        diff_str = f"{format_decimal((1.0 - r) * 100.0, 1)} % menos"
    else:
        diff_str = f"{format_decimal((r - 1.0) * 100.0, 1)} % más"
    return (
        f"{fam} se equivocó {diff_str} que la línea base, pero la prueba no encuentra una"
        " diferencia significativa."
    )


def read_sku_r_interval(
    db_path: Path | None, run_id: str, sku: str
) -> tuple[float, float, float | None] | None:
    """Lee el intervalo de 95 % para r publicado en ``m3_intervalos_r`` si la base está
    accesible."""
    if not db_path or not db_path.is_file():
        return None
    try:
        with _open_readonly(db_path) as conn:
            if conn is None:
                return None
            cursor = conn.cursor()
            cursor.execute(
                "SELECT inferior, superior, inferior_cola FROM m3_intervalos_r "
                "WHERE run_id = ? AND sku = ?",
                (run_id, sku),
            )
            row = cursor.fetchone()
            if row:
                lo = float(row[0])
                hi = float(row[1]) if row[1] is not None else float("inf")
                cola = float(row[2]) if row[2] is not None else None
                return (lo, hi, cola)
    except Exception:
        pass
    return None


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
    repository: DataRepository,
    decision: M3Decision,
    sku: str,
    *,
    include_alt: bool = False,
) -> ChampionForecastSeries:
    """Lee únicamente el candidato del veredicto en la corrida M3 ya resuelta, con h=1."""
    series = ChampionForecastSeries(sku)
    verdict = decision.verdicts.get(sku)
    if decision.run is None or verdict is None or sku in decision.excluded:
        return replace(
            series, problem="No hay un veredicto con pronóstico del modelo campeón para este SKU."
        )
    series = replace(series, candidate=verdict.candidato_campeon, family=verdict.familia_campeona)
    if not series.candidate:
        return replace(
            series, problem="La evaluación final no publicó un modelo campeón para este SKU."
        )
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

    alt_candidate = None
    alt_family = None
    alt_model = None
    alt_r = None
    alt_forecast: tuple[ForecastPoint, ...] = ()

    other = best_other_model(verdict)
    if include_alt and other is not None:
        alt_candidate = other.candidato_id
        alt_family = other.familia
        alt_model = other.modelo
        alt_r = other.razon_sn
        try:
            alt_docs = _all_pages(
                lambda page: repository.get_m3_forecasts(
                    run_id, sku=sku, candidato_id=other.candidato_id, h=1, page=page, size=MAX_SIZE
                )
            )
            if all(doc.availability.status == "available" for doc in alt_docs):
                alt_forecast = tuple(
                    ForecastPoint(row.fecha, row.valor, row.origen)
                    for row in sorted(
                        (row for doc in alt_docs for row in doc.items),
                        key=lambda r: (r.fecha, r.origen),
                    )
                )
        except (DataLayerError, InvalidQuery):
            alt_forecast = ()

    return replace(
        series,
        observed=observed,
        forecast=forecast,
        blocks=decision.blocks,
        alt_candidate=alt_candidate,
        alt_family=alt_family,
        alt_model=alt_model,
        alt_r=alt_r,
        alt_forecast=alt_forecast,
    )
