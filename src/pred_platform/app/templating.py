"""Jinja2 compartido por los routers: assets locales y mapa de estados."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from fastapi.templating import Jinja2Templates

from pred_platform.app.presentation import describe_status, format_count


def build_templates(directory: Path, static_url: Callable[[str], str]) -> Jinja2Templates:
    """Una sola instancia, registrada en application.state.templates."""
    templates = Jinja2Templates(directory=str(directory))
    templates.env.globals["static_url"] = static_url
    templates.env.globals["describe_status"] = describe_status
    templates.env.globals["format_count"] = format_count
    return templates
