"""Structured-value helpers shared by native policies."""

from __future__ import annotations

from collections.abc import Iterable
from copy import deepcopy
from typing import Any

from lode.application.intake import canonical_hash
from lode.evidence_access.types import AccessRejection

TreePath = tuple[str | int, ...]


def walk_values(value: Any, path: TreePath = ()) -> Iterable[tuple[TreePath, Any]]:
    if isinstance(value, dict):
        for key, item in value.items():
            yield from walk_values(item, (*path, key))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from walk_values(item, (*path, index))
    else:
        yield path, value


def tree_shape(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: tree_shape(item) for key, item in sorted(value.items())}
    if isinstance(value, list):
        return [tree_shape(item) for item in value]
    if value is None:
        return "null"
    return type(value).__name__


def structural_hash(value: Any) -> str:
    return canonical_hash(tree_shape(value))


def find_exact_value_slots(value: Any, sentinels: set[str]) -> dict[str, TreePath]:
    slots: dict[str, TreePath] = {}
    for path, item in walk_values(value):
        if isinstance(item, str) and item in sentinels:
            if item in slots:
                raise AccessRejection("invalid_syntax", "sentinel appears in multiple value nodes")
            slots[item] = path
        elif isinstance(item, str) and any(sentinel in item for sentinel in sentinels):
            raise AccessRejection("invalid_syntax", "sentinel must occupy a complete value node")
    if set(slots) != sentinels:
        raise AccessRejection("invalid_syntax", "every binding sentinel must occupy one value node")
    return slots


def bind_exact_values(
    value: Any,
    slots: dict[str, TreePath],
    replacements: dict[str, str],
) -> Any:
    if set(slots) != set(replacements):
        raise AccessRejection("scope_violation", "resolved values do not match parsed slots")
    bound = deepcopy(value)
    for sentinel, path in slots.items():
        target = bound
        for part in path[:-1]:
            target = target[part]
        target[path[-1]] = replacements[sentinel]
    return bound
