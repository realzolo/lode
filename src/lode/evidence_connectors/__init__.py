"""Provider-neutral evidence connector boundary."""

from lode.evidence_connectors.registry import (
    build_log_policy_registry,
    create_log_connector,
    log_connector_capabilities,
)
from lode.evidence_connectors.types import (
    IntrospectionBudget,
    NativeSchemaCatalog,
    ProviderExecutionError,
    VerificationResult,
)

__all__ = [
    "IntrospectionBudget",
    "NativeSchemaCatalog",
    "ProviderExecutionError",
    "VerificationResult",
    "build_log_policy_registry",
    "create_log_connector",
    "log_connector_capabilities",
]
