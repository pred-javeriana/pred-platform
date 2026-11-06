"""Lumpy effect ranges on full and shared populations, without subtracting ranges.

The descriptive reading compares the same published confirmation statistic in
both populations: exp(median(log(RMSE champion / RMSE comparison))). Per-product
losses use only common valid test windows, as ADR-03-012 decision 4 requires.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from statistics import median

from pred_platform.evidence.models import EvidenceInvalid, RunEvidence, Verdict


@dataclass(frozen=True)
class PairEffect:
    """Source row keys and the common-window loss sums behind one product's ratio."""

    run_id: str
    seed: int
    sku: str
    comparison: str
    champion_id: str | None
    comparison_id: str | None
    origins: tuple[str, ...]
    champion_loss: float | None
    comparison_loss: float | None
    log_ratio: float | None
    cause: str | None


@dataclass(frozen=True)
class SeedEffect:
    run_id: str
    seed: int
    champion: str
    comparison: str
    full_count: int
    full_median: float | None
    common_count: int
    common_median: float | None
    common_cause: str | None


@dataclass(frozen=True)
class EffectRange:
    comparison: str
    full_min: float | None
    full_max: float | None
    full_range: float | None
    common_min: float | None
    common_max: float | None
    common_range: float | None
    cause: str | None


@dataclass(frozen=True)
class LumpyEvidence:
    common_competing: tuple[str, ...]
    effects: tuple[SeedEffect, ...]
    ranges: tuple[EffectRange, ...]
    products: tuple[PairEffect, ...]
    cause: str | None = None


# (run_id, sku, candidate_id) -> origin -> RMSE, all from m3_metricas.
Windows = Mapping[tuple[str, str, str], Mapping[str, float]]
# (run_id, comparison family) -> (published count, published median).
PublishedEffects = Mapping[tuple[str, str], tuple[int, float | None]]


def calculate_lumpy(
    runs: Sequence[RunEvidence],
    windows: Windows,
    published: PublishedEffects,
    champions: Mapping[str, str],
) -> LumpyEvidence:
    """Read-side descriptive ranges; no stability threshold or causal subtraction.

    The fixed shared population first requires competition in every run. For each
    comparison it then requires enough common test windows in every run. Products
    without those windows remain listed with their per-run causes, not as zeros.
    """
    per_run = [{v.sku: v for v in r.verdicts if v.sku_class == "lumpy"} for r in runs]
    if not any(per_run):
        return LumpyEvidence((), (), (), (), "No hay productos lumpy en el grupo.")
    if any(r.run_id not in champions for r in runs):
        return LumpyEvidence((), (), (), (), "Falta la confirmación publicada de lumpy.")
    common = set.intersection(*(set(v) for v in per_run))
    # The final lumpy comparison is foundation against SARIMA and Seasonal Naive.
    # A different class champion cannot be attributed to new SARIMA draws.
    if set(champions.values()) != {"foundation"}:
        return LumpyEvidence(
            tuple(sorted(common)),
            (),
            (),
            (),
            "La campeona de lumpy no es Fundacional en todas las corridas.",
        )
    comparisons = ("seasonal_naive", "classical")
    products = tuple(
        _pair(run, v, comparison, windows)
        for run, verdicts in zip(runs, per_run, strict=True)
        for v in verdicts.values()
        for comparison in comparisons
    )
    effects = []
    ranges = []
    for comparison in comparisons:
        own = [p for p in products if p.comparison == comparison]
        common_valid = common.intersection(
            *({p.sku for p in own if p.run_id == r.run_id and p.cause is None} for r in runs)
        )
        seeds = []
        for run in runs:
            full = [p for p in own if p.run_id == run.run_id and p.cause is None]
            shared = [p for p in full if p.sku in common_valid]
            full_median, _ = _center(full)
            common_median, common_cause = _center(shared)
            if (run.run_id, comparison) not in published:
                raise EvidenceInvalid(f"Falta el efecto publicado de {comparison} en {run.run_id}.")
            count, stored = published[run.run_id, comparison]
            if count != len(full) or not _same(stored, full_median):
                raise EvidenceInvalid(
                    f"Las ventanas no reproducen el efecto publicado de {comparison} "
                    f"en {run.run_id}; no se atribuye una diferencia sin reconciliarla."
                )
            seeds.append(
                SeedEffect(
                    run.run_id,
                    run.seed,
                    champions[run.run_id],
                    comparison,
                    count,
                    stored,
                    len(shared),
                    common_median,
                    common_cause,
                )
            )
        effects.extend(seeds)
        low, high, span = _range([s.full_median for s in seeds])
        common_low, common_high, common_span = _range([s.common_median for s in seeds])
        ranges.append(
            EffectRange(
                comparison,
                low,
                high,
                span,
                common_low,
                common_high,
                common_span,
                "rango_no_definido" if span is None or common_span is None else None,
            )
        )
    return LumpyEvidence(tuple(sorted(common)), tuple(effects), tuple(ranges), products)


