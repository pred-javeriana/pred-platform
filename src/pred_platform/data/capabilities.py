"""What the platform can show today, and which open gap (G1 to G8) blocks the rest.

This registry is the single source of truth for the ``dal`` source: ``get_capabilities`` is built
from it and so is every "unavailable" document (their ``blocked_by``). When a gap is closed, the
entry changes here and its read is implemented in ``DalRepository``; nothing else moves.

It describes the platform's point of view (can *this platform* read it from its DAL?), which is
narrower than the engine's: ``parquet_read`` and ``synthetic_log_read`` work in the engine, but the
platform does not read the engine's files until G3 and G8 land.
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
    "parquet_read": _blocked("G3"),
    "ingest_report_persisted": _blocked("G1"),
    "topology_persisted": _blocked("G1"),
    "run_orchestration": _blocked("G3"),
    "family_comparison": _blocked("G2", "G3"),
    "selection_results_persisted": _blocked("G4"),
    "trial_detail_persisted": _blocked("G2", "G4"),
    "walkforward_evidence_persisted": _blocked("G3", "G4"),
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
