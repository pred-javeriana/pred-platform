"""La decisión publicada por M3 en la superficie Resultados (contrato v2).

Las vistas «Por SKU» y el detalle de un SKU muestran la selección M2 de una ejecución. El campeón,
el veredicto y su evidencia vienen de una corrida M3 exacta de esa ejecución: la que pide la
consulta (``m3``) o, si no pide ninguna, la PUBLICADA más nueva que resuelve el repositorio. Aquí
solo se resuelve esa corrida y se nombra lo que el repositorio entregó; no se recalcula nada ni se
traducen los cinco veredictos.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from pred_platform.contract import models_v2
from pred_platform.contract.errors import DataLayerError, InvalidQuery
from pred_platform.data.m3_query import GROUPS
from pred_platform.data.repository import DataRepository

# Texto literal del contrato v2 (sección 2) para varias corridas PUBLICADA de una identidad.
_DUPLICATES = "Hay varias corridas PUBLICADA con esta identidad. Se muestra {run_id}."


@dataclass(frozen=True, slots=True)
class M3Problem:
    """Por qué no hay veredictos M3 que mostrar, con la causa que dio el repositorio."""

    title: str
    detail: str
    code: str


@dataclass(frozen=True, slots=True)
class M3Decision:
    """La corrida M3 resuelta para una ejecución M2 y los veredictos de los SKU pedidos."""

    run: models_v2.M3Run | None = None
    problem: M3Problem | None = None
    verdicts: dict[str, models_v2.M3Verdict] = field(default_factory=dict)
    # SKU excluido antes de M3 -> causa publicada: lanzado después del corte, o excluido de la
    # competencia en M2 (por ejemplo, la regla de dos familias).
    excluded: dict[str, str] = field(default_factory=dict)
    # Los SKU lanzados después del corte t*, la única exclusión que el corte explica.
    late_launched: frozenset[str] = frozenset()
    t_star: str | None = None
    others: tuple[str, ...] = ()

    @property
    def notice(self) -> str | None:
        """El aviso visible cuando varias corridas PUBLICADA comparten la identidad."""
        if self.run is None or self.run.aviso is None:
            return None
        return _DUPLICATES.format(run_id=self.run.run_id)

    @property
    def notice_parts(self) -> tuple[str, str, str] | None:
        """El mismo aviso partido en (antes, run_id, después), para no cortar el identificador."""
        if self.run is None or self.run.aviso is None:
            return None
        before, after = _DUPLICATES.split("{run_id}")
        return before, self.run.run_id, after


def _problem(title: str, availability: models_v2.Availability) -> M3Problem:
    return M3Problem(
        title, availability.detail or "No hay evidencia M3.", availability.reason_code or ""
    )


def _resolve(
    repository: DataRepository, m2_run_id: str, wanted: str | None
) -> models_v2.M3Run | M3Problem:
    runs = repository.list_m3_runs(run_id_m2=m2_run_id)
    if wanted:
        status = repository.get_m3_run_status(wanted)
        mine = {run.run_id_m2 for run in runs.items}
        if status.run is None or status.run.run_id_m2 not in mine:
            return M3Problem(
                "La corrida M3 pedida no corresponde a esta ejecución.",
                f"La corrida M3 {wanted} no es una evaluación de la ejecución #{m2_run_id}.",
                "no_data",
            )
        return status.run
    if runs.selected_run_id is None:
        if runs.availability.status != "available":
            return _problem("Esta ejecución todavía no tiene evaluación M3.", runs.availability)
        newest = runs.items[0]
        return M3Problem(
            "Esta ejecución todavía no tiene una corrida M3 PUBLICADA.",
            f"La corrida M3 más reciente, {newest.run_id}, está {newest.estado}.",
            "not_published",
        )
    # Resolved over every run of the execution, not only the first page of the list.
    status = repository.get_m3_run_status(runs.selected_run_id)
    return status.run or _problem("La corrida M3 ya no está disponible.", status.availability)


def decision(
    repository: DataRepository,
    m2_run_id: str | None,
    skus: Sequence[str],
    wanted: str | None = None,
) -> M3Decision:
    """Resuelve la corrida M3 de ``m2_run_id`` y lee los veredictos de ``skus`` en ella."""
    if m2_run_id is None:
        return M3Decision()
    try:
        resolved = _resolve(repository, m2_run_id, (wanted or "").strip() or None)
        if isinstance(resolved, M3Problem):
            return M3Decision(problem=resolved)
        others = tuple(r for r in resolved.publicadas_misma_identidad if r != resolved.run_id)
        evidence = repository.get_m3_evidence(
            resolved.run_id, skus=list(skus), size=max(1, min(len(skus), 500))
        )
    except InvalidQuery as exc:
        return M3Decision(problem=M3Problem("La consulta M3 no es válida.", str(exc), "invalid"))
    except DataLayerError as exc:
        return M3Decision(
            problem=M3Problem("No fue posible leer la evidencia M3.", str(exc), exc.code)
        )
    if evidence.availability.status != "available":
        title = (
            "La publicación M3 no tiene sus metadatos de exclusión."
            if evidence.availability.reason_code == "metadata_missing"
            else "La corrida M3 no está publicada."
        )
        return M3Decision(
            run=resolved, problem=_problem(title, evidence.availability), others=others
        )
    late = evidence.late_launches
    eligibility = evidence.eligibility_exclusions
    excluded = [*(late.items if late else []), *(eligibility.items if eligibility else [])]
    return M3Decision(
        run=resolved,
        verdicts={row.sku: row for row in evidence.items},
        excluded={row.sku: row.causa for row in excluded},
        late_launched=frozenset(row.sku for row in late.items) if late else frozenset(),
        t_star=late.t_star if late else None,
        others=others,
    )


# ----------------------------------------------------------------------------- textos
def origin_label(run: models_v2.M3Run | None) -> str:
    """Los dos bits publicados: real declarado, sintético o aumentado, u origen desconocido."""
    if run is None:
        return "no informado en esta vista"
    if run.datos_sinteticos:
        return "sintéticos o aumentados (declarado)"
    if run.origen_declarado:
        return "reales (declarado)"
    return "desconocido: no se declaró si los datos son reales"


def verdict_group(veredicto: str) -> str:
    return "Evaluado" if veredicto in GROUPS["evaluados"] else "No evaluado"


def significance_items(row: models_v2.M3Verdict) -> list[tuple[str, str]]:
    """La columna Significancia (HLN-DM y BH), separada del veredicto; la causa si no hay prueba."""
    if row.dm_causa is not None:
        return [
            ("Prueba HLN-DM", f"sin prueba: {row.dm_causa}"),
            ("Ventanas de la prueba", str(row.dm_n_ventanas)),
            ("Horizonte", str(row.dm_horizonte)),
        ]
    return [
        ("Estadístico HLN-DM", _decimal(row.dm_estadistico)),
        ("p-valor", _decimal(row.dm_p_valor)),
        ("p ajustado (BH)", _decimal(row.dm_p_ajustado)),
        ("α", _decimal(row.dm_alfa)),
        ("Significativa", "sí" if row.dm_significativa else "no"),
        ("Ventanas de la prueba", str(row.dm_n_ventanas)),
        ("Horizonte", str(row.dm_horizonte)),
    ]


def _decimal(value: float | None, digits: int = 3) -> str:
    return "—" if value is None else f"{value:.{digits}f}".replace(".", ",")
