"""The packaged data contract: pinned copies, valid examples and rejected invalid cases."""

import copy
import hashlib
import json
from collections.abc import Callable
from importlib.resources import files
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from pred_platform.contract import models
from pred_platform.contract.resources import example_names, read_example, read_schema

_ROOT = files("pred_platform.contract")
_LOCK = json.loads((_ROOT / "contract.lock.json").read_text(encoding="utf-8"))
_SCHEMA_WRAPPER_KEYS = ("$schema", "$id", "x-contract-version")


def _kind(example: str) -> str:
    return example.split(".")[0]


def _load(example: str) -> dict[str, Any]:
    return json.loads(read_example(example))


# --------------------------------------------------------------------- the pinned copies
def test_lock_lists_exactly_the_packaged_files() -> None:
    packaged = {"models.py"}
    packaged |= {f"schemas/v1/{kind}.schema.json" for kind in models.DOCUMENTS}
    packaged |= {f"examples/v1/{name}.json" for name in example_names()}
    assert set(_LOCK["files"]) == packaged


@pytest.mark.parametrize("relative", sorted(_LOCK["files"]))
def test_packaged_file_matches_the_lock(relative: str) -> None:
    digest = hashlib.sha256((_ROOT / relative).read_bytes()).hexdigest()
    assert digest == _LOCK["files"][relative], (
        f"{relative} differs from contract.lock.json: the contract is changed in pred-docs and "
        "copied with `make sync-contract`, never edited here"
    )


def test_every_schema_declares_the_locked_contract_version() -> None:
    for kind in models.DOCUMENTS:
        assert read_schema(kind)["x-contract-version"] == _LOCK["contract_version"]


# ---------------------------------------------------------------------- valid documents
@pytest.mark.parametrize("name", example_names())
def test_example_validates_against_its_model(name: str) -> None:
    models.DOCUMENTS[_kind(name)].model_validate(_load(name))


@pytest.mark.parametrize("name", example_names())
def test_example_validates_against_its_published_schema(name: str) -> None:
    validator = Draft202012Validator(
        read_schema(_kind(name)), format_checker=Draft202012Validator.FORMAT_CHECKER
    )
    assert [e.message for e in validator.iter_errors(_load(name))] == []


@pytest.mark.parametrize("kind", sorted(models.DOCUMENTS))
def test_exported_schema_equals_the_published_one(kind: str) -> None:
    published = {k: v for k, v in read_schema(kind).items() if k not in _SCHEMA_WRAPPER_KEYS}
    assert published == models.DOCUMENTS[kind].model_json_schema(), (
        f"the schema pydantic exports for {kind} differs from the published one; "
        "run `make sync-contract` against an up-to-date pred-docs"
    )


def test_a_previous_synthetic_log_without_the_new_fields_is_still_valid() -> None:
    previous = _load("synthetic_run.bitacora_anterior")
    assert "metodo" not in previous["log"]
    models.SyntheticRun.model_validate(previous)


@pytest.mark.parametrize("name", ["run_status.normal", "run_status.cinco_estados"])
def test_run_status_tasks_are_unique_per_sku_model_and_cut(name: str) -> None:
    keys = [(t["sku"], t["modelo"], t["corte"]) for t in _load(name)["tasks"]]
    assert len(keys) == len(set(keys))


# -------------------------------------------------------------------- invalid documents
Mutation = Callable[[dict[str, Any]], object]

T = "topology_report.normal"
R = "run_status.normal"
C5 = "run_status.cinco_estados"
L = "sku_selection.lumpy_solo_clasica"
S4 = "sku_selection.smooth_cuatro_familias"
SY = "synthetic_run.normal"
V = "validation_verdicts.con_veredictos"

