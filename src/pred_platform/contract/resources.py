"""Read the packaged copies of the contract's examples and schemas, of every major version."""

from __future__ import annotations

import json
from functools import cache
from importlib.resources import files
from importlib.resources.abc import Traversable
from typing import Any

from pydantic import BaseModel

from pred_platform.contract import models, models_v2

# Each major's folder and models. Document kinds are unique across versions (v2 only adds m3_*).
_MAJORS: dict[str, dict[str, type[BaseModel]]] = {"v1": models.DOCUMENTS, "v2": models_v2.DOCUMENTS}


def _root() -> Traversable:
    return files("pred_platform.contract")


@cache
def _example_folders() -> dict[str, str]:
    """Example name (without extension) -> its major folder."""
    return {
        item.name.removesuffix(".json"): major
        for major in _MAJORS
        for item in (_root() / "examples" / major).iterdir()
    }


def _major_of(kind: str) -> str:
    for major, documents in _MAJORS.items():
        if kind in documents:
            return major
    raise KeyError(f"no contract document named {kind!r}")


def document_model(kind: str) -> type[BaseModel]:
    """The model of one document kind (for example ``run_status`` or ``m3_evidence``)."""
    return _MAJORS[_major_of(kind)][kind]


def document_kinds() -> tuple[str, ...]:
    """Every document kind the contract publishes, v1 first."""
    return tuple(kind for documents in _MAJORS.values() for kind in documents)


def example_names() -> tuple[str, ...]:
    """Names of the packaged examples, without extension (for example ``run_status.normal``)."""
    return tuple(sorted(_example_folders()))


def read_example(name: str) -> str:
    """The JSON text of one example. Raises ``KeyError`` if there is none with that name."""
    major = _example_folders().get(name)
    if major is None:
        raise KeyError(f"no packaged example named {name!r}")
    return (_root() / "examples" / major / f"{name}.json").read_text(encoding="utf-8")


def read_schema(kind: str) -> dict[str, Any]:
    """The published JSON Schema of one document kind (for example ``run_status``)."""
    path = _root() / "schemas" / _major_of(kind) / f"{kind}.schema.json"
    return json.loads(path.read_text(encoding="utf-8"))
