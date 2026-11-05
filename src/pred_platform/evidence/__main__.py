"""Read the final seed group without starting the app, worker or engine."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from pred_platform.dal.grupo import read_group
from pred_platform.evidence.export import document
from pred_platform.evidence.models import EvidenceInvalid


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Leer la evidencia del grupo final de semillas.")
    parser.add_argument(
        "--db", type=Path, required=True, help="base existente, abierta solo para lectura"
    )
    parser.add_argument(
        "--run",
        action="append",
        required=True,
        help="identificador M3 publicado; repetir por semilla",
    )
    parser.add_argument(
        "--preview",
        action="store_true",
        help="lectura provisional de las semillas 0 y 1, nunca final",
    )
    args = parser.parse_args(argv)
    try:
        result = read_group(args.db, args.run, preview=args.preview)
    except EvidenceInvalid as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(document(result), ensure_ascii=False, allow_nan=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
