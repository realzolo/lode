"""Code-owned hard limits for dynamic investigations.

The planner decides after every committed evidence wave whether the
investigation should finish or continue. These values are safety ceilings, not
user-selectable depth profiles and not model-controlled budgets.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class InvestigationHardLimits:
    max_decision_waves: int
    max_model_calls: int
    max_native_reads: int
    max_output_bytes: int
    max_cost: Decimal
    timeout_seconds: int
    max_parallel_operations: int


INVESTIGATION_HARD_LIMITS = InvestigationHardLimits(
    max_decision_waves=16,
    max_model_calls=14,
    max_native_reads=12,
    max_output_bytes=16 * 1024 * 1024,
    max_cost=Decimal("200"),
    timeout_seconds=900,
    max_parallel_operations=4,
)


def investigation_execution_budget() -> dict[str, int | float]:
    """Return a fresh JSON-serializable copy of the immutable hard ceiling."""
    limits = INVESTIGATION_HARD_LIMITS
    return {
        "max_decision_waves": limits.max_decision_waves,
        "max_model_calls": limits.max_model_calls,
        "max_native_reads": limits.max_native_reads,
        "max_output_bytes": limits.max_output_bytes,
        "max_cost": float(limits.max_cost),
        "timeout_seconds": limits.timeout_seconds,
        "max_parallel_operations": limits.max_parallel_operations,
    }
