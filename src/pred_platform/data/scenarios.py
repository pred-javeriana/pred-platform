"""Which of the contract's examples each fixture scenario serves.

A scenario picks, for every operation, the example that answers it. That settles the cases where
several examples share an identifier (``rejected``, ``failed`` and ``needs_confirmation`` reports
all use the same ``ingest_id``; the SKU ``FIL-0001`` appears in two selections).

Limits of the fixtures, inherited from the examples: there is a single set of ingests, one or two
runs, and per-SKU detail only for ``FIL-0001`` and ``FIL-0004``. The M3 documents (contract v2) are
their own illustrative runs (``m3-real-02`` and others, of the M2 run ``m2-ejemplo``); they are not
linked to the v1 run ``1`` or to its SKUs.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Scenario:
    """The examples (by name, without extension) that answer each operation."""

    name: str
    description: str
    capabilities: str
    ingest_list: str
    ingest_reports: tuple[str, ...]
    # What `submit_ingest` returns, without and with confirmation to overwrite.
    submit_ingest: tuple[str, str]
    topology_reports: tuple[str, ...]
    run_status: tuple[str, ...]
    sku_selection_list: str
    sku_selections: tuple[str, ...]
    synthetic_list: str
    synthetic_runs: tuple[str, ...]
    # M3's published evidence (contract v2): the run list and, per operation, the examples that
    # answer it, each for its own exact M3 run.
    m3_runs: str
    m3_run_status: tuple[str, ...] = ()
    m3_evidence: tuple[str, ...] = ()
    m3_metrics: tuple[str, ...] = ()
    m3_categories: tuple[str, ...] = ()
    m3_portfolio: tuple[str, ...] = ()
    m3_forecasts: tuple[str, ...] = ()
    m3_failures: tuple[str, ...] = ()
    m3_units: tuple[str, ...] = ()


_M3_STATUS = ("m3_run_status.publicada", "m3_run_status.rechazada", "m3_run_status.en_curso")
_M3_OTHER_RUNS = (
    "m3_evidence.rechazada",
    "m3_evidence.linea_base",
    "m3_evidence.origen_desconocido",
    "m3_evidence.sin_p_valor",
    "m3_evidence.sintetica",
)


_NORMAL = Scenario(
    name="normal",
    description="Happy path with data in every view.",
    capabilities="capabilities.todo_disponible",
    ingest_list="ingest_list.normal",
    ingest_reports=("ingest_report.accepted", "ingest_report.rejected"),
    submit_ingest=("ingest_report.accepted", "ingest_report.accepted"),
    topology_reports=("topology_report.normal",),
    run_status=("run_status.normal",),
    sku_selection_list="sku_selection_list.normal",
    sku_selections=("sku_selection.lumpy_solo_clasica", "sku_selection.smooth_cuatro_familias"),
    synthetic_list="synthetic_list.normal",
    synthetic_runs=("synthetic_run.normal", "synthetic_run.bitacora_anterior"),
    m3_runs="m3_runs.varias_publicadas",
    m3_run_status=_M3_STATUS,
    m3_evidence=("m3_evidence.publicada", *_M3_OTHER_RUNS),
    m3_metrics=("m3_metrics.campeon_y_base",),
    m3_categories=("m3_categories.por_clase",),
    m3_portfolio=("m3_portfolio.distribuciones",),
    m3_forecasts=("m3_forecasts.normal_y_nulo",),
    m3_failures=("m3_failures.candidato_invalido",),
    m3_units=("m3_units.confirmada_y_fallida",),
)

_VACIO = Scenario(
    name="vacio",
    description="Empty and unavailable states: nothing ingested, nothing run, gaps still open.",
    capabilities="capabilities.actual",
    ingest_list="ingest_list.vacio",
    ingest_reports=(),
    submit_ingest=("ingest_report.accepted", "ingest_report.accepted"),
    topology_reports=("topology_report.vacio",),
    run_status=("run_status.vacio",),
    sku_selection_list="sku_selection_list.vacio",
    sku_selections=(),
    synthetic_list="synthetic_list.vacio",
    synthetic_runs=(),
    m3_runs="m3_runs.vacia",
)

_ERRORES = Scenario(
    name="errores",
    description=(
        "Error variants: failed and conflicting uploads, run 2 with every task state, and an M3 "
        "publication without its exclusion metadata."
    ),
    capabilities="capabilities.todo_disponible",
    ingest_list="ingest_list.normal",
    ingest_reports=("ingest_report.accepted", "ingest_report.failed"),
    submit_ingest=("ingest_report.needs_confirmation", "ingest_report.failed"),
    topology_reports=("topology_report.normal",),
    run_status=("run_status.cinco_estados", "run_status.normal"),
    sku_selection_list="sku_selection_list.normal",
    sku_selections=("sku_selection.familia_fallida", "sku_selection.lumpy_solo_clasica"),
    synthetic_list="synthetic_list.normal",
    synthetic_runs=("synthetic_run.normal", "synthetic_run.bitacora_anterior"),
    m3_runs="m3_runs.varias_publicadas",
    m3_run_status=_M3_STATUS,
    m3_evidence=("m3_evidence.sin_metadatos", *_M3_OTHER_RUNS),
    m3_metrics=("m3_metrics.campeon_y_base",),
    m3_categories=("m3_categories.por_clase",),
    m3_portfolio=("m3_portfolio.distribuciones",),
    m3_forecasts=("m3_forecasts.normal_y_nulo",),
    m3_failures=("m3_failures.modelo_vacio",),
    m3_units=("m3_units.confirmada_y_fallida",),
)

SCENARIOS: dict[str, Scenario] = {s.name: s for s in (_NORMAL, _VACIO, _ERRORES)}
DEFAULT_SCENARIO = _NORMAL.name


def get_scenario(name: str) -> Scenario:
    """The scenario called ``name``; ``ValueError`` listing the valid names otherwise."""
    try:
        return SCENARIOS[name]
    except KeyError:
        raise ValueError(
            f"unknown fixture scenario {name!r}; use one of {sorted(SCENARIOS)}"
        ) from None
