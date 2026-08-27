"""Startup security-key validation tests."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from lode.config import Settings


def _settings(**overrides) -> Settings:
    values = {
        "master_key": "a" * 32,
        "command_runner_key": "d" * 32,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_master_key_derives_distinct_security_keys() -> None:
    configured = _settings()
    assert len({
        configured.jwt_signing_key,
        configured.data_encryption_key,
        configured.evidence_authorization_key,
    }) == 3


def test_master_key_and_runner_key_must_be_independent() -> None:
    with pytest.raises(ValidationError, match="independent"):
        _settings(command_runner_key="a" * 32)


@pytest.mark.parametrize(
    "field",
    [
        "master_key",
        "command_runner_key",
    ],
)
def test_security_keys_reject_short_values(field: str) -> None:
    with pytest.raises(ValidationError, match="at least 32 bytes"):
        _settings(**{field: "short"})
