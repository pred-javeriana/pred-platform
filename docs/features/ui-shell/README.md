# ui-shell

Session that landed TASK-UI-1.0-A1, TASK-UI-1.1-B2 and TASK-UI-1.1-B3, and later the step model
of a run.

## What

The home (`/`, Corridas) lists every run where it stands and starts a new one. Each run walks
five steps: Datos, Ajustes, Revisar y lanzar, Ejecución and Resultados, at
`/corridas/nueva/<step>` until it is launched and at `/corridas/<id>/<step>` after. A fixed rail
on the left shows the steps with their state and opens over the page on hover or focus; below
900 px a compact summary replaces it. Each step has a header (which step, what it is, what comes
next) and Atrás and Siguiente at its foot. The old addresses `/datos`, `/ejecucion` and
`/resultados` redirect (307) to their step with the same query. There is no login, no roles and
no administration screen. M0 is out of the flow.

## How

- `app/pasos.py` owns the step model: names, purposes, step states and where Atrás and
  Siguiente go. It reads nothing; `app/corridas.py` reads the repository (`list_runs`,
  `get_run_status`, the published M3 runs) and builds the home and the facts the rail needs.
- The draft of a new run (the chosen file and settings) lives in a browser session cookie
  (`app/borrador.py`). Siguiente in Datos and Ajustes writes it; launching clears it and
  validates everything again with the worker's rule.
- `docs/UI_FLOW.md` maps the routes and lists their `DataRepository` operations and
  capabilities.
- `static/css/pred.css` and `templates/components/` are the reusable design system on
  Pico.css. Status vocabularies are mapped in `app/presentation.py` (contract codes →
  icon + label + CSS modifier). `podado` renders as `podada`, never as failure. Dates and
  times read in Bogotá time (`presentation.format_when`), with the exact UTC instant in `title`.
- Routers live under `app/routers/`; `routers/corridas.py` serves the home and the shared
  locked and not-found pages. `create_app()` stays a factory: settings, DAL migrate,
  repository, static files, Jinja, `/health`.
- Ejecución exposes `/ejecucion/estado` as an HTMX fragment that polls while the run is
  active.

## Where

| Piece | Path |
| --- | --- |
| Flow map | `docs/UI_FLOW.md` |
| Step model | `src/pred_platform/app/pasos.py` |
| Home and run facts | `src/pred_platform/app/corridas.py` |
| Draft of a new run | `src/pred_platform/app/borrador.py` |
| Design tokens | `src/pred_platform/app/static/css/pred.css` |
| Components | `src/pred_platform/app/templates/components/` |
| Presentation map | `src/pred_platform/app/presentation.py` |
| Shell | `src/pred_platform/app/templates/base.html`, `partials/navigation.html`, `partials/wizard_*.html` |
| Routers | `src/pred_platform/app/routers/` |
| Tests | `tests/test_corridas.py`, `tests/test_shell.py` |
| Catalog | `/_dev/componentes` |
