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
from pred_platform.app.resultados_m3 import (
    M3Classes,
    M3Problem,
    classes,
    percent_text,
)
from pred_platform.contract import models_v2
from pred_platform.contract.errors import DataLayerError, InvalidQuery
from pred_platform.contract.models import RunStatus
from pred_platform.data.m3_confirmation import M3ClassConfirmation
from pred_platform.data.repository import DataRepository
from pred_platform.evidence.models import EvidenceInvalid

CHAPTERS = (
    ("resumen", "Resumen", ""),
    ("p1", "¿Qué familia gana?", "1"),
    ("p2", "¿Se verifica producto a producto?", "2"),
    ("estabilidad", "¿Se sostiene con otra semilla?", "3"),
    ("productos", "Productos", ""),
    ("exportar", "Exportar y reproducir", ""),
)
LEGACY_CHAPTERS = {
    "clase": "p1",
    "errores": "p1",
    "prueba": "p2",
    "sku": "productos",
    "consulta": "productos",
}
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
    # Por qué no se pudo leer la confirmación guardada; con ella no hay ausencia que afirmar.
    unread: M3Problem | None = None

    @property
    def unchanged(self) -> int:
        return self.tested - self.better - self.worse


def _unread(found: M3Classes) -> M3Problem | None:
    """Por qué falló la lectura de la confirmación guardada; ``None`` si se leyó o no existe."""
    return found.confirmation_problem if found.confirmation is None else None


# Una categoría solo "pronostica mejor" si se eligió entre productos comparables.
_RANKED = ("menor_mediana", "empate_por_simplicidad")


def _types(count: int) -> str:
    return f"{format_count(count)} {'tipo' if count == 1 else 'tipos'} de demanda"


def _selection_text(found: M3Classes, verdicts: Sequence[models_v2.M3Verdict]) -> str:
    """Lo que la selección publicada dice de cada tipo, sin llamar «mejor» a un valor por defecto.

    Sin productos comparables no hubo elección: la familia publicada es la de respaldo. Cuando
    ninguna familia supera a la línea base, la línea base conserva el tipo.
    """
    rows = found.rows
    if not rows:
        return "Esta evaluación no publicó la selección por tipo de demanda."
    chosen = [r for r in rows if r.motivo in _RANKED and r.skus_comparables]
    gated = [r for r in rows if r.motivo == "compuerta_linea_base" and r.skus_comparables]
    open_types = [r for r in rows if r not in chosen and r not in gated]
    families = {row.familia_campeona for row in chosen}
    if len(chosen) == len(rows) and len(families) == 1:
        family = next(iter(families))
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
        if len(names) == 1 and model_types == {row.sku_class for row in rows}:
            label += f" ({model_label(next(iter(names)))})"
        return f"{label} pronostica mejor los {format_count(len(rows))} tipos de demanda."
    parts = []
    if chosen and len(families) == 1:
        parts.append(
            f"{family_label(next(iter(families)))} pronostica mejor {_types(len(chosen))}."
        )
    elif chosen:
        parts.append(
            "La familia ganadora cambia según el tipo de demanda"
            + ("" if len(chosen) == len(rows) else f" en {_types(len(chosen))}")
            + "."
        )
    if gated:
        parts.append(f"En {_types(len(gated))} ninguna familia supera a la línea base.")
    if open_types:
        parts.append(
            f"En {_types(len(open_types))} no hubo productos comparables para elegir una "
            "familia, y la que se muestra es la de respaldo."
        )
    return " ".join(parts)


def _effect_range(effects: Sequence[float]) -> str:
    if not effects:
        return "Sin efecto publicado"
    low, high = min(effects), max(effects)
    if high == math.inf:
        if low == math.inf:
            return "Sin límite (r = ∞)"
        if low > 1:
            return f"{format_decimal(100 * (low - 1), 1)} % más a sin límite"
        return f"r = {format_decimal(low, 3)} a ∞"
    if high < 1:
        return (
            f"{format_decimal(100 * (1 - high), 1)} a {format_decimal(100 * (1 - low), 1)} % menos"
        )
    if low > 1:
        return f"{format_decimal(100 * (low - 1), 1)} a {format_decimal(100 * (high - 1), 1)} % más"
    return f"r = {format_decimal(low, 3)} a {format_decimal(high, 3)}"


