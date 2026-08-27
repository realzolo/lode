"""Role-isolated, token-exact context assembly."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Protocol

from lode.domain.model_execution import AssembledContext, ContextEvidence
from lode.domain.types import ModelRole

_HIDDEN_KEYS = frozenset(
    {
        "chain_of_thought",
        "hidden_reasoning",
        "model_session",
        "provider_cache",
        "raw_model_output",
        "scratchpad",
    }
)


class ContextCapacityExceeded(RuntimeError):
    pass


class Tokenizer(Protocol):
    @property
    def tokenizer_id(self) -> str: ...

    def count_json(self, value: Any) -> int: ...


class ContextManager:
    """Build immutable context without carrying model-private state across roles."""

    def build(
        self,
        *,
        role: ModelRole,
        state_packet: Mapping[str, Any],
        evidence: Sequence[ContextEvidence],
        tokenizer: Tokenizer,
        allowed_input_tokens: int,
        reserved_output_tokens: int,
        provider_safety_margin_tokens: int,
        summary_refs: Sequence[int] = (),
        request_token_count: Callable[[Mapping[str, Any]], int] | None = None,
    ) -> AssembledContext:
        safe_state = _scrub(state_packet)
        if not isinstance(safe_state, dict):
            raise TypeError("state packet must be an object")
        capacity = allowed_input_tokens

        def _payload(items: Sequence[ContextEvidence]) -> dict[str, Any]:
            return {
                "role": role.value,
                "state_packet": safe_state,
                "evidence": [
                    {"artifact_id": item.artifact_id, "content": _plain(item.content)}
                    for item in items
                ],
                "summary_refs": list(summary_refs),
                "tokenizer_id": tokenizer.tokenizer_id,
            }

        def _count(items: Sequence[ContextEvidence]) -> int:
            assert request_token_count is not None
            return request_token_count(_payload(items))

        state_tokens = _count(()) if request_token_count is not None else tokenizer.count_json(safe_state)
        if state_tokens > capacity:
            raise ContextCapacityExceeded("pinned investigation state exceeds model capacity")
        unique: dict[int, ContextEvidence] = {}
        for item in evidence:
            previous = unique.get(item.artifact_id)
            if previous is not None and previous.content != item.content:
                raise ValueError("the same evidence ref has conflicting content")
            unique[item.artifact_id] = item
        pinned = sorted(
            (item for item in unique.values() if item.pinned), key=lambda item: item.artifact_id
        )
        optional = sorted(
            (item for item in unique.values() if not item.pinned),
            key=lambda item: (-item.counter_evidence, -item.relevance, item.artifact_id),
        )
        selected: list[ContextEvidence] = []
        used = state_tokens
        for item in (*pinned, *optional):
            exact = tokenizer.count_json(_plain(item.content))
            if exact != item.token_count:
                raise ValueError("evidence token count does not match the selected tokenizer")
            next_selected = [*selected, item]
            next_used = (
                _count(next_selected)
                if request_token_count is not None
                else used + exact
            )
            if next_used > capacity:
                if item.pinned:
                    raise ContextCapacityExceeded("pinned evidence exceeds model capacity")
                continue
            selected.append(item)
            used = next_used
        payload = _payload(selected)
        context_hash = hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
        ).hexdigest()
        return AssembledContext(
            role=role,
            state_packet=safe_state,
            evidence=tuple(selected),
            summary_refs=tuple(summary_refs),
            token_count=used,
            reserved_output_tokens=reserved_output_tokens,
            provider_safety_margin_tokens=provider_safety_margin_tokens,
            tokenizer_id=tokenizer.tokenizer_id,
            context_hash=context_hash,
        )


class ExactJSONTokenizer:
    """Deterministic tokenizer for mock/offline deployments only."""

    tokenizer_id = "exact-json-bytes"

    def count_json(self, value: Any) -> int:
        return len(
            json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
        )


class TiktokenJSONTokenizer:
    """Deterministically count the compact JSON sent to OpenAI-compatible APIs."""

    def __init__(self, encoding_name: str) -> None:
        import tiktoken

        self._encoding = tiktoken.get_encoding(encoding_name)
        self.tokenizer_id = f"tiktoken:{encoding_name}"

    def count_json(self, value: Any) -> int:
        serialized = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        return len(self._encoding.encode(serialized, disallowed_special=()))


def _scrub(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _scrub(child) for key, child in value.items() if str(key) not in _HIDDEN_KEYS
        }
    if isinstance(value, tuple | list):
        return [_scrub(child) for child in value]
    if value is None or isinstance(value, str | int | float | bool):
        return value
    raise TypeError(f"context value is not JSON-compatible: {type(value).__name__}")


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain(child) for key, child in value.items()}
    if isinstance(value, tuple | list):
        return [_plain(child) for child in value]
    return value
