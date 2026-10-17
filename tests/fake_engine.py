"""A stand-in for ``pred-engine run`` used by the worker tests.

It accepts the same command line the worker builds and leaves the same files in the run folder
(``progreso.jsonl``, ``unidades.jsonl``, ``corrida.json``), so the worker is tested without the
real engine, its heavy dependencies or its running time. The scenario comes from the
``FAKE_ENGINE_SCENARIO`` environment variable and the SKUs from ``FAKE_ENGINE_SKUS``:

    ok            every unit completes in L2 and L3 (exit 7, like the real engine without L4)
    unit_fails    FIL-0002 / ml fails in L2
    excluded      FIL-0003 is excluded before L2
    crash         dies after the first unit started, without corrida.json
    stage_fails   L3 fails as a whole: no L3 unit, corrida.json says so (exit 1)
    slow          plans and starts one unit, then waits until it is killed
    slow_ok       like ok but each unit takes ~0.3 s, so a poller can see it in flight
    future_schema writes a progress stream of an unsupported major version

``classify --csv <file> --data-root <root> [--confirm-overwrite]`` plays the ingestion the browser
upload launches: it leaves ``ingestas/<sha256>.json`` (copied from ``tests/fixtures/motor``) chosen
by a marker in the file's content: ``BADHEADER`` (rejected), ``BADVALUE`` (failed), ``CONFLICT``
(needs confirmation, exit 7, accepted keeping both files once confirmed), ``BOOM`` (dies without
a report) and ``SLOW`` (hangs); anything else is accepted.

``FAKE_ENGINE_SELECTION`` shapes ``seleccion.jsonl`` (contract section 20): ``ok`` (default),
``none`` (an engine that predates it), ``future`` (an unsupported major version) or ``bad_line``
(one unreadable line and one with a family that contradicts its model, between valid ones).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

MODELS = {"classical": "sarima", "ml": "lightgbm", "dl": "mlp", "foundation": "chronos2"}
RESERVE = {
    "fraction": 0.2,
    "t_star": "2026-03-13",
    "first_reserved": "2026-03-14",
    "last_observed": "2026-03-31",
    "reserved_days": 18,
}


CLASS_BY_SKU = {"FIL-0001": "smooth", "FIL-0002": "intermittent", "FIL-0003": "lumpy"}
PROFILE_BY_CLASS = {
    "smooth": "dense_stable",
    "intermittent": "sparse_stable",
    "lumpy": "sparse_variable",
    "erratic": "dense_variable",
}
MATRIX = {
    "smooth": ["classical", "ml", "dl", "foundation"],
    "erratic": ["classical", "ml", "dl", "foundation"],
    "intermittent": ["classical", "ml", "foundation"],
    "lumpy": ["classical", "foundation"],
}
ALL_FAMILIES = ["classical", "ml", "dl", "foundation"]
POLICY = "2.2.0-initial"


def _now() -> str:
    return datetime.now(UTC).isoformat()


class Stream:
    def __init__(self, path: Path) -> None:
        self.path = path

    def emit(self, event: str, **data: Any) -> None:
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"event": event, "t": _now(), **data}) + "\n")


def _selection_family(
    sku: str,
    family: str,
    *,
    configured: list[str],
    excluded: list[str],
    l2_failed: bool,
    l3_failed: bool,
    index: int,
) -> dict[str, Any]:
    """One family of one SKU as the engine writes it in ``seleccion.jsonl``."""
    sku_class = CLASS_BY_SKU[sku]
    if family not in MATRIX[sku_class]:
        return {
            "family": family,
            "estado": "excluida",
            "exclusion": {
                "reason_code": "not_in_policy_matrix",
                "detail": f"La politica {POLICY} no asigna {family} a la clase {sku_class}.",
            },
        }
    if family not in configured:
        return {
            "family": family,
            "estado": "excluida",
            "exclusion": {
                "reason_code": "not_configured",
                "detail": "La corrida se configuro con otro subconjunto de familias.",
            },
        }
    if sku in excluded:
        return {
            "family": family,
            "estado": "no_ejecutable",
            "cause": "la serie termina antes de t*: sin reserva",
        }
    model = f"{family}:{MODELS[family]}"
    study = f"{family}-{sku}-fake"
    trials = None
    if family != "foundation":
        trials = [
            {
                "id": f"{study}-0000",
                "configuracion": {"p": 1, "d": 1},
                "estado": "completado" if not l2_failed else "fallido",
                "valor": None if l2_failed else 1.5,
                "n_ventanas": 2,
                "motivo": "ventana=1 ajuste_fallido: AjusteModeloError: no converge"
                if l2_failed
                else None,
                "timestamp": "2026-10-14T16:19:02+00:00",
            },
            {
                "id": f"{study}-0001",
                "configuracion": {"p": 2, "d": 1},
                "estado": "podado",
                "valor": 2.5,
                "n_ventanas": 1,
                "motivo": "escalon=1 valor=2.5 umbral_top_1=1.5",
                "timestamp": "2026-10-14T16:19:03+00:00",
            },
        ]
    if l2_failed:
        return {
            "family": family,
            "estado": "fallida",
            "modelo": model,
            "error": {"stage": "L2", "text": "AjusteModeloError: el modelo no converge"},
            "trials": trials,
        }
    value = round(1.0 + 0.5 * list(CLASS_BY_SKU).index(sku) + 0.25 * index, 4)
    if family == "foundation":
        evidence: dict[str, Any] = {
            "family": family,
            "configuracion": {"modelo": "chronos-2", "revision": "fijada"},
            "optimizado": False,
        }
    else:
        evidence = {
            "family": family,
            "metrica_objetivo": "mae",
            "valor": value,
            "n_ventanas": 2,
            "n_trials": 2,
            "n_completados": 1,
            "n_podados": 1,
            "n_fallidos": 0,
            "seed": 17,
            "estudio_hpo": study,
        }
        if family == "classical":
            evidence |= {"order": [1, 1, 1], "seasonal_order": [0, 1, 1, 7]}
        else:
            evidence |= {"hiperparametros": {"learning_rate": 0.1}}
    entry: dict[str, Any] = {
        "family": family,
        "modelo": model,
        "produced_by": f"{family.capitalize()}SelectionStrategy",
        "evidence": evidence,
        "forecast_config": {"m": 7},
        "forecast_seed": 17,
        "trials": trials,
    }
    if l3_failed:
        return entry | {
            "estado": "fallida",
            "error": {"stage": "L3", "text": "EvaluacionError: ninguna unidad evaluó"},
        }
    windows = [
        {
            "indice": i,
            "corte": ("2026-02-10", "2026-02-24")[i],
            "inicio_train": 0,
            "fin_train": 40 + 14 * i,
            "inicio_val": 40 + 14 * i,
            "fin_val": 42 + 14 * i,
            "metricas": {"mae": value + i, "rmse": value + 1 + i, "smape": None, "mase": 0.9},
            "duracion_s": 0.25,
            "fallo": None,
        }
        for i in range(2)
    ]
    return entry | {
        "estado": "exitosa",
        "walk_forward": {
            "metrica_objetivo": "mae",
            "valor_agregado": value,
            "completo": True,
            "n_ventanas_evaluadas": 2,
            "n_ventanas_totales": 2,
            "selection_scope": "same_history",
            "statistical_comparison_available": False,
            "windows": windows,
        },
    }


def _selection_lines(
    skus: list[str],
    configured: list[str],
    excluded: list[str],
    unit_rows: list[dict[str, Any]],
    *,
    l3_fails: bool,
) -> list[dict[str, Any]]:
    failed_l2 = {(u["sku_id"], u["family"]) for u in unit_rows if u["state"] == "fallida"}
    lines = []
    for sku in skus:
        sku_class = CLASS_BY_SKU[sku]
        lines.append(
            {
                "schema": "pred-engine.seleccion/1",
                "run_id": "fake",
                "sku_id": sku,
                "sku_class": sku_class,
                "profile": PROFILE_BY_CLASS[sku_class],
                "policy_version": f"{POLICY}:configured:{','.join(configured)}",
                "families": [
                    _selection_family(
                        sku,
                        family,
                        configured=configured,
                        excluded=excluded,
                        l2_failed=(sku, family) in failed_l2,
                        l3_failed=l3_fails,
                        index=index,
                    )
                    for index, family in enumerate(ALL_FAMILIES)
                ],
            }
        )
    return lines


def _write_selection(folder: Path, lines: list[dict[str, Any]], mode: str) -> None:
    if mode == "none":
        return
    if mode == "future":
        lines = [{**line, "schema": "pred-engine.seleccion/2"} for line in lines]
    out = [json.dumps(line) for line in lines]
    if mode == "bad_line":
        # Unreadable JSON, and a classical family whose model says ml: the contract refuses it.
        broken = json.loads(json.dumps(lines[0]))
        broken["sku_id"] = "FIL-9999"
        broken["families"][0]["modelo"] = "ml:lightgbm"
        out = [out[0], "{esto no es json", json.dumps(broken), *out[1:]]
    (folder / "seleccion.jsonl").write_text("".join(f"{x}\n" for x in out), encoding="utf-8")


_FIXTURES = Path(__file__).parent / "fixtures" / "motor"


def _classify(args: argparse.Namespace) -> int:
    import hashlib

    path = Path(args.csv)
    content = path.read_bytes()
    sha = hashlib.sha256(content).hexdigest()
    root = Path(args.data_root)
    if b"BOOM" in content:
        print("el motor se cayó", file=sys.stderr)
        return 1
    if b"SLOW" in content:
        time.sleep(60)
    conflict = b"CONFLICT" in content
    if b"BADHEADER" in content:
        label, code = "rejected", 3
    elif b"BADVALUE" in content:
        label, code = "failed", 3
    elif conflict and not args.confirm_overwrite:
        label, code = "needs_confirmation", 7
    elif conflict:
        label, code = "accepted_keep_both", 0
    else:
        label, code = "accepted", 0
    report = json.loads((_FIXTURES / f"ingesta.{label}.json").read_text())
    stem = path.stem + (f"-{sha[:8]}" if label == "accepted_keep_both" else "")
    report["ingest_id"] = sha
    report["source_file"] = {**report["source_file"], "name": path.name, "sha256": sha}
    report["deposit"]["raw_path"] = f"raw/{stem}{path.suffix}"
    if report.get("published"):
        report["published"]["parquet_path"] = f"processed/{stem}.parquet"
        (root / "processed").mkdir(parents=True, exist_ok=True)
        (root / "processed" / f"{stem}.parquet").write_bytes(b"")
    (root / "ingestas").mkdir(parents=True, exist_ok=True)
    (root / "ingestas" / f"{sha}.json").write_text(json.dumps(report), encoding="utf-8")
    return code


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command")
    parser.add_argument("--csv")
    parser.add_argument("--confirm-overwrite", action="store_true")
    parser.add_argument("--parquet")
    parser.add_argument("--data-root", required=True)
    parser.add_argument("--run-id")
    parser.add_argument("--families", nargs="+", default=["classical", "ml", "dl"])
    parser.add_argument("--trials")
    parser.add_argument("--min-train")
    parser.add_argument("--horizon")
    parser.add_argument("--step")
    parser.add_argument("--seasonality")
    parser.add_argument("--metric")
    parser.add_argument("--seed")
    parser.add_argument("--workers")
    args = parser.parse_args()
    if args.command == "classify":
        return _classify(args)

    scenario = os.environ.get("FAKE_ENGINE_SCENARIO", "ok")
    skus = os.environ.get("FAKE_ENGINE_SKUS", "FIL-0001,FIL-0002,FIL-0003").split(",")
    folder = Path(args.data_root) / "runs" / args.run_id
    folder.mkdir(parents=True, exist_ok=True)
    stream = Stream(folder / "progreso.jsonl")
    attempt = (
        1 + sum(1 for line in stream.path.read_text().splitlines() if '"run_started"' in line)
        if stream.path.exists()
        else 1
    )
    schema = "pred-engine.progreso/2" if scenario == "future_schema" else "pred-engine.progreso/1"
    stream.emit(
        "run_started", schema=schema, run_id=args.run_id, attempt=attempt, workers=args.workers
    )

    excluded = ["FIL-0003"] if scenario == "excluded" else []
    runnable = [s for s in skus if s not in excluded]
    delay = 0.3 if scenario == "slow_ok" else 0.0
    units = [(s, f) for s in runnable for f in args.families]
    unit_rows: list[dict[str, Any]] = []

    stream.emit("stage_started", stage="L1")
    stream.emit("stage_finished", stage="L1", state="completed")
    stream.emit("stage_started", stage="L2")
    for sku in excluded:
        stream.emit("excluded", sku_id=sku, cause="la serie termina antes de t*: sin reserva")
    stream.emit(
        "planned",
        stage="L2",
        units=[{"sku_id": s, "family": f, "model": MODELS[f]} for s, f in units],
    )

    def run_unit(stage: str, sku: str, family: str, *, fail: bool = False) -> None:
        label = {"stage": stage, "sku_id": sku, "family": family, "model": MODELS[family]}
        started = _now()
        stream.emit("unit_started", **label, pid=os.getpid(), started_at=started)
        time.sleep(delay)
        state = "fallida" if fail else "completada"
        error = "AjusteModeloError: el modelo no converge" if fail else None
        finished = _now()
        row = {
            **label,
            "state": state,
            "pid": os.getpid(),
            "started_at": started,
            "finished_at": finished,
            "seconds": 1.5 if stage == "L2" else 0.5,
            "cpu_s": 1.0,
            "error": error,
        }
        stream.emit("unit_finished", **row)
        unit_rows.append(row)

    if scenario == "slow":
        first_sku, first_family = units[0]
        stream.emit("stage_started", stage="L2")
        label = {"stage": "L2", "sku_id": first_sku, "family": first_family}
        stream.emit(
            "unit_started",
            **label,
            model=MODELS[first_family],
            pid=os.getpid(),
            started_at=_now(),
        )
        time.sleep(60)
        return 0

    for index, (sku, family) in enumerate(units):
        if scenario == "crash" and index == 1:
            os._exit(3)
        run_unit(
            "L2", sku, family, fail=scenario == "unit_fails" and (sku, family) == ("FIL-0002", "ml")
        )

    ok_units = [(u["sku_id"], u["family"]) for u in unit_rows if u["state"] == "completada"]
    stages = [{"id": "L1", "state": "completed"}, {"id": "L2", "state": "completed"}]
    if scenario == "stage_fails":
        stream.emit("stage_started", stage="L3")
        stream.emit("stage_finished", stage="L3", state="failed", message="ninguna unidad evaluó")
        stages.append({"id": "L3", "state": "failed", "message": "ninguna unidad evaluó"})
    else:
        stream.emit("stage_finished", stage="L2", state="completed")
        stream.emit("stage_started", stage="L3")
        stream.emit(
            "planned",
            stage="L3",
            units=[{"sku_id": s, "family": f, "model": MODELS[f]} for s, f in ok_units],
        )
        for sku, family in ok_units:
            run_unit("L3", sku, family)
        stream.emit("stage_finished", stage="L3", state="completed")
        stages.append({"id": "L3", "state": "completed"})
    stages.append({"id": "L4", "state": "blocked"})

    (folder / "unidades.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in unit_rows), encoding="utf-8"
    )
    _write_selection(
        folder,
        _selection_lines(
            skus,
            args.families,
            excluded,
            unit_rows,
            l3_fails=scenario == "stage_fails",
        ),
        os.environ.get("FAKE_ENGINE_SELECTION", "ok"),
    )
    summary = {
        "complete": False,
        "blocked_stage": "L4",
        "stages": stages,
        "reserve": RESERVE,
        "exclusions": [
            {"sku_id": s, "cause": "la serie termina antes de t*: sin reserva"} for s in excluded
        ],
        "failures": [
            {"stage": r["stage"], "sku_id": r["sku_id"], "family": r["family"], "error": r["error"]}
            for r in unit_rows
            if r["state"] == "fallida"
        ],
    }
    (folder / "corrida.json").write_text(
        json.dumps({"schema": "pred-engine.corrida/1", "run_id": args.run_id, "summary": summary}),
        encoding="utf-8",
    )
    failed = scenario == "stage_fails"
    stream.emit(
        "run_finished",
        state="failed" if failed else "completed",
        exit_code=1 if failed else 7,
        failed_stage="L3" if failed else None,
        blocked_stage="L4",
    )
    return 1 if failed else 7


if __name__ == "__main__":
    sys.exit(main())
