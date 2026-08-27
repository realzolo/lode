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


CATALOG_REVISION = "openai-gpt5-2026-08-27"


@dataclass(frozen=True, slots=True)
class OpenAIModelProfile:
    model_id: str
    display_name: str
    context_window_tokens: int
    max_output_tokens: int
    tokenizer_encoding: str
    provider_safety_margin_tokens: int
    capabilities: Mapping[str, bool]
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
            "provider_safety_margin_tokens": self.provider_safety_margin_tokens,
            "tokenizer_encoding": self.tokenizer_encoding,
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

# These are stable model IDs.  Do not add rolling aliases such as ``gpt-5.6``.
_PROFILES = (
    OpenAIModelProfile(
        model_id="gpt-5.6-sol",
        display_name="GPT-5.6 Sol",
        context_window_tokens=1_050_000,
        max_output_tokens=128_000,
        tokenizer_encoding="o200k_base",
        provider_safety_margin_tokens=256,
        capabilities=_GPT5_CAPABILITIES,
    ),
    OpenAIModelProfile(
        model_id="gpt-5.6-terra",
        display_name="GPT-5.6 Terra",
        context_window_tokens=1_050_000,
        max_output_tokens=128_000,
        tokenizer_encoding="o200k_base",
        provider_safety_margin_tokens=256,
        capabilities=_GPT5_CAPABILITIES,
    ),
    OpenAIModelProfile(
        model_id="gpt-5.6-luna",
        display_name="GPT-5.6 Luna",
        context_window_tokens=1_050_000,
        max_output_tokens=128_000,
        tokenizer_encoding="o200k_base",
        provider_safety_margin_tokens=256,
        capabilities=_GPT5_CAPABILITIES,
    ),
)
_BY_ID = {profile.model_id: profile for profile in _PROFILES}


def supported_openai_models() -> tuple[OpenAIModelProfile, ...]:
    return _PROFILES


def find_openai_model(model_id: str) -> OpenAIModelProfile | None:
    return _BY_ID.get(model_id)


def require_openai_model(model_id: str) -> OpenAIModelProfile:
    profile = find_openai_model(model_id)
    if profile is None:
        raise ValueError("model is not in the supported OpenAI catalog")
    return profile


def supported_tokenizer_ids() -> frozenset[str]:
    return frozenset(profile.tokenizer_id for profile in _PROFILES)
