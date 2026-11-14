# PRED Platform — Design Reference

Visual design reference for anyone building a screen in this repo: palette, typography, spacing,
and component conventions, with the requirement each decision satisfies. This is the operational
reference for implementation. The academic record of how these decisions were reached — rationale,
requirement text, and revision history — lives in the `pred-docs` repository's `diseno/` folder;
this document tracks that record and should be updated alongside it, not diverge from it.

## Foundation

The UI is built on a vendored Pico.css base plus a small hand-written brand layer on top —
palette, typography, and the component patterns below. No CDN, no build toolchain: everything
ships as local static assets. Templates reference them through `static_url`, which appends a
hash of the file's bytes (`?v=…`), so an update never leaves a browser on a cached older sheet or
script; never hand-write a `?v=` number. Keep the brand layer small; lean on Pico's own element defaults
(forms, tables, buttons) wherever the brand layer doesn't need to override them.

Polish is not spent evenly. Concentrate it on the three operational surfaces an evaluator
actually sees — Datos, Ejecución, and Resultados (`docs/UI_FLOW.md`). There is no login, no
role switch and no administration screen in the product. Utility chrome stays plain and
functional; a plain supporting block is correct, not unfinished.

## Palette

Cool neutral base; every color carries a semantic role, never decoration. `--primary` is reserved
for exactly one primary action per view — don't spend it on borders, backgrounds, or emphasis.

```css
--canvas: #E8E9E5;        /* page background, dark enough that a white card reads as a card */
--surface: #FFFFFF;       /* cards, panels, inputs */
--ink: #14161A;           /* the one most prominent heading on a screen */
--ink-soft: #2C2F34;      /* ordinary body/label/heading text */
--muted: #6B6F76;         /* secondary/meta text */
--border: #DBDCD7;
--border-strong: #C7C9C5;
--card-border: #CDCFCA;   /* the edge of every card against the canvas */
--primary: #1F5C54;
--primary-dark: #164740;
--primary-soft: #E2EDEA;
--ok: #3E6B4F;
--ok-soft: #E3EDE6;
--err: #A3402F;
--err-soft: #F4E4E0;
--warn: #A0741F;
--warn-soft: #F3E9D4;
--info: #2E6E9E;
--info-soft: #E4EEF4;
--data: #1B7A8C;          /* numeric/metric values */
--family-classical: #008300;  --family-classical-ink: #0B5C0B;  --family-classical-soft: #E4F2E4;
--family-ml: #EDA100;         --family-ml-ink: #7A5200;         --family-ml-soft: #FCF0D2;
--family-dl: #E87BA4;         --family-dl-ink: #99305C;         --family-dl-soft: #FCE8F0;
--family-foundation: #6250D6; --family-foundation-ink: #4636AE; --family-foundation-soft: #EBE8FB;
```

Numeric values (metrics, scores) are colored `--data` directly rather than left in body ink. A
count with pass/fail meaning takes that role's color (e.g. a validated-count KPI takes `--ok`); a
plain count with no such meaning stays `--ink-soft`. Status is always color + icon + label
together — never color alone (RNF-USA-04).

## Typography

One family for both display and body — a single well-tuned face reads more considered than a
display/body split, and this is a working tool (Operate mode), not a marketing surface. Archivo,
weights 400–800, self-hosted (no CDN). Display headings run large and heavy with tight tracking
(-0.02 to -0.025em); body and data text stay small and dense — the contrast comes from a real
scale jump, not from adding elements.

JetBrains Mono is scoped to numeric/measurement content only — tabular figures, metric values, SKU
codes — never used decoratively elsewhere.

## Structure

- No nested cards, no card-of-icon-plus-heading-plus-stat as the default container. Sections and
  rows are divided by 1px `--border` hairlines.
- Every card (`.pred-panel`, `.wz-panel`, `.pred-sheet`, `.pred-evidence-panel`, `.pred-run`,
  answer cards) takes the same contract from `pred.css`: `--surface` fill, a 1px `--card-border`
  edge, `--card-pad-y`/`--card-pad-x` inner padding and `--card-gap` below it; the page keeps
  `--page-gutter` at its sides. Narrow screens change only these tokens, never a card's own rule,
  and no card drops its padding or border to sit flush with the canvas.
