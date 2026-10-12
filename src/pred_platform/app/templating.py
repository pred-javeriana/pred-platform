"""Jinja2 compartido por los routers: assets locales y mapa de estados."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from fastapi.templating import Jinja2Templates

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
    return templates
