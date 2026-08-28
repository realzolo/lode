from __future__ import annotations

from pathlib import Path

import pytest

from lode.resource_understanding import (
    ManifestScanner,
    ResourceIdentityValidator,
    SemanticAnnotationDraft,
    repository_candidate_namespace,
)
from lode.resource_understanding.scanner import RepositoryScanLimitError, ScanLimits
from lode.resource_understanding.validator import ResourceValidationError

FIXTURES = Path(__file__).parents[1] / "fixtures" / "resource_scanner"
REVISION = "a" * 40


@pytest.mark.parametrize(
    ("fixture", "roots", "systems"),
    [
        ("single", {"."}, {"npm"}),
        ("pnpm", {".", "apps/api", "apps/api/packages/nested", "packages/contracts"}, {"pnpm", "npm"}),
        ("python", {".", "services/worker"}, {"python"}),
        ("jvm", {".", "services/api"}, {"maven"}),
    ],
)
def test_scans_single_and_monorepo_fixtures(fixture: str, roots: set[str], systems: set[str]) -> None:
    result = ManifestScanner().scan(FIXTURES / fixture, REVISION)

    assert {unit.source_root for unit in result.build_units} == roots
    assert {unit.build_system for unit in result.build_units} == systems
    assert not result.issues


def test_scanner_never_executes_manifest_commands(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        '{"name":"unsafe","scripts":{"start":"touch SHOULD_NOT_EXIST"}}'
    )

    result = ManifestScanner().scan(tmp_path, REVISION)

    assert result.build_units
    assert not (tmp_path / "SHOULD_NOT_EXIST").exists()


