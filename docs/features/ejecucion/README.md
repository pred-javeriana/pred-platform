# ejecucion

Step 4, Ejecución y monitoreo, at `/corridas/{id}/ejecucion`. The old `/ejecucion` URL redirects
with its query preserved. Launch settings and review belong to steps 2 and 3.

## What

From the browser the operator can: launch a run on an accepted ingestion with every parameter of the
engine (families, selection metric, minimum training, horizon, step, seasonality, trials per study,
seed, processes; the defaults are the engine's), follow it live (state, global progress, count per
task state, reserve cut t\* and settings, a filtered and paged task table), **stop** it and
**resume** it, and jump to its results. Each failed or unrunnable task shows a readable title and a
next step (`pred_platform/mensajes.py`, the single catalogue of error codes in Spanish: title, action
and whether it can be retried), then, folded, why it failed and a copyable "Detalle para soporte"
with the code, stage and the engine's text (`pred_platform/fallas.py`, one presenter for every error
on every page). "Why it failed" comes from the error's `facts` (contract 1.3.0, section 11.1): for a
search without complete trials, how many trials failed or were pruned and each cause with its count
and one example in words; for a fit, the model and the reason; for a short series, the observations
it had and needed. Runs written before the engine reported facts are read from the engine's fixed
message format (`pred_platform/hechos_legados.py`). The engine's text is never shown outside the
support detail. The code comes from the engine (G6, G7); runs written by an older engine, which
carry none, fall back to the class name or the cause text (`worker/errores.py`). A stopped run does not show
its closed tasks as progress: its bar counts the successful ones.

**Copy, export and import the configuration** (RF-CON-07, task A3 of section 4.5). Ajustes
shows the families × demand-type matrix, evaluation fields and seed. Its copy form
(`POST /corridas/nueva/copiar`) preserves every stored launch setting, including trials and
parallel processes, in the draft without launching. Revisar shows a checklist and the recorded
stage durations of the two latest completed runs with the same data, never a time estimate.
A duplicate identity disables Lanzar and links to the existing run. A replayed launch also
opens that run without resuming it, including when it stopped or completed with failures.
After launch, Ajustes and Revisar become read-only records with the complete stored settings.

The Configuración tab and the Ajustes/Revisar records have an "Exportar configuración (JSON)" button
(`GET /ejecucion/configuracion.json?ejecucion=<id>`, the latest run when omitted), and the
Ajustes step of a new run has an "Importar un archivo de configuración" disclosure with the import form
(`POST /ejecucion/configuracion`, multipart field `archivo`). The file is JSON with its own format
(`pred-platform.configuracion-ejecucion`, `schema_version` 1) and holds the stored `run_id`, the
`identidad` (`run_id_for`, the id the platform uses to recognise the same run), the SHA-256 of the
ingestion, the nine parameters (`min_train`, `horizon`, `step`, `seasonality`, `metric`, `families`,
`trials`, `seed`, `workers`) and the versions that read it (platform, contract v1 and v2, format of
`parametros`). Nothing local travels (no execution id, no dates). Importing **fills the draft**:
it validates with the same rule as a launch (`parse_launch_form`), never writes to the database
and never launches. The operator reviews and presses "Lanzar la corrida". A file that is
empty, over 64 KB, not JSON, of another format or major version, with unknown or missing fields, a
wrong type or a value the launch rejects is refused (400) with its cause. If the file's ingestion is
not accepted here, its settings are imported but no ingestion is chosen and the notice names the
expected SHA, so settings are not launched over other data by accident. Reading uses
`dal/lectura.get_run` (not `DataRepository`: the public `RunSettings` has no `trials`, `seed` or
`workers`), so with fixtures the export answers 409. Code: `app/configuracion.py`,
`routers/execution.py`.

Reproducibility: asking again for the same ingestion and settings resolves to the same run, because
its identity does not count `workers`; a changed seed or horizon is another run. The file pins the
settings. The same *results* also need the same engine code: the platform does not import the
engine nor record its version, and the engine already refuses a run stored with another code
fingerprint (`run.incompatible_config`).

## How

- **Reads** go through `DataRepository` (`get_run_status` with the `latest` alias or `?ejecucion=`,
  `list_ingests`); filters and paging of the tasks are delegated to the repository.
- **Writes** go through `app.state.run_control` (`worker/control.py`), a command service separate
  from the read contract (section 19 of the data contract). `DalRunControl` writes the DAL for the
  worker to follow; with fixtures `UnavailableRunControl` makes every action raise with the reason,
  and the screen shows the buttons disabled and explained. A database the app cannot write
  (`worker/solo_lectura.py`: the file or its `-wal`/`-shm` has no write permission, or a
  rollback-journal database sits in a folder without it) gets the same treatment from `DalRunControl`, checked on every request, so
  a permission change applies without a restart; a write the database still refuses as read-only
  answers with the same reason, never a server error.
