# ui-shell

Session that landed TASK-UI-1.0-A1, TASK-UI-1.1-B2 and TASK-UI-1.1-B3.

## What

Product navigation is three surfaces: Datos (`/datos`), Ejecución (`/ejecucion`) and
Resultados (`/resultados`). `/` redirects to `/datos`. There is no login, no roles and
no administration screen. M0 is out of the flow.

## How

- `docs/UI_FLOW.md` maps former eight screens onto the three surfaces and lists the
  `DataRepository` operations plus G-gaps that still block live data.
- `static/css/pred.css` and `templates/components/` are the reusable design system on
  Pico.css. Status vocabularies are mapped in `app/presentation.py` (contract codes →
  icon + label + CSS modifier). `podado` renders as `podada`, never as failure.
- Routers live under `app/routers/`. `create_app()` stays a factory: settings, DAL
  migrate, repository, static files, Jinja, `/health`.
- Ejecución exposes `/ejecucion/estado` as an HTMX fragment. Polling is not enabled
  (blocked by G3).

## Where

| Piece | Path |
| --- | --- |
| Flow map | `docs/UI_FLOW.md` |
| Design tokens | `src/pred_platform/app/static/css/pred.css` |
| Components | `src/pred_platform/app/templates/components/` |
| Presentation map | `src/pred_platform/app/presentation.py` |
| Shell | `src/pred_platform/app/templates/base.html` |
| Routers | `src/pred_platform/app/routers/` |
| Catalog | `/_dev/componentes` |
