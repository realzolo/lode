"""Validation and bounded DNF normalization for server-owned Loki scope filters."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

_LABEL = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
_OPERATORS = {"equals", "not_equals", "any_of", "not_any_of"}
MAX_DEPTH = 3
MAX_NODES = 32
MAX_BRANCHES = 8
MAX_VALUES = 20


def normalize_loki_filter(value: Mapping[str, Any]) -> tuple[tuple[dict[str, Any], ...], ...]:
    condition_count = 0

    def normalize_branches(
        branches: Sequence[Sequence[dict[str, Any]]],
    ) -> list[list[dict[str, Any]]]:
        unique: dict[str, list[dict[str, Any]]] = {}
        for branch in branches:
            conditions = {
                json.dumps(condition, ensure_ascii=False, separators=(",", ":"), sort_keys=True): condition
                for condition in branch
            }
            normalized = [conditions[key] for key in sorted(conditions)]
            key = json.dumps(normalized, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
            unique[key] = normalized
        if len(unique) > MAX_BRANCHES:
            raise ValueError("Loki root filter expands to too many branches")
        return [unique[key] for key in sorted(unique)]

    def walk(item: Mapping[str, Any], depth: int) -> list[list[dict[str, Any]]]:
        nonlocal condition_count
        if depth > MAX_DEPTH:
            raise ValueError("Loki root filter is too complex")
        kind = item.get("kind")
        if kind == "condition":
            condition_count += 1
            if condition_count > MAX_NODES:
                raise ValueError("Loki root filter is too complex")
            label = item.get("label")
            operator = item.get("operator")
            values = item.get("values")
            if (
                not isinstance(label, str)
                or _LABEL.fullmatch(label) is None
                or operator not in _OPERATORS
                or not isinstance(values, Sequence)
                or isinstance(values, str | bytes)
                or not 1 <= len(values) <= MAX_VALUES
                or any(not isinstance(value, str) or not value or len(value) > 1_000 for value in values)
            ):
                raise ValueError("Loki root filter condition is invalid")
            if operator in {"equals", "not_equals"} and len(values) != 1:
                raise ValueError("Loki equality conditions require exactly one value")
            normalized_values = sorted(set(values))
            return [[{"label": label, "operator": operator, "values": normalized_values}]]
        if kind != "group" or item.get("combinator") not in {"all", "any"}:
            raise ValueError("Loki root filter group is invalid")
        items = item.get("items")
        if not isinstance(items, Sequence) or isinstance(items, str | bytes) or not items:
            raise ValueError("Loki root filter group must not be empty")
        children = [walk(child, depth + 1) for child in items if isinstance(child, Mapping)]
        if len(children) != len(items):
            raise ValueError("Loki root filter item is invalid")
        if item["combinator"] == "any":
            branches = normalize_branches([branch for child in children for branch in child])
        else:
            branches = [[]]
            for child in children:
                branches = normalize_branches(
                    [left + right for left in branches for right in child]
                )
        return branches

    branches = normalize_branches(walk(value, 1))
    if not branches or any(
        not any(condition["operator"] == "equals" for condition in branch)
        for branch in branches
    ):
        raise ValueError("Every Loki scope branch requires a positive equals condition")
    return tuple(tuple(condition for condition in branch) for branch in branches)


def matcher_text(condition: Mapping[str, Any]) -> str:
    label = str(condition["label"])
    operator = str(condition["operator"])
    values = condition["values"]
    if operator == "equals":
        symbol, value = "=", values[0]
    elif operator == "not_equals":
        symbol, value = "!=", values[0]
    else:
        symbol = "=~" if operator == "any_of" else "!~"
        value = "^(?:" + "|".join(re.escape(item) for item in values) + ")$"
    return f"{label}{symbol}{json.dumps(value, ensure_ascii=False)}"


def selector_for_branch(branch: Sequence[Mapping[str, Any]]) -> str:
    return "{" + ",".join(matcher_text(item) for item in branch) + "}"
