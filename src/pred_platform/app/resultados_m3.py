"""La decisión publicada por M3 en la superficie Resultados (contrato v2).

La pestaña SKU y el detalle de un SKU muestran la selección M2 de una ejecución. El campeón, el
veredicto y su evidencia vienen de una corrida M3 exacta de esa ejecución: la que pide la
consulta (``m3``) o, si no pide ninguna, la PUBLICADA más nueva que resuelve el repositorio. Aquí
solo se resuelve esa corrida y se nombra lo que el repositorio entregó; no se recalcula nada ni se
traducen los cinco veredictos.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from pred_platform import mensajes
from pred_platform.app.consulta import ConsultaQuery
from pred_platform.app.presentation import SKU_CLASS_ORDER, format_count
from pred_platform.app.seleccion import family_label
from pred_platform.contract import models_v2
from pred_platform.contract.errors import DataLayerError, InvalidQuery
from pred_platform.data.m3_confirmation import (
    M3ClassConfirmation,
    M3ClassSignificance,
    M3Comparison,
    M3Confirmation,
)
from pred_platform.data.m3_documents import NO_RUN
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


def _read_problem(exc: InvalidQuery | DataLayerError) -> M3Problem:
    """El problema visible de una lectura M3 que falló: consulta inválida o error de datos."""
    if isinstance(exc, InvalidQuery):
        return M3Problem("La consulta M3 no es válida.", str(exc), "invalid")
    return M3Problem("No fue posible leer la evidencia M3.", str(exc), exc.code)


def _resolve(
    repository: DataRepository, m2_run_id: str, wanted: str | None
) -> models_v2.M3Run | M3Problem:
    runs = repository.list_m3_runs(run_id_m2=m2_run_id)
    if wanted:
        run = repository.get_m3_run(wanted)
        mine = {item.run_id_m2 for item in runs.items}
        if run is None or run.run_id_m2 not in mine:
            return M3Problem(
                "La corrida M3 pedida no corresponde a esta ejecución.",
                f"La corrida M3 {wanted} no es una evaluación de la ejecución #{m2_run_id}.",
                "no_data",
            )
        return run
    if runs.selected_run_id is None:
        if runs.availability.status != "available":
            # El listado dice que ninguna corrida coincide con su consulta; aquí se dice cuándo
            # aparece la evaluación.
            return M3Problem(
                "Esta ejecución todavía no tiene evaluación M3.",
                "M3 publica la evaluación al final de la ejecución, en la etapa L4; entonces "
                "aparecen el campeón y el veredicto de cada SKU.",
                runs.availability.reason_code or "",
            )
        newest = runs.items[0]
        return M3Problem(
            "Esta ejecución todavía no tiene una corrida M3 PUBLICADA.",
            f"La corrida M3 más reciente, {newest.run_id}, está {newest.estado}.",
            "not_published",
        )
    # Resolved over every run of the execution, not only the first page of the list. Only the
    # run's metadata: its progress would count every unit of the run on each page.
    run = repository.get_m3_run(runs.selected_run_id)
    if run is None:
        return M3Problem("La corrida M3 ya no está disponible.", NO_RUN, "no_data")
    return run


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
    except (InvalidQuery, DataLayerError) as exc:
        return M3Decision(problem=_read_problem(exc))
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


# Las familias que pueden ser la campeona publicada de un SKU, en el orden de las tablas; la línea
# base gana cuando ninguna familia la supera.
CHAMPION_FAMILIES: tuple[str, ...] = ("classical", "ml", "dl", "foundation", "seasonal_naive")


@dataclass(frozen=True, slots=True)
class ChampionRow:
    """Los SKU con veredicto cuya campeona publicada es ``familia``, tal como los contó M3."""

    familia: str
    n_skus: int
    n_evaluados: int
    n_no_evaluados: int
    # Fracción de los SKU con veredicto (``ChampionDistribution.published_total``); ``None`` si
    # no hay ninguno. Es presentación de dos cifras publicadas, no un conteo nuevo.
    share: float | None


@dataclass(frozen=True, slots=True)
class ClassChampions:
    """Cuántos SKU de una clase tienen a cada familia como campeona publicada."""

    sku_class: str
    counts: dict[str, int]
    total: int


@dataclass(frozen=True, slots=True)
class ChampionDistribution:
    """La distribución de campeones por familia de una corrida M3 (``m3_campeones``).

    ``published_total`` es el denominador que M3 publicó en el embudo (SKU con veredicto). Las
    filas de cartera deben sumarlo: si no, ``consistent`` es falso y la vista lo dice en vez de
    ajustar una cifra.
    """

    portfolio: tuple[ChampionRow, ...]
    classes: tuple[ClassChampions, ...]
    published_total: int
    counted_total: int

    @property
    def consistent(self) -> bool:
        return self.counted_total == self.published_total


def champion_distribution(
    items: Sequence[models_v2.M3ChampionCount], published_total: int
) -> ChampionDistribution:
    """Ordena las filas publicadas por familia y por clase; no las recalcula ni las completa.

    Las familias que M3 no publicó para un grupo salen con 0, para que las tablas sean
    comparables entre corridas. ``sku_class`` nulo es la cartera completa.
    """
    portfolio = {row.familia: row for row in items if row.sku_class is None}
    rows = tuple(
        ChampionRow(
            familia=familia,
            n_skus=(found.n_skus if (found := portfolio.get(familia)) else 0),
            n_evaluados=(found.n_evaluados if found else 0),
            n_no_evaluados=(found.n_no_evaluados if found else 0),
            share=((found.n_skus if found else 0) / published_total if published_total else None),
        )
        for familia in CHAMPION_FAMILIES
    )
    by_class: dict[str, dict[str, int]] = {}
    for row in items:
        if row.sku_class is not None:
            by_class.setdefault(row.sku_class, {})[row.familia] = row.n_skus
    classes = tuple(
        ClassChampions(
            sku_class=sku_class,
            counts={familia: by_class[sku_class].get(familia, 0) for familia in CHAMPION_FAMILIES},
            total=sum(by_class[sku_class].values()),
        )
        for sku_class in SKU_CLASS_ORDER
        if sku_class in by_class
    )
    return ChampionDistribution(
        portfolio=rows,
        classes=classes,
        published_total=published_total,
        counted_total=sum(row.n_skus for row in rows),
    )


def champion_share_text(row: ChampionRow) -> str:
    """El valor de una familia como se lee en la tabla y en el gráfico: ``5 SKU · 41,7 %``."""
    count = f"{format_count(row.n_skus)} SKU"
    return count if row.share is None else f"{count} · {percent_text(row.share)}"


def champion_bars(distribution: ChampionDistribution) -> list[tuple[str, float | None, str]]:
    """(familia, fracción, valor) de cada familia, en el orden de la tabla, para el gráfico."""
    return [
        (family_label(row.familia), row.share, champion_share_text(row))
        for row in distribution.portfolio
    ]


@dataclass(frozen=True, slots=True)
class M3Summary:
    """El resumen de la corrida M3 resuelta para una ejecución M2: embudo publicado y prueba DM.

    ``champions`` es la distribución de campeones por familia; si su lectura falló, el resto del
    resumen sigue y la causa queda en ``champions_problem``. ``eligibility`` cuenta, por código,
    las exclusiones de M2 que M3 publicó (``None`` si la publicación no las informa).
    """

    run: models_v2.M3Run | None = None
    problem: M3Problem | None = None
    funnel: models_v2.M3Funnel | None = None
    dm: DmCounts | None = None
    champions: ChampionDistribution | None = None
    champions_problem: M3Problem | None = None
    eligibility: list[tuple[str, str, int]] | None = None


def _champions(
    repository: DataRepository, run_id: str, funnel: models_v2.M3Funnel | None
) -> tuple[ChampionDistribution | None, M3Problem | None]:
    """La distribución de campeones de la corrida, o por qué no se pudo leer."""
    if funnel is None:
        return None, None
    try:
        read = repository.get_m3_champions(run_id, size=MAX_SIZE)
    except (InvalidQuery, DataLayerError) as exc:
        return None, _read_problem(exc)
    if read.availability.status != "available":
        return None, _unavailable_evidence(read.availability)
    if read.page.total != len(read.items):
        return None, M3Problem(
            "La distribución de campeones llegó incompleta.",
            f"M3 publicó {read.page.total} filas y se leyeron {len(read.items)}.",
            "incomplete",
        )
    return champion_distribution(read.items, funnel.tras_exclusiones), None


def summary(repository: DataRepository, m2_run_id: str, wanted: str | None = None) -> M3Summary:
    """Resuelve la corrida M3 de ``m2_run_id`` y lee el embudo que M3 publicó para toda ella."""
    try:
        return _summary(repository, m2_run_id, wanted)
    except (InvalidQuery, DataLayerError) as exc:
        return M3Summary(problem=_read_problem(exc))


def _summary(repository: DataRepository, m2_run_id: str, wanted: str | None) -> M3Summary:
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
    champions, champions_problem = _champions(repository, resolved.run_id, evidence.funnel)
    return M3Summary(
        run=resolved,
        funnel=evidence.funnel,
        dm=_dm_counts(rows),
        champions=champions,
        champions_problem=champions_problem,
        eligibility=eligibility_rows(evidence.eligibility_exclusions),
    )


def eligibility_rows(
    exclusions: models_v2.EligibilityExclusions | None,
) -> list[tuple[str, str, int]] | None:
    """(código, motivo, SKU) de las exclusiones de M2 publicadas, una fila por código y la causa
    más frecuente primero.

    Cuenta las filas publicadas de ``m3_exclusiones``: la historia insuficiente antes de L2 y la
    regla de dos familias después de L2 son causas distintas y se leen por separado.
    """
    if exclusions is None:
        return None
    counts = Counter(item.codigo for item in exclusions.items)
    return [
        (code, mensajes.titulo(code) if code in mensajes.CATALOGO else code, count)
        for code, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    ]


@dataclass(frozen=True, slots=True)
class ExecutionChoice:
    """Una ejecución con una corrida M3 PUBLICADA, para elegir qué resultados ver."""

    ejecucion_id: str
    estado: str
    iniciada_en: datetime | None
    m3_run_id: str


def published_executions(repository: DataRepository, limit: int = 20) -> list[ExecutionChoice]:
    """Las ejecuciones con al menos una corrida M3 PUBLICADA, de la más nueva a la más vieja.

    Resultados abre por defecto la primera: la ejecución más reciente puede seguir en curso y
    todavía no tener evidencia que mostrar. Una lectura que falla deja la lista vacía.
    """
    try:
        runs = repository.list_m3_runs(estado="PUBLICADA", size=MAX_SIZE)
    except (InvalidQuery, DataLayerError):
        return []
    found: dict[str, ExecutionChoice] = {}
    for m3_run in runs.items:  # la más nueva primero: queda la corrida M3 más reciente
        m2_run_id = m3_run.run_id_m2
        if m2_run_id is None or m2_run_id in found:
            continue
        try:
            status = repository.get_run_status(m2_run_id, size=1)
        except (InvalidQuery, DataLayerError):
            continue
        if status.ejecucion is not None:
            run = status.ejecucion
            found[m2_run_id] = ExecutionChoice(run.id, run.estado, run.iniciada_en, m3_run.run_id)
    choices = sorted(
        {choice.ejecucion_id: choice for choice in found.values()}.values(),
        key=lambda choice: (len(choice.ejecucion_id), choice.ejecucion_id),
        reverse=True,
    )
    return choices[:limit]


@dataclass(frozen=True, slots=True)
class M3Consulta:
    """Los veredictos de la corrida M3 resuelta: la página pedida y el embudo completo."""

    run: models_v2.M3Run | None = None
    problem: M3Problem | None = None
    evidence: models_v2.M3Evidence | None = None


def consulta(repository: DataRepository, m2_run_id: str, query: ConsultaQuery) -> M3Consulta:
    """Resuelve la corrida M3 de ``m2_run_id`` y delega el filtro, el orden y la página a M3.

    Un filtro desconocido lo rechaza el repositorio: el aviso es visible y la corrida resuelta se
    conserva para que el panel lateral y los enlaces sigan mostrándola.
    """
    try:
        resolved = _resolve(repository, m2_run_id, query.m3)
    except (InvalidQuery, DataLayerError) as exc:
        return M3Consulta(problem=_read_problem(exc))
    if isinstance(resolved, M3Problem):
        return M3Consulta(problem=resolved)
    try:
        evidence = repository.get_m3_evidence(resolved.run_id, **query.as_kwargs())
    except (InvalidQuery, DataLayerError) as exc:
        return M3Consulta(run=resolved, problem=_read_problem(exc))
    if evidence.availability.status != "available":
        return M3Consulta(run=resolved, problem=_unavailable_evidence(evidence.availability))
    return M3Consulta(run=resolved, evidence=evidence)


@dataclass(frozen=True, slots=True)
class M3Export:
    """Todo lo que una exportación necesita de la corrida M3 resuelta, sin el límite de página."""

    run: models_v2.M3Run | None = None
    problem: M3Problem | None = None
    items: tuple[models_v2.M3Verdict, ...] = ()
    late_launches: models_v2.LateLaunches | None = None
    eligibility_exclusions: models_v2.EligibilityExclusions | None = None


_CHANGED = M3Problem(
    "La evidencia cambió durante la exportación.",
    "La corrida M3 dejó de coincidir entre dos páginas. No se entregó un archivo parcial; "
    "vuelva a exportar.",
    "changed",
)


def export_evidence(
    repository: DataRepository, m2_run_id: str, query: ConsultaQuery, *, rows: bool = True
) -> M3Export:
    """Lee todas las páginas de la consulta, con los mismos filtros y orden que la vista.

    Cada página es una lectura propia del repositorio: si entre dos páginas cambia el total, el
    embudo o el estado de la corrida, la exportación se aborta en vez de mezclar dos instantáneas.
    Con ``rows=False`` solo se lee lo de toda la corrida (exclusiones), sin recorrer los veredictos.
    """
    try:
        resolved = _resolve(repository, m2_run_id, query.m3)
    except (InvalidQuery, DataLayerError) as exc:
        return M3Export(problem=_read_problem(exc))
    if isinstance(resolved, M3Problem):
        return M3Export(problem=resolved)
    kwargs = {**query.as_kwargs(), "page": 1, "size": MAX_SIZE if rows else 1}
    try:
        first = repository.get_m3_evidence(resolved.run_id, **kwargs)
        if first.availability.status != "available":
            return M3Export(run=resolved, problem=_unavailable_evidence(first.availability))
        items = list(first.items)
        last_page = -(-first.page.total // MAX_SIZE) if rows else 1
        for number in range(2, last_page + 1):
            page = repository.get_m3_evidence(resolved.run_id, **{**kwargs, "page": number})
            if (
                page.availability.status != "available"
                or page.run is None
                or page.run.estado != "PUBLICADA"
                or page.page.total != first.page.total
                or page.funnel != first.funnel
            ):
                return M3Export(run=resolved, problem=_CHANGED)
            items += page.items
    except (InvalidQuery, DataLayerError) as exc:
        return M3Export(run=resolved, problem=_read_problem(exc))
    if rows and len(items) != first.page.total:
        return M3Export(run=resolved, problem=_CHANGED)
    return M3Export(
        run=resolved,
        items=tuple(items) if rows else (),
        late_launches=first.late_launches,
        eligibility_exclusions=first.eligibility_exclusions,
    )


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
    try:
        resolved = _resolve(repository, m2_run_id, (wanted or "").strip() or None)
        if isinstance(resolved, M3Problem):
            return M3Profiles(problem=resolved, metrica=metrica)
        portfolio = repository.get_m3_portfolio(resolved.run_id, metrica=metrica, size=MAX_SIZE)
    except (InvalidQuery, DataLayerError) as exc:
        return M3Profiles(problem=_read_problem(exc), metrica=metrica)
    if portfolio.availability.status != "available":
        return M3Profiles(problem=_unavailable_evidence(portfolio.availability), metrica=metrica)
    return M3Profiles(run=resolved, metrica=metrica, rows=portfolio.items)


@dataclass(frozen=True, slots=True)
class M3Classes:
    """El campeón que M3 publicó para cada clase de demanda (``m3_categorias``).

    ``confirmation`` es la confirmación del campeón en el bloque de prueba; ``None`` si la
    corrida se publicó sin ella o si su lectura falló, con la causa en ``confirmation_problem``.
    """

    run: models_v2.M3Run | None = None
    problem: M3Problem | None = None
    rows: list[models_v2.M3Category] = field(default_factory=list)
    confirmation: M3Confirmation | None = None
    confirmation_problem: M3Problem | None = None

    @property
    def confirmations(self) -> dict[str, M3ClassConfirmation]:
        """La confirmación de cada clase, con sus competidoras en el orden de la tabla."""
        if self.confirmation is None:
            return {}
        return {
            row.sku_class: row.model_copy(
                update={"comparaciones": sorted(row.comparaciones, key=_family_position)}
            )
            for row in self.confirmation.clases
        }

    @property
    def significance(self) -> list[M3ClassSignificance]:
        """La significancia HLN-DM frente a Seasonal Naive de cada clase, en el orden de clases."""
        if self.confirmation is None:
            return []
        return sorted(
            self.confirmation.significancia,
            key=lambda row: SKU_CLASS_ORDER.index(row.sku_class),
        )


# Las familias que compiten por clase, en el orden de la tabla. La línea base no lleva columna:
# M3 no publica su mediana de r, que por definición vale 1.
CLASS_FAMILIES: tuple[str, ...] = ("classical", "ml", "dl", "foundation")
MOTIVOS: dict[str, str] = {
    "menor_mediana": "Menor mediana de r",
    "empate_por_simplicidad": "Empate resuelto por simplicidad",
    "compuerta_linea_base": "Ninguna familia supera a la línea base",
    "sin_skus_comparables": "Sin SKU comparables",
}


def _family_position(row: M3Comparison) -> int:
    family = row.competidora
    return CLASS_FAMILIES.index(family) if family in CLASS_FAMILIES else len(CLASS_FAMILIES)


def classes(repository: DataRepository, m2_run_id: str, wanted: str | None = None) -> M3Classes:
    """Resuelve la corrida M3 de ``m2_run_id`` y lee el campeón publicado de cada clase.

    La confirmación del campeón se lee aparte: si falla, el campeón de cada clase se sigue
    mostrando y la sección de la confirmación explica la causa.
    """
    try:
        resolved = _resolve(repository, m2_run_id, (wanted or "").strip() or None)
        if isinstance(resolved, M3Problem):
            return M3Classes(problem=resolved)
        categories = repository.get_m3_categories(resolved.run_id, size=MAX_SIZE)
    except (InvalidQuery, DataLayerError) as exc:
        return M3Classes(problem=_read_problem(exc))
    if categories.availability.status != "available":
        return M3Classes(problem=_unavailable_evidence(categories.availability))
    rows = sorted(categories.items, key=lambda row: SKU_CLASS_ORDER.index(row.sku_class))
    try:
        confirmation = repository.get_m3_confirmation(resolved.run_id)
    except (InvalidQuery, DataLayerError) as exc:
        return M3Classes(run=resolved, rows=rows, confirmation_problem=_read_problem(exc))
    return M3Classes(run=resolved, rows=rows, confirmation=confirmation)


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


def champion_model(verdict: models_v2.M3Verdict) -> str | None:
    """El modelo del candidato campeón publicado: lo que el SKU y la familia de la fila no dicen."""
    return next(
        (
            m.modelo
            for m in verdict.modelos_evaluados
            if m.candidato_id == verdict.candidato_campeon
        ),
        None,
    )


def _direction(row: models_v2.M3Verdict) -> str | None:
    """A quién favorece una HLN-DM significativa; ``None`` si no lo fue o no se sabe.

    El estadístico compara la pérdida de la campeona con la de Seasonal Naive: negativo favorece a
    la campeona y positivo a la línea base (prueba bilateral del motor).
    """
    if not row.dm_significativa or row.dm_estadistico is None or row.dm_estadistico == 0:
        return None
    return "a favor de la campeona" if row.dm_estadistico < 0 else "en contra de la campeona"


def significance_text(row: models_v2.M3Verdict) -> str:
    """La lectura corta de la HLN-DM de un SKU, con el sentido de una diferencia significativa."""
    if row.dm_causa is not None:
        return f"Sin prueba: {row.dm_causa}"
    if not row.dm_significativa:
        return "No significativa"
    direction = _direction(row)
    return f"Significativa, {direction}" if direction else "Significativa"


def significance_items(row: models_v2.M3Verdict) -> list[tuple[str, str]]:
    """La columna Significancia (HLN-DM y BH), separada del veredicto; la causa si no hay prueba."""
    if row.dm_causa is not None:
        return [
            ("Prueba HLN-DM", f"sin prueba: {row.dm_causa}"),
            ("Ventanas de la prueba", format_count(row.dm_n_ventanas)),
            ("Horizonte", format_count(row.dm_horizonte)),
        ]
    direction = _direction(row)
    significant = "no" if not row.dm_significativa else f"sí, {direction}" if direction else "sí"
    return [
        ("Estadístico HLN-DM", _decimal(row.dm_estadistico)),
        ("p-valor", p_text(row.dm_p_valor, row.dm_alfa)),
        ("p ajustado (BH)", p_text(row.dm_p_ajustado, row.dm_alfa)),
        ("α", exact_text(row.dm_alfa) if row.dm_alfa is not None else "—"),
        ("Significativa", significant),
        ("Ventanas de la prueba", format_count(row.dm_n_ventanas)),
        ("Horizonte", format_count(row.dm_horizonte)),
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


CONFIRMATION_STATES: dict[str, str] = {
    "confirmado": "Confirmado",
    "no_confirmado": "No confirmado",
    "sin_comparables": "Sin comparables",
    "no_aplica": "No aplica",
}

# Las causas sin argumento que escribe la confirmación del motor (confirmacion.py).
_CAUSES: dict[str, str] = {
    "campeon_es_linea_base": "la campeona es la línea base",
    "origen_desconocido": "origen de los datos desconocido",
    "datos_sinteticos": "datos sintéticos",
    "sin_familias_competidoras": "sin familias competidoras",
}

# Los valores que una pérdida cero deja sin definir (``perdida_cero:<campos>``).
_UNDEFINED_FIELDS: dict[str, str] = {
    "mediana_r": "mediana de r",
    "mediana_razon": "mediana de la razón",
    "ic_mediana": "intervalo de la mediana",
    "hodges_lehmann": "Hodges-Lehmann",
}


def _spanish_list(items: Sequence[str]) -> str:
    if len(items) == 1:
        return items[0]
    last = items[-1]
    # «y» se vuelve «e» ante un sonido /i/: «mediana de r e intervalo de la mediana».
    conjunction = "e" if last.startswith(("i", "hi")) and not last.startswith("hie") else "y"
    return f"{', '.join(items[:-1])} {conjunction} {last}"


def _families(codes: str) -> str:
    return _spanish_list([family_label(code) for code in codes.split(",")])


def _cause_part(code: str) -> str:
    name, _, argument = code.partition(":")
    if name in _CAUSES and not argument:
        return _CAUSES[name]
    if name == "pocos_skus" and "<" in argument:
        n, minimum = argument.split("<", 1)
        return f"{n} SKU (mínimo {minimum})"
    if name == "pocos_skus" and argument:
        return f"pocos SKU frente a {_families(argument)}"
    if name == "intervalo_no_bajo_1" and argument:
        return f"intervalo no bajo 1 frente a {_families(argument)}"
    if name == "perdida_cero" and argument:
        fields = [_UNDEFINED_FIELDS.get(field, field) for field in argument.split(",")]
        return f"sin {_spanish_list(fields)} por pérdidas cero"
    if code.startswith("p_holm>"):
        return f"p Holm > {code.removeprefix('p_holm>').replace('.', ',')}"
    return code


def cause_text(code: str) -> str:
    """Una causa de la confirmación del motor en español: ``pocos_skus:7<10`` es «7 SKU (mínimo
    10)». Las causas unidas por ``; `` se traducen una a una; una causa desconocida queda igual."""
    return "; ".join(_cause_part(part) for part in code.split("; "))


def _side_digits(value: float, threshold: float | None, digits: int) -> int:
    """Los decimales, desde ``digits``, que dejan la cifra redondeada del mismo lado del umbral.

    Una razón menor que 1 no se muestra como 1 ni un p menor que α como α: 0,9996 queda 0,9996 y
    no 1,000. Las cifras lejos del umbral conservan ``digits``.
    """
    if threshold is None:
        return digits
    side = (value > threshold) - (value < threshold)
    # Un double necesita hasta 17 cifras para distinguirse de su vecino: 0.9999999999999999.
    while digits < 17:
        rounded = float(f"{value:.{digits}f}")
        if (rounded > threshold) - (rounded < threshold) == side:
            break
        digits += 1
    return digits


def ratio_text(value: float | None) -> str:
    """Una razón publicada con tres decimales, o más si hacen falta para no confundirla con 1 ni
    con 0.

    0 e infinito son bordes válidos: 0 significa que solo la campeona no tiene error, así que una
    razón positiva muy pequeña (0,0004) nunca se muestra como 0,000.
    """
    if value is None:
        return "—"
    if value == math.inf:
        return "∞"
    return _decimal(value, max(_side_digits(value, 1.0, 3), _side_digits(value, 0.0, 3)))


def interval_text(lower: float | None, upper: float | None, *, percent: bool = False) -> str:
    """Un intervalo publicado, ``[inferior; superior]``; vacío si M3 no lo publicó."""
    if lower is None or upper is None:
        return ""
    if percent:
        return f"[{percent_text(lower)}; {percent_text(upper)}]"
    return f"[{ratio_text(lower)}; {ratio_text(upper)}]"


def percent_text(share: float) -> str:
    """Una proporción en porcentaje con un decimal; entre 0 y 1 nunca se muestra 0,0 % ni 100,0 %.

    La proporción se acota a [0, 1] y lo que dista menos de 1e-12 de 0 o de 1 es 0 o 1, porque el
    motor guarda las cotas de Wilson sin recortar. Si el redondeo llevaría una proporción interior a
    un borde, se agregan decimales: 1999 de 2000 se muestra 99,95 %.
    """
    share = min(max(share, 0.0), 1.0)
    if share < 1e-12:
        share = 0.0
    elif share > 1 - 1e-12:
        share = 1.0
    value = 100 * share
    digits = max(_side_digits(value, 0.0, 1), _side_digits(value, 100.0, 1))
    return f"{_decimal(value, digits)} %"


def p_text(value: float | None, alfa: float | None = None) -> str:
    """Un p-valor publicado con tres decimales, o más si hacen falta frente a ``alfa``.

    Por debajo de 0,001 se muestra el límite, salvo que α también lo esté. Con ``alfa``, la cifra
    queda del mismo lado de α que el p publicado: 0,0496 y 0,0504 no se muestran como 0,050.
    """
    if value is None:
        return "—"
    if value < 0.001 and (alfa is None or alfa >= 0.001):
        return "< 0,001"
    return _decimal(value, _side_digits(value, alfa, 3))


_SUPERSCRIPTS = str.maketrans("-0123456789", "⁻⁰¹²³⁴⁵⁶⁷⁸⁹")


def exact_text(value: float) -> str:
    """La cifra exacta que M3 guardó, sin redondear: 0,00002, ∞.

    Bajo 10⁻⁶ se escribe con exponente, para que un p de 3,5 × 10⁻¹⁷¹ no ocupe 175 caracteres.
    """
    if value == math.inf:
        return "∞"
    exact = Decimal(repr(value))
    exponent = exact.adjusted()
    if value == 0 or exponent >= -6:
        return format(exact, "f").replace(".", ",")
    sign, digits, _ = exact.as_tuple()
    mantissa = str(digits[0]) + ("," + "".join(map(str, digits[1:])) if len(digits) > 1 else "")
    return f"{'-' if sign else ''}{mantissa} × 10{str(exponent).translate(_SUPERSCRIPTS)}"


def wins_text(wins: int, n_skus: int) -> str:
    """Las victorias del campeón y su proporción entre los SKU comparados."""
    if n_skus == 0:
        return "—"
    return f"{format_count(wins)} ({percent_text(wins / n_skus)})"


def level_text(level: float) -> str:
    """Un nivel de confianza en porcentaje: 95 %, 99,38 %; bajo 1 nunca se muestra como 100 %.

    Usa dos decimales y agrega los necesarios para que un nivel menor que 1 quede bajo 100:
    0,99998 se muestra 99,998 %.
    """
    digits = _side_digits(100 * level, 100.0, 2)
    return f"{_decimal(100 * level, digits).rstrip('0').rstrip(',')} %"


def _decimal(value: float | None, digits: int = 3) -> str:
    return "—" if value is None else f"{value:.{digits}f}".replace(".", ",")
