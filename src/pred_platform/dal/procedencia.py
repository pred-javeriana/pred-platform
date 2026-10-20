"""Una procedencia por ingesta, compartida por informes, cola y trabajador."""

from __future__ import annotations

import json
import sqlite3


def resolver_procedencia(
    conn: sqlite3.Connection,
    ingesta_sha256: str,
    declarada: bool | None = None,
    *,
    ingesta_ref_m1: str | None = None,
) -> bool | None:
    """Resuelve y conserva la declaracion dentro de la transaccion del llamador.

    None representa origen desconocido, no una declaracion de datos reales.
    Se consultan todas las configuraciones de la ingesta, incluidas las antiguas.
    """
    if not conn.in_transaction:
        raise ValueError("la procedencia requiere una transaccion de escritura")
    if declarada is not None and type(declarada) is not bool:
        raise ValueError("datos_sinteticos debe ser booleano o nulo")
    tablas = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    origenes: set[bool] = set()
    columnas: dict[str, set[str]] = {}
    for tabla in ("m3_ingestas", "m3_corridas"):
        if tabla not in tablas:
            continue
        columnas[tabla] = {r[1] for r in conn.execute(f"PRAGMA table_info({tabla})")}
        filtro = " AND origen_declarado=1" if "origen_declarado" in columnas[tabla] else ""
        origenes.update(
            bool(r[0])
            for r in conn.execute(
                f"SELECT datos_sinteticos FROM {tabla} WHERE ingesta_sha256=?{filtro}",
                (ingesta_sha256,),
            )
        )
    if "ejecuciones" in tablas:
        for (texto,) in conn.execute(
            "SELECT c.parametros FROM configuraciones c "
            "JOIN ejecuciones e ON e.configuracion_id=c.id "
            "JOIN ingestas i ON i.id=e.ingesta_id WHERE i.sha256=?",
            (ingesta_sha256,),
        ):
            valor = json.loads(texto).get("datos_sinteticos")
            if valor is not None and type(valor) is not bool:
                raise ValueError("procedencia invalida en la configuracion almacenada")
            if valor is not None:
                origenes.add(valor)
    if declarada is not None:
        origenes.add(declarada)
    if len(origenes) > 1:
        raise ValueError("el origen real o sintetico de una ingesta no puede cambiar")
    origen = next(iter(origenes), None)
    if "m3_ingestas" in columnas:
        moderna = "origen_declarado" in columnas["m3_ingestas"]
        if moderna and origen is not None:
            conn.execute(
                "UPDATE m3_ingestas SET datos_sinteticos=?, origen_declarado=1 "
                "WHERE ingesta_sha256=? AND origen_declarado=0",
                (origen, ingesta_sha256),
            )
        if ingesta_ref_m1 is not None:
            if moderna:
                conn.execute(
                    "INSERT INTO m3_ingestas "
                    "(ingesta_sha256, ingesta_ref_m1, datos_sinteticos, origen_declarado) "
                    "VALUES (?, ?, ?, ?) ON CONFLICT DO NOTHING",
                    (ingesta_sha256, ingesta_ref_m1, bool(origen), origen is not None),
                )
            elif origen is not None:
                conn.execute(
                    "INSERT INTO m3_ingestas (ingesta_sha256, ingesta_ref_m1, datos_sinteticos) "
                    "VALUES (?, ?, ?) ON CONFLICT DO NOTHING",
                    (ingesta_sha256, ingesta_ref_m1, origen),
                )
    return origen
