"""Turn raw data into a validated contract document, or fail with an explicit error."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ValidationError

from pred_platform.contract import CONTRACT_MAJOR
from pred_platform.contract.errors import ContractInvalid, SchemaVersionUnsupported


def _major(version: object) -> int | None:
    if not isinstance(version, str):
        return None
    head = version.split(".", 1)[0]
    return int(head) if head.isdigit() else None


def parse_as[T: BaseModel](model: type[T], raw: str | bytes | Mapping[str, Any]) -> T:
    """Validate ``raw`` (JSON text or an already decoded mapping) as ``model``.

    The declared ``schema_version`` is checked first: a document of another major version is
    rejected as unsupported instead of failing with an unrelated validation message.

    Raises:
        ContractInvalid: unreadable JSON, not an object, or it does not satisfy the model.
        SchemaVersionUnsupported: the document is of another major contract version.
    """
    name = model.__name__
    if isinstance(raw, str | bytes):
        try:
            data: object = json.loads(raw)
        except ValueError as exc:
            raise ContractInvalid(f"{name}: JSON ilegible ({exc})") from exc
    else:
        data = raw
    if not isinstance(data, Mapping):
        raise ContractInvalid(f"{name}: se esperaba un objeto JSON")

    if "schema_version" in model.model_fields:
        major = _major(data.get("schema_version"))
        if major is None:
            raise ContractInvalid(
                f"{name}: falta schema_version o no es válido", field="schema_version"
            )
        if major != CONTRACT_MAJOR:
            raise SchemaVersionUnsupported(
                f"{name}: el documento es de la versión mayor {major} y la plataforma entiende "
                f"la {CONTRACT_MAJOR}",
                field="schema_version",
            )

    try:
        return model.model_validate(data)
    except ValidationError as exc:
        first = exc.errors()[0]
        path = ".".join(str(part) for part in first["loc"])
        raise ContractInvalid(
            f"{name} no cumple el contrato: {path or '(raíz)'}: {first['msg']}", field=path or None
        ) from exc