def test_scanner_skips_symlinks_and_rejects_unsafe_yaml(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside-package.json"
    outside.write_text('{"name":"outside"}')
    (tmp_path / "linked-package.json").symlink_to(outside)
    deploy = tmp_path / "deploy"
    deploy.mkdir()
    (deploy / "bad.yaml").write_text("value: !!python/object/apply:os.system ['touch owned']")

    result = ManifestScanner().scan(tmp_path, REVISION)

    assert {issue.code for issue in result.issues} == {"unsafe_symlink", "invalid_manifest"}
    assert not (tmp_path / "owned").exists()


def test_scanner_rejects_duplicate_yaml_keys(tmp_path: Path) -> None:
    deploy = tmp_path / "deploy"
    deploy.mkdir()
    (deploy / "duplicate.yaml").write_text("kind: Deployment\nkind: Job\n")

    result = ManifestScanner().scan(tmp_path, REVISION)

    assert [(issue.code, issue.path) for issue in result.issues] == [
        ("invalid_manifest", "deploy/duplicate.yaml")
    ]


def test_scanner_rejects_yaml_aliases(tmp_path: Path) -> None:
    deploy = tmp_path / "deploy"
    deploy.mkdir()
    (deploy / "alias.yaml").write_text("common: &common {image: example/api}\ncopy: *common\n")

    result = ManifestScanner().scan(tmp_path, REVISION)

    assert result.issues[0].code == "invalid_manifest"
    assert result.issues[0].detail == "manifest format is invalid or unsupported"


@pytest.mark.parametrize(
    "limits, configure",
    [
        (ScanLimits(max_files=1), lambda root: [(root / "a.txt").write_text("a"), (root / "b.txt").write_text("b")]),
        (ScanLimits(max_manifest_bytes=8), lambda root: (root / "package.json").write_text('{"name":"too-large"}')),
        (ScanLimits(max_directory_depth=1), lambda root: ((root / "a" / "b").mkdir(parents=True), (root / "a" / "b" / "package.json").write_text('{"name":"deep"}'))),
        (ScanLimits(max_structure_nodes=2), lambda root: ((root / "deploy").mkdir(), (root / "deploy" / "large.yaml").write_text("kind: Deployment\nmetadata:\n  name: api\n"))),
    ],
)
def test_scanner_fails_closed_on_global_safety_limits(tmp_path: Path, limits: ScanLimits, configure) -> None:
    configure(tmp_path)

    with pytest.raises(RepositoryScanLimitError):
        ManifestScanner(limits).scan(tmp_path, REVISION)


def test_scanner_diagnostics_do_not_echo_manifest_content(tmp_path: Path) -> None:
    deploy = tmp_path / "deploy"
    deploy.mkdir()
    secret = "not-for-diagnostics"
    (deploy / "invalid.yaml").write_text(f"metadata: [ {secret}\n")

    result = ManifestScanner().scan(tmp_path, REVISION)

    assert result.issues[0].detail == "manifest format is invalid or unsupported"
    assert secret not in result.issues[0].detail


def test_annotation_cannot_self_verify_identity() -> None:
    scan = ManifestScanner().scan(FIXTURES / "pnpm", REVISION)
    unit = next(item for item in scan.build_units if item.source_root == "apps/api")
    annotation = SemanticAnnotationDraft(
        annotation_kind="component_identity",
        stable_key="component:api",
        display_name="API",
        component_kind="service",
        build_unit_keys=(unit.candidate_key,),
        observation_refs=unit.observation_refs,
        aliases=("api",),
    )

    resolutions = ResourceIdentityValidator().validate(scan, [annotation])
    component = next(item for item in resolutions if item.resolution_kind == "component")

    assert component.status == "provisional"
    assert component.evidence_basis["annotation_is_evidence"] is False


def test_independent_structured_families_verify_component() -> None:
    scan = ManifestScanner().scan(FIXTURES / "single", REVISION)
    unit = scan.build_units[0]
    annotation = SemanticAnnotationDraft(
        annotation_kind="component_identity",
        stable_key="component:single-api",
        display_name="Single API",
        component_kind="service",
        build_unit_keys=(unit.candidate_key,),
        observation_refs=tuple(item.source_ref for item in scan.observations),
        aliases=("single-api",),
    )

    resolutions = ResourceIdentityValidator().validate(scan, [annotation])
    component = next(item for item in resolutions if item.resolution_kind == "component")

    assert component.status == "verified"
    assert set(component.evidence_basis["source_families"]) == {
        "build_manifest",
        "deployment_manifest",
        "source_manifest",
    }


def test_annotation_cannot_create_paths_or_authorization() -> None:
    scan = ManifestScanner().scan(FIXTURES / "single", REVISION)
    observation_refs = tuple(item.source_ref for item in scan.observations)
    invalid_path = SemanticAnnotationDraft(
        annotation_kind="component_identity",
        stable_key="component:invented",
        display_name="Invented",
        component_kind="service",
        build_unit_keys=("build-unit:../../outside",),
        observation_refs=observation_refs,
    )
    expands_scope = SemanticAnnotationDraft(
        annotation_kind="component_identity",
        stable_key="component:scope",
        display_name="Scope",
        component_kind="service",
        build_unit_keys=(scan.build_units[0].candidate_key,),
        observation_refs=observation_refs,
        extra={"nested": {"scope_config": {"indices": ["*"]}}},
    )

    with pytest.raises(ResourceValidationError, match="unknown scanner candidates") as path_error:
        ResourceIdentityValidator().validate(scan, [invalid_path])
    assert path_error.value.code == "unknown_build_unit"
    with pytest.raises(ResourceValidationError, match="authorization fields") as scope_error:
        ResourceIdentityValidator().validate(scan, [expands_scope])
    assert scope_error.value.code == "authorization_expansion"


def test_duplicate_aliases_are_ambiguous() -> None:
    scan = ManifestScanner().scan(FIXTURES / "pnpm", REVISION)
    units = scan.build_units[:2]
    annotations = [
        SemanticAnnotationDraft(
            annotation_kind="component_identity",
            stable_key=f"component:{index}",
            display_name=f"Component {index}",
            component_kind="service",
            build_unit_keys=(unit.candidate_key,),
            observation_refs=unit.observation_refs,
            aliases=("shared",),
        )
        for index, unit in enumerate(units)
    ]

    resolutions = ResourceIdentityValidator().validate(scan, annotations)

    assert {
        item.status for item in resolutions if item.resolution_kind == "component"
    } == {"ambiguous"}


def test_validator_combines_namespaced_multi_repository_candidates() -> None:
    scanner = ManifestScanner()
    source = scanner.scan(
        FIXTURES / "single",
        "1" * 40,
        candidate_namespace=repository_candidate_namespace(10),
    )
    worker = scanner.scan(
        FIXTURES / "python",
        "2" * 40,
        candidate_namespace=repository_candidate_namespace(20),
    )
    annotation = SemanticAnnotationDraft(
        annotation_kind="component_identity",
        stable_key="component:multi-repository",
        display_name="Multi Repository",
        component_kind="service",
        build_unit_keys=(source.build_units[0].candidate_key, worker.build_units[0].candidate_key),
        observation_refs=tuple(
            item.source_ref for scan in (source, worker) for item in scan.observations
        ),
    )

    resolutions = ResourceIdentityValidator().validate_many((worker, source), (annotation,))
    component = next(item for item in resolutions if item.resolution_kind == "component")

    assert component.status == "verified"
    assert set(component.resolved_payload["build_unit_keys"]) == {
        "repository:10/build-unit:.",
        "repository:20/build-unit:.",
    }


def test_repository_candidate_namespace_is_canonical_and_positive() -> None:
    assert repository_candidate_namespace(42) == "repository:42"
    with pytest.raises(ValueError, match="positive"):
        repository_candidate_namespace(0)
