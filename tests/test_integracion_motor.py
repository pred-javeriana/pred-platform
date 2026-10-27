"""Caso de integración sobre un panel reducido con el motor real (TASK-EVAL-3.5-A2).

La plataforma ingiere un panel sintético con ``pred-engine classify``, encola la corrida y el
worker lanza ``pred-engine run`` (``PRED_ENGINE_CMD``). El operador la detiene cuando M3 ya
confirmó unidades; antes de reanudarla se borra el traspaso local del motor (``candidatos.json``),
la pérdida que en pred-engine#41 llegó a crear una segunda corrida publicada. La lectura posterior
pasa por ``read_m3_evidence`` y ``m3_run_status``, el contrato de consumo de M4.

Se omite cuando el motor no está instalado; ``make integration`` lo corre.
"""

from __future__ import annotations

import hashlib
import shutil
import sqlite3
import threading
import time
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest

from pred_platform.config import ENGINE_COMMAND_VAR, Settings
from pred_platform.dal import ejecuciones as dal
from pred_platform.dal.evidencia import m3_run_status, read_m3_evidence
from pred_platform.dal.migrate import migrate
from pred_platform.worker.bucle import Worker
from pred_platform.worker.control import DalRunControl
from pred_platform.worker.ingesta import submit_csv
from pred_platform.worker.service import LaunchSettings, enqueue_run

pytestmark = pytest.mark.integration

DIAS = 180
HORIZONTE = 2
# Seis series sintéticas deterministas, sin datos hospitalarios: tendencia con semana marcada,
# densas con amplitud variable e intermitentes con ceros frecuentes.
SERIES: dict[str, Callable[[int], float]] = {
    "SINT-001": lambda i: 20 + 0.1 * i + 3 * (i % 7 == 5) + (i * 7 % 5 - 2),
    "SINT-002": lambda i: max(0, 15 + (i * 37 % 23) - 11),
    "SINT-003": lambda i: (2 + i * 13 % 9) if i % 2 == 0 else 0,
    "SINT-004": lambda i: 40 + 8 * (i % 7 in (0, 6)) + (i * 11 % 7 - 3),
    "SINT-005": lambda i: max(0, 30 - 0.05 * i + (i * 29 % 17) - 8),
    "SINT-006": lambda i: (5 + i * 17 % 11) if i % 3 != 1 else 0,
}
LIMITE_S = 600.0


def _panel() -> bytes:
    inicio = date(2024, 1, 1)
    filas = ["sku_id,timestamp,demand_qty,lead_time_days"]
    for i in range(DIAS):
        dia = (inicio + timedelta(days=i)).isoformat()
        filas += [f"{sku},{dia},{serie(i)},7" for sku, serie in SERIES.items()]
    return ("\n".join(filas) + "\n").encode()


@dataclass(frozen=True)
class Caso:
    db: Path
    ingesta_sha256: str
    run_id_m2: str
    ejecucion_id: int
    estado_detenida: dict[str, Any]
    evidencia_detenida: dict[str, list[dict[str, Any]]]
    unidades_previas: dict[tuple[str, str, str], str]


def _engine_command() -> tuple[str, ...]:
    command = Settings.from_env().engine_command
    if shutil.which(command[0]) is None:
        pytest.skip(f"pred-engine no está disponible; defina {ENGINE_COMMAND_VAR}")
    return command


def _wait_for(condition: Callable[[], bool], worker: threading.Thread, what: str) -> None:
    deadline = time.monotonic() + LIMITE_S
    while not condition():
        assert worker.is_alive(), f"la corrida terminó antes de {what}"
        assert time.monotonic() < deadline, f"no se alcanzó {what} en {LIMITE_S:.0f} s"
        time.sleep(0.02)


@pytest.fixture(scope="module")
def caso(tmp_path_factory: pytest.TempPathFactory) -> Caso:
    root = tmp_path_factory.mktemp("integracion")
    settings = Settings(data_root=root, db_path=root / "pred.db", engine_command=_engine_command())
    migrate(settings.db_path)
    panel = _panel()
    sha = submit_csv(settings, "panel_sintetico_reducido.csv", panel)
    queued = enqueue_run(
        settings.db_path,
        sha,
        LaunchSettings(
            min_train=40,
            horizon=HORIZONTE,
            step=7,
            families=("classical", "ml"),
            trials=1,
            seed=17,
            workers=1,
            datos_sinteticos=True,
        ),
    )
    control = DalRunControl(settings.db_path)

    worker = threading.Thread(target=Worker(settings, poll_interval=0.05).run_next)
    worker.start()

    def confirmed() -> bool:
        status = m3_run_status(settings.db_path, queued.run_id)
        return bool(status) and status[0]["n_confirmadas"] > 0

    _wait_for(confirmed, worker, "la primera unidad M3 confirmada")
    assert control.stop(queued.ejecucion_id)
    worker.join(LIMITE_S)
    assert not worker.is_alive()

    [estado] = m3_run_status(settings.db_path, queued.run_id)
    with closing(sqlite3.connect(f"{settings.db_path.as_uri()}?mode=ro", uri=True)) as conn:
        unidades = {
            (sku, candidato, origen): emitido
            for sku, candidato, origen, emitido in conn.execute(
                "SELECT sku, candidato_id, origen, emitido_en FROM m3_unidades WHERE run_id = ?",
                (estado["run_id"],),
            )
        }
    caso = Caso(
        db=settings.db_path,
        ingesta_sha256=hashlib.sha256(panel).hexdigest(),
        run_id_m2=queued.run_id,
        ejecucion_id=queued.ejecucion_id,
        estado_detenida=estado,
        evidencia_detenida=read_m3_evidence(settings.db_path, queued.run_id),
        unidades_previas=unidades,
    )

    (root / "runs" / queued.run_id / "candidatos.json").unlink()
    assert control.resume(queued.ejecucion_id)
    Worker(settings, poll_interval=0.05).drain()
    return caso


