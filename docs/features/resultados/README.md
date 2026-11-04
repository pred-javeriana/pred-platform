# resultados

Session that lands TASK-UI-1.2-C2 (model selection per SKU) on the `/resultados` surface.

## What

`/resultados` has four tabs, overview first: **Resumen**, **Por clase**, **SKU** and **Perfiles de
error**. Without `?ejecucion=` it shows the newest execution with a PUBLICADA M3 run, because the
newest execution may still be running; a picker in the context bar lists the executions with
published evidence. Without `?vista=` it opens on Resumen when the execution shown has published M3
evidence and on the SKU tab otherwise. The SKU tab has two readings of the same products, chosen with
a segmented control: **Veredictos de M3** (`?vista=sku`, what M3 decided per SKU; the old
`?vista=consulta` link still opens it) and **Familias de M2** (`?vista=sku&lista=familias`, one row
per SKU with its demand class, the state and value of each of the four families, one column per
family, and the champion with its verdict, as M3 published them). A link with the M2 filters
(`family`, `estado`) opens the M2 reading. `/resultados/sku/{sku_id}` is the detail, under a
breadcrumb that goes back to Resultados and to the list the reader came from: per family its state, the evidence of the chosen configuration, the HPO trials
(completed, **pruned** and **failed**, each with its reason), the Walk-Forward summary and, on
request, every window with its metrics. Excluded families say why (policy matrix, configured subset,
series too short) and failed ones show their error.

The champion, the verdict and its evidence come from one exact M3 run of the M2 run shown (contract
v2): the one in `?m3=` or, without it, the newest PUBLICADA one that `list_m3_runs` resolves. Detail
links, filters and pages keep that resolved run. A context bar above the tabs names the execution,
its ingest, the M3 run and the data origin. When several PUBLICADA runs share an identity the bar
shows the contract's notice and links to the others. The five verdicts are shown
verbatim; the detail adds the justification, windows against `n_min`, r against Seasonal Naive,
coverage, the data origin and, apart from the verdict, the HLN-DM/BH significance or its cause. A
SKU launched after the cut says so; a run that is not PUBLICADA, or a publication without its
exclusion metadata, explains why there is no verdict.

The other tabs read the same resolved M3 run. **Resumen** shows the funnel M3 published for the
whole run and counts the published HLN-DM results (with test, significant after BH within each
demand class, without test by cause); it never recomputes the test. **Por clase** shows the champion
family M3 froze for each class on the selection block, its reason, every family's median r and the
class verdict counts. Below it, **Confirmación del campeón** shows, per class, the champion's state
on the test block with its IUT and Holm p, its median r against Seasonal Naive with the bootstrap
interval, and one row per competitor (SKU count, median ratio with interval, Hodges-Lehmann, wins
with Wilson interval, Wilcoxon p and IUT p). **Significancia frente a Seasonal Naive** lists the
HLN-DM counts per class after BH and after BY, with the FCR level. A run evaluated before the
reserve blocks has no confirmation and says so; a class without a row says so in its own line. **Perfiles de error** shows the error profiles of the portfolio (`m3_cartera`, test
block) for one metric at a time. The SKU detail also draws the champion's published h=1 forecast
against the observed demand and, on request (`?reserva=1`), lists the reserve windows of the
champion and Seasonal Naive.

**Champion distribution** (RF-CON-08, task A4). **Resumen** also answers which family dominates the
portfolio: after the funnel it shows "Distribución de campeones por familia" and "Campeones por clase
de demanda". It counts the SKU with a verdict by the champion family M3 published for each SKU
(`get_m3_champions`, the `m3_campeones` view; `seasonal_naive` when the baseline wins) and shows it as a
horizontal bar chart (`champion_share_svg` in `app/charts.py`, server-side SVG, one series, value at the
tip in ink, no script) with the equivalent table beside it: evaluated, not evaluated, SKU and the share.
The share is a presentation of two published figures, the family's count over the funnel's
`tras_exclusiones` (SKU with a verdict); the platform does not add or recompute counts. Products
excluded before M3 are in the funnel and not here. The portfolio rows are checked against that
denominator and, if they do not add up, the page says so with both numbers instead of adjusting one.
The family of a SKU can differ from its class champion (shown in "Por clase") by the fallback M3
documents in the justification. If the read of `m3_campeones` fails or comes incomplete, the funnel and
the test counts stay and the cause is shown. The M2 reading of the SKU tab links back to the summary
("Ver distribución de campeones") when a PUBLICADA M3 run is resolved.

