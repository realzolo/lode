"""Closed V1 domain enumerations."""

from __future__ import annotations

from enum import StrEnum


class IngestionState(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    PAUSED = "paused"


class ExecutionClass(StrEnum):
    LATENCY_OPTIMIZED = "latency_optimized"
    REASONING_OPTIMIZED = "reasoning_optimized"


class ModelRole(StrEnum):
    PLANNER = "planner"
    NATIVE_QUERY = "native_query"
    SYNTHESIZER = "synthesizer"
    VERIFIER = "verifier"
    CONTEXT_COMPACTOR = "context_compactor"


class ModelDataClass(StrEnum):
    MASKED = "masked"
    SOURCE_CODE = "source_code"
    INTERNAL = "internal"
    RESTRICTED = "restricted"


class LifecycleState(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"


class HealthState(StrEnum):
    UNTESTED = "untested"
    HEALTHY = "healthy"
    UNAVAILABLE = "unavailable"


class RepositoryRole(StrEnum):
    RUNTIME_SOURCE = "runtime_source"
    SHARED_LIBRARY = "shared_library"
    INFRASTRUCTURE = "infrastructure"
    DOCUMENTATION = "documentation"


class IdentityStatus(StrEnum):
    VERIFIED = "verified"
    PROVISIONAL = "provisional"
    AMBIGUOUS = "ambiguous"


class ComponentKind(StrEnum):
    SERVICE = "service"
    WORKER = "worker"
    JOB = "job"
    GATEWAY = "gateway"
    LIBRARY_RUNTIME = "library_runtime"
    UNKNOWN = "unknown"


class SourceBindingRole(StrEnum):
    PRIMARY = "primary"
    SUPPORTING = "supporting"
    GENERATED = "generated"
    CONTRACT = "contract"


class ResolutionKind(StrEnum):
    BUILD_UNIT = "build_unit"
    COMPONENT = "component"
    COMPONENT_SOURCE_BINDING = "component_source_binding"
    IDENTITY_ALIAS = "identity_alias"
    RELATION_EXTRACTION_RULE = "relation_extraction_rule"


class ResolutionStatus(StrEnum):
    VERIFIED = "verified"
    PROVISIONAL = "provisional"
    AMBIGUOUS = "ambiguous"
    SUPERSEDED = "superseded"


class NativeLanguage(StrEnum):
    LOGQL = "logql"
    ELASTICSEARCH_QUERY_DSL = "elasticsearch_query_dsl"
    OPENSEARCH_QUERY_DSL = "opensearch_query_dsl"
    SQL = "sql"
    HTTPS = "https"
    COMMAND = "command"


class RelationKind(StrEnum):
    PARTICIPATED_IN = "participated_in"
    CALLED = "called"
    PUBLISHED_TO = "published_to"
    CONSUMED_FROM = "consumed_from"
    DEPENDS_ON = "depends_on"
    CAUSED_BY = "caused_by"
    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"
    SAME_IDENTITY_CANDIDATE = "same_identity_candidate"


class AccessOutcome(StrEnum):
    ALLOW = "allow"
    REJECT = "reject"


class AccessRejectionCode(StrEnum):
    INVALID_SYNTAX = "invalid_syntax"
    UNSUPPORTED_NODE = "unsupported_node"
    WRITE_SEMANTICS = "write_semantics"
    SCOPE_VIOLATION = "scope_violation"
    BUDGET_VIOLATION = "budget_violation"
    SANDBOX_VIOLATION = "sandbox_violation"
    PREFLIGHT_FAILED = "preflight_failed"


EVIDENCE_REQUIRED_RELATIONS = frozenset(
    {
        RelationKind.CALLED,
        RelationKind.PUBLISHED_TO,
        RelationKind.CONSUMED_FROM,
        RelationKind.CAUSED_BY,
    }
)
