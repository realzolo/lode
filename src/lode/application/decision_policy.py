"""Deterministic policy for one dynamic investigation decision wave."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from collections.abc import Set as AbstractSet
from typing import Any

from lode.domain.investigation import (
    CapabilityEntry,
    DecisionBudget,
    EvaluatedDecision,
    InvestigationDecision,
    PlannedOperation,
    PolicyDecision,
)


class DecisionPolicyEngine:
    """Evaluate model intent without adding any action the model did not select."""

    def evaluate(
        self,
        decision: InvestigationDecision,
        catalog: Sequence[CapabilityEntry],
        *,
        budget: DecisionBudget,
        attempted_fingerprints: AbstractSet[str] = frozenset(),
    ) -> EvaluatedDecision:
        entries = {entry.action_id: entry for entry in catalog}
        policy_log: list[PolicyDecision] = []
        hypothesis_ids = {item.hypothesis_id for item in decision.hypotheses}

        if decision.next_model_hint is not None:
            hint_role = decision.next_model_hint.get("role")
            if decision.decision == "continue" and hint_role == "planner":
                policy_log.append(
                    PolicyDecision(
                        "next_model_hint_approved",
                        "allow",
                        None,
                        {
                            "reason": "The server policy permits a planner execution-class preference.",
                            "execution_class": decision.next_model_hint.get(
                                "execution_class"
                            ),
                        },
                    )
                )
            else:
                policy_log.append(
                    PolicyDecision(
                        "next_model_hint_rejected",
                        "trim",
                        None,
                        {
                            "reason": "The server workflow, not model output, selects the next role."
                        },
                    )
                )

        counter_gate = self._counter_evidence_gate(decision)
        if counter_gate is not None:
            return self._reject(decision, counter_gate)

        if decision.decision == "finish":
            return EvaluatedDecision(
                decision, "allow", (), tuple(policy_log), 0.0, 0, 0
            )

        accepted: list[tuple[PlannedOperation, CapabilityEntry]] = []
        for operation in decision.operations:
            if operation.fingerprint in attempted_fingerprints:
                policy_log.append(
                    PolicyDecision(
                        "operation_already_attempted",
                        "trim",
                        operation.action_id,
                        {"fingerprint": operation.fingerprint},
                    )
                )
                continue
            entry = entries.get(operation.action_id)
            reason = self._operation_rejection(
                operation,
                entry,
                hypothesis_ids=hypothesis_ids,
            )
            if reason is not None:
                policy_log.append(reason)
                continue
            assert entry is not None
            accepted.append((operation, entry))

        resource_counts = Counter(entry.resource_key for _, entry in accepted)
        conflicts = {
            key
            for key, count in resource_counts.items()
            if count
            > min(entry.max_parallelism for _, entry in accepted if entry.resource_key == key)
        }
        if conflicts:
            retained: list[tuple[PlannedOperation, CapabilityEntry]] = []
            resource_used: Counter[str] = Counter()
            for operation, entry in accepted:
                if (
                    entry.resource_key in conflicts
                    and resource_used[entry.resource_key] >= entry.max_parallelism
                ):
                    policy_log.append(
                        PolicyDecision(
                            "resource_conflict",
                            "trim",
                            operation.action_id,
                            {"resource_key": entry.resource_key},
                        )
                    )
                    continue
                resource_used[entry.resource_key] += 1
                retained.append((operation, entry))
            accepted = retained

        total_cost = sum(entry.server_cost for _, entry in accepted)
        total_output = sum(entry.output_bytes for _, entry in accepted)
        total_timeout = sum(entry.timeout_ms for _, entry in accepted)
        native_count = sum(entry.operation_kind == "native_read" for _, entry in accepted)
        over_budget = (
            len(accepted) > budget.remaining_operations
            or native_count > budget.remaining_native_reads
            or total_output > budget.remaining_output_bytes
            or total_cost > budget.remaining_cost
            or total_timeout > budget.remaining_timeout_ms
        )
        if over_budget:
            return self._reject(
                decision,
                PolicyDecision(
                    "wave_budget_exceeded",
                    "reject",
                    None,
                    {
                        "operation_count": len(accepted),
                        "native_read_count": native_count,
                        "output_bytes": total_output,
                        "server_cost": total_cost,
                        "timeout_ms": total_timeout,
                    },
                ),
            )
        if not accepted:
            return self._reject(
                decision,
                PolicyDecision(
                    "no_eligible_operation",
                    "reject",
                    None,
                    {"trimmed_count": len(policy_log)},
                ),
                preceding=policy_log,
            )
        for operation, entry in accepted:
            policy_log.append(
                PolicyDecision(
                    "operation_allowed",
                    "allow",
                    operation.action_id,
                    {
                        "operation_kind": entry.operation_kind,
                        "resource_key": entry.resource_key,
                        "server_cost": entry.server_cost,
                        "timeout_ms": entry.timeout_ms,
                        "output_bytes": entry.output_bytes,
                    },
                )
            )
        outcome = "trim" if any(value.outcome == "trim" for value in policy_log) else "allow"
        return EvaluatedDecision(
            candidate=decision,
            outcome=outcome,
            operations=tuple(operation for operation, _ in accepted),
            policy_decisions=tuple(policy_log),
            server_cost=total_cost,
            native_read_count=native_count,
            output_bytes=total_output,
        )

    def _counter_evidence_gate(self, decision: InvestigationDecision) -> PolicyDecision | None:
        refuted = {
            hypothesis_id
            for operation in decision.operations
            for hypothesis_id in operation.refutes_hypotheses
        }
        blocked = [
            item.hypothesis_id
            for item in decision.hypotheses
            if item.confirmation_requested
            and not item.counter_evidence_refs
            and not item.counter_evidence_unavailable
            and item.hypothesis_id not in refuted
        ]
        if not blocked:
            return None
        return PolicyDecision(
            "counter_evidence_required",
            "reject",
            None,
            {"hypothesis_ids": blocked},
        )

    def _operation_rejection(
        self,
        operation: PlannedOperation,
        entry: CapabilityEntry | None,
        *,
        hypothesis_ids: set[str],
    ) -> PolicyDecision | None:
        if entry is None:
            return PolicyDecision("unknown_action", "trim", operation.action_id, {})
        referenced = set(operation.supports_hypotheses) | set(operation.refutes_hypotheses)
        if not referenced <= hypothesis_ids:
            return PolicyDecision(
                "unknown_hypothesis",
                "trim",
                operation.action_id,
                {"hypothesis_ids": sorted(referenced - hypothesis_ids)},
            )
        if not set(operation.evidence_anchors) <= set(entry.evidence_anchors):
            return PolicyDecision(
                "irrelevant_evidence_anchor",
                "trim",
                operation.action_id,
                {"allowed": list(entry.evidence_anchors)},
            )
        if operation.depends_on:
            return PolicyDecision(
                "dependent_wave_operation",
                "trim",
                operation.action_id,
                {"depends_on": list(operation.depends_on)},
            )
        return None

    def _reject(
        self,
        candidate: InvestigationDecision,
        reason: PolicyDecision,
        *,
        preceding: Sequence[PolicyDecision] = (),
    ) -> EvaluatedDecision:
        return EvaluatedDecision(
            candidate=candidate,
            outcome="reject",
            operations=(),
            policy_decisions=tuple(preceding) + (reason,),
            server_cost=0.0,
            native_read_count=0,
            output_bytes=0,
        )


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain(child) for key, child in value.items()}
    if isinstance(value, tuple | list):
        return [_plain(child) for child in value]
    return value
