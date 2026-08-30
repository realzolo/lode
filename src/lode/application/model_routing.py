"""Deterministic server-side model selection over frozen binding snapshots."""

from __future__ import annotations

from collections.abc import Sequence

from lode.domain.model_execution import (
    ModelCandidate,
    ModelTask,
    RouteExclusion,
    SelectedModelRoute,
)
from lode.domain.types import ExecutionClass, ModelRole


class ModelCapabilityUnavailable(RuntimeError):
    def __init__(self, exclusions: Sequence[RouteExclusion]) -> None:
        super().__init__("no frozen model binding satisfies the task")
        self.exclusions = tuple(exclusions)


class ModelSelectionPolicyEngine:
    def select(
        self,
        task: ModelTask,
        candidates: Sequence[ModelCandidate],
        *,
        remaining_calls: int,
        remaining_cost: float,
        verifier_separate_account_model: bool = False,
        verifier_separate_provider: bool = False,
    ) -> SelectedModelRoute:
        required_class = self.required_execution_class(task)
        exclusions: list[RouteExclusion] = []
        eligible: list[tuple[ModelCandidate, int, int]] = []
        for candidate in sorted(candidates, key=lambda item: item.binding_snapshot_id):
            code = self._exclusion(
                task,
                candidate,
                required_class=required_class,
                remaining_calls=remaining_calls,
                remaining_cost=remaining_cost,
                verifier_separate_account_model=verifier_separate_account_model,
                verifier_separate_provider=verifier_separate_provider,
            )
            allowed_output = min(candidate.max_output_tokens, task.reserved_output_tokens)
            allowed_input = int(
                min(
                    candidate.context_window_tokens
                    - allowed_output
                    - candidate.provider_safety_margin_tokens,
                    candidate.context_window_tokens * candidate.max_context_utilization,
                )
            )
            if code is None and task.required_context_tokens > allowed_input:
                code = "context_capacity_exceeded"
            if code is not None:
                detail = {"required_execution_class": required_class.value}
                if code == "data_class_not_allowed":
                    detail.update(
                        {
                            "requested_data_class": task.data_class,
                            "allowed_data_classes": sorted(candidate.allowed_data_classes),
                        }
                    )
                exclusions.append(
                    RouteExclusion(
                        candidate.binding_snapshot_id,
                        code,
                        detail,
                    )
                )
                continue
            eligible.append((candidate, allowed_input, allowed_output))
        if not eligible:
            raise ModelCapabilityUnavailable(exclusions)
        selected, allowed_input, allowed_output = min(
            eligible,
            key=lambda item: (
                item[0].priority,
                item[0].predicted_cost,
                -item[0].quality_score,
                item[0].binding_snapshot_id,
            ),
        )
        return SelectedModelRoute(
            candidate=selected,
            execution_class=required_class,
            required_context_tokens=task.required_context_tokens,
            allowed_input_tokens=allowed_input,
            allowed_output_tokens=allowed_output,
            selection_reason=(
                "server complexity requires reasoning capacity"
                if required_class is ExecutionClass.REASONING_OPTIMIZED
                else "server complexity permits the lowest-cost latency route"
            ),
            exclusions=tuple(exclusions),
            budget={
                "remaining_calls": remaining_calls,
                "remaining_cost": remaining_cost,
                "predicted_cost": selected.predicted_cost,
            },
        )

    @staticmethod
    def required_execution_class(task: ModelTask) -> ExecutionClass:
        complex_task = (
            task.contradiction_count > 0
            or task.component_count > 1
            or task.repository_count > 1
            or task.causal_depth >= 3
            or task.conclusion_risk in {"medium", "high"}
            or task.role in {ModelRole.SYNTHESIZER, ModelRole.VERIFIER}
        )
        return (
            ExecutionClass.REASONING_OPTIMIZED if complex_task else ExecutionClass.LATENCY_OPTIMIZED
        )

    @staticmethod
    def _exclusion(
        task: ModelTask,
        candidate: ModelCandidate,
        *,
        required_class: ExecutionClass,
        remaining_calls: int,
        remaining_cost: float,
        verifier_separate_account_model: bool,
        verifier_separate_provider: bool,
    ) -> str | None:
        if candidate.health_status != "healthy":
            return "model_unhealthy"
        if task.role not in candidate.allowed_roles:
            return "role_not_allowed"
        if required_class not in candidate.execution_classes:
            return "execution_class_unavailable"
        if task.data_class not in candidate.allowed_data_classes:
            return "data_class_not_allowed"
        if remaining_calls < 1:
            return "model_call_budget_exhausted"
        if candidate.used_calls >= candidate.max_calls:
            return "model_binding_call_budget_exhausted"
        if candidate.predicted_cost > min(remaining_cost, candidate.max_cost_per_call):
            return "model_cost_budget_exceeded"
        if (
            task.role is ModelRole.VERIFIER
            and verifier_separate_account_model
            and candidate.provider_account_model_id == task.prior_synthesizer_account_model_id
        ):
            return "verifier_account_model_not_independent"
        if (
            task.role is ModelRole.VERIFIER
            and verifier_separate_provider
            and candidate.provider_account_id == task.prior_synthesizer_provider_id
        ):
            return "verifier_provider_not_independent"
        return None
