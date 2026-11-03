"""Exportación a CSV de la evidencia que publicó M3 (RF-CON-06, contrato v2 sección 7).

Solo da formato a lo que el repositorio entregó: no recalcula métricas, no reetiqueta veredictos y
no deduce la significancia. Los valores salen tal cual, de modo que los identificadores coinciden
con los registros internos; los nulos son celdas vacías y la causa de una ausencia va en su propia
columna (``dm_causa``). El formato es CSV RFC 4180 en UTF-8 sin BOM: coma como separador, punto
decimal y ``true``/``false`` para los booleanos.
"""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Iterable, Sequence

from pred_platform.contract import models_v2

# Las columnas son contrato con quien lee el archivo: cambiarlas rompe sus herramientas.
VERDICT_COLUMNS: tuple[str, ...] = (
    "run_id",
    "run_id_m2",
    "ingesta_sha256",
    "sku",
    "sku_class",
    "veredicto",
    "justificacion",
    "familia_campeona",
    "candidato_campeon",
    "n_ventanas",
    "n_min",
    "cobertura",
    "razon_sn",
    "pierde_frente_a_linea_base",
    "comparacion_incompleta",
    "iqr_diferencia_mae",
    "dm_estadistico",
    "dm_p_valor",
    "dm_p_ajustado",
    "dm_alfa",
    "dm_significativa",
    "dm_n_ventanas",
    "dm_horizonte",
    "dm_causa",
    "datos_sinteticos",
    "origen_declarado",
)

EXCLUSION_COLUMNS: tuple[str, ...] = (
    "run_id",
    "run_id_m2",
    "ingesta_sha256",
    "sku",
    "tipo",
    "sku_class",
    "codigo",
    "causa",
    "t_star",
)

LATE_LAUNCH = "lanzamiento_tardio"
ELIGIBILITY = "elegibilidad"


def _cell(value: object) -> str:
    """Una celda: nulo vacío, booleanos ``true``/``false`` y números sin formato regional."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return repr(value)
    return str(value)


def _csv(columns: Sequence[str], rows: Iterable[Sequence[object]]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(columns)
    writer.writerows([_cell(value) for value in row] for row in rows)
    return buffer.getvalue()


def verdicts_csv(run: models_v2.M3Run, items: Iterable[models_v2.M3Verdict]) -> str:
    """Un veredicto por fila, con la corrida M3 y su vínculo con M2 en cada una."""
    return _csv(
        VERDICT_COLUMNS,
        (
            [
                run.run_id_m2 if column == "run_id_m2" else getattr(item, column)
                for column in VERDICT_COLUMNS
            ]
            for item in items
        ),
    )


def exclusions_csv(
    run: models_v2.M3Run,
    late: models_v2.LateLaunches | None,
    eligibility: models_v2.EligibilityExclusions | None,
) -> str:
    """Los productos que no llegaron a un veredicto: lanzados después del corte y excluidos en M2.

    Son los de toda la corrida, no los de una consulta. Un lanzamiento tardío no tiene clase ni
    código: sus celdas van vacías. ``t_star`` es el corte de la corrida, el mismo en cada fila.
    """
    t_star = late.t_star if late is not None else None
    rows: list[list[object]] = [
        [
            run.run_id,
            run.run_id_m2,
            run.ingesta_sha256,
            item.sku,
            LATE_LAUNCH,
            None,
            None,
            item.causa,
            t_star,
        ]
        for item in (late.items if late is not None else [])
    ]
    rows += [
        [
            run.run_id,
            run.run_id_m2,
            run.ingesta_sha256,
            item.sku,
            ELIGIBILITY,
            item.sku_class,
            item.codigo,
            item.causa,
            t_star,
        ]
        for item in (eligibility.items if eligibility is not None else [])
    ]
    return _csv(EXCLUSION_COLUMNS, rows)


def filename(kind: str, run_id: str) -> str:
    """El nombre del archivo: la corrida M3 que lo produjo, limpia para una cabecera HTTP."""
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", run_id)
    return f"pred-{kind}-{safe}.csv"
