"""Procesos nativos de Windows: consulta sin señales y cierre de todo el árbol."""

from __future__ import annotations

import csv
import io
import subprocess
import sys
import time
from pathlib import Path

import pytest

from pred_platform.config import Settings
from pred_platform.worker import engine

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="requiere Windows nativo")
_HIDDEN = 0x08000000


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        (r"C:\pred\python.exe C:\pred\engine.py", (r"C:\pred\python.exe", r"C:\pred\engine.py")),
        (
            r'"C:\Program Files\PRED\python.exe" "C:\Mis datos\engine.py"',
            (r"C:\Program Files\PRED\python.exe", r"C:\Mis datos\engine.py"),
        ),
    ],
)
def test_engine_command_preserves_windows_paths(command: str, expected: tuple[str, ...]) -> None:
    assert Settings.from_env({"PRED_ENGINE_CMD": command}).engine_command == expected


def _listed(pid: int) -> bool:
    result = subprocess.run(
        ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
        capture_output=True,
        text=True,
        errors="replace",
        check=True,
        creationflags=_HIDDEN,
    )
    return any(
        len(row) > 1 and row[1] == str(pid) for row in csv.reader(io.StringIO(result.stdout))
    )


def _cleanup(pid: int) -> None:
    subprocess.run(
        ["taskkill", "/PID", str(pid), "/T", "/F"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
        creationflags=_HIDDEN,
    )


def test_checking_a_live_pid_does_not_stop_the_process() -> None:
    process = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"], creationflags=_HIDDEN
    )
    try:
        assert engine.is_alive(process.pid)
        time.sleep(0.1)
        assert process.poll() is None, "Consultar el PID no debe terminar el proceso"
    finally:
        _cleanup(process.pid)
        process.wait(timeout=10)
    assert not engine.is_alive(process.pid)


def test_stopping_the_engine_also_stops_its_child_process(tmp_path: Path) -> None:
    marker = tmp_path / "child.pid"
    code = (
        "import subprocess,sys,time; from pathlib import Path; "
        "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); "
        "Path(sys.argv[1]).write_text(str(child.pid)); time.sleep(60)"
    )
    process = subprocess.Popen([sys.executable, "-c", code, str(marker)], creationflags=_HIDDEN)
    child_pid = None
    try:
        deadline = time.monotonic() + 10
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        child_pid = int(marker.read_text())
        assert _listed(child_pid)
        engine.terminate(process.pid)
        process.wait(timeout=10)
        assert not engine.is_alive(child_pid), "Detener debe esperar a los procesos descendientes"
    finally:
        _cleanup(process.pid)
        if child_pid is not None:
            _cleanup(child_pid)
        process.wait(timeout=10)
