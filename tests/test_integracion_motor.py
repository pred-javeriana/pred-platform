"""Caso de integración sobre un panel reducido con el motor real (TASK-EVAL-3.5-A2).

La plataforma ingiere un panel sintético con ``pred-engine classify``, encola la corrida y el
worker lanza ``pred-engine run`` (``PRED_ENGINE_CMD``). El operador la detiene cuando M3 ya
confirmó unidades; antes de reanudarla se borra el traspaso local del motor (``candidatos.json``),
la pérdida que en pred-engine#41 llegó a crear una segunda corrida publicada. La lectura posterior
pasa por ``DataRepository`` (``DalRepository``), los documentos del contrato v2 que consume M4.

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
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from pred_platform.config import ENGINE_COMMAND_VAR, Settings
from pred_platform.contract import models_v2
from pred_platform.dal import ejecuciones as dal
from pred_platform.dal.metadatos_parquet import read_cutoff_metadata
from pred_platform.dal.migrate import migrate
from pred_platform.data.dal import DalRepository
from pred_platform.data.query import MAX_SIZE
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
    repo: DalRepository
    db: Path
    parquet: Path
    ingesta_sha256: str
    run_id_m2: str
    run_id: str
    ejecucion_id: int
    estado_detenida: models_v2.M3RunStatus
    evidencia_detenida: models_v2.M3Evidence
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
    repo = DalRepository(settings.db_path, settings.data_root)

    worker = threading.Thread(target=Worker(settings, poll_interval=0.05).run_next)
    worker.start()

    def confirmed() -> bool:
        runs = repo.list_m3_runs(run_id_m2=queued.run_id)
        if not runs.items:
            return False
        progress = repo.get_m3_run_status(runs.items[0].run_id).progress
        return progress is not None and progress.n_confirmadas > 0

    _wait_for(confirmed, worker, "la primera unidad M3 confirmada")
    assert control.stop(queued.ejecucion_id)
    worker.join(LIMITE_S)
    assert not worker.is_alive()

    [corrida] = repo.list_m3_runs(run_id_m2=queued.run_id).items
    with closing(sqlite3.connect(f"{settings.db_path.as_uri()}?mode=ro", uri=True)) as conn:
        unidades = {
            (sku, candidato, origen): emitido
            for sku, candidato, origen, emitido in conn.execute(
                "SELECT sku, candidato_id, origen, emitido_en FROM m3_unidades WHERE run_id = ?",
                (corrida.run_id,),
            )
        }
        [(parquet,)] = conn.execute("SELECT ruta_parquet FROM ingestas WHERE sha256 = ?", (sha,))
    caso = Caso(
        repo=repo,
        db=settings.db_path,
        parquet=root / parquet,
        ingesta_sha256=hashlib.sha256(panel).hexdigest(),
        run_id_m2=queued.run_id,
        run_id=corrida.run_id,
        ejecucion_id=queued.ejecucion_id,
        estado_detenida=repo.get_m3_run_status(corrida.run_id),
        evidencia_detenida=repo.get_m3_evidence(corrida.run_id),
        unidades_previas=unidades,
    )

    (root / "runs" / queued.run_id / "candidatos.json").unlink()
    assert control.resume(queued.ejecucion_id)
    Worker(settings, poll_interval=0.05).drain()
    return caso


def test_unpublished_run_shows_progress_only_then_publishes(caso: Caso) -> None:
    detenida = caso.estado_detenida
    assert detenida.run is not None and detenida.progress is not None
    assert (detenida.run.estado, detenida.run.causa_rechazo) == ("EN_CURSO", None)
    previstas = detenida.progress.n_unidades_previstas
    assert previstas is not None and 0 < detenida.progress.n_confirmadas < previstas
    evidencia = caso.evidencia_detenida
    assert evidencia.availability.reason_code == "not_published"
    assert evidencia.items == [] and evidencia.funnel is None

    publicada = caso.repo.get_m3_run_status(caso.run_id)
    assert publicada.run is not None and publicada.progress is not None
    assert (publicada.run.run_id_m2, publicada.run.estado) == (caso.run_id_m2, "PUBLICADA")
    assert (publicada.run.datos_sinteticos, publicada.run.origen_declarado) == (True, True)
    assert publicada.run.publicadas_misma_identidad == [caso.run_id]
    assert publicada.run.aviso is None
    progress = publicada.progress
    assert (progress.n_resueltas, progress.n_confirmadas, progress.n_fallidas) == (
        previstas,
        previstas,
        0,
    )
    with closing(dal.connect(caso.db)) as conn:
        run = dal.get_run(conn, caso.ejecucion_id)
    assert run is not None and (run.estado, run.pid) == ("completada", None)


def test_the_verdicts_and_the_funnel_follow_the_published_cutoff_metadata(caso: Caso) -> None:
    evidencia = caso.repo.get_m3_evidence(caso.run_id)
    metadata = read_cutoff_metadata(caso.parquet)
    if metadata is None:
        # An engine that does not keep the cut in the Parquet: no funnel instead of zero exclusions.
        assert evidencia.availability.reason_code == "metadata_missing"
        assert evidencia.items == [] and evidencia.funnel is None
        return
    assert evidencia.availability.status == "available" and evidencia.funnel is not None
    assert sorted(v.sku for v in evidencia.items) == sorted(SERIES)
    assert evidencia.funnel.total_productos == len(SERIES) + len(metadata.exclusions)
    assert evidencia.funnel.tras_exclusiones == len(SERIES)
    # Datos sintéticos: sin prueba HLN-DM ni p-valores, con la causa visible.
    assert {(v.dm_causa, v.dm_p_valor) for v in evidencia.items} == {("datos_sinteticos", None)}
    for veredicto in evidencia.items:
        assert (veredicto.run_id, veredicto.ingesta_sha256) == (caso.run_id, caso.ingesta_sha256)
        assert veredicto.veredicto != "VALIDADO"


def test_every_row_keeps_run_sku_origin_and_ingest_identity(caso: Caso) -> None:
    repo = caso.repo
    documents = {
        "metricas": repo.get_m3_metrics(caso.run_id, size=500),
        "categorias": repo.get_m3_categories(caso.run_id),
        "cartera": repo.get_m3_portfolio(caso.run_id, size=500),
        "pronosticos": repo.get_m3_forecasts(caso.run_id, size=500),
        "fallos": repo.get_m3_failures(caso.run_id),
        "unidades": repo.get_m3_units(caso.run_id, size=500),
    }
    for name, doc in documents.items():
        assert doc.availability.status == "available", name
        assert {(row.run_id, row.ingesta_sha256) for row in doc.items} <= {
            (caso.run_id, caso.ingesta_sha256)
        }, name
        assert {row.sku for row in doc.items} <= {None, *SERIES}, name
    # Ningún candidato falló la validación de 3.2; todas las demás tablas tienen filas.
    assert [name for name, doc in documents.items() if not doc.items] == ["fallos"]
    assert {c.sku for c in documents["categorias"].items} == {None}
    assert {m.metrica for m in documents["metricas"].items} >= {"mae", "rmse", "mase", "me"}


def test_resume_reuses_committed_units_and_leaves_no_duplicates(caso: Caso) -> None:
    repo = caso.repo
    detenida = caso.estado_detenida.progress
    assert detenida is not None and detenida.n_unidades_previstas is not None
    previstas = detenida.n_unidades_previstas
    # Todas las páginas: el panel tiene más unidades de las que caben en una página.
    unidades: list[models_v2.M3Unit] = []
    page = repo.get_m3_units(caso.run_id, size=MAX_SIZE)
    while page.items:
        unidades += page.items
        page = repo.get_m3_units(caso.run_id, page=page.page.number + 1, size=MAX_SIZE)
    assert page.page.total == len(unidades) == previstas
    emitidas = {(u.sku, u.candidato_id, u.origen): u.emitido_en for u in unidades}
    assert len(emitidas) == previstas
    # Las unidades confirmadas antes de detener la corrida no se recalcularon.
    assert {key: emitidas[key] for key in caso.unidades_previas} == {
        key: datetime.fromisoformat(value) for key, value in caso.unidades_previas.items()
    }
    assert repo.get_m3_forecasts(caso.run_id, size=1).page.total == previstas * HORIZONTE
    assert repo.list_m3_runs(run_id_m2=caso.run_id_m2).page.total == 1
    with closing(dal.connect(caso.db)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM ejecuciones").fetchone() == (1,)


def test_sku_read_carries_the_run_origin(caso: Caso) -> None:
    sku = sorted(SERIES)[0]
    metricas = caso.repo.get_m3_metrics(caso.run_id, sku=sku, size=500)
    assert metricas.items and {(m.sku, m.run_id) for m in metricas.items} == {(sku, caso.run_id)}
    assert metricas.run is not None
    assert (
        metricas.run.ingesta_sha256,
        metricas.run.datos_sinteticos,
        metricas.run.origen_declarado,
    ) == (caso.ingesta_sha256, True, True)
