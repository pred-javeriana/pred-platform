"""Which of the contract's examples each fixture scenario serves.

A scenario picks, for every operation, the example that answers it. That settles the cases where
several examples share an identifier (``rejected``, ``failed`` and ``needs_confirmation`` reports
all use the same ``ingest_id``; the SKU ``FIL-0001`` appears in two selections).

Limits of the fixtures, inherited from the examples: there is a single set of ingests, one or two
runs, and per-SKU detail only for ``FIL-0001`` and ``FIL-0004``.
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
    validation_verdicts: str
    synthetic_list: str
    synthetic_runs: tuple[str, ...]


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
    validation_verdicts="validation_verdicts.con_veredictos",
    synthetic_list="synthetic_list.normal",
    synthetic_runs=("synthetic_run.normal", "synthetic_run.bitacora_anterior"),
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
    validation_verdicts="validation_verdicts.no_disponible",
    synthetic_list="synthetic_list.vacio",
    synthetic_runs=(),
)

_ERRORES = Scenario(
    name="errores",
    description="Error variants: failed and conflicting uploads, run 2 with every task state.",
    capabilities="capabilities.todo_disponible",
    ingest_list="ingest_list.normal",
    ingest_reports=("ingest_report.accepted", "ingest_report.failed"),
    submit_ingest=("ingest_report.needs_confirmation", "ingest_report.failed"),
    topology_reports=("topology_report.normal",),
    run_status=("run_status.cinco_estados", "run_status.normal"),
    sku_selection_list="sku_selection_list.normal",
    sku_selections=("sku_selection.familia_fallida", "sku_selection.lumpy_solo_clasica"),
    validation_verdicts="validation_verdicts.con_veredictos",
    synthetic_list="synthetic_list.normal",
    synthetic_runs=("synthetic_run.normal", "synthetic_run.bitacora_anterior"),
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
