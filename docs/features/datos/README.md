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
