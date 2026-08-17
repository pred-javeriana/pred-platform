# pred-platform

PRED product platform: server-rendered FastAPI + Jinja2 UI, session auth (bcrypt),
single DAL over SQLite, and task-table run orchestration.

## Requirements

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)

## Setup

```bash
uv sync --extra dev
```

## Run

```bash
uv run uvicorn pred_platform.app.main:app --reload
```

Open <http://localhost:8000> in your browser.

## Test

```bash
uv run pytest
```

## Lint

```bash
uv run ruff check .
uv run ruff format --check .
```

## Pre-commit hooks

```bash
uv run pre-commit install
```

## Dependencies

See [DEPENDENCIES.md](DEPENDENCIES.md) for the pred-engine git dependency and CI auth approach.

## UI/frontend work

Before building or changing any screen, read [docs/DESIGN.md](docs/DESIGN.md) — palette,
typography, spacing, and component conventions, with the requirement each decision satisfies.
