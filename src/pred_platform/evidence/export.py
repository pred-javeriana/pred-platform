"""Strict JSON of group results and the source keys behind every number."""

from __future__ import annotations

import math
from dataclasses import asdict
from typing import Any

from pred_platform.evidence.models import GroupEvidence


def document(result: GroupEvidence) -> dict[str, Any]:
    """Retain source row keys; infinite effect ratios are explicit strings."""

    def clean(value: Any) -> Any:
        if isinstance(value, float) and math.isinf(value):
            return "infinito" if value > 0 else "-infinito"
        if isinstance(value, dict):
            return {k: clean(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [clean(v) for v in value]
        return value

    return clean(asdict(result))
