"""Tests for runtime settings."""

from pathlib import Path

import pytest

from pred_platform.config import DATA_ROOT_VAR, DB_PATH_VAR, Settings


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
