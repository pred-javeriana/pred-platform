"""Paso 5, Resultados: selección por SKU (C2). La interfaz no elige campeones.

Dos páginas sobre ``DataRepository``: las pestañas del paso (``/corridas/<id>/resultados``) y el
detalle de un SKU (``/resultados/sku/{sku_id}``). ``/resultados`` lleva al paso de la corrida
pedida (``?ejecucion=``) o, sin pedirla, de la más nueva con evidencia publicada. Filtros, orden
y paginación se delegan al repositorio; aquí solo se arma la consulta y se presentan los
documentos tal como llegan. La selección es la de M2; el
campeón y el veredicto de cada SKU son los que publicó M3 en una corrida exacta
(``resultados_m3``), y los enlaces conservan esa corrida resuelta.
"""

from __future__ import annotations

import json
import math
from dataclasses import replace
from typing import Any
from urllib.parse import quote, urlencode

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from pred_platform.app.borrador import read_draft
from pred_platform.app.charts import (
    FAMILY_COLORS,
    champion_forecast_svg,
    champion_share_svg,
    product_forecast_key_svg,
    product_models_key_svg,
    product_models_legend_svg,
    product_models_svg,
)
from pred_platform.app.consulta import ConsultaQuery, parse_consulta_query
from pred_platform.app.corridas import draft_facts, run_facts
from pred_platform.app.datos import error_view, ingest_name, page_meta
from pred_platform.app.ejecucion import run_resumable
from pred_platform.app.pasos import (
    NEW,
    Wizard,
    draft_resume,
    draft_wizard,
    run_wizard,
    step_href,
)
from pred_platform.app.presentation import (
    FAMILY_ORDER,
    describe_run,
    describe_status,
    family_label,
    format_decimal,
    sku_class_term,
    verdict_label,
)
from pred_platform.app.resultados_m3 import (
    CLASS_FAMILIES,
    MOTIVOS,
    PROFILE_METRICS,
    ExecutionChoice,
    M3Classes,
    M3Consulta,
    M3Decision,
    M3Profiles,
    M3Summary,
    champion_bars,
    classes,
    consulta,
    decision,
    default_execution,
    other_publications,
    profiles,
    published_executions,
    reserve_windows,
    run_outcome,
    summary,
)
from pred_platform.app.resultados_pronostico import (
    best_other_model,
    champion_forecast,
    compute_demand_blocks,
    product_outcome_sentence,
    read_sku_r_interval,
)
from pred_platform.app.routers.corridas import (
    Locked,
    redirect_with_query,
    render_locked,
    render_missing_run,
)
from pred_platform.app.seleccion import (
    SelectionQuery,
    parse_selection_query,
    run_action,
)
from pred_platform.contract import models_v2
from pred_platform.contract.errors import ArtifactMissing, DataLayerError, InvalidQuery
from pred_platform.contract.models import RunStatus, SkuSelection, SkuSelectionList
from pred_platform.data.deps import get_repository
from pred_platform.data.m3_intervals import M3Interval
from pred_platform.data.query import LATEST
from pred_platform.data.repository import DataRepository

router = APIRouter()
# Cuántas corridas con evidencia publicada ofrece el selector de corrida.
_EXECUTION_CHOICES = 20

# Las pestañas, en orden de lectura: primero la vista general (Shneiderman: «overview first»),
# después el detalle. «consulta» es el nombre anterior de la lista de veredictos.
VIEWS = ("resumen", "clase", "errores", "sku", "exportar")
# Las dos lecturas de la pestaña SKU: lo que M3 decidió por SKU y lo que M2 dejó por familia.
LISTS = ("veredictos", "familias")


def _base_context(repository: DataRepository, vista: str) -> dict[str, Any]:
    capabilities = repository.get_capabilities()
    champion = next(item for item in capabilities.items if item.id == "champion_selection")
    return {
        "capabilities": capabilities,
        "vista": vista,
        "action": run_action(capabilities),
        "champion_capability": champion,
        "source": repository.source,
    }


def _run_header(repository: DataRepository, reference: str) -> RunStatus | None:
    """El encabezado de la ejecución mostrada; sin él la página sigue siendo válida."""
    try:
        status = repository.get_run_status(reference, size=1)
    except (DataLayerError, InvalidQuery):
        return None
    return status if status.ejecucion is not None else None