- No `box-shadow` as default elevation. A real popover declares elevation once — border or shadow,
  never both.
- Sharp corners: 0–2px radius on every control (buttons, inputs, containers). Not the rounded
  6–12px shape that reads as generic template UI.
- A KPI row is flat — label/value pairs separated by space or a hairline, not a row of cards.
  Plain counts stay in ink; a row of metrics (`pred-kpis--metrics`) takes `--data`.
- Table numerics: aligned columns, `--data` color, `font-variant-numeric: tabular-nums`. The
  header of a numeric column aligns right with its figures.
- A table is as wide as its content (`width: auto`, at least 36rem) and scrolls inside its own
  region when it does not fit. A four-column table never stretches across a wide screen.

## Components

**Buttons.** Three hierarchy tiers — primary (filled, the one primary action per view), secondary,
subtle. Flat, 0–2px radius. Destructive actions use `--err`, never `--primary`, and never rely on
color alone to signal "destructive."

**Forms.** States: default, focus (`:focus-visible` ring in `--primary`), error (border + icon +
message, never color alone), disabled. Controls share the 0–2px radius.

**Status and alerts.** Plain colored text + icon on the neutral surface — not a filled pill/badge.
Four alert levels (info, success, warning, error), each icon + message, color as reinforcement, not
the only channel. A message states the fact and, where possible, the next action.

**Failures.** Every engine or platform error renders through `fallas.describir_falla` and the
`components/falla.html` macros, in three layers: a one-line cause and what to do; «Por qué
falló», a disclosure with the trial counts and one example per discard cause in words; and
«Detalle para soporte», a collapsed, copyable block with the code, stage, exception and engine
text. The engine text, Python literals and internal paths never appear outside that block.

**Execution progress.** The run ring draws only the published percentage: `--info` while the run
is open and `--ok` at 100 %. The linear bar interpolates blue (running) to green (complete) on a
cubic curve so the transition to "complete" is visually obvious. Percentage, counts and label
always accompany the color. The engine stages (L1 to L4) render as a numbered stepper, as GitHub
Actions and Vercel show a deploy: a finished stage shows a check in `--ok`, the current stage is
outlined in `--info` with `aria-current="step"`, and a pending stage stays muted.

**Navigation.** The top bar names the product and numbers the three surfaces in flow order
(1 Datos, 2 Ejecución, 3 Resultados). The current surface carries `aria-current="page"` and an
underline in `--primary`. The browser tab shows `static/favicon.svg`, three bars in `--primary`.

**Context bar.** A page that reads one run (Resultados) says which run, M3 evidence and data
origin it shows in a bar at the top, across the full width, between two hairlines. The run
picker lives in that bar, not in a side column.

**Tabs, segmented control and breadcrumb.** Detail sections are horizontal tabs with a 2px
underline in `--primary` on the active one. Two readings of one list use a segmented control.
A detail page under a list opens with a breadcrumb whose links return to the list with its
filters.

**Empty states.** Guide the next step rather than leaving the screen blank — centered content, max
width, one clear action.

## States

State vocabularies are specific to each context; there is no single fixed total.
Every presented state uses color + icon + label (RNF-USA-04); color alone never carries meaning.

**Current implementation.** Task lifecycle: pendiente (neutral, clock), ejecutando (info, play),
finalizo_m2 (info, check-circle, shown as «Por evaluar»; M2 finished, waiting for M3), exitosa (ok, check), fallida (err, x), no ejecutable (warn, banned-sign). Family states also include
excluida. Runs use pendiente, ejecutando, completada, completada_con_fallos and detenida. A run that
finished with failed tasks still reads «Completada» (ok); the outcome line beside it says how many
tasks failed and how many products have a verdict. A stopped run («Detenida») is warn, like its
error notice, while the page offers Reanudar; it is err only when resuming would fail the same way
(`run.incompatible_config`, families that leave no class two to compare, or an L2 failure that
depends only on the engine policy and the data). `presentation.describe_run` owns that tone;
`ejecucion.offers_resume` owns the rule for all three cases, read the same way by Ejecución and by
the Resultados context bar.
HPO trials use pendiente, corriendo, completado, podado and fallido; podado is distinct from a
failure. Ingestion has accepted, rejected, failed and needs_confirmation, presented through the
ingest vocabulary. These contexts remain separate in the presentation helpers.

