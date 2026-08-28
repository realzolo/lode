"""OpenSearch connector with OpenSearch-only version proof."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from lode.evidence_connectors.common import (
    credential_identity_hash,
    provider_headers,
    response_json,
)
from lode.evidence_connectors.search import SearchConnectorMechanics
from lode.evidence_connectors.transport import (
    BoundedHTTPTransport,
    validate_base_url,
)
from lode.evidence_connectors.types import (
    ProviderExecutionError,
    ProviderHTTPTransport,
    VerificationResult,
)

_VERSION = re.compile(r"^(?P<major>[0-9]+)\.(?P<minor>[0-9]+)(?:\.[0-9]+)?")


class OpenSearchConnectorConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_url: str = Field(max_length=1_000)
    max_response_bytes: int = Field(default=8 * 1024 * 1024, ge=1, le=16 * 1024 * 1024)

    @field_validator("base_url")
    @classmethod
    def base_url_is_origin(cls, value: str) -> str:
        return validate_base_url(value)[0]

class OpenSearchConnector(SearchConnectorMechanics):
    kind = "opensearch"
    language = "opensearch_query_dsl"
    read_capabilities = ("bounded_search", "bounded_aggregation", "field_capabilities")

    def __init__(
        self,
        config: Mapping[str, Any],
        secrets: Mapping[str, str],
        transport: ProviderHTTPTransport | None = None,
    ) -> None:
        self.config = OpenSearchConnectorConfig.model_validate(config)
        self.secrets = dict(secrets)
        headers = provider_headers(self.secrets)
        super().__init__(
            transport
            or BoundedHTTPTransport(
                base_url=self.config.base_url,
                headers=headers,
                max_response_bytes=self.config.max_response_bytes,
            )
        )

    async def verify(self) -> VerificationResult:
        response = await self.transport.request("GET", "/", timeout_ms=5_000)
        payload = response_json(response)
        if not isinstance(payload, dict):
            raise ProviderExecutionError("invalid_response", "provider version response is invalid")
        version = payload.get("version") if isinstance(payload, dict) else None
        number = version.get("number") if isinstance(version, dict) else None
        distribution = version.get("distribution") if isinstance(version, dict) else None
        match = _VERSION.match(number) if isinstance(number, str) else None
        if (
            match is None
            or int(match.group("major")) not in {2, 3}
            or distribution != "opensearch"
            or payload.get("tagline") != "The OpenSearch Project: https://opensearch.org/"
        ):
            observed_version = match.group(0) if match is not None else "unknown"
            raise ProviderExecutionError(
                "unsupported_version",
                f"Unsupported OpenSearch version {observed_version}. "
                "This connector requires OpenSearch 2.x or 3.x.",
                {
                    "provider": "opensearch",
                    "observed_version": observed_version,
                    "supported_major_versions": [2, 3],
                },
            )
        self.version = number
        return VerificationResult(
            self.kind,
            number,
            credential_identity_hash(self.secrets),
            self.read_capabilities,
        )
