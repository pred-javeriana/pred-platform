"""El mapa de todos los productos de una ingesta, sin recalcular su clase de demanda."""

from __future__ import annotations

import math
from xml.sax.saxutils import escape, quoteattr

from pred_platform.app.presentation import format_decimal, sku_class_label
from pred_platform.contract.models import TopologyReport


def demand_scatter_svg(report: TopologyReport) -> str:
    """ADI y CV² sobre ejes logarítmicos; CV² cero se marca en el borde inferior y se declara."""
    width, height = 640, 400
    left, right, top, bottom = 66, 20, 28, 70
    x_min, y_min = 0.9, 0.01
    x_max = max([5, *(row.adi * 1.15 for row in report.items)])
    y_max = max([3, *(row.cv2 * 1.15 for row in report.items)])

    def x(value: float) -> float:
        return left + (math.log(max(value, x_min)) - math.log(x_min)) / math.log(x_max / x_min) * (
            width - left - right
        )

    def y(value: float) -> float:
        return (
            height
            - bottom
            - (math.log(max(value, y_min)) - math.log(y_min))
            / math.log(y_max / y_min)
            * (height - top - bottom)
        )

    parts = [
        f'<svg class="pred-chart__svg pred-demand-scatter" role="img" tabindex="0" '
        f'aria-labelledby="mapa-demanda-titulo mapa-demanda-descripcion" '
        f'viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg">',
        '<title id="mapa-demanda-titulo">Distribución ADI–CV² de la demanda</title>',
        '<desc id="mapa-demanda-descripcion">Cada punto es un producto. '
        "Ambos ejes son logarítmicos. Las líneas punteadas separan los tipos de demanda. "
        "CV² cero se muestra en el borde inferior. Use las flechas para consultar cada punto; "
        "sus cifras también están en Consultar los productos y sus clases.</desc>",
    ]
    for tick in (1, 2, 5, 10, 20, 50, 100, 200, 500, 1000):
        if tick > x_max:
            continue
        px = x(tick)
        parts.append(
            f'<line x1="{px:.2f}" x2="{px:.2f}" y1="{top}" y2="{height - bottom}" '
            'class="pred-scatter-grid"/>'
        )
        parts.append(
            f'<text x="{px:.2f}" y="{height - bottom + 18}" text-anchor="middle" '
            f'class="pred-chart__tick">{format_decimal(tick, 0)}</text>'
        )
    for tick in (0.01, 0.1, 0.49, 1, 10, 100, 1000, 10000):
        if tick > y_max:
            continue
        py = y(tick)
        parts.append(
            f'<line x1="{left}" x2="{width - right}" y1="{py:.2f}" y2="{py:.2f}" '
            'class="pred-scatter-grid"/>'
        )
        parts.append(
            f'<text x="{left - 8}" y="{py + 4:.2f}" text-anchor="end" '
            f'class="pred-chart__tick">{format_decimal(tick, 2 if tick < 1 else 0)}</text>'
        )
    if report.thresholds:
        px, py = x(report.thresholds.adi), y(report.thresholds.cv2)
        parts.extend(
            [
                f'<line x1="{px:.2f}" x2="{px:.2f}" y1="{top}" y2="{height - bottom}" '
                'class="pred-chart__threshold"/>',
                f'<line x1="{left}" x2="{width - right}" y1="{py:.2f}" y2="{py:.2f}" '
                'class="pred-chart__threshold"/>',
            ]
        )
        for code, lx, ly, anchor in (
            ("erratic", left + 8, top + 17, "start"),
            ("lumpy", width - right - 8, top + 17, "end"),
            ("smooth", left + 8, height - bottom - 12, "start"),
            ("intermittent", width - right - 8, height - bottom - 12, "end"),
        ):
            parts.append(
                f'<text x="{lx}" y="{ly}" text-anchor="{anchor}" '
                f'class="pred-scatter-class">{sku_class_label(code)}</text>'
            )
    for row in report.items:
        label = (
            f"{row.sku_id} · {sku_class_label(row.sku_class)} · "
            f"ADI {format_decimal(row.adi, 3)} · CV² {format_decimal(row.cv2, 3)}"
        )
        parts.append(
            f'<circle cx="{x(row.adi):.2f}" cy="{y(row.cv2):.2f}" r="2.6" '
            f'class="pred-scatter-point" data-label={quoteattr(label)}>'
            f"<title>{escape(label)}</title></circle>"
        )
    parts.extend(
        [
            f'<text x="{(left + width - right) / 2}" y="{height - 26}" text-anchor="middle" '
            'class="pred-chart__axis-label">ADI · días entre ventas (escala logarítmica)</text>',
            f'<text x="16" y="{(top + height - bottom) / 2}" text-anchor="middle" '
            f'transform="rotate(-90 16 {(top + height - bottom) / 2})" '
            'class="pred-chart__axis-label">CV² · variabilidad (escala logarítmica)</text>',
            "</svg>",
        ]
    )
    return "".join(parts)
