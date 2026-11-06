# ejecucion

Session that lands TASK-UI-1.2-C4 (Ejecución y monitoreo) on the `/ejecucion` surface and the real
CSV upload of `/datos`.

## What

From the browser the operator can: launch a run on an accepted ingestion with every parameter of the
engine (families, selection metric, minimum training, horizon, step, seasonality, trials per study,
seed, processes; the defaults are the engine's), follow it live (state, global progress, count per
task state, reserve cut t\* and settings, a filtered and paged task table), **stop** it and
**resume** it, and jump to its results. Each failed or unrunnable task shows a readable title, the
engine's detail and a next step (`pred_platform/mensajes.py`, the single catalogue of error codes in
Spanish: title, action and whether it can be retried). The code comes from the engine (G6, G7); runs
written by an older engine, which carry none, fall back to the class name or the cause text
(`worker/errores.py`). A stopped run does not show
its closed tasks as progress: its bar counts the successful ones.

**Export and import the configuration** (RF-CON-07, task A3 of section 4.5). The Configuración tab
of the shown run has an "Exportar configuración (JSON)" button
(`GET /ejecucion/configuracion.json?ejecucion=<id>`, the latest run when omitted), and the
"Nueva ejecución" panel has a "Reproducir una ejecución anterior" disclosure with the import form
(`POST /ejecucion/configuracion`, multipart field `archivo`). The file is JSON with its own format
(`pred-platform.configuracion-ejecucion`, `schema_version` 1) and holds the stored `run_id`, the
`identidad` (`run_id_for`, the id the platform uses to recognise the same run), the SHA-256 of the
ingestion, the nine parameters (`min_train`, `horizon`, `step`, `seasonality`, `metric`, `families`,
`trials`, `seed`, `workers`) and the versions that read it (platform, contract v1 and v2, format of
`parametros`). Nothing local travels (no execution id, no dates). Importing only **fills the launch
form**: it validates with the same rule as a launch (`parse_launch_form`), never writes to the
database and never launches; the operator reviews and presses "Lanzar ejecución". A file that is
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
- **Live view.** The body (`partials/execution_body.html`) polls `/ejecucion/estado` every 2 s while
  the run is in progress; the server answers 286 when it is not, so htmx stops. The task filter form
  is `hx-preserve`d so typing is not lost. The launch form folds away once there is a run to show.
- **One worker, one run at a time.** A second launch waits in the queue and the screen says so; with
  `PRED_WORKER=off` and no other worker holding the database lock (`WorkerLock.held_elsewhere`) it
  also says the run waits for `make worker`.

## Where

| Piece | Path |
| --- | --- |
| Router | `src/pred_platform/app/routers/execution.py` |
| Form, query, texts | `src/pred_platform/app/ejecucion.py` |
| Commands | `src/pred_platform/worker/control.py`, `dal/ejecuciones.py` (`request_stop`, `resume_run`) |
| Page and partials | `templates/pages/execution.html`, `templates/partials/execution_{launch,body}.html` |
| CSV upload | `src/pred_platform/worker/ingesta.py`, `DalRepository.submit_ingest` |
| Tests | `tests/test_ejecucion.py`, `tests/test_control.py`, `tests/test_carga.py` |

## Known limits

- The run-level error (for example the engine could not be launched) is stored in
  `ejecuciones.error_json` but the `run_status` read model has no field for it (contract 1.0.0);
  the screen shows the errors of the tasks and, for a run without tasks, points to `motor.log`.
  An optional `ejecucion.error` would be an additive contract change.
- Skipping completed units requires the accompanying `pred-engine` checkpoint implementation and
  preserving the run directory. Earlier engine versions reuse HPO studies but repeat L2/L3 work.
  See [recovery](../reanudacion/README.md) for deployment, compatibility and verification.
