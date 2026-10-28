"""Gráfico ADI–CV² generado en el servidor a partir de TopologyReport.

No clasifica SKU ni recalcula umbrales: dibuja los puntos y las líneas que ya
vienen en el contrato. La clase se distingue por forma y por color (RNF-USA-04).
"""

from __future__ import annotations

from xml.sax.saxutils import escape

from pred_platform.app.presentation import format_decimal, sku_class_label
from pred_platform.contract.models import TopologyMetrics, TopologyReport

_WIDTH = 640
_HEIGHT = 400
_LEFT = 52
_RIGHT = 18
_TOP = 16
_BOTTOM = 92
_RADIUS = 5.0

# Forma + color: la leyenda textual evita que el color sea el único canal.
_MARKERS: dict[str, tuple[str, str]] = {
    "smooth": ("circle", "#3e6b4f"),
    "erratic": ("square", "#2e6e9e"),
    "intermittent": ("triangle", "#a0741f"),
    "lumpy": ("diamond", "#a3402f"),
}


def topology_scatter_svg(report: TopologyReport) -> str:
    """SVG inline con ejes ADI (x) y CV² (y), umbrales y un punto por ítem."""
    thresholds = report.thresholds
    x_max = _span([row.adi for row in report.items], thresholds.adi if thresholds else 0.0)
    y_max = _span([row.cv2 for row in report.items], thresholds.cv2 if thresholds else 0.0)
    title = "Distribución ADI–CV² de la demanda"
    desc = (
        "Cada marca es un SKU del reporte. El eje horizontal es ADI y el vertical es CV². "
        "Las clases se distinguen por forma: círculo Smooth (Suave), cuadrado Erratic (Errática), "
        "triángulo Intermittent (Intermitente) y rombo Lumpy (Grumosa)."
    )
    parts = [
        f'<svg role="img" viewBox="0 0 {_WIDTH} {_HEIGHT}" width="{_WIDTH}" height="{_HEIGHT}" '
        'xmlns="http://www.w3.org/2000/svg" class="pred-chart__svg">',
        f"<title>{escape(title)}</title>",
        f"<desc>{escape(desc)}</desc>",
        _axes(x_max, y_max),
    ]
    if thresholds is not None:
        adi_line = _map_x(thresholds.adi, x_max)
        cv2_line = _map_y(thresholds.cv2, y_max)
        parts.extend(
            [
                f'<line x1="{adi_line:.1f}" y1="{_TOP}" x2="{adi_line:.1f}" '
                f'y2="{_HEIGHT - _BOTTOM}" class="pred-chart__threshold" />',
                f'<line x1="{_LEFT}" y1="{cv2_line:.1f}" x2="{_WIDTH - _RIGHT}" '
                f'y2="{cv2_line:.1f}" class="pred-chart__threshold" />',
                f'<text x="{adi_line:.1f}" y="{_TOP + 12}" class="pred-chart__threshold-label" '
                f'text-anchor="middle">ADI {escape(format_decimal(thresholds.adi))}</text>',
                f'<text x="{_WIDTH - _RIGHT}" y="{cv2_line - 6:.1f}" '
                f'class="pred-chart__threshold-label" '
                f'text-anchor="end">CV² {escape(format_decimal(thresholds.cv2))}</text>',
            ]
        )
    parts.extend(_point(row, x_max, y_max) for row in report.items)
    parts.append(_legend())
    parts.append("</svg>")
    return "".join(parts)


def _span(values: list[float], threshold: float) -> float:
    peak = max([threshold, *values, 1.0])
    return peak * 1.15


def _map_x(adi: float, x_max: float) -> float:
    plot = _WIDTH - _LEFT - _RIGHT
    return _LEFT + (adi / x_max) * plot


def _map_y(cv2: float, y_max: float) -> float:
    plot = _HEIGHT - _TOP - _BOTTOM
    return _TOP + (1 - cv2 / y_max) * plot


