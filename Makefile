.PHONY: run test integration motor-local lint format typecheck lock check sync-contract migrate import worker launch

# Load .env (KEY=VALUE, no quotes) when present so `make run` honours it.
ifneq (,$(wildcard .env))
include .env
export
endif

run:
	uv run uvicorn pred_platform.app.main:app --reload

test:
	uv run pytest

# The reduced-panel case against the real engine (PRED_ENGINE_CMD, for example
# "uv run --project ../pred-engine pred-engine"); it is skipped when the engine is not installed.
integration:
	uv run pytest -m integration --no-cov -ra $(ARGS)

# Local engine check before asking for review: runs the case above with ../pred-engine (override
# with MOTOR=path) and writes the receipt that CI reads; see docs/features/motor-local/README.md.
# Example: make motor-local ARGS=--publicar
MOTOR ?= ../pred-engine
motor-local:
	uv run --project $(MOTOR) python $(MOTOR)/.github/scripts/motor_local.py --plataforma . $(ARGS)

lint:
	uv run ruff check .
	uv run ruff format --check .

format:
	uv run ruff check --fix .
	uv run ruff format .

typecheck:
	uv run pyright

lock:
	uv lock --check

# Copy the data contract from ../pred-docs (override with CONTRACT_SRC=path). Not part of `check`:
# it crosses repositories. Use `make sync-contract ARGS=--check` to only report differences.
CONTRACT_SRC ?= ../pred-docs/diseno/contratos
sync-contract:
	uv run python scripts/sync_contract.py --source $(CONTRACT_SRC) $(ARGS)

# Bring the database to the latest schema (the app also does this on start with PRED_DATA_SOURCE=dal).
# `make migrate ARGS=--status` only reports; `ARGS="--db path/to/file.db"` targets another file.
migrate:
	uv run python -m pred_platform.dal.migrate $(ARGS)

# Import the engine's ingest reports (<PRED_DATA_ROOT>/ingestas/*.json) into the database; the app
# also does this on start with PRED_DATA_SOURCE=dal. `make import ARGS=--status` only compares.
import:
	uv run python -m pred_platform.dal.importacion $(ARGS)

# Follow the run queue in its own process (the app already does it with PRED_WORKER=embedded).
worker:
	uv run python -m pred_platform.worker $(ARGS)

# Queue a run on the engine: make launch ARGS="--ingest <sha256> --min-train 40 --trials 1 --once"
launch:
	uv run python -m pred_platform.worker --launch $(ARGS)

# The same checks CI runs, except its smoke job that boots the server (see .github/workflows/ci.yml).
check: lock lint typecheck test
