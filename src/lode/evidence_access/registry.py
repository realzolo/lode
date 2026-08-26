"""Explicit parser/policy registry; missing languages remain disabled."""

from __future__ import annotations

from typing import Mapping, Protocol

from lode.evidence_access.candidate import NativeReadCandidateInput
from lode.evidence_access.types import (
    AccessContext,
    BoundNativeAction,
    ParsedNativeAction,
    PolicyEvaluation,
)


class NativeLanguagePolicy(Protocol):
    language: str
    parser_name: str
    parser_version: str
    policy_version: str

    def parse(self, candidate: NativeReadCandidateInput) -> ParsedNativeAction: ...

    def evaluate(
        self,
        action: ParsedNativeAction,
        candidate: NativeReadCandidateInput,
        context: AccessContext,
    ) -> PolicyEvaluation: ...

    def bind_values(
        self,
        action: ParsedNativeAction,
        evaluation: PolicyEvaluation,
        values: Mapping[str, str],
    ) -> BoundNativeAction: ...


class NativePolicyRegistry:
    def __init__(self) -> None:
        self._policies: dict[str, NativeLanguagePolicy] = {}

    def register(self, policy: NativeLanguagePolicy) -> None:
        if policy.language in self._policies:
            raise ValueError(f"policy already registered for {policy.language}")
        for value in (
            policy.language,
            policy.parser_name,
            policy.parser_version,
            policy.policy_version,
        ):
            if not value or value != value.strip():
                raise ValueError("policy metadata must be nonblank and trimmed")
        self._policies[policy.language] = policy

    def require(self, language: str) -> NativeLanguagePolicy:
        from lode.evidence_access.types import AccessRejection

        policy = self._policies.get(language)
        if policy is None:
            raise AccessRejection(
                "unsupported_node",
                "no complete parser and policy is active for this language",
                {"language": language},
            )
        return policy

    @property
    def capabilities(self) -> Mapping[str, Mapping[str, str]]:
        return {
            language: {
                "parser_name": policy.parser_name,
                "parser_version": policy.parser_version,
                "policy_version": policy.policy_version,
            }
            for language, policy in sorted(self._policies.items())
        }
