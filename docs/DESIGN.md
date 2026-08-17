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

Polish is not spent evenly. Concentrate it on the screens an evaluator actually sees — Panel de
pronósticos, Resultados, and Monitoreo. Admin/utility screens (Configuración, Administración)
stay plain and functional; a plain admin screen is correct, not unfinished.

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
--ok: #3E6B4F;
--err: #A3402F;
--warn: #A0741F;
--info: #2E6E9E;
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

Eight states total, each rendered as color + icon + label (RNF-USA-04) — color alone never carries
meaning.

Task lifecycle: pendiente (neutral, clock), ejecutando (info, play), exitosa (ok, check), fallida
(err, x), no ejecutable (warn, banned-sign).

Validation verdicts: se sostiene (ok, check-circle), se sostiene parcialmente (warn, dash-circle),
no se sostiene (err, x-circle).

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
