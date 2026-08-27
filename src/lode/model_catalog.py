"""Reviewed OpenAI model specifications used by account-model routing.

The Models API is an entitlement inventory, not a capacity API.  This module
therefore owns the immutable, reviewed runtime facts required for safe context
admission.  Additions require a code review, catalog tests, and a new catalog
revision; callers must never accept user-supplied capacity or tokenizer data.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping


CATALOG_REVISION = "providers-openai-anthropic-2026-08-27"


@dataclass(frozen=True, slots=True)
class OpenAIModelProfile:
    provider_kind: str
    model_id: str
    display_name: str
    context_window_tokens: int
    max_output_tokens: int
    tokenizer_encoding: str
    provider_safety_margin_tokens: int
    capabilities: Mapping[str, bool]
    protocol_ids: tuple[str, ...]
    catalog_revision: str = CATALOG_REVISION

    @property
    def tokenizer_id(self) -> str:
        return f"tiktoken:{self.tokenizer_encoding}"

    @property
    def profile_hash(self) -> str:
        payload = {
            "catalog_revision": self.catalog_revision,
            "capabilities": dict(self.capabilities),
            "context_window_tokens": self.context_window_tokens,
            "display_name": self.display_name,
            "max_output_tokens": self.max_output_tokens,
            "model_id": self.model_id,
            "provider_kind": self.provider_kind,
            "provider_safety_margin_tokens": self.provider_safety_margin_tokens,
            "tokenizer_encoding": self.tokenizer_encoding,
            "protocol_ids": self.protocol_ids,
        }
        encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
        return hashlib.sha256(encoded.encode()).hexdigest()


_GPT5_CAPABILITIES = MappingProxyType(
    {
        "chat_completions": True,
        "structured_outputs": True,
        "text_input": True,
        "text_output": True,
    }
)

_CLAUDE_CAPABILITIES = MappingProxyType(
    {
        "messages": True,
        "structured_outputs": True,
        "text_input": True,
        "text_output": True,
    }
)

# These are stable model IDs.  Do not add rolling aliases such as ``gpt-5.6``.
_PROFILES = (
    OpenAIModelProfile(
        provider_kind="openai",
        model_id="gpt-5.6-sol",
        display_name="GPT-5.6 Sol",
        context_window_tokens=1_050_000,
        max_output_tokens=128_000,
        tokenizer_encoding="o200k_base",
        provider_safety_margin_tokens=256,
        capabilities=_GPT5_CAPABILITIES,
        protocol_ids=("openai.responses.v1", "openai.chat_completions.v1"),
    ),
    OpenAIModelProfile(
        provider_kind="openai",
        model_id="gpt-5.6-terra",
        display_name="GPT-5.6 Terra",
        context_window_tokens=1_050_000,
        max_output_tokens=128_000,
        tokenizer_encoding="o200k_base",
        provider_safety_margin_tokens=256,
        capabilities=_GPT5_CAPABILITIES,
        protocol_ids=("openai.responses.v1", "openai.chat_completions.v1"),
    ),
    OpenAIModelProfile(
        provider_kind="openai",
        model_id="gpt-5.6-luna",
        display_name="GPT-5.6 Luna",
        context_window_tokens=1_050_000,
        max_output_tokens=128_000,
        tokenizer_encoding="o200k_base",
        provider_safety_margin_tokens=256,
        capabilities=_GPT5_CAPABILITIES,
        protocol_ids=("openai.responses.v1", "openai.chat_completions.v1"),
    ),
    OpenAIModelProfile(
        provider_kind="anthropic",
        model_id="claude-opus-4-1-20250805",
        display_name="Claude Opus 4.1",
        context_window_tokens=200_000,
        max_output_tokens=32_000,
        tokenizer_encoding="cl100k_base",
        provider_safety_margin_tokens=512,
        capabilities=_CLAUDE_CAPABILITIES,
        protocol_ids=("anthropic.messages.v1",),
    ),
    OpenAIModelProfile(
        provider_kind="anthropic",
        model_id="claude-sonnet-4-20250514",
        display_name="Claude Sonnet 4",
        context_window_tokens=200_000,
        max_output_tokens=64_000,
        tokenizer_encoding="cl100k_base",
        provider_safety_margin_tokens=512,
        capabilities=_CLAUDE_CAPABILITIES,
        protocol_ids=("anthropic.messages.v1",),
    ),
)
_BY_ID = {(profile.provider_kind, profile.model_id): profile for profile in _PROFILES}


def supported_models(provider_kind: str, protocol_id: str) -> tuple[OpenAIModelProfile, ...]:
    return tuple(
        profile
        for profile in _PROFILES
        if profile.provider_kind == provider_kind and protocol_id in profile.protocol_ids
    )


def find_model(provider_kind: str, protocol_id: str, model_id: str) -> OpenAIModelProfile | None:
    profile = _BY_ID.get((provider_kind, model_id))
    return profile if profile is not None and protocol_id in profile.protocol_ids else None


def require_model(provider_kind: str, protocol_id: str, model_id: str) -> OpenAIModelProfile:
    profile = find_model(provider_kind, protocol_id, model_id)
    if profile is None:
        raise ValueError("model is not in the supported provider protocol catalog")
    return profile


def supported_tokenizer_ids() -> frozenset[str]:
    return frozenset(profile.tokenizer_id for profile in _PROFILES)
