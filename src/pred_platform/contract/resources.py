"""Read the packaged copies of the contract's examples and schemas."""

from __future__ import annotations

import json
from functools import cache
from importlib.resources import files
from importlib.resources.abc import Traversable
from typing import Any

_VERSION_DIR = "v1"


def _root() -> Traversable:
    return files("pred_platform.contract")


@cache
def example_names() -> tuple[str, ...]:
    """Names of the packaged examples, without extension (for example ``run_status.normal``)."""
    folder = _root() / "examples" / _VERSION_DIR
    return tuple(sorted(item.name.removesuffix(".json") for item in folder.iterdir()))


def read_example(name: str) -> str:
    """The JSON text of one example. Raises ``KeyError`` if there is none with that name."""
    if name not in example_names():
        raise KeyError(f"no packaged example named {name!r}")
    return (_root() / "examples" / _VERSION_DIR / f"{name}.json").read_text(encoding="utf-8")


def read_schema(kind: str) -> dict[str, Any]:
    """The published JSON Schema of one document kind (for example ``run_status``)."""
    path = _root() / "schemas" / _VERSION_DIR / f"{kind}.schema.json"
    return json.loads(path.read_text(encoding="utf-8"))
