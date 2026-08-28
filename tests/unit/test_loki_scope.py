from __future__ import annotations

import pytest

from lode.evidence_access.loki_scope import normalize_loki_filter, selector_for_branch


def condition(label: str, operator: str, *values: str) -> dict:
    return {"kind": "condition", "label": label, "operator": operator, "values": list(values)}


def group(combinator: str, *items: dict) -> dict:
    return {"kind": "group", "combinator": combinator, "items": list(items)}


def test_loki_filter_normalizes_nested_groups_to_stable_dnf() -> None:
    value = group(
        "all",
        condition("cluster", "equals", "prod"),
        group(
            "any",
            condition("namespace", "equals", "orders"),
            condition("namespace", "equals", "billing"),
        ),
        condition("app", "any_of", "worker.*", "api+", "api+"),
    )

    branches = normalize_loki_filter(value)

    assert len(branches) == 2
    assert branches == normalize_loki_filter(value)
    selectors = [selector_for_branch(branch) for branch in branches]
    assert all('cluster="prod"' in selector for selector in selectors)
    assert all('app=~"^(?:api\\\\+|worker\\\\.\\\\*)$"' in selector for selector in selectors)
    assert any('namespace="orders"' in selector for selector in selectors)
    assert any('namespace="billing"' in selector for selector in selectors)


def test_loki_filter_accepts_any_of_as_a_positive_exact_matcher() -> None:
    branches = normalize_loki_filter(group("all", condition("app", "any_of", "payments", "orders")))

    assert branches == (({"label": "app", "operator": "any_of", "values": ["orders", "payments"]},),)


@pytest.mark.parametrize(
    "value,reason",
    [
        (
            group("all", condition("cluster", "not_equals", "dev")),
            "positive exact matcher",
        ),
        (
            group(
                "all",
                condition("cluster", "equals", "prod"),
                group("all", group("all", condition("app", "equals", "api"))),
            ),
            "too complex",
        ),
        (
            group(
                "all",
                condition("cluster", "equals", "prod"),
                group(
                    "any",
                    *(condition("namespace", "equals", str(index)) for index in range(9)),
                ),
            ),
            "too many branches",
        ),
        (
            group(
                "all",
                condition("cluster", "equals", "prod"),
                *(condition(f"label_{index}", "not_equals", "x") for index in range(32)),
            ),
            "too complex",
        ),
    ],
)
def test_loki_filter_rejects_unsafe_or_excessive_trees(value: dict, reason: str) -> None:
    with pytest.raises(ValueError, match=reason):
        normalize_loki_filter(value)


def test_loki_filter_allows_exactly_32_conditions_and_20_values() -> None:
    value = group(
        "all",
        condition("cluster", "equals", "prod"),
        condition("app", "any_of", *(str(index) for index in range(20))),
        *(condition(f"label_{index}", "not_equals", "x") for index in range(30)),
    )

    branches = normalize_loki_filter(value)

    assert len(branches) == 1
    assert len(branches[0]) == 32
