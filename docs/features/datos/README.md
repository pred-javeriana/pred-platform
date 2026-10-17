# datos

Session that lands TASK-UI-1.2-C1 and TASK-UI-1.2-C3 on the existing `/datos` surface.

## What

`/datos` is no longer a B3 placeholder. The operator can upload a CSV, read the ingest
diagnosis (`accepted` / `rejected` / `failed` / `needs_confirmation`), and — only after
an accepted ingest — inspect demand topology (summary, thresholds, ADI–CV² SVG, SKU
table, filters, pagination) before continuing to `/ejecucion`.

## How

Views call only `DataRepository` (`submit_ingest`, `get_ingest_report`, `list_ingests`,
`get_topology_report`). Fixtures demonstrate the full path. DAL gaps G1/G5 render as
`unavailable`, never as HTTP 500. The SVG is generated in `app/charts.py` from
`TopologyReport.items`; ADI, CV² and class are never recalculated.

## Where

| Piece | Path |
| --- | --- |
| Router | `src/pred_platform/app/routers/data.py` |
| Helpers | `src/pred_platform/app/datos.py` |
| Chart | `src/pred_platform/app/charts.py` |
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

- A missing or crashing engine, or one that takes more than 5 minutes, is an `unavailable` state
  with a retry button, never a 500. Files must be `.csv` and at most 50 MB; only the base name of
  the uploaded file is used.
- Aligning free-form headers with an LLM (`pred-engine ingest`) is out of scope: it needs a
  provider and a key the platform does not store.
- "Continuar a Ejecución" now carries the ingestion (`/ejecucion?ingest_id=<sha256>`).
