"""Las respuestas de Resultados, leídas de la selección y de las pruebas publicadas.

Solo resume filas para la lectura. No elige campeonas, ajusta p ni cambia veredictos.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from pred_platform.app.corridas import THESIS_SEEDS, group_key
from pred_platform.app.presentation import (
    SKU_CLASS_ORDER,
    family_label,
    format_count,
    format_decimal,
    model_label,
    sku_class_label,
)
from pred_platform.app.resultados_m3 import M3Classes, classes, percent_text
from pred_platform.contract import models_v2
from pred_platform.contract.errors import DataLayerError, InvalidQuery
from pred_platform.contract.models import RunStatus
from pred_platform.data.m3_confirmation import M3ClassConfirmation
from pred_platform.data.repository import DataRepository

CHAPTERS = (
    ("resumen", "Resumen", ""),
    ("p1", "¿Qué familia gana?", "1"),
    ("p2", "¿Se verifica producto a producto?", "2"),
    ("estabilidad", "¿Se sostiene con otra semilla?", "3"),
    ("productos", "Productos", ""),
    ("exportar", "Exportar y reproducir", ""),
)
LEGACY_CHAPTERS = {"clase": "p1", "errores": "p1", "sku": "productos", "consulta": "productos"}
CHAPTER_VIEWS = {
    "resumen": "resumen",
    "p1": "clase",
    "p2": "prueba",
    "estabilidad": "estabilidad",
    "productos": "sku",
    "exportar": "exportar",
}


@dataclass(frozen=True, slots=True)
class Answers:
    family: str | None
    confirmed: int
    types: int
    tested: int
    better: int
    worse: int
    selection: str
    verification: str
    tone: str
    label: str
    effect_range: str

    @property
    def unchanged(self) -> int:
        return self.tested - self.better - self.worse


def answers(found: M3Classes, verdicts: Sequence[models_v2.M3Verdict] = ()) -> Answers:
    """Una familia solo se nombra como ganadora de todos los tipos si las filas lo dicen."""
    families = {row.familia_campeona for row in found.rows}
    family = next(iter(families)) if len(families) == 1 else None
    confirmations = found.confirmations
    confirmed = sum(
        1
        for row in found.rows
        if row.sku_class in confirmations
        and confirmations[row.sku_class].estado == "confirmado"
        and confirmations[row.sku_class].familia_campeona == row.familia_campeona
    )
    types = len(found.rows)
    tested = sum(row.n_probados for row in found.significance)
    better = sum(row.n_mejores for row in found.significance)
    worse = sum(row.n_peores for row in found.significance)
    if not types:
        selection = "Esta evaluación no publicó la selección por tipo de demanda."
    elif family:
        names = {
            model.modelo
            for row in verdicts
            for model in row.modelos_evaluados
            if row.familia_campeona == family and model.candidato_id == row.candidato_campeon
        }
        label = family_label(family)
        model_types = {
            row.sku_class
            for row in verdicts
            if row.familia_campeona == family and row.candidato_campeon is not None
        }
        if len(names) == 1 and model_types == {row.sku_class for row in found.rows}:
            label += f" ({model_label(next(iter(names)))})"
        selection = f"{label} pronostica mejor los {format_count(types)} tipos de demanda."
    else:
        selection = "La familia ganadora cambia según el tipo de demanda."
    if not tested:
        verification = "Esta evaluación no publicó pruebas por producto."
    else:
        verification = (
            f"La mejora se verifica en {format_count(better)} de {format_count(tested)} "
            f"productos probados ({percent_text(better / tested)})."
        )
    effects = [
        r.efecto_mediana_r
        for r in confirmations.values()
        if r.efecto_mediana_r is not None and math.isfinite(r.efecto_mediana_r)
    ]
    effect_range = "Sin efecto publicado"
    if effects:
        low, high = min(effects), max(effects)
        if high < 1:
            effect_range = (
                f"{format_decimal(100 * (1 - high), 1)} a "
                f"{format_decimal(100 * (1 - low), 1)} % menos"
            )
        elif low > 1:
            effect_range = (
                f"{format_decimal(100 * (low - 1), 1)} a "
                f"{format_decimal(100 * (high - 1), 1)} % más"
            )
        else:
            effect_range = f"r = {format_decimal(low, 3)} a {format_decimal(high, 3)}"
    supported = bool(types and confirmed == types)
    return Answers(
        family,
        confirmed,
        types,
        tested,
        better,
        worse,
        selection,
        verification,
        "ok" if supported else "warn" if types else "neutral",
        "Respaldada en esta corrida"
        if supported
        else "No concluyente"
        if types
        else "Sin responder",
        effect_range,
    )


@dataclass(frozen=True, slots=True)
class SeedEvidence:
    seed: int
    run_id: str | None = None
    evidence: M3Classes | None = None


def seed_evidence(
    repository: DataRepository, current: RunStatus, found: M3Classes
) -> tuple[SeedEvidence, ...]:
    """Las semillas 0, 1 y 2 con los mismos datos y ajustes que la corrida abierta.

    De una semilla repetida se muestra la corrida más nueva; la corrida abierta siempre se
    conserva. La confirmación ausente no se convierte en confirmación negativa.
    """
    key = group_key(current)
    if key is None or current.ejecucion is None:
        return ()
    try:
        runs = repository.list_runs()
    except (DataLayerError, InvalidQuery):
        return ()
    choices: dict[int, SeedEvidence] = {}
    active = current.ejecucion
    if active.seed in THESIS_SEEDS:
        choices[active.seed] = SeedEvidence(active.seed, active.id, found)
    for run in sorted(runs, key=lambda r: (len(r.id), r.id), reverse=True):
        if run.seed not in THESIS_SEEDS or run.seed in choices:
            continue
        try:
            status = repository.get_run_status(run.id, size=1)
        except (DataLayerError, InvalidQuery):
            continue
        if group_key(status) != key:
            continue
        choices[run.seed] = SeedEvidence(run.seed, run.id, classes(repository, run.id))
    return tuple(choices.get(seed, SeedEvidence(seed)) for seed in THESIS_SEEDS)


@dataclass(frozen=True, slots=True)
class SeedCell:
    seed: SeedEvidence
    category: models_v2.M3Category | None
    confirmation: M3ClassConfirmation | None


@dataclass(frozen=True, slots=True)
class StabilityRow:
    code: str
    label: str
    cells: tuple[SeedCell, ...]
    confirmed: int
    published: int
    stable: bool


def stability_rows(seeds: tuple[SeedEvidence, ...]) -> list[StabilityRow]:
    """Estado por tipo y semilla. La regla no tiene un umbral sobre el tamaño del efecto."""
    result = []
    for code in SKU_CLASS_ORDER:
        cells: list[SeedCell] = []
        for seed in seeds:
            category = (
                next((r for r in seed.evidence.rows if r.sku_class == code), None)
                if seed.evidence
                else None
            )
            confirmation = seed.evidence.confirmations.get(code) if seed.evidence else None
            cells.append(SeedCell(seed, category, confirmation))
        if not any(cell.category for cell in cells):
            continue
        families = {cell.category.familia_campeona for cell in cells if cell.category is not None}
        confirmed = sum(
            1
            for cell in cells
            if cell.confirmation
            and cell.category
            and cell.confirmation.estado == "confirmado"
            and cell.confirmation.familia_campeona == cell.category.familia_campeona
        )
        published = sum(1 for cell in cells if cell.category is not None)
        result.append(
            StabilityRow(
                code,
                sku_class_label(code),
                tuple(cells),
                confirmed,
                published,
                published == len(THESIS_SEEDS)
                and confirmed == len(THESIS_SEEDS)
                and len(families) == 1,
            )
        )
    return result
