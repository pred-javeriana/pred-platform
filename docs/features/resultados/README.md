# resultados

Session that lands TASK-UI-1.2-C2 (model selection per SKU) on the `/resultados` surface.

## What

`/resultados` is no longer a B3 placeholder. Its **Por SKU** view shows, for the latest run (or the
one in `?ejecucion=`), a table with one row per SKU: demand class and profile, the state and value of
each of the four families (Clásica, ML, DL, Fundacional) and the champion. `/resultados/sku/{sku_id}`
is the detail: per family its state, the evidence of the chosen configuration, the HPO trials
(completed, **pruned** and **failed**, each with its reason), the Walk-Forward summary and, on
request, every window with its metrics. Excluded families say why (policy matrix, configured subset,
series too short) and failed ones show their error.

The **Resumen** and **Por clase** tabs need the champion and the retrospective verdicts (M3, L4): they
say so instead of inventing anything. The champion column reads "pendiente de M3" until M3 writes
`resultados_comparativos`.

## How

Views call only `DataRepository` (`list_sku_selections`, `get_sku_selection`, `get_run_status` for the
run header, `get_capabilities` for the action). Filters, sort and paging are **delegated** to the
repository and the page shows whatever it returned: nothing is filtered, sorted or computed in the
view or in the browser (no inline script). `include_windows` is only requested with `?ventanas=1`.

- **Which run.** The repository accepts `latest` (`pred_platform.data.query.LATEST`) wherever it takes a
  run id, and the views use it by default; `?ejecucion=3` shows another one. It resolves to the run
  with the highest id.
- **"Ejecutar selección".** Primary action that leads to `/ejecucion` (launching is C4). It is enabled
  only when `parquet_read`, `family_comparison` and `selection_results_persisted` are available
  (contract 7.8); otherwise it is disabled and lists what is missing and why.
- **States.** Families use the task vocabulary plus `excluida` (neutral, not a failure); trials use
  the trial vocabulary, where *podada* has its own icon and colour (decision D1). Every state is icon
  + text + colour.
- **Honest degradation.** No run, a run that has not finished or one without a stored selection render
  as `unavailable` with the reason; an unknown SKU is a 404 page, never a traceback.

## Where

| Piece | Path |
| --- | --- |
| Router | `src/pred_platform/app/routers/results.py` |
| Query, texts and evidence helpers | `src/pred_platform/app/seleccion.py` |
| Presentation (`family` vocabulary) | `src/pred_platform/app/presentation.py` |
| Pages | `src/pred_platform/app/templates/pages/results.html`, `results_sku.html` |
| Partials and macro | `templates/partials/results_*.html`, `templates/components/run_line.html` |
| Tests | `tests/test_resultados.py`, `tests/test_presentation.py` |

## Data

With `PRED_DATA_SOURCE=dal` everything comes from the DAL: the worker imports the engine's
`seleccion.jsonl` when a run closes (G1 to G5, see the root README). With `fixture` the contract's
examples show all four families, a failed family and a lumpy SKU with only the classical family.
