"""Jinja2 compartido por los routers: assets locales y mapa de estados."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.templating import Jinja2Templates

from pred_platform import fallas, mensajes
from pred_platform.app import presentation, resultados_m3, seleccion
from pred_platform.data.deps import get_repository


def _shell(request: Request) -> dict[str, Any]:
    """Lo que la barra superior de cada página necesita: si se puede empezar una corrida nueva
    (``seleccion.run_action``) y, si no, por qué."""
    return {"new_run": seleccion.run_action(get_repository(request).get_capabilities())}


def build_templates(directory: Path, static_url: Callable[[str], str]) -> Jinja2Templates:
    """Una sola instancia, registrada en application.state.templates."""
    templates = Jinja2Templates(directory=str(directory), context_processors=[_shell])
    templates.env.globals["static_url"] = static_url
    # «capitalize» de Jinja pasa el resto a minúsculas («Rmse»); este solo sube la primera letra.
    templates.env.filters["capitalize_first"] = lambda text: str(text)[:1].upper() + str(text)[1:]
    # Cada etiqueta y su explicación salen de `presentation`, el único dueño de esas lecturas.
    for name in (
        "abbreviate_id",
        "aggregation_label",
        "closing_period",
        "count_noun",
        "describe_status",
        "evaluation_state_label",
        "exact_when",
        "family_label",
        "family_options",
        "family_term",
        "format_count",
        "format_date",
        "format_decimal",
        "format_duration",
        "format_when",
        "glossary_hint",
        "group_lumpy_cause_text",
        "group_option_label",
        "group_problem_text",
        "metric_label",
        "metric_term",
        "model_label",
        "model_term",
        "sku_class_label",
        "sku_class_options",
        "sku_class_term",
        "stage_label",
        "stage_term",
        "status_options",
        "verdict_label",
        "verdict_options",
        "FAMILY_ORDER",
        "SKU_CLASS_ORDER",
        "VERDICT_ORDER",
    ):
        templates.env.globals[name] = getattr(presentation, name)
    for name in (
        "compact_config",
        "error_title",
        "evidence_items",
        "exclusion_text",
        "profile_label",
        "trial_counts",
        "window_metric_names",
    ):
        templates.env.globals[name] = getattr(seleccion, name)
    templates.env.globals["CHAMPION_FAMILIES"] = resultados_m3.CHAMPION_FAMILIES
    templates.env.globals["CHAMPION_HEADERS"] = [
        presentation.family_label(family) for family in resultados_m3.CHAMPION_FAMILIES
    ]
    templates.env.globals["FAMILY_HEADERS"] = [
        presentation.family_label(family) for family in presentation.FAMILY_ORDER
    ]
    for name in (
        "cause_text",
        "champion_model",
        "champion_share_text",
        "effect_parts",
        "effect_phrase",
        "exact_text",
        "funnel_rows",
        "gap_digits",
        "interval_text",
        "level_text",
        "origin_fact",
        "origin_label",
        "p_text",
        "percent_text",
        "ranking",
        "ratio_text",
        "r_interval_text",
        "significance_items",
        "significance_text",
        "source_cause",
        "verdict_counts",
        "verdict_group",
        "verdict_reason",
        "wins_text",
    ):
        templates.env.globals[name] = getattr(resultados_m3, name)
    templates.env.globals["CONFIRMATION_STATES"] = resultados_m3.CONFIRMATION_STATES
    templates.env.globals["error_action"] = mensajes.accion
    templates.env.globals["describir_falla"] = fallas.describir_falla
    templates.env.globals["texto_soporte"] = fallas.texto_soporte
    templates.env.globals["describir_motivo"] = fallas.describir_motivo
    templates.env.globals["soporte_ensayos"] = fallas.soporte_ensayos
    templates.env.globals["CATALOGO_ERRORES"] = mensajes.CATALOGO
    templates.env.globals["fila_en_archivo"] = mensajes.fila_en_archivo
    return templates
