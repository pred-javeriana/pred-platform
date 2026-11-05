"""Gráficos SVG generados en el servidor con datos del repositorio.

No clasifica SKU ni recalcula umbrales: dibuja los puntos y las líneas que ya
vienen en el contrato. La clase se distingue por forma y por color (RNF-USA-04).
La serie del campeón conserva los valores publicados y los bloques ya resueltos.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from datetime import date
from xml.sax.saxutils import escape

from pred_platform.app.presentation import (
    family_label,
    format_date,
    format_decimal,
    model_label,
    sku_class_label,
)
from pred_platform.app.resultados_pronostico import ChampionForecastSeries, ForecastPoint
from pred_platform.contract import models_v2
from pred_platform.contract.models import TopologyMetrics, TopologyReport

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

FAMILY_COLORS: dict[str, str] = {
    "foundation": "#6250d6",
    "dl": "#e87ba4",
    "ml": "#eda100",
    "classical": "#008300",
    "seasonal_naive": "#8d9198",
}

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
    interval: tuple[float, float, float | None] | None = None,
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
