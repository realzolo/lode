"""Opaque value encryption tests."""

from __future__ import annotations

import pytest

from lode import crypto
from lode.config import settings


@pytest.mark.parametrize(
    "value",
    ["", "opaque", "  spaces  ", "引号'\"与中文", "{job=~\".*\"} | json"],
)
def test_opaque_evidence_value_round_trips_without_normalization(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setattr(settings, "data_encryption_key", "unit-test-evidence-key")
    monkeypatch.setattr(crypto, "_fernet", None)

    ciphertext = crypto.encrypt_value(value)

    assert ciphertext
    assert ciphertext != value
    assert crypto.decrypt_value(ciphertext) == value
