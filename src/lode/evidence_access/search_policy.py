"""Provider-profiled structured search policy primitives."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC
from typing import Any

from lode.application.intake import canonical_hash
from lode.evidence_access.budget import intersect_budget
from lode.evidence_access.candidate import NativeReadCandidateInput, SearchPayload
from lode.evidence_access.tree import (
    bind_exact_values,
    find_exact_value_slots,
    structural_hash,
)
from lode.evidence_access.types import (
    AccessContext,
    AccessRejection,
    BoundNativeAction,
    ParsedNativeAction,
    PolicyEvaluation,
)

_PATH = re.compile(r"^/(?P<index>[A-Za-z0-9][A-Za-z0-9_.-]{0,254})/_search$")
_FIELD = re.compile(r"^[A-Za-z_@][A-Za-z0-9_@.-]{0,254}$")
_INTERVAL = re.compile(r"^(?P<value>[1-9][0-9]*)(?P<unit>ms|s|m|h|d)$")
_INTERVAL_SECONDS = {"ms": 0.001, "s": 1, "m": 60, "h": 3600, "d": 86400}
_BODY_KEYS = {
    "query",
    "aggs",
    "aggregations",
    "size",
    "from",
    "sort",
    "_source",
    "timeout",
    "track_total_hits",
}
_QUERY_NODES = {"bool", "term", "terms", "range", "exists", "match", "match_phrase", "match_all"}
_AGGREGATIONS = {
    "date_histogram",
    "terms",
    "min",
    "max",
    "avg",
    "sum",
    "value_count",
    "percentiles",
}
_SCALAR_TYPES = (str, int, float, bool)


@dataclass(frozen=True, slots=True)
class SearchPolicyProfile:
    language: str
    adapter_kind: str
    parser_name: str
    parser_version: str
    policy_version: str
    allowed_query_nodes: frozenset[str]
    allowed_aggregations: frozenset[str]


@dataclass(slots=True)
class _ValidationState:
    fields: Mapping[str, Mapping[str, Any]] | None
    clauses: int = 0
    max_clauses: int = 128
    max_depth: int = 16


class StructuredSearchPolicy:
    def __init__(self, profile: SearchPolicyProfile) -> None:
        self.profile = profile
        self.language = profile.language
        self.parser_name = profile.parser_name
        self.parser_version = profile.parser_version
        self.policy_version = profile.policy_version

    def parse(self, candidate: NativeReadCandidateInput) -> ParsedNativeAction:
        if not isinstance(candidate.payload, SearchPayload):
            raise AccessRejection("invalid_syntax", "search DSL requires a structured payload")
        action = candidate.payload.model_dump(mode="json")
        self._parse_path(action["path"])
        self._validate_body_syntax(action["body"])
        slots = find_exact_value_slots(action, set(candidate.value_bindings))
        for path in slots.values():
            if len(path) < 3 or path[:2] != ("body", "query"):
                raise AccessRejection(
                    "invalid_syntax", "search sentinel is outside a query value node"
                )
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
        if budget.window_start is None or budget.window_end is None:
            raise AccessRejection(
                "budget_violation", "search DSL requires an absolute bounded window"
            )
        path = str(action.canonical_action["path"])
        index = self._parse_path(path)
        allowed_indices = self._string_list(
            context.scope_config.get("allowed_indices"), "allowed indices"
        )
        if index not in allowed_indices:
            raise AccessRejection("scope_violation", "search index is outside the snapshot scope")
        fields = self._catalog_fields(context, index)
        max_clauses = self._scope_int(context, "max_query_clauses", 128)
        max_depth = self._scope_int(context, "max_query_depth", 16)

        original_body = deepcopy(dict(action.canonical_action["body"]))
        query = original_body.get("query")
        state = _ValidationState(fields=fields, max_clauses=max_clauses, max_depth=max_depth)
        predicate_count = 0 if query is None else self._validate_query(query, state, 0)
        required_terms = self._required_terms(context, fields)
        if predicate_count == 0 and not required_terms:
            raise AccessRejection(
                "scope_violation", "search query has no evidence-relevant predicate"
            )

        timestamp_field = context.scope_config.get("timestamp_field")
        self._require_field(timestamp_field, fields, searchable=True)
        filters: list[dict[str, Any]] = [
            {"term": {field: value}} for field, value in sorted(required_terms.items())
        ]
        filters.append(
            {
                "range": {
                    timestamp_field: {
                        "gte": budget.window_start.astimezone(UTC).isoformat(),
                        "lt": budget.window_end.astimezone(UTC).isoformat(),
                    }
                }
            }
        )
        if query is not None:
            filters.append(query)
        effective_body: dict[str, Any] = {"query": {"bool": {"filter": filters}}}

        aggregations = original_body.get("aggs", original_body.get("aggregations"))
        if "aggs" in original_body and "aggregations" in original_body:
            raise AccessRejection("invalid_syntax", "use only one aggregation key")
        if aggregations is not None:
            effective_body["aggs"] = self._validate_aggregations(
                aggregations,
                fields,
                context,
                (budget.window_end - budget.window_start).total_seconds(),
            )

        source_fields = self._effective_source_fields(original_body.get("_source"), context, fields)
        effective_body["_source"] = source_fields
        requested_from = original_body.get("from", 0)
        if (
            isinstance(requested_from, bool)
            or not isinstance(requested_from, int)
            or requested_from != 0
        ):
            raise AccessRejection("budget_violation", "search pagination offset must be zero")
        requested_size = original_body.get("size", budget.result_limit)
        if (
            isinstance(requested_size, bool)
            or not isinstance(requested_size, int)
            or requested_size < 0
        ):
            raise AccessRejection("invalid_syntax", "search size is invalid")
        effective_size = min(requested_size, budget.result_limit)
        effective_body["size"] = effective_size
        stable_sort = [
            {timestamp_field: {"order": "asc", "unmapped_type": "date"}},
            {"_id": {"order": "asc"}},
        ]
        supplied_sort = original_body.get("sort")
        if supplied_sort is not None and supplied_sort != stable_sort:
            raise AccessRejection(
                "unsupported_node", "search sort must match the stable server order"
            )
        effective_body["sort"] = stable_sort
        effective_body["timeout"] = f"{budget.timeout_ms}ms"
        effective_body["track_total_hits"] = False

        for key in sorted(set(original_body) - set(effective_body) - {"aggregations", "aggs"}):
            if key in {"from", "size"}:
                continue
            raise AccessRejection(
                "unsupported_node", "search body option is not supported", {"field": key}
            )
        diff["mandatory_scope"] = {
            "index": index,
            "required_terms": sorted(required_terms),
            "timestamp_field": timestamp_field,
        }
        if requested_size != effective_size:
            diff["body.size"] = {"requested": requested_size, "effective": effective_size}
        effective = {
            "adapter_kind": self.profile.adapter_kind,
            "path": path,
            "body": effective_body,
            "page_size": min(effective_size, self._scope_int(context, "max_page_size", 250)),
            "timeout_ms": budget.timeout_ms,
        }
        return PolicyEvaluation(
            effective_action=effective,
            effective_structural_hash=structural_hash(effective),
            validation_decisions=(
                {"check": f"{self.language}_json_ast", "outcome": "allow"},
                {"check": f"{self.language}_index_scope", "outcome": "allow", "index": index},
                {
                    "check": f"{self.language}_query_allowlist",
                    "outcome": "allow",
                    "clauses": state.clauses,
                },
                {"check": f"{self.language}_aggregation_budget", "outcome": "allow"},
                {"check": f"{self.language}_mandatory_constraints", "outcome": "allow"},
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
        for path in slots.values():
            if "query" not in path:
                raise AccessRejection("invalid_syntax", "search sentinel moved outside query")
        bound = bind_exact_values(effective, slots, dict(values))
        self._parse_path(bound["path"])
        self._validate_body_syntax(bound["body"])
        shape = structural_hash(bound)
        if shape != evaluation.effective_structural_hash:
            raise AccessRejection(
                "invalid_syntax", "ValueRef binding changed search JSON structure"
            )
        return BoundNativeAction(
            language=self.language,
            canonical_action=bound,
            structural_hash=shape,
            parse_tree_hash=canonical_hash(bound),
        )

    @staticmethod
    def _parse_path(path: Any) -> str:
        if not isinstance(path, str):
            raise AccessRejection("invalid_syntax", "search path must be a string")
        match = _PATH.fullmatch(path)
        if match is None or "%" in path or ".." in path or "," in path or "*" in path:
            raise AccessRejection("write_semantics", "only one exact index _search path is allowed")
        index = match.group("index")
        if index.startswith(".") or index in {"_all", "all"}:
            raise AccessRejection("scope_violation", "reserved search index is disabled")
        return index

    def _validate_body_syntax(self, body: Any) -> None:
        if not isinstance(body, dict):
            raise AccessRejection("invalid_syntax", "search body must be an object")
        unknown = set(body) - _BODY_KEYS
        if unknown:
            raise AccessRejection(
                "unsupported_node",
                "search body contains disabled fields",
                {"fields": sorted(unknown)},
            )
        if "aggs" in body and "aggregations" in body:
            raise AccessRejection("invalid_syntax", "duplicate aggregation forms are disabled")
        if "query" in body:
            self._validate_query(body["query"], _ValidationState(fields=None), 0)
        aggs = body.get("aggs", body.get("aggregations"))
        if aggs is not None:
            self._validate_aggregation_syntax(aggs, 0)
        if "timeout" in body and not isinstance(body["timeout"], str):
            raise AccessRejection("invalid_syntax", "search timeout must be a duration string")
        if "track_total_hits" in body and not isinstance(body["track_total_hits"], (bool, int)):
            raise AccessRejection("invalid_syntax", "track_total_hits is invalid")

    def _validate_query(self, node: Any, state: _ValidationState, depth: int) -> int:
        if depth > state.max_depth:
            raise AccessRejection("budget_violation", "search query depth budget exceeded")
        if not isinstance(node, dict) or len(node) != 1:
            raise AccessRejection("invalid_syntax", "each search query node must have one operator")
        operator, value = next(iter(node.items()))
        if operator not in self.profile.allowed_query_nodes or operator not in _QUERY_NODES:
            code = "write_semantics" if operator in {"script", "percolate"} else "unsupported_node"
            raise AccessRejection(code, "search query operator is disabled", {"operator": operator})
        state.clauses += 1
        if state.clauses > state.max_clauses:
            raise AccessRejection("budget_violation", "search query clause budget exceeded")
        if operator == "match_all":
            if value != {}:
                raise AccessRejection("invalid_syntax", "match_all must be empty")
            return 0
        if operator == "bool":
            return self._validate_bool(value, state, depth)
        if operator == "exists":
            if not isinstance(value, dict) or set(value) != {"field"}:
                raise AccessRejection("invalid_syntax", "exists query shape is invalid")
            self._require_field(value["field"], state.fields, searchable=True)
            return 1
        if not isinstance(value, dict) or len(value) != 1:
            raise AccessRejection("invalid_syntax", f"{operator} query must contain one field")
        field, operand = next(iter(value.items()))
        self._require_field(field, state.fields, searchable=True)
        if operator == "term":
            if isinstance(operand, dict):
                if set(operand) != {"value"}:
                    raise AccessRejection("unsupported_node", "term options are disabled")
                operand = operand["value"]
            self._require_scalar(operand)
        elif operator == "terms":
            if not isinstance(operand, list) or not 1 <= len(operand) <= 100:
                raise AccessRejection("budget_violation", "terms value budget exceeded")
            for item in operand:
                self._require_scalar(item)
        elif operator == "range":
            if (
                not isinstance(operand, dict)
                or not operand
                or not set(operand) <= {"gt", "gte", "lt", "lte"}
            ):
                raise AccessRejection("unsupported_node", "range options are disabled")
            for item in operand.values():
                self._require_scalar(item)
        elif operator in {"match", "match_phrase"}:
            if isinstance(operand, dict):
                if not set(operand) <= {"query", "operator"} or "query" not in operand:
                    raise AccessRejection("unsupported_node", "match options are disabled")
                if operand.get("operator", "and") != "and":
                    raise AccessRejection("unsupported_node", "only match operator=and is allowed")
                operand = operand["query"]
            self._require_scalar(operand)
        return 1

    def _validate_bool(self, value: Any, state: _ValidationState, depth: int) -> int:
        if (
            not isinstance(value, dict)
            or not value
            or not set(value)
            <= {
                "filter",
                "must",
                "must_not",
                "should",
                "minimum_should_match",
            }
        ):
            raise AccessRejection("unsupported_node", "bool query options are disabled")
        count = 0
        for key in ("filter", "must", "must_not", "should"):
            if key not in value:
                continue
            children = value[key] if isinstance(value[key], list) else [value[key]]
            if not children or len(children) > 64:
                raise AccessRejection("budget_violation", "bool child budget exceeded")
            count += sum(self._validate_query(item, state, depth + 1) for item in children)
        minimum = value.get("minimum_should_match")
        if minimum is not None and (
            isinstance(minimum, bool) or not isinstance(minimum, int) or minimum < 0
        ):
            raise AccessRejection(
                "invalid_syntax", "minimum_should_match must be a nonnegative integer"
            )
        return count

    def _validate_aggregation_syntax(self, aggregations: Any, depth: int) -> None:
        if depth > 4 or not isinstance(aggregations, dict) or not 1 <= len(aggregations) <= 20:
            raise AccessRejection("budget_violation", "aggregation structure budget exceeded")
        for name, definition in aggregations.items():
            if not isinstance(name, str) or not name or not isinstance(definition, dict):
                raise AccessRejection("invalid_syntax", "aggregation definition is invalid")
            nested_keys = {key for key in ("aggs", "aggregations") if key in definition}
            operators = set(definition) - nested_keys
            if len(nested_keys) > 1 or len(operators) != 1:
                raise AccessRejection("invalid_syntax", "aggregation must have one operator")
            operator = next(iter(operators))
            if operator not in self.profile.allowed_aggregations or operator not in _AGGREGATIONS:
                raise AccessRejection(
                    "unsupported_node", "aggregation operator is disabled", {"operator": operator}
                )
            options = definition[operator]
            if not isinstance(options, dict):
                raise AccessRejection("invalid_syntax", "aggregation options must be an object")
            self._validate_aggregation_options(operator, options, None)
            if nested_keys:
                self._validate_aggregation_syntax(definition[next(iter(nested_keys))], depth + 1)

    def _validate_aggregations(
        self,
        aggregations: Any,
        fields: Mapping[str, Mapping[str, Any]],
        context: AccessContext,
        window_seconds: float,
        depth: int = 0,
        parent_buckets: int = 1,
    ) -> dict[str, Any]:
        max_depth = self._scope_int(context, "max_aggregation_depth", 3)
        max_buckets = self._scope_int(context, "max_aggregation_buckets", 1_000)
        if depth > max_depth:
            raise AccessRejection("budget_violation", "aggregation nesting depth exceeded")
        self._validate_aggregation_syntax(aggregations, depth)
        output: dict[str, Any] = {}
        for name, definition in aggregations.items():
            nested_key = (
                "aggs"
                if "aggs" in definition
                else "aggregations"
                if "aggregations" in definition
                else None
            )
            operator = next(key for key in definition if key not in {"aggs", "aggregations"})
            options = deepcopy(definition[operator])
            field = options.get("field")
            self._require_field(field, fields, aggregatable=True)
            buckets = 1
            if operator == "terms":
                maximum = self._scope_int(context, "max_terms_size", 50)
                cardinality = fields[field].get("cardinality")
                if (
                    isinstance(cardinality, bool)
                    or not isinstance(cardinality, int)
                    or cardinality <= 0
                ):
                    raise AccessRejection("budget_violation", "terms field cardinality is unknown")
                requested = options.get("size", 10)
                if isinstance(requested, bool) or not isinstance(requested, int) or requested <= 0:
                    raise AccessRejection("invalid_syntax", "terms size is invalid")
                options["size"] = min(requested, maximum, cardinality)
                buckets = options["size"]
            elif operator == "date_histogram":
                interval = options.get("fixed_interval")
                seconds = self._interval_seconds(interval)
                minimum = self._scope_int(context, "min_histogram_interval_seconds", 1)
                if seconds < minimum:
                    raise AccessRejection(
                        "budget_violation", "date_histogram interval is too small"
                    )
                buckets = max(1, int(window_seconds / seconds) + 1)
            elif operator == "percentiles":
                percents = options.get("percents", [50, 95, 99])
                if (
                    not isinstance(percents, list)
                    or not 1 <= len(percents) <= 10
                    or any(
                        isinstance(item, bool)
                        or not isinstance(item, (int, float))
                        or not 0 < item < 100
                        for item in percents
                    )
                ):
                    raise AccessRejection("budget_violation", "percentiles budget is invalid")
                options["percents"] = percents
                options["keyed"] = True
            if parent_buckets * buckets > max_buckets:
                raise AccessRejection("budget_violation", "aggregation bucket budget exceeded")
            row: dict[str, Any] = {operator: options}
            if nested_key is not None:
                if operator not in {"terms", "date_histogram"}:
                    raise AccessRejection(
                        "unsupported_node", "metric aggregation cannot contain buckets"
                    )
                row["aggs"] = self._validate_aggregations(
                    definition[nested_key],
                    fields,
                    context,
                    window_seconds,
                    depth + 1,
                    parent_buckets * buckets,
                )
            output[name] = row
        return output

    def _validate_aggregation_options(
        self,
        operator: str,
        options: Mapping[str, Any],
        fields: Mapping[str, Mapping[str, Any]] | None,
    ) -> None:
        allowed = {
            "terms": {"field", "size", "min_doc_count", "order"},
            "date_histogram": {"field", "fixed_interval", "min_doc_count"},
            "percentiles": {"field", "percents", "keyed"},
            "min": {"field"},
            "max": {"field"},
            "avg": {"field"},
            "sum": {"field"},
            "value_count": {"field"},
        }[operator]
        if "field" not in options or not set(options) <= allowed:
            raise AccessRejection("unsupported_node", "aggregation options are disabled")
        self._require_field(options["field"], fields, aggregatable=True)
        if "order" in options and options["order"] not in ({"_count": "desc"}, {"_key": "asc"}):
            raise AccessRejection("unsupported_node", "terms aggregation order is disabled")
        if "min_doc_count" in options and options["min_doc_count"] != 1:
            raise AccessRejection("unsupported_node", "aggregation min_doc_count must be one")
        if operator == "date_histogram":
            self._interval_seconds(options.get("fixed_interval"))

    def _effective_source_fields(
        self,
        requested: Any,
        context: AccessContext,
        fields: Mapping[str, Mapping[str, Any]],
    ) -> list[str]:
        allowed = self._string_list(
            context.scope_config.get("allowed_source_fields"), "source fields"
        )
        defaults = self._string_list(
            context.scope_config.get("default_source_fields"), "default source fields"
        )
        if not set(defaults) <= set(allowed):
            raise AccessRejection("scope_violation", "default source fields exceed snapshot scope")
        if requested is None:
            selected = defaults
        elif isinstance(requested, list) and all(isinstance(item, str) for item in requested):
            selected = [item for item in requested if item in set(allowed)]
        else:
            raise AccessRejection("unsupported_node", "search _source form is disabled")
        if not selected:
            raise AccessRejection("scope_violation", "search source projection is empty")
        for field in selected:
            self._require_field(field, fields)
        return sorted(set(selected))

    @staticmethod
    def _catalog_fields(context: AccessContext, index: str) -> Mapping[str, Mapping[str, Any]]:
        indices = context.schema_catalog.get("indices")
        entry = indices.get(index) if isinstance(indices, dict) else None
        fields = entry.get("fields") if isinstance(entry, dict) else None
        if not isinstance(fields, dict) or not fields:
            raise AccessRejection("scope_violation", "search index schema catalog is unavailable")
        for name, descriptor in fields.items():
            if (
                not isinstance(name, str)
                or not _FIELD.fullmatch(name)
                or not isinstance(descriptor, dict)
            ):
                raise AccessRejection("scope_violation", "search field catalog is invalid")
        return fields

    @staticmethod
    def _required_terms(
        context: AccessContext,
        fields: Mapping[str, Mapping[str, Any]],
    ) -> dict[str, str | int | float | bool]:
        value = context.scope_config.get("required_terms", {})
        if not isinstance(value, dict):
            raise AccessRejection("scope_violation", "required search terms are invalid")
        output: dict[str, str | int | float | bool] = {}
        for field, item in value.items():
            StructuredSearchPolicy._require_field(field, fields, searchable=True)
            StructuredSearchPolicy._require_scalar(item)
            output[field] = item
        return output

    @staticmethod
    def _require_field(
        field: Any,
        fields: Mapping[str, Mapping[str, Any]] | None,
        *,
        searchable: bool = False,
        aggregatable: bool = False,
    ) -> None:
        if not isinstance(field, str) or not _FIELD.fullmatch(field):
            raise AccessRejection("invalid_syntax", "search field name is invalid")
        if fields is None:
            return
        descriptor = fields.get(field)
        if descriptor is None:
            raise AccessRejection(
                "scope_violation", "search field is outside schema catalog", {"field": field}
            )
        if searchable and descriptor.get("searchable") is not True:
            raise AccessRejection(
                "scope_violation", "search field is not searchable", {"field": field}
            )
        if aggregatable and descriptor.get("aggregatable") is not True:
            raise AccessRejection(
                "scope_violation", "search field is not aggregatable", {"field": field}
            )

    @staticmethod
    def _require_scalar(value: Any) -> None:
        if value is None or not isinstance(value, _SCALAR_TYPES):
            raise AccessRejection("invalid_syntax", "search query value must be scalar")
        if isinstance(value, float) and not math.isfinite(value):
            raise AccessRejection("invalid_syntax", "search query number must be finite")
        if isinstance(value, str) and len(value) > 8_000:
            raise AccessRejection("budget_violation", "search query string budget exceeded")

    @staticmethod
    def _string_list(value: Any, field: str) -> list[str]:
        if (
            not isinstance(value, list)
            or not value
            or len(value) != len(set(value))
            or any(not isinstance(item, str) or not item for item in value)
        ):
            raise AccessRejection("scope_violation", f"{field} snapshot is invalid")
        return value

    @staticmethod
    def _scope_int(context: AccessContext, field: str, default: int) -> int:
        value = context.scope_config.get(field, default)
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise AccessRejection("budget_violation", f"search {field} budget is invalid")
        return value

    @staticmethod
    def _interval_seconds(value: Any) -> float:
        if not isinstance(value, str):
            raise AccessRejection("invalid_syntax", "fixed_interval is required")
        match = _INTERVAL.fullmatch(value)
        if match is None:
            raise AccessRejection("unsupported_node", "date_histogram interval is unsupported")
        return int(match.group("value")) * _INTERVAL_SECONDS[match.group("unit")]
