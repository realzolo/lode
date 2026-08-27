"""Worker-side client for the isolated command runner."""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Mapping
from time import time
from typing import Any, Protocol
from urllib.parse import urlsplit
from uuid import uuid4

import httpx

from lode.command_runner.protocol import (
    RunnerAction,
    RunnerRequest,
    RunnerResult,
    SignedRunnerRequest,
    sign_request,
)
from lode.evidence_access.orchestrator import ExecutionPermit
from lode.evidence_connectors.common import credential_identity_hash
from lode.evidence_connectors.safety import sanitize_evidence
from lode.evidence_connectors.types import (
    IntrospectionBudget,
    NativeSchemaCatalog,
    ProviderExecutionError,
    VerificationResult,
)


class CommandRunnerClient(Protocol):
    async def catalog(self) -> Mapping[str, Any]: ...
    async def preflight(
        self, authorized_read_id: int, action: RunnerAction
    ) -> Mapping[str, Any]: ...
    async def execute(self, authorized_read_id: int, action: RunnerAction) -> RunnerResult: ...


class HTTPCommandRunnerClient:
    def __init__(self, base_url: str, key: str) -> None:
        parsed = urlsplit(base_url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("command runner URL must be an HTTP(S) origin")
        if len(key.encode()) < 32:
            raise ValueError("command runner key must contain at least 32 bytes")
        self.base_url = base_url.rstrip("/")
        self.key = key

    async def catalog(self) -> Mapping[str, Any]:
        signature = hmac.new(self.key.encode(), b"catalog", hashlib.sha256).hexdigest()
        payload = await self._request("GET", "/catalog", headers={"x-runner-signature": signature})
        if not isinstance(payload, dict):
            raise ProviderExecutionError("invalid_response", "command runner catalog is invalid")
        return payload

    async def preflight(self, authorized_read_id: int, action: RunnerAction) -> Mapping[str, Any]:
        payload = await self._request(
            "POST", "/preflight", json_body=self._envelope(authorized_read_id, action)
        )
        if not isinstance(payload, dict):
            raise ProviderExecutionError("invalid_response", "command runner preflight is invalid")
        return payload

    async def execute(self, authorized_read_id: int, action: RunnerAction) -> RunnerResult:
        payload = await self._request(
            "POST", "/execute", json_body=self._envelope(authorized_read_id, action)
        )
        try:
            return RunnerResult.model_validate(payload)
        except ValueError as exc:
            raise ProviderExecutionError(
                "invalid_response", "command runner result is invalid"
            ) from exc

    def _envelope(self, authorized_read_id: int, action: RunnerAction) -> dict[str, Any]:
        now = int(time())
        request = RunnerRequest(
            nonce=uuid4().hex,
            authorized_read_id=authorized_read_id,
            issued_at=now,
            expires_at=now + 30,
            action=action,
        )
        return SignedRunnerRequest(
            request=request, signature=sign_request(request, self.key)
        ).model_dump(mode="json")

    async def _request(
        self,
        method: str,
        path: str,
        *,
        headers: Mapping[str, str] | None = None,
        json_body: Mapping[str, Any] | None = None,
    ) -> Any:
        try:
            async with httpx.AsyncClient(
                follow_redirects=False,
                timeout=20,
                trust_env=False,
            ) as client:
                response = await client.request(
                    method, self.base_url + path, headers=headers, json=json_body
                )
        except httpx.TimeoutException as exc:
            raise ProviderExecutionError("provider_timeout", "command runner timed out") from exc
        except httpx.HTTPError as exc:
            raise ProviderExecutionError(
                "provider_unavailable", "command runner is unavailable"
            ) from exc
        if response.status_code == 403:
            raise ProviderExecutionError(
                "authentication_failed", "command runner rejected identity"
            )
        if response.status_code in {409, 422}:
            raise ProviderExecutionError("sandbox_violation", "command runner rejected action")
        if response.status_code >= 500:
            raise ProviderExecutionError("provider_unavailable", "command runner is unavailable")
        if response.status_code != 200 or len(response.content) > 2 * 1024 * 1024:
            raise ProviderExecutionError("invalid_response", "command runner response is invalid")
        try:
            return response.json()
        except ValueError as exc:
            raise ProviderExecutionError(
                "invalid_response", "command runner JSON is invalid"
            ) from exc


class CommandRunnerConnector:
    kind = "command_runner"
    language = "command"
    read_capabilities = ("rg_fixed_search", "isolated_execution", "working_set_catalog")

    def __init__(self, client: CommandRunnerClient, runner_key: str) -> None:
        if len(runner_key.encode()) < 32:
            raise ValueError("command runner key must contain at least 32 bytes")
        self.client = client
        self.runner_key = runner_key
        self._catalog: Mapping[str, Any] | None = None

    async def verify(self) -> VerificationResult:
        catalog = await self.client.catalog()
        profiles = catalog.get("profiles")
        version = catalog.get("runner_version")
        if (
            not isinstance(version, str)
            or version != "isolated-command-runner.1"
            or not isinstance(profiles, dict)
            or set(profiles) != {"rg_fixed_search"}
        ):
            raise ProviderExecutionError("invalid_response", "command runner catalog is invalid")
        RunnerAction.model_validate(
            {
                "profile": "rg_fixed_search",
                **profiles["rg_fixed_search"],
                "argv": [
                    "--fixed-strings",
                    "--line-number",
                    "--no-heading",
                    "--color=never",
                    "--no-config",
                    "--max-count=1",
                    "--",
                    "x",
                    "x",
                ],
                "pattern_index": 7,
                "working_set_id": "verify",
                "working_root": "/worksets/verify",
                "allowed_files": ["x"],
                "timeout_ms": 1,
                "output_bytes": 1,
                "result_limit": 1,
            }
        )
        self._catalog = catalog
        return VerificationResult(
            self.kind,
            version,
            credential_identity_hash({"runner_key": self.runner_key}),
            self.read_capabilities,
        )

    async def introspect(
        self, scope: Mapping[str, Any], budget: IntrospectionBudget
    ) -> NativeSchemaCatalog:
        catalog = self._catalog or await self.client.catalog()
        profiles = catalog.get("profiles")
        working_sets = scope.get("working_sets")
        if (
            not isinstance(profiles, dict)
            or not isinstance(working_sets, dict)
            or not working_sets
            or len(working_sets) > budget.max_resources
        ):
            raise ProviderExecutionError("invalid_response", "command working-set scope is invalid")
        for working_set_id, descriptor in working_sets.items():
            if (
                not isinstance(working_set_id, str)
                or not isinstance(descriptor, dict)
                or descriptor.get("root") != f"/worksets/{working_set_id}"
                or not isinstance(descriptor.get("files"), list)
                or not descriptor["files"]
            ):
                raise ProviderExecutionError(
                    "sandbox_violation", "command working-set scope is invalid"
                )
        return NativeSchemaCatalog(
            self.kind,
            str(catalog["runner_version"]),
            {"working_sets": working_sets, "command_capabilities": profiles},
        )

    async def preflight(self, permit: ExecutionPermit) -> Mapping[str, Any]:
        action = self._runner_action(permit)
        return await self.client.preflight(permit.authorized_read_id, action)

    async def execute(self, permit: ExecutionPermit) -> Mapping[str, Any]:
        action = self._runner_action(permit)
        result = await self.client.execute(permit.authorized_read_id, action)
        if result.status != "succeeded":
            raise ProviderExecutionError(
                result.failure_code or "invalid_response",
                "isolated command execution failed",
                {
                    "output_sha256": result.output_sha256,
                    "secret_categories": result.secret_categories,
                },
            )
        records = [line for line in result.stdout.splitlines() if line]
        if len(records) > action.result_limit:
            raise ProviderExecutionError("cost_exceeded", "command result count exceeded budget")
        sanitized, categories, injection = sanitize_evidence(
            {"provider": self.kind, "records": records, "stderr": result.stderr}
        )
        return {
            **sanitized,
            "record_count": len(records),
            "exit_code": result.exit_code,
            "duration_ms": result.duration_ms,
            "bytes": result.output_bytes,
            "secret_categories": sorted(set(categories) | set(result.secret_categories)),
            "prompt_injection_detected": injection or result.prompt_injection_detected,
        }

    @staticmethod
    def _action(permit: ExecutionPermit) -> Mapping[str, Any]:
        if not isinstance(permit, ExecutionPermit):
            raise PermissionError("command adapter requires an internal execution permit")
        permit.assert_valid()
        if permit.action.get("adapter_kind") != "command_runner":
            raise PermissionError("execution permit is not authorized for command runner")
        return permit.action

    @classmethod
    def _runner_action(cls, permit: ExecutionPermit) -> RunnerAction:
        action = cls._action(permit)
        return RunnerAction.model_validate(
            {name: action[name] for name in RunnerAction.model_fields if name in action}
        )
