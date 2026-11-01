"""Jinja2 compartido por los routers: assets locales y mapa de estados."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from fastapi.templating import Jinja2Templates

from pred_platform import mensajes
from pred_platform.app import resultados_m3, seleccion
from pred_platform.app.presentation import (
    abbreviate_id,
    describe_status,
    format_count,
    format_decimal,
    format_when,
    sku_class_label,
)


def build_templates(directory: Path, static_url: Callable[[str], str]) -> Jinja2Templates:
    """Una sola instancia, registrada en application.state.templates."""
    templates = Jinja2Templates(directory=str(directory))
    templates.env.globals["static_url"] = static_url
    templates.env.globals["describe_status"] = describe_status
    templates.env.globals["format_count"] = format_count
    templates.env.globals["format_decimal"] = format_decimal
    templates.env.globals["format_when"] = format_when
    templates.env.globals["abbreviate_id"] = abbreviate_id
    templates.env.globals["sku_class_label"] = sku_class_label
    for name in (
        "compact_config",
        "error_title",
        "evidence_items",
        "exclusion_text",
        "family_label",
        "profile_label",
        "trial_counts",
        "window_metric_names",
    ):
        templates.env.globals[name] = getattr(seleccion, name)
    for name in (
        "exact_text",
        "funnel_rows",
        "interval_text",
        "level_text",
        "origin_label",
        "p_text",
        "ranking",
        "ratio_text",
        "significance_items",
        "verdict_counts",
        "verdict_group",
        "wins_text",
    ):
        templates.env.globals[name] = getattr(resultados_m3, name)
    templates.env.globals["CONFIRMATION_STATES"] = resultados_m3.CONFIRMATION_STATES
    templates.env.globals["error_action"] = mensajes.accion
    templates.env.globals["describir_error"] = mensajes.describir
    templates.env.globals["CATALOGO_ERRORES"] = mensajes.CATALOGO
    templates.env.globals["fila_en_archivo"] = mensajes.fila_en_archivo
    templates.env.globals["FAMILY_ORDER"] = seleccion.FAMILY_ORDER
    templates.env.globals["FAMILY_HEADERS"] = [
        seleccion.family_label(family) for family in seleccion.FAMILY_ORDER
    ]
    return templates
