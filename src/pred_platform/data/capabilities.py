"""What the platform can show today, and which open gap (G1 to G8) blocks the rest.

This registry is the single source of truth for the ``dal`` source: ``get_capabilities`` is built
from it and so is every "unavailable" document (their ``blocked_by``). When a gap is closed, the
entry changes here and its read is implemented in ``DalRepository``; nothing else moves.

It describes the platform's point of view (can *this platform* read it from its DAL?), which is
narrower than the engine's: ``synthetic_log_read`` works in the engine, but the platform does not
read the Phase 0 log until G8 lands. ``parquet_read`` is available since the worker launches the
engine on the ingestion's Parquet (G3), and the selection per SKU, its trials and its Walk-Forward
windows are in the DAL (G2 and G4).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, get_args

from pred_platform.contract.models import Availability, Capability, CapabilityId


@dataclass(frozen=True, slots=True)
class CapabilityState:
    available: bool
    reason_code: Literal["not_implemented", "stage_not_implemented"] | None = None
    blocked_by: tuple[str, ...] = ()


def _blocked(*gaps: str) -> CapabilityState:
    return CapabilityState(False, "not_implemented", gaps)


REGISTRY: dict[CapabilityId, CapabilityState] = {
    "parquet_read": CapabilityState(True),
    "ingest_report_persisted": CapabilityState(True),
    "topology_persisted": CapabilityState(True),
    "run_orchestration": CapabilityState(True),
    "family_comparison": CapabilityState(True),
    "selection_results_persisted": CapabilityState(True),
    "trial_detail_persisted": CapabilityState(True),
    "walkforward_evidence_persisted": CapabilityState(True),
    "champion_selection": CapabilityState(False, "stage_not_implemented"),
    "retrospective_validation": CapabilityState(False, "stage_not_implemented"),
    "synthetic_log_read": _blocked("G8"),
}

assert set(REGISTRY) == set(get_args(CapabilityId)), "the registry must list every capability"


def capability_items() -> list[Capability]:
    """The registry as the contract's ``Capability`` entries, in the contract's order."""
    return [
        Capability(
            id=capability,
            available=state.available,
            reason_code=state.reason_code,
            blocked_by=list(state.blocked_by),
        )
        for capability, state in REGISTRY.items()
    ]


def unavailable(capability: CapabilityId, detail: str) -> Availability:
    """The ``availability`` of a document that depends on ``capability``, per the registry."""
    state = REGISTRY[capability]
    if state.available:
        raise ValueError(f"{capability} is available; it has no unavailable form")
    assert state.reason_code is not None
    return Availability(
        status="unavailable",
        reason_code=state.reason_code,
        blocked_by=list(state.blocked_by),
        detail=detail,
    )


def blocked_by(capability: CapabilityId) -> tuple[str, ...]:
    """The open gaps behind ``capability`` (empty when none or when it is available)."""
    return REGISTRY[capability].blocked_by