# Each case breaks one rule of the contract. The reference models enforce them all; several are
# cross-field rules that JSON Schema cannot express, which is why the models are the source.
INVALID: dict[str, tuple[str, Mutation]] = {
    "unavailable without a reason": (
        T,
        lambda d: d["availability"].update(status="unavailable"),
    ),
    "not_implemented without blocked_by": (
        "topology_report.vacio",
        lambda d: d["availability"].update(blocked_by=[]),
    ),
    "source engine (it is dal or fixture)": (T, lambda d: d.update(source="engine")),
    "unknown sku_class": (T, lambda d: d["items"][0].update(sku_class="Smooth")),
    "non positive adi": (T, lambda d: d["items"][0].update(adi=0)),
    "evidence.family differs from family": (
        L,
        lambda d: d["families"][0]["evidence"].update(family="ml"),
    ),
    "failed family without error": (L, lambda d: d["families"][0].update(estado="fallida")),
    "excluded family without exclusion": (L, lambda d: d["families"][1].pop("exclusion")),
    "engine task state instead of the DAL's": (
        R,
        lambda d: d["tasks"][0].update(estado="completada"),
    ),
    "failed task without error": (C5, lambda d: d["tasks"][3].pop("error")),
    "running task with an end time": (
        C5,
        lambda d: d["tasks"][1].update(finalizada_en="2026-10-02T07:00:00Z"),
    ),
    "engine verdict instead of the DAL's": (V, lambda d: d["items"][0].update(veredicto="hold")),
    "engine severity instead of the DAL's": (
        "ingest_report.failed",
        lambda d: d["quality_log"][1].update(severity="warning"),
    ),
    "major version 2": (R, lambda d: d.update(schema_version="2.0.0")),
    "extra field": (R, lambda d: d.update(foo=1)),
    "malformed sha256": ("ingest_report.accepted", lambda d: d.update(ingest_id="abc")),
    "model without family": (R, lambda d: d["tasks"][0].update(modelo="SARIMA")),
    "model with capitals": (R, lambda d: d["tasks"][0].update(modelo="classical:SARIMA")),
    "unknown family in model": (
        R,
        lambda d: d["tasks"][0].update(modelo="hybrid:prophet", family=None),
    ),
    "family differs from the model prefix": (R, lambda d: d["tasks"][0].update(family="ml")),
    "cut as dd/mm/yyyy": (R, lambda d: d["tasks"][0].update(corte="02/03/2026")),
    "cut with a time": (R, lambda d: d["tasks"][0].update(corte="2026-03-02T00:00:00Z")),
    "cut that does not exist": (R, lambda d: d["tasks"][0].update(corte="2026-13-45")),
    "window cut with a time": (
        L,
        lambda d: d["families"][0]["walk_forward"]["windows"][0].update(corte="2026-03-02T00:00"),
    ),
    "settings without schema_version": (R, lambda d: d["settings"].pop("schema_version")),
    "champion family differs from its model": (
        S4,
        lambda d: d["champion"].update(family="dl"),
    ),
    "verdict with a free text model": (
        V,
        lambda d: d["items"][0].update(modelo_campeon="SARIMA"),
    ),
    "model without a family prefix (chronos2)": (
        R,
        lambda d: d["tasks"][0].update(modelo="chronos2"),
    ),
    "reserve: first_reserved is not t_star + 1": (
        R,
        lambda d: d["reserve"].update(first_reserved="2026-07-02"),
    ),
    "reserve: reserved_days does not match": (
        R,
        lambda d: d["reserve"].update(reserved_days=44),
    ),
    "reserve: fraction out of (0, 1)": (R, lambda d: d["reserve"].update(fraction=1.0)),
    "reserve: t_star with a time": (
        R,
        lambda d: d["reserve"].update(t_star="2026-06-29T00:00:00Z"),
    ),
    "topology: t_star as dd/mm/yyyy": (T, lambda d: d.update(t_star="29/06/2026")),
    "synthetic log: block_size 0": (SY, lambda d: d["log"].update(block_size=0)),
    "synthetic log: bad semilla_sha256": (SY, lambda d: d["log"].update(semilla_sha256="xyz")),
    "synthetic log: unknown field": (SY, lambda d: d["log"].update(campo_nuevo=1)),
}


@pytest.mark.parametrize(("example", "mutate"), list(INVALID.values()), ids=list(INVALID))
def test_models_reject_an_invalid_document(example: str, mutate: Mutation) -> None:
    document = copy.deepcopy(_load(example))
    mutate(document)
    with pytest.raises(ValidationError):
        models.DOCUMENTS[_kind(example)].model_validate(document)