def _vista(params: dict[str, str], evidence: bool) -> str:
    """La pestaña pedida; sin pedirla, el resumen si hay evidencia M3 publicada y, si no, la
    lista de SKU, que ya muestra lo que dejó M2."""
    wanted = params.get("vista") or ""
    if wanted in ("consulta", "productos"):
        return "sku"
    if wanted in VIEWS:
        return wanted
    return "resumen" if evidence else "sku"


def _lista(params: dict[str, str], champion: bool, evidence: bool) -> str:
    wanted = params.get("lista") or ""
    if wanted in LISTS:
        return wanted if champion else "familias"
    # Los filtros de familia y de estado son de la lista de M2: un enlace con ellos la abre.
    if not evidence or params.get("family") or params.get("estado"):
        return "familias"
    return "veredictos"


@router.get("/resultados")
def resultados(
    request: Request,
    repository: DataRepository = Depends(get_repository),  # noqa: B008
) -> RedirectResponse:
    """La ruta anterior: el paso Resultados de la corrida pedida o, sin pedirla, de la más nueva
    con evidencia publicada (si no hay, la más reciente)."""
    params = request.query_params
    wanted = (params.get("ejecucion") or "").strip()
    target: str | None = None
    if wanted and wanted != LATEST:
        header = _run_header(repository, wanted)
        target = (
            header.ejecucion.id if header is not None and header.ejecucion is not None else wanted
        )
    if target is None:
        published = published_executions(repository)
        target = published[0].ejecucion_id if published else _latest_run(repository)
    if target is None:
        return redirect_with_query(step_href(NEW, "resultados"), [])
    return redirect_with_query(
        step_href(target, "resultados"), params.multi_items(), drop=("ejecucion",)
    )


def _latest_run(repository: DataRepository) -> str | None:
    status = _run_header(repository, LATEST)
    return status.ejecucion.id if status is not None and status.ejecucion is not None else None


@router.get("/corridas/{corrida}/resultados", response_class=HTMLResponse)
def paso_resultados(
    request: Request,
    corrida: str,
    repository: DataRepository = Depends(get_repository),  # noqa: B008
) -> HTMLResponse:
    if corrida == NEW:
        draft = draft_facts(repository, read_draft(request))
        resume = draft_resume(draft)
        return render_locked(
            request,
            Locked(
                title="Resultados",
                heading="Los resultados todavía no existen",
                text="Se abren cuando la etapa 4, Evaluación final, publique.",
                # El grupo de semillas lee las evaluaciones publicadas, no este borrador.
                actions=(
                    (resume.label, resume.href, True),
                    ("Grupo de semillas", "/resultados/grupo", False),
                ),
            ),
            draft_wizard("resultados", draft),
        )
    executions = published_executions(repository, limit=None)
    facts = run_facts(repository, corrida, frozenset(e.ejecucion_id for e in executions))
    if facts is None:
        return render_missing_run(request, corrida)
    # Mientras la evaluación final no publica, el paso muestra lo que ya hay (la selección de cada
    # familia) y lo dice; no se bloquea.
    wizard = run_wizard("resultados", facts)
    return _resultados_page(request, repository, facts.id, wizard, executions, seed=facts.seed)


@router.get("/corridas/{corrida}/resultados/exportar", response_class=HTMLResponse)
def paso_resultados_exportar(
    request: Request,
    corrida: str,
    repository: DataRepository = Depends(get_repository),  # noqa: B008
) -> HTMLResponse:
    if corrida == NEW:
        draft = draft_facts(repository, read_draft(request))
        resume = draft_resume(draft)
        return render_locked(
            request,
            Locked(
                title="Resultados",
                heading="Los resultados todavía no existen",
                text="Se abren cuando la etapa 4, Evaluación final, publique.",
                actions=((resume.label, resume.href, True),),
            ),
            draft_wizard("resultados", draft),
        )
    executions = published_executions(repository, limit=None)
    facts = run_facts(repository, corrida, frozenset(e.ejecucion_id for e in executions))
    if facts is None:
        return render_missing_run(request, corrida)
    wizard = run_wizard("resultados", facts)
    return _resultados_page(
        request, repository, facts.id, wizard, executions, forced_vista="exportar", seed=facts.seed
    )


