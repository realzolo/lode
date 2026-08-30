"""Analysis input hashes distinguish structural binding changes only."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from lode.api.routes.control_plane import _analysis_binding_snapshot
from lode.application.intake import canonical_hash
from lode.infrastructure.repository_analysis import RepositoryAnalysisService


def _hash(binding: SimpleNamespace, repository: SimpleNamespace) -> str:
    return canonical_hash(
        {"repository_bindings": [_analysis_binding_snapshot(binding, repository)]}
    )


def test_analysis_hash_ignores_metadata_but_tracks_mode_source_branch_and_default_branch() -> None:
    binding = SimpleNamespace(
        id=1,
        descriptor_revision=1,
        account_connection_id=3,
        analysis_mode="code",
        is_alert_source=True,
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

    binding.analysis_mode = "documentation"
    binding.is_alert_source = False
    binding.descriptor_revision = 2
    assert _hash(binding, repository) != original

    binding.analysis_mode = "code"
    binding.is_alert_source = True
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


class CapturingScanner:
    def __init__(self) -> None:
        self.namespace: str | None = None

    def scan(self, _root, _revision, *, candidate_namespace):
        self.namespace = candidate_namespace
        return "scan-result"


class InMemoryCheckoutReader:
    async def read_checkout(self, *, reader, **_kwargs):
        return reader(Path("/checkout"))


async def test_repository_analysis_uses_the_canonical_binding_namespace() -> None:
    scanner = CapturingScanner()
    service = RepositoryAnalysisService(
        session_factory=object(),  # type: ignore[arg-type]
        reader=InMemoryCheckoutReader(),  # type: ignore[arg-type]
        scanner=scanner,  # type: ignore[arg-type]
    )

    result = await service._scan_repository(
        SimpleNamespace(id=42),
        SimpleNamespace(repo_url="https://example.invalid/repository.git"),
        "a" * 40,
        SimpleNamespace(),
    )

    assert result == "scan-result"
    assert scanner.namespace == "repository:42"
