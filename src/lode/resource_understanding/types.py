"""Immutable values shared by scanners, validators, and persistence."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Mapping


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def content_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class ObservationDraft:
    source_kind: str
    source_ref: str
    observation_kind: str
    structured_payload: Mapping[str, Any]
    path: str
    root_provenance_id: str
    source_family: str
    trust_class: str = "structured_repository_data"
    parser_name: str = "manifest_scanner"
    parser_version: str = "1"

    @property
    def content_hash(self) -> str:
        return content_hash(self.structured_payload)


@dataclass(frozen=True, slots=True)
class BuildUnitCandidate:
    candidate_key: str
    source_root: str
    build_system: str
    manifest_paths: tuple[str, ...]
    entrypoints: tuple[str, ...] = ()
    artifact_hints: Mapping[str, Any] = field(default_factory=dict)
    observation_refs: tuple[str, ...] = ()
    ownership_priority: int = 0


@dataclass(frozen=True, slots=True)
class ScanIssue:
    code: str
    path: str
    detail: str


@dataclass(frozen=True, slots=True)
class ScanResult:
    source_revision: str
    observations: tuple[ObservationDraft, ...]
    build_units: tuple[BuildUnitCandidate, ...]
    issues: tuple[ScanIssue, ...]
    scanned_file_count: int

    @property
    def input_hash(self) -> str:
        return content_hash(
            {
                "source_revision": self.source_revision,
                "observations": [
                    {"source_ref": item.source_ref, "content_hash": item.content_hash}
                    for item in self.observations
                ],
                "build_units": [
                    {
                        "candidate_key": item.candidate_key,
                        "source_root": item.source_root,
                        "build_system": item.build_system,
                        "manifest_paths": item.manifest_paths,
                        "entrypoints": item.entrypoints,
                        "artifact_hints": item.artifact_hints,
                        "observation_refs": item.observation_refs,
                        "ownership_priority": item.ownership_priority,
                    }
                    for item in self.build_units
                ],
            }
        )


@dataclass(frozen=True, slots=True)
class SemanticAnnotationDraft:
    annotation_kind: str
    stable_key: str
    display_name: str
    component_kind: str
    build_unit_keys: tuple[str, ...]
    observation_refs: tuple[str, ...]
    aliases: tuple[str, ...] = ()
    description: str = ""
    extra: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class IdentityResolutionDraft:
    stable_key: str
    resolution_kind: str
    status: str
    resolved_payload: Mapping[str, Any]
    evidence_basis: Mapping[str, Any]
    observation_refs: tuple[str, ...]
    annotation_indexes: tuple[int, ...]
    root_provenance_refs: tuple[str, ...]

    @property
    def resolution_hash(self) -> str:
        return content_hash(
            {
                "stable_key": self.stable_key,
                "resolution_kind": self.resolution_kind,
                "status": self.status,
                "resolved_payload": self.resolved_payload,
                "evidence_basis": self.evidence_basis,
                "observation_refs": self.observation_refs,
                "annotation_indexes": self.annotation_indexes,
                "root_provenance_refs": self.root_provenance_refs,
            }
        )
