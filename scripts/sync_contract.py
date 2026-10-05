"""Copy the frontend <-> engine data contract from pred-docs into the platform package.

The contract is authored in pred-docs (``diseno/contratos/``). This script copies, byte for byte,
the reference models, the JSON Schemas and the examples (which are the fixtures) into
``src/pred_platform/contract/`` and writes ``contract.lock.json`` with the SHA-256 of every copied
file. Tests verify that lock, so an accidental edit to a copy fails the build.

Before writing anything it checks the source the way pred-docs' CI does, plus one more thing: the
schema exported from the models must equal the published one, which proves the platform's pydantic
produces the same schema the contract was published with.

    uv run python scripts/sync_contract.py            # copy and regenerate the lock
    uv run python scripts/sync_contract.py --check    # report differences, write nothing
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import shutil
import sys
from pathlib import Path
from types import ModuleType

import pydantic
from jsonschema import Draft202012Validator

REPO = Path(__file__).resolve().parent.parent
DEFAULT_SOURCE = REPO.parent / "pred-docs" / "diseno" / "contratos"
DEST = REPO / "src" / "pred_platform" / "contract"
LOCK = DEST / "contract.lock.json"
SCHEMA_WRAPPER_KEYS = ("$schema", "$id", "x-contract-version")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_models(path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location("_contract_source_models", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # pydantic resolves forward references through sys.modules
    spec.loader.exec_module(module)
    return module


def check_source(models: ModuleType, schemas: Path, examples: Path) -> list[str]:
    """Problems that make the source unfit to copy (empty list when it is consistent)."""
    problems: list[str] = []
    documents: dict[str, type[pydantic.BaseModel]] = models.DOCUMENTS
    published: dict[str, dict[str, object]] = {}
    for kind, model in documents.items():
        schema_path = schemas / f"{kind}.schema.json"
        if not schema_path.is_file():
            problems.append(f"missing schema {schema_path.name}")
            continue
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        published[kind] = schema
        bare = {k: v for k, v in schema.items() if k not in SCHEMA_WRAPPER_KEYS}
        if bare != model.model_json_schema():
            problems.append(
                f"{kind}: the schema exported by pydantic {pydantic.VERSION} differs from the "
                "published one"
            )
    for example in sorted(examples.glob("*.json")):
        kind = example.name.split(".")[0]
        if kind not in documents or kind not in published:
            problems.append(f"{example.name}: no model or schema for '{kind}'")
            continue
        data = json.loads(example.read_text(encoding="utf-8"))
        validator = Draft202012Validator(published[kind])
        errors = [e.message for e in validator.iter_errors(data)]
        if errors:
            problems.append(f"{example.name}: fails its JSON Schema ({errors[0]})")
        try:
            documents[kind].model_validate(data)
        except pydantic.ValidationError as exc:
            problems.append(f"{example.name}: fails its model ({exc.errors()[0]['msg']})")
    return problems


def contract_version(schemas: Path) -> str:
    versions = {
        json.loads(p.read_text(encoding="utf-8"))["x-contract-version"]
        for p in schemas.glob("*.schema.json")
    }
    if len(versions) != 1:
        raise SystemExit(f"the schemas declare different contract versions: {sorted(versions)}")
    return versions.pop()


def plan(source: Path) -> dict[Path, Path]:
    """Destination file -> source file, for every file the package must contain."""
    mapping: dict[Path, Path] = {DEST / "models.py": source / "referencia" / "modelos_v1.py"}
    for folder, target in (("schemas/v1", "schemas/v1"), ("ejemplos/v1", "examples/v1")):
        for file in sorted((source / folder).glob("*.json")):
            mapping[DEST / target / file.name] = file
    return mapping


def build_lock(mapping: dict[Path, Path], version: str) -> str:
    lock = {
        "contract_version": version,
        "pydantic_version": pydantic.VERSION,
        "files": {
            dest.relative_to(DEST).as_posix(): sha256(src) for dest, src in sorted(mapping.items())
        },
    }
    return json.dumps(lock, indent=2, ensure_ascii=False) + "\n"


def stale_files(mapping: dict[Path, Path]) -> list[Path]:
    """Copies in the package whose source no longer exists."""
    wanted = set(mapping)
    found = [p for sub in ("schemas", "examples") for p in (DEST / sub).rglob("*.json")]
    return sorted(p for p in found if p not in wanted)


def sync(source: Path, *, check: bool) -> int:
    for needed in ("referencia/modelos_v1.py", "schemas/v1", "ejemplos/v1"):
        if not (source / needed).exists():
            print(f"error: {source / needed} does not exist", file=sys.stderr)
            return 2

    models = load_models(source / "referencia" / "modelos_v1.py")
    problems = check_source(models, source / "schemas" / "v1", source / "ejemplos" / "v1")
    if problems:
        print("The contract source is inconsistent:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1

    mapping = plan(source)
    version = contract_version(source / "schemas" / "v1")
    lock = build_lock(mapping, version)

    changes = [
        dest.relative_to(DEST).as_posix()
        for dest, src in mapping.items()
        if not dest.is_file() or dest.read_bytes() != src.read_bytes()
    ]
    removals = stale_files(mapping)
    lock_changed = not LOCK.is_file() or LOCK.read_text(encoding="utf-8") != lock

    if check:
        for name in changes:
            print(f"differs: {name}")
        for path in removals:
            print(f"stale:   {path.relative_to(DEST).as_posix()}")
        if lock_changed:
            print("differs: contract.lock.json")
        if changes or removals or lock_changed:
            print("The platform's copy is out of sync. Run `make sync-contract`.")
            return 1
        print(f"In sync with contract v{version} ({len(mapping)} files).")
        return 0

    for dest, src in mapping.items():
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
    for path in removals:
        path.unlink()
    LOCK.write_text(lock, encoding="utf-8")
    print(
        f"Contract v{version}: {len(mapping)} files copied, {len(changes)} changed, "
        f"{len(removals)} removed."
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--source", type=Path, default=DEFAULT_SOURCE, help="pred-docs contracts dir"
    )
    parser.add_argument("--check", action="store_true", help="only report differences")
    args = parser.parse_args()
    return sync(args.source.resolve(), check=args.check)


if __name__ == "__main__":
    raise SystemExit(main())
