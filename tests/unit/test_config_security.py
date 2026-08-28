"""Startup security-key validation tests."""

from __future__ import annotations

import ssl

import pytest
from pydantic import ValidationError

from lode.config import Settings, kafka_security_kwargs


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


def test_authenticated_kafka_supports_plaintext_compatibility_mode() -> None:
    configured = _settings(
        kafka_security_protocol="SASL_PLAINTEXT",
        kafka_sasl_username="consumer",
        kafka_sasl_password="secret",
    )

    kwargs = kafka_security_kwargs(configured)

    assert kwargs == {
        "security_protocol": "SASL_PLAINTEXT",
        "sasl_mechanism": "PLAIN",
        "sasl_plain_username": "consumer",
        "sasl_plain_password": "secret",
    }


def test_authenticated_kafka_builds_verified_tls_context() -> None:
    configured = _settings(
        kafka_security_protocol="SASL_SSL",
        kafka_sasl_username="consumer",
        kafka_sasl_password="secret",
    )

    kwargs = kafka_security_kwargs(configured)

    assert kwargs["security_protocol"] == "SASL_SSL"
    assert kwargs["sasl_plain_username"] == "consumer"
    assert kwargs["ssl_context"].check_hostname is True
    assert kwargs["ssl_context"].verify_mode == ssl.CERT_REQUIRED


def test_kafka_credentials_require_a_complete_sasl_configuration() -> None:
    with pytest.raises(ValidationError, match="both username and password"):
        _settings(kafka_security_protocol="SASL_SSL", kafka_sasl_username="consumer")
    with pytest.raises(ValidationError, match="require a SASL"):
        _settings(kafka_sasl_username="consumer", kafka_sasl_password="secret")
