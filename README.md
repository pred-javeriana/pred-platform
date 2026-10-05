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
| `PRED_DB_PATH` | `<PRED_DATA_ROOT>/pred.db` | SQLite file behind the DAL (created and migrated on start with `dal`) |
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

## Database migrations

The DAL schema is versioned with SQLite's `PRAGMA user_version` and changed only through numbered
SQL scripts in `src/pred_platform/dal/migrations/` (decision: `pred-docs/diseno/ADRs/ADR 05-002`).
Version 1 is the original eleven tables. The current schema is what applying every script produces.

- **When it runs.** Starting the app with `PRED_DATA_SOURCE=dal` creates the database file (and its
  folder) if missing and applies whatever is pending; with `fixture` the disk is never touched.
  Reading views never migrate. To migrate without starting the server:

  ```bash
  make migrate                                   # PRED_DB_PATH, or <PRED_DATA_ROOT>/pred.db
  make migrate ARGS=--status                     # only report the version; changes nothing
  make migrate ARGS="--db path/to/other.db"
  ```

- **Each migration is atomic.** It runs in its own transaction together with the `user_version`
  update, so it is applied whole or rolled back; a failure names the script and later ones do not
  run. There are no down migrations: to go back, restore a backup.
- **Backup before migrating.** If the database already has tables, a consistent copy is saved next
  to it as `pred.db.antes-de-v000N.bak` (N = target version) before anything changes; if the copy
  fails, nothing is migrated. A new, empty database is not backed up. `*.bak` is git-ignored.
- **Newer than the code.** A database whose version is higher than the last script (for example, a
  backup from a newer platform) makes the app refuse to start, and the file is left untouched.

### Adding a migration

1. Create `NNNN_description.sql` (next number, four digits, lowercase `snake_case`) with plain
   SQL statements. Do not use `BEGIN`/`COMMIT` or set `user_version`/`foreign_keys`: the runner
   does. To change a table's constraints, rebuild it (create new, copy, drop old, rename); the
   runner switches foreign keys off while applying and runs `foreign_key_check` before committing.
2. Add its SHA-256 to `RELEASED` in `tests/test_dal_migrations_files.py` (the test prints how).
3. Add a test that builds a database at the previous version with sample rows, migrates, and checks
   the data is kept and the new schema is as expected (see `tests/test_dal_migrate.py`).
4. Ask the author of the schema to review (decision D5 of the data contract).

A released migration is never edited: the test pins its hash. A mistake is fixed by a new migration.

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
runtime dependencies only and requests `/health` and `/` (which redirects to `/datos`).

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
