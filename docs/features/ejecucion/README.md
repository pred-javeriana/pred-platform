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

## How

- **Reads** go through `DataRepository` (`get_run_status` with the `latest` alias or `?ejecucion=`,
  `list_ingests`); filters and paging of the tasks are delegated to the repository.
- **Writes** go through `app.state.run_control` (`worker/control.py`), a command service separate
  from the read contract (section 19 of the data contract). `DalRunControl` writes the DAL for the
  worker to follow; with fixtures `UnavailableRunControl` makes every action raise with the reason,
  and the screen shows the buttons disabled and explained.
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
  `PRED_WORKER=off` it also says the run waits for `make worker`.

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
