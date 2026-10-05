# pred-platform

PRED product platform: server-rendered FastAPI + Jinja2 UI, session auth (bcrypt),
single DAL over SQLite, and task-table run orchestration.

## Requirements

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)
- `make` (optional: every target below is a thin wrapper over a `uv run` command)

## Quick start

```bash
uv sync --extra dev --locked   # install the pinned dependencies
cp .env.example .env           # optional; defaults work without it
make run                       # http://localhost:8000
```

`make run` is `uv run uvicorn pred_platform.app.main:app --reload`. It loads `.env` when present.
The app needs no network access at runtime: styles, scripts and fonts are served from this repo.

## Configuration

Environment variables (see `.env.example`):

| Variable | Default | Purpose |
|----------|---------|---------|
| `PRED_DATA_ROOT` | `data` | Data tree shared with pred-engine (`raw/`, `processed/`, `logs/`) |
| `PRED_DB_PATH` | `<PRED_DATA_ROOT>/pred.db` | SQLite file behind the DAL |
| `PRED_DATA_SOURCE` | `dal` | Where the views read from: `dal` (real) or `fixture` (sample data) |
| `PRED_FIXTURE_SCENARIO` | `normal` | Sample data served by `fixture`: `normal`, `vacio` or `errores` |

An unknown `PRED_DATA_SOURCE` or `PRED_FIXTURE_SCENARIO` stops the app at startup with the valid options.

## Data access

Views never touch SQLite or the engine's files. They read through a `DataRepository`
(`pred_platform.data`) that returns documents of the frontend <-> engine data contract
(`pred_platform.contract`, v1.0.0, authored in `pred-docs/diseno/contratos/`).

```python
from fastapi import Depends
from pred_platform.data.deps import get_repository
from pred_platform.data.repository import DataRepository

@router.get("/ingestas")
def ingests(repository: DataRepository = Depends(get_repository)):
    listing = repository.list_ingests()          # IngestList, already validated
    if listing.availability.status == "unavailable":
        ...  # show the empty state; availability.reason_code / blocked_by say why
```

- **Two sources, one switch.** `PRED_DATA_SOURCE=fixture` serves the contract's examples (scenarios
  `normal`, `vacio`, `errores`); `dal` reads the platform's database. Views do not change.
- **`dal` is honest.** The engine's results do not reach the DAL yet (gaps G1 to G4, G8), so only
  the list of ingests is real. The rest answers `availability.status == "unavailable"` with the gap
  in `blocked_by`, or raises `ArtifactMissing` naming it. When a gap closes, its entry in
  `data/capabilities.py` changes and its read is implemented in `data/dal.py`.
- **Invalid data is rejected.** Every document is validated by the contract's Pydantic models.
  Failures raise explicit errors with the contract's codes (`pred_platform.contract.errors`):
  `ContractInvalid`, `SchemaVersionUnsupported`, `ArtifactMissing`, `EngineUnavailable`; call
  `.to_error_info()` for the normalized `ErrorInfo`. A bad parameter (page, size, sort key) raises
  `InvalidQuery`, which is a bug in the caller.
- **Server-side lists.** Every list is filtered, sorted and paged by the repository
  (`page`/`size`, default 50, maximum 500).

### The contract copy

`src/pred_platform/contract/` holds a byte-for-byte copy of the contract's reference models, JSON
Schemas and examples (the examples are the fixtures). Do not edit it: change the contract in
`pred-docs` first, then

```bash
make sync-contract                 # copies from ../pred-docs and rewrites contract.lock.json
make sync-contract ARGS=--check    # only report differences
```

`contract.lock.json` pins the SHA-256 of every copied file and the tests verify it, so an edited
copy fails the build. The sync also checks that the source is consistent before it writes.

## Checks

```bash
make check       # lockfile + lint + type check + tests: every CI job except Smoke
make lock        # uv.lock matches pyproject.toml (uv lock --check)
make lint        # ruff check + ruff format --check
make typecheck   # pyright
make test        # pytest (coverage must stay at or above 80%)
make format      # apply ruff fixes and formatting
```

CI (`.github/workflows/ci.yml`) runs each check as its own job, so a red job names what broke:
Lockfile, Lint, Format, Types, Tests (coverage >= 80%), and Smoke, which boots the server with
runtime dependencies only and requests `/health` and `/`.

## Pre-commit hooks

```bash
uv run pre-commit install
```

## Static assets

Everything under `src/pred_platform/app/static/` is local; there are no CDN references.
Third-party files (Pico.css, htmx, fonts) are vendored and pinned in
[`vendor.lock.json`](src/pred_platform/app/static/vendor.lock.json) with version, source, license and
SHA-256; the tests fail if a file drifts from its entry. To update one, replace the file and its
entry together. License texts are in `static/licenses/`.

`static/css/pred.css` is the hand-written brand layer on top of Pico.css.

## Dependencies

See [DEPENDENCIES.md](DEPENDENCIES.md) for the pred-engine git dependency and CI auth approach.

## UI/frontend work

Before building or changing any screen, read [docs/DESIGN.md](docs/DESIGN.md) — palette,
typography, spacing, and component conventions, with the requirement each decision satisfies.