def _resultados_page(
    request: Request,
    repository: DataRepository,
    reference: str,
    wizard: Wizard,
    published: list[ExecutionChoice],
    *,
    forced_vista: str | None = None,
    seed: int | None = None,
) -> HTMLResponse:
    """La página del paso para la corrida ``reference``; ``published`` son las corridas con
    evidencia M3 publicada, de la más nueva a la más vieja."""
    params = dict(request.query_params)
    probe = _base_context(repository, "")
    champion = probe["champion_capability"].available
    # La ejecución (la de la ruta) y la corrida M3 se leen aparte de los filtros: un page o un
    # size inválido nunca hace perder qué ejecución se estaba mirando.
    executions = published[:_EXECUTION_CHOICES]
    run = _run_header(repository, reference)
    run_id = run.ejecucion.id if run is not None and run.ejecucion is not None else reference
    evidence = champion and any(e.ejecucion_id == run_id for e in executions)
    vista = forced_vista or _vista(params, evidence)
    lista = _lista(params, champion, evidence) if vista == "sku" else None
    context = {**probe, "vista": vista, "lista": lista, "wizard": wizard}
    wanted_m3 = (params.get("m3") or "").strip() or None
    query_error: str | None = None
    try:
        query = replace(parse_selection_query(params), ejecucion=reference, m3=wanted_m3)
    except InvalidQuery as exc:
        query, query_error = SelectionQuery(ejecucion=reference, m3=wanted_m3), str(exc)
    context.update(
        query=query,
        query_error=query_error,
        listing=None,
        list_error=None,
        rows=[],
        pages=None,
        run=run,
        executions=executions,
        reference=reference,
        missing_execution=reference if run is None else None,
        m3=M3Decision(),
        m3_summary=M3Summary(),
        m3_profiles=M3Profiles(),
        m3_classes=M3Classes(),
        champion_svg="",
        m3_consulta=M3Consulta(),
        cq=ConsultaQuery(ejecucion=reference, m3=wanted_m3),
        consulta_error=None,
        consulta_pages=None,
        metric_hrefs={},
        profile_metrics=PROFILE_METRICS,
        class_families=CLASS_FAMILIES,
        class_headers=[family_label(family) for family in CLASS_FAMILIES],
        motivos=MOTIVOS,
        m3_hrefs={},
        tab_query=urlencode({"ejecucion": reference}) if reference != LATEST else "",
    )
    if run is None or run.ejecucion is None:
        return _render(request, context)
    if vista == "sku" and lista == "familias":
        _fill_list(repository, query, context)
    elif vista == "sku":
        _fill_consulta(repository, params, context)
    elif vista == "exportar":
        _fill_exportar(repository, run.ejecucion.id, query, context, seed=seed)
    elif champion:
        _fill_m3_view(repository, run.ejecucion.id, vista, params, context)
    resolved = context["m3"].run
    _describe_run(repository, run, resolved, context)
    if resolved is not None:
        # Las pestañas conservan la corrida M3 resuelta, no «la más nueva».
        context["tab_query"] = urlencode(
            {
                **({"ejecucion": reference} if reference != LATEST else {}),
                "m3": resolved.run_id,
            }
        )
    return _render(request, context)


def _describe_run(
    repository: DataRepository,
    run: RunStatus,
    resolved: models_v2.M3Run | None,
    context: dict[str, Any],
) -> None:
    """Lo que la barra de contexto dice de la ejecución: su archivo y qué dejó al terminar."""
    if run.ejecucion is None:
        return
    counts = run.progress.tareas_por_estado if run.progress is not None else None
    # El embudo que la vista ya leyó (Resumen o Veredictos) evita leerlo otra vez.
    summary, consulta = context.get("m3_summary"), context.get("m3_consulta")
    funnel = (
        (
            (summary.funnel if summary is not None else None)
            or (consulta.evidence.funnel if consulta is not None and consulta.evidence else None)
        )
        if resolved is not None and resolved.estado == "PUBLICADA"
        else None
    )
    context["outcome"] = run_outcome(
        repository,
        run.ejecucion,
        counts,
        resolved.run_id if resolved and resolved.estado == "PUBLICADA" else None,
        funnel,
    )
    context["run_ingest"] = ingest_name(repository, run.ejecucion.ingest_id)
    context["run_status"] = describe_run(
        run.ejecucion.estado, resumable=run_resumable(repository, run)
    )


