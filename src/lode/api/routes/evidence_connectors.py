"""Evidence Connector control-plane routes and typed Provider catalog assembly."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from lode.api.control_schemas import ConnectorCreate, ConnectorOut
from lode.api.deps import require_user
from lode.api.routes.control_plane import _audit, _error, _workspace_access, get_session
from lode.api.types import EntityId
from lode.crypto import decrypt_secret, encrypt_secret
from lode.db.models import AuditEvent, EvidenceAccessScope, EvidenceConnector
from lode.domain.evidence_budget import standard_execution_budget_policy
from lode.evidence_access.loki_scope import normalize_loki_filter
from lode.evidence_connectors.registry import (
    create_evidence_connector,
    native_connector_capabilities,
)
from lode.evidence_connectors.types import IntrospectionBudget, ProviderExecutionError

router = APIRouter(tags=["evidence-connectors"])

_CONNECTOR_SECRET_FIELDS = {
    "loki": ["bearer_token"],
    "elasticsearch": ["api_key", "bearer_token", "username", "password"],
    "opensearch": ["api_key", "bearer_token", "username", "password"],
    "postgresql": ["password"],
    "mysql": ["password"],
    "clickhouse": ["password"],
    "https": ["api_key", "bearer_token", "username", "password"],
    "prometheus": ["api_key", "bearer_token", "username", "password"],
    "tempo": ["api_key", "bearer_token", "username", "password"],
    "jaeger": ["api_key", "bearer_token", "username", "password"],
    "kubernetes": ["api_key", "bearer_token", "username", "password"],
    "github": ["api_key", "bearer_token", "username", "password"],
    "gitlab": ["api_key", "bearer_token", "username", "password"],
    "argocd": ["api_key", "bearer_token", "username", "password"],
}


def _connector_secret_map(row: EvidenceConnector) -> dict[str, str]:
    plaintext = decrypt_secret(row.secret_ciphertext)
    try:
        value = json.loads(plaintext or "", object_pairs_hook=_unique_pairs)
    except (json.JSONDecodeError, DuplicateKey) as exc:
        raise _error(
            500, "connector_secret_invalid", "Stored connector secret is invalid."
        ) from exc
    if not isinstance(value, dict) or any(not isinstance(item, str) for item in value.values()):
        raise _error(500, "connector_secret_invalid", "Stored connector secret is invalid.")
    return value


class DuplicateKey(ValueError):
    pass


def _unique_pairs(values):
    result = {}
    for key, value in values:
        if key in result:
            raise DuplicateKey(key)
        result[key] = value
    return result


def _connector_out(row: EvidenceConnector) -> ConnectorOut:
    public_config = {key: value for key, value in row.config.items() if key != "ca_certificate_pem"}
    return ConnectorOut(
        id=row.id,
        workspace_id=row.workspace_id,
        name=row.name,
        kind=row.kind,
        kind_version=row.kind_version,
        config=public_config,
        instance_revision=row.instance_revision,
        state=row.state,
        verification_status=row.verification_status,
        verified_at=row.verified_at,
        last_error=row.last_error,
        capabilities=row.capabilities,
        last_introspected_at=row.last_introspected_at,
        configured_secret_fields=sorted(_connector_secret_map(row)),
    )


@router.get("/evidence-connector-kinds")
async def connector_kinds(_: int = Depends(require_user)):
    capabilities = native_connector_capabilities()
    return [
        {
            "kind": kind,
            "version": 1,
            "language": value["language"],
            "capabilities": list(value["read_capabilities"]),
            "secret_fields": _CONNECTOR_SECRET_FIELDS[kind],
        }
        for kind, value in sorted(capabilities.items())
    ]


@router.get("/workspaces/{workspace_id}/evidence-connectors", response_model=list[ConnectorOut])
async def list_connectors(
    workspace_id: EntityId,
    user_id: EntityId = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    await _workspace_access(session, user_id, workspace_id, "read")
    rows = (
        await session.execute(
            select(EvidenceConnector)
            .where(EvidenceConnector.workspace_id == workspace_id)
            .order_by(EvidenceConnector.name, EvidenceConnector.id)
        )
    ).scalars()
    return [_connector_out(row) for row in rows]


def _connector_capability(kind: str):
    value = native_connector_capabilities()[kind]
    language = value["language"]
    return getattr(language, "value", str(language)), list(value["read_capabilities"])


def _connector_secrets(payload: ConnectorCreate) -> dict[str, str]:
    if payload.authentication == "bearer_token":
        return {"bearer_token": payload.credential or ""}
    if payload.authentication == "api_key":
        return {"api_key": payload.credential or ""}
    if payload.authentication == "basic":
        return {"username": payload.credential_username or "", "password": payload.credential or ""}
    return {}


def _connector_storage(payload: ConnectorCreate) -> tuple[dict, dict[str, str], dict, dict]:
    budget = standard_execution_budget_policy()
    if payload.kind == "loki":
        if payload.root_filter is None:
            raise ValueError("Loki root filter is required")
        root_filter = payload.root_filter.model_dump()
        branches = normalize_loki_filter(root_filter)
        return (
            {
                "base_url": payload.endpoint,
                **({"tenant_id": payload.tenant_id} if payload.tenant_id else {}),
            },
            {"bearer_token": payload.credential}
            if payload.authentication == "bearer_token"
            else {},
            {
                "root_filter": root_filter,
                "root_filter_dnf": [[dict(item) for item in branch] for branch in branches],
            },
            budget,
        )
    if payload.kind in {"elasticsearch", "opensearch"}:
        indices = list(payload.allowed_indices)
        return (
            {"base_url": payload.endpoint},
            _connector_secrets(payload),
            {"allowed_indices": indices, "cardinality_bounds": {}},
            budget,
        )
    if payload.kind == "https":
        parsed = urlsplit(payload.endpoint or "")
        try:
            default_port = 80 if parsed.scheme == "http" else 443
            port = parsed.port or default_port
        except ValueError as exc:
            raise ValueError("HTTP connector port is invalid") from exc
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("HTTP connector endpoint must be an HTTP or HTTPS origin")
        return (
            {
                "base_url": payload.endpoint,
                "verification_path": payload.verification_path or "/health",
            },
            _connector_secrets(payload),
            {
                "safe_read_endpoints": [
                    {
                        "id": "default-read",
                        "method": "GET",
                        "scheme": parsed.scheme,
                        "host": parsed.hostname.lower(),
                        "port": port,
                        "path_template": payload.safe_read_path,
                        "path_parameters": {},
                        "query_parameters": {},
                        "allowed_content_types": ["application/json"],
                        "max_response_bytes": 1_000_000,
                    }
                ]
            },
            budget,
        )
    if payload.kind in {
        "prometheus",
        "tempo",
        "jaeger",
        "kubernetes",
        "github",
        "gitlab",
        "argocd",
    }:
        parsed = urlsplit(payload.endpoint)
        try:
            default_port = 80 if parsed.scheme == "http" else 443
            port = parsed.port or default_port
        except ValueError as exc:
            raise ValueError("Provider connector port is invalid") from exc
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("Provider connector endpoint must be an HTTP or HTTPS origin")
        verification_path, endpoints = _typed_provider_endpoints(payload, parsed, port)
        return (
            {"base_url": payload.endpoint, "verification_path": verification_path},
            _connector_secrets(payload),
            {"safe_read_endpoints": endpoints},
            budget,
        )
    if payload.kind in {"postgresql", "mysql", "clickhouse"}:
        allowed_schemas = (
            {"allowed_schemas": list(payload.allowed_schemas)}
            if payload.kind == "postgresql"
            else {}
        )
        default_port = {
            "postgresql": 5432,
            "mysql": 3306,
            "clickhouse": 8123 if payload.tls_mode == "disabled" else 8443,
        }[payload.kind]
        dialect = {
            "postgresql": "postgres",
            "mysql": "mysql",
            "clickhouse": "clickhouse",
        }[payload.kind]
        return (
            {
                "host": payload.host,
                "port": payload.port or default_port,
                "database": payload.database,
                "username": payload.database_username,
                "tls_mode": payload.tls_mode,
                **(
                    {"ca_certificate_pem": payload.ca_certificate_pem}
                    if payload.ca_certificate_pem is not None
                    else {}
                ),
            },
            {"password": payload.database_password or ""},
            {
                "dialect": dialect,
                **allowed_schemas,
                "allowed_tables": [],
                "table_policies": {},
            },
            budget,
        )
    raise ValueError("unsupported connector kind")


def _typed_provider_endpoints(payload: ConnectorCreate, parsed, port: int) -> tuple[str, list[dict]]:
    def endpoint(endpoint_id: str, path: str, query: dict[str, dict] | None = None) -> dict:
        return {
            "id": endpoint_id,
            "method": "GET",
            "scheme": parsed.scheme,
            "host": parsed.hostname.lower(),
            "port": port,
            "path_template": path,
            "path_parameters": {},
            "query_parameters": query or {},
            "allowed_content_types": ["application/json"],
            "max_response_bytes": 2 * 1024 * 1024,
        }

    text_query = {"type": "string", "required": True, "max_length": 4_000}
    optional_text = {"type": "string", "required": False, "max_length": 1_000}
    result_limit = {"type": "integer", "source": "result_limit"}
    if payload.kind == "prometheus":
        return "/-/ready", [
            endpoint(
                "prometheus.query_range",
                "/api/v1/query_range",
                {
                    "query": text_query,
                    "start": {"type": "string", "source": "window_start"},
                    "end": {"type": "string", "source": "window_end"},
                    "step": {"type": "integer", "minimum": 1, "maximum": 3_600},
                },
            ),
            endpoint(
                "prometheus.series",
                "/api/v1/series",
                {
                    "match[]": text_query,
                    "start": {"type": "string", "source": "window_start"},
                    "end": {"type": "string", "source": "window_end"},
                },
            ),
            endpoint("prometheus.metadata", "/api/v1/metadata", {"metric": optional_text}),
        ]
    if payload.kind == "tempo":
        return "/ready", [
            endpoint(
                "tempo.trace_search",
                "/api/search",
                {
                    "q": text_query,
                    "start": {"type": "string", "source": "window_start"},
                    "end": {"type": "string", "source": "window_end"},
                    "limit": result_limit,
                },
            ),
            endpoint(
                "tempo.service_graph",
                "/api/metrics/query_range",
                {
                    "query": text_query,
                    "start": {"type": "string", "source": "window_start"},
                    "end": {"type": "string", "source": "window_end"},
                },
            ),
        ]
    if payload.kind == "jaeger":
        return "/", [
            endpoint(
                "jaeger.traces",
                "/api/traces",
                {"service": optional_text, "traceID": optional_text, "limit": result_limit},
            ),
            endpoint("jaeger.services", "/api/services"),
        ]
    if payload.kind == "kubernetes":
        namespace = payload.namespace
        prefix = f"/api/v1/namespaces/{namespace}"
        apps = f"/apis/apps/v1/namespaces/{namespace}"
        return "/version", [
            endpoint("kubernetes.pods", f"{prefix}/pods", {"limit": result_limit}),
            endpoint("kubernetes.events", f"{prefix}/events", {"limit": result_limit}),
            endpoint("kubernetes.deployments", f"{apps}/deployments", {"limit": result_limit}),
            endpoint("kubernetes.statefulsets", f"{apps}/statefulsets", {"limit": result_limit}),
        ]
    if payload.kind == "github":
        root = f"/repos/{payload.owner}/{payload.repository}"
        return "/rate_limit", [
            endpoint("github.commits", f"{root}/commits", {"per_page": result_limit}),
            endpoint("github.deployments", f"{root}/deployments", {"per_page": result_limit}),
            endpoint(
                "github.workflow_runs", f"{root}/actions/runs", {"per_page": result_limit}
            ),
        ]
    if payload.kind == "gitlab":
        root = f"/api/v4/projects/{payload.project_id}"
        return "/api/v4/version", [
            endpoint("gitlab.commits", f"{root}/repository/commits", {"per_page": result_limit}),
            endpoint("gitlab.deployments", f"{root}/deployments", {"per_page": result_limit}),
            endpoint("gitlab.pipelines", f"{root}/pipelines", {"per_page": result_limit}),
        ]
    if payload.kind == "argocd":
        return "/api/version", [
            endpoint("argocd.applications", "/api/v1/applications"),
            endpoint(
                "argocd.application_resources",
                "/api/v1/applications/resources-tree",
                {"applicationName": text_query},
            ),
        ]
    raise ValueError("unsupported typed provider connector")


def _connector_introspection_budget(kind: str, now: datetime) -> IntrospectionBudget:
    return IntrospectionBudget(
        timeout_ms=10_000 if kind in {"postgresql", "mysql", "clickhouse"} else 5_000,
        max_resources=500,
        window_start=now - timedelta(minutes=30),
        window_end=now,
    )


_CONNECTOR_PROVIDER_ERROR_STATUS = {
    "authentication_failed": 422,
    "unsupported_version": 422,
    "provider_timeout": 504,
}
_CONNECTOR_PROVIDER_DETAIL_FIELDS = frozenset(
    {
        "provider",
        "observed_version",
        "supported_major_versions",
        "status_code",
        "failed_checks",
        "sqlstate",
        "clickhouse_code",
    }
)


def _connector_operation_error(
    operation: str, exc: Exception, fallback_message: str
) -> HTTPException:
    if not isinstance(exc, ProviderExecutionError):
        return _error(502, f"connector_{operation}_failed", fallback_message)
    safe_details = {
        key: value for key, value in exc.detail.items() if key in _CONNECTOR_PROVIDER_DETAIL_FIELDS
    }
    return _error(
        _CONNECTOR_PROVIDER_ERROR_STATUS.get(exc.code, 502),
        f"connector_{operation}_{exc.code}",
        exc.reason,
        provider_error=exc.code,
        **safe_details,
    )


def _scope_config_from_catalog(kind: str, current_scope: dict, resources: dict) -> dict:
    if kind not in {"postgresql", "mysql", "clickhouse"}:
        return current_scope
    tables = resources.get("tables")
    if not isinstance(tables, dict):
        raise ValueError("SQL discovery returned an invalid catalog")
    clickhouse = kind == "clickhouse"
    if any(
        not isinstance(table, str)
        or not isinstance(descriptor, dict)
        or not isinstance(descriptor.get("stable_order"), list)
        or (
            not clickhouse
            and (
                not isinstance(descriptor.get("time_column"), str) or not descriptor["stable_order"]
            )
        )
        or (
            clickhouse
            and descriptor.get("time_column") is not None
            and not isinstance(descriptor.get("time_column"), str)
        )
        for table, descriptor in tables.items()
    ):
        raise ValueError("SQL discovery returned an invalid catalog")
    expected_dialect = {
        "postgresql": "postgres",
        "mysql": "mysql",
        "clickhouse": "clickhouse",
    }[kind]
    if (
        current_scope.get("dialect") != expected_dialect
        or resources.get("dialect") != expected_dialect
    ):
        raise ValueError("SQL discovery returned an unexpected dialect")
    allowed_schemas = {}
    if kind == "postgresql":
        schemas = current_scope.get("allowed_schemas")
        if not isinstance(schemas, list) or not schemas:
            raise ValueError("PostgreSQL Schema allowlist is required")
        allowed_schemas = {"allowed_schemas": list(schemas)}
    return {
        "dialect": expected_dialect,
        **allowed_schemas,
        "allowed_tables": sorted(tables),
        "table_policies": {
            table: {
                "time_column": descriptor["time_column"],
                "stable_order": descriptor["stable_order"],
            }
            for table, descriptor in tables.items()
        },
    }


@router.post(
    "/workspaces/{workspace_id}/evidence-connectors", response_model=ConnectorOut, status_code=201
)
async def create_connector(
    workspace_id: EntityId,
    payload: ConnectorCreate,
    user_id: EntityId = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    user, _ = await _workspace_access(session, user_id, workspace_id, "admin")
    duplicate = await session.scalar(
        select(EvidenceConnector.id).where(
            EvidenceConnector.workspace_id == workspace_id,
            EvidenceConnector.name == payload.name,
        )
    )
    if duplicate is not None:
        raise _error(409, "connector_name_conflict", "Connector name is already used.")
    actor_id, actor_username = user.id, user.username
    # Do not keep the authorization read transaction open across remote I/O.
    await session.rollback()
    try:
        config, secrets, scope_config, budget = _connector_storage(payload)
        adapter = create_evidence_connector(payload.kind, config, secrets)
    except ValueError as exc:
        raise _error(422, "connector_configuration_invalid", str(exc)) from exc
    try:
        await adapter.verify()
    except Exception as exc:
        raise _connector_operation_error(
            "verification", exc, "Read-only connector verification failed."
        ) from exc
    now = datetime.now(UTC)
    try:
        catalog = await adapter.introspect(
            scope_config, _connector_introspection_budget(payload.kind, now)
        )
        final_scope_config = _scope_config_from_catalog(
            payload.kind, scope_config, dict(catalog.resources)
        )
    except Exception as exc:
        raise _connector_operation_error(
            "introspection", exc, "Connector scope discovery failed."
        ) from exc
    if (
        payload.kind in {"postgresql", "mysql", "clickhouse"}
        and not final_scope_config["allowed_tables"]
    ):
        raise _error(
            422,
            "connector_scope_empty",
            "No safely queryable tables were found in the configured database scope.",
        )
    language, capabilities = _connector_capability(payload.kind)
    ciphertext = (
        encrypt_secret(
            json.dumps(secrets, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        )
        or ""
    )
    row = EvidenceConnector(
        workspace_id=workspace_id,
        name=payload.name,
        kind=payload.kind,
        kind_version=1,
        config=config,
        secret_ciphertext=ciphertext,
        instance_revision=1,
        capabilities=capabilities,
        verification_status="healthy",
        verified_at=now,
        last_error=None,
        last_introspected_at=now,
    )
    session.add(row)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise _error(409, "connector_name_conflict", "Connector name is already used.") from exc
    session.add(
        EvidenceAccessScope(
            connector_id=row.id,
            allowed_languages=[language],
            scope_config=final_scope_config,
            schema_catalog=dict(catalog.resources),
            schema_catalog_revision=1,
            read_policy_revision=1,
            execution_budget_policy=budget,
            normalization_policy_revision=1,
            revision=1,
        )
    )
    session.add(
        AuditEvent(
            actor_id=actor_id,
            actor_username=actor_username,
            action="evidence_connector.create",
            target_type="evidence_connector",
            target_id=str(row.id),
            workspace_id=workspace_id,
            result="ok",
            detail={},
        )
    )
    await session.commit()
    await session.refresh(row)
    return _connector_out(row)


async def _latest_scope(session: AsyncSession, connector_id: EntityId) -> EvidenceAccessScope:
    row = (
        await session.execute(
            select(EvidenceAccessScope)
            .where(EvidenceAccessScope.connector_id == connector_id)
            .order_by(EvidenceAccessScope.revision.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if row is None:
        raise _error(409, "connector_scope_missing", "Connector access scope is missing.")
    return row


@router.post("/workspaces/{workspace_id}/evidence-connectors/{connector_id}/test")
async def test_connector(
    workspace_id: EntityId,
    connector_id: EntityId,
    user_id: EntityId = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    await _workspace_access(session, user_id, workspace_id, "admin")
    row = await session.get(EvidenceConnector, connector_id)
    if row is None or row.workspace_id != workspace_id:
        raise _error(404, "connector_not_found", "Evidence connector not found.")
    adapter = create_evidence_connector(row.kind, row.config, _connector_secret_map(row))
    try:
        verification = await adapter.verify()
    except Exception as exc:
        row.verification_status = "unavailable"
        row.verified_at = None
        row.last_error = type(exc).__name__
        await session.commit()
        raise _connector_operation_error(
            "verification", exc, "Read-only connector verification failed."
        ) from exc
    row.verification_status = "healthy"
    row.verified_at = datetime.now(UTC)
    row.last_error = None
    await session.commit()
    return {
        "provider": verification.provider,
        "version": verification.version,
        "capabilities": list(verification.capabilities),
        "verified_at": row.verified_at,
    }


@router.post("/workspaces/{workspace_id}/evidence-connectors/{connector_id}/introspect")
async def introspect_connector(
    workspace_id: EntityId,
    connector_id: EntityId,
    user_id: EntityId = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    await _workspace_access(session, user_id, workspace_id, "admin")
    row = await session.get(EvidenceConnector, connector_id)
    if row is None or row.workspace_id != workspace_id:
        raise _error(404, "connector_not_found", "Evidence connector not found.")
    if row.verification_status != "healthy":
        raise _error(409, "connector_not_verified", "Verify the connector before introspection.")
    scope = await _latest_scope(session, connector_id)
    adapter = create_evidence_connector(row.kind, row.config, _connector_secret_map(row))
    now = datetime.now(UTC)
    try:
        catalog = await adapter.introspect(
            scope.scope_config, _connector_introspection_budget(row.kind, now)
        )
    except Exception as exc:
        raise _connector_operation_error(
            "introspection", exc, "Connector scope discovery failed."
        ) from exc
    try:
        scope_config = _scope_config_from_catalog(
            row.kind, scope.scope_config, dict(catalog.resources)
        )
    except ValueError as exc:
        raise _error(
            502,
            "connector_introspection_invalid",
            "SQL discovery returned an invalid catalog.",
        ) from exc
    new_scope = EvidenceAccessScope(
        connector_id=row.id,
        allowed_languages=scope.allowed_languages,
        scope_config=scope_config,
        schema_catalog=dict(catalog.resources),
        schema_catalog_revision=scope.schema_catalog_revision + 1,
        read_policy_revision=scope.read_policy_revision,
        execution_budget_policy=scope.execution_budget_policy,
        normalization_policy_revision=scope.normalization_policy_revision,
        revision=scope.revision + 1,
    )
    session.add(new_scope)
    row.last_introspected_at = now
    await session.commit()
    return {
        "provider": catalog.provider,
        "version": catalog.version,
        "resources": catalog.resources,
        "scope_revision": new_scope.revision,
        "readiness": "ready"
        if row.kind not in {"postgresql", "mysql", "clickhouse"}
        or bool(scope_config["allowed_tables"])
        else "empty",
    }


@router.delete("/workspaces/{workspace_id}/evidence-connectors/{connector_id}", status_code=204)
async def disable_connector(
    workspace_id: EntityId,
    connector_id: EntityId,
    user_id: EntityId = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    user, _ = await _workspace_access(session, user_id, workspace_id, "admin")
    row = await session.get(EvidenceConnector, connector_id)
    if row is None or row.workspace_id != workspace_id:
        raise _error(404, "connector_not_found", "Evidence connector not found.")
    row.state = "disabled"
    row.instance_revision += 1
    session.add(
        _audit(user, "evidence_connector.disable", "evidence_connector", row.id, workspace_id)
    )
    await session.commit()
    return Response(status_code=204)

