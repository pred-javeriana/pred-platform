# resultados

Session that lands TASK-UI-1.2-C2 (model selection per SKU) on the `/resultados` surface.

## What

`/resultados` is no longer a B3 placeholder. Its **Por SKU** view shows, for the latest run (or the
one in `?ejecucion=`), a table with one row per SKU: demand class and profile, the state and value of
each of the four families (Clásica, ML, DL, Fundacional) and the champion with its verdict, as M3
published them. `/resultados/sku/{sku_id}`
is the detail: per family its state, the evidence of the chosen configuration, the HPO trials
(completed, **pruned** and **failed**, each with its reason), the Walk-Forward summary and, on
request, every window with its metrics. Excluded families say why (policy matrix, configured subset,
series too short) and failed ones show their error.

The champion, the verdict and its evidence come from one exact M3 run of the M2 run shown (contract
v2): the one in `?m3=` or, without it, the newest PUBLICADA one that `list_m3_runs` resolves. Detail
links, filters and pages keep that resolved run. When several PUBLICADA runs share an identity the
side panel shows the contract's notice and links to the others. The five verdicts are shown
verbatim; the detail adds the justification, windows against `n_min`, r against Seasonal Naive,
coverage, the data origin and, apart from the verdict, the HLN-DM/BH significance or its cause. A
SKU launched after the cut says so; a run that is not PUBLICADA, or a publication without its
exclusion metadata, explains why there is no verdict. The **Resumen** and **Por clase** tabs are
not built yet (Module 4, sections 4.2 to 4.5) and say so.

## How

Views call only `DataRepository` (`list_sku_selections`, `get_sku_selection`, `get_run_status` for the
run header, `list_m3_runs`, `get_m3_run_status` and `get_m3_evidence` for M3's decision,
`get_capabilities` for the action). Filters, sort and paging are **delegated** to the
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
| M3 run resolution and decision texts | `src/pred_platform/app/resultados_m3.py` |
| Presentation (`family` and `verdict` vocabularies) | `src/pred_platform/app/presentation.py` |
| Pages | `src/pred_platform/app/templates/pages/results.html`, `results_sku.html` |
| Partials and macro | `templates/partials/results_*.html`, `templates/components/run_line.html` |
| Tests | `tests/test_resultados.py`, `tests/test_presentation.py` |

## Data

With `PRED_DATA_SOURCE=dal` everything comes from the DAL: the worker imports the engine's
`seleccion.jsonl` when a run closes (G1 to G5, see the root README). M3's evidence is read from the same database once the engine publishes it. With
`fixture` the contract's examples show all four families, a failed family and a lumpy SKU with only
the classical family; the v2 examples are separate illustrative runs (`m3-real-02`, M2 run
`m2-ejemplo`), so the v1 run `1` shows that it has no M3 evaluation.
