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

The built-in `GITHUB_TOKEN` cannot install `pred-engine`: GitHub scopes it to the repository that
runs the workflow, even inside the same org. CI needs its own read-only credential for the engine
repo. The narrowest one is a deploy key:

1. Create an SSH key pair. Add the public key to `pred-engine` as a read-only deploy key.
2. Store the private key in `pred-platform` as the repository secret `PRED_ENGINE_DEPLOY_KEY`.
3. In every job of `.github/workflows/ci.yml` that runs `uv sync` or `uv lock`, add this step
   first. It loads the key and routes the HTTPS dependency URL through SSH (uv fetches git
   dependencies with `git`):

```yaml
- name: Configure pred-engine access
  env:
    PRED_ENGINE_DEPLOY_KEY: ${{ secrets.PRED_ENGINE_DEPLOY_KEY }}
  run: |
    eval "$(ssh-agent -s)"
    echo "SSH_AUTH_SOCK=$SSH_AUTH_SOCK" >> "$GITHUB_ENV"
    ssh-add - <<< "$PRED_ENGINE_DEPLOY_KEY"
    git config --global url."git@github.com:pred-javeriana/pred-engine".insteadOf \
      "https://github.com/pred-javeriana/pred-engine"
```

Pull requests from forks do not receive repository secrets, so this step fails there. A GitHub
App installation token or a fine-grained token from an org-owned account also works, but GitHub
ranks deploy keys first for read access to a single repository.
