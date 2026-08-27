"""Strict contract for the language used in investigation output."""

from __future__ import annotations

SUPPORTED_AI_OUTPUT_LANGUAGES = ("en", "zh")

_LANGUAGE_NAMES = {
    "en": "English",
    "zh": "Simplified Chinese",
}


def require_ai_output_language(value: str) -> str:
    """Validate a persisted language without silently changing historical output."""
    if value not in SUPPORTED_AI_OUTPUT_LANGUAGES:
        raise ValueError(f"unsupported AI output language: {value}")
    return value


def ai_output_language_name(value: str) -> str:
    """Return the language name used in the LLM instruction."""
    return _LANGUAGE_NAMES[require_ai_output_language(value)]


def ai_output_language_instruction(value: str) -> str:
    """Give every model role the same immutable language constraint."""
    language = ai_output_language_name(value)
    return (
        f"Write every human-readable output value in {language}. "
        "Keep JSON object keys, schema enum values, IDs, hashes, timestamps, "
        "file paths, code, and quoted evidence exactly as supplied."
    )
