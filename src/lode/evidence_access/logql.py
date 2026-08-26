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
from lode.config import settings
from lode.evidence_access.budget import intersect_budget
from lode.evidence_access.candidate import NativeReadCandidateInput, QueryPayload
from lode.evidence_access.types import (
    AccessContext,
    AccessRejection,
    BoundNativeAction,
    ParsedNativeAction,
    PolicyEvaluation,
)

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
        script = (
            Path(settings.logql_parser_script)
            if settings.logql_parser_script
            else _default_script()
        )
        if not script.is_file():
            raise AccessRejection("unsupported_node", "LogQL parser helper is unavailable")
        if settings.logql_parser_timeout_seconds <= 0:
            raise AccessRejection("unsupported_node", "LogQL parser timeout is invalid")
        try:
            process = subprocess.run(
                [settings.logql_parser_node, str(script)],
                input=json.dumps({"query": query}, ensure_ascii=False).encode(),
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=settings.logql_parser_timeout_seconds,
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
    policy_version = "lode-logql-policy.1"

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
        query, injected = self._inject_root_matchers(
            str(action.canonical_action["query"]), syntax, context
        )
        effective_syntax = self._parser.parse(query)
        self._validate_syntax(effective_syntax)
        self._validate_scope(effective_syntax, context, require_roots=True)

        duration = (budget.window_end - budget.window_start).total_seconds()
        metric = effective_syntax.query_kind == "metric"
        max_samples = self._scope_int(context, "max_metric_samples", 2_000)
        step_seconds = max(1, math.ceil(duration / max_samples)) if metric else None
        if metric and not bool(context.scope_config.get("allow_metric_queries", False)):
            raise AccessRejection("scope_violation", "metric LogQL is disabled by the snapshot")
        if injected:
            diff["root_matchers"] = {"injected": injected}
        effective_action = {
            "adapter_kind": "loki",
            "query": query,
            "query_kind": effective_syntax.query_kind,
            "start": budget.window_start.astimezone(UTC).isoformat(),
            "end": budget.window_end.astimezone(UTC).isoformat(),
            "limit": budget.result_limit,
            "direction": "forward",
            "step_seconds": step_seconds,
            "timeout_ms": budget.timeout_ms,
        }
        return PolicyEvaluation(
            effective_action=effective_action,
            effective_structural_hash=self._effective_structure(effective_action, effective_syntax),
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
        query = str(effective["query"])
        syntax = self._parser.parse(query)
        spans = self._string_spans(syntax, set(values))
        if set(spans) != set(values):
            raise AccessRejection("scope_violation", "resolved values do not match LogQL slots")
        for sentinel, (start, end) in sorted(
            spans.items(), key=lambda item: item[1][0], reverse=True
        ):
            query = query[:start] + json.dumps(values[sentinel], ensure_ascii=False) + query[end:]
        bound_syntax = self._parser.parse(query)
        self._validate_syntax(bound_syntax)
        effective["query"] = query
        shape = self._effective_structure(effective, bound_syntax)
        if shape != evaluation.effective_structural_hash:
            raise AccessRejection("invalid_syntax", "ValueRef binding changed LogQL structure")
        return BoundNativeAction(
            language=self.language,
            canonical_action=effective,
            structural_hash=shape,
            parse_tree_hash=canonical_hash({"query": query, "tree": bound_syntax.structural_tree}),
        )

    @staticmethod
    def _validate_syntax(syntax: LogQLSyntax) -> None:
        if len(syntax.selectors) != 1:
            raise AccessRejection("unsupported_node", "LogQL must contain exactly one selector")
        nodes = set(syntax.node_counts)
        if nodes & _FORBIDDEN_NODES:
            raise AccessRejection(
                "unsupported_node",
                "LogQL contains a disabled AST node",
                {"nodes": sorted(nodes & _FORBIDDEN_NODES)},
            )
        allowed = _BASE_NODES | (_METRIC_NODES if syntax.query_kind == "metric" else set())
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
        if any(item.get("operator") not in {"Eq"} for item in syntax.matchers):
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
        require_roots: bool = False,
    ) -> None:
        roots = self._root_matchers(context)
        allowed_labels = self._string_set(context.schema_catalog.get("labels"), "schema labels")
        allowed_fields = self._string_set(context.schema_catalog.get("fields", []), "schema fields")
        for matcher in syntax.matchers:
            if matcher.get("name") not in allowed_labels | set(roots):
                raise AccessRejection(
                    "scope_violation", "LogQL selector label is outside schema catalog"
                )
        identifiers = {
            item["text"] for item in syntax.terminals if item.get("name") == "Identifier"
        }
        if not identifiers <= allowed_labels | allowed_fields:
            raise AccessRejection("scope_violation", "LogQL field is outside schema catalog")
        if require_roots:
            present = {
                (item.get("name"), (item.get("value") or {}).get("value"))
                for item in syntax.matchers
                if item.get("operator") == "Eq"
            }
            missing = {(name, value) for name, value in roots.items()} - present
            if missing:
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
        context: AccessContext,
    ) -> tuple[str, list[str]]:
        roots = self._root_matchers(context)
        present = {
            (item.get("name"), (item.get("value") or {}).get("value"))
            for item in syntax.matchers
            if item.get("operator") == "Eq"
        }
        missing = [(name, value) for name, value in roots.items() if (name, value) not in present]
        if not missing:
            return query, []
        selector = syntax.selectors[0]
        position = int(selector["from"]) + 1
        prefix = ",".join(
            f"{name}={json.dumps(value, ensure_ascii=False)}" for name, value in missing
        )
        existing = query[position : int(selector["to"])].lstrip().startswith("}")
        separator = "" if existing else ","
        return query[:position] + prefix + separator + query[position:], [
            name for name, _ in missing
        ]

    def _root_matchers(self, context: AccessContext) -> dict[str, str]:
        value = context.scope_config.get("root_matchers")
        if not isinstance(value, dict) or not value:
            raise AccessRejection("scope_violation", "LogQL snapshot has no root matcher")
        if any(not isinstance(key, str) or not _LABEL.fullmatch(key) for key in value):
            raise AccessRejection("scope_violation", "LogQL root matcher label is invalid")
        if any(not isinstance(item, str) or not item for item in value.values()):
            raise AccessRejection("scope_violation", "LogQL root matcher value is invalid")
        return dict(value)

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
    def _effective_structure(action: Mapping[str, Any], syntax: LogQLSyntax) -> str:
        shape = {
            key: (syntax.structural_tree if key == "query" else type(value).__name__)
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