**Veredictos de M3** (`?vista=sku`, RF-CON-05 of section 4.5; called Consulta before) is the query
over M3's published verdicts. Unlike the M2 reading, whose rows are M2's selection per family, its
list comes from `get_m3_evidence` of
the resolved M3 run, so it can filter by the three things M3 decides: demand class (Syntetos-Boylan),
champion family and verdict (the five exact labels). They combine, with a SKU search, and the list
sorts by SKU, class, verdict, r, windows or BH-adjusted p. Each row shows the champion family and
model, the verdict, r, windows with `n_min` under them and, apart from the verdict, the significance
(whether it is significant, in which direction, and the BH-adjusted p) or the cause M3 published when
there is no test; the full test opens on demand. The count is the filtered total, while the funnel line (SKU with a verdict out of all the
products) is the whole run's and does not change with the filters. The M2 family state is not a
verdict filter (contract v2, section 7), so those filters stay in the M2 reading. An unknown filter value is
rejected by the repository and shown as a notice; it is never ignored. Query code is
`app/consulta.py` (`ConsultaQuery`, whose `query_string` the CSV export will reuse), the view is
`consulta()` in `app/resultados_m3.py` and the template is `partials/results_consulta.html`.

**CSV export** (RF-CON-06, tarea A2) hangs from the verdict list. `GET /resultados/exportar.csv` takes
the same query string as the list (the "Exportar CSV" link already carries the filters and the resolved
`m3=` run; `page` and `size` are ignored) and returns every SKU of the query, not one page, in the
same order as the view. `GET /resultados/exclusiones.csv` lists the products without a verdict for the
whole run (launched after the cut, with `t_star`, and excluded by M2 with their class, code and cause);
verdict filters do not reach it. Both are built by `app/exportar.py` from what the repository returned:
nothing is recomputed or relabelled, values are written as they are (so identifiers match the internal
records) and a null is an empty cell, with `dm_causa` as the cause when there is no HLN-DM test.

- **Format.** RFC 4180 CSV, UTF-8 without BOM, comma separator, dot decimal, `true`/`false`. Excel in
  Spanish locales may need *Datos > Desde texto/CSV* to read the comma and the dot. The file name is
  `pred-resultados-<m3 run_id>.csv` (`pred-exclusiones-…`).
- **Columns of the verdicts file** (`VERDICT_COLUMNS`, fixed): `run_id` (M3), `run_id_m2`,
  `ingesta_sha256`, `sku`, `sku_class`, `veredicto`, `justificacion`, `familia_campeona`,
  `candidato_campeon`, `n_ventanas`, `n_min`, `cobertura`, `razon_sn`, `pierde_frente_a_linea_base`,
  `comparacion_incompleta`, `iqr_diferencia_mae`, the seven `dm_*` columns apart from the verdict, and
  `datos_sinteticos` / `origen_declarado`. The per-candidate list (`modelos_evaluados`) and the run's
  `identidad` are not exported; the identity is found with `run_id`.
- **One consistent read.** The export reads the pages (500 SKU each) of `get_m3_evidence`; each page
  is its own read, so it checks that the total, the funnel and the PUBLICADA state do not change
  between pages and, if they do, answers 409 instead of a file that mixes two states.
- **Errors, never a partial file.** An invalid filter or page is 400; an execution or M3 run that does
  not exist (or has no M3 evaluation) is 404; a run that is not PUBLICADA, a publication without its
  exclusion metadata or a read that changed is 409. The body is the reason in plain text.
