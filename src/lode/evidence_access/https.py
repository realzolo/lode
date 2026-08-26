"""Canonical URL and endpoint-catalog policy for generic HTTPS safe reads."""

from __future__ import annotations

import re
from collections.abc import Mapping
from copy import deepcopy
from typing import Any
from urllib.parse import urlsplit

from lode.application.intake import canonical_hash
from lode.evidence_access.budget import intersect_budget
from lode.evidence_access.candidate import HTTPSPayload, NativeReadCandidateInput
from lode.evidence_access.tree import bind_exact_values, find_exact_value_slots, structural_hash
from lode.evidence_access.types import (
    AccessContext,
    AccessRejection,
    BoundNativeAction,
    ParsedNativeAction,
    PolicyEvaluation,
)

_PATH = re.compile(r"^/[A-Za-z0-9._~{}/-]*$")
_SEGMENT_TYPES = {
    "slug": re.compile(r"^[A-Za-z0-9_-]{1,128}$"),
    "integer": re.compile(r"^[1-9][0-9]{0,18}$"),
    "uuid": re.compile(
        r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
    ),
    "sha256": re.compile(r"^[0-9a-f]{64}$"),
}
_ENDPOINT_KEYS = {
    "id",
    "method",
    "host",
    "port",
    "path_template",
    "path_parameters",
    "query_parameters",
    "allowed_content_types",
    "max_response_bytes",
}


