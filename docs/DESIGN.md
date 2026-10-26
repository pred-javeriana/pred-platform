# PRED Platform — Design Reference

Visual design reference for anyone building a screen in this repo: palette, typography, spacing,
and component conventions, with the requirement each decision satisfies. This is the operational
reference for implementation. The academic record of how these decisions were reached — rationale,
requirement text, and revision history — lives in the `pred-docs` repository's `diseno/` folder;
this document tracks that record and should be updated alongside it, not diverge from it.

## Foundation

The UI is built on a vendored Pico.css base plus a small hand-written brand layer on top —
palette, typography, and the component patterns below. No CDN, no build toolchain: everything
ships as local static assets. Keep the brand layer small; lean on Pico's own element defaults
(forms, tables, buttons) wherever the brand layer doesn't need to override them.

Polish is not spent evenly. Concentrate it on the three operational surfaces an evaluator
actually sees — Datos, Ejecución, and Resultados (`docs/UI_FLOW.md`). There is no login, no
role switch and no administration screen in the product. Utility chrome stays plain and
functional; a plain supporting block is correct, not unfinished.

## Palette

Cool neutral base; every color carries a semantic role, never decoration. `--primary` is reserved
for exactly one primary action per view — don't spend it on borders, backgrounds, or emphasis.

