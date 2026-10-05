"""Choose the data source from the settings: this is the whole fixture/real switch."""

from __future__ import annotations

from pred_platform.config import Settings
from pred_platform.data.dal import DalRepository
from pred_platform.data.fixtures import FixtureRepository
from pred_platform.data.repository import DataRepository


def build_repository(settings: Settings) -> DataRepository:
    """``FixtureRepository`` when ``data_source`` is ``fixture``, otherwise ``DalRepository``."""
    if settings.data_source == "fixture":
        return FixtureRepository(settings.fixture_scenario)
    return DalRepository(settings.db_path)
