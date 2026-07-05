# Dependencies

## Runtime dependencies

| Package | Purpose |
|---------|---------|
| `fastapi` | HTTP framework and routing |
| `jinja2` | Server-side template rendering |
| `uvicorn` | ASGI server |
| `bcrypt` | Password hashing for session auth |
| `itsdangerous` | Signed session cookies |

## pred-engine (private repo)

`pred-platform` depends on `pred-engine`, the pure-Python forecasting library
in the same GitHub org. This dependency is **not yet wired** in `pyproject.toml`
while the engine is scaffolded; it is documented here so CI can be configured
before it is activated.

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

If external collaborators or forks need access, a personal access token (PAT)
or deploy key should be added as a repository secret and configured in the
workflow via:

```yaml
- name: Configure git credentials
  run: git config --global url."https://x-access-token:${{ secrets.GITHUB_TOKEN }}@github.com/".insteadOf "https://github.com/"
```

Add this step before `uv sync` in `.github/workflows/ci.yml`.
