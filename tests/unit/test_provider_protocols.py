from __future__ import annotations

from lode.engine.llm import model_endpoint


def test_model_protocols_select_explicit_paths() -> None:
    assert model_endpoint("openai.responses.v1", "https://gateway.example") == "https://gateway.example/v1/responses"
    assert model_endpoint("openai.chat_completions.v1", "https://gateway.example/v1") == "https://gateway.example/v1/chat/completions"
    assert model_endpoint("anthropic.messages.v1", "https://gateway.example") == "https://gateway.example/v1/messages"