def _render(request: Request, context: dict[str, Any]) -> HTMLResponse:
    return request.app.state.templates.TemplateResponse(request, "pages/results.html", context)


def _fill_m3_view(
    repository: DataRepository,
    ejecucion_id: str,
    vista: str,
    params: dict[str, str],
    context: dict[str, Any],
) -> None:
    """Resumen, Por clase y Perfiles de error: la misma corrida M3 resuelta para la ejecución."""
    query: SelectionQuery = context["query"]
    base = {"vista": vista, "ejecucion": context["reference"]}
    resolved: models_v2.M3Run | None
    if vista == "resumen":
        context["m3_summary"] = found_summary = summary(repository, ejecucion_id, query.m3)
        resolved = found_summary.run
        if found_summary.champions is not None:
            context["champion_svg"] = champion_share_svg(
                champion_bars(found_summary.champions),
                found_summary.champions.published_total,
            )
    elif vista == "clase":
        context["m3_classes"] = found_classes = classes(repository, ejecucion_id, query.m3)
        resolved = found_classes.run
    else:
        context["m3_profiles"] = found_profiles = profiles(
            repository, ejecucion_id, query.m3, params.get("metrica") or "razon_sn"
        )
        resolved = found_profiles.run
        metric_base = {**base, **({"m3": resolved.run_id} if resolved else {})}
        context["metric_hrefs"] = {
            name: "/resultados?" + urlencode({**metric_base, "metrica": name})
            for name in PROFILE_METRICS
        }
    if resolved is not None:
        # El encabezado muestra la misma corrida M3 que la pestaña; la causa de un problema la
        # explica solo la pestaña.
        others = other_publications(repository, resolved)
        context["m3"] = M3Decision(run=resolved, others=others)
        context["m3_hrefs"] = {
            other.run_id: "/resultados?" + urlencode({**base, "m3": other.run_id})
            for other in others
        }


def _fill_exportar(
    repository: DataRepository,
    ejecucion_id: str,
    query: SelectionQuery,
    context: dict[str, Any],
    *,
    seed: int | None = None,
) -> None:
    """Exportar y reproducir: resuelve la corrida M3, exclusiones y semilla."""
    context["m3_summary"] = found_summary = summary(repository, ejecucion_id, query.m3)
    resolved = found_summary.run
    if resolved is not None:
        others = other_publications(repository, resolved)
        context["m3"] = M3Decision(run=resolved, others=others)
        context["m3_hrefs"] = {
            other.run_id: "/corridas/"
            + str(context["reference"])
            + "/resultados?"
            + urlencode({"vista": "exportar", "m3": other.run_id})
            for other in others
        }
    funnel = found_summary.funnel
    if funnel is not None and funnel.total_productos and funnel.tras_exclusiones:
        n_excl = funnel.total_productos - funnel.tras_exclusiones
    elif funnel is not None:
        n_excl = funnel.excluded_count + (funnel.eligibility_excluded_count or 0)
    else:
        n_excl = 0
    context["n_exclusiones"] = n_excl
    context["run_seed"] = seed if seed is not None else 0


def _fill_consulta(
    repository: DataRepository, params: dict[str, str], context: dict[str, Any]
) -> None:
    """La lista de veredictos: lo que M3 publicó por SKU, filtrado y paginado por el repositorio."""
    reference, wanted_m3 = context["reference"], context["query"].m3
    try:
        cq = replace(parse_consulta_query(params), ejecucion=reference, m3=wanted_m3)
    except InvalidQuery as exc:
        cq, context["consulta_error"] = ConsultaQuery(ejecucion=reference, m3=wanted_m3), str(exc)
    context["cq"] = cq
    run = context["run"]
    found = consulta(repository, run.ejecucion.id, cq)
    context["m3_consulta"] = found
    if found.run is not None:
        # Filtros, páginas y detalle conservan la corrida M3 resuelta, no «la más nueva».
        context["cq"] = cq = replace(cq, m3=found.run.run_id)
        others = other_publications(repository, found.run)
        context["m3"] = M3Decision(run=found.run, others=others)
        context["m3_hrefs"] = {
            other.run_id: "/resultados?" + cq.query_string(m3=other.run_id, page="")
            for other in others
        }
    if found.evidence is not None:
        context["consulta_pages"] = page_meta(found.evidence.page)


