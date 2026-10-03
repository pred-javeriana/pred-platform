.PHONY: run test lint format typecheck lock check

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

# The same checks CI runs, except its smoke job that boots the server (see .github/workflows/ci.yml).
check: lock lint typecheck test
