# datos

Step 1 of a new run at `/corridas/nueva/datos`; `/datos` redirects with the same query.

## What

The operator chooses a validated file or uploads a CSV. Four file figures and an ADI × CV²
map beside the class/family table summarize an accepted file. Diagnosis, the filtered product
table and the complete ingest history stay in disclosures. The four diagnosis states
(`accepted`, `rejected`, `failed`, `needs_confirmation`) keep their actions. Continuing chooses
the file and opens Ajustes. `/corridas/<id>/datos` shows a launched run's file without upload
or file-selection controls.

## How

Views call only `DataRepository` (`submit_ingest`, `get_ingest_report`, `list_ingests`,
`get_topology_report`). Fixtures demonstrate the full path. DAL gaps G1/G5 render as
`unavailable`, never as HTTP 500. The summary SVG in `app/charts_datos.py` reads every page
of topology, uses logarithmic axes and names the nearest product on pointer or keyboard input.
CV² zero is shown at the lower border and declared in the caption. The filtered detail chart
stays in `app/charts.py`. Neither chart recalculates ADI, CV² or class.

## Where

| Piece | Path |
| --- | --- |
| Router | `src/pred_platform/app/routers/data.py` |
| Helpers | `src/pred_platform/app/datos.py` |
| Charts | `src/pred_platform/app/charts_datos.py`, `src/pred_platform/app/charts.py` |
| Map interaction | `src/pred_platform/app/static/js/datos.js` |
| Presentation | `src/pred_platform/app/presentation.py` |
| Page | `src/pred_platform/app/templates/pages/data.html` |
| Partials | `src/pred_platform/app/templates/partials/data_*.html` |

## Data with `PRED_DATA_SOURCE=dal` (G1)

With the real database the surface reads the ingest list, each ingest report and the demand
topology from the DAL, imported from the engine's `ingestas/<sha256>.json` (see the root README,
"Importing ingest reports").

## Uploading a CSV from the browser

With `dal` the upload is real. `DalRepository.submit_ingest` hands the file to
`worker/ingesta.py`, which saves it in a temporary folder under `<PRED_DATA_ROOT>/staging/` and runs
`pred-engine classify --csv <file>` (the command is `PRED_ENGINE_CMD`; the engine is never
imported). `classify` is the engine path for a CSV with the four canonical headers
(`sku_id, timestamp, demand_qty, lead_time_days`) and needs no LLM: it deposits the file in `raw/`,
publishes the Parquet and always leaves `ingestas/<sha256>.json`, which is imported like any other
report. So the page shows the usual diagnosis: accepted (topology below), rejected or failed with
the engine's reason (for example, missing canonical columns), or `needs_confirmation` when `raw/`
already has a file with that name and other content (G5): confirming repeats the call with
`--confirm-overwrite` and the engine keeps both files.

- On a database the app cannot write (`worker/solo_lectura.py`), "Cargar y validar" is disabled with
  the reason and a posted file is refused before the engine runs, so nothing lands in the data root.
  If the database becomes read-only while the engine runs, the page gives that reason too. The
  engine's report stays in `ingestas/` and the app imports it at its next start with a writable
  database.
- A missing or crashing engine, or one that takes more than 15 minutes, is an `unavailable` state
  with a retry button, never a 500. Files must be `.csv` and at most 256 MiB; only the base name of
  the uploaded file is used.
- Aligning free-form headers with an LLM (`pred-engine ingest`) is out of scope: it needs a
  provider and a key the platform does not store.
- "Continuar a Ajustes", at the foot of the step, chooses the ingestion for the new run
  (`POST /corridas/nueva/datos`) and opens Ajustes.
