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
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable
from pathlib import Path, PurePosixPath

from pred_platform.config import Settings
from pred_platform.dal.importacion import ReportImportError, import_reports

MAX_BYTES = 256 * 1024 * 1024
DEFAULT_TIMEOUT_S = 900.0

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
    timeout: float = DEFAULT_TIMEOUT_S,
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
        raise ValueError(f"El archivo supera el límite de {MAX_BYTES // (1024 * 1024)} MB.")
    sha256 = hashlib.sha256(content).hexdigest()
    root = settings.data_root.resolve()
    staging = root / "staging"
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
    # A report older than this launch belongs to an earlier ingestion of the same file: the
    # engine died before writing the new one.
    if not report.is_file() or report.stat().st_mtime < started - 2:
        detail = _tail(done.stderr or done.stdout)
        raise IngestLaunchError(
            f"El motor terminó con código {done.returncode} sin dejar el informe de la ingesta."
            + (f" {detail}" if detail else "")
        )
    try:
        result = import_reports(settings.db_path, settings.data_root)
    except ReportImportError as exc:
        raise IngestLaunchError(f"No se pudo importar el informe de la ingesta: {exc}") from exc
    if result.failed:
        raise IngestLaunchError(f"El informe de la ingesta no es válido: {result.failed[0]}")
    return sha256


def make_submitter(settings: Settings) -> Submitter:
    """The ``(file_name, content, confirm_overwrite) -> sha256`` callable of the repository."""

    def submit(file_name: str, content: bytes, confirm_overwrite: bool) -> str:
        return submit_csv(settings, file_name, content, confirm_overwrite=confirm_overwrite)

    return submit
