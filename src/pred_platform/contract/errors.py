"""Explicit errors of the data layer, with the codes of the contract (section 11)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, ClassVar

from pydantic import ValidationError

from pred_platform.contract.models import ErrorInfo


class DataLayerError(Exception):
    """A failure of the data layer, identified by a stable ``code``.

    The platform translates ``code`` into a Spanish message and a suggested action; ``detail`` is
    the technical text and is not shown to users as is. ``blocked_by`` names the open gaps
    (``G1`` to ``G8``) behind a missing document, so a view can say why something is unavailable.
    """

    code: ClassVar[str]
    retryable: ClassVar[bool] = False

    def __init__(
        self,
        detail: str,
        *,
        field: str | None = None,
        blocked_by: Sequence[str] = (),
    ) -> None:
        self.detail = detail
        self.field = field
        self.blocked_by = tuple(blocked_by)
        super().__init__(f"{self.code}: {self._full_detail()}")

    def _full_detail(self) -> str:
        if not self.blocked_by:
            return self.detail
        return f"{self.detail} (hueco abierto: {', '.join(self.blocked_by)})"

    def to_error_info(self) -> ErrorInfo:
        """The normalized error of the contract, ready to be translated for the user."""
        return ErrorInfo(
            code=self.code,
            stage="platform",
            detail=self._full_detail(),
            field=self.field,
            source_exception=type(self).__name__,
            retryable=self.retryable,
        )


class ContractInvalid(DataLayerError):
    """The data is unreadable or does not satisfy the contract."""

    code = "platform.contract_invalid"


class SchemaVersionUnsupported(DataLayerError):
    """The document declares a major contract version this platform does not understand."""

    code = "platform.schema_version_unsupported"


class ArtifactMissing(DataLayerError):
    """The requested document does not exist, or depends on an open gap."""

    code = "platform.artifact_missing"


class EngineUnavailable(DataLayerError):
    """The operation needs the engine, which is not connected yet."""

    code = "platform.engine_unavailable"
    retryable = True


class InvalidQuery(ValueError):
    """The caller passed a bad parameter (page, size, sort key...). A bug, not a data problem."""


# The reason for each pydantic error type, in Spanish; ``{name}`` takes the error's context.
_REASONS = {
    "missing": "falta el campo",
    "extra_forbidden": "campo no permitido",
    "greater_than": "debe ser mayor que {gt}",
    "greater_than_equal": "debe ser mayor o igual que {ge}",
    "less_than": "debe ser menor que {lt}",
    "less_than_equal": "debe ser menor o igual que {le}",
    "multiple_of": "debe ser múltiplo de {multiple_of}",
    "finite_number": "debe ser un número finito",
    "literal_error": "debe ser {expected}",
    "enum": "debe ser {expected}",
    "string_too_short": "debe tener al menos {min_length} caracteres",
    "string_too_long": "admite como máximo {max_length} caracteres",
    "string_pattern_mismatch": "no tiene el formato esperado",
    "too_short": "debe tener al menos {min_length} elementos",
    "too_long": "admite como máximo {max_length} elementos",
    "string_type": "debe ser texto",
    "int_type": "debe ser un número entero",
    "int_parsing": "debe ser un número entero",
    "int_from_float": "debe ser un número entero",
    "float_type": "debe ser un número",
    "float_parsing": "debe ser un número",
    "bool_type": "debe ser verdadero o falso",
    "bool_parsing": "debe ser verdadero o falso",
    "list_type": "debe ser una lista",
    "tuple_type": "debe ser una lista",
    "dict_type": "debe ser un objeto",
    "model_type": "debe ser un objeto",
    "model_attributes_type": "debe ser un objeto",
    "date_type": "debe ser una fecha AAAA-MM-DD",
    "date_parsing": "debe ser una fecha AAAA-MM-DD",
    "date_from_datetime_parsing": "debe ser una fecha AAAA-MM-DD",
    "date_from_datetime_inexact": "debe ser una fecha sin hora",
    "datetime_type": "debe ser una fecha y hora ISO 8601",
    "datetime_parsing": "debe ser una fecha y hora ISO 8601",
    "datetime_from_date_parsing": "debe ser una fecha y hora ISO 8601",
    "timezone_aware": "debe llevar zona horaria",
    "none_required": "debe ser nulo",
}


def _context_text(value: Any) -> str:
    if isinstance(value, float):
        return repr(int(value) if value.is_integer() else value).replace(".", ",")
    if isinstance(value, str):
        return value.replace(" or ", " o ")  # the options of a literal: "'a', 'b' or 'c'"
    return str(value)


@dataclass(frozen=True, slots=True)
class ValidationProblem:
    """The first problem pydantic found in a document, in Spanish: where it is and why."""

    field: str | None
    reason: str

    def __str__(self) -> str:
        return f"{self.field or '(raíz)'}: {self.reason}"


def first_problem(exc: ValidationError) -> ValidationProblem:
    """The first error of ``exc`` as a field path and a Spanish reason.

    Every place where external data meets a pydantic model reports its failure through this, so a
    page never shows pydantic's English text. A rule the platform wrote (``value_error``) is
    already Spanish and is shown as it is.
    """
    error = exc.errors()[0]
    field = ".".join(str(part) for part in error["loc"]) or None
    context = error.get("ctx") or {}
    if error["type"] in ("value_error", "assertion_error") and "error" in context:
        return ValidationProblem(field, str(context["error"]))
    template = _REASONS.get(error["type"], "valor no válido")
    texts = {key: _context_text(value) for key, value in context.items()}
    try:
        return ValidationProblem(field, template.format(**texts))
    except (KeyError, IndexError):
        return ValidationProblem(field, "valor no válido")