- **Stop** needs no migration: `dal.request_stop` turns a `pendiente` or `ejecutando` run into
  `detenida` with the error `platform.run_stopped`, only if it is still in progress. The worker,
  which looks at the run's state every 0.2 s, sees it, terminates the engine's process group and
  closes the open tasks with that error without settling the run as if it had finished.
- **Resume** (`dal.resume_run`) queues a `detenida` or `completada_con_fallos` run again with its
  own parameters and engine `run_id` and puts only unsuccessful tasks back to `pendiente`.
  Successful tasks keep their state and evidence. With the accompanying engine checkpoint change,
  successful L2/L3 units are restored without selection, fitting or evaluation being called again.
  New attempts follow only new progress events; importing a final trace records its original
  timestamps without presenting it as live work.
  Launching a run identical to an existing one returns that one.
  The page does not offer Reanudar when a resume would fail the same way: an incompatible
  identity, families that give no class two to compare, or an L2 stage that failed because the
  engine's policy gave no decisions or no unit was left to run.
- **Live view.** The body (`partials/execution_body.html`) polls `/ejecucion/estado` every 2 s while
  the run is in progress; the server answers 286 when it is not, so htmx stops. The ring, current
  stage, product count and clock come first. `static/js/live.js` animates those values without
  requests; reduced motion disables the transitions. Pausing, focused controls and selected text
  prevent replacement. The task filter form is `hx-preserve`d so typing is not lost.
- **Stages and causes.** `data/run_status.py` adds local display fields without modifying the
  packaged contract. Durations use recorded stage timestamps from the current attempt, never
  an estimate. The parsed progress summary is reused while the log is unchanged. One task
  page-load query reads causes for the whole run, independent of filters and paging. Its
  timestamped snapshot stays across swaps; “Actualizar causas” reloads it explicitly. Polling
  uses the original state-count query and never aggregates causes. Initial page load also reads
  cached stage references for up to two comparable
  completed seeds (same data and evaluation settings) from earlier runs, never the run itself or
  a later one, matching the “Antes” label. The browser retains those references
  across polling swaps; polling never reads history. Missing history leaves only the current
  run's bars. The full task list and configuration remain in a disclosure below the causes.
- **Returning to a run.** The browser stores its last visible snapshot locally. After at least a
  minute away, “Desde su última visita” reports observed stage, task and product-count changes.
  No previous snapshot means no comparison. Storage failure does not stop progress updates.
- **One worker, one run at a time.** A second launch waits in the queue and the screen says so; with
  `PRED_WORKER=off` and no other worker holding the database lock (`WorkerLock.held_elsewhere`) it
  also says the run waits for `make worker`. If the app's own worker thread has ended, for example
  because it could not create its lock file in a read-only folder, the screen says that worker
  stopped and the app log says why.
- **Earlier durations** read `stage_started`/`stage_finished` pairs from `progreso.jsonl` in
  `app/historial.py`. A missing or incomplete trace shows no invented duration. A new attempt
  resets the pairs. Copying and duplicate detection use read-only DAL reads, because the public
  `RunSettings` does not expose every launch field.

## Where

| Piece | Path |
| --- | --- |
| Router | `src/pred_platform/app/routers/execution.py` |
| Form, query, texts | `src/pred_platform/app/ejecucion.py` |
| Commands | `src/pred_platform/worker/control.py`, `dal/ejecuciones.py` (`request_stop`, `resume_run`) |
| Page and partials | `templates/pages/execution.html`, `templates/partials/execution_{launch,body}.html` |
| CSV upload | `src/pred_platform/worker/ingesta.py`, `DalRepository.submit_ingest` |
| Progress presentation | `app/execution_display.py`, `app/static/js/live.js`, `app/static/css/live.css` |
| Tests | `tests/test_wizard_steps.py`, `tests/test_ejecucion.py`, `tests/test_execution_live.py`, `tests/test_control.py`, `tests/test_carga.py` |

## Known limits

- The packaged `RunStatus` has no run-level error field. The screen reads
  `ejecuciones.error_json` through `get_run_error`; it shows the cause and whether resuming is
  possible. Missing stage timestamps show “Sin registro de duración”, not an inferred duration.
- Skipping completed units requires the accompanying `pred-engine` checkpoint implementation and
  preserving the run directory. Earlier engine versions reuse HPO studies but repeat L2/L3 work.
  See [recovery](../reanudacion/README.md) for deployment, compatibility and verification.
