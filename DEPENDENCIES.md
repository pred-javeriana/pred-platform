# Dependencies

## Runtime dependencies

| Package | Purpose |
|---------|---------|
| `fastapi` | HTTP framework and routing |
| `python-multipart` | Parses the form posts and the CSV upload FastAPI receives |
| `jinja2` | Server-side template rendering |
| `uvicorn` | ASGI server |
| `bcrypt` | Password hashing for session auth |
| `itsdangerous` | Signed session cookies |
| `psutil` | Waits for the engine's process pool to close before a resume on Windows |
| `pydantic` | Models of the data contract (declared directly; `>=2.13`, the version the contract's JSON Schemas were exported with) |

## Development dependencies

Installed with `uv sync --extra dev --locked`.

| Package | Purpose |
|---------|---------|
| `pytest`, `pytest-cov` | Tests and the 80% coverage gate |
| `httpx` | Client used by FastAPI's `TestClient` |
| `jsonschema` | Validates the contract's examples against its published JSON Schemas (tests and `scripts/sync_contract.py`) |
| `ruff` | Lint and formatting |
| `pyright` | Type checking |
| `pre-commit` | Local git hooks |

## Vendored static assets

Served from `src/pred_platform/app/static/`; there is no CDN and no Node toolchain. Versions, sources,
licenses and SHA-256 are pinned in `static/vendor.lock.json`, and the tests verify them.

| Asset | Version | License |
|-------|---------|---------|
| Pico.css (`css/pico.min.css`) | 2.1.1 | MIT |
| htmx (`js/htmx.min.js`) | 2.0.11 | 0BSD |
| Archivo (variable, regular and italic) | — | OFL-1.1 |
| JetBrains Mono (variable) | — | OFL-1.1 |

The font files come from the design reference in `pred-docs/diseno/fonts/`.

## pred-engine (public repo)

The platform runs the `pred-engine` CLI as a subprocess (`PRED_ENGINE_CMD`) and does not import it; the git dependency stays commented until a module needs the library.

The engine pulls in heavy scientific dependencies (LightGBM, statsmodels, Optuna), so a
fresh install gets noticeably larger once it is activated. On macOS, LightGBM needs the OpenMP
runtime: `brew install libomp`.

### uv git dependency line

```toml
# Add to [project.dependencies] in pyproject.toml:
"pred-engine @ git+https://github.com/pred-javeriana/pred-engine.git"
```

### CI

`pred-engine` is public, so installing it over HTTPS needs no credential; CI does not install it because the engine runs locally.
