"""Startup security-key validation tests."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from lode.config import Settings


def _settings(**overrides) -> Settings:
    values = {
        "secret_key": "a" * 32,
        "data_encryption_key": "b" * 32,
        "evidence_authorization_key": "c" * 32,
        "command_runner_key": "d" * 32,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_security_keys_must_be_independent() -> None:
    with pytest.raises(ValidationError, match="independent"):
        _settings(evidence_authorization_key="a" * 32)


@pytest.mark.parametrize(
    "field",
    [
        "secret_key",
        "data_encryption_key",
        "evidence_authorization_key",
        "command_runner_key",
    ],
)
def test_security_keys_reject_short_values(field: str) -> None:
    with pytest.raises(ValidationError, match="at least 32 bytes"):
        _settings(**{field: "short"})
