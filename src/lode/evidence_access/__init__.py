"""Fail-closed native evidence authorization boundary."""

from lode.evidence_access.candidate import NativeReadCandidateInput, parse_candidate_json
from lode.evidence_access.registry import NativeLanguagePolicy, NativePolicyRegistry
from lode.evidence_access.types import (
    AccessContext,
    AccessRejection,
    BoundNativeAction,
    EffectiveBudget,
    ParsedNativeAction,
    PolicyEvaluation,
)

__all__ = [
    "AccessContext",
    "AccessRejection",
    "BoundNativeAction",
    "EffectiveBudget",
    "NativeLanguagePolicy",
    "NativePolicyRegistry",
    "NativeReadCandidateInput",
    "ParsedNativeAction",
    "PolicyEvaluation",
    "parse_candidate_json",
]