- **Speed.** On a local SQLite the 1,000-SKU export took about 0.03 s and the 3,499-SKU one about 0.1
  to 0.2 s (in-process, three runs each; the formal measurement is task B1).

## How

Views call only `DataRepository` (`list_sku_selections`, `get_sku_selection`, `get_run_status` for the
run header, `list_m3_runs`, `get_m3_run_status` and `get_m3_evidence` for M3's decision,
`get_m3_categories` and `get_m3_confirmation` for Por clase,
`get_capabilities` for the action). Filters, sort and paging are **delegated** to the
repository and the page shows whatever it returned. The views only order published rows to show
them (classes, competitors, evaluated models) and format published numbers, for example a share or a
win percentage from published counts; they never filter rows or recompute a statistic, and the
browser runs no inline script. `include_windows` is only requested with `?ventanas=1`.

- **Which run.** The repository accepts `latest` (`pred_platform.data.query.LATEST`) wherever it takes a
  run id; it resolves to the run with the highest id. Resultados uses it only when no execution has
  a PUBLICADA M3 run; otherwise it opens the newest one that has. `?ejecucion=3` shows another one,
  and an execution that does not exist says so instead of showing an empty panel.
- **"Nueva ejecución".** Secondary action in the page header that opens the launch form of
  `/ejecucion` with the ingest of the execution shown (launching is C4). It is enabled only when
  `parquet_read`, `family_comparison` and `selection_results_persisted` are available
  (contract 7.8); otherwise it is disabled and "Por qué no se puede lanzar" lists what is missing.
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
| Partials | `templates/partials/results_*.html` (`results_context.html` is the context bar) |
| Champion forecast series and chart | `src/pred_platform/app/resultados_pronostico.py`, `src/pred_platform/app/charts.py` |
| Tests | `tests/test_resultados.py`, `tests/test_resultados_pronostico.py`, `tests/test_presentation.py` |

## Data

With `PRED_DATA_SOURCE=dal` everything comes from the DAL: the worker imports the engine's
`seleccion.jsonl` when a run closes (G1 to G5, see the root README). M3's evidence is read from the same database once the engine publishes it. With
`fixture` the contract's examples show all four families, a failed family and a lumpy SKU with only
the classical family; the v2 examples are separate illustrative runs (`m3-real-02`, M2 run
`m2-ejemplo`), so the v1 run `1` shows that it has no M3 evaluation.

The read interface also exposes `get_m3_observations` (reserve demand by SKU and date) and
`get_m3_champions` (engine-owned counts by class and family, with whole-portfolio rows).
The cut and late launches come from `m3_cortes` and `m3_lanzamientos_tardios` in SQLite, even
when the ingestion's Parquet is absent. A published run without `m3_cortes` returns
`metadata_missing` for evidence and observations and asks for a new execution.

## Champion forecast chart

The SKU detail draws the observed demand and the h=1 forecast of the champion candidate M3
published, before the model comparison. It reads every page of the exact run, SKU and candidate
through `DataRepository`; it does not forecast, choose models or compute metrics. Nulls break the
lines, zeros are kept, and rows are never interpolated or averaged. Each forecast keeps its target
date, origin and value, and the accessible table lists the origin too.

The reserve blocks come from `get_m3_reserve_blocks`, read once per M3 decision and shared by all
its SKUs. The DAL reads them from the limits M3 fixed in the run's protocol
(`m3_protocolos.protocolo`, key `bloques`): the selection, embargo and test origins, and the target
dates of the selection and test blocks. The chart shades only the target-date ranges. Embargo
origins are windows that cross the boundary between the blocks; they are listed, never drawn as a
band of observed demand. No limit is derived from H, the selection fraction, the reserve length or
t*. A run whose protocol has no blocks is drawn without regions and says so.

The SVG has a title and a description, and uses circles with a solid line for demand and squares
with a dashed line for the forecast, so colour is never the only cue. The tests check the evidence
and the SVG separately: changing the origin ranges, the reported horizon, the reserve length, t* or
values such as 0.4 does not move the target-date bands.
