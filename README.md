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