class HTTPSPolicy:
    language = "https"
    parser_name = "python-urllib-split"
    parser_version = "3.12-safe-url.1"
    policy_version = "https-safe-read.1"

    def parse(self, candidate: NativeReadCandidateInput) -> ParsedNativeAction:
        if not isinstance(candidate.payload, HTTPSPayload):
            raise AccessRejection("invalid_syntax", "HTTPS requires a structured request")
        if candidate.payload.body is not None:
            raise AccessRejection("unsupported_node", "generic HTTPS request bodies are disabled")
        url = self._canonical_url(candidate.payload.url)
        query = candidate.payload.query
        if any(not isinstance(key, str) or not key or len(key) > 128 for key in query):
            raise AccessRejection("invalid_syntax", "HTTPS query key is invalid")
        if any(not isinstance(value, (str, int, bool)) for value in query.values()):
            raise AccessRejection("unsupported_node", "HTTPS query value type is disabled")
        action = {"method": candidate.payload.method, "url": url, "query": deepcopy(query)}
        slots = find_exact_value_slots(action, set(candidate.value_bindings))
        if any(path[:1] != ("query",) for path in slots.values()):
            raise AccessRejection("invalid_syntax", "HTTPS sentinel must be a query value")
        return ParsedNativeAction(
            language=self.language,
            canonical_action=action,
            parse_tree_hash=canonical_hash(action),
            structural_hash=structural_hash(action),
            value_slots=slots,
            parser_name=self.parser_name,
            parser_version=self.parser_version,
        )

    def evaluate(
        self,
        action: ParsedNativeAction,
        candidate: NativeReadCandidateInput,
        context: AccessContext,
    ) -> PolicyEvaluation:
        budget, diff = intersect_budget(candidate, context)
        parsed = urlsplit(str(action.canonical_action["url"]))
        endpoints = context.scope_config.get("safe_read_endpoints")
        if not isinstance(endpoints, list) or not endpoints:
            raise AccessRejection("scope_violation", "HTTPS endpoint catalog is unavailable")
        matches = [
            endpoint
            for endpoint in endpoints
            if self._endpoint_matches(endpoint, action.canonical_action, parsed)
        ]
        if len(matches) != 1:
            raise AccessRejection(
                "scope_violation", "HTTPS request does not match one safe endpoint"
            )
        endpoint = matches[0]
        query = self._effective_query(dict(action.canonical_action["query"]), endpoint, budget)
        port = parsed.port or 443
        effective = {
            "adapter_kind": "https",
            "endpoint_id": endpoint["id"],
            "method": action.canonical_action["method"],
            "origin": f"https://{parsed.hostname}" + (f":{port}" if port != 443 else ""),
            "path": parsed.path,
            "query": query,
            "timeout_ms": budget.timeout_ms,
            "output_bytes": min(budget.output_bytes, endpoint["max_response_bytes"]),
            "allowed_content_types": sorted(endpoint["allowed_content_types"]),
            "query_constraints": {
                name: descriptor
                for name, descriptor in endpoint["query_parameters"].items()
                if descriptor.get("source", "candidate") == "candidate"
            },
        }
        diff["endpoint_scope"] = {
            "endpoint_id": endpoint["id"],
            "host": parsed.hostname,
            "port": port,
            "path_template": endpoint["path_template"],
        }
        return PolicyEvaluation(
            effective_action=effective,
            effective_structural_hash=structural_hash(effective),
            validation_decisions=(
                {"check": "https_canonical_url", "outcome": "allow"},
                {"check": "https_safe_endpoint", "outcome": "allow", "id": endpoint["id"]},
                {"check": "https_query_schema", "outcome": "allow"},
                {"check": "https_egress_scope", "outcome": "allow"},
            ),
            constraint_diff=diff,
            effective_budget=budget,
        )

    def bind_values(
        self,
        action: ParsedNativeAction,
        evaluation: PolicyEvaluation,
        values: Mapping[str, str],
    ) -> BoundNativeAction:
        effective = deepcopy(dict(evaluation.effective_action))
        slots = find_exact_value_slots(effective, set(values))
        if any(path[:1] != ("query",) for path in slots.values()):
            raise AccessRejection("invalid_syntax", "HTTPS sentinel moved outside query")
        bound = bind_exact_values(effective, slots, dict(values))
        for name, descriptor in bound["query_constraints"].items():
            if name in bound["query"]:
                bound["query"][name] = self._query_value(bound["query"][name], descriptor)
        shape = structural_hash(bound)
        if shape != evaluation.effective_structural_hash:
            raise AccessRejection("invalid_syntax", "ValueRef binding changed HTTPS structure")
        return BoundNativeAction(
            language=self.language,
            canonical_action=bound,
            structural_hash=shape,
            parse_tree_hash=canonical_hash(bound),
        )

    @staticmethod
    def _canonical_url(value: str) -> str:
        parsed = urlsplit(value)
        try:
            parsed_port = parsed.port
        except ValueError as exc:
            raise AccessRejection("egress_violation", "HTTPS URL port is invalid") from exc
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.netloc != parsed.netloc.lower()
            or "%" in value
            or "//" in parsed.path
            or not _PATH.fullmatch(parsed.path)
            or any(segment in {".", ".."} for segment in parsed.path.split("/"))
        ):
            raise AccessRejection("egress_violation", "HTTPS URL is not canonical")
        hostname = parsed.hostname
        if not re.fullmatch(
            r"(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
            r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?",
            hostname,
        ):
            raise AccessRejection(
                "egress_violation", "HTTPS hostname must be lowercase canonical DNS"
            )
        port = parsed_port or 443
        origin = f"https://{hostname}" + (f":{port}" if port != 443 else "")
        return origin + (parsed.path or "/")

    def _endpoint_matches(
        self,
        endpoint: Any,
        action: Mapping[str, Any],
        parsed_url,
    ) -> bool:
        self.validate_endpoint(endpoint)
        if (
            action["method"] != endpoint["method"]
            or parsed_url.hostname != endpoint["host"]
            or (parsed_url.port or 443) != endpoint["port"]
        ):
            return False
        template = endpoint["path_template"].split("/")
        actual = parsed_url.path.split("/")
        if len(template) != len(actual):
            return False
        for expected, value in zip(template, actual, strict=True):
            if expected.startswith("{") and expected.endswith("}"):
                name = expected[1:-1]
                kind = endpoint["path_parameters"].get(name)
                if kind not in _SEGMENT_TYPES or _SEGMENT_TYPES[kind].fullmatch(value) is None:
                    return False
            elif expected != value:
                return False
        return True

    @staticmethod
    def validate_endpoint(endpoint: Any) -> None:
        if (
            not isinstance(endpoint, dict)
            or set(endpoint) != _ENDPOINT_KEYS
            or not isinstance(endpoint["id"], str)
            or not endpoint["id"]
            or endpoint["method"] not in {"GET", "HEAD"}
            or not isinstance(endpoint["host"], str)
            or endpoint["host"] != endpoint["host"].lower()
            or isinstance(endpoint["port"], bool)
            or not isinstance(endpoint["port"], int)
            or not 1 <= endpoint["port"] <= 65_535
            or not isinstance(endpoint["path_template"], str)
            or _PATH.fullmatch(endpoint["path_template"]) is None
            or not isinstance(endpoint["path_parameters"], dict)
            or not isinstance(endpoint["query_parameters"], dict)
            or not isinstance(endpoint["allowed_content_types"], list)
            or not endpoint["allowed_content_types"]
            or any(
                not isinstance(item, str) or "/" not in item
                for item in endpoint["allowed_content_types"]
            )
            or isinstance(endpoint["max_response_bytes"], bool)
            or not isinstance(endpoint["max_response_bytes"], int)
            or not 1 <= endpoint["max_response_bytes"] <= 2 * 1024 * 1024
        ):
            raise AccessRejection("scope_violation", "HTTPS endpoint catalog entry is invalid")
        try:
            canonical = HTTPSPolicy._canonical_url(
                f"https://{endpoint['host']}"
                + (f":{endpoint['port']}" if endpoint["port"] != 443 else "")
                + endpoint["path_template"]
            )
        except AccessRejection as exc:
            raise AccessRejection("scope_violation", "HTTPS endpoint origin is invalid") from exc
        if not canonical:
            raise AccessRejection("scope_violation", "HTTPS endpoint origin is invalid")
        placeholders = {
            segment[1:-1]
            for segment in endpoint["path_template"].split("/")
            if segment.startswith("{") and segment.endswith("}")
        }
        if placeholders != set(endpoint["path_parameters"]) or any(
            kind not in _SEGMENT_TYPES for kind in endpoint["path_parameters"].values()
        ):
            raise AccessRejection("scope_violation", "HTTPS path parameter catalog is invalid")
        if any(
            ("{" in segment or "}" in segment)
            and not (segment.startswith("{") and segment.endswith("}"))
            for segment in endpoint["path_template"].split("/")
        ):
            raise AccessRejection("scope_violation", "HTTPS path template is invalid")
        allowed_descriptor_keys = {
            "type",
            "source",
            "required",
            "max_length",
            "minimum",
            "maximum",
            "value",
        }
        for name, descriptor in endpoint["query_parameters"].items():
            if (
                not isinstance(name, str)
                or re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,127}", name) is None
                or not isinstance(descriptor, dict)
                or not set(descriptor) <= allowed_descriptor_keys
                or descriptor.get("type") not in {"string", "integer", "boolean"}
            ):
                raise AccessRejection("scope_violation", "HTTPS query descriptor is invalid")
            source = descriptor.get("source", "candidate")
            if (
                source
                not in {"candidate", "window_start", "window_end", "result_limit", "constant"}
                or ("required" in descriptor and not isinstance(descriptor["required"], bool))
                or (
                    "max_length" in descriptor
                    and (
                        isinstance(descriptor["max_length"], bool)
                        or not isinstance(descriptor["max_length"], int)
                        or not 1 <= descriptor["max_length"] <= 10_000
                    )
                )
                or any(
                    key in descriptor
                    and (isinstance(descriptor[key], bool) or not isinstance(descriptor[key], int))
                    for key in ("minimum", "maximum")
                )
                or (source == "constant") != ("value" in descriptor)
                or (source in {"window_start", "window_end"} and descriptor["type"] != "string")
                or (source == "result_limit" and descriptor["type"] != "integer")
                or (source != "candidate" and "required" in descriptor)
            ):
                raise AccessRejection("scope_violation", "HTTPS query descriptor is invalid")
            if "minimum" in descriptor and "maximum" in descriptor:
                if descriptor["minimum"] > descriptor["maximum"]:
                    raise AccessRejection(
                        "scope_violation", "HTTPS query descriptor range is invalid"
                    )
            if source == "constant":
                try:
                    HTTPSPolicy._query_value(descriptor["value"], descriptor)
                except AccessRejection as exc:
                    raise AccessRejection(
                        "scope_violation", "HTTPS query constant is invalid"
                    ) from exc

    def _effective_query(
        self, supplied: dict[str, Any], endpoint: Mapping[str, Any], budget
    ) -> dict[str, str]:
        schema = endpoint["query_parameters"]
        if any(
            not isinstance(key, str) or not isinstance(value, dict) for key, value in schema.items()
        ):
            raise AccessRejection("scope_violation", "HTTPS query schema is invalid")
        if set(supplied) - set(schema):
            raise AccessRejection("scope_violation", "HTTPS query key is outside endpoint scope")
        output: dict[str, str] = {}
        for name, descriptor in sorted(schema.items()):
            source = descriptor.get("source", "candidate")
            if source == "candidate":
                if name not in supplied:
                    if descriptor.get("required") is True:
                        raise AccessRejection(
                            "scope_violation", "HTTPS required query value is missing"
                        )
                    continue
                output[name] = self._query_value(supplied[name], descriptor)
            elif source == "window_start":
                if name in supplied or budget.window_start is None:
                    raise AccessRejection("scope_violation", "HTTPS window query source is invalid")
                output[name] = budget.window_start.isoformat()
            elif source == "window_end":
                if name in supplied or budget.window_end is None:
                    raise AccessRejection("scope_violation", "HTTPS window query source is invalid")
                output[name] = budget.window_end.isoformat()
            elif source == "result_limit":
                if name in supplied:
                    raise AccessRejection("scope_violation", "HTTPS result limit is server-owned")
                output[name] = str(budget.result_limit)
            elif source == "constant" and "value" in descriptor:
                if name in supplied:
                    raise AccessRejection(
                        "scope_violation", "HTTPS constant query value is server-owned"
                    )
                output[name] = self._query_value(descriptor["value"], descriptor)
            else:
                raise AccessRejection("scope_violation", "HTTPS query source is invalid")
        return output

    @staticmethod
    def _query_value(value: Any, descriptor: Mapping[str, Any]) -> str:
        kind = descriptor.get("type")
        if kind == "string" and isinstance(value, str):
            maximum = descriptor.get("max_length", 1_000)
            if (
                isinstance(maximum, bool)
                or not isinstance(maximum, int)
                or not 1 <= len(value) <= maximum
            ):
                raise AccessRejection("budget_violation", "HTTPS string query value is invalid")
            return value
        if kind == "integer" and isinstance(value, int) and not isinstance(value, bool):
            minimum = descriptor.get("minimum", 0)
            maximum = descriptor.get("maximum", 1_000_000)
            if (
                not isinstance(minimum, int)
                or not isinstance(maximum, int)
                or not minimum <= value <= maximum
            ):
                raise AccessRejection("budget_violation", "HTTPS integer query value is invalid")
            return str(value)
        if kind == "boolean" and isinstance(value, bool):
            return "true" if value else "false"
        raise AccessRejection("unsupported_node", "HTTPS query value does not match schema")
