"""Explicit errors of the data layer, with the codes of the contract (section 11)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import ClassVar

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
