"""The cut t* and the late-launch exclusions M1 stores in the published Parquet's metadata.

M1 writes them as JSON under the ``pred_engine`` key of the Parquet schema metadata, next to the
five panel columns. M4 reads them from there (contract v2, section 5) and never infers an
exclusion by comparing SKUs or recomputing launch dates. An older Parquet without that key has no
metadata: that is not zero exclusions.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pyarrow.parquet as pq
from pyarrow import ArrowException

METADATA_KEY = b"pred_engine"


@dataclass(frozen=True, slots=True)
class CutoffMetadata:
    """The cut and every SKU excluded before L2, with the cause M1 recorded."""

    t_star: str
    exclusions: tuple[tuple[str, str], ...]


def read_cutoff_metadata(path: Path) -> CutoffMetadata | None:
    """The metadata of the Parquet at ``path``; ``None`` if the file or its metadata is absent.

    Raises:
        ValueError: the metadata is there but is not the shape M1 writes.
    """
    if not path.is_file():
        return None
    try:
        schema = pq.read_schema(path)
    except (ArrowException, OSError):
        return None
    raw = (schema.metadata or {}).get(METADATA_KEY)
    if raw is None:
        return None
    return _parse(raw)


def _parse(raw: bytes) -> CutoffMetadata:
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ValueError(f"metadatos {METADATA_KEY.decode()} ilegibles: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"metadatos {METADATA_KEY.decode()}: se esperaba un objeto JSON")
    t_star = payload.get("t_star")
    if not isinstance(t_star, str) or len(t_star) != 10:
        raise ValueError(f"metadatos {METADATA_KEY.decode()}: t_star debe ser AAAA-MM-DD")
    try:
        date.fromisoformat(t_star)
    except ValueError as exc:
        raise ValueError(f"metadatos {METADATA_KEY.decode()}: t_star debe ser AAAA-MM-DD") from exc
    items = payload.get("exclusions", [])
    if not isinstance(items, list):
        raise ValueError(f"metadatos {METADATA_KEY.decode()}: exclusions debe ser una lista")
    exclusions: list[tuple[str, str]] = []
    for item in items:
        if not (
            isinstance(item, dict)
            and isinstance(item.get("sku_id"), str)
            and isinstance(item.get("cause"), str)
        ):
            raise ValueError(
                f"metadatos {METADATA_KEY.decode()}: cada exclusión necesita sku_id y cause"
            )
        exclusions.append((item["sku_id"], item["cause"]))
    return CutoffMetadata(t_star=t_star, exclusions=tuple(exclusions))
