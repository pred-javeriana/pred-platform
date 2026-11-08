"""Lectura compartida del grupo final: selección explícita y mismas fuentes publicadas."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from urllib.parse import urlencode

from pred_platform.app.presentation import group_problem_text
from pred_platform.data.repository import DataRepository
from pred_platform.evidence.models import EvidenceInvalid, GroupEvidence, GroupRunOption


def group_selection(
    params: Mapping[str, str], options: tuple[GroupRunOption, ...]
) -> tuple[dict[int, list[GroupRunOption]], tuple[str, ...]]:
    by_seed = {seed: [r for r in options if r.seed == seed] for seed in range(3)}
    # Publication alone does not declare a run to be final thesis evidence.
    # Require explicit choices, even when only one publication exists per seed.
    selected = tuple(params.get(f"semilla{seed}", "").strip() for seed in range(3))
    return by_seed, selected


def read_group(
    repository: DataRepository, by_seed: dict[int, list[GroupRunOption]], selected: tuple[str, ...]
) -> GroupEvidence:
    for seed, run_id in enumerate(selected):
        if run_id not in {r.run_id for r in by_seed[seed]}:
            raise EvidenceInvalid(f"Seleccione una corrida PUBLICADA de la semilla {seed}.")
    return repository.get_m3_group(selected)


def group_context(repository: DataRepository, params: Mapping[str, str]) -> dict[str, Any]:
    context: dict[str, Any] = {
        "active_surface": "resultados",
        "page_title": "Grupo de semillas",
        "options": {seed: [] for seed in range(3)},
        "selected": ("", "", ""),
        "group": None,
        "problem": None,
        "product": None,
        "sku": params.get("sku", "").strip(),
        "download_url": "",
        "source": repository.source,
    }
    try:
        by_seed, selected = group_selection(params, repository.list_m3_group_runs())
        context.update(options=by_seed, selected=selected)
        if all(selected):
            group = read_group(repository, by_seed, selected)
            context["group"] = group
            context["download_url"] = "/resultados/grupo.json?" + urlencode(
                {f"semilla{seed}": r for seed, r in enumerate(selected)}
            )
            context["product"] = next(
                (p for p in group.significance.products if p.sku == context["sku"]), None
            )
        elif any(params.get(f"semilla{s}") for s in range(3)):
            context["problem"] = group_problem_text(
                "Falta elegir una corrida publicada para cada semilla."
            )
    except (EvidenceInvalid, ValueError) as exc:
        context["problem"] = group_problem_text(str(exc))
    return context
