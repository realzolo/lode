"""Analysis input hashes distinguish structural binding changes only."""

from __future__ import annotations

from types import SimpleNamespace

from lode.api.routes.control_plane import _analysis_binding_snapshot
from lode.application.intake import canonical_hash


def _hash(binding: SimpleNamespace, repository: SimpleNamespace) -> str:
    return canonical_hash(
        {"repository_bindings": [_analysis_binding_snapshot(binding, repository)]}
    )


def test_analysis_hash_ignores_metadata_but_tracks_role_branch_and_default_branch() -> None:
    binding = SimpleNamespace(
        id=1,
        descriptor_revision=1,
        account_connection_id=3,
        role="runtime_source",
        branch_mode="default",
        branch_name=None,
        priority=0,
        description="Runtime source",
        revision=1,
    )
    repository = SimpleNamespace(id=2, default_branch="main")

    original = _hash(binding, repository)
    binding.priority = 20
    binding.description = "Metadata-only update"
    binding.revision = 2
    assert _hash(binding, repository) == original

    binding.role = "shared_library"
    binding.descriptor_revision = 2
    assert _hash(binding, repository) != original

    binding.role = "runtime_source"
    binding.branch_mode = "branch"
    binding.branch_name = "release/2026.08"
    binding.descriptor_revision = 3
    fixed = _hash(binding, repository)
    assert fixed != original

    binding.branch_mode = "default"
    binding.branch_name = None
    binding.descriptor_revision = 4
    repository.default_branch = "trunk"
    assert _hash(binding, repository) != original
