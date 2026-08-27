"""Strict LogQL policy backed by Grafana's maintained Lezer grammar."""

from __future__ import annotations

import json
import math
import re
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC
from pathlib import Path
from typing import Any

from lode.application.intake import canonical_hash
from lode.evidence_access.budget import intersect_budget
from lode.evidence_access.candidate import NativeReadCandidateInput, QueryPayload
from lode.evidence_access.loki_scope import matcher_text
from lode.evidence_access.types import (
    AccessContext,
    AccessRejection,
    BoundNativeAction,
    ParsedNativeAction,
    PolicyEvaluation,
)
from lode.runtime_defaults import LOGQL_PARSER_NODE, LOGQL_PARSER_TIMEOUT_SECONDS

_PARSER_VERSION = "@grafana/lezer-logql@0.4.1"
_BASE_NODES = {
    "LogQL",
    "Expr",
    "LogExpr",
    "Selector",
    "Matchers",
    "Matcher",
    "Identifier",
    "Eq",
    "String",
    "PipelineExpr",
    "PipelineStage",
    "LineFilters",
    "LineFilter",
    "Filter",
    "PipeExact",
    "PipeNotEqual",
    "Pipe",
    "LabelParser",
    "Json",
    "Logfmt",
    "LabelFilter",
    "NumberFilter",
    "StringLabelFilter",
    "DurationFilter",
    "BytesFilter",
    "LiteralExpr",
    "Number",
    "Duration",
    "Bytes",
    "EqEq",
    "Neq",
    "Gt",
    "Gte",
    "Lt",
    "Lte",
    "And",
    "Or",
    "ParenLabelFilter",
}
_METRIC_NODES = {
    "MetricExpr",
    "RangeAggregationExpr",
    "RangeOp",
    "CountOverTime",
    "Rate",
    "BytesOverTime",
    "BytesRate",
    "LogRangeExpr",
    "Range",
    "VectorAggregationExpr",
    "VectorOp",
    "Sum",
    "Count",
    "Avg",
    "Min",
    "Max",
    "Grouping",
    "By",
    "Labels",
}
_FORBIDDEN_NODES = {
    "LineFormatExpr",
    "LabelFormatExpr",
    "DropLabelsExpr",
    "KeepLabelsExpr",
    "DecolorizeExpr",
    "UnwrapExpr",
    "OffsetExpr",
    "BinOpExpr",
    "LabelReplaceExpr",
    "MultiVariantExpr",
    "JsonExpressionParser",
    "LogfmtExpressionParser",
    "Pattern",
    "Regexp",
    "PipeMatch",
    "PipeNotMatch",
    "PipePattern",
    "PipeNotPattern",
    "Re",
    "Nre",
}
_DURATION = re.compile(r"^(?P<value>[1-9][0-9]*)(?P<unit>ms|s|m|h|d|w|y)$")
_DURATION_SECONDS = {
    "ms": 0.001,
    "s": 1,
    "m": 60,
    "h": 3600,
    "d": 86400,
    "w": 604800,
    "y": 31536000,
}
_LABEL = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _default_script() -> Path:
    return Path(__file__).resolve().parents[3] / "tools" / "logql_parser" / "parser.mjs"


@dataclass(frozen=True, slots=True)
class LogQLSyntax:
    query_kind: str
    structural_tree: str
    node_counts: Mapping[str, int]
    strings: tuple[Mapping[str, Any], ...]
    terminals: tuple[Mapping[str, Any], ...]
    selectors: tuple[Mapping[str, Any], ...]
    matchers: tuple[Mapping[str, Any], ...]
    line_filters: tuple[Mapping[str, Any], ...]
    pipeline_stages: tuple[Mapping[str, Any], ...]


