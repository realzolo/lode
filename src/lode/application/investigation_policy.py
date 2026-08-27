"""Server-owned investigation profiles.

Profiles keep product-level investigation limits intentional and reviewable
without turning each internal budget into a deployment setting or a mutable
free-form JSON policy.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from types import MappingProxyType
from typing import Literal, Mapping

InvestigationProfile = Literal["fast", "balanced", "deep"]


@dataclass(frozen=True, slots=True)
class InvestigationPolicyValues:
    max_evidence_steps: int
    max_model_calls: int
    max_native_reads: int
    max_output_bytes: int
    max_cost: Decimal
    timeout_seconds: int
    window_before_seconds: int
    window_after_seconds: int


INVESTIGATION_POLICY_PROFILES: Mapping[InvestigationProfile, InvestigationPolicyValues] = (
    MappingProxyType(
        {
            "fast": InvestigationPolicyValues(
                max_evidence_steps=6,
                max_model_calls=5,
                max_native_reads=4,
                max_output_bytes=2 * 1024 * 1024,
                max_cost=Decimal("25"),
                timeout_seconds=300,
                window_before_seconds=600,
                window_after_seconds=600,
            ),
            "balanced": InvestigationPolicyValues(
                max_evidence_steps=12,
                max_model_calls=10,
                max_native_reads=8,
                max_output_bytes=8 * 1024 * 1024,
                max_cost=Decimal("100"),
                timeout_seconds=600,
                window_before_seconds=900,
                window_after_seconds=900,
            ),
            "deep": InvestigationPolicyValues(
                max_evidence_steps=16,
                max_model_calls=14,
                max_native_reads=12,
                max_output_bytes=16 * 1024 * 1024,
                max_cost=Decimal("200"),
                timeout_seconds=900,
                window_before_seconds=1800,
                window_after_seconds=1800,
            ),
        }
    )
)


def investigation_policy_values(profile: InvestigationProfile) -> InvestigationPolicyValues:
    """Resolve a validated profile to its immutable server-owned budget."""
    return INVESTIGATION_POLICY_PROFILES[profile]


def investigation_policy_columns(profile: InvestigationProfile) -> dict[str, int | Decimal]:
    """Return the persisted budget columns for a server-owned profile."""
    values = investigation_policy_values(profile)
    return {
        "max_evidence_steps": values.max_evidence_steps,
        "max_model_calls": values.max_model_calls,
        "max_native_reads": values.max_native_reads,
        "max_output_bytes": values.max_output_bytes,
        "max_cost": values.max_cost,
        "timeout_seconds": values.timeout_seconds,
        "window_before_seconds": values.window_before_seconds,
        "window_after_seconds": values.window_after_seconds,
    }
