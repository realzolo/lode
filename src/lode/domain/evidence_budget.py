"""Canonical execution budget policy for native evidence access."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any

from lode.domain.errors import DomainValidationError


@dataclass(frozen=True, slots=True)
class ExecutionBudgetPolicy:
    """Closed value object persisted in each access-scope revision."""

    max_result_limit: int
    max_timeout_ms: int
    max_output_bytes: int
    max_total_output_bytes: int
    max_native_reads: int
    max_window_seconds: int
    max_parallel_operations: int
    estimated_cost: float

    def __post_init__(self) -> None:
        integer_values = (
            self.max_result_limit,
            self.max_timeout_ms,
            self.max_output_bytes,
            self.max_total_output_bytes,
            self.max_native_reads,
            self.max_window_seconds,
            self.max_parallel_operations,
        )
        if any(
            isinstance(item, bool) or not isinstance(item, int) or item < 1
            for item in integer_values
        ):
            raise DomainValidationError(
                "invalid_execution_budget_policy",
                "execution budget limits must be positive integers",
            )
        if (
            isinstance(self.estimated_cost, bool)
            or not isinstance(self.estimated_cost, int | float)
            or self.estimated_cost < 0
        ):
            raise DomainValidationError(
                "invalid_execution_budget_policy",
                "execution budget estimated cost must be non-negative",
            )
        if self.max_output_bytes > self.max_total_output_bytes:
            raise DomainValidationError(
                "invalid_execution_budget_policy",
                "per-read output budget cannot exceed the total output budget",
            )

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> ExecutionBudgetPolicy:
        expected = frozenset(cls.__dataclass_fields__)
        if frozenset(value) != expected:
            raise DomainValidationError(
                "invalid_execution_budget_policy",
                "execution budget policy must use the canonical closed field set",
            )
        return cls(
            max_result_limit=value["max_result_limit"],
            max_timeout_ms=value["max_timeout_ms"],
            max_output_bytes=value["max_output_bytes"],
            max_total_output_bytes=value["max_total_output_bytes"],
            max_native_reads=value["max_native_reads"],
            max_window_seconds=value["max_window_seconds"],
            max_parallel_operations=value["max_parallel_operations"],
            estimated_cost=float(value["estimated_cost"]),
        )

    def as_dict(self) -> dict[str, int | float]:
        return asdict(self)


def standard_execution_budget_policy(
    *,
    max_result_limit: int = 1_000,
    max_timeout_ms: int = 5_000,
    max_output_bytes: int = 1_000_000,
    max_total_output_bytes: int = 20_000_000,
    max_native_reads: int = 8,
    max_window_seconds: int = 3_600,
    max_parallel_operations: int = 1,
    estimated_cost: float = 0.0,
) -> dict[str, int | float]:
    return ExecutionBudgetPolicy(
        max_result_limit=max_result_limit,
        max_timeout_ms=max_timeout_ms,
        max_output_bytes=max_output_bytes,
        max_total_output_bytes=max_total_output_bytes,
        max_native_reads=max_native_reads,
        max_window_seconds=max_window_seconds,
        max_parallel_operations=max_parallel_operations,
        estimated_cost=estimated_cost,
    ).as_dict()
