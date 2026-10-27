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
| `PRED_ENGINE_CMD` | `pred-engine` | Command that runs the engine's CLI (split like a shell line) |
| `PRED_WORKER` | `embedded` | `embedded` runs the worker as a thread of the app (with `dal`); `off` disables it |

An unknown `PRED_DATA_SOURCE` or `PRED_FIXTURE_SCENARIO` stops the app at startup with the valid options.

On Windows, `PRED_ENGINE_CMD` preserves backslashes; quote paths containing spaces. For example,
in PowerShell:

```powershell
$env:PRED_ENGINE_CMD = '"C:\ruta con espacios\pred-engine\.venv\Scripts\pred-engine.exe"'
```

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
Version 1 is the original eleven tables; 2 adds the ingest report, 3 the runs and tasks and 4 the
selection per SKU. Versions 5 and 6 add M3 evidence, durable forecast units, frozen protocols and
precalculated portfolio summaries. Version 7 retains M1's declared real/synthetic provenance,
8 records whether that origin was declared and 9 exposes that marker in the published evidence view.
The current schema is version 9. An undeclared origin stays unknown and cannot
produce statistical inference. The file-only website upload does not declare real data.

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

## M3 evaluation evidence

The engine writes M3 results into this same SQLite database through an injected connection.
Its CLI receives the effective `Settings.db_path` through `PRED_DB_PATH`; it never creates a
standalone result database. It applies pending M3 migrations itself on an existing platform
schema. The migration texts are owned by pred-engine and copied exactly into platform versions
5 through 9, so both processes agree on `user_version`.

`pred_platform.dal.evidencia.read_m3_evidence` reads only PUBLICADA runs by M2/M3 run id, optionally filtered by SKU.
One call materializes all matching rows within one read snapshot. An M2 alias can match several
M3 runs; use an exact M3 id and SKU for bounded reads, or group results by their retained `run_id`.
`m3_run_status` exposes durable progress and rejections without partial metrics. The platform
reads precalculated descriptive metrics, verdicts, HLN-DM/BH and portfolio distributions; it does
not evaluate models. See [the M3 data interface](docs/features/evaluacion/README.md).

## Importing ingest reports

M1 ingestion remains a file-based handoff. Every ingestion (`pred-engine classify`/`ingest`/`run`) leaves
`<PRED_DATA_ROOT>/ingestas/<sha256>.json` (schema `pred-engine.ingesta/1`, section 17 of the data
contract in `pred-docs`), also when the ingestion is rejected, failed or waits for confirmation. The
platform imports those files into `ingestas`, `bitacora_calidad` and `series`:

- **When.** Starting the app with `PRED_DATA_SOURCE=dal` (after migrating), or on demand:

  ```bash
  make import                    # migrate if needed, then import what is in ingestas/
  make import ARGS=--status      # compare the files with the database; changes nothing
  ```

- **Idempotent.** The ingestion is keyed by the SHA-256 of its CSV: importing again replaces its
  quality log and series, never duplicates them. When a name conflict is confirmed in the engine
  (`--confirm-overwrite`) the same report is rewritten as `accepted`, and the next import updates it.
  A file kept next to an older one with the same name shows its versioned name in the list.
- **Strict, but per file.** A report with another major version, a class outside the contract, a
  repeated SKU or inconsistent fields is skipped with its reason (logged at startup, listed by
  `make import`); the others are imported and the app still starts.
- **What the views read.** `list_ingests`, `get_ingest_report` and `get_topology_report` come from
  the DAL (`/datos` shows them). Uploading a CSV from the browser (`submit_ingest`) runs
  `pred-engine classify` through `PRED_ENGINE_CMD` (canonical headers, no LLM) and imports the report
  it leaves; see `docs/features/datos/README.md`.

## Runs and the worker

A run executes the engine on an accepted ingestion and leaves its state, tasks, times and errors in
the DAL (`ejecuciones`, `tareas`). The platform **does not import the engine**: the worker launches
`pred-engine run` as a subprocess (`PRED_ENGINE_CMD`) and reads its run folder, the same file-based
coupling as the ingest reports. The engine writes `progreso.jsonl` while it runs (section 18 of the
data contract), so tasks move from `pendiente` to `ejecutando` to `exitosa`/`fallida` live.