def _fill_list(repository: DataRepository, query: SelectionQuery, context: dict[str, Any]) -> None:
    """La lista de M2: el estado y el valor de cada familia por SKU, con el veredicto de M3."""
    listing: SkuSelectionList | None = None
    try:
        listing = repository.list_sku_selections(query.ejecucion, **query.as_kwargs())
    except InvalidQuery as exc:
        context["query_error"] = str(exc)
    except DataLayerError as exc:
        context["list_error"] = error_view(exc)
    if listing is None:
        return
    context["listing"] = listing
    context["pages"] = page_meta(listing.page)
    if listing.availability.status == "available":
        m3 = decision(repository, listing.run_id, [row.sku_id for row in listing.items], query.m3)
        context["m3"] = m3
        if m3.run is not None:
            # Filtros, páginas y detalle conservan la corrida M3 resuelta, no «la más nueva».
            context["query"] = replace(query, m3=m3.run.run_id)
        context["m3_hrefs"] = {
            other.run_id: "/resultados?" + query.query_string(m3=other.run_id, page="")
            for other in m3.others
        }
    # Una celda por familia, en el orden del contrato; una familia que el documento no trae es «—».
    context["rows"] = [
        {"row": row, "cells": {brief.family: brief for brief in row.families}}
        for row in listing.items
    ]


def _fmt_ci(lo: float | None, hi: float | None, cola: float | None = None) -> str:
    if lo is None:
        return "sin intervalo"
    if hi is None or math.isinf(hi):
        return f"desde {format_decimal(lo, 3)}, sin cota superior"
    res = f"{format_decimal(lo, 3)} a {format_decimal(hi, 3)}"
    if cola is not None:
        res += f", o desde {format_decimal(cola, 3)}"
    return res


@router.get("/corridas/{corrida}/resultados/producto/{sku_id:path}", response_class=HTMLResponse)
@router.get("/corridas/{corrida}/resultados/sku/{sku_id:path}", response_class=HTMLResponse)
def producto_detalle(
    request: Request,
    corrida: str,
    sku_id: str,
    repository: DataRepository = Depends(get_repository),  # noqa: B008
) -> HTMLResponse:
    return _producto_detalle_page(request, repository, corrida, sku_id)


def _resolve_sku_run(repository: DataRepository, sku_id: str) -> str | None:
    published = published_executions(repository)
    candidates = [e.ejecucion_id for e in published]
    try:
        for r in repository.list_runs():
            if r.id not in candidates:
                candidates.append(r.id)
    except Exception:
        pass
    scenario = getattr(repository, "scenario", None)
    if scenario and hasattr(scenario, "sku_selections"):
        from pred_platform.contract.resources import read_example

        for ex in scenario.sku_selections:
            try:
                data = json.loads(read_example(ex))
                if data.get("sku_id") == sku_id:
                    rid = str(data.get("run_id") or "")
                    if rid and rid in candidates:
                        return rid
            except Exception:
                continue
    if candidates:
        return candidates[0]
    return _latest_run(repository)


@router.get("/resultados/sku/{sku_id:path}")
def sku_detalle(
    request: Request,
    sku_id: str,
    repository: DataRepository = Depends(get_repository),  # noqa: B008
) -> RedirectResponse:
    params = request.query_params
    wanted = (params.get("ejecucion") or "").strip()
    target: str | None = wanted if wanted not in ("", LATEST) else None
    if target is None:
        target = _resolve_sku_run(repository, sku_id)
    if target is None:
        target = default_execution(repository)
    quoted_target = quote(target, safe="")
    quoted_sku = quote(sku_id, safe="")
    return redirect_with_query(
        f"/corridas/{quoted_target}/resultados/producto/{quoted_sku}",
        params.multi_items(),
        drop=("ejecucion",),
    )


