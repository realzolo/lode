from __future__ import annotations

from lode.model_catalog import CATALOG_REVISION, find_model, require_model


def test_catalog_accepts_only_reviewed_fixed_openai_model_ids() -> None:
    profile = require_model("openai", "openai.responses.v1", "gpt-5.6-sol")

    assert profile.catalog_revision == CATALOG_REVISION
    assert profile.context_window_tokens == 1_050_000
    assert profile.max_output_tokens == 128_000
    assert profile.tokenizer_id == "tiktoken:o200k_base"
    assert len(profile.profile_hash) == 64
    assert find_model("openai", "openai.responses.v1", "gpt-5.6") is None
    assert find_model("openai", "anthropic.messages.v1", "gpt-5.6-sol") is None
    anthropic = require_model("anthropic", "anthropic.messages.v1", "claude-sonnet-5")
    assert anthropic.display_name == "Claude Sonnet 5"
    assert anthropic.token_counting_strategy == "anthropic_count_tokens"
    assert anthropic.tokenizer_encoding is None
    assert anthropic.capabilities["tool_calling"] is True
