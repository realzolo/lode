"""Resource analyst output is anchored, unambiguous, and identity-stable."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from lode.application.resource_analysis import ResourceAnalysisPayload
from lode.infrastructure.resource_analyst import _annotations, _validate_anchors
from lode.resource_understanding.store import BoundRepositoryScan
from lode.resource_understanding.types import (
    BuildUnitCandidate,
    ObservationDraft,
    ScanResult,
)


def _scan() -> tuple[BoundRepositoryScan, ...]:
    observation = ObservationDraft(
        source_kind="repository",
        source_ref="repository:3/package.json",
        observation_kind="package_manifest",
        structured_payload={"name": "payments-api"},
        path="package.json",
        root_provenance_id="repository:3",
        source_family="npm",
    )
    unit = BuildUnitCandidate(
        candidate_key="repository:3/build-unit:root",
        source_root=".",
        build_system="npm",
        manifest_paths=("package.json",),
        observation_refs=(observation.source_ref,),
    )
    return (
        BoundRepositoryScan(
            repository_binding_id=3,
            repository_id=7,
            scan=ScanResult(
                source_revision="a" * 40,
                observations=(observation,),
                build_units=(unit,),
                issues=(),
                scanned_file_count=1,
            ),
        ),
    )


def _payload(**overrides) -> ResourceAnalysisPayload:
    component = {
        "component_key": "payments-api",
        "display_name": "Payments API",
        "component_kind": "service",
        "build_unit_keys": ["repository:3/build-unit:root"],
        "observation_refs": ["repository:3/package.json"],
        "aliases": ["payments"],
        "description": "Handles payment requests.",
        "entrypoints": ["src/main.ts"],
        "dependencies": ["postgresql"],
        "runbooks": ["docs/payments.md"],
        "owners": ["team-payments"],
        **overrides,
    }
    return ResourceAnalysisPayload.model_validate({"components": [component]})


def test_resource_analyst_requires_real_scanner_anchors() -> None:
    scans = _scan()
    _validate_anchors(_payload(), scans)

    with pytest.raises(ValueError, match="unknown observation"):
        _validate_anchors(_payload(observation_refs=["repository:3/missing"]), scans)

    with pytest.raises(ValueError, match="unknown build unit"):
        _validate_anchors(_payload(build_unit_keys=["repository:3/missing"]), scans)


def test_resource_analyst_rejects_competing_component_identity() -> None:
    component = _payload().components[0].model_dump(mode="json")
    competing = {**component, "component_key": "payments-worker"}

    with pytest.raises(ValidationError, match="only one component"):
        ResourceAnalysisPayload.model_validate({"components": [component, competing]})


def test_resource_analyst_component_key_is_stable_and_semantics_are_preserved() -> None:
    first = _annotations(_payload())[0]
    second = _annotations(_payload(component_key="renamed-by-model"))[0]

    assert first.stable_key == second.stable_key
    assert first.build_unit_keys == ("repository:3/build-unit:root",)
    assert first.extra == {
        "entrypoints": ["src/main.ts"],
        "dependencies": ["postgresql"],
        "runbooks": ["docs/payments.md"],
        "owners": ["team-payments"],
    }