def _producto_detalle_page(
    request: Request,
    repository: DataRepository,
    corrida: str,
    sku_id: str,
) -> HTMLResponse:
    if corrida == NEW:
        draft = draft_facts(repository, read_draft(request))
        resume = draft_resume(draft)
        return render_locked(
            request,
            Locked(
                title="Resultados",
                heading="Los resultados todavía no existen",
                text="Se abren cuando la etapa 4, Evaluación final, publique.",
                actions=((resume.label, resume.href, True),),
            ),
            draft_wizard("resultados", draft),
        )
    executions = published_executions(repository, limit=None)
    facts = run_facts(repository, corrida, frozenset(e.ejecucion_id for e in executions))
    if facts is None:
        return render_missing_run(request, corrida)
    wizard = run_wizard("resultados", facts)

    params = dict(request.query_params)
    wanted_m3 = (params.get("m3") or "").strip() or None
    back = params.get("volver", "")
    with_windows = params.get("ventanas") == "1"
    context = _base_context(repository, "sku")
    status = 200
    selection: SkuSelection | None = None
    detail_error: dict[str, Any] | None = None
    try:
        selection = repository.get_sku_selection(corrida, sku_id, include_windows=with_windows)
    except ArtifactMissing as exc:
        detail_error, status = error_view(exc), 404
    except InvalidQuery as exc:
        detail_error = {
            "title": "La consulta no es válida.",
            "detail": str(exc),
            "code": "platform.invalid_query",
            "blocked_by": [],
        }
        status = 400
    except DataLayerError as exc:
        detail_error = error_view(exc)

    m3 = (
        decision(repository, selection.run_id if selection else corrida, [sku_id], wanted_m3)
        if selection is not None
        else M3Decision()
    )
    interval_obj: M3Interval | None = None
    interval_error = False
    if m3.run is not None and sku_id in m3.verdicts:
        try:
            intervals = repository.get_m3_intervals(m3.run.run_id, [sku_id])
            interval_obj = next((row for row in intervals if row.sku == sku_id), None)
        except DataLayerError:
            interval_error = True

    resolved_m3 = m3.run.run_id if m3.run is not None else wanted_m3
    verdict = m3.verdicts.get(sku_id)
    fetch_alt = bool(
        verdict
        and (
            verdict.pierde_frente_a_linea_base
            or (verdict.razon_sn is not None and verdict.razon_sn > 1.0)
        )
    )
    forecast = champion_forecast(repository, m3, sku_id, include_alt=fetch_alt)

    best_other = best_other_model(verdict) if verdict else None
    outcome_sentence = product_outcome_sentence(verdict) if verdict else ""
    demand_blocks = compute_demand_blocks(forecast) if (forecast and not forecast.problem) else None

    interval_tuple: tuple[float, float | None, float | None] | None = None
    if interval_obj:
        interval_tuple = (interval_obj.inferior, interval_obj.superior, interval_obj.inferior_cola)
    elif not interval_error and verdict and verdict.run_id:
        db_path = getattr(request.app.state.settings, "db_path", None)
        if db_path:
            raw_interval = read_sku_r_interval(db_path, verdict.run_id, sku_id)
            if raw_interval:
                interval_tuple = raw_interval
                interval_obj = M3Interval(
                    sku=sku_id,
                    inferior=raw_interval[0],
                    superior=raw_interval[1],
                    inferior_cola=raw_interval[2],
                )

    interval_text = (
        _fmt_ci(interval_tuple[0], interval_tuple[1], interval_tuple[2])
        if interval_tuple
        else "sin intervalo"
    )

    sig_title = "Sin prueba"
    p_adj_text = "—"
    if verdict and verdict.dm_estadistico is not None and verdict.dm_p_ajustado is not None:
        p_val = verdict.dm_p_ajustado
        p_adj_text = "< 0,001" if p_val < 0.001 else format_decimal(p_val, 3)
        if verdict.dm_significativa:
            if verdict.razon_sn is not None and verdict.razon_sn < 1.0:
                sig_title = "Significativa, a favor"
            else:
                sig_title = "Significativa, en contra"
        else:
            sig_title = "No significativa"
    elif verdict and verdict.dm_p_ajustado is not None:
        p_val = verdict.dm_p_ajustado
        p_adj_text = "< 0,001" if p_val < 0.001 else format_decimal(p_val, 3)

    verdict_desc = describe_status("verdict", verdict.veredicto) if verdict else None
    verdict_hint = verdict_desc.hint if verdict_desc else ""

    forecast_key = (
        product_forecast_key_svg(
            verdict.familia_campeona,
            best_other.familia if best_other else None,
        )
        if verdict
        else ""
    )
    forecast_svg = champion_forecast_svg(forecast) if (forecast and not forecast.problem) else ""

    models_svg = (
        product_models_svg(verdict, verdict.candidato_campeon, interval_tuple) if verdict else ""
    )
    models_legend = product_models_legend_svg() if verdict else ""
    models_key = product_models_key_svg() if verdict else ""

    sku_class = selection.sku_class if selection else (verdict.sku_class if verdict else "")
    c_term = sku_class_term(sku_class)

    run = _run_header(repository, corrida)
    scope = {
        k: v
        for k, v in (
            ("ejecucion", "" if corrida == LATEST else corrida),
            ("m3", resolved_m3 or ""),
        )
        if v
    }
    results_href = "/resultados" + (f"?{urlencode(scope)}" if scope else "")
    list_href = "/resultados?" + (back or urlencode({"vista": "sku", **scope}))
    base = {**scope, **({"volver": back} if back else {})}

    reserve = None
    if params.get("reserva") == "1" and m3.run is not None and sku_id in m3.verdicts:
        reserve = reserve_windows(repository, m3.run.run_id, m3.verdicts[sku_id])

    entries = (
        sorted(
            selection.families,
            key=lambda f: FAMILY_ORDER.index(f.family) if f.family in FAMILY_ORDER else 99,
        )
        if selection is not None
        else []
    )

    context.update(
        wizard=wizard,
        reference=corrida,
        sku_id=sku_id,
        selection=selection,
        entries=entries,
        with_windows=with_windows,
        reserve=reserve,
        windows_href=(
            f"/corridas/{corrida}/resultados/producto/{quote(sku_id, safe='')}"
            f"?{urlencode({**base, 'ventanas': 1})}"
        ),
        reserve_href=(
            f"/corridas/{corrida}/resultados/producto/{quote(sku_id, safe='')}"
            f"?{urlencode({**base, 'reserva': 1})}"
        ),
        hide_windows_href=f"/corridas/{corrida}/resultados/producto/{quote(sku_id, safe='')}"
        + (f"?{urlencode(base)}" if base else ""),
        topology=selection.topology if selection else None,
        detail_error=detail_error,
        not_found=status == 404,
        results_href=results_href,
        list_href=list_href,
        run=run,
        m3=m3,
        verdict=verdict,
        class_label=c_term.label,
        class_hint=c_term.hint,
        outcome_sentence=outcome_sentence,
        interval_text=interval_text,
        sig_title=sig_title,
        p_adj_text=p_adj_text,
        verdict_label=verdict_label,
        verdict_hint=verdict_hint,
        best_other=best_other,
        alt_color=FAMILY_COLORS.get(best_other.familia, "#333") if best_other else "",
        alt_family_label=family_label(best_other.familia) if best_other else "",
        champion_family_label=family_label(verdict.familia_campeona) if verdict else "",
        forecast=forecast,
        forecast_chart_key=forecast_key,
        forecast_svg=forecast_svg,
        demand_blocks=demand_blocks,
        models_svg=models_svg,
        models_legend=models_legend,
        models_chart_key=models_key,
        interval=interval_obj,
        interval_error=interval_error,
        m3_hrefs={
            other.run_id: (
                f"/corridas/{corrida}/resultados/producto/{quote(sku_id, safe='')}"
                f"?{urlencode({**base, 'm3': other.run_id})}"
            )
            for other in m3.others
        },
    )
    if run is not None:
        _describe_run(repository, run, m3.run, context)

    return request.app.state.templates.TemplateResponse(
        request, "pages/results_producto.html", context, status_code=status
    )
