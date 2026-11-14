# resultados

Published Results by research question, with per-product and per-family evidence.

## What

Step 5 has six chapters under `/corridas/{id}/resultados/`: **Resumen** (`resumen`),
**¿Qué familia gana?** (`p1`), **¿Se verifica producto a producto?** (`p2`),
**¿Se sostiene con otra semilla?** (`estabilidad`), **Productos** (`productos`) and
**Exportar y reproducir** (`exportar`). The export chapter links every product of the evaluation;
the filtered CSV stays on the Productos verdict list.
`/resultados` redirects to the newest execution with a PUBLICADA M3 run. The old `vista=clase`
query opens chapter 1, `vista=errores` opens its error-profile panel, and `vista=sku` or
`vista=consulta` opens Productos, `vista=prueba` opens chapter 2, and `vista=exportar` opens the export chapter. Redirects keep filters, sort, page and the exact `m3` query.
Chapter 3 shows, for each seed 0, 1 and 2, the newest PUBLICADA evaluation that
`comparable_m3_runs` accepts beside the open one: the final group's own rules (same ingest,
identity, protocol and products). A queued, failed or incompatible run leaves its seed pending and
never hides a published one.
A running execution without publication locks step 5. A finished execution without M3 evidence
keeps the M2 reading. Productos has two readings: **Veredictos** and **Ver por familia**
(`lista=familias`, the state and value of each family, plus the published champion and verdict).
Both product readings show 25 rows per page unless `size` is explicit.
A link with the M2 filters (`family`, `estado`) opens the family reading. `/resultados/sku/{sku_id}` is the detail, under a
breadcrumb that goes back to Resultados and to the list the reader came from: per family its state, the evidence of the chosen configuration, the HPO trials
(completed, **pruned** and **failed**, each with its reason as a sentence from `fallas.describir_motivo`, and the engine's text for every trial in one "Detalle para soporte" under the table), the Walk-Forward summary and, on
request, every window with its metrics. Excluded families say why (policy matrix, configured subset,
series too short) and failed ones show their error the way Ejecución does: cause, next step, why it
failed and a copyable support detail (`pred_platform/fallas.py`).

The champion, the verdict and its evidence come from one exact M3 run of the M2 run shown (contract
v2): the one in `?m3=` or, without it, the newest PUBLICADA one that `list_m3_runs` resolves. Detail
links, filters and pages keep that resolved run. A context bar above the tabs names the execution,
its ingest, the M3 run and the data origin. The publication time is the end of the L4 stage that
created the shown M3 run, read by `get_stage_timings` from the run's `progreso.jsonl`. It shares
the cached reader of Ejecución (`run_status.read_stage_attempts`, every attempt in log order, an
end before its start is dropped and only a `completed` L4 publishes); without
that log the bar says the time is unknown and never shows `m3_corridas.creado_en`, which is the
start of L4. When several PUBLICADA runs share an identity the bar
shows the contract's notice and links to the others. Each verdict is shown with its Spanish
label and a one-line explanation, both owned by `app/presentation.py`; the published code stays
in filters, CSV, `data-*` attributes and engine output, not in `title`. The detail adds why the SKU got its verdict (`verdict_reason`, in the
order of the engine's rules), the engine's justification on request, windows against `n_min`, r
against Seasonal Naive (shown as «r frente a la línea base»),
coverage, the data origin and, apart from the verdict, the HLN-DM/BH significance or its cause. A
SKU launched after the cut says so; a run that is not PUBLICADA, or a publication without its
exclusion metadata, explains why there is no verdict.

Each chapter keeps the same resolved M3 run. Resumen quotes the thesis research question,
answers by demand type, and shows the three coverage figures from the published funnel.
Chapter 1 has the reserve timeline, selection dot plot, confirmation forest plots and
median-with-quartiles error profiles (`m3_cartera`), with the same metric switch. Its closed
tables preserve the selection reason, comparable and excluded products, excluded families,
IUT and Holm p, the median r against Seasonal Naive and its interval, and every competitor's
median ratio, Hodges-Lehmann, victories with Wilson intervals, the Wilcoxon statistic and Wilcoxon/IUT p.
Chapter 2 has 100% outcome bars, r histograms, BH/BY/FCR counts and descriptive verdicts.
Chapter 3 shows the champion and confirmation per seed, and the range between published effects.
It does not infer stability from two seeds or apply an effect-size threshold. Missing evidence
stays missing. All charts are server-rendered SVG, with values in `data-*` attributes for hover
and keyboard focus. Every value remains available in a label or a closed table. The SKU detail also draws the champion's published h=1 forecast
against the observed demand and, on request (`?reserva=1`), lists the reserve windows of the
champion and Seasonal Naive.

**Champion distribution** (RF-CON-08, task A4). **Resumen** also answers which family dominates the
portfolio: the closed "Campeona de cada producto, por familia" disclosure keeps the portfolio
and per-type tables. It counts the SKU with a verdict by the champion family M3 published for
each SKU (`get_m3_champions`, the `m3_campeones` view; `seasonal_naive` when the baseline wins).
The tables retain evaluated, not evaluated, SKU and share. The former single-family bar chart
is replaced by the per-type answer table.
The share is a presentation of two published figures, the family's count over the funnel's
`tras_exclusiones` (SKU with a verdict); the platform does not add or recompute counts. Products
excluded before M3 are in the funnel and not here. The portfolio rows are checked against that
denominator and, if they do not add up, the page says so with both numbers instead of adjusting one.
The family of a SKU can differ from its class champion (shown in chapter 1) by the fallback M3
documents in the justification. If the read of `m3_campeones` fails or comes incomplete, the funnel and
the test counts stay and the cause is shown. The M2 reading of the SKU tab links back to the summary
("Ver distribución de campeones") when a PUBLICADA M3 run is resolved.

**Veredictos** (`?vista=sku`, RF-CON-05 of section 4.5; called Consulta before) is the query
over M3's published verdicts. Unlike the M2 reading, whose rows are M2's selection per family, its
list comes from `get_m3_evidence` of
the resolved M3 run, so it can filter by the three things M3 decides: demand class (Syntetos-Boylan),
champion family and verdict (the five published codes, shown with their labels). The `prueba`
filter adds improvement, no demonstrated difference, worse, or untested, using the published
HLN-DM significance and the sign of its statistic. It does not use r or the descriptive verdict.
The view reads the matching SKU set and delegates its intersection, sort and page to
`get_m3_evidence(skus=...)`; CSV uses the same predicate. These filters combine with a SKU search, and the list
sorts by SKU, class, verdict, r, windows or BH-adjusted p. Each row shows the champion family and
model, the verdict, r, windows with `n_min` under them and, apart from the verdict, the significance
category or the cause M3 published when there is no test. The full test, including the statistic,
raw p and BH-adjusted p, opens on demand. The count is the filtered total, while the funnel line (SKU with a verdict out of all the
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

**Grupo de semillas** is a separate compact view at `/resultados/grupo`, linked from the
header of the Resultados step (also while it is locked) and from the thesis group on the
Corridas home. The reader explicitly chooses the PUBLICADA runs for seeds 0, 1 and 2.
Each option reads «Creada el …» from `GroupRunOption.creado_en` in Bogotá time (the start of L4,
not its end), with the run id
and the data fingerprint in `title`. A source without a test reads its cause through
`resultados_m3.source_cause`: the message catalog title for an exclusion, plain Spanish for a test
cause, and the published code in `title`.
It shows group significance by class, full/common-population lumpy medians and ranges, a query
for one SKU's three source tests, and a JSON download with all values and source row keys.
Until all three publications are selected, it shows no final-group numbers. The calculation
runs on read without new tables or an engine change; the app reuses the last four results, in
memory only, until `pred.db` or its WAL changes. See [the seed-group rules and command](../../../README.md#final-seed-group-evidence).

## How

Views call only `DataRepository` (`list_sku_selections`, `get_sku_selection`, `get_run_status` for the
run header, `list_m3_runs`, `get_m3_run` and `get_m3_evidence` for M3's decision,
`get_m3_categories` and `get_m3_confirmation` for Por clase,
`get_capabilities` for the action, and `list_m3_group_runs` / `get_m3_group` for the seed group).
Filters, sort and paging are **delegated** to the
repository and the page shows whatever it returned. The views only order published rows to show
them (classes, competitors, evaluated models) and format published numbers, for example a share or a
win percentage from published counts; they never filter rows or recompute a statistic, except the seed-group page, which picks the one SKU the reader asked for from the computed group,
and the test-result predicate, which selects SKU identifiers from published tests. The repository
applies all list filters, ordering and pagination. The browser never computes a statistic and runs
no inline script. `include_windows` is only requested with `?ventanas=1`.

- **Which run.** The repository accepts `latest` (`pred_platform.data.query.LATEST`) wherever it takes a
  run id; it resolves to the run with the highest id. Resultados uses it only when no execution has
  a PUBLICADA M3 run; otherwise it opens the newest one that has. `?ejecucion=3` shows another one,
  and an execution that does not exist says so instead of showing an empty panel.
- **"Nueva corrida con estos datos".** Secondary action in the step header that opens Ajustes of a
  new run (`/corridas/nueva/ajustes?ingest_id=…`) with the ingest of the execution shown. It is enabled only when
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
| Chapter answers and seed comparison | `src/pred_platform/app/resultados_capitulos.py` |
| SVG charts and interaction | `src/pred_platform/app/charts.py`, `static/js/charts.js`, `static/css/results.css` |
| Presentation (`family` and `verdict` vocabularies) | `src/pred_platform/app/presentation.py` |
| Pages | `src/pred_platform/app/templates/pages/results.html`, `results_producto.html` |
| Partials | `templates/partials/results_*.html` (`results_context.html` is the context bar) |
| Champion forecast series and chart | `src/pred_platform/app/resultados_pronostico.py`, `src/pred_platform/app/charts.py` |
| Tests | `tests/test_results_chapters.py`, `tests/test_resultados.py`, `tests/test_resultados_pronostico.py`, `tests/test_presentation.py` |

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
