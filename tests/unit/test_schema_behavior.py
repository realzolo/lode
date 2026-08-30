"""Database behavior checker tests."""

from __future__ import annotations

from lode.contracts.schema_behavior import _BEHAVIOR_SQL


def test_database_behavior_check_is_rollback_safe_and_covers_critical_triggers() -> None:
    assert "DROP " not in _BEHAVIOR_SQL
    assert "TRUNCATE " not in _BEHAVIOR_SQL
    assert "updated_at trigger did not advance" in _BEHAVIOR_SQL
    assert "secret-free config trigger accepted" in _BEHAVIOR_SQL
    assert "immutable trigger accepted" in _BEHAVIOR_SQL
    assert "incident occurrence trigger accepted" in _BEHAVIOR_SQL