def answers(found: M3Classes, verdicts: Sequence[models_v2.M3Verdict] = ()) -> Answers:
    """Una familia solo se nombra como ganadora de todos los tipos si las filas lo dicen."""
    families = {row.familia_campeona for row in found.rows}
    family = next(iter(families)) if len(families) == 1 else None
    confirmations = found.confirmations
    unread = _unread(found)
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
    selection = _selection_text(found, verdicts)
    if unread:
        verification = "No se pudieron leer las pruebas por producto de esta evaluación."
    elif not tested:
        verification = "Esta evaluación no publicó pruebas por producto."
    else:
        verification = (
            f"La mejora se verifica en {format_count(better)} de {format_count(tested)} "
            f"productos probados ({percent_text(better / tested)})."
        )
    effects = [r.efecto_mediana_r for r in confirmations.values() if r.efecto_mediana_r is not None]
    effect_range = "No se pudo leer" if unread else _effect_range(effects)
    ranked = bool(types) and all(r.motivo in _RANKED and r.skus_comparables for r in found.rows)
    supported = bool(ranked and confirmed == types and not unread)
    return Answers(
        family if ranked else None,
        confirmed,
        types,
        tested,
        better,
        worse,
        selection,
        verification,
        "ok" if supported else "warn" if types and not unread else "neutral",
        "Respaldada en esta corrida"
        if supported
        else "No concluyente"
        if types and not unread
        else "Sin responder",
        effect_range,
        unread,
    )


@dataclass(frozen=True, slots=True)
class SeedEvidence:
    seed: int
    run_id: str | None = None
    evidence: M3Classes | None = None

    @property
    def unread(self) -> M3Problem | None:
        """Por qué no se pudo leer su confirmación guardada, si la evaluación sí se leyó."""
        return _unread(self.evidence) if self.evidence else None


def seed_evidence(
    repository: DataRepository, current: RunStatus, found: M3Classes
) -> tuple[SeedEvidence, ...]:
    """Las semillas 0, 1 y 2 con los mismos datos y ajustes que la corrida abierta.

    De una semilla repetida se muestra la corrida publicada más nueva que el grupo final acepta
    junto a la abierta (mismos datos, identidad, protocolo y productos). Una ejecución sin
    evaluación publicada no oculta la publicada de su semilla. La confirmación ausente no se
    convierte en confirmación negativa.
    """
    key = group_key(current)
    if key is None or current.ejecucion is None:
        return ()
    try:
        runs = repository.list_runs()
    except (DataLayerError, InvalidQuery):
        return ()
    active = current.ejecucion
    choices: dict[int, SeedEvidence] = {}
    if active.seed in THESIS_SEEDS:
        choices[active.seed] = SeedEvidence(active.seed, active.id, found)
    reference = found.run.run_id if found.run and found.run.estado == "PUBLICADA" else None
    candidates: list[SeedEvidence] = []
    for run in sorted(runs, key=lambda r: (len(r.id), r.id), reverse=True):
        if reference is None or run.seed not in THESIS_SEEDS or run.seed == active.seed:
            continue
        try:
            status = repository.get_run_status(run.id, size=1)
        except (DataLayerError, InvalidQuery):
            continue
        if group_key(status) != key:
            continue
        evidence = classes(repository, run.id)
        if evidence.run is None or evidence.run.estado != "PUBLICADA" or evidence.problem:
            continue
        candidates.append(SeedEvidence(run.seed, run.id, evidence))
    if candidates and reference is not None:
        try:
            accepted = repository.comparable_m3_runs(
                reference,
                [c.evidence.run.run_id for c in candidates if c.evidence and c.evidence.run],
            )
        except EvidenceInvalid:
            accepted = frozenset()
        for candidate in candidates:
            run = candidate.evidence.run if candidate.evidence else None
            if run and run.run_id in accepted:
                choices.setdefault(candidate.seed, candidate)
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
    # Semillas cuya confirmación guardada no se pudo leer: no cuentan como ausentes.
    unread: int = 0


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
        unread = sum(1 for cell in cells if cell.category is not None and cell.seed.unread)
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
                unread,
            )
        )
    return result
