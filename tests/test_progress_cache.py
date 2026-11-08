"""Live polls must not make unchanged completed logs expensive to open again."""

from contextlib import closing
from pathlib import Path

import pytest

from pred_platform.dal import ejecuciones as dal
from tests.test_ejecucion import _launch
from tests.test_execution_live import _running, _snapshot
from tests.test_worker import Env, _escribir_progreso
from tests.test_worker import env as env  # noqa: F401


def test_live_updates_keep_completed_summaries_warm_on_data_and_results_routes(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, live = _running(env)
    completed = []
    for seed in range(20, 24):
        assert _launch(client, env, seed=str(seed)).status_code == 303
        with closing(dal.connect(env.db)) as conn:
            run = dal.claim_pending(conn)
            assert run is not None
            conn.execute(
                "UPDATE ejecuciones SET estado='completada', ruta_corrida=? WHERE id=?",
                (f"runs/{run.run_id}", run.id),
            )
            conn.commit()
        _escribir_progreso(
            env,
            run.run_id,
            {"event": "stage_started", "stage": "L2", "t": "2026-11-04T20:00:00Z"},
            {
                "event": "stage_finished",
                "stage": "L2",
                "state": "completed",
                "t": "2026-11-04T20:02:00Z",
            },
        )
        completed.append((run.id, env.settings.data_root / "runs" / run.run_id / "progreso.jsonl"))
    _escribir_progreso(
        env,
        live.run_id,
        {"event": "stage_started", "stage": "L2", "t": "2026-11-04T20:00:00Z"},
    )
    paths = {path for _, path in completed}
    reads = []
    original = Path.read_text

    def reading(path: Path, *args, **kwargs):
        if path in paths:
            reads.append(path)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", reading)
    for run_id, _ in completed:
        assert client.get(f"/corridas/{run_id}/datos").status_code == 200
        assert client.get(f"/corridas/{run_id}/resultados").status_code == 200
    assert set(reads) == paths  # The routes really loaded every completed log.
    reads.clear()
    for index in range(9):
        _escribir_progreso(env, live.run_id, {"event": "heartbeat", "sequence": index})
        response = client.get("/ejecucion/estado?ejecucion=1")
        assert response.status_code == 200
        assert _snapshot(response.text)["stage"] == "L2"
    for run_id, _ in completed:
        assert client.get(f"/corridas/{run_id}/datos").status_code == 200
        assert client.get(f"/corridas/{run_id}/resultados").status_code == 200
    assert reads == [], "Live file versions evicted unchanged completed summaries"
    # A same-sized replacement still invalidates that completed file's summary.
    run_id, path = completed[0]
    replacement = path.with_suffix(".new")
    replacement.write_text(original(path).replace("20:02:00Z", "20:03:00Z"))
    replacement.replace(path)
    refreshed = client.get(f"/corridas/{run_id}/ejecucion")
    assert _snapshot(refreshed.text)["stages"][1]["seconds"] == 180
    assert reads == [path]
    _escribir_progreso(env, live.run_id, {"event": "stage_started", "stage": "L3"})
    assert _snapshot(client.get("/ejecucion/estado?ejecucion=1").text)["stage"] == "L3"
