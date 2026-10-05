"""Tests for runtime settings."""

from pathlib import Path

import pytest

from pred_platform.config import (
    DATA_ROOT_VAR,
    DATA_SOURCE_VAR,
    DB_PATH_VAR,
    FIXTURE_SCENARIO_VAR,
    Settings,
)


def test_defaults_when_environment_is_empty() -> None:
    settings = Settings.from_env({})
    assert settings.data_root == Path("data")
    assert settings.db_path == Path("data") / "pred.db"


def test_db_path_follows_data_root() -> None:
    settings = Settings.from_env({DATA_ROOT_VAR: "/srv/pred"})
    assert settings.data_root == Path("/srv/pred")
    assert settings.db_path == Path("/srv/pred") / "pred.db"


def test_explicit_db_path_wins() -> None:
    settings = Settings.from_env({DATA_ROOT_VAR: "/srv/pred", DB_PATH_VAR: "/var/lib/pred.db"})
    assert settings.db_path == Path("/var/lib/pred.db")


def test_blank_values_count_as_unset() -> None:
    settings = Settings.from_env({DATA_ROOT_VAR: "  ", DB_PATH_VAR: ""})
    assert settings == Settings.from_env({})


def test_reads_process_environment_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(DATA_ROOT_VAR, "/tmp/pred-data")
    monkeypatch.delenv(DB_PATH_VAR, raising=False)
    assert Settings.from_env().data_root == Path("/tmp/pred-data")


def test_the_real_dal_is_the_default_source() -> None:
    settings = Settings.from_env({})
    assert settings.data_source == "dal"
    assert settings.fixture_scenario == "normal"


def test_fixture_source_and_scenario_are_read_from_the_environment() -> None:
    settings = Settings.from_env({DATA_SOURCE_VAR: "fixture", FIXTURE_SCENARIO_VAR: "errores"})
    assert (settings.data_source, settings.fixture_scenario) == ("fixture", "errores")


def test_blank_source_and_scenario_count_as_unset() -> None:
    assert Settings.from_env({DATA_SOURCE_VAR: " ", FIXTURE_SCENARIO_VAR: ""}) == Settings.from_env(
        {}
    )


def test_an_unknown_source_fails_with_the_valid_options() -> None:
    with pytest.raises(ValueError, match=r"PRED_DATA_SOURCE='mock'.*\['dal', 'fixture'\]"):
        Settings.from_env({DATA_SOURCE_VAR: "mock"})


def test_an_unknown_scenario_fails_only_when_fixtures_are_used() -> None:
    with pytest.raises(ValueError, match=r"PRED_FIXTURE_SCENARIO='otro'.*normal"):
        Settings.from_env({DATA_SOURCE_VAR: "fixture", FIXTURE_SCENARIO_VAR: "otro"})
    assert Settings.from_env({FIXTURE_SCENARIO_VAR: "otro"}).data_source == "dal"