def _axes(x_max: float, y_max: float) -> str:
    x0, y0 = _LEFT, _HEIGHT - _BOTTOM
    x1, y1 = _WIDTH - _RIGHT, _TOP
    return (
        f'<line x1="{x0}" y1="{y0}" x2="{x1}" y2="{y0}" class="pred-chart__axis" />'
        f'<line x1="{x0}" y1="{y0}" x2="{x0}" y2="{y1}" class="pred-chart__axis" />'
        f'<text x="{(x0 + x1) / 2:.1f}" y="{y0 + 44:.1f}" class="pred-chart__axis-label" '
        'text-anchor="middle">ADI</text>'
        f'<text x="14" y="{(y0 + y1) / 2:.1f}" class="pred-chart__axis-label" '
        'text-anchor="middle" transform="rotate(-90 14 '
        f'{(y0 + y1) / 2:.1f})">CV²</text>'
        f'<text x="{x0}" y="{y0 + 14}" class="pred-chart__tick">0</text>'
        f'<text x="{x1}" y="{y0 + 14}" class="pred-chart__tick" text-anchor="end">'
        f"{escape(format_decimal(x_max))}</text>"
        f'<text x="{x0 - 6}" y="{y1 + 4}" class="pred-chart__tick" text-anchor="end">'
        f"{escape(format_decimal(y_max))}</text>"
    )


def _point(row: TopologyMetrics, x_max: float, y_max: float) -> str:
    cx = _map_x(row.adi, x_max)
    cy = _map_y(row.cv2, y_max)
    shape, fill = _MARKERS.get(row.sku_class, ("circle", "#2c2f34"))
    label = sku_class_label(row.sku_class)
    title = (
        f"{row.sku_id}, {label}, ADI {format_decimal(row.adi, 3)}, CV² {format_decimal(row.cv2, 3)}"
    )
    inner = f"<title>{escape(title)}</title>"
    r = _RADIUS
    if shape == "square":
        mark = (
            f'<rect x="{cx - r:.1f}" y="{cy - r:.1f}" width="{r * 2:.1f}" '
            f'height="{r * 2:.1f}" fill="{fill}">{inner}</rect>'
        )
    elif shape == "triangle":
        points = f"{cx:.1f},{cy - r:.1f} {cx + r:.1f},{cy + r:.1f} {cx - r:.1f},{cy + r:.1f}"
        mark = f'<polygon points="{points}" fill="{fill}">{inner}</polygon>'
    elif shape == "diamond":
        points = (
            f"{cx:.1f},{cy - r:.1f} {cx + r:.1f},{cy:.1f} "
            f"{cx:.1f},{cy + r:.1f} {cx - r:.1f},{cy:.1f}"
        )
        mark = f'<polygon points="{points}" fill="{fill}">{inner}</polygon>'
    else:
        mark = f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{r}" fill="{fill}">{inner}</circle>'
    return mark


def _legend() -> str:
    chunks = ['<g class="pred-chart__legend">']
    for index, (code, (shape, fill)) in enumerate(_MARKERS.items()):
        x = _LEFT + (index % 2) * 290
        y = _HEIGHT - 30 + (index // 2) * 20
        if shape == "square":
            glyph = f'<rect x="{x:.1f}" y="{y - 8:.1f}" width="8" height="8" fill="{fill}" />'
        elif shape == "triangle":
            glyph = (
                f'<polygon points="{x + 4:.1f},{y - 8:.1f} {x + 8:.1f},{y:.1f} '
                f'{x:.1f},{y:.1f}" fill="{fill}" />'
            )
        elif shape == "diamond":
            glyph = (
                f'<polygon points="{x + 4:.1f},{y - 8:.1f} {x + 8:.1f},{y - 4:.1f} '
                f'{x + 4:.1f},{y:.1f} {x:.1f},{y - 4:.1f}" fill="{fill}" />'
            )
        else:
            glyph = f'<circle cx="{x + 4:.1f}" cy="{y - 4:.1f}" r="4" fill="{fill}" />'
        chunks.append(glyph)
        chunks.append(
            f'<text x="{x + 12:.1f}" y="{y:.1f}" class="pred-chart__legend-label">'
            f"{escape(sku_class_label(code))}</text>"
        )
    chunks.append("</g>")
    return "".join(chunks)
