"""Tests for the lark-alert.ts dedupe-key port.

These assert the contract: the key is ``alert:{eventType}:{sha1}`` with
eventType normalization and the field-based fingerprint join exactly as the
TypeScript source computes it.
"""

from __future__ import annotations

import hashlib
import re

import pytest

from lode.consumer.dedupe import compute_dedupe_key


def test_key_shape_and_format():
    key = compute_dedupe_key(event_type="checkout_error", title="x", fields={})
    parts = key.split(":")
    assert parts[0] == "alert"
    assert parts[1] == "checkout_error"
    assert re.fullmatch(r"[0-9a-f]{40}", parts[2])


def test_event_type_normalization_collapses_non_alnum():
    # Mirrors normalizeEventType: lowercase + non [a-z0-9._-] -> '_'
    # "Checkout Error!" -> "checkout error!" -> "checkout_error_"
    key = compute_dedupe_key(event_type="Checkout Error!", title="t", fields={})
    assert key.startswith("alert:checkout_error_:")


def test_missing_event_type_uses_title():
    key = compute_dedupe_key(event_type=None, title="Order Failed", fields={})
    assert key.startswith("alert:order_failed:")


def test_empty_event_type_and_title_falls_back_to_lark_alert():
    key = compute_dedupe_key(event_type="", title="", fields={})
    assert key.startswith("alert:lark.alert:")


def test_field_fingerprint_order_and_join():
    # DEFAULT_FIELD_KEYS order: orderId, transactionId, ...
    event_type = "pay_error"
    fields = {"userId": "u_123", "orderId": "O1", "status": "failed"}
    key = compute_dedupe_key(event_type=event_type, title="t", fields=fields)
    # Recompute expected fingerprint the way lark-alert.ts does it.
    parts = [f"orderId:{fields['orderId']}", f"userId:{fields['userId']}", f"status:{fields['status']}"]
    fingerprint = "|".join(parts)
    expected = f"alert:{event_type}:{hashlib.sha1(fingerprint.encode()).hexdigest()}"
    assert key == expected


def test_boolean_dedupe_parts_use_js_string_semantics():
    # Node String(false) == 'false'; Python str(False) == 'False'.
    key = compute_dedupe_key(
        event_type="ev", title="t", fields={}, dedupe_parts=["a", False, "c"]
    )
    # fingerprint = 'a|false|c'  (only the two truthy-ish strings remain after
    # the null/empty filter; booleans are kept and stringified as 'true'/'false')
    assert key == compute_dedupe_key(
        event_type="ev", title="t", fields={}, dedupe_parts=["a", "false", "c"]
    )


def test_deterministic():
    args = dict(event_type="ev", title="t", fields={"orderId": "1"})
    assert compute_dedupe_key(**args) == compute_dedupe_key(**args)
