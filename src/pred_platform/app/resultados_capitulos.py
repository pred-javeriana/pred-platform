"""Las respuestas de Resultados, leídas de la selección y de las pruebas publicadas.

Solo resume filas para la lectura. No elige campeonas, ajusta p ni cambia veredictos.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
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
    product_tests,
)
from pred_platform.contract import models_v2
from pred_platform.contract.errors import DataLayerError, InvalidQuery
from pred_platform.contract.models import RunStatus
from pred_platform.data.m3_confirmation import M3ClassConfirmation
from pred_platform.data.query import MAX_SIZE
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
    # Por qué no se pudieron leer las pruebas por producto: la confirmación, o los veredictos de
    # una evaluación sin ella. Sin pruebas legibles no se afirma que no hay pruebas ni se cuentan.
    tests_unread: M3Problem | None = None

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


def answers(
    found: M3Classes,
    verdicts: Sequence[models_v2.M3Verdict] = (),
    verdicts_problem: M3Problem | None = None,
) -> Answers:
    """Una familia solo se nombra como ganadora de todos los tipos si las filas lo dicen.

    ``verdicts_problem`` es por qué no se pudieron leer los veredictos publicados: una
    evaluación sin confirmación por clase cuenta sus pruebas en ellos.
    """
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
    tests = product_tests(found, verdicts)
    tested = sum(row.n_probados for row in tests)
    better = sum(row.n_mejores for row in tests)
    worse = sum(row.n_peores for row in tests)
    selection = _selection_text(found, verdicts)
    # Filas guardadas que no se pudieron leer: las demás se cuentan, sin completar las que faltan.
    unread_tests = [sku_class_label(code) for code in found.rejected_significance]
    partial = bool(found.rejections)
    tests_unread = unread or (
        verdicts_problem if found.confirmation is None and found.run is not None else None
    )
    if tests_unread or (unread_tests and not tested):
        verification = "No se pudieron leer las pruebas por producto de esta evaluación."
    elif not tested:
        verification = "Esta evaluación no publicó pruebas por producto."
    else:
        verification = (
            f"La mejora se verifica en {format_count(better)} de {format_count(tested)} "
            f"productos probados ({percent_text(better / tested)})."
        )
        if unread_tests:
            verification += (
                f" No se pudieron leer las pruebas de {', '.join(unread_tests).lower()}."
            )
    effects = [r.efecto_mediana_r for r in confirmations.values() if r.efecto_mediana_r is not None]
    effect_range = (
        "No se pudo leer"
        if unread or (not effects and found.rejected_confirmations)
        else _effect_range(effects)
    )
    ranked = bool(types) and all(r.motivo in _RANKED and r.skus_comparables for r in found.rows)
    supported = bool(ranked and confirmed == types and not unread)
    # Lo que no se pudo leer no se presenta como «no concluyente».
    concluded = bool(types) and not unread and not partial
    return Answers(
        family if ranked else None,
        confirmed,
        types,
        tested,
        better,
        worse,
        selection,
        verification,
        "ok" if supported else "warn" if concluded else "neutral",
        "Respaldada en esta corrida"
        if supported
        else "No concluyente"
        if concluded
        else "Sin responder",
        effect_range,
        unread,
        tests_unread,
    )


@dataclass(frozen=True, slots=True)
class SeedEvidence:
    seed: int
    run_id: str | None = None
    evidence: M3Classes | None = None

    @property
    def published(self) -> bool:
        return bool(
            self.evidence
            and self.evidence.run
            and self.evidence.run.estado == "PUBLICADA"
            and not self.evidence.problem
        )

    @property
    def unread(self) -> M3Problem | None:
        """Por qué no se pudo leer su confirmación guardada, si la evaluación sí se leyó.

        Una fila de confirmación rechazada también cuenta: ese tipo no se lee como ausente.
        """
        if self.evidence is None:
            return None
        problem = _unread(self.evidence)
        rejected = self.evidence.rejected_confirmations
        if problem is not None or not rejected:
            return problem
        return M3Problem(
            "No se pudo leer la confirmación guardada de "
            + ", ".join(sku_class_label(code).lower() for code in rejected)
            + ".",
            "; ".join(f"{sku_class_label(code)}: {detail}" for code, detail in rejected.items()),
            "platform.contract_invalid",
        )


def seed_evidence(
    repository: DataRepository,
    current: RunStatus,
    found: M3Classes,
    statuses: Mapping[str, RunStatus] | None = None,
) -> tuple[SeedEvidence, ...]:
    """Las semillas 0, 1 y 2 con los mismos datos y ajustes que la corrida abierta.

    De una semilla repetida se muestra la corrida publicada más nueva que el grupo final acepta
    junto a la abierta (mismos datos, identidad, protocolo y productos). Una ejecución sin
    evaluación publicada no oculta la publicada de su semilla. La confirmación ausente no se
    convierte en confirmación negativa. ``statuses`` son los estados (``size=1``) que la página
    ya leyó, por número de ejecución: no se leen otra vez.
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
    if reference is None:
        return tuple(choices.get(seed, SeedEvidence(seed)) for seed in THESIS_SEEDS)
    # Every published evaluation of every other execution of the group is a candidate, newest
    # execution first and newest evaluation first, because one execution can publish several.
    offered: list[tuple[int, str, models_v2.M3Run]] = []
    for run in sorted(runs, key=lambda r: (len(r.id), r.id), reverse=True):
        if run.seed not in THESIS_SEEDS or run.seed == active.seed:
            continue
        try:
            status = (statuses or {}).get(run.id) or repository.get_run_status(run.id, size=1)
            if group_key(status) != key:
                continue
            published = repository.list_m3_runs(
                run_id_m2=run.id, estado="PUBLICADA", size=MAX_SIZE
            ).items
        except (DataLayerError, InvalidQuery):
            continue
        offered += [(run.seed, run.id, item) for item in published]
    try:
        accepted = repository.comparable_m3_runs(reference, [m3.run_id for _, _, m3 in offered])
    except EvidenceInvalid:
        accepted = frozenset()
    for seed, execution, m3_run in offered:
        if seed in choices or m3_run.run_id not in accepted:
            continue
        # La corrida M3 sale de la lista ya leída de esa ejecución: no se resuelve otra vez.
        evidence = classes(repository, execution, m3_run.run_id, run=m3_run)
        if evidence.run is not None and evidence.run.estado == "PUBLICADA" and not evidence.problem:
            choices[seed] = SeedEvidence(seed, execution, evidence)
    return tuple(choices.get(seed, SeedEvidence(seed)) for seed in THESIS_SEEDS)


@dataclass(frozen=True, slots=True)
class SeedCell:
    seed: SeedEvidence
    category: models_v2.M3Category | None
    confirmation: M3ClassConfirmation | None

    @property
    def unread(self) -> bool:
        """Si la confirmación de este tipo en esta semilla no se pudo leer."""
        evidence = self.seed.evidence
        if evidence is None or self.category is None:
            return False
        return _unread(evidence) is not None or (
            self.category.sku_class in evidence.rejected_confirmations
        )


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
        published = sum(1 for cell in cells if cell.seed.published)
        unread = sum(1 for cell in cells if cell.unread)
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
