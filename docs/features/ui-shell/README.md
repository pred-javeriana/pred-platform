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
  (`app/borrador.py`). Siguiente in Datos and Ajustes writes it. Ajustes also saves each edit as
  it happens, unvalidated (`destino=ajustes` on `POST /corridas/nueva/ajustes`, sent by
  `static/js/wizard_steps.js`), and Atrás and the rail steps of Ajustes are submit buttons with
  their own `destino`, so no exit loses what was typed, even without JavaScript. Ajustes never
  chooses a file for the person, and choosing another file marks Ajustes to do again. A link that
  chooses a runnable file (`/corridas/nueva/ajustes?ingest_id=`), a copied run and an imported
  configuration write it too. Revisar and launching validate everything again with the worker's
  rule; launching clears the draft.
- Every draft write raises a revision and gets its own mark, both stored in the cookie; a cookie
  from before the revision loads as revision 0. Each Ajustes page carries the stamp it saw
  (`draft_rev`, also in the copy and import forms) and `POST /corridas/nueva/ajustes`, the copy and
  the import refuse a stamp that is not the current draft's: nothing is saved, the answer is 409
  (`X-Pred-Draft-Rev` names the current stamp) and the page shows `#conflicto-borrador` with a
  reload link; the script does not retry. The autosave writes its own cookie per revision
  (`pred_borrador_a<rev>`) and never the main one: the current draft is the highest revision, and
  the main cookie wins a tie, so a response that arrives late from a page already left cannot
  replace a later copy, import or edit. Ajustes is sent with `Cache-Control: no-store` so the
  browser's Back button asks for the page again, with the current stamp.
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
