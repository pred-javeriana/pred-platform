"""Runtime settings read from environment variables (see .env.example)."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, get_args

from pred_platform.data.scenarios import SCENARIOS

DATA_ROOT_VAR = "PRED_DATA_ROOT"
DB_PATH_VAR = "PRED_DB_PATH"
DATA_SOURCE_VAR = "PRED_DATA_SOURCE"
FIXTURE_SCENARIO_VAR = "PRED_FIXTURE_SCENARIO"

DataSource = Literal["dal", "fixture"]

DEFAULT_DATA_ROOT = "data"
DEFAULT_DB_NAME = "pred.db"
DEFAULT_DATA_SOURCE: DataSource = "dal"
DEFAULT_FIXTURE_SCENARIO = "normal"


@dataclass(frozen=True, slots=True)
class Settings:
    """Where the platform keeps its data.

    ``data_root`` is the same root pred-engine uses (``PRED_DATA_ROOT``), so the platform and
    the engine agree on ``raw/``, ``processed/`` and ``logs/``. ``db_path`` is the SQLite file
    behind the DAL. ``data_source`` picks where the views read from: ``dal`` (the real database,
    the default) or ``fixture`` (the data contract's examples, for building views before the
    engine's results reach the DAL); ``fixture_scenario`` picks which examples.
    """

    data_root: Path
    db_path: Path
    data_source: DataSource = DEFAULT_DATA_SOURCE
    fixture_scenario: str = DEFAULT_FIXTURE_SCENARIO

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> Settings:
        """Build settings from ``environ`` (defaults to ``os.environ``). Blank values are unset.

        Raises:
            ValueError: ``PRED_DATA_SOURCE`` or ``PRED_FIXTURE_SCENARIO`` has an unknown value, so
                a typo fails at startup instead of silently showing the wrong data.
        """
        env = os.environ if environ is None else environ
        data_root = Path(env.get(DATA_ROOT_VAR, "").strip() or DEFAULT_DATA_ROOT)
        raw_db = env.get(DB_PATH_VAR, "").strip()
        db_path = Path(raw_db) if raw_db else data_root / DEFAULT_DB_NAME
        source = env.get(DATA_SOURCE_VAR, "").strip() or DEFAULT_DATA_SOURCE
        if source not in get_args(DataSource):
            options = list(get_args(DataSource))
            raise ValueError(f"{DATA_SOURCE_VAR}={source!r} is not valid; use one of {options}")
        scenario = env.get(FIXTURE_SCENARIO_VAR, "").strip() or DEFAULT_FIXTURE_SCENARIO
        if source == "fixture":
            _check_scenario(scenario)
        return cls(
            data_root=data_root,
            db_path=db_path,
            data_source=source,  # type: ignore[arg-type]  # validated just above
            fixture_scenario=scenario,
        )


def _check_scenario(name: str) -> None:
    if name not in SCENARIOS:
        raise ValueError(
            f"{FIXTURE_SCENARIO_VAR}={name!r} is not valid; use one of {sorted(SCENARIOS)}"
        )
