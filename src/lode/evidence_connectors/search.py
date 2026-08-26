"""Provider-neutral mechanics for independently validated search products."""

from __future__ import annotations

import re
from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from lode.evidence_access.orchestrator import ExecutionPermit
from lode.evidence_connectors.common import response_json
from lode.evidence_connectors.safety import sanitize_evidence
from lode.evidence_connectors.types import (
    IntrospectionBudget,
    NativeSchemaCatalog,
    ProviderExecutionError,
    ProviderHTTPTransport,
)

_INDEX = re.compile(r"^[a-z0-9][a-z0-9._-]{0,254}$")


class SearchConnectorMechanics:
    kind: str
    language: str

    def __init__(self, transport: ProviderHTTPTransport) -> None:
        self.transport = transport
        self.version: str | None = None

    async def introspect(
        self, scope: Mapping[str, Any], budget: IntrospectionBudget
    ) -> NativeSchemaCatalog:
        indices = scope.get("allowed_indices")
        if (
            not isinstance(indices, list)
            or not indices
            or any(
                not isinstance(item, str)
                or _INDEX.fullmatch(item) is None
                or item.startswith(".")
                or ".." in item
                for item in indices
            )
            or len(indices) > budget.max_resources
        ):
            raise ProviderExecutionError(
                "invalid_response", "search introspection scope is invalid"
            )
        cardinality_bounds = scope.get("cardinality_bounds", {})
        if not isinstance(cardinality_bounds, dict) or any(
            index not in indices or not isinstance(bounds, dict)
            for index, bounds in cardinality_bounds.items()
        ):
            raise ProviderExecutionError("invalid_response", "search cardinality scope is invalid")
        resources: dict[str, Any] = {"indices": {}}
        resource_count = len(indices)
        for index in indices:
            response = await self.transport.request(
                "GET",
                f"/{index}/_field_caps",
                query={"fields": "*", "include_unmapped": "false"},
                timeout_ms=budget.timeout_ms,
            )
            payload = response_json(response)
            fields = payload.get("fields") if isinstance(payload, dict) else None
            if not isinstance(fields, dict):
                raise ProviderExecutionError(
                    "invalid_response", "search field capabilities are invalid"
                )
            catalog: dict[str, Any] = {}
            for field, by_type in fields.items():
                resource_count += 1
                if resource_count > budget.max_resources:
                    raise ProviderExecutionError(
                        "cost_exceeded", "search field catalog is too large"
                    )
                if not isinstance(field, str) or not isinstance(by_type, dict) or not by_type:
                    raise ProviderExecutionError(
                        "invalid_response", "search field capability entry is invalid"
                    )
                descriptors = list(by_type.values())
                if any(not isinstance(item, dict) for item in descriptors):
                    raise ProviderExecutionError(
                        "invalid_response", "search field capability type is invalid"
                    )
                bound = cardinality_bounds.get(index, {}).get(field)
                if bound is not None and (
                    isinstance(bound, bool)
                    or not isinstance(bound, int)
                    or not 1 <= bound <= 1_000_000
                ):
                    raise ProviderExecutionError(
                        "invalid_response", "search field cardinality bound is invalid"
                    )
                catalog[field] = {
                    "type": min(by_type),
                    "searchable": all(item.get("searchable") is True for item in descriptors),
                    "aggregatable": all(item.get("aggregatable") is True for item in descriptors),
                    "cardinality": bound,
                }
            unknown_bounds = set(cardinality_bounds.get(index, {})) - set(catalog)
            if unknown_bounds:
                raise ProviderExecutionError(
                    "invalid_response", "search cardinality scope names unknown fields"
                )
            resources["indices"][index] = {"fields": catalog}
        return NativeSchemaCatalog(self.kind, self.version or "unverified", resources)

    async def preflight(self, permit: ExecutionPermit) -> Mapping[str, Any]:
        action = self._action(permit)
        index = action["path"].split("/", 2)[1]
        response = await self.transport.request(
            "POST",
            f"/{index}/_validate/query",
            query={"explain": "false"},
            json_body={"query": action["body"]["query"]},
            timeout_ms=min(5_000, action["timeout_ms"]),
        )
        payload = response_json(response)
        if not isinstance(payload, dict) or payload.get("valid") is not True:
            raise ProviderExecutionError("invalid_response", "provider query validation failed")
        return {"provider": self.kind, "valid": True, "version": self.version}

    async def execute(self, permit: ExecutionPermit) -> Mapping[str, Any]:
        action = self._action(permit)
        requested = int(action["body"]["size"])
        page_size = min(int(action["page_size"]), requested) if requested else 0
        remaining = requested
        cursor: list[Any] | None = None
        seen_cursors: set[str] = set()
        records: list[dict[str, Any]] = []
        aggregations: Mapping[str, Any] | None = None
        took_ms = 0
        pages = 0
        while pages == 0 or remaining > 0:
            body = deepcopy(dict(action["body"]))
            body["size"] = min(page_size, remaining) if requested else 0
            if cursor is not None:
                body["search_after"] = cursor
            response = await self.transport.request(
                "POST",
                action["path"],
                json_body=body,
                timeout_ms=action["timeout_ms"],
            )
            payload = response_json(response)
            page, page_aggregations, page_took = self._normalize_page(payload)
            pages += 1
            took_ms += page_took
            if aggregations is None:
                aggregations = page_aggregations
            records.extend(page[:remaining])
            remaining = max(0, requested - len(records))
            if requested == 0 or remaining == 0 or len(page) < body["size"]:
                break
            next_cursor = page[-1].get("sort")
            if not isinstance(next_cursor, list) or not next_cursor:
                raise ProviderExecutionError(
                    "invalid_response", "search pagination cursor is missing"
                )
            key = repr(next_cursor)
            if key in seen_cursors:
                raise ProviderExecutionError(
                    "invalid_response", "search pagination cursor repeated"
                )
            seen_cursors.add(key)
            cursor = next_cursor
            if pages >= 20:
                raise ProviderExecutionError(
                    "cost_exceeded", "search pagination request budget exceeded"
                )
        normalized = {
            "provider": self.kind,
            "records": records,
            "record_count": len(records),
            "aggregations": aggregations or {},
            "pages": pages,
            "took_ms": took_ms,
        }
        sanitized, categories, injection = sanitize_evidence(normalized)
        return {
            **sanitized,
            "secret_categories": list(categories),
            "prompt_injection_detected": injection,
        }

    def _action(self, permit: ExecutionPermit) -> Mapping[str, Any]:
        if not isinstance(permit, ExecutionPermit):
            raise PermissionError("search adapter requires an internal execution permit")
        permit.assert_valid()
        action = permit.action
        required = {"adapter_kind", "path", "body", "page_size", "timeout_ms"}
        if action.get("adapter_kind") != self.kind or not required <= set(action):
            raise PermissionError(f"execution permit is not authorized for {self.kind}")
        return action

    @staticmethod
    def _normalize_page(payload: Any) -> tuple[list[dict[str, Any]], Mapping[str, Any], int]:
        if not isinstance(payload, dict):
            raise ProviderExecutionError("invalid_response", "search response is not an object")
        shards = payload.get("_shards")
        if payload.get("timed_out") is not False or not isinstance(shards, dict):
            raise ProviderExecutionError(
                "partial_response", "search response timed out or omitted shard status"
            )
        if shards.get("failed") != 0:
            raise ProviderExecutionError(
                "partial_response", "search response contains failed shards"
            )
        hits = payload.get("hits")
        rows = hits.get("hits") if isinstance(hits, dict) else None
        if not isinstance(rows, list):
            raise ProviderExecutionError("invalid_response", "search hits are invalid")
        output: list[dict[str, Any]] = []
        for hit in rows:
            if (
                not isinstance(hit, dict)
                or not isinstance(hit.get("_id"), str)
                or not isinstance(hit.get("_index"), str)
            ):
                raise ProviderExecutionError("invalid_response", "search hit identity is invalid")
            source = hit.get("_source", {})
            sort = hit.get("sort")
            if not isinstance(source, dict) or not isinstance(sort, list) or not sort:
                raise ProviderExecutionError(
                    "invalid_response", "search hit source or stable sort is invalid"
                )
            output.append(
                {"index": hit["_index"], "id": hit["_id"], "sort": sort, "source": source}
            )
        took = payload.get("took", 0)
        if isinstance(took, bool) or not isinstance(took, int) or took < 0:
            raise ProviderExecutionError("invalid_response", "search timing is invalid")
        aggregations = payload.get("aggregations", {})
        if not isinstance(aggregations, dict):
            raise ProviderExecutionError("invalid_response", "search aggregations are invalid")
        return output, aggregations, took
