"""La decisión publicada por M3 en la superficie Resultados (contrato v2).

Las vistas «Por SKU» y el detalle de un SKU muestran la selección M2 de una ejecución. El campeón,
el veredicto y su evidencia vienen de una corrida M3 exacta de esa ejecución: la que pide la
consulta (``m3``) o, si no pide ninguna, la PUBLICADA más nueva que resuelve el repositorio. Aquí
solo se resuelve esa corrida y se nombra lo que el repositorio entregó; no se recalcula nada ni se
traducen los cinco veredictos.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field

from pred_platform.app.presentation import SKU_CLASS_ORDER
from pred_platform.contract import models_v2
from pred_platform.contract.errors import DataLayerError, InvalidQuery
from pred_platform.data.m3_query import AGGREGATED_WINDOW, GROUPS
from pred_platform.data.query import MAX_SIZE
from pred_platform.data.repository import DataRepository, M3ReserveBlocks

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
    blocks: M3ReserveBlocks | None = None

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


def _unavailable_evidence(availability: models_v2.Availability) -> M3Problem:
    """Por qué una corrida M3 resuelta no entrega evidencia (sin metadatos o no publicada)."""
    title = (
        "La publicación M3 no tiene sus metadatos de exclusión."
        if availability.reason_code == "metadata_missing"
        else "La corrida M3 no está publicada."
    )
    return _problem(title, availability)


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
    # Se resuelve una vez para la corrida, compartido por todos los SKU de esta decisión.
    try:
        blocks = repository.get_m3_reserve_blocks(resolved.run_id)
    except (DataLayerError, InvalidQuery):
        blocks = None
    late = evidence.late_launches
    eligibility = evidence.eligibility_exclusions
    excluded = [*(late.items if late else []), *(eligibility.items if eligibility else [])]
    return M3Decision(
        run=resolved,
        blocks=blocks,
        verdicts={row.sku: row for row in evidence.items},
        excluded={row.sku: row.causa for row in excluded},
        late_launched=frozenset(row.sku for row in late.items) if late else frozenset(),
        t_star=late.t_star if late else None,
        others=others,
    )


@dataclass(frozen=True, slots=True)
class DmCounts:
    """Cuántos veredictos publicados traen la prueba HLN-DM; M4 los cuenta, no la recalcula."""

    con_prueba: int
    significativas: int
    sin_prueba: dict[str, int]  # dm_causa publicada -> SKU

    @property
    def no_significativas(self) -> int:
        return self.con_prueba - self.significativas


def _dm_counts(rows: Sequence[models_v2.M3Verdict]) -> DmCounts:
    tested = [row for row in rows if row.dm_causa is None]
    causes = Counter(row.dm_causa for row in rows if row.dm_causa is not None)
    return DmCounts(
        con_prueba=len(tested),
        significativas=sum(1 for row in tested if row.dm_significativa),
        sin_prueba=dict(sorted(causes.items())),
    )


@dataclass(frozen=True, slots=True)
class M3Summary:
    """El resumen de la corrida M3 resuelta para una ejecución M2: embudo publicado y prueba DM."""

    run: models_v2.M3Run | None = None
    problem: M3Problem | None = None
    funnel: models_v2.M3Funnel | None = None
    dm: DmCounts | None = None


def summary(repository: DataRepository, m2_run_id: str, wanted: str | None = None) -> M3Summary:
    """Resuelve la corrida M3 de ``m2_run_id`` y lee el embudo que M3 publicó para toda ella."""
    resolved = _resolve(repository, m2_run_id, (wanted or "").strip() or None)
    if isinstance(resolved, M3Problem):
        return M3Summary(problem=resolved)
    evidence = repository.get_m3_evidence(resolved.run_id, size=MAX_SIZE)
    if evidence.availability.status != "available":
        return M3Summary(run=resolved, problem=_unavailable_evidence(evidence.availability))
    rows = list(evidence.items)
    # El conteo DM es de toda la corrida: se leen todas las páginas de veredictos.
    for page in range(2, -(-evidence.page.total // MAX_SIZE) + 1):
        rows += repository.get_m3_evidence(resolved.run_id, page=page, size=MAX_SIZE).items
    return M3Summary(run=resolved, funnel=evidence.funnel, dm=_dm_counts(rows))


@dataclass(frozen=True, slots=True)
class M3Profiles:
    """Los perfiles de error que M3 publicó en su cartera para una métrica (``m3_cartera``)."""

    run: models_v2.M3Run | None = None
    problem: M3Problem | None = None
    metrica: str = "razon_sn"
    rows: list[models_v2.M3PortfolioRow] = field(default_factory=list)


# Las métricas de la cartera (contrato v2, sin sMAPE); r va primero porque es la escala común.
PROFILE_METRICS: dict[str, str] = {
    "razon_sn": "r frente a Seasonal Naive",
    "mae": "MAE",
    "rmse": "RMSE",
    "mase": "MASE",
    "me": "ME",
}


def profiles(
    repository: DataRepository,
    m2_run_id: str,
    wanted: str | None = None,
    metrica: str = "razon_sn",
) -> M3Profiles:
    """Resuelve la corrida M3 de ``m2_run_id`` y lee su cartera para ``metrica``."""
    resolved = _resolve(repository, m2_run_id, (wanted or "").strip() or None)
    if isinstance(resolved, M3Problem):
        return M3Profiles(problem=resolved, metrica=metrica)
    try:
        portfolio = repository.get_m3_portfolio(resolved.run_id, metrica=metrica, size=MAX_SIZE)
    except InvalidQuery as exc:
        problem = M3Problem("La consulta M3 no es válida.", str(exc), "invalid")
        return M3Profiles(problem=problem, metrica=metrica)
    if portfolio.availability.status != "available":
        return M3Profiles(problem=_unavailable_evidence(portfolio.availability), metrica=metrica)
    return M3Profiles(run=resolved, metrica=metrica, rows=portfolio.items)


@dataclass(frozen=True, slots=True)
class M3Classes:
    """El campeón que M3 publicó para cada clase de demanda (``m3_categorias``)."""

    run: models_v2.M3Run | None = None
    problem: M3Problem | None = None
    rows: list[models_v2.M3Category] = field(default_factory=list)


# Las familias que compiten por clase, en el orden de la tabla; la línea base va al final.
CLASS_FAMILIES: tuple[str, ...] = ("classical", "ml", "dl", "foundation", "seasonal_naive")
MOTIVOS: dict[str, str] = {
    "menor_mediana": "Menor mediana de r",
    "empate_por_simplicidad": "Empate resuelto por simplicidad",
    "compuerta_linea_base": "Ninguna familia supera a la línea base",
    "sin_skus_comparables": "Sin SKU comparables",
}


def classes(repository: DataRepository, m2_run_id: str, wanted: str | None = None) -> M3Classes:
    """Resuelve la corrida M3 de ``m2_run_id`` y lee el campeón publicado de cada clase."""
    resolved = _resolve(repository, m2_run_id, (wanted or "").strip() or None)
    if isinstance(resolved, M3Problem):
        return M3Classes(problem=resolved)
    categories = repository.get_m3_categories(resolved.run_id, size=MAX_SIZE)
    if categories.availability.status != "available":
        return M3Classes(problem=_unavailable_evidence(categories.availability))
    rows = sorted(categories.items, key=lambda row: SKU_CLASS_ORDER.index(row.sku_class))
    return M3Classes(run=resolved, rows=rows)


def verdict_counts(row: models_v2.M3Category) -> list[tuple[str, int]]:
    """Los cinco conteos de veredictos de una clase, en el orden de los grupos del contrato."""
    return [(v, getattr(row.conteos, v)) for veredictos in GROUPS.values() for v in veredictos]


@dataclass(frozen=True, slots=True)
class ReserveWindow:
    """Una ventana de la reserva: RMSE del campeón y de la línea base, y r, como los publicó M3."""

    fecha: str
    rmse_campeon: float | None
    rmse_linea_base: float | None
    razon_sn: float | None
    causa: str | None  # por qué r no se calculó en esta ventana, tal como la publicó M3


@dataclass(frozen=True, slots=True)
class M3Reserve:
    """Las ventanas de la reserva de un SKU y cuántas quedaron sin r, por causa publicada."""

    windows: list[ReserveWindow]
    sin_r: dict[str, int]


def _by_window(
    repository: DataRepository, run_id: str, sku: str, candidato_id: str, metrica: str
) -> dict[str, models_v2.M3Metric]:
    """Las métricas por ventana de un candidato, de todas las páginas; sin la fila agregada."""
    first = repository.get_m3_metrics(
        run_id, sku=sku, candidato_id=candidato_id, metrica=metrica, size=MAX_SIZE
    )
    rows = list(first.items)
    for page in range(2, -(-first.page.total // MAX_SIZE) + 1):
        rows += repository.get_m3_metrics(
            run_id, sku=sku, candidato_id=candidato_id, metrica=metrica, page=page, size=MAX_SIZE
        ).items
    return {row.ventana: row for row in rows if row.ventana != AGGREGATED_WINDOW}


def reserve_windows(
    repository: DataRepository, run_id: str, verdict: models_v2.M3Verdict
) -> M3Reserve:
    """Las ventanas de la reserva del campeón publicado frente a Seasonal Naive, por fecha."""
    champion = verdict.candidato_campeon or ""
    baseline = next(
        m.candidato_id for m in verdict.modelos_evaluados if m.familia == "seasonal_naive"
    )
    rmse = _by_window(repository, run_id, verdict.sku, champion, "rmse")
    rmse_base = _by_window(repository, run_id, verdict.sku, baseline, "rmse")
    ratio = _by_window(repository, run_id, verdict.sku, champion, "razon_sn")
    windows = [
        ReserveWindow(
            fecha,
            rmse[fecha].valor if fecha in rmse else None,
            rmse_base[fecha].valor if fecha in rmse_base else None,
            ratio[fecha].valor if fecha in ratio else None,
            ratio[fecha].causa if fecha in ratio else None,
        )
        for fecha in sorted(rmse.keys() | rmse_base.keys() | ratio.keys())
    ]
    causes = Counter(w.causa for w in windows if w.causa is not None)
    return M3Reserve(windows, dict(sorted(causes.items())))


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


def funnel_rows(funnel: models_v2.M3Funnel) -> list[tuple[str, str, int]]:
    """(grupo, veredicto, SKU) de los cinco veredictos publicados, en los grupos del contrato."""
    labels = {"evaluados": "Evaluados", "no_evaluados": "No evaluados"}
    return [
        (labels[group], veredicto, getattr(funnel.conteos, veredicto))
        for group, veredictos in GROUPS.items()
        for veredicto in veredictos
    ]


def ranking(row: models_v2.M3Verdict) -> list[models_v2.EvaluatedModel]:
    """Los modelos evaluados por M3, ordenados por su r frente a Seasonal Naive; sin r, al final."""
    return sorted(
        row.modelos_evaluados,
        key=lambda model: (model.razon_sn is None, model.razon_sn or 0.0),
    )


def _decimal(value: float | None, digits: int = 3) -> str:
    return "—" if value is None else f"{value:.{digits}f}".replace(".", ",")
