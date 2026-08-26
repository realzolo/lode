"""Non-production policy/adapter used to prove the authorization boundary."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Mapping

from lode.application.intake import canonical_hash
from lode.evidence_access.budget import intersect_budget
from lode.evidence_access.candidate import NativeReadCandidateInput, SearchPayload
from lode.evidence_access.types import (
    AccessContext, AccessRejection, BoundNativeAction, ParsedNativeAction, PolicyEvaluation,
)


def _walk(value: Any, path: tuple[str | int, ...] = ()):
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _walk(item, path + (key,))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _walk(item, path + (index,))
    else:
        yield path, value


def _shape(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _shape(item) for key, item in sorted(value.items())}
    if isinstance(value, list):
        return [_shape(item) for item in value]
    if value is None:
        return "null"
    return type(value).__name__


def _assign(value: Any, path: tuple[str | int, ...], replacement: str) -> None:
    target = value
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = replacement


@dataclass(frozen=True, slots=True)
class MockTreePolicy:
    """Exact JSON-value policy for kernel tests; never registered by the app."""

    language: str = "elasticsearch_query_dsl"
    parser_name: str = "mock-json-tree"
    parser_version: str = "1"
    policy_version: str = "1"

    def parse(self, candidate: NativeReadCandidateInput) -> ParsedNativeAction:
        if not isinstance(candidate.payload, SearchPayload):
            raise AccessRejection("invalid_syntax", "mock policy requires structured search payload")
        action = candidate.payload.model_dump(mode="json")
        slots: dict[str, tuple[str | int, ...]] = {}
        sentinel_keys = set(candidate.value_bindings)
        for path, value in _walk(action):
            if isinstance(value, str) and value in sentinel_keys:
                if value in slots:
                    raise AccessRejection("invalid_syntax", "sentinel appears in multiple value nodes")
                slots[value] = path
            elif isinstance(value, str) and any(sentinel in value for sentinel in sentinel_keys):
                raise AccessRejection("invalid_syntax", "sentinel must occupy a complete JSON value node")
        if set(slots) != sentinel_keys:
            raise AccessRejection("invalid_syntax", "every binding sentinel must occupy one value node")
        structural_hash = canonical_hash(_shape(action))
        return ParsedNativeAction(
            language=self.language,
            canonical_action=action,
            parse_tree_hash=canonical_hash(action),
            structural_hash=structural_hash,
            value_slots=slots,
            parser_name=self.parser_name,
            parser_version=self.parser_version,
        )

    def evaluate(
        self,
        action: ParsedNativeAction,
        candidate: NativeReadCandidateInput,
        context: AccessContext,
    ) -> PolicyEvaluation:
        allowed_paths = context.scope_config.get("allowed_paths", [])
        path = action.canonical_action.get("path")
        if path not in allowed_paths:
            raise AccessRejection("scope_violation", "search path is outside snapshot scope")
        budget, diff = intersect_budget(candidate, context)
        return PolicyEvaluation(
            effective_action=action.canonical_action,
            validation_decisions=(
                {"check": "complete_parse", "outcome": "allow"},
                {"check": "scope_intersection", "outcome": "allow", "path": path},
                {"check": "budget_intersection", "outcome": "allow"},
            ),
            constraint_diff=diff,
            effective_budget=budget,
        )

    def bind_values(
        self,
        action: ParsedNativeAction,
        evaluation: PolicyEvaluation,
        values: Mapping[str, str],
    ) -> BoundNativeAction:
        bound = deepcopy(dict(evaluation.effective_action))
        if set(values) != set(action.value_slots):
            raise AccessRejection("scope_violation", "resolved values do not match parsed slots")
        for sentinel, path in action.value_slots.items():
            _assign(bound, path, values[sentinel])
        structural_hash = canonical_hash(_shape(bound))
        if structural_hash != action.structural_hash:
            raise AccessRejection("invalid_syntax", "ValueRef binding changed action structure")
        return BoundNativeAction(
            language=action.language,
            canonical_action=bound,
            structural_hash=structural_hash,
            parse_tree_hash=canonical_hash(bound),
        )


class MockEvidenceAdapter:
    async def preflight(self, permit: Any) -> Mapping[str, Any]:
        permit.assert_valid()
        return {"status": "ok", "estimated_rows": 1}

    async def execute(self, permit: Any) -> Mapping[str, Any]:
        permit.assert_valid()
        return {"records": [{"value": "mock"}], "bytes": 18}
