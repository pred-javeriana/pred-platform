"""Explicit errors: what the loader and the error classes do with bad data."""

import json

import pytest

from pred_platform.contract import CONTRACT_MAJOR, CONTRACT_VERSION, models
from pred_platform.contract.errors import (
    ArtifactMissing,
    ContractInvalid,
    DataLayerError,
    EngineUnavailable,
    InvalidQuery,
    SchemaVersionUnsupported,
)
from pred_platform.contract.loader import parse_as
from pred_platform.contract.resources import example_names, read_example


def test_the_contract_version_is_the_locked_one() -> None:
    assert CONTRACT_VERSION == "1.0.0"
    assert CONTRACT_MAJOR == 1


@pytest.mark.parametrize("name", example_names())
def test_loads_every_example_from_text_and_from_a_mapping(name: str) -> None:
    model = models.DOCUMENTS[name.split(".")[0]]
    from_text = parse_as(model, read_example(name))
    from_mapping = parse_as(model, json.loads(read_example(name)))
    assert from_text == from_mapping


def test_unreadable_json_is_a_contract_error() -> None:
    with pytest.raises(ContractInvalid, match="JSON ilegible"):
        parse_as(models.RunStatus, "{not json")


def test_something_that_is_not_an_object_is_a_contract_error() -> None:
    with pytest.raises(ContractInvalid, match="objeto JSON"):
        parse_as(models.RunStatus, "[1, 2, 3]")


def test_a_validation_failure_names_the_first_failing_path() -> None:
    document = json.loads(read_example("topology_report.normal"))
    document["items"][0]["adi"] = 0
    with pytest.raises(ContractInvalid) as raised:
        parse_as(models.TopologyReport, document)
    assert raised.value.field == "items.0.adi"
    assert "items.0.adi" in str(raised.value)


def test_another_major_version_is_unsupported_not_just_invalid() -> None:
    document = json.loads(read_example("run_status.normal"))
    document["schema_version"] = "2.0.0"
    with pytest.raises(SchemaVersionUnsupported) as raised:
        parse_as(models.RunStatus, document)
    assert raised.value.field == "schema_version"
    assert raised.value.code == "platform.schema_version_unsupported"


def test_a_newer_minor_version_of_the_same_major_is_accepted() -> None:
    document = json.loads(read_example("run_status.normal"))
    document["schema_version"] = "1.7.3"
    assert parse_as(models.RunStatus, document).schema_version == "1.7.3"


@pytest.mark.parametrize("version", [None, 1, "", "x.y.z", "v1.0.0"])
def test_a_missing_or_malformed_version_is_a_contract_error(version: object) -> None:
    document = json.loads(read_example("run_status.normal"))
    document["schema_version"] = version
    with pytest.raises(ContractInvalid) as raised:
        parse_as(models.RunStatus, document)
    assert raised.value.field == "schema_version"


def test_a_missing_version_key_is_a_contract_error() -> None:
    document = json.loads(read_example("run_status.normal"))
    del document["schema_version"]
    with pytest.raises(ContractInvalid, match="schema_version"):
        parse_as(models.RunStatus, document)


def test_the_error_document_has_no_version_envelope() -> None:
    error = parse_as(models.ErrorInfo, read_example("error.schema_barrier"))
    assert error.code == "ingest.schema_barrier"


# ---------------------------------------------------------------------- the error classes
@pytest.mark.parametrize(
    ("error_class", "code"),
    [
        (ContractInvalid, "platform.contract_invalid"),
        (SchemaVersionUnsupported, "platform.schema_version_unsupported"),
        (ArtifactMissing, "platform.artifact_missing"),
        (EngineUnavailable, "platform.engine_unavailable"),
    ],
)
def test_each_error_carries_its_contract_code(error_class: type[DataLayerError], code: str) -> None:
    error = error_class("algo salió mal", field="items.0")
    info = error.to_error_info()
    assert error.code == code
    assert code in str(error)
    assert (info.code, info.stage, info.field) == (code, "platform", "items.0")
    assert info.source_exception == error_class.__name__


def test_blocked_by_names_the_open_gaps_in_the_detail() -> None:
    error = ArtifactMissing("No hay informe de ingesta", blocked_by=["G1"])
    assert error.blocked_by == ("G1",)
    assert "G1" in (error.to_error_info().detail or "")


def test_only_the_engine_error_is_marked_as_retryable() -> None:
    assert EngineUnavailable("x").to_error_info().retryable is True
    assert ArtifactMissing("x").to_error_info().retryable is False


def test_an_invalid_query_is_a_value_error_and_not_a_data_error() -> None:
    assert issubclass(InvalidQuery, ValueError)
    assert not issubclass(InvalidQuery, DataLayerError)
