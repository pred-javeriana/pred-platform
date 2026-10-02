# Dependencies

## Runtime dependencies

| Package | Purpose |
|---------|---------|
| `fastapi` | HTTP framework and routing |
| `jinja2` | Server-side template rendering |
| `uvicorn` | ASGI server |
| `bcrypt` | Password hashing for session auth |
| `itsdangerous` | Signed session cookies |

## Development dependencies

Installed with `uv sync --extra dev --locked`.

| Package | Purpose |
|---------|---------|
| `pytest`, `pytest-cov` | Tests and the 80% coverage gate |
| `httpx` | Client used by FastAPI's `TestClient` |
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

## pred-engine (private repo)

`pred-platform` depends on `pred-engine`, the pure-Python forecasting library
in the same GitHub org. This dependency is **not yet wired** in `pyproject.toml`.
Wiring it is deferred to the worker task (the first code that imports the engine);
it is documented here so CI can be configured before it is activated. When wiring,
pin it to a commit (or tag) so installs stay reproducible.

The engine pulls in heavy scientific dependencies (LightGBM, statsmodels, Optuna), so a
fresh install gets noticeably larger once it is activated. On macOS, LightGBM needs the OpenMP
runtime: `brew install libomp`.

### uv git dependency line

```toml
# Add to [project.dependencies] in pyproject.toml:
"pred-engine @ git+https://github.com/pred-javeriana/pred-engine.git"
```

### CI auth approach

GitHub Actions authenticates via `GITHUB_TOKEN` using the built-in token for
same-org private repos. No extra secrets are needed when both repos belong to
`pred-javeriana`. The workflow step that runs `uv sync` will resolve the git
dependency automatically once it is uncommented.

> To confirm when wiring: the built-in `GITHUB_TOKEN` is normally scoped to the repository
> running the workflow, so reading a second private repo may need a PAT or deploy key anyway.

If external collaborators or forks need access, a personal access token (PAT)
or deploy key should be added as a repository secret and configured in the
workflow via:

```yaml
- name: Configure git credentials
  run: git config --global url."https://x-access-token:${{ secrets.GITHUB_TOKEN }}@github.com/".insteadOf "https://github.com/"
```

Add this step before `uv sync` in `.github/workflows/ci.yml`.