**M3 verdicts (contract v2).** The five verdicts M3 publishes are shown with a Spanish label and
a one-line explanation from `app/presentation.py`; the code stays in filters, CSV, `data-*` attributes and engine output, not in `title`:
Validado (ok, check-circle), Exploratorio (info, info), Evidencia insuficiente (warn,
minus-circle), No evaluable (neutral, banned-sign) and Error técnico (err, x-circle).
The significance (HLN-DM/BH) is a separate block, never a verdict tone.

## Icons

Inline SVG, line style, stroke 2, one meaning per icon. No emoji, no icon font/CDN. Icons double as
the non-color channel for state (RNF-USA-04).

## Spacing

4px-based scale (4, 8, 16, 24). Dividers are hairlines; control radius stays 0–2px. No stacked
boxes-and-shadows — that's what keeps the register sober and analytical rather than reading as a
consumer/SaaS product (RNF-USA-05).

## Why these constraints exist

The interface is demonstrated to academic evaluators and used as an operational analysis
instrument — RNF-USA-05 requires a sober, analytical appearance and explicitly rules out a
consumer-product or SaaS look. RNF-USA-04 requires every state to be distinguishable without color
alone, since the interface may be shown on a projector to viewers with reduced color perception.
Both are recorded in the SRS (v1.1, §3.5.6); full rationale and the decision history live in
`pred-docs/diseno/`.

## Ajustes de presentación D2