def test_unpublished_run_shows_progress_only_then_publishes(caso: Caso) -> None:
    detenida = caso.estado_detenida
    assert (detenida["estado"], detenida["causa_rechazo"]) == ("EN_CURSO", None)
    assert 0 < detenida["n_confirmadas"] < detenida["n_unidades_previstas"]
    assert caso.evidencia_detenida and all(rows == [] for rows in caso.evidencia_detenida.values())

    [publicada] = m3_run_status(caso.db, caso.run_id_m2)
    assert publicada == {
        "run_id": detenida["run_id"],
        "run_id_m2": caso.run_id_m2,
        "estado": "PUBLICADA",
        "causa_rechazo": None,
        "datos_sinteticos": 1,
        "origen_declarado": 1,
        "n_unidades_previstas": detenida["n_unidades_previstas"],
        "n_resueltas": detenida["n_unidades_previstas"],
        "n_confirmadas": detenida["n_unidades_previstas"],
        "n_fallidas": 0,
    }
    with closing(dal.connect(caso.db)) as conn:
        run = dal.get_run(conn, caso.ejecucion_id)
    assert run is not None and (run.estado, run.pid) == ("completada", None)


def test_every_row_keeps_run_sku_origin_and_ingest_identity(caso: Caso) -> None:
    run_id = caso.estado_detenida["run_id"]
    evidencia = read_m3_evidence(caso.db, caso.run_id_m2)
    assert [
        {k: corrida[k] for k in ("run_id", "run_id_m2", "ingesta_sha256", "estado")}
        | {"origen": (corrida["datos_sinteticos"], corrida["origen_declarado"])}
        for corrida in evidencia["m3_corridas"]
    ] == [
        {
            "run_id": run_id,
            "run_id_m2": caso.run_id_m2,
            "ingesta_sha256": caso.ingesta_sha256,
            "estado": "PUBLICADA",
            "origen": (1, 1),
        }
    ]
    # Ningún candidato falló la validación de 3.2; todas las demás tablas tienen filas.
    assert [table for table, rows in evidencia.items() if not rows] == ["m3_fallos"]
    for table, rows in evidencia.items():
        assert {row["run_id"] for row in rows} <= {run_id}, table
        assert {row.get("sku") for row in rows} <= {None, *SERIES}, table
    assert sorted(row["sku"] for row in evidencia["m3_veredictos"]) == sorted(SERIES)
    # Datos sintéticos: sin prueba HLN-DM ni p-valores, con la causa visible.
    assert {(v["dm_causa"], v["dm_p_valor"]) for v in evidencia["m3_veredictos"]} == {
        ("datos_sinteticos", None)
    }


def test_resume_reuses_committed_units_and_leaves_no_duplicates(caso: Caso) -> None:
    evidencia = read_m3_evidence(caso.db, caso.run_id_m2)
    previstas = caso.estado_detenida["n_unidades_previstas"]
    unidades = {
        (u["sku"], u["candidato_id"], u["origen"]): u["emitido_en"]
        for u in evidencia["m3_unidades"]
    }
    assert len(unidades) == len(evidencia["m3_unidades"]) == previstas
    # Las unidades confirmadas antes de detener la corrida no se recalcularon.
    assert {key: unidades[key] for key in caso.unidades_previas} == caso.unidades_previas
    assert len(evidencia["m3_pronosticos"]) == previstas * HORIZONTE
    assert len(evidencia["m3_corridas"]) == len(evidencia["m3_protocolos"]) == 1
    assert len(m3_run_status(caso.db, caso.run_id_m2)) == 1
    with closing(dal.connect(caso.db)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM ejecuciones").fetchone() == (1,)


@pytest.mark.xfail(
    strict=True,
    raises=KeyError,
    reason="#29: la lectura por SKU todavía omite la fila de la corrida y su origen",
)
def test_sku_read_carries_the_run_origin(caso: Caso) -> None:
    sku = sorted(SERIES)[0]
    evidencia = read_m3_evidence(caso.db, caso.estado_detenida["run_id"], sku=sku)
    assert [(v["sku"], v["run_id"]) for v in evidencia["m3_veredictos"]] == [
        (sku, caso.estado_detenida["run_id"])
    ]
    assert [
        (c["run_id"], c["ingesta_sha256"], c["datos_sinteticos"], c["origen_declarado"])
        for c in evidencia["m3_corridas"]
    ] == [(caso.estado_detenida["run_id"], caso.ingesta_sha256, 1, 1)]
