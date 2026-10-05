.PHONY: run test lint format typecheck lock check sync-contract

# Load .env (KEY=VALUE, no quotes) when present so `make run` honours it.
ifneq (,$(wildcard .env))
include .env
export
endif

run:
	uv run uvicorn pred_platform.app.main:app --reload

test:
	uv run pytest

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

# The same checks CI runs, except its smoke job that boots the server (see .github/workflows/ci.yml).
check: lock lint typecheck test