def _pair(run: RunEvidence, verdict: Verdict, comparison: str, windows: Windows) -> PairEffect:
    blocks = run.protocol["bloques"]
    start, end = (blocks["origenes_prueba"][key] for key in ("primero", "ultimo"))
    chosen = {
        family: _representative(run, verdict, family, windows)
        for family in ("foundation", comparison)
    }
    a_id, b_id = chosen["foundation"], chosen[comparison]
    a = windows.get((run.run_id, verdict.sku, a_id), {}) if a_id is not None else {}
    b = windows.get((run.run_id, verdict.sku, b_id), {}) if b_id is not None else {}
    origins = tuple(sorted(o for o in a.keys() & b.keys() if start <= o <= end))
    n_min = run.protocol["politica"]["n_min_dm"]
    cause = None
    if a_id is None or b_id is None:
        cause = "sin_instancia_de_ambas_familias"
    elif len(origins) < n_min:
        cause = f"pocas_ventanas_comunes:{len(origins)}<{n_min}"
    a_sum = math.fsum(a[o] ** 2 for o in origins) if origins else None
    b_sum = math.fsum(b[o] ** 2 for o in origins) if origins else None
    log_ratio = None
    if cause is None:
        assert a_sum is not None and b_sum is not None
        log_ratio = (
            0.0
            if a_sum == b_sum == 0
            else -math.inf
            if a_sum == 0
            else math.inf
            if b_sum == 0
            else (math.log(a_sum) - math.log(b_sum)) / 2
        )
    return PairEffect(
        run.run_id,
        run.seed,
        verdict.sku,
        comparison,
        a_id,
        b_id,
        origins,
        a_sum,
        b_sum,
        log_ratio,
        cause,
    )


def _representative(
    run: RunEvidence, verdict: Verdict, family: str, windows: Windows
) -> str | None:
    candidates = [c.candidato_id for c in verdict.modelos_evaluados if c.familia == family]
    if not candidates:
        return None
    # Match the frozen selection rule. Candidate order also owns exact RMSE ties
    # and the first-instance fallback when no selection windows are valid.
    start, end = (run.protocol["bloques"]["origenes_seleccion"][k] for k in ("primero", "ultimo"))
    scores = []
    for candidate in candidates:
        values = [
            v
            for o, v in windows.get((run.run_id, verdict.sku, candidate), {}).items()
            if start <= o <= end
        ]
        if values:
            scores.append((math.fsum(v * v for v in values) / len(values), candidate))
    return min(scores, key=lambda s: s[0])[1] if scores else candidates[0]


def _center(pairs: Sequence[PairEffect]) -> tuple[float | None, str | None]:
    if not pairs:
        return None, "sin_productos_comparables"
    values = [p.log_ratio for p in pairs]
    assert all(v is not None for v in values)
    center = median(v for v in values if v is not None)
    if math.isnan(center):  # central values -inf and +inf, no epsilon or dropped products.
        return None, "perdida_cero:mediana"
    return math.exp(center), None


def _same(a: float | None, b: float | None) -> bool:
    if a is None or b is None:
        return a is b
    # Numerical reconciliation only, not an effect-stability decision threshold.
    return a == b or math.isclose(a, b, rel_tol=1e-10, abs_tol=1e-12)


def _range(values: Sequence[float | None]) -> tuple[float | None, float | None, float | None]:
    if any(v is None for v in values):
        return None, None, None
    low, high = min(v for v in values if v is not None), max(v for v in values if v is not None)
    span = high - low
    return low, high, None if math.isnan(span) else span
