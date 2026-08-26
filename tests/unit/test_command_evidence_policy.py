from __future__ import annotations

from datetime import UTC, datetime

import pytest

from lode.evidence_access.candidate import NativeReadCandidateInput
from lode.evidence_access.command import CommandPolicy
from lode.evidence_access.types import AccessContext, AccessRejection


def candidate(
    *,
    executable: str = "/usr/bin/rg",
    argv: list[str] | None = None,
    bindings: dict[str, str] | None = None,
    working_set_id: str = "orders-repo",
) -> NativeReadCandidateInput:
    return NativeReadCandidateInput.model_validate(
        {
            "schema_version": "native-read-candidate.v1",
            "action_id": "evidence.command.search",
            "connector_id": 9,
            "language": "command",
            "purpose": "find the trace in frozen source files",
            "expected_evidence": "exact matching source lines",
            "evidence_anchors": ["incident.trace_id"],
            "payload": {
                "executable": executable,
                "argv": argv or ["--fixed-strings", "--", "trace", "src/orders.py"],
                "working_set_id": working_set_id,
            },
            "value_bindings": bindings or {},
            "requested_window": None,
            "requested_limit": 100,
            "requested_timeout_ms": 30_000,
        }
    )


def context() -> AccessContext:
    return AccessContext(
        investigation_id=1,
        operation_id=2,
        connector_snapshot_id=3,
        model_invocation_id=4,
        workspace_id=5,
        connector_id=9,
        snapshot_hash="a" * 64,
        allowed_languages=("command",),
        allowed_evidence_anchors=("incident.trace_id",),
        scope_config={
            "command_capabilities": {
                "rg_fixed_search": {
                    "executable": "/usr/bin/rg",
                    "binary_sha256": "b" * 64,
                }
            }
        },
        schema_catalog={
            "working_sets": {
                "orders-repo": {
                    "root": "/worksets/orders-repo",
                    "files": ["src/orders.py", "tests/test_orders.py"],
                    "revision": "c" * 40,
                }
            }
        },
        execution_budget_policy={
            "max_result_limit": 20,
            "max_timeout_ms": 20_000,
            "max_output_bytes": 50_000,
            "max_total_output_bytes": 100_000,
            "max_native_reads": 8,
        },
        investigation_window_start=datetime(2026, 8, 26, 9, 0, tzinfo=UTC),
        investigation_window_end=datetime(2026, 8, 26, 10, 0, tzinfo=UTC),
    )


def test_command_policy_builds_server_owned_rg_argv_and_binds_literal_pattern() -> None:
    policy = CommandPolicy()
    sentinel = "__LODE_VALUE_REF_INCIDENT_TRACE__"
    raw = candidate(
        argv=["--fixed-strings", "--", sentinel, "src/orders.py"],
        bindings={sentinel: "incident.trace_id"},
    )
    parsed = policy.parse(raw)
    evaluated = policy.evaluate(parsed, raw, context())
    bound = policy.bind_values(parsed, evaluated, {sentinel: "--glob=**; rm -rf /"})

    action = evaluated.effective_action
    assert action["argv"][:7] == [
        "--fixed-strings",
        "--line-number",
        "--no-heading",
        "--color=never",
        "--no-config",
        "--max-count=20",
        "--",
    ]
    assert bound.canonical_action["argv"][7] == "--glob=**; rm -rf /"
    assert bound.canonical_action["argv"][8:] == ["src/orders.py"]
    assert action["timeout_ms"] == 15_000
    assert bound.structural_hash == evaluated.effective_structural_hash


@pytest.mark.parametrize(
    "executable,argv,working_set,code",
    [
        (
            "/bin/sh",
            ["--fixed-strings", "--", "x", "src/orders.py"],
            "orders-repo",
            "unsupported_node",
        ),
        ("/usr/bin/rg", ["-e", "x", "src/orders.py", "extra"], "orders-repo", "unsupported_node"),
        (
            "/usr/bin/rg",
            ["--fixed-strings", "--", "x", "../secret"],
            "orders-repo",
            "sandbox_violation",
        ),
        (
            "/usr/bin/rg",
            ["--fixed-strings", "--", "x", "/etc/passwd"],
            "orders-repo",
            "sandbox_violation",
        ),
        (
            "/usr/bin/rg",
            ["--fixed-strings", "--", "x", "-g=*.py"],
            "orders-repo",
            "sandbox_violation",
        ),
        (
            "/usr/bin/rg",
            ["--fixed-strings", "--", "x\n--hidden", "src/orders.py"],
            "orders-repo",
            "invalid_syntax",
        ),
        (
            "/usr/bin/rg",
            ["--fixed-strings", "--", "x", "src/orders.py"],
            "../escape",
            "sandbox_violation",
        ),
    ],
)
def test_command_policy_rejects_shell_flag_and_path_bypass(
    executable: str, argv: list[str], working_set: str, code: str
) -> None:
    with pytest.raises(AccessRejection) as error:
        CommandPolicy().parse(
            candidate(executable=executable, argv=argv, working_set_id=working_set)
        )
    assert error.value.code == code


def test_command_policy_rejects_file_outside_frozen_working_set() -> None:
    policy = CommandPolicy()
    raw = candidate(argv=["--fixed-strings", "--", "trace", "README.md"])
    parsed = policy.parse(raw)
    with pytest.raises(AccessRejection) as error:
        policy.evaluate(parsed, raw, context())
    assert error.value.code == "sandbox_violation"