class LogQLParser:
    def parse(self, query: str) -> LogQLSyntax:
        script = _default_script()
        if not script.is_file():
            raise AccessRejection("unsupported_node", "LogQL parser helper is unavailable")
        try:
            process = subprocess.run(
                [LOGQL_PARSER_NODE, str(script)],
                input=json.dumps({"query": query}, ensure_ascii=False).encode(),
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=LOGQL_PARSER_TIMEOUT_SECONDS,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise AccessRejection("unsupported_node", "LogQL parser helper failed closed") from exc
        if process.returncode != 0 or len(process.stdout) > 512 * 1024:
            raise AccessRejection("invalid_syntax", "LogQL parser rejected the query")
        try:
            result = json.loads(process.stdout)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AccessRejection(
                "unsupported_node", "LogQL parser returned invalid output"
            ) from exc
        if result.get("parser_version") != _PARSER_VERSION:
            raise AccessRejection("unsupported_node", "LogQL parser version mismatch")
        if not result.get("ok") or result.get("errors"):
            raise AccessRejection("invalid_syntax", "LogQL query was not completely parsed")
        return LogQLSyntax(
            query_kind=result["query_kind"],
            structural_tree=result["structural_tree"],
            node_counts=dict(result["node_counts"]),
            strings=tuple(result["strings"]),
            terminals=tuple(result["terminals"]),
            selectors=tuple(result["selectors"]),
            matchers=tuple(result["matchers"]),
            line_filters=tuple(result["line_filters"]),
            pipeline_stages=tuple(result["pipeline_stages"]),
        )


class LogQLPolicy:
    language = "logql"
    parser_name = "grafana-lezer-logql"
    parser_version = _PARSER_VERSION
    policy_version = "lode-logql-policy.2"

    def __init__(self, parser: LogQLParser | None = None) -> None:
        self._parser = parser or LogQLParser()

    def parse(self, candidate: NativeReadCandidateInput) -> ParsedNativeAction:
        if not isinstance(candidate.payload, QueryPayload):
            raise AccessRejection("invalid_syntax", "LogQL requires a query payload")
        syntax = self._parser.parse(candidate.payload.query)
        self._validate_syntax(syntax)
        slots = self._value_slots(syntax, set(candidate.value_bindings))
        action = {"query": candidate.payload.query, "query_kind": syntax.query_kind}
        return ParsedNativeAction(
            language=self.language,
            canonical_action=action,
            parse_tree_hash=canonical_hash(
                {"query": candidate.payload.query, "tree": syntax.structural_tree}
            ),
            structural_hash=canonical_hash(syntax.structural_tree),
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
            raise AccessRejection("budget_violation", "LogQL requires an absolute bounded window")
        syntax = self._parser.parse(str(action.canonical_action["query"]))
        self._validate_scope(syntax, context)
        queries: list[str] = []
        effective_syntaxes: list[LogQLSyntax] = []
        branches = self._root_branches(context)
        for branch in branches:
            query, _ = self._inject_root_matchers(
                str(action.canonical_action["query"]), syntax, branch
            )
            effective_syntax = self._parser.parse(query)
            self._validate_syntax(effective_syntax, allow_generated_matchers=True)
            self._validate_scope(effective_syntax, context, root_branch=branch)
            queries.append(query)
            effective_syntaxes.append(effective_syntax)

        duration = (budget.window_end - budget.window_start).total_seconds()
        metric = effective_syntaxes[0].query_kind == "metric"
        max_samples = self._scope_int(context, "max_metric_samples", 2_000)
        step_seconds = max(1, math.ceil(duration / max_samples)) if metric else None
        if metric and not bool(context.scope_config.get("allow_metric_queries", False)):
            raise AccessRejection("scope_violation", "metric LogQL is disabled by the snapshot")
        diff["root_filter"] = {"branch_count": len(branches), "injected": True}
        effective_action = {
            "adapter_kind": "loki",
            "queries": queries,
            "query_kind": effective_syntaxes[0].query_kind,
            "start": budget.window_start.astimezone(UTC).isoformat(),
            "end": budget.window_end.astimezone(UTC).isoformat(),
            "limit": budget.result_limit,
            "direction": "forward",
            "step_seconds": step_seconds,
            "timeout_ms": budget.timeout_ms,
        }
        return PolicyEvaluation(
            effective_action=effective_action,
            effective_structural_hash=self._effective_structure(effective_action, effective_syntaxes),
            validation_decisions=(
                {"check": "logql_complete_cst", "outcome": "allow", "parser": self.parser_version},
                {"check": "logql_node_allowlist", "outcome": "allow"},
                {"check": "logql_root_scope", "outcome": "allow"},
                {"check": "logql_absolute_budget", "outcome": "allow"},
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
        effective = dict(evaluation.effective_action)
        queries = effective.get("queries")
        if not isinstance(queries, list) or not queries:
            raise AccessRejection("scope_violation", "effective Loki branch set is invalid")
        bound_queries: list[str] = []
        bound_syntaxes: list[LogQLSyntax] = []
        for raw_query in queries:
            query = str(raw_query)
            syntax = self._parser.parse(query)
            spans = self._string_spans(syntax, set(values))
            if set(spans) != set(values):
                raise AccessRejection("scope_violation", "resolved values do not match LogQL slots")
            for sentinel, (start, end) in sorted(
                spans.items(), key=lambda item: item[1][0], reverse=True
            ):
                query = query[:start] + json.dumps(values[sentinel], ensure_ascii=False) + query[end:]
            bound_syntax = self._parser.parse(query)
            self._validate_syntax(bound_syntax, allow_generated_matchers=True)
            bound_queries.append(query)
            bound_syntaxes.append(bound_syntax)
        effective["queries"] = bound_queries
        shape = self._effective_structure(effective, bound_syntaxes)
        if shape != evaluation.effective_structural_hash:
            raise AccessRejection("invalid_syntax", "ValueRef binding changed LogQL structure")
        return BoundNativeAction(
            language=self.language,
            canonical_action=effective,
            structural_hash=shape,
            parse_tree_hash=canonical_hash(
                {"queries": bound_queries, "trees": [item.structural_tree for item in bound_syntaxes]}
            ),
        )

    @staticmethod
    def _validate_syntax(
        syntax: LogQLSyntax, *, allow_generated_matchers: bool = False
    ) -> None:
        if len(syntax.selectors) != 1:
            raise AccessRejection("unsupported_node", "LogQL must contain exactly one selector")
        nodes = set(syntax.node_counts)
        forbidden = _FORBIDDEN_NODES - ({"Re", "Nre"} if allow_generated_matchers else set())
        if nodes & forbidden:
            raise AccessRejection(
                "unsupported_node",
                "LogQL contains a disabled AST node",
                {"nodes": sorted(nodes & forbidden)},
            )
        allowed = _BASE_NODES | (_METRIC_NODES if syntax.query_kind == "metric" else set())
        if allow_generated_matchers:
            allowed |= {"Re", "Nre"}
        unknown = nodes - allowed
        if unknown:
            raise AccessRejection(
                "unsupported_node", "LogQL contains an unknown AST node", {"nodes": sorted(unknown)}
            )
        if sum(syntax.node_counts.values()) > 512:
            raise AccessRejection("budget_violation", "LogQL AST node budget exceeded")
        if any(item.get("encoding") != "json" for item in syntax.strings):
            raise AccessRejection(
                "invalid_syntax", "LogQL strings must use JSON-compatible quoting"
            )
        matcher_operators = {"Eq", "Neq", "Re", "Nre"} if allow_generated_matchers else {"Eq"}
        if any(item.get("operator") not in matcher_operators for item in syntax.matchers):
            raise AccessRejection("unsupported_node", "LogQL selector operator is disabled")
        if any(
            item.get("operator") not in {"PipeExact", "PipeNotEqual"}
            for item in syntax.line_filters
        ):
            raise AccessRejection("unsupported_node", "LogQL line-filter operator is disabled")

    def _validate_scope(
        self,
        syntax: LogQLSyntax,
        context: AccessContext,
        *,
        root_branch: tuple[Mapping[str, Any], ...] | None = None,
    ) -> None:
        root_labels = self._root_labels(context)
        allowed_labels = self._string_set(context.schema_catalog.get("labels"), "schema labels")
        allowed_fields = self._string_set(context.schema_catalog.get("fields", []), "schema fields")
        for matcher in syntax.matchers:
            if matcher.get("name") not in allowed_labels | root_labels:
                raise AccessRejection(
                    "scope_violation", "LogQL selector label is outside schema catalog"
                )
        identifiers = {
            item["text"] for item in syntax.terminals if item.get("name") == "Identifier"
        }
        if not identifiers <= allowed_labels | allowed_fields | root_labels:
            raise AccessRejection("scope_violation", "LogQL field is outside schema catalog")
        if root_branch is not None:
            required = {
                (
                    item["label"],
                    {"equals": "Eq", "not_equals": "Neq", "any_of": "Re", "not_any_of": "Nre"}[item["operator"]],
                    item["values"][0]
                    if item["operator"] in {"equals", "not_equals"}
                    else "^(?:" + "|".join(re.escape(value) for value in item["values"]) + ")$",
                )
                for item in root_branch
            }
            present = {
                (
                    item.get("name"),
                    item.get("operator"),
                    (item.get("value") or {}).get("value"),
                )
                for item in syntax.matchers
            }
            if not required <= present:
                raise AccessRejection("scope_violation", "LogQL root selector was not enforced")
        stages = set()
        for stage in syntax.pipeline_stages:
            nodes = set(stage.get("nodes", []))
            if "LineFilter" in nodes:
                stages.add("line_filter")
            if "Json" in nodes:
                stages.add("json")
            if "Logfmt" in nodes:
                stages.add("logfmt")
            if "LabelFilter" in nodes:
                stages.add("label_filter")
        allowed_stages = self._string_set(
            context.scope_config.get("allowed_pipeline_stages", ["line_filter"]),
            "allowed pipeline stages",
        )
        if not stages <= allowed_stages:
            raise AccessRejection(
                "scope_violation", "LogQL pipeline stage is outside snapshot capability"
            )
        if syntax.query_kind == "metric":
            max_range = self._scope_int(context, "max_metric_range_seconds", 900)
            for item in syntax.terminals:
                if (
                    item.get("name") == "Duration"
                    and self._duration_seconds(item["text"]) > max_range
                ):
                    raise AccessRejection(
                        "budget_violation", "LogQL metric range exceeds snapshot budget"
                    )
            grouping_count = int(syntax.node_counts.get("Grouping", 0))
            if grouping_count > self._scope_int(context, "max_grouping_depth", 1):
                raise AccessRejection(
                    "budget_violation", "LogQL grouping depth exceeds snapshot budget"
                )

    def _inject_root_matchers(
        self,
        query: str,
        syntax: LogQLSyntax,
        branch: tuple[Mapping[str, Any], ...],
    ) -> tuple[str, list[str]]:
        present = {
            (item.get("name"), (item.get("value") or {}).get("value"))
            for item in syntax.matchers
            if item.get("operator") == "Eq"
        }
        missing = [
            item for item in branch
            if item["operator"] != "equals" or (item["label"], item["values"][0]) not in present
        ]
        if not missing:
            return query, []
        selector = syntax.selectors[0]
        position = int(selector["from"]) + 1
        prefix = ",".join(matcher_text(item) for item in missing)
        existing = query[position : int(selector["to"])].lstrip().startswith("}")
        separator = "" if existing else ","
        return query[:position] + prefix + separator + query[position:], [
            str(item["label"]) for item in missing
        ]

    def _root_branches(
        self, context: AccessContext
    ) -> tuple[tuple[Mapping[str, Any], ...], ...]:
        value = context.scope_config.get("root_filter_dnf")
        if not isinstance(value, list) or not 1 <= len(value) <= 8:
            raise AccessRejection("scope_violation", "LogQL snapshot has no root filter")
        branches: list[tuple[Mapping[str, Any], ...]] = []
        for branch in value:
            if not isinstance(branch, list) or not branch:
                raise AccessRejection("scope_violation", "LogQL root filter branch is invalid")
            branches.append(tuple(branch))
        return tuple(branches)

    def _root_labels(self, context: AccessContext) -> set[str]:
        labels = {
            str(item.get("label"))
            for branch in self._root_branches(context)
            for item in branch
            if isinstance(item, Mapping)
        }
        if any(_LABEL.fullmatch(label) is None for label in labels):
            raise AccessRejection("scope_violation", "LogQL root filter label is invalid")
        return labels

    def _value_slots(
        self,
        syntax: LogQLSyntax,
        sentinels: set[str],
    ) -> dict[str, tuple[str | int, ...]]:
        spans = self._string_spans(syntax, sentinels)
        if set(spans) != sentinels:
            raise AccessRejection(
                "invalid_syntax", "every LogQL sentinel must occupy one string node"
            )
        return {sentinel: ("query", start, end) for sentinel, (start, end) in spans.items()}

    @staticmethod
    def _string_spans(
        syntax: LogQLSyntax,
        sentinels: set[str],
    ) -> dict[str, tuple[int, int]]:
        spans: dict[str, tuple[int, int]] = {}
        for item in syntax.strings:
            value = item.get("value")
            if value in sentinels:
                if value in spans:
                    raise AccessRejection(
                        "invalid_syntax", "sentinel appears in multiple LogQL nodes"
                    )
                parents = set(item.get("parents", []))
                if not parents & {"Matcher", "LineFilter", "StringLabelFilter"}:
                    raise AccessRejection(
                        "invalid_syntax", "sentinel is not in an allowed LogQL value node"
                    )
                spans[value] = (int(item["from"]), int(item["to"]))
            elif isinstance(value, str) and any(sentinel in value for sentinel in sentinels):
                raise AccessRejection(
                    "invalid_syntax", "sentinel must occupy a complete LogQL string node"
                )
        return spans

    @staticmethod
    def _effective_structure(
        action: Mapping[str, Any], syntaxes: list[LogQLSyntax]
    ) -> str:
        shape = {
            key: ([item.structural_tree for item in syntaxes] if key == "queries" else type(value).__name__)
            for key, value in sorted(action.items())
        }
        return canonical_hash(shape)

    @staticmethod
    def _string_set(value: Any, field: str) -> set[str]:
        if not isinstance(value, list) or any(
            not isinstance(item, str) or not item for item in value
        ):
            raise AccessRejection("scope_violation", f"{field} is invalid")
        return set(value)

    @staticmethod
    def _scope_int(context: AccessContext, field: str, default: int) -> int:
        value = context.scope_config.get(field, default)
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise AccessRejection("budget_violation", f"LogQL {field} is invalid")
        return value

    @staticmethod
    def _duration_seconds(value: str) -> float:
        match = _DURATION.fullmatch(value)
        if match is None:
            raise AccessRejection("invalid_syntax", "LogQL duration is unsupported")
        return int(match.group("value")) * _DURATION_SECONDS[match.group("unit")]