```css
--canvas: #EFEFEC;        /* page background */
--surface: #FFFFFF;       /* cards, panels, inputs */
--ink: #14161A;           /* the one most prominent heading on a screen */
--ink-soft: #2C2F34;      /* ordinary body/label/heading text */
--muted: #6B6F76;         /* secondary/meta text */
--border: #DBDCD7;
--border-strong: #C7C9C5;
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
- No `box-shadow` as default elevation. A real popover declares elevation once — border or shadow,
  never both.
- Sharp corners: 0–2px radius on every control (buttons, inputs, containers). Not the rounded
  6–12px shape that reads as generic template UI.
- A KPI row is flat — label/value pairs separated by space or a hairline, not a row of cards.
- Table numerics: aligned columns, `--data` color, `font-variant-numeric: tabular-nums`.

## Components

**Buttons.** Three hierarchy tiers — primary (filled, the one primary action per view), secondary,
subtle. Flat, 0–2px radius. Destructive actions use `--err`, never `--primary`, and never rely on
color alone to signal "destructive."

**Forms.** States: default, focus (`:focus-visible` ring in `--primary`), error (border + icon +
message, never color alone), disabled. Controls share the 0–2px radius.

**Status and alerts.** Plain colored text + icon on the neutral surface — not a filled pill/badge.
Four alert levels (info, success, warning, error), each icon + message, color as reinforcement, not
the only channel. A message states the fact and, where possible, the next action.

**Execution progress.** Bar interpolates blue (running) → green (complete) on a cubic curve so the
transition to "complete" is visually obvious. Percentage and label always accompany the color.

**Empty states.** Guide the next step rather than leaving the screen blank — centered content, max
width, one clear action.

## States

State vocabularies are specific to each context; there is no single fixed total.
Every presented state uses color + icon + label (RNF-USA-04); color alone never carries meaning.

**Current implementation.** Task lifecycle: pendiente (neutral, clock), ejecutando (info, play),
exitosa (ok, check), fallida (err, x), no ejecutable (warn, banned-sign). Family states also include
excluida. Runs use pendiente, ejecutando, completada, completada_con_fallos and detenida.
HPO trials use pendiente, corriendo, completado, podado and fallido; podado is distinct from a
failure. Ingestion has accepted, rejected, failed and needs_confirmation, presented through the
ingest vocabulary. These contexts remain separate in the presentation helpers.

**Current v1 / legacy contract verdicts.** mantiene → se sostiene (ok, check-circle),
parcial → se sostiene parcialmente (warn, dash-circle), falla → no se sostiene (err, x-circle).
These labels describe the copied v1 contract and fixtures; they are not the final M4 vocabulary.

**Published M3 evidence; pending M4 integration in these views.** The five verdicts are
FALLO_TECNICO, NO_EVALUABLE, EVIDENCIA_INSUFICIENTE, VALIDADO and EXPLORATORIO.
D2 documents this dependency without adding their contract adapter, mapping them to legacy
verdicts, deriving new decisions or implementing M4 presentation functionality.

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


La revisión final D2 usa texto operativo de 14px y controles de 40px. Inputs/selects comparten altura y alineación; una ayuda debajo no desplaza el campo. Checkboxes tienen dimensiones explícitas y los selects conservan un chevron. La navegación de estados se agrupa con tamaños homogéneos. La carga visible usa etiquetas españolas independientemente del idioma del navegador (el diálogo del sistema conserva su propio idioma). Las clases se presentan como Smooth (Suave), Erratic (Errática), Intermittent (Intermitente) y Lumpy (Grumosa); sus códigos contractuales no cambian.

La primera vista presenta situación, resumen y siguiente acción. Diagnóstico y tareas se consultan mediante navegación local persistente; filtros, familias y evidencia técnica conservan disclosures nativos con semántica expanded/collapsed. Una consulta explícita o error activa el área relevante. Un envío inválido lleva el foco al primer control con error. Ver UI_FLOW.md y UI_REVIEW.md para comportamiento y evidencia.

## Composición operativa en dos columnas

La revisión espacial solicitada por el auditor usa un área principal amplia y un contexto lateral menor. Son dos superficies hermanas, blancas y sin sombras; no se anidan tarjetas ni se convierte cada cifra en una tarjeta. La grilla se apila por debajo de 56rem, conservando el orden de lectura del DOM. Las tablas mantienen su desplazamiento local y la paginación del repositorio.

En Datos, el estado del archivo y sus conteos tienen la mayor jerarquía; la topología global y la siguiente acción ocupan la columna secundaria. La validez corresponde a la ingesta, no a la calidad predictiva. Diagnóstico, distribución detallada, sustitución de archivo e historial quedan en un navegador inferior con título y descripción por sección. La acción de continuar tiene un área de 48px de alto y usa el ancho de la columna secundaria; las acciones ordinarias conservan ancho intrínseco.

En Ejecución, el gráfico circular representa únicamente el porcentaje ya informado, con conteos y alternativa accesible. A su lado, los cinco estados enlazan con las tareas filtradas. No representa etapas M1–M4 ni estima tiempo restante. Una corrida detenida sigue mostrando las tareas exitosas previas, sin presentar fallos cerrados como progreso logrado. En Resultados, el explorador paginado es el área principal y el contexto metodológico queda al lado; en SKU, decisión y familias preceden a la evidencia técnica.

## Navegación de evidencia y jerarquía de lectura

Las secciones secundarias relacionadas se presentan en un solo espacio de consulta: menú vertical persistente y un panel activo. Abrir una sección no desplaza las otras opciones. En pantallas compactas el menú se convierte en dos filas de opciones cortas, sin esconderlas tras scroll horizontal; permanece visible al leer el panel. La comparación principal entre familias no se divide en pestañas, porque se necesita contrastar sus resúmenes simultáneamente.

El componente evidence.html genera enlaces y secciones funcionales sin JavaScript. pred.js mejora ese marcado con tablist/tab/tabpanel, selección, controles y orientación ARIA, un único tab stop en el menú, flechas según orientación, Home/End y Enter/Espacio. Una sola sección está visible con JS. El hash permite volver directamente a ella; filtros/paginación activan el panel correspondiente. El panel elegido sobrevive a los swaps HTMX sin alterar el bloqueo de actualizaciones durante foco o selección.

La tipografía secundaria diferencia título del espacio (22px), título del panel (20px), subsección (16px) y texto/etiquetas (14px). Ayudas de navegación de 13px se omiten en móvil, donde se conservan los títulos. Publicación y parámetros se leen en pares etiqueta/valor; umbrales y fecha se agrupan como hechos, con fechas y cifras sin cortes internos. Las tablas desplazan localmente cuando hace falta. Los detalles técnicos anidados siguen siendo opcionales. Un único bloque de trazabilidad se muestra directamente, sin un acordeón o una pestaña redundantes.
