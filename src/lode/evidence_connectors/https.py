"""Generic endpoint-catalog HTTP(S) safe-read connector."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from lode.evidence_access.orchestrator import ExecutionPermit
from lode.evidence_access.https import HTTPSPolicy
from lode.evidence_access.types import AccessRejection
from lode.evidence_connectors.common import credential_identity_hash, provider_headers
from lode.evidence_connectors.safety import sanitize_evidence
from lode.evidence_connectors.transport import (
    BoundedHTTPTransport,
    validate_base_url,
)
from lode.evidence_connectors.types import (
    IntrospectionBudget,
    NativeSchemaCatalog,
    ProviderExecutionError,
    ProviderHTTPTransport,
    VerificationResult,
    decode_provider_json,
)


class HTTPSConnectorConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_url: str = Field(max_length=1_000)
    verification_path: str = Field(pattern=r"^/[A-Za-z0-9._~/-]*$", max_length=1_000)
    max_response_bytes: int = Field(default=2 * 1024 * 1024, ge=1, le=2 * 1024 * 1024)
    max_decompression_ratio: int = Field(default=20, ge=1, le=100)

    @field_validator("base_url")
    @classmethod
    def base_url_is_origin(cls, value: str) -> str:
        return validate_base_url(value)[0]


class HTTPSConnector:
    kind = "https"
    language = "https"
    read_capabilities = ("safe_get", "safe_head", "endpoint_catalog")

    def __init__(
        self,
        config: Mapping[str, Any],
        secrets: Mapping[str, str],
        transport: ProviderHTTPTransport | None = None,
    ) -> None:
        self.config = HTTPSConnectorConfig.model_validate(config)
        self.secrets = dict(secrets)
        headers = provider_headers(self.secrets)
        self.transport = transport or BoundedHTTPTransport(
            base_url=self.config.base_url,
            headers=headers,
            max_response_bytes=self.config.max_response_bytes,
            max_decompression_ratio=self.config.max_decompression_ratio,
        )

    async def verify(self) -> VerificationResult:
        response = await self.transport.request(
            "HEAD", self.config.verification_path, timeout_ms=5_000
        )
        if not 200 <= response.status_code < 300:
            raise ProviderExecutionError(
                "provider_unavailable", "HTTP(S) safe-read endpoint is unavailable"
            )
        return VerificationResult(
            self.kind,
            "http/1.1",
            credential_identity_hash(self.secrets),
            self.read_capabilities,
        )

    async def introspect(
        self, scope: Mapping[str, Any], budget: IntrospectionBudget
    ) -> NativeSchemaCatalog:
        endpoints = scope.get("safe_read_endpoints")
        if not isinstance(endpoints, list) or not 1 <= len(endpoints) <= budget.max_resources:
            raise ProviderExecutionError("invalid_response", "HTTP(S) endpoint catalog is invalid")
        origin = validate_base_url(self.config.base_url)[0]
        for endpoint in endpoints:
            if not isinstance(endpoint, dict):
                raise ProviderExecutionError("invalid_response", "HTTP(S) endpoint entry is invalid")
            try:
                HTTPSPolicy.validate_endpoint(endpoint)
            except AccessRejection as exc:
                raise ProviderExecutionError(
                    "invalid_response", "HTTP(S) endpoint entry is invalid"
                ) from exc
            hostname = str(endpoint["host"])
            scheme = str(endpoint["scheme"])
            default_port = 80 if scheme == "http" else 443
            endpoint_origin = f"{scheme}://[{hostname}]" if ":" in hostname else f"{scheme}://{hostname}"
            if endpoint["port"] != default_port:
                endpoint_origin += f":{endpoint['port']}"
            if endpoint_origin != origin:
                raise ProviderExecutionError(
                    "scope_violation", "HTTP(S) endpoint exceeds connector origin"
                )
        return NativeSchemaCatalog(self.kind, "http/1.1", {"safe_read_endpoints": endpoints})

    async def preflight(self, permit: ExecutionPermit) -> Mapping[str, Any]:
        action = self._action(permit)
        return {"provider": self.kind, "endpoint_id": action["endpoint_id"], "safe_read": True}

    async def execute(self, permit: ExecutionPermit) -> Mapping[str, Any]:
        action = self._action(permit)
        if action["origin"] != self.config.base_url:
            raise ProviderExecutionError("invalid_response", "HTTP(S) permit origin changed")
        response = await self.transport.request(
            action["method"],
            action["path"],
            query=action["query"],
            timeout_ms=action["timeout_ms"],
        )
        if not 200 <= response.status_code < 300:
            from lode.evidence_connectors.common import classify_response

            classify_response(response)
        content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
        if action["method"] != "HEAD" and content_type not in action["allowed_content_types"]:
            raise ProviderExecutionError(
                "invalid_response", "HTTP(S) content type is outside endpoint scope"
            )
        if len(response.body) > action["output_bytes"]:
            raise ProviderExecutionError("cost_exceeded", "HTTP(S) output byte budget exceeded")
        if action["method"] == "HEAD":
            value: Any = {"status_code": response.status_code}
        elif content_type == "application/json":
            value = decode_provider_json(response.body)
        elif content_type.startswith("text/"):
            try:
                value = response.body.decode("utf-8", errors="strict")
            except UnicodeDecodeError as exc:
                raise ProviderExecutionError("invalid_response", "HTTP(S) text is not UTF-8") from exc
        else:
            raise ProviderExecutionError("invalid_response", "HTTP(S) response type is disabled")
        sanitized, categories, injection = sanitize_evidence(
            {"provider": self.kind, "endpoint_id": action["endpoint_id"], "record": value}
        )
        return {
            **sanitized,
            "bytes": len(response.body),
            "secret_categories": list(categories),
            "prompt_injection_detected": injection,
        }

    @staticmethod
    def _action(permit: ExecutionPermit) -> Mapping[str, Any]:
        if not isinstance(permit, ExecutionPermit):
            raise PermissionError("HTTP(S) adapter requires an internal execution permit")
        permit.assert_valid()
        action = permit.action
        required = {
            "adapter_kind",
            "endpoint_id",
            "method",
            "origin",
            "path",
            "query",
            "timeout_ms",
            "output_bytes",
            "allowed_content_types",
        }
        if action.get("adapter_kind") != "https" or not required <= set(action):
            raise PermissionError("execution permit is not authorized for HTTP(S)")
        return action
