"""Runtime settings read from environment variables (see .env.example)."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

DATA_ROOT_VAR = "PRED_DATA_ROOT"
DB_PATH_VAR = "PRED_DB_PATH"

DEFAULT_DATA_ROOT = "data"
DEFAULT_DB_NAME = "pred.db"


@dataclass(frozen=True, slots=True)
class Settings:
    """Where the platform keeps its data.

    ``data_root`` is the same root pred-engine uses (``PRED_DATA_ROOT``), so the platform and
    the engine agree on ``raw/``, ``processed/`` and ``logs/``. ``db_path`` is the SQLite file
    behind the DAL.
    """

    data_root: Path
    db_path: Path

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> Settings:
        """Build settings from ``environ`` (defaults to ``os.environ``). Blank values are unset."""
        env = os.environ if environ is None else environ
        data_root = Path(env.get(DATA_ROOT_VAR, "").strip() or DEFAULT_DATA_ROOT)
        raw_db = env.get(DB_PATH_VAR, "").strip()
        db_path = Path(raw_db) if raw_db else data_root / DEFAULT_DB_NAME
        return cls(data_root=data_root, db_path=db_path)
