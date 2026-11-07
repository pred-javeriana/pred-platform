"""Gráficos SVG generados en el servidor con datos del repositorio.

No clasifica SKU ni recalcula umbrales: dibuja los puntos y las líneas que ya
vienen en el contrato. La clase se distingue por forma y por color (RNF-USA-04).
La serie del campeón conserva los valores publicados y los bloques ya resueltos.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from datetime import date
from html import escape as attr_escape
from xml.sax.saxutils import escape

from pred_platform.app.presentation import (
    family_label,
    format_date,
    format_decimal,
    metric_label,
    model_label,
    sku_class_label,
)
from pred_platform.app.resultados_m3 import (
    interval_text,
    p_text,
    percent_text,
    r_interval_text,
    ratio_text,
    wins_text,
)
from pred_platform.app.resultados_pronostico import ChampionForecastSeries, ForecastPoint
from pred_platform.contract import models_v2
from pred_platform.contract.models import TopologyMetrics, TopologyReport
from pred_platform.data.m3_confirmation import M3ClassConfirmation, M3ClassSignificance
from pred_platform.data.repository import M3ReserveBlocks

FAMILY_COLORS = {
    "foundation": "#6250d6",
    "dl": "#e87ba4",
    "ml": "#eda100",
    "classical": "#008300",
    "seasonal_naive": "#8d9198",
}
FAMILY_CHART_ORDER = tuple(FAMILY_COLORS)
OUTCOME_COLORS = ("#3e6b4f", "#c4c7c1", "#a3402f", "#ffffff")

# Tan ancho como una tarjeta a 1280 px: el texto de 11 px se lee a su tamaño y el mapa no deja
# media tarjeta vacía.
_WIDTH = 960
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


_SHAPE_NAMES = {
    "circle": "círculo",
    "square": "cuadrado",
    "triangle": "triángulo",
    "diamond": "rombo",
}


def _shape_names() -> str:
    """«círculo Suave, cuadrado Errática, …»: la forma de cada clase, con su nombre visible."""
    names = [
        f"{_SHAPE_NAMES[shape]} {sku_class_label(code)}" for code, (shape, _) in _MARKERS.items()
    ]
    return f"{', '.join(names[:-1])} y {names[-1]}"


def topology_scatter_svg(report: TopologyReport) -> str:
    """SVG inline con ejes ADI (x) y CV² (y), umbrales y un punto por ítem."""
    thresholds = report.thresholds
    x_max = _span([row.adi for row in report.items], thresholds.adi if thresholds else 0.0)
    y_max = _span([row.cv2 for row in report.items], thresholds.cv2 if thresholds else 0.0)
    title = "Distribución ADI–CV² de la demanda"
    desc = (
        "Cada marca es un SKU del reporte. El eje horizontal es ADI y el vertical es CV². "
        + ("Las líneas de puntos marcan los umbrales del motor. " if thresholds else "")
        + f"Las clases se distinguen por forma: {_shape_names()}."
    )
    parts = [
        f'<svg role="img" viewBox="0 0 {_WIDTH} {_HEIGHT}" width="{_WIDTH}" height="{_HEIGHT}" '
        'xmlns="http://www.w3.org/2000/svg" class="pred-chart__svg pred-topology__svg">',
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


def champion_forecast_svg(series: ChampionForecastSeries) -> str:
    """Dibuja fechas, valores y bloques recibidos; no resuelve cortes ni agrega pronósticos."""
    if series.problem or not series.observed or not series.forecast:
        return ""
    points = (*series.observed, *series.forecast, *series.alt_forecast)
    dates = sorted({p.fecha for p in points})
    # El medio día de margen dibuja completos los extremos de las fechas inclusivas.
    first = date.fromisoformat(dates[0]).toordinal() - 0.5
    last = date.fromisoformat(dates[-1]).toordinal() + 0.5
    # Una fecha en mono de 11 px mide 66 px: el margen derecho deja entera la última. 960 de
    # ancho, como una tarjeta a 1280 px, para que el texto se lea a su tamaño.
    left, right, top, bottom = 68, 920, 42, 286
    values = [p.valor for p in points if p.valor is not None]
    low, high = min(0.0, *values), max(1.0, *values)
    high += (high - low) * 0.1

    def x(fecha: str, edge: float = 0) -> float:
        ordinal = date.fromisoformat(fecha).toordinal() + edge
        return left + (ordinal - first) / (last - first) * (right - left)

    def y(value: float) -> float:
        return bottom - (value - low) / (high - low) * (bottom - top)

    regions = "Sin etiquetas de bloques disponibles. "
    if series.blocks:
        regions = (
            "Selección: fechas objetivo contextuales. Prueba final: fechas objetivo de evaluación. "
            "Los orígenes de embargo se informan en texto; no son una región de fechas objetivo. "
        )
    description = (
        f"SKU {series.sku}. Modelo campeón {model_label(series.candidate or '')}, familia "
        f"{family_label(series.family or '')}. "
        f"Demanda observada (línea continua y círculos) y pronóstico del campeón un día antes "
        f"(h=1, línea discontinua y cuadrados), del {format_date(dates[0])} "
        f"al {format_date(dates[-1])}. "
        "Los valores ausentes interrumpen las líneas. " + regions
    )
    parts = [
        '<svg role="img" viewBox="0 0 960 350" width="960" height="350" '
        'xmlns="http://www.w3.org/2000/svg" class="pred-chart__svg pred-forecast__svg">',
        f"<title>{escape('Demanda y pronóstico del campeón · ' + series.sku)}</title>",
        f"<desc>{escape(description)}</desc>",
    ]
    if series.blocks:
        blocks = series.blocks
        bands = [
            ("Selección", "selection", blocks.selection_dates.start, blocks.selection_dates.end),
            ("Prueba final", "test", blocks.test_dates.start, blocks.test_dates.end),
        ]
        for label, kind, start, end in bands:
            if start is None or end is None:
                continue
            start_x, end_x = max(left, x(start, -0.5)), min(right, x(end, 0.5))
            if end_x <= start_x:
                continue
            parts.extend(
                [
                    f'<rect x="{start_x:.2f}" y="{top}" width="{end_x - start_x:.2f}" '
                    f'height="{bottom - top}" class="pred-forecast__{kind}">'
                    f"<title>{label}: del {escape(format_date(start))} "
                    f"al {escape(format_date(end))}</title></rect>",
                    f'<text x="{(start_x + end_x) / 2:.2f}" y="26" text-anchor="middle" '
                    f'class="pred-chart__tick pred-forecast__label-{kind}">{label}</text>',
                ]
            )
    for value in sorted({low, 0.0, high / 2, high}):
        position = y(value)
        parts.append(
            f'<line x1="{left}" x2="{right}" y1="{position:.2f}" y2="{position:.2f}" '
            'class="pred-chart__axis" />'
            f'<text x="{left - 8}" y="{position + 4:.2f}" text-anchor="end" '
            f'class="pred-chart__tick">{escape(format_decimal(value, 1))}</text>'
        )
    parts.append(
        f'<line x1="{left}" x2="{left}" y1="{top}" y2="{bottom}" '
        'class="pred-chart__axis" />'
        '<text x="16" y="164" transform="rotate(-90 16 164)" text-anchor="middle" '
        'class="pred-chart__axis-label">Demanda</text>'
        f'<text x="{(left + right) / 2:.0f}" y="340" text-anchor="middle" '
        'class="pred-chart__axis-label">Fecha</text>'
    )
    for fecha in sorted({dates[0], dates[len(dates) // 2], dates[-1]}):
        parts.append(
            f'<text x="{x(fecha):.2f}" y="310" text-anchor="middle" '
            f'class="pred-chart__tick">{escape(format_date(fecha))}</text>'
        )
    parts.append(_forecast_line(series.observed, "observed", x, y))
    if series.alt_forecast:
        parts.append(_forecast_line(series.alt_forecast, "alt_forecast", x, y))
    parts.append(_forecast_line(series.forecast, "forecast", x, y))
    parts.append("</svg>")
    return "".join(parts)


def _forecast_line(
    points: tuple[ForecastPoint, ...],
    kind: str,
    x: Callable[[str], float],
    y: Callable[[float], float],
) -> str:
    """Un nuevo tramo tras cada nulo; las marcas permiten ver también puntos aislados."""
    commands: list[str] = []
    markers: list[str] = []
    move = True
    for point in points:
        if point.valor is None:
            move = True
            continue
        cx, cy = x(point.fecha), y(point.valor)
        commands.append(f"{'M' if move else 'L'}{cx:.2f},{cy:.2f}")
        move = False
        title = f"<title>{escape(format_date(point.fecha))}: {format_decimal(point.valor, 3)}"
        if point.origen is not None:
            title += f"; origen {escape(format_date(point.origen))}"
        title += "</title>"
        if kind == "observed":
            mark = f'<circle cx="{cx:.2f}" cy="{cy:.2f}" r="3">{title}</circle>'
        elif kind == "alt_forecast":
            mark = (
                f'<circle cx="{cx:.2f}" cy="{cy:.2f}" r="2.5" '
                f'class="pred-forecast__alt-point">{title}</circle>'
            )
        else:
            mark = f'<rect x="{cx - 3:.2f}" y="{cy - 3:.2f}" width="6" height="6">{title}</rect>'
        markers.append(mark)
    return (
        f'<g class="pred-forecast__{kind}"><path d="{" ".join(commands)}" fill="none" />'
        + "".join(markers)
        + "</g>"
    )


_CHAMPION_LEFT = 180
_CHAMPION_PLOT = 210
_CHAMPION_ROW = 34
_CHAMPION_BAR = 18
_CHAMPION_TOP = 10
_CHAMPION_ROOM = 150


def champion_share_svg(bars: Sequence[tuple[str, float | None, str]], total: int) -> str:
    """Barras horizontales: cada familia con su fracción de los SKU con veredicto.

    Dibuja lo que recibe (familia, fracción y el texto del valor ya formateado) y no cuenta nada.
    El largo es la fracción sobre 100 %, para que las barras se comparen entre sí; el valor va en
    la punta, con el texto en tinta y no en el color de la barra. Una sola serie no lleva leyenda:
    el título la nombra, y la tabla de al lado dice lo mismo.
    """
    if not bars:
        return ""
    height = _CHAMPION_TOP * 2 + _CHAMPION_ROW * len(bars)
    width = _CHAMPION_LEFT + _CHAMPION_PLOT + _CHAMPION_ROOM
    description = (
        f"Fracción de los {total} SKU con veredicto según su familia campeona: "
        + "; ".join(f"{label}, {value or '0 SKU'}" for label, _, value in bars)
        + "."
    )
    parts = [
        f'<svg role="img" viewBox="0 0 {width} {height}" width="{width}" height="{height}" '
        'xmlns="http://www.w3.org/2000/svg" class="pred-chart__svg pred-champion__svg">',
        "<title>Distribución de campeones por familia</title>",
        f"<desc>{escape(description)}</desc>",
        f'<line x1="{_CHAMPION_LEFT}" x2="{_CHAMPION_LEFT}" y1="{_CHAMPION_TOP}" '
        f'y2="{height - _CHAMPION_TOP}" class="pred-chart__axis" />',
    ]
    for index, (label, share, value) in enumerate(bars):
        top = _CHAMPION_TOP + index * _CHAMPION_ROW + (_CHAMPION_ROW - _CHAMPION_BAR) / 2
        middle = top + _CHAMPION_BAR / 2
        length = 0.0 if not share else max(2.0, min(share, 1.0) * _CHAMPION_PLOT)
        parts.append(
            f'<text x="{_CHAMPION_LEFT - 12}" y="{middle + 4:.1f}" text-anchor="end" '
            f'class="pred-champion__label">{escape(label)}</text>'
        )
        if length:
            parts.append(_champion_bar(top, length, f"{label}: {value}"))
        parts.append(
            f'<text x="{_CHAMPION_LEFT + length + 8:.1f}" y="{middle + 4:.1f}" '
            f'class="pred-champion__value">{escape(value)}</text>'
        )
    parts.append("</svg>")
    return "".join(parts)


def _champion_bar(top: float, length: float, tip: str) -> str:
    """Una barra anclada a la base: recta en el eje y con 4 px de radio en el extremo del dato."""
    radius = min(4.0, length / 2)
    x0, x1 = _CHAMPION_LEFT, _CHAMPION_LEFT + length
    y0, y1 = top, top + _CHAMPION_BAR
    path = (
        f"M{x0},{y0:.1f} H{x1 - radius:.1f} A{radius:.1f},{radius:.1f} 0 0 1 {x1:.1f},"
        f"{y0 + radius:.1f} V{y1 - radius:.1f} A{radius:.1f},{radius:.1f} 0 0 1 "
        f"{x1 - radius:.1f},{y1:.1f} H{x0} Z"
    )
    return f'<path d="{path}" class="pred-champion__bar"><title>{escape(tip)}</title></path>'


# ----------------------------------------------------------- gráficos del paso 5: resultados

_MON_ES = (
    "ene",
    "feb",
    "mar",
    "abr",
    "may",
    "jun",
    "jul",
    "ago",
    "sept",
    "oct",
    "nov",
    "dic",
)
_DEMAND_COLOR = "#6b6f76"


def product_forecast_svg(series: ChampionForecastSeries) -> str:
    """Demanda real frente al pronóstico del campeón (y del mejor modelo alternativo si existe)."""
    if series.problem or not series.observed or not series.forecast:
        return ""

    obs_valid = [p for p in series.observed if p.valor is not None]
    if not obs_valid:
        return ""

    t0 = date.fromisoformat(obs_valid[0].fecha).toordinal()
    t1 = date.fromisoformat(obs_valid[-1].fecha).toordinal()
    if t1 <= t0:
        return ""

    width, height = 900, 300
    left, right, top, bottom = 62, 170, 30, 44

    def sx(iso: str) -> float:
        val = date.fromisoformat(iso).toordinal()
        return left + (val - t0) / (t1 - t0) * (width - right - left)

    all_vals = [
        p.valor
        for p in (*series.observed, *series.forecast, *series.alt_forecast)
        if p.valor is not None
    ]
    max_val = max(all_vals) if all_vals else 1.0
    ymax = max_val * 1.08 if max_val > 0 else 1.0

    def sy(val: float) -> float:
        return (height - bottom) - (val / ymax) * (height - bottom - top)

    fcol = FAMILY_COLORS.get(series.family or "", "#6250d6")
    acol = FAMILY_COLORS.get(series.alt_family or "", "#8d9198") if series.alt_family else None

    title = f"Demanda real y pronóstico del producto {series.sku}"
    parts = [
        f'<svg role="img" viewBox="0 0 {width} {height}" width="100%" class="wz-svg" '
        'xmlns="http://www.w3.org/2000/svg" '
        f'aria-label="{escape(title)}">',
        f"<title>{escape(title)}</title>",
    ]

    # Rectángulos sombreados de bloques de reserva
    if series.blocks is not None:
        b_sel = (series.blocks.selection_dates.start, series.blocks.selection_dates.end)
        b_test = (series.blocks.test_dates.start, series.blocks.test_dates.end)
        if b_sel[0] and b_sel[1]:
            x_s0 = max(left, sx(b_sel[0]))
            x_s1 = min(width - right, sx(b_sel[1]))
            if x_s1 > x_s0:
                parts.extend(
                    [
                        f'<rect x="{x_s0:.1f}" y="{top}" width="{x_s1 - x_s0:.1f}" '
                        f'height="{height - bottom - top}" fill="#f1f2ef"/>',
                        f'<text x="{x_s0 + 6:.1f}" y="{top - 8}" class="t-muted">'
                        "Bloque de selección</text>",
                    ]
                )
        if b_test[0] and b_test[1]:
            x_t0 = max(left, sx(b_test[0]))
            x_t1 = min(width - right, sx(b_test[1]))
            if x_t1 > x_t0:
                parts.extend(
                    [
                        f'<rect x="{x_t0:.1f}" y="{top}" width="{x_t1 - x_t0:.1f}" '
                        f'height="{height - bottom - top}" fill="#ebeaf6"/>',
                        f'<text x="{x_t0 + 6:.1f}" y="{top - 8}" class="t-strong">'
                        "Bloque de prueba: aquí se decide el veredicto</text>",
                    ]
                )

    # Rejilla y marcas de escala vertical
    for v in (0.0, ymax / 2.0, ymax):
        y_pos = sy(v)
        parts.extend(
            [
                f'<line x1="{left}" y1="{y_pos:.1f}" x2="{width - right}" y2="{y_pos:.1f}" '
                'class="grid"/>',
                f'<text x="{left - 6}" y="{y_pos + 4:.1f}" class="t-num t-muted" '
                f'text-anchor="end">{escape(format_decimal(v, 0))}</text>',
            ]
        )

    # Líneas de demanda y pronósticos
    def _path(
        pts: Sequence[ForecastPoint], stroke: str, stroke_width: float, dash: str = ""
    ) -> str:
        cmds: list[str] = []
        move = True
        for p in pts:
            if p.valor is None:
                move = True
                continue
            cx, cy = sx(p.fecha), sy(p.valor)
            cmds.append(f"{'M' if move else 'L'}{cx:.1f},{cy:.1f}")
            move = False
        if not cmds:
            return ""
        dash_attr = f' stroke-dasharray="{dash}"' if dash else ""
        return (
            f'<path d="{" ".join(cmds)}" fill="none" stroke="{stroke}" '
            f'stroke-width="{stroke_width}" stroke-linejoin="round"{dash_attr}/>'
        )

    parts.append(_path(series.observed, _DEMAND_COLOR, 1.2))
    if series.alt_forecast and acol:
        parts.append(_path(series.alt_forecast, acol, 2.0, dash="6 3"))
    parts.append(_path(series.forecast, fcol, 2.0))

    # Etiquetas directas a la derecha
    last_obs_val = obs_valid[-1].valor if obs_valid[-1].valor is not None else 0.0
    fc_valid = [p for p in series.forecast if p.valor is not None]
    last_fc_val = fc_valid[-1].valor if fc_valid and fc_valid[-1].valor is not None else 0.0
    champ_lbl = family_label(series.family or "")

    class _LabelLine:
        def __init__(self, y: float, h: float, draw: Callable[[float, float], str]):
            self.y = y
            self.h = h
            self.draw = draw

    def _draw_obs(yy: float, kx: float) -> str:
        return (
            f'<line x1="{kx}" y1="{yy - 4:.1f}" x2="{kx + 14}" y2="{yy - 4:.1f}" '
            f'stroke="{_DEMAND_COLOR}" stroke-width="2"/>'
            f'<text x="{kx + 20}" y="{yy:.1f}" class="t-strong">Demanda real</text>'
        )

    def _draw_fc(yy: float, kx: float) -> str:
        sub_text = f"({escape(champ_lbl)}, campeona)"
        return (
            f'<line x1="{kx}" y1="{yy - 4:.1f}" x2="{kx + 14}" y2="{yy - 4:.1f}" '
            f'stroke="{fcol}" stroke-width="3"/>'
            f'<text x="{kx + 20}" y="{yy:.1f}" class="t-strong">Pronóstico</text>'
            f'<text x="{kx + 20}" y="{yy + 15:.1f}" class="t-muted">{sub_text}</text>'
        )

    labs: list[_LabelLine] = [
        _LabelLine(y=sy(last_obs_val) + 4, h=20, draw=_draw_obs),
        _LabelLine(y=sy(last_fc_val) + 4, h=34, draw=_draw_fc),
    ]

    alt_valid = [p for p in series.alt_forecast if p.valor is not None]
    if alt_valid and acol and series.alt_family:
        last_alt_val = alt_valid[-1].valor if alt_valid[-1].valor is not None else 0.0
        alt_lbl = family_label(series.alt_family)

        def _draw_alt(yy: float, kx: float) -> str:
            return (
                f'<line x1="{kx}" y1="{yy - 4:.1f}" x2="{kx + 14}" y2="{yy - 4:.1f}" '
                f'stroke="{acol}" stroke-width="2" stroke-dasharray="5 3"/>'
                f'<text x="{kx + 20}" y="{yy:.1f}" class="t-strong">Pronóstico</text>'
                f'<text x="{kx + 20}" y="{yy + 15:.1f}" class="t-muted">({escape(alt_lbl)})</text>'
            )

        labs.append(_LabelLine(y=sy(last_alt_val) + 4, h=34, draw=_draw_alt))

    labs.sort(key=lambda item: item.y)
    for i in range(1, len(labs)):
        labs[i].y = max(labs[i].y, labs[i - 1].y + labs[i - 1].h)
    over = labs[-1].y + labs[-1].h - 14 - (height - bottom)
    if over > 0:
        for item in labs:
            item.y -= over
    for item in labs:
        parts.append(item.draw(item.y, width - right + 8))

    # Título vertical de eje Y
    parts.append(
        f'<text class="t-axis" text-anchor="middle" '
        f'transform="translate(12 {(top + height - bottom) / 2:.1f}) rotate(-90)">'
        "Unidades vendidas por día</text>"
    )

    # Fechas mensuales a lo largo del eje X
    d_start = date.fromisoformat(obs_valid[0].fecha)
    d_end = date.fromisoformat(obs_valid[-1].fecha)
    cur_year, cur_month = d_start.year, d_start.month + 1
    if cur_month > 12:
        cur_year += 1
        cur_month -= 12
    while True:
        m_date = date(cur_year, cur_month, 1)
        if m_date > d_end:
            break
        m_lbl = f"{_MON_ES[m_date.month - 1]} {str(m_date.year)[2:]}"
        parts.append(
            f'<text x="{sx(m_date.isoformat()):.1f}" y="{height - bottom + 18}" '
            f'class="t-num t-muted" text-anchor="middle">{m_lbl}</text>'
        )
        cur_month += 2
        if cur_month > 12:
            cur_year += 1
            cur_month -= 12

    # Título horizontal de eje X
    parts.append(
        f'<text x="{(left + width - right) / 2:.1f}" y="{height - 3}" class="t-axis" '
        'text-anchor="middle">Fecha (el pronóstico de cada día se hizo el día anterior)</text>'
    )
    parts.append("</svg>")
    return "".join(parts)


def product_forecast_key_svg(champion_family: str, alt_family: str | None = None) -> str:
    """Clave de marcas sobre el gráfico de demanda y pronóstico."""
    champ_col = FAMILY_COLORS.get(champion_family, "#6250d6")
    champ_lbl = family_label(champion_family)
    alt_item = ""
    if alt_family:
        alt_col = FAMILY_COLORS.get(alt_family, "#8d9198")
        alt_lbl = family_label(alt_family)
        alt_item = (
            '<li><svg width="24" height="14" aria-hidden="true">'
            f'<line x1="1" y1="7" x2="23" y2="7" stroke="{alt_col}" stroke-width="2" '
            'stroke-dasharray="5 3"/></svg>'
            f"pronóstico de {escape(alt_lbl)} (mejor modelo aquí)</li>"
        )
    return (
        '<ul class="wz-legend wz-key" aria-label="Qué es cada marca">'
        f'<li><svg width="24" height="14" aria-hidden="true"><line x1="1" y1="7" x2="23" y2="7" '
        f'stroke="{_DEMAND_COLOR}" stroke-width="2"/></svg>demanda real</li>'
        f'<li><svg width="24" height="14" aria-hidden="true"><line x1="1" y1="7" x2="23" y2="7" '
        f'stroke="{champ_col}" stroke-width="2"/></svg>'
        f"pronóstico de {escape(champ_lbl)} (campeona)</li>"
        f"{alt_item}"
        '<li><svg width="16" height="14" aria-hidden="true">'
        '<rect x="0" y="1" width="16" height="12" fill="#f1f2ef"/></svg>'
        "bloque de selección</li>"
        '<li><svg width="16" height="14" aria-hidden="true">'
        '<rect x="0" y="1" width="16" height="12" fill="#ebeaf6"/></svg>'
        "bloque de prueba</li>"
        "</ul>"
    )


def product_models_svg(
    verdict: models_v2.M3Verdict,
    champion_candidate: str | None,
    interval: tuple[float, float | None, float | None] | None = None,
) -> str:
    """Gráfico de puntos en escala logarítmica para todos los modelos de un producto."""
    if not verdict.modelos_evaluados:
        return ""

    ms = sorted(
        verdict.modelos_evaluados,
        key=lambda m: (m.razon_sn if m.razon_sn is not None else float("inf"), m.candidato_id),
    )
    valid_r = [m.razon_sn for m in ms if m.razon_sn is not None]
    if not valid_r:
        return ""

    lo = max(0.3, min(0.5, *valid_r) * 0.95)
    hi = max(1.6, *valid_r) * 1.1
    if interval and interval[0] > 0:
        lo = min(lo, interval[0] * 0.95)
    if interval and interval[1] is not None:
        hi = max(hi, interval[1] * 1.1)

    width = 760
    left = 230
    right = 150
    row_h = 26
    top = 24
    height = top + len(ms) * row_h + 42

    def sx(val: float) -> float:
        clamped = max(lo, min(hi, val))
        return left + (math.log(clamped) - math.log(lo)) / (math.log(hi) - math.log(lo)) * (
            width - right - left
        )

    title = "Razón de error de cada modelo evaluado en este producto"
    parts = [
        f'<svg role="img" viewBox="0 0 {width} {height}" width="100%" class="wz-svg" '
        'xmlns="http://www.w3.org/2000/svg" '
        f'aria-label="{escape(title)}">',
        f"<title>{escape(title)}</title>",
        f'<text x="{sx(1.0) + 6:.1f}" y="14" class="t-muted">1 = igual que la línea base</text>',
        '<text x="0" y="14" class="t-axis">Modelo</text>',
        f'<text x="{width}" y="14" class="t-axis" text-anchor="end">r</text>',
    ]

    candidate_ticks = (0.5, 1.0, 10.0, 100.0, 1000.0) if hi > 4 else (0.5, 0.75, 1.0, 1.5, 2.0, 3.0)
    ticks = [v for v in candidate_ticks if lo <= v <= hi]
    for v in ticks:
        tick_x = sx(v)
        lbl = format_decimal(v, 2) if v < 10 else str(int(v))
        parts.extend(
            [
                f'<line x1="{tick_x:.1f}" y1="{top - 4}" x2="{tick_x:.1f}" y2="{height - 38}" '
                'class="grid"/>',
                f'<text x="{tick_x:.1f}" y="{height - 23}" class="t-num t-muted" '
                f'text-anchor="middle">{lbl}</text>',
            ]
        )

    ref_x = sx(1.0)
    parts.append(
        f'<line x1="{ref_x:.1f}" y1="{top - 6}" x2="{ref_x:.1f}" y2="{height - 38}" class="ref"/>'
    )
    parts.append(
        f'<text x="{(left + width - right) / 2:.1f}" y="{height - 3}" class="t-axis" '
        'text-anchor="middle">Razón de error frente a la línea base (r, escala logarítmica)</text>'
    )

    for i, m in enumerate(ms):
        y = top + i * row_h + row_h / 2
        is_champ = m.candidato_id == champion_candidate
        col = FAMILY_COLORS.get(m.familia, "#14161a")
        f_lbl = family_label(m.familia)
        m_lbl = "referencia" if m.familia == "seasonal_naive" else model_label(m.modelo)
        strong_cls = ' class="t-strong"' if is_champ else ""
        parts.append(
            f'<text x="0" y="{y + 4:.1f}"{strong_cls}>{escape(f_lbl)} '
            f'<tspan class="t-muted">({escape(m_lbl)})</tspan></text>'
        )

        if is_champ and interval and interval[0] is not None:
            x_lo = sx(max(lo, interval[0]))
            x_hi = sx(min(hi, interval[1] if interval[1] is not None else hi))
            parts.append(
                f'<line x1="{x_lo:.1f}" y1="{y:.1f}" x2="{x_hi:.1f}" y2="{y:.1f}" class="ci" '
                f'stroke="{col}"/>'
            )

        if m.razon_sn is not None:
            if m.razon_sn > hi:
                parts.extend(
                    [
                        f'<path class="mk" d="M{sx(hi) - 9:.1f},{y - 6:.1f} l9,6 l-9,6 z" '
                        f'fill="{col}"/>',
                        f'<text x="{sx(hi) - 14:.1f}" y="{y + 4:.1f}" class="t-muted" '
                        'text-anchor="end" style="font-size:11px">fuera de escala</text>',
                    ]
                )
            else:
                rad = 7.0 if is_champ else 5.5
                stroke_c = "#14161a" if is_champ else "#fff"
                stroke_w = 2.0 if is_champ else 1.5
                parts.append(
                    f'<circle class="mk" cx="{sx(m.razon_sn):.1f}" cy="{y:.1f}" r="{rad}" '
                    f'fill="{col}" stroke="{stroke_c}" stroke-width="{stroke_w}"/>'
                )
            val_text = f"{format_decimal(m.razon_sn, 3)}{' · campeona' if is_champ else ''}"
            val_cls = "t-num-strong" if is_champ else "t-num"
            parts.append(
                f'<text x="{width}" y="{y + 4:.1f}" class="{val_cls}" text-anchor="end">'
                f"{escape(val_text)}</text>"
            )

    parts.append("</svg>")
    return "".join(parts)


def product_models_legend_svg() -> str:
    """Leyenda de colores por familia para el gráfico de modelos."""
    items = (
        ("foundation", "Fundacional", "Chronos-2"),
        ("dl", "Aprendizaje profundo", "MLP"),
        ("ml", "Aprendizaje automático", "LightGBM"),
        ("classical", "Clásica", "SARIMA"),
        ("seasonal_naive", "Línea base estacional", ""),
    )
    chunks = ['<ul class="wz-legend wz-legend--fam" aria-label="Familias">']
    for fam_key, label, model_hint in items:
        col = FAMILY_COLORS[fam_key]
        hint = f' <span class="wz-muted">({model_hint})</span>' if model_hint else ""
        chunks.append(
            f'<li tabindex="0"><span class="wz-fk" style="background:{col}" aria-hidden="true">'
            f"</span>{escape(label)}{hint}</li>"
        )
    chunks.append("</ul>")
    return "".join(chunks)


def product_models_key_svg() -> str:
    """Clave de marcas sobre el gráfico de modelos."""
    return (
        '<ul class="wz-legend wz-key" aria-label="Qué es cada marca">'
        '<li><svg width="14" height="14" aria-hidden="true">'
        '<circle cx="7" cy="7" r="5" fill="#14161a"/></svg>'
        "razón de cada modelo</li>"
        '<li><svg width="16" height="16" aria-hidden="true">'
        '<circle cx="8" cy="8" r="6" fill="#fff" stroke="#14161a" stroke-width="2"/></svg>'
        "campeona</li>"
        '<li><svg width="24" height="14" aria-hidden="true">'
        '<line x1="1" y1="7" x2="23" y2="7" stroke="#14161a" stroke-width="2.5"/></svg>'
        "intervalo de 95 % de la campeona</li>"
        '<li><svg width="10" height="16" aria-hidden="true">'
        '<line x1="5" y1="1" x2="5" y2="15" stroke="#4a4e55" stroke-width="1.25" '
        'stroke-dasharray="4 3"/></svg>1 = mismo error que la línea base</li>'
        "</ul>"
    )


# Resultados: elementos comunes. Las cifras exactas quedan en data-* y en la vista de tabla.
def _result_svg(width: int, height: int, title: str, body: str, *, mini: bool = False) -> str:
    return (
        f'<svg class="pred-result-svg{" pred-result-svg--mini" if mini else ""}" '
        f'xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" role="img" aria-label="{attr_escape(title)}">'
        f"<title>{escape(title)}</title>{body}</svg>"
    )


def _text(x: float, y: float, value: str, kind: str = "", anchor: str = "start") -> str:
    return (
        f'<text x="{x:.2f}" y="{y:.2f}" class="{kind}" text-anchor="{anchor}">'
        f"{escape(value)}</text>"
    )


def _line(x1: float, y1: float, x2: float, y2: float, kind: str = "grid", color: str = "") -> str:
    return (
        f'<line x1="{x1:.2f}" y1="{y1:.2f}" x2="{x2:.2f}" y2="{y2:.2f}" '
        f'class="{kind}"' + (f' style="stroke:{color}"' if color else "") + "/>"
    )


def _hit(
    x: float,
    y: float,
    width: float,
    height: float,
    value: str,
    label: str,
    detail: str = "",
    *,
    family: str = "",
) -> str:
    description = f"{label}: {value}. {detail}"
    return (
        f'<rect class="pred-chart-hit" x="{x:.2f}" y="{y:.2f}" width="{width:.2f}" '
        f'height="{height:.2f}" tabindex="0" role="img" aria-label="{attr_escape(description)}" '
        f'data-tip="{attr_escape(value)}" data-label="{attr_escape(label)}" '
        f'data-detail="{attr_escape(detail)}"'
        + (f' data-family="{family}"' if family else "")
        + "/>"
    )


def _log_domain(
    values: Sequence[float | None], low: float = 0.7, high: float = 1.03
) -> tuple[float, float]:
    finite = [v for v in values if v is not None and v > 0 and math.isfinite(v)]
    return min([low, *finite]) * 0.98, max([high, *finite]) * 1.02


def _log_scale(low: float, high: float, left: float, right: float) -> Callable[[float], float]:
    def position(value: float) -> float:
        if value <= 0:
            return left
        if not math.isfinite(value):
            return right
        return left + (math.log(value) - math.log(low)) / (math.log(high) - math.log(low)) * (
            right - left
        )

    return position


def _result_axis(
    sx: Callable[[float], float],
    low: float,
    high: float,
    top: int,
    bottom: int,
    left: int,
    right: int,
    title: str,
    *,
    reference: float | None = 1,
    log: bool = True,
) -> str:
    ticks = (
        [
            v
            for v in (
                0.25,
                0.4,
                0.5,
                0.6,
                0.7,
                0.75,
                0.8,
                0.85,
                0.9,
                0.95,
                1,
                1.2,
                1.5,
                2,
                3,
                5,
                10,
            )
            if low <= v <= high
        ]
        if log
        else [low, (low + high) / 2, high]
    )
    # No amontonar etiquetas cuando el dominio cubre varios órdenes de magnitud.
    chosen: list[float] = []
    for value in ticks:
        if not chosen or sx(value) - sx(chosen[-1]) >= 44:
            chosen.append(value)
    body = "".join(
        _line(sx(v), top, sx(v), bottom)
        + _text(sx(v), bottom + 17, format_decimal(v, 2), "num muted", "middle")
        for v in chosen
    )
    if reference is not None and low <= reference <= high:
        body += _line(sx(reference), top, sx(reference), bottom, "reference")
    body += _text((left + right) / 2, bottom + 39, title, "axis-label", "middle")
    return body


def _estimate(
    sx: Callable[[float], float],
    y: float,
    value: float | None,
    lower: float | None,
    upper: float | None,
    family: str,
    *,
    champion: bool = False,
) -> str:
    color = FAMILY_COLORS.get(family, "#2c2f34")
    body = ""
    if lower is not None and upper is not None:
        body += _line(sx(lower), y, sx(upper), y, "interval", color)
    if value is None:
        return body
    x = sx(value)
    if value == 0 or math.isinf(value):
        direction = -1 if value == 0 else 1
        body += f'<path d="M{x:.2f},{y:.2f} l{-direction * 8},-5 v10 z" fill="{color}"/>'
    else:
        body += (
            f'<circle cx="{x:.2f}" cy="{y:.2f}" r="{7 if champion else 5.5}" '
            f'fill="{color}" stroke="{"#2c2f34" if champion else "#fff"}" '
            f'stroke-width="{2 if champion else 1.5}"/>'
        )
    return body


def selection_svg(rows: Sequence[models_v2.M3Category]) -> str:
    """Medianas de selección publicadas por tipo y familia, sobre una escala común."""
    if not rows:
        return ""
    width, left, right = 820, 248, 704
    low, high = _log_domain([v for row in rows for v in row.medianas_r.values()])
    sx = _log_scale(low, high, left, right)
    height = 68 + sum(38 + len(row.medianas_r) * 27 for row in rows)
    body = _text(0, 18, "Tipo / familia", "muted") + _text(width, 18, "Mediana r", "muted", "end")
    body += _result_axis(
        sx, low, high, 30, height - 48, left, right, "Razón de error r (escala logarítmica)"
    )
    y = 30
    for row in rows:
        label = sku_class_label(row.sku_class)
        body += _text(
            0,
            y + 17,
            f"{label} · {len(row.skus_comparables):,} productos comparables".replace(",", "."),
            "group",
        )
        y += 32
        for family, value in sorted(row.medianas_r.items(), key=lambda item: item[1]):
            champion = family == row.familia_campeona
            marks = _text(12, y + 4, family_label(family), "strong" if champion else "")
            marks += _estimate(sx, y, value, None, None, family, champion=champion)
            marks += _text(
                width, y + 4, ratio_text(value) + (" · campeona" if champion else ""), "num", "end"
            )
            marks += _hit(
                0,
                y - 13,
                width,
                26,
                ratio_text(value),
                f"{family_label(family)} · {label}",
                f"{len(row.skus_comparables)} productos comparables. "
                + ("Campeona de este tipo." if champion else "Bloque de selección."),
                family=family,
            )
            body += f'<g data-family="{family}">{marks}</g>'
            y += 27
        y += 6
    return _result_svg(
        width, height, "Mediana de r por familia y tipo en el bloque de selección", body
    )


def confirmation_svg(row: M3ClassConfirmation) -> str:
    """Mediana e intervalo de la campeona frente a la base y cada competidora, sin recalcular."""
    entries = [
        (
            "seasonal_naive",
            row.efecto_mediana_r,
            row.efecto_ic_inferior,
            row.efecto_ic_superior,
            f"{row.efecto_n_skus} productos; p IUT {p_text(row.p_iut)}; "
            f"p Holm {p_text(row.p_holm)}",
        )
    ]
    entries += [
        (
            r.competidora,
            r.mediana_razon,
            r.ic_mediana_inferior,
            r.ic_mediana_superior,
            f"Victorias: {wins_text(r.n_victorias, r.n_skus)}; "
            f"p Wilcoxon {p_text(r.p_wilcoxon)}; p IUT {p_text(r.p_iut)}",
        )
        for r in sorted(
            row.comparaciones,
            key=lambda item: (item.mediana_razon is None, item.mediana_razon or 0),
        )
    ]
    width, left, right = 400, 142, 345
    low, high = _log_domain([v for _, med, lo, hi, _ in entries for v in (med, lo, hi)])
    sx = _log_scale(low, high, left, right)
    height = 78 + 32 * len(entries)
    body = _text(0, 15, "Frente a", "muted") + _text(width, 15, "Mediana", "muted", "end")
    body += _result_axis(sx, low, high, 26, height - 48, left, right, "Razón de errores (log)")
    for index, (family, med, lo, hi, detail) in enumerate(entries):
        y = 42 + index * 32
        name = (
            "Línea base"
            if family == "seasonal_naive"
            else family_label(family).replace("Aprendizaje", "Aprend.")
        )
        marks = _text(0, y + 4, name)
        marks += _estimate(sx, y, med, lo, hi, family)
        marks += _text(width, y + 4, ratio_text(med), "num", "end")
        label = (
            f"{family_label(row.familia_campeona)} frente a {family_label(family)} · "
            f"{sku_class_label(row.sku_class)}"
        )
        marks += _hit(
            0,
            y - 15,
            width,
            30,
            ratio_text(med),
            label,
            f"Intervalo: {interval_text(lo, hi) or 'no publicado'}. {detail}",
            family=family,
        )
        body += f'<g data-family="{family}">{marks}</g>'
    return _result_svg(width, height, f"Confirmación en {sku_class_label(row.sku_class)}", body)


def reserve_timeline_svg(blocks: M3ReserveBlocks, cut: str | None) -> str:
    """Fechas objetivo, distintas de los orígenes y del embargo, a la escala publicada."""
    selection, test = blocks.selection_dates, blocks.test_dates
    total = selection.count + test.count
    if not total:
        return ""
    width, start = 820, 230
    split = start + selection.count / total * (width - start)
    body = _text(0, 16, "Bloque / uso", "muted")
    body += '<rect x="0" y="40" width="212" height="36" fill="#e4e5e1"/>'
    body += '<path d="M210,36 l8,8 l-8,8 l8,8 l-8,8 l8,8" fill="none" stroke="#8d9198"/>'
    body += _text(12, 63, "Historia: entrena y ajusta", "strong")
    body += _text(0, 100, "Comprimida, no a escala", "muted")
    if cut:
        body += _text(start, 28, "Corte: " + format_date(cut), "muted")
    for x, end, block, label, color in (
        (float(start), split, selection, "Selección", "#cfd8e0"),
        (split, float(width), test, "Prueba", "#1b7a8c"),
    ):
        body += f'<rect x="{x:.2f}" y="40" width="{end - x:.2f}" height="36" fill="{color}"/>'
        body += _text(
            x + 10, 63, f"{label} · {block.count} días", "on" if label == "Prueba" else "strong"
        )
        body += _text(x + 2, 100, format_date(block.start) if block.start else "Sin fecha", "muted")
        body += _hit(
            x,
            36,
            end - x,
            44,
            f"{block.count} días",
            "Bloque de " + label.lower(),
            f"Del {format_date(block.start) if block.start else '-'} "
            f"al {format_date(block.end) if block.end else '-'}",
        )
    body += _text(width, 118, format_date(test.end) if test.end else "Sin fecha", "muted", "end")
    body += _text(width / 2, 143, "Fechas objetivo de la reserva", "axis-label", "middle")
    return _result_svg(width, 156, "Historia, selección y prueba: fechas objetivo", body)


def significance_svg(
    rows: Sequence[models_v2.M3Category], significance: Sequence[M3ClassSignificance]
) -> str:
    """Cuatro partes sobre todos los productos con veredicto, incluida la ausencia de prueba."""
    width, left, right = 820, 120, 650
    height = 70 + len(rows) * 48
    by_class = {r.sku_class: r for r in significance}
    body = _text(0, 15, "Tipo", "muted") + _text(width, 15, "Mejora / probados", "muted", "end")
    for index, category in enumerate(rows):
        row = by_class.get(category.sku_class)
        y = 30 + index * 48
        body += _text(0, y + 18, sku_class_label(category.sku_class), "strong")
        if row is None:
            body += _text(left, y + 18, "Sin prueba publicada", "muted")
            continue
        total = sum(category.conteos.model_dump().values())
        if row.n_probados > total:
            body += _text(
                left, y + 18, "Los conteos publicados no coinciden; ver las cifras.", "muted"
            )
            continue
        parts = (
            row.n_mejores,
            row.n_probados - row.n_mejores - row.n_peores,
            row.n_peores,
            total - row.n_probados,
        )
        x = float(left)
        for count, color, label in zip(
            parts,
            OUTCOME_COLORS,
            (
                "Mejora verificada",
                "Sin diferencia demostrada",
                "Peor que la línea base",
                "Sin prueba",
            ),
            strict=True,
        ):
            length = count / total * (right - left) if total else 0
            if count:
                body += (
                    f'<rect x="{x:.2f}" y="{y}" width="{length:.2f}" height="26" '
                    f'fill="{color}" class="stack-part"/>'
                )
                if length > 43:
                    body += _text(
                        x + 7,
                        y + 18,
                        f"{count:,}".replace(",", "."),
                        "num on" if color in (OUTCOME_COLORS[0], OUTCOME_COLORS[2]) else "num",
                    )
                body += _hit(
                    x,
                    y - 3,
                    max(length, 2),
                    32,
                    str(count),
                    f"{label} · {sku_class_label(category.sku_class)}",
                    f"{percent_text(count / total)} de {total} productos con veredicto",
                )
            x += length
        body += _text(
            width,
            y + 12,
            percent_text(row.n_mejores / row.n_probados) if row.n_probados else "Sin prueba",
            "strong",
            "end",
        )
        body += _text(width, y + 28, f"{row.n_mejores} / {row.n_probados}", "num muted", "end")
    body += _text(left, height - 26, "0 %", "num muted") + _text(
        right, height - 26, "100 %", "num muted", "end"
    )
    body += _text(
        (left + right) / 2, height - 6, "Productos con veredicto (%)", "axis-label", "middle"
    )
    return _result_svg(width, height, "Resultado de la prueba por tipo de demanda", body)


def profile_svg(rows: Sequence[models_v2.M3PortfolioRow], metric: str, sku_class: str) -> str:
    """Mediana y cuartiles de la cartera publicada, con eje lineal para errores con signo."""
    if not rows:
        return ""
    width, left, right = 400, 146, 342
    values = [
        v
        for row in rows
        for v in (row.mediana, row.p25, row.p75)
        if v is not None and math.isfinite(v)
    ]
    if metric == "razon_sn":
        low, high = _log_domain(values, 0.6, 1.4)
        sx = _log_scale(low, high, left, right)
    else:
        low, high = min([0.0, *values]), max([1.0, *values])
        high += (high - low) * 0.05

        def sx(v: float, /) -> float:
            return left + (v - low) / (high - low) * (right - left)

    height = 72 + len(rows) * 28
    body = _text(0, 15, "Familia", "muted") + _text(width, 15, "Mediana", "muted", "end")
    reference = 1 if metric in ("razon_sn", "mase") else 0 if metric == "me" else None
    body += _result_axis(
        sx,
        low,
        high,
        25,
        height - 48,
        left,
        right,
        metric_label(metric),
        reference=reference,
        log=metric == "razon_sn",
    )
    for index, row in enumerate(sorted(rows, key=lambda r: FAMILY_CHART_ORDER.index(r.familia))):
        y = 40 + index * 28
        marks = _text(0, y + 4, family_label(row.familia).replace("Aprendizaje", "Aprend."))
        marks += _estimate(sx, y, row.mediana, row.p25, row.p75, row.familia)
        value = (
            ratio_text(row.mediana)
            if metric == "razon_sn"
            else (format_decimal(row.mediana, 3) if row.mediana is not None else "-")
        )
        marks += _text(width, y + 4, value, "num", "end")
        detail = (
            f"Cuartiles: {format_decimal(row.p25, 3) if row.p25 is not None else '-'} a "
            f"{format_decimal(row.p75, 3) if row.p75 is not None else '-'}. "
            f"{row.n_skus} productos; {row.n_instancias} instancias; "
            f"{row.n_no_calculables} no calculables."
        )
        marks += _hit(
            0,
            y - 13,
            width,
            26,
            value,
            f"{family_label(row.familia)} · {sku_class_label(sku_class)}",
            detail,
            family=row.familia,
        )
        body += f'<g data-family="{row.familia}">{marks}</g>'
    return _result_svg(
        width, height, f"Perfil de {metric_label(metric)} en {sku_class_label(sku_class)}", body
    )


def histogram_svg(
    rows: Sequence[models_v2.M3Verdict], sku_class: str
) -> tuple[str, list[tuple[str, tuple[int, int, int, int]]]]:
    """Cuenta filas publicadas en intervalos logarítmicos; los extremos no se descartan.

    Cada producto con r publicado cuenta una vez: mejora verificada, sin diferencia demostrada,
    peor o sin prueba (la prueba no se hizo y el veredicto publica la causa).
    """
    low, high, count = 0.4, 2.5, 26
    bins = [[0, 0, 0, 0] for _ in range(count + 2)]
    for row in rows:
        if row.sku_class != sku_class or row.razon_sn is None:
            continue
        value = row.razon_sn
        index = (
            0
            if value < low
            else count + 1
            if value >= high
            else 1
            + min(count - 1, int((math.log(value) - math.log(low)) / math.log(high / low) * count))
        )
        group = (
            3
            if row.dm_causa is not None
            else 0
            if row.dm_significativa and row.dm_estadistico is not None and row.dm_estadistico < 0
            else 2
            if row.dm_significativa and row.dm_estadistico is not None and row.dm_estadistico > 0
            else 1
        )
        bins[index][group] += 1
    width, height, left, right, bottom, top = 400, 220, 46, 390, 164, 28
    maximum = max(1, *(sum(b) for b in bins))
    band = (right - left) / len(bins)
    body = _text(left, 14, "Productos", "axis-label")
    for v in sorted({0, maximum // 2, maximum}):
        y = bottom - v / maximum * (bottom - top)
        body += _line(left, y, right, y) + _text(left - 6, y + 4, str(v), "num muted", "end")
    table = []
    for index, values in enumerate(bins):
        lo = low * (high / low) ** ((index - 1) / count)
        hi = low * (high / low) ** (index / count)
        label = (
            "r < 0,4"
            if index == 0
            else "r ≥ 2,5"
            if index == count + 1
            else f"{format_decimal(lo, 3)} ≤ r < {format_decimal(hi, 3)}"
        )
        table.append((label, (values[0], values[1], values[2], values[3])))
        y = float(bottom)
        for n, color in zip(values, OUTCOME_COLORS, strict=True):
            h = n / maximum * (bottom - top)
            y -= h
            if n:
                outline = ' class="stack-part"' if color == OUTCOME_COLORS[3] else ""
                body += (
                    f'<rect x="{left + index * band + 0.5:.2f}" y="{y:.2f}" '
                    f'width="{band - 1:.2f}" height="{h:.2f}" fill="{color}"{outline}/>'
                )
        if sum(values):
            body += _hit(
                left + index * band,
                top,
                band,
                bottom - top,
                f"{sum(values)} productos",
                f"{label} · {sku_class_label(sku_class)}",
                f"{values[0]} con mejora verificada; {values[1]} sin diferencia demostrada; "
                f"{values[2]} peores; {values[3]} sin prueba",
            )
    sx = _log_scale(low, high, left + band, right - band)
    body += _line(sx(1), top, sx(1), bottom, "reference")
    for value in (0.5, 0.75, 1, 1.5, 2):
        body += _text(sx(value), bottom + 17, format_decimal(value, 2), "num muted", "middle")
    body += _text(left, bottom + 32, "<0,4", "muted") + _text(
        right, bottom + 32, "≥2,5", "muted", "end"
    )
    body += _text(
        (left + right) / 2,
        height - 4,
        "Razón r frente a la línea base (log)",
        "axis-label",
        "middle",
    )
    return _result_svg(
        width, height, f"Distribución de r en {sku_class_label(sku_class)}", body
    ), table


def _spread_text(values: Sequence[float]) -> str:
    """La distancia entre los valores publicados; un ∞ aparte de un valor finito la hace ∞."""
    if len(values) < 2:
        return "-"
    low, high = min(values), max(values)
    if low == high:
        return format_decimal(0.0, 4)
    return "∞" if math.isinf(high) else format_decimal(high - low, 4)


def stability_svg(
    rows: Sequence[tuple[str, Sequence[tuple[int, float | None, str | None]]]], title: str
) -> str:
    """La distancia entre valores publicados, sin umbral de estabilidad."""
    width, left, right = 820, 136, 530
    low, high = _log_domain([value for _, values in rows for _, value, _ in values], 0.72, 0.93)
    sx = _log_scale(low, high, left, right)
    height = 76 + 36 * len(rows)
    body = (
        _text(0, 16, "Tipo", "muted")
        + _text(width - 82, 16, "r por semilla", "muted", "end")
        + _text(width, 16, "Rango", "muted", "end")
    )
    body += _result_axis(
        sx,
        low,
        high,
        26,
        height - 48,
        left,
        right,
        "Razón mediana (escala logarítmica)",
        reference=None,
    )
    for index, (label, values) in enumerate(rows):
        y = 42 + index * 36
        body += _text(0, y + 4, label, "strong")
        present = [v for _, v, _ in values if v is not None]
        if present:
            body += _line(sx(min(present)), y, sx(max(present)), y, "interval", "#2c2f34")
        for seed, value, family in values:
            if value is None:
                continue
            color = FAMILY_COLORS.get(family or "", "#8d9198")
            x = sx(value)
            if value == 0 or math.isinf(value):
                # Fuera de la escala logarítmica: una flecha al borde, una por semilla.
                direction = -1 if value == 0 else 1
                nudge = -direction * seed * 9
                body += (
                    f'<path d="M{x + nudge:.2f},{y:.2f} l{-direction * 8},-5 v10 z" '
                    f'fill="{color}" stroke="#2c2f34" stroke-width="1"/>'
                )
            elif seed == 1:
                body += (
                    f'<rect x="{x - 6:.2f}" y="{y - 6:.2f}" width="12" height="12" '
                    f'fill="none" stroke="{color}" stroke-width="2"/>'
                )
            elif seed == 2:
                body += f'<path d="M{x:.2f},{y - 7:.2f} l7,7 l-7,7 l-7,-7 z" fill="{color}"/>'
            else:
                body += f'<circle cx="{x:.2f}" cy="{y:.2f}" r="5" fill="{color}"/>'
        value_text = " / ".join(ratio_text(value) for _, value, _ in values)
        range_text = _spread_text(present)
        body += _text(width - 82, y + 4, value_text, "num", "end") + _text(
            width, y + 4, range_text, "num strong", "end"
        )
        body += _hit(
            0,
            y - 16,
            width,
            32,
            "Rango " + range_text,
            label + " · " + title,
            "; ".join(
                f"Semilla {seed}: {ratio_text(value)} "
                f"({family_label(family) if family else 'sin campeona'})"
                for seed, value, family in values
            ),
        )
    return _result_svg(width, height, title, body)


def product_ratio_svg(
    value: float | None,
    lower: float | None = None,
    upper: float | None = None,
    tail: float | None = None,
    *,
    result: str = "sin_diferencia",
) -> str:
    """r y su intervalo publicado en una línea; los valores fuera de escala se señalan."""
    if value is None:
        return ""
    low, high = 0.4, 2.5
    sx = _log_scale(low, high, 8, 142)

    def clamp(v: float) -> float:
        return max(low, min(high, v))

    color = (
        OUTCOME_COLORS[0]
        if result == "mejora"
        else OUTCOME_COLORS[2]
        if result == "peor"
        else "#8d9198"
    )
    body = _line(sx(1), 1, sx(1), 23, "reference")
    if lower is not None:
        if upper is not None:
            body += _line(sx(clamp(lower)), 12, sx(clamp(upper)), 12, "interval", color)
        else:
            body += _line(sx(clamp(lower)), 12, sx(high), 12, "interval", color)
    if tail is not None:
        body += _line(sx(clamp(tail)), 12, sx(high), 12, "interval", color)
    x = sx(clamp(value))
    if value < low or value > high:
        direction = -1 if value < low else 1
        body += f'<path d="M{x:.2f},12 l{-direction * 6},-5 v10 z" fill="{color}"/>'
    else:
        body += f'<circle cx="{x:.2f}" cy="12" r="4" fill="{color}"/>'
    detail = (
        f"r = {ratio_text(value)}. Intervalo sin ajuste BH: {r_interval_text(lower, upper, tail)}"
    )
    if value < low or value > high:
        detail += "; fuera de la escala 0,4 a 2,5"
    body += _hit(0, 0, 150, 24, ratio_text(value), "Razón frente a la línea base", detail)
    return _result_svg(150, 24, detail, body, mini=True)
