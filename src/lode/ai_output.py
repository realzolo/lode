"""Shared contract for the language used in analysis results."""

from __future__ import annotations

AI_OUTPUT_LANGUAGE_SETTING_KEY = "ai_output_language"
DEFAULT_AI_OUTPUT_LANGUAGE = "en"
SUPPORTED_AI_OUTPUT_LANGUAGES = ("en", "zh")

_LANGUAGE_NAMES = {
    "en": "English",
    "zh": "Simplified Chinese",
}


def normalize_ai_output_language(value: str | None) -> str:
    """Return a supported output language, falling back safely to English."""
    return value if value in SUPPORTED_AI_OUTPUT_LANGUAGES else DEFAULT_AI_OUTPUT_LANGUAGE


def ai_output_language_name(value: str | None) -> str:
    """Return the language name used in the LLM instruction."""
    return _LANGUAGE_NAMES[normalize_ai_output_language(value)]
