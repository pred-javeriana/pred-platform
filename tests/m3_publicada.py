"""A run published by M3, written the way the engine writes it, for tests of the M4 read.

``publish`` inserts one M3 run into a migrated platform database: the run with its frozen policy,
every table M3 fills (SKUs, units, forecasts, metrics, categories, verdicts, portfolio and
failures), the cut, the late-launch exclusions and the observed reserve demand. The rows follow
 the engine's rules (a 64-character ingest
SHA, the five verdicts with their HLN-DM test or its cause, categories whose counts match their
verdicts) so a read that validates them as contract v2 has something real to validate.
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from contextlib import closing
from pathlib import Path
from typing import Literal

VERDICTS = ("FALLO_TECNICO", "NO_EVALUABLE", "EVIDENCIA_INSUFICIENTE", "VALIDADO", "EXPLORATORIO")
LATE = "lanzado despues del corte"
Origin = Literal["real", "sintetico", "desconocido"]
# The two published bits of each origin, and the cause M3 gives for not testing it.
_ORIGINS: dict[str, tuple[int, int, str | None]] = {
    "real": (0, 1, None),
    "sintetico": (1, 1, "datos_sinteticos"),
    "desconocido": (0, 0, "origen_desconocido"),
}
POLICY = {
    "version": "3.4.3-inicial",
    "n_min": 30,
    "n_min_dm": 30,
    "alfa_dm": 0.05,
    "tolerancia_empate": 0.01,
    "orden_simplicidad": ["seasonal_naive", "classical", "ml", "dl", "foundation"],
}


def identity(configuracion: str = "config-1", ingesta_ref: str = "ingesta-1") -> str:
    """The canonical JSON M3 stores as a run's identity."""
    return json.dumps(
        {
            "codigo_version": "f8678bf",
            "configuracion_id": configuracion,
            "ingesta_ref_m1": ingesta_ref,
            "t_corte_reserva": "2026-03-13",
            "version_metricas": "3.4.1-inicial",
            "version_politica": POLICY["version"],
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def _verdict(
    run_id: str,
    sku: str,
    sku_class: str,
    veredicto: str,
    *,
    untested: str | None,
    extra: list[tuple[str, str, float | None]],
    significant: bool = False,
) -> tuple[tuple[object, ...], str | None]:
    """The ``m3_veredictos`` row of one SKU and its champion candidate."""
    baseline = f"{sku}/seasonal_naive/seasonal_naive"
    champion: str | None = f"{sku}/classical/sarima"
    windows, coverage, ratio, loses, iqr = 40, 1.0, 0.8, 0, 0.5
    test: tuple[object, ...] = (40, 7, -2.1, 0.04, 0.08, 0.05, 0, None)
    if significant:
        test = (40, 7, -2.9, 0.004, 0.02, 0.05, 1, None)
    if veredicto == "EXPLORATORIO":
        ratio, loses = 1.1, 1
        test = (40, 7, 0.4, 0.6, 0.6, 0.05, 0, None)
    elif veredicto == "EVIDENCIA_INSUFICIENTE":
        windows, coverage, ratio, loses, iqr = 12, 0.3, None, None, None
        test = (12, 7, None, None, None, None, None, f"veredicto:{veredicto}")
    elif veredicto == "NO_EVALUABLE":
        windows, coverage, ratio, loses, iqr = 0, 0.0, None, None, None
        test = (0, 0, None, None, None, None, None, f"veredicto:{veredicto}")
    elif veredicto == "FALLO_TECNICO":
        champion = None
        windows, coverage, ratio, loses, iqr = 0, 0.0, None, None, None
        test = (0, 0, None, None, None, None, None, f"veredicto:{veredicto}")
    if untested is not None and test[-1] is None:
        test = (*test[:2], None, None, None, None, None, untested)
    models = [
        {
            "candidato_id": baseline,
            "familia": "seasonal_naive",
            "modelo": "seasonal_naive",
            "razon_sn": 1.0,
        },
    ]
    if champion is not None:
        models.append(
            {
                "candidato_id": champion,
                "familia": "classical",
                "modelo": "sarima",
                "razon_sn": ratio,
            }
        )
    models += [
        {
            "candidato_id": f"{sku}/{familia}/{modelo}",
            "familia": familia,
            "modelo": modelo,
            "razon_sn": r,
        }
        for familia, modelo, r in extra
    ]
    justification = {
        "VALIDADO": "VALIDADO: datos reales, ventanas suficientes y r < 1 (descriptivo)",
        "EXPLORATORIO": f"EXPLORATORIO: r={ratio} >= 1 (resultado adverso)",
        "EVIDENCIA_INSUFICIENTE": "EVIDENCIA_INSUFICIENTE: 12 ventanas validas (< n_min=30)",
        "NO_EVALUABLE": "NO_EVALUABLE: no hay observaciones validas en la reserva",
        "FALLO_TECNICO": "FALLO_TECNICO: ningun candidato produjo pronosticos validos",
    }[veredicto]
    row = (
        run_id,
        sku,
        sku_class,
        veredicto,
        "classical",
        champion,
        windows,
        coverage,
        ratio,
        loses,
        0,
        iqr,
        *test,
        justification,
        json.dumps(models),
    )
    return row, champion


def publish(
    db: Path,
    *,
    ingesta_sha256: str,
    run_id_m2: str,
    verdicts: dict[str, tuple[str, str]],
    run_id: str = "m3-real-1",
    estado: str = "PUBLICADA",
    origin: Origin = "real",
    configuracion: str = "config-1",
    creado_en: str = "2026-10-27 10:00:00",
    publish_m4: bool = True,
    late: tuple[str, ...] = (),
    extra_models: dict[str, list[tuple[str, str, float | None]]] | None = None,
    significant: tuple[str, ...] = (),
    extra_portfolio: list[tuple[object, ...]] | None = None,
    windows: dict[str, list[tuple[str, float, float, float | None, str | None]]] | None = None,
) -> None:
    """Insert one M3 run whose SKUs have ``verdicts`` (SKU -> (Syntetos-Boylan class, verdict)).

    ``extra_models`` adds (familia, modelo, r) to a SKU's evaluated models, beside the baseline and
    the champion; only the verdict lists them. The HLN-DM test of the ``significant`` SKUs rejects
    after BH; every other test does not. ``extra_portfolio`` adds ``m3_cartera`` rows (sku_class,
    familia, metrica, n_skus, n_instancias, n_calculables, n_no_calculables, media, mediana, p25,
    p75) beside the RMSE of the classical family and the baseline. ``windows`` publishes, per SKU,
    the reserve windows (fecha, RMSE of the champion, RMSE of the baseline, r, cause of a null r).
    """
    synthetic, declared, untested = _ORIGINS[origin]
    with closing(sqlite3.connect(db)) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(
            "INSERT INTO m3_corridas (run_id, ingesta_sha256, run_id_m2, identidad, "
            "datos_sinteticos, origen_declarado, estado, causa_rechazo, creado_en) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                run_id,
                ingesta_sha256,
                run_id_m2,
                identity(configuracion),
                synthetic,
                declared,
                estado,
                "manifiesto incompatible con la ingesta" if estado == "RECHAZADA" else None,
                creado_en,
            ),
        )
        conn.execute(
            "INSERT INTO m3_protocolos (run_id, protocolo, manifiesto, procedencia, "
            "reserva_abierta_en, n_unidades_previstas) VALUES (?, ?, '{}', '{}', ?, ?)",
            (
                run_id,
                json.dumps({"horizonte": 7, "politica": POLICY}, sort_keys=True),
                "2026-10-27T09:00:00+00:00",
                4 * len(verdicts),
            ),
        )
        if publish_m4:
            conn.execute("INSERT INTO m3_cortes VALUES (?, '2026-03-13')", (run_id,))
            conn.executemany(
                "INSERT INTO m3_lanzamientos_tardios VALUES (?, ?, ?)",
                [(run_id, sku, LATE) for sku in late],
            )
        by_class: dict[str, Counter[str]] = {}
        for sku, (sku_class, veredicto) in verdicts.items():
            conn.execute("INSERT INTO m3_skus VALUES (?, ?)", (run_id, sku))
            by_class.setdefault(sku_class, Counter())[veredicto] += 1
            if publish_m4:
                conn.executemany(
                    "INSERT INTO m3_observaciones VALUES (?, ?, ?, ?)",
                    [
                        (run_id, sku, day, value)
                        for day, value in (
                            ("2026-03-15", 4.0),
                            ("2026-03-22", None),
                            ("2026-03-23", 0.0),
                        )
                    ],
                )
        for sku_class, counts in sorted(by_class.items()):
            total = sum(counts.values())
            conteos = {v: counts.get(v, 0) for v in VERDICTS}
            conn.execute(
                "INSERT INTO m3_categorias (run_id, sku_class, familia_campeona, motivo, adverso, "
                "medianas_r, skus_comparables, excluidos, familias_excluidas, conteos, "
                "porcentajes, mediana_r, n_adversos) VALUES (?, ?, 'classical', 'menor_mediana', "
                "0, ?, ?, '{}', ?, ?, ?, 0.8, ?)",
                (
                    run_id,
                    sku_class,
                    json.dumps({"classical": 0.8, "seasonal_naive": 1.0}),
                    json.dumps(sorted(s for s, (c, _) in verdicts.items() if c == sku_class)),
                    json.dumps({"dl": "no_entregada"}),
                    json.dumps(conteos),
                    json.dumps({v: 100.0 * n / total for v, n in conteos.items()}),
                    counts.get("EXPLORATORIO", 0),
                ),
            )
        for sku, (sku_class, veredicto) in verdicts.items():
            row, champion = _verdict(
                run_id,
                sku,
                sku_class,
                veredicto,
                untested=untested,
                extra=(extra_models or {}).get(sku, []),
                significant=sku in significant,
            )
            ratio = row[8]
            conn.execute(f"INSERT INTO m3_veredictos VALUES ({', '.join('?' * len(row))})", row)
            baseline = f"{sku}/seasonal_naive/seasonal_naive"
            for candidate, familia, modelo in (
                (baseline, "seasonal_naive", "seasonal_naive"),
                *(((champion, "classical", "sarima"),) if champion else ()),
            ):
                failed = champion is None
                for start, target, state, cause in (
                    ("2026-03-14", "2026-03-15", "confirmada", None),
                    (
                        "2026-03-21",
                        "2026-03-22",
                        "fallida" if failed else "confirmada",
                        "pronostico_invalido" if failed else None,
                    ),
                ):
                    conn.execute(
                        "INSERT INTO m3_unidades (run_id, sku, candidato_id, origen, familia, "
                        "modelo, estado, causa, emitido_en, codigo_version, semilla) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'f8678bf', 0)",
                        (
                            run_id,
                            sku,
                            candidate,
                            start,
                            familia,
                            modelo,
                            state,
                            cause,
                            "2026-10-27T09:30:00+00:00",
                        ),
                    )
                    if state == "confirmada":
                        conn.execute(
                            "INSERT INTO m3_pronosticos VALUES (?, ?, ?, ?, 1, ?, ?)",
                            (run_id, sku, candidate, start, target, 3.5),
                        )
                for metric, value in (("mae", 1.2), ("rmse", 1.5), ("mase", 0.9), ("me", -0.2)):
                    conn.execute(
                        "INSERT INTO m3_metricas VALUES (?, ?, ?, 'agregada', ?, ?, NULL)",
                        (run_id, sku, candidate, metric, value),
                    )
                value = 1.0 if candidate == baseline else ratio
                conn.execute(
                    "INSERT INTO m3_metricas VALUES (?, ?, ?, 'agregada', 'razon_sn', ?, ?)",
                    (
                        run_id,
                        sku,
                        candidate,
                        value,
                        "sin_ventanas_comunes" if value is None else None,
                    ),
                )
            if champion is None:
                conn.execute(
                    "INSERT INTO m3_fallos VALUES (?, ?, ?, 'sarima', 'pronostico', "
                    "'pronostico_invalido', 'el candidato no produjo pronosticos finitos')",
                    (run_id, f"{sku}/classical/sarima", sku),
                )
        for sku_class in sorted(by_class):
            for familia in ("classical", "seasonal_naive"):
                conn.execute(
                    "INSERT INTO m3_cartera VALUES "
                    "(?, ?, ?, 'rmse', 2, 2, 2, 0, 1.5, 1.5, 1.2, 1.8)",
                    (run_id, sku_class, familia),
                )
        conn.executemany(
            "INSERT INTO m3_cartera VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(run_id, *row) for row in extra_portfolio or []],
        )
        for sku, rows in (windows or {}).items():
            champion, baseline = f"{sku}/classical/sarima", f"{sku}/seasonal_naive/seasonal_naive"
            for fecha, rmse_champion, rmse_baseline, ratio, cause in rows:
                conn.executemany(
                    "INSERT INTO m3_metricas VALUES (?, ?, ?, ?, ?, ?, ?)",
                    [
                        (run_id, sku, champion, fecha, "rmse", rmse_champion, None),
                        (run_id, sku, baseline, fecha, "rmse", rmse_baseline, None),
                        (run_id, sku, champion, fecha, "razon_sn", ratio, cause),
                    ],
                )
        conn.commit()