La paleta base se conserva. Para texto pequeño sobre canvas/surface, pred.css usa variantes de contraste: --muted-text (#5D6168), --warn-text (#785415) y --data-text (#176777). Los tokens base continúan disponibles para trazos y marcadores. Debe verificarse contraste con el color de fondo computado en navegador, incluido hover/foco.

Los títulos primarios usan escala adaptable y peso 800. Las etiquetas KPI usan Archivo; JetBrains Mono queda en cifras e identificadores. Toda tabla ancha tiene una región nombrada desplazable por teclado, caption y scope de encabezados. Las listas primarias se paginan en servidor; evidencia técnica anidada usa details cerrados y un contenedor visual de altura acotada cuando el contrato no ofrece paginación propia. El contenedor pred-evidence-scroll no tiene role, aria-label ni tabindex: la pred-table-wrap interior creada por table_start() es la única región nombrada y enfocable de la tabla.


La revisión final D2 usa texto operativo de 14px y controles de 40px. Inputs/selects comparten altura y alineación; una ayuda debajo no desplaza el campo. Checkboxes tienen dimensiones explícitas y los selects conservan un chevron. La navegación de estados se agrupa con tamaños homogéneos. La carga visible usa etiquetas españolas independientemente del idioma del navegador (el diálogo del sistema conserva su propio idioma). Las clases se presentan como Suave, Errática, Intermitente y Grumosa, cada una con una explicación de una línea; sus códigos contractuales no cambian.

La primera vista presenta situación, resumen y siguiente acción. Diagnóstico y tareas se consultan mediante pestañas de detalle; filtros, familias y evidencia técnica conservan disclosures nativos con semántica expanded/collapsed. Una consulta explícita o error activa el área relevante. Un envío inválido lleva el foco al primer control con error. Ver UI_FLOW.md y UI_REVIEW.md para comportamiento y límites.

## Composición en una columna

Cada paso de una corrida es una sola columna de hasta 1240px a la derecha de la barra de pasos: el encabezado con «Paso N de 5», el nombre, el propósito, «Después: …» y a lo sumo una acción; el resumen de la situación en un panel blanco; debajo, pestañas horizontales de detalle con un panel a la vez; y al pie, Atrás y Siguiente. La barra de pasos es angosta (88px, la marca y el nombre corto de cada paso) y se abre a 340px sobre la página al pasar el puntero o al enfocarla, sin mover la página, así que no le quita ancho a las tablas. Bajo 900px la reemplaza un resumen compacto arriba del encabezado. Linear y Stripe Dashboard ordenan así sus vistas de detalle: el contexto arriba y a lo ancho, y el contenido debajo. Panel y pestañas son superficies hermanas, blancas y sin sombras; no se anidan tarjetas ni se convierte cada cifra en una tarjeta. Las tablas mantienen su desplazamiento local y la paginación del repositorio.

En Datos, el resumen del archivo tiene la mayor jerarquía: nombre, estado, conteos en una fila de KPI, la mezcla de clases de demanda en barras; Siguiente, al pie del paso, elige el archivo. La validez corresponde a la ingesta, no a la calidad predictiva. Diagnóstico, clases y SKU, cargar otro archivo e historial quedan en pestañas con título y descripción por sección. Las acciones conservan ancho intrínseco.

En Ejecución, el panel de la corrida reúne estado, acciones, hechos de la corrida, etapas del motor, el anillo de progreso y los conteos por estado. El anillo representa únicamente el porcentaje ya informado, con conteos y alternativa accesible. A su lado, los conteos por estado enlazan con las tareas filtradas, y se nombra la etapa en curso y cuántos de sus SKU terminaron; no estima tiempo restante. Una corrida detenida sigue mostrando las tareas exitosas previas, sin presentar fallos cerrados como progreso logrado. En Resultados, la barra de contexto precede a las pestañas y a la hoja de la vista elegida; en SKU, la ruta de migas, la decisión y las familias preceden a la evidencia técnica.

## Pestañas de detalle y jerarquía de lectura

Las secciones secundarias relacionadas se presentan como pestañas horizontales sobre un panel activo, como las de GitHub y Stripe. Abrir una sección no desplaza las otras opciones. En pantallas compactas las pestañas pasan a otra fila en vez de esconderse tras scroll horizontal. La comparación principal entre familias no se divide en pestañas, porque se necesita contrastar sus resúmenes simultáneamente.

El componente evidence.html genera enlaces y secciones funcionales sin JavaScript. pred.js mejora ese marcado con tablist/tab/tabpanel horizontal, selección y controles ARIA, un único tab stop en las pestañas, flechas izquierda y derecha, Home/End y Enter/Espacio. Una sola sección está visible con JS. El hash permite volver directamente a ella; filtros/paginación activan el panel correspondiente. El panel elegido sobrevive a los swaps HTMX sin alterar el bloqueo de actualizaciones durante foco o selección.

La tipografía secundaria diferencia título del panel (20px), título de sección y de pestaña (18px), subsección (16px) y texto/etiquetas (14px). Publicación y parámetros se leen en pares etiqueta/valor; umbrales y fecha se agrupan como hechos, con fechas y cifras sin cortes internos. Las tablas desplazan localmente cuando hace falta. Los detalles técnicos anidados siguen siendo opcionales. Un único bloque de trazabilidad se muestra directamente, sin un acordeón o una pestaña redundantes.

## Identidad de las familias

Cada familia de modelos tiene un color, un ícono y un tono para el texto. Son los mismos en Ajustes,
en Revisar y lanzar y en los gráficos de Resultados.

| Familia (código) | Ícono | Gráficos y regla superior `--family-X` | Texto e ícono `--family-X-ink` | Fondo `--family-X-soft` |
| --- | --- | --- | --- | --- |
| Clásica (`classical`) | `i-family-classical`, onda estacional | `#008300` | `#0b5c0b` | `#e4f2e4` |
| Aprendizaje automático (`ml`) | `i-family-ml`, árbol de decisión | `#eda100` | `#7a5200` | `#fcf0d2` |
| Aprendizaje profundo (`dl`) | `i-family-dl`, capas apiladas | `#e87ba4` | `#99305c` | `#fce8f0` |
| Fundacional (`foundation`) | `i-family-foundation`, chispa grande y chica | `#6250d6` | `#4636ae` | `#ebe8fb` |

- El color del gráfico (`--family-X`) es solo para reglas y marcas. Dos de ellos fallan como texto sobre
  blanco (Aprendizaje automático 2,17; Aprendizaje profundo 2,69), así que el texto y los íconos usan `-ink`
  (contraste de 6:1 o más sobre `-soft`) y el fondo del bloque usa `-soft`.
- `--family-X` y `charts.FAMILY_COLORS` tienen los mismos cuatro valores; una prueba lo comprueba.
- El bloque de una familia en Revisar y lanzar es el único bloque con fondo de color permitido. No es una
  tarjeta: sin sombra, sin anidar, radio de 2 px. Una familia que no compite va en blanco, con borde
  punteado gris y el texto «No compite»: el color nunca es la única señal (RNF-USA-04).
