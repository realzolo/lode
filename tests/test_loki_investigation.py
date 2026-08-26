from __future__ import annotations

import pytest

from lode.engine.loki_investigation import build_lifecycle_logql, build_request_logql
from lode.integration_policy import IntegrationPolicyError, integration_kind, normalize_integration_config


def test_request_logql_is_server_generated_and_request_scoped() -> None:
    query = build_request_logql(
        service_name="payment-gateway", environment="prod",
        request_id="4bf92f35-77b3-4daa-b3ce-929d0e0e4736",
    )
    assert query == '{service_name="payment-gateway", environment="prod"} | json | request_id="4bf92f35-77b3-4daa-b3ce-929d0e0e4736"'


def test_request_logql_rejects_selector_injection() -> None:
    with pytest.raises(ValueError, match="invalid service_name"):
        build_request_logql(
            service_name='sonakit"} |= "secret', environment="prod",
            request_id="4bf92f35-77b3-4daa-b3ce-929d0e0e4736",
        )


def test_lifecycle_logql_only_accepts_allowlisted_keys() -> None:
    assert "job_id" in build_lifecycle_logql(
        service_name="sonakit", environment="prod",
        correlation_key="job_id", correlation_value="job-123",
    )
    with pytest.raises(ValueError, match="unsupported correlation key"):
        build_lifecycle_logql(
            service_name="sonakit", environment="prod",
            correlation_key="query", correlation_value="unsafe",
        )


def test_loki_is_a_replaceable_log_search_kind() -> None:
    definition = integration_kind("loki")
    assert "log_search" in definition.capabilities
    assert normalize_integration_config(
        "loki", {"base_url": "https://logs.example.com/", "limit": 250}
    ) == {"base_url": "https://logs.example.com", "limit": 250}
    with pytest.raises(IntegrationPolicyError):
        integration_kind("unknown-log-product")
