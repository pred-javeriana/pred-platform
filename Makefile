.PHONY: setup datos-demo up down status run test integration motor-local lint format typecheck lock check sync-contract migrate import worker launch

# Load .env (KEY=VALUE, no quotes) when present so `make run` honours it.
ifneq (,$(wildcard .env))
include .env
export
endif

# One-command install and lifecycle (README, «Instalar y arrancar con make»):
#   make setup    check prerequisites, install both checkouts, create .env, migrate, load the sample
#   make up       start PRED in the background and wait until it answers (PORT=8000)
#   make status   whether PRED runs, the database version and the runs
#   make down     stop PRED
# pred-engine sits next to this checkout (MOTOR=path to override); its own `make setup` installs it,
# and `make setup FUNDACIONAL=no` passes through to skip Chronos-2.
MOTOR ?= ../pred-engine
PORT ?= 8000
ENTORNO = uv run python scripts/entorno.py

setup:
	@command -v uv >/dev/null 2>&1 || { echo "Falta uv: instálelo (https://docs.astral.sh/uv/) y repita make setup."; exit 1; }
	uv sync --extra dev --locked
	@$(ENTORNO) revisar --motor $(MOTOR)
	$(MAKE) -C $(MOTOR) setup
	@$(ENTORNO) env --motor $(MOTOR)
	@$(MAKE) --no-print-directory migrate
	@$(MAKE) --no-print-directory datos-demo
	@echo "Instalación lista. Siga con: make up"

# Ingest ejemplos/panel_demo.csv as a browser upload would, so Datos already lists it.
datos-demo:
	@$(ENTORNO) cargar-demo

up:
	@$(ENTORNO) up --port $(PORT)

down:
	@$(ENTORNO) down

status:
	@$(ENTORNO) status
	@$(MAKE) -s --no-print-directory migrate ARGS=--status
	@$(MAKE) -s --no-print-directory worker ARGS=--status

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
