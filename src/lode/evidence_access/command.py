"""Positive argv grammar for the isolated read-only command runner."""

from __future__ import annotations

import re
from collections.abc import Mapping
from copy import deepcopy
from pathlib import PurePosixPath

from lode.application.intake import canonical_hash
from lode.evidence_access.budget import intersect_budget
from lode.evidence_access.candidate import CommandPayload, NativeReadCandidateInput
from lode.evidence_access.tree import bind_exact_values, find_exact_value_slots, structural_hash
from lode.evidence_access.types import (
    AccessContext,
    AccessRejection,
    BoundNativeAction,
    ParsedNativeAction,
    PolicyEvaluation,
)

_WORKING_SET = re.compile(r"^[a-z0-9][a-z0-9_-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class CommandPolicy:
    language = "command"
    parser_name = "lode-argv-grammar"
    parser_version = "rg-fixed-search.1"
    policy_version = "isolated-command.1"

    def parse(self, candidate: NativeReadCandidateInput) -> ParsedNativeAction:
        if not isinstance(candidate.payload, CommandPayload):
            raise AccessRejection("invalid_syntax", "command requires executable and argv")
        payload = candidate.payload
        if payload.executable != "/usr/bin/rg":
            raise AccessRejection("unsupported_node", "command executable is not permitted")
        if _WORKING_SET.fullmatch(payload.working_set_id) is None:
            raise AccessRejection("sandbox_violation", "command working set ID is invalid")
        if len(payload.argv) < 4 or payload.argv[:2] != ["--fixed-strings", "--"]:
            raise AccessRejection("unsupported_node", "rg argv does not match the fixed grammar")
        pattern = payload.argv[2]
        files = payload.argv[3:]
        if not pattern or "\x00" in pattern or "\n" in pattern or "\r" in pattern:
            raise AccessRejection("invalid_syntax", "rg pattern is invalid")
        if len(files) != 1 or any(not self._relative_file(path) for path in files):
            raise AccessRejection("sandbox_violation", "rg file list is invalid")
        action = {
            "profile": "rg_fixed_search",
            "executable": payload.executable,
            "argv": list(payload.argv),
            "working_set_id": payload.working_set_id,
        }
        slots = find_exact_value_slots(action, set(candidate.value_bindings))
        if any(path != ("argv", 2) for path in slots.values()):
            raise AccessRejection("invalid_syntax", "command sentinel must be the rg pattern")
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
        working_set_id = str(action.canonical_action["working_set_id"])
        working_sets = context.schema_catalog.get("working_sets")
        working_set = working_sets.get(working_set_id) if isinstance(working_sets, dict) else None
        if not isinstance(working_set, dict):
            raise AccessRejection("sandbox_violation", "command working set is outside snapshot")
        root = working_set.get("root")
        allowed_files = working_set.get("files")
        if (
            root != f"/worksets/{working_set_id}"
            or not isinstance(allowed_files, list)
            or not allowed_files
            or any(not self._relative_file(path) for path in allowed_files)
        ):
            raise AccessRejection("sandbox_violation", "command working set catalog is invalid")
        capabilities = context.scope_config.get("command_capabilities")
        capability = capabilities.get("rg_fixed_search") if isinstance(capabilities, dict) else None
        if (
            not isinstance(capability, dict)
            or set(capability) != {"executable", "binary_sha256"}
            or capability["executable"] != action.canonical_action["executable"]
            or not isinstance(capability["binary_sha256"], str)
            or _SHA256.fullmatch(capability["binary_sha256"]) is None
        ):
            raise AccessRejection("sandbox_violation", "command binary attestation is invalid")
        requested_files = list(action.canonical_action["argv"])[3:]
        if not set(requested_files) <= set(allowed_files):
            raise AccessRejection("sandbox_violation", "command file exceeds frozen working set")
        pattern = list(action.canonical_action["argv"])[2]
        effective = {
            "adapter_kind": "command_runner",
            "profile": "rg_fixed_search",
            "executable": capability["executable"],
            "binary_sha256": capability["binary_sha256"],
            "argv": [
                "--fixed-strings",
                "--line-number",
                "--no-heading",
                "--color=never",
                "--no-config",
                f"--max-count={budget.result_limit}",
                "--",
                pattern,
                *requested_files,
            ],
            "pattern_index": 7,
            "working_set_id": working_set_id,
            "working_root": root,
            "allowed_files": requested_files,
            "timeout_ms": min(budget.timeout_ms, 15_000),
            "output_bytes": budget.output_bytes,
            "result_limit": budget.result_limit,
        }
        diff["command_sandbox"] = {
            "profile": "rg_fixed_search",
            "working_set_id": working_set_id,
            "files": requested_files,
            "network": "none",
        }
        return PolicyEvaluation(
            effective_action=effective,
            effective_structural_hash=structural_hash(effective),
            validation_decisions=(
                {"check": "command_argv_grammar", "outcome": "allow"},
                {"check": "command_binary_attestation", "outcome": "allow"},
                {"check": "command_working_set", "outcome": "allow"},
                {"check": "command_sandbox_budget", "outcome": "allow"},
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
        if any(path != ("argv", effective["pattern_index"]) for path in slots.values()):
            raise AccessRejection("invalid_syntax", "command sentinel moved outside pattern")
        bound = bind_exact_values(effective, slots, dict(values))
        pattern = bound["argv"][bound["pattern_index"]]
        if not isinstance(pattern, str) or "\x00" in pattern or "\n" in pattern or "\r" in pattern:
            raise AccessRejection("invalid_syntax", "bound command pattern is invalid")
        shape = structural_hash(bound)
        if shape != evaluation.effective_structural_hash:
            raise AccessRejection("invalid_syntax", "ValueRef binding changed command structure")
        return BoundNativeAction(
            language=self.language,
            canonical_action=bound,
            structural_hash=shape,
            parse_tree_hash=canonical_hash(bound),
        )

    @staticmethod
    def _relative_file(value: object) -> bool:
        if not isinstance(value, str) or not value or len(value) > 500 or "\x00" in value:
            return False
        path = PurePosixPath(value)
        return (
            not path.is_absolute()
            and value == path.as_posix()
            and ".." not in path.parts
            and "." not in path.parts
            and not value.startswith("-")
        )
