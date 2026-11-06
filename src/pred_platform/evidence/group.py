"""Final group significance: ADR-03-012, decision 10, «Grupo final de corridas».

For each product tested in every run, take the largest raw bilateral HLN-DM p,
then apply BH once per demand class. Direction never filters the BH family.
Only a rejection with negative loss differentials in every run is an improvement.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import replace

from pred_platform.evidence.models import (
    ClassSignificance,
    EvidenceInvalid,
    GroupProduct,
    GroupSignificance,
    ProductSource,
    RunEvidence,
    RunReference,
)


def calculate_significance(
    runs: Sequence[RunEvidence], *, preview: bool = False
) -> GroupSignificance:
    """Combine exactly seeds 0/1/2, or seeds 0/1 as an explicitly incomplete preview.

    The caller supplies publications read in one snapshot. The DB reader enforces
    publication and origin before constructing these inputs. Missing tests retain
    their published per-run causes and receive no group p-value.
    """
    ordered = tuple(sorted(runs, key=lambda r: r.seed))
    expected = (0, 1) if preview else (0, 1, 2)
    if tuple(r.seed for r in ordered) != expected:
        raise EvidenceInvalid(f"Se requieren las semillas {expected}; no se reemplazan corridas.")
    if len({r.run_id for r in ordered}) != len(ordered):
        raise EvidenceInvalid("Cada semilla debe tener una corrida distinta.")
    reference = ordered[0]
    for run in ordered:
        if run.ingesta_sha256 != reference.ingesta_sha256:
            raise EvidenceInvalid("Las corridas no comparten la ingesta.")
        # The configuration fingerprint includes the seed. All other identity and
        # protocol fields, including policy, block limits and versions, stay fixed.
        identity = {k: v for k, v in run.identity.items() if k != "configuracion_id"}
        base_identity = {k: v for k, v in reference.identity.items() if k != "configuracion_id"}
        if identity != base_identity or _fixed_protocol(run) != _fixed_protocol(reference):
            raise EvidenceInvalid("Las corridas difieren en algo distinto de la semilla.")
    try:
        alpha = float(reference.protocol["politica"]["alfa_dm"])
    except (KeyError, TypeError, ValueError) as exc:
        raise EvidenceInvalid("Falta alfa_dm en la política publicada.") from exc
    if not math.isfinite(alpha) or not 0 < alpha < 1:
        raise EvidenceInvalid("alfa_dm no es válido.")

    populations = []
    for run in ordered:
        population = {v.sku: v.sku_class for v in (*run.verdicts, *run.exclusions)}
        if len(population) != len(run.verdicts) + len(run.exclusions):
            raise EvidenceInvalid("Un producto aparece más de una vez en una corrida.")
        populations.append(population)
        if population != populations[0]:
            raise EvidenceInvalid("Las corridas no comparten los productos y sus clases.")
        if any(v.dm_p_valor is not None and v.dm_alfa != alpha for v in run.verdicts):
            raise EvidenceInvalid("Una prueba no usa el alfa de la política.")

    sources: dict[str, list[ProductSource]] = defaultdict(list)
    for run in ordered:
        for v in run.verdicts:
            statistic = v.dm_estadistico
            direction = (
                None
                if statistic is None
                else "negativo"
                if statistic < 0
                else "positivo"
                if statistic > 0
                else "cero"
            )
            sources[v.sku].append(
                ProductSource(
                    run.run_id,
                    run.seed,
                    v.dm_p_valor,
                    statistic,
                    direction,
                    v.dm_causa,
                    v.familia_campeona,
                    v.candidato_campeon,
                )
            )
        for e in run.exclusions:
            sources[e.sku].append(
                ProductSource(
                    run.run_id,
                    run.seed,
                    None,
                    None,
                    None,
                    f"{e.codigo}: {e.causa}",
                    None,
                    None,
                )
            )
    products = []
    by_class: dict[str, list[int]] = defaultdict(list)
    for sku, sku_class in sorted(populations[0].items()):
        own = tuple(sources[sku])
        tested = all(s.p_value is not None for s in own)
        p_max = max(s.p_value for s in own if s.p_value is not None) if tested else None
        if tested:
            by_class[sku_class].append(len(products))
        products.append(GroupProduct(sku, sku_class, own, p_max, None, None, None))
    for indices in by_class.values():
        ranked = sorted(indices, key=lambda i: (products[i].p_max, products[i].sku))
        adjusted = 1.0
        for rank in range(len(ranked), 0, -1):
            i = ranked[rank - 1]
            p_max = products[i].p_max
            assert p_max is not None
            adjusted = min(adjusted, p_max * len(ranked) / rank)
            significant = adjusted <= alpha
            products[i] = replace(
                products[i],
                p_adjusted=adjusted,
                significant=significant,
                improved=significant
                and all(s.direction == "negativo" for s in products[i].sources),
            )
    classes = []
    for sku_class in sorted(set(populations[0].values())):
        own = [p for p in products if p.sku_class == sku_class]
        significant = [p for p in own if p.significant]
        better = sum(p.improved is True for p in own)
        worse = sum(p.worsened is True for p in own)
        classes.append(
            ClassSignificance(
                sku_class,
                sum(p.p_max is not None for p in own),
                sum(p.p_max is None for p in own),
                len(significant),
                better,
                worse,
                len(significant) - better - worse,
            )
        )
    references = tuple(
        RunReference(r.run_id, r.seed, r.ingesta_sha256, r.run_id_m2, r.identity, r.protocol)
        for r in ordered
    )
    return GroupSignificance(references, not preview, alpha, tuple(products), tuple(classes))


def _fixed_protocol(run: RunEvidence) -> dict:
    try:
        config = {k: v for k, v in run.protocol["configuracion_m2"].items() if k != "seed"}
        return {**run.protocol, "configuracion_m2": config}
    except (KeyError, AttributeError) as exc:
        raise EvidenceInvalid("Falta la configuración M2 del protocolo publicado.") from exc
