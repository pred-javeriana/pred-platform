"""Ingest a CSV uploaded in the browser by running the engine (TASK-UI-1.2-C1, upload).

The platform never imports the engine: it saves the file in a temporary folder under
``<data_root>/staging/`` and runs ``pred-engine classify --csv <file>`` (the command is
``Settings.engine_command``), the engine path for CSVs with the four canonical headers that needs
no LLM. The engine deposits the file in ``raw/``, publishes the Parquet and always leaves
``ingestas/<sha256>.json`` (also when the ingestion is rejected, failed or needs confirmation);
that report is then imported into the DAL like any other (G1) and the views read it from there.

Exit code 7 is an unconfirmed name conflict (G5): the report says ``needs_confirmation`` and the
caller asks the operator, then repeats with ``confirm_overwrite`` (the engine keeps both files).
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable
from pathlib import Path, PurePosixPath

from pred_platform.config import ENGINE_CALL_TIMEOUT_S, Settings
from pred_platform.dal.importacion import ReportImportError, import_reports

MAX_BYTES = 256 * 1024 * 1024
# Under the data root: browser uploads wait here for the engine or for a confirmation.
STAGING_DIR = "staging"
# The headers of the CSV path that needs no LLM (`pred-engine classify`, guide 16.7).
CANONICAL_HEADERS = ("sku_id", "timestamp", "demand_qty", "lead_time_days")

# ejemplos/panel_demo.csv is synthetic. Match its contents after newline and BOM
# normalization, including when it is uploaded under a different name.
_DEMO_SHA256 = "f35cd0368f88ef550097b8ce058a2e9af248fcff34d24d2147524db7aee0dd3d"
_DEMO_MAX_BYTES = 63_349  # CRLF and a UTF-8 BOM

Submitter = Callable[[str, bytes, bool], str]


class IngestLaunchError(Exception):
    """The engine could not ingest the file (not installed, crashed, timed out, no report)."""


def safe_name(file_name: str) -> str:
    """The base name of an uploaded file; ``ValueError`` if it cannot be a CSV name.

    Only the last path component is kept, so a name such as ``../../etc/x.csv`` cannot leave the
    temporary folder.
    """
    name = PurePosixPath(file_name.replace("\\", "/")).name.strip()
    if not name or name in {".", ".."}:
        raise ValueError("El archivo debe tener un nombre.")
    if not name.lower().endswith(".csv"):
        raise ValueError("El archivo debe tener extensión .csv.")
    return name


def _tail(text: str, lines: int = 6) -> str:
    return "\n".join(text.strip().splitlines()[-lines:])


def submit_csv(
    settings: Settings,
    file_name: str,
    content: bytes,
    *,
    confirm_overwrite: bool = False,
    timeout: float = ENGINE_CALL_TIMEOUT_S,
) -> str:
    """Ingest ``content`` with the engine and import its report. Returns the file's SHA-256.

    Raises:
        ValueError: the name is not a CSV name or the file is empty or too large.
        IngestLaunchError: the engine could not leave a report for this file.
    """
    name = safe_name(file_name)
    if not content:
        raise ValueError("El archivo está vacío.")
    if len(content) > MAX_BYTES:
        raise ValueError(f"El archivo supera el límite de {MAX_BYTES // (1024 * 1024)} MiB.")
    sha256 = hashlib.sha256(content).hexdigest()
    root = settings.data_root.resolve()
    staging = root / STAGING_DIR
    staging.mkdir(parents=True, exist_ok=True)
    folder = Path(tempfile.mkdtemp(prefix="carga-", dir=staging))
    try:
        path = folder / name
        path.write_bytes(content)
        command = [
            *settings.engine_command,
            "classify",
            "--csv",
            str(path),
            "--data-root",
            str(root),
        ]
        if confirm_overwrite:
            command.append("--confirm-overwrite")
        if len(content) <= _DEMO_MAX_BYTES and (
            hashlib.sha256(
                content.removeprefix(b"\xef\xbb\xbf").replace(b"\r\n", b"\n")
            ).hexdigest()
            == _DEMO_SHA256
        ):
            command.append("--datos-sinteticos")
        started = time.time()
        try:
            done = subprocess.run(  # noqa: S603 - the command comes from trusted settings
                command, capture_output=True, text=True, timeout=timeout, check=False
            )
        except subprocess.TimeoutExpired as exc:
            raise IngestLaunchError(
                f"El motor tardó más de {int(timeout)} s en procesar el archivo; "
                "inténtelo de nuevo."
            ) from exc
        except OSError as exc:
            raise IngestLaunchError(
                f"No se pudo lanzar el motor ({' '.join(settings.engine_command)}): {exc}"
            ) from exc
    finally:
        shutil.rmtree(folder, ignore_errors=True)

    report = root / "ingestas" / f"{sha256}.json"
    if not report.is_file():
        raise IngestLaunchError(_no_report_message(done))
    if report.stat().st_mtime < started - 2:
        # A report older than this launch belongs to an earlier ingestion of the same file. The
        # engine keeps an accepted one when a retry of the same bytes fails.
        if _report_status(report) == "accepted":
            detail = _tail(done.stderr or done.stdout)
            raise IngestLaunchError(
                "El nuevo intento falló; la ingesta aceptada de este archivo sigue vigente."
                + (f" {detail}" if detail else "")
            )
        raise IngestLaunchError(_no_report_message(done))
    try:
        result = import_reports(settings.db_path, settings.data_root)
    except ReportImportError as exc:
        raise IngestLaunchError(f"No se pudo importar el informe de la ingesta: {exc}") from exc
    if result.failed:
        raise IngestLaunchError(f"El informe de la ingesta no es válido: {result.failed[0]}")
    return sha256


def _no_report_message(done: subprocess.CompletedProcess[str]) -> str:
    detail = _tail(done.stderr or done.stdout)
    return f"El motor terminó con código {done.returncode} sin dejar el informe de la ingesta." + (
        f" {detail}" if detail else ""
    )


def _report_status(report: Path) -> object:
    try:
        data = json.loads(report.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data.get("status") if isinstance(data, dict) else None


def make_submitter(settings: Settings) -> Submitter:
    """The ``(file_name, content, confirm_overwrite) -> sha256`` callable of the repository."""

    def submit(file_name: str, content: bytes, confirm_overwrite: bool) -> str:
        return submit_csv(settings, file_name, content, confirm_overwrite=confirm_overwrite)

    return submit
