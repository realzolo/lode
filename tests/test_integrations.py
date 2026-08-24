"""Safety tests for capability-limited service collectors."""

from __future__ import annotations

import pytest

from lode.crypto import encrypt_secret
from lode.api.schemas import ApplicationIntegrationIn
from lode.api.routes.applications import _integration_out
from lode.db.models.integration import ApplicationIntegration
from lode.db.models.intake import AuditEvent, EvidenceArtifact
from lode.engine import integrations
from lode.engine.integrations import (
    IntegrationError,
    ReadOnlyVerificationError,
    RedisStatusClient,
    KafkaConnector,
    Snapshot,
    _verify_clickhouse_grants,
    collect_service_evidence,
)
from lode.integration_policy import IntegrationPolicyError, normalize_integration_config


class _Result:
    def __init__(self, rows):
        self.rows = rows

    def scalars(self):
        return self

    def all(self):
        return self.rows


class _Session:
    def __init__(self, rows):
        self.rows = rows
        self.added = []

    async def execute(self, _statement):
        return _Result(self.rows)

    def add(self, item):
        self.added.append(item)

    def add_all(self, items):
        self.added.extend(items)

    async def flush(self):
        for index, item in enumerate(self.added, start=1):
            if isinstance(item, EvidenceArtifact) and item.id is None:
                item.id = index


class _FixedReadConnector:
    async def verify_readonly(self, _config, _credential):
        return None

    async def collect_snapshot(self, _config, _credential):
        return Snapshot("redis://status", "Redis inspected", {"password": "must-mask", "role": "replica"})


class _PermissionDriftConnector:
    async def verify_readonly(self, _config, _credential):
        raise ReadOnlyVerificationError("effective write grant")

    async def collect_snapshot(self, _config, _credential):  # pragma: no cover
        raise AssertionError("collection must not run after a policy failure")


class _UnavailableConnector:
    async def verify_readonly(self, _config, _credential):
        raise IntegrationError("network unavailable")

    async def collect_snapshot(self, _config, _credential):  # pragma: no cover
        raise AssertionError("collection must not run")


def _integration(kind: str = "redis") -> ApplicationIntegration:
    config = {
        "host": "redis.internal", "port": 6380, "tls": True,
        "username": "ops", "database": 0,
    }
    return ApplicationIntegration(
        id=7, application_id=3, name="cache", kind=kind, config=config,
        secret_ref=encrypt_secret("credential") or "", state="active",
    )


def test_config_policy_rejects_urls_ips_unknown_options_and_plaintext_kafka() -> None:
    with pytest.raises(IntegrationPolicyError):
        normalize_integration_config("redis", {"host": "redis://bad", "tls": True})
    with pytest.raises(IntegrationPolicyError):
        normalize_integration_config("redis", {"host": "10.0.0.4", "tls": True})
    with pytest.raises(Exception):
        normalize_integration_config("redis", {"host": "cache.internal", "tls": True, "password": "x"})
    with pytest.raises(Exception):
        normalize_integration_config("kafka", {"bootstrap_servers": ["broker.internal:9093"], "username": "ops", "security_protocol": "PLAINTEXT"})


def test_clickhouse_grant_policy_is_an_allowlist() -> None:
    _verify_clickhouse_grants(["GRANT SELECT ON app.*", "GRANT SELECT ON system.replicas"], "app")
    _verify_clickhouse_grants(["GRANT SELECT ON app.* TO analyst", "GRANT SELECT ON system.replicas TO analyst"], "app")
    for grant in ("GRANT TRUNCATE ON app.*", "GRANT SELECT ON *.*", "GRANT SELECT ON app.* WITH GRANT OPTION"):
        with pytest.raises(ReadOnlyVerificationError):
            _verify_clickhouse_grants([grant], "app")


def test_redis_status_facade_has_no_raw_command_escape_hatch() -> None:
    assert not hasattr(RedisStatusClient(), "execute_command")
    assert not hasattr(KafkaConnector(), "_admin")


def test_secret_free_schema_and_reader_projection() -> None:
    with pytest.raises(Exception):
        ApplicationIntegrationIn(
            name="cache", kind="redis", secret_ref="secret",
            config={"host": "cache.internal", "tls": True, "password": "leak"},
        )
    projection = _integration_out(_integration())
    assert "config" not in projection.model_dump()


@pytest.mark.asyncio
async def test_service_snapshot_is_redacted_timed_and_persisted(monkeypatch) -> None:
    row = _integration()
    session = _Session([row])
    monkeypatch.setattr(integrations, "connector_for", lambda _kind: _FixedReadConnector())

    result = await collect_service_evidence(session, 3, 42)

    assert len(result) == 1
    artifact = next(item for item in session.added if isinstance(item, EvidenceArtifact))
    assert "must-mask" not in (artifact.redacted_excerpt or "")
    assert artifact.metadata_["permission_verification"] == "passed"
    assert artifact.metadata_["time_scope"] == "analysis_time_observation"
    assert artifact.metadata_["observed_started_at"] <= artifact.metadata_["observed_finished_at"]


@pytest.mark.asyncio
async def test_permission_drift_disables_only_affected_binding(monkeypatch) -> None:
    row = _integration()
    session = _Session([row])
    monkeypatch.setattr(integrations, "connector_for", lambda _kind: _PermissionDriftConnector())

    assert await collect_service_evidence(session, 3, 42) == []
    assert row.state == "disabled"
    assert any(isinstance(item, AuditEvent) and item.action == "integration.disable" for item in session.added)


@pytest.mark.asyncio
async def test_transient_collection_failure_keeps_binding_active(monkeypatch) -> None:
    row = _integration()
    session = _Session([row])
    monkeypatch.setattr(integrations, "connector_for", lambda _kind: _UnavailableConnector())

    assert await collect_service_evidence(session, 3, 42) == []
    assert row.state == "active"
    assert "collection unavailable" in (row.last_error or "")