```bash
make import                                     # ingest reports -> DAL (needs an accepted ingestion)
make launch ARGS="--ingest <sha256> --min-train 40 --horizon 7 --trials 2 --once"
make worker ARGS=--status                       # runs and how many tasks per state
make worker                                     # follow the queue in its own process
```

- **Where it runs.** With `PRED_DATA_SOURCE=dal` and `PRED_WORKER=embedded` (the default) the app
  starts the worker as a thread, so one `make run` is enough; `PRED_WORKER=off` leaves it to
  `make worker`. Point `PRED_ENGINE_CMD` at the engine, for example
  `uv run --project ../pred-engine pred-engine`. If it cannot be launched the run ends `detenida`
  with a readable error and the app keeps working.
- **One task per engine unit** (SKU × model, decision D6): `modelo` is `familia:algoritmo`, `corte`
  is the reserve cut t\*. A SKU the engine skips becomes one `no_ejecutable` task per configured
  family, with the cause.
- **Run state.** `completada` when every task is `exitosa`; `completada_con_fallos` when some are
  `fallida` or `no_ejecutable`; `detenida` when the engine process died, the operator stopped it or a
  stage left no results (see the table in section 7.4 of the contract).
- **Resume.** The run id derives from the ingestion and the settings (not the number of
  processes), so launching the same run again resumes it with the same task rows. Successful
  tasks retain their state; historical progress is not replayed as live work. Graceful app shutdown
  queues interrupted work, and a crash is recovered on startup. Closing a browser tab leaves the
  server worker running. The accompanying engine checkpoint change also skips successful L2/L3
  units; updating platform alone cannot provide that guarantee. See
  [recovery and verification](docs/features/reanudacion/README.md).
- **Reading it.** `get_run_status` returns the run, its settings, reserve cut, progress over all its
  tasks and a filtered, paged list of tasks; `/ejecucion` will show it (task C4).

### The selection per SKU

When a run closes the worker also imports the `seleccion.jsonl` the engine leaves in its folder
(section 20 of the contract, `pred-engine.seleccion/1`), so the model selection (C2) reads only the
DAL. Per run and SKU it stores `selecciones`; per family (always the four) `seleccion_familias`
with its state, the chosen configuration's evidence and the Walk-Forward summary; the HPO study's
trials in `ensayos` (completed, pruned and failed, each with its reason); the windows in
`ventanas_wf`; and the metrics of every window in `metricas`, linked to the family's task.

- `list_sku_selections` and `get_sku_selection` return `sku_selection_list` and `sku_selection`
  (windows only with `include_windows`). While the run has not finished, or left no file, the list
  answers `unavailable` (`prerequisite_missing`) instead of showing half a result.
- The engine decides which families a SKU gets (policy matrix, configured subset); the platform
  never repeats that policy. A SKU the engine could not run keeps its cause as the exclusion.
- A bad line only loses its SKU and a file of another major version is refused; neither changes how
  the run ended. Importing again replaces each SKU's rows, so a resume leaves no duplicates.
- The champion is not here: it is read from `resultados_comparativos` and is `null` until M3 writes
  it.
- The views read it through `/resultados` and `/resultados/sku/{sku_id}` (task C2, see
  `docs/features/resultados/README.md`). Wherever the repository takes a run id it also accepts
  `latest`, the run with the highest id.

## Checks

```bash
make check       # lockfile + lint + type check + tests: every CI job except Smoke
make lock        # uv.lock matches pyproject.toml (uv lock --check)
make lint        # ruff check + ruff format --check
make typecheck   # pyright
make test        # pytest (coverage must stay at or above 80%)
make integration # the reduced-panel case against the real engine (PRED_ENGINE_CMD)
make format      # apply ruff fixes and formatting
```

CI (`.github/workflows/ci.yml`) runs each check as its own job, so a red job names what broke:
Lockfile, Lint, Format, Types, Tests (coverage >= 80%), and Smoke, which boots the server with
runtime dependencies only and requests `/health` and `/` (which redirects to `/datos`).
CI cannot install the engine yet, so the integration case is skipped there; see
[the M3 data interface](docs/features/evaluacion/README.md#verificación).

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
