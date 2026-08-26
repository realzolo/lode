"""Deterministic identity validation over scanner-owned candidates."""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any, Iterable, Mapping

from lode.resource_understanding.types import (
    IdentityResolutionDraft,
    ScanResult,
    SemanticAnnotationDraft,
)


_STABLE_KEY = re.compile(r"^[a-z0-9][a-z0-9._:/-]{0,199}$")
_COMPONENT_KINDS = {"service", "worker", "job", "gateway", "library_runtime", "unknown"}
_FORBIDDEN_KEYS = {
    "credential",
    "credentials",
    "secret",
    "secret_ciphertext",
    "evidence_access_scope",
    "scope_config",
    "allowed_languages",
    "repository_binding",
    "repository_id",
    "workspace_repository_binding",
}


class ResourceValidationError(ValueError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code


class ResourceIdentityValidator:
    version = "resource-identity-validator.1"

    def validate(
        self,
        scan: ScanResult,
        annotations: Iterable[SemanticAnnotationDraft] = (),
    ) -> tuple[IdentityResolutionDraft, ...]:
        annotation_list = tuple(annotations)
        observations = {item.source_ref: item for item in scan.observations}
        build_units = {item.candidate_key: item for item in scan.build_units}
        resolutions: list[IdentityResolutionDraft] = []

        for unit in scan.build_units:
            self._require_observations(unit.observation_refs, observations)
            roots, families = self._provenance(unit.observation_refs, observations)
            status = "verified" if len(set(roots)) >= 2 else "provisional"
            resolutions.append(IdentityResolutionDraft(
                stable_key=unit.candidate_key,
                resolution_kind="build_unit",
                status=status,
                resolved_payload={
                    "candidate_key": unit.candidate_key,
                    "source_root": unit.source_root,
                    "build_system": unit.build_system,
                    "manifest_paths": unit.manifest_paths,
                    "entrypoints": unit.entrypoints,
                    "artifact_hints": unit.artifact_hints,
                    "ownership_priority": unit.ownership_priority,
                },
                evidence_basis={
                    "path_validated": True,
                    "scanner_candidate": True,
                    "source_families": families,
                },
                observation_refs=unit.observation_refs,
                annotation_indexes=(),
                root_provenance_refs=roots,
            ))

        alias_targets: dict[str, set[str]] = defaultdict(set)
        for annotation in annotation_list:
            for alias in annotation.aliases:
                alias_targets[alias].add(annotation.stable_key)

        for index, annotation in enumerate(annotation_list):
            self._validate_annotation(annotation, build_units, observations)
            roots, families = self._provenance(annotation.observation_refs, observations)
            status = "verified" if len(set(families)) >= 2 else "provisional"
            conflicts = sorted(
                alias for alias in annotation.aliases if len(alias_targets[alias]) > 1
            )
            if conflicts:
                status = "ambiguous"
            resolutions.append(IdentityResolutionDraft(
                stable_key=annotation.stable_key,
                resolution_kind="component",
                status=status,
                resolved_payload={
                    "display_name": annotation.display_name,
                    "kind": annotation.component_kind,
                    "description": annotation.description,
                    "build_unit_keys": annotation.build_unit_keys,
                    "aliases": annotation.aliases,
                },
                evidence_basis={
                    "scanner_candidates_only": True,
                    "annotation_is_evidence": False,
                    "source_families": families,
                    "alias_conflicts": conflicts,
                },
                observation_refs=annotation.observation_refs,
                annotation_indexes=(index,),
                root_provenance_refs=roots,
            ))
            for build_key in annotation.build_unit_keys:
                unit = build_units[build_key]
                binding_refs = tuple(sorted(set(unit.observation_refs) | set(annotation.observation_refs)))
                binding_roots, binding_families = self._provenance(binding_refs, observations)
                resolutions.append(IdentityResolutionDraft(
                    stable_key=f"{annotation.stable_key}/source/{build_key}",
                    resolution_kind="component_source_binding",
                    status=status,
                    resolved_payload={
                        "component_key": annotation.stable_key,
                        "build_unit_key": build_key,
                        "role": "primary" if len(annotation.build_unit_keys) == 1 else "supporting",
                        "path_prefix": unit.source_root,
                    },
                    evidence_basis={
                        "scanner_candidate": True,
                        "source_families": binding_families,
                    },
                    observation_refs=binding_refs,
                    annotation_indexes=(index,),
                    root_provenance_refs=binding_roots,
                ))

        return tuple(resolutions)

    def validate_many(
        self,
        scans: Iterable[ScanResult],
        annotations: Iterable[SemanticAnnotationDraft] = (),
    ) -> tuple[IdentityResolutionDraft, ...]:
        scan_list = tuple(scans)
        observations = tuple(item for scan in scan_list for item in scan.observations)
        build_units = tuple(item for scan in scan_list for item in scan.build_units)
        if len({item.source_ref for item in observations}) != len(observations):
            raise ResourceValidationError("duplicate_observation", "observation refs must be namespaced")
        if len({item.candidate_key for item in build_units}) != len(build_units):
            raise ResourceValidationError("duplicate_build_unit", "build unit keys must be namespaced")
        combined = ScanResult(
            source_revision="combined",
            observations=observations,
            build_units=build_units,
            issues=tuple(issue for scan in scan_list for issue in scan.issues),
            scanned_file_count=sum(scan.scanned_file_count for scan in scan_list),
        )
        return self.validate(combined, annotations)

    def _validate_annotation(
        self,
        annotation: SemanticAnnotationDraft,
        build_units: Mapping[str, Any],
        observations: Mapping[str, Any],
    ) -> None:
        if annotation.annotation_kind != "component_identity":
            raise ResourceValidationError("unsupported_annotation", "unsupported annotation kind")
        if not _STABLE_KEY.fullmatch(annotation.stable_key):
            raise ResourceValidationError("invalid_stable_key", "component stable key is not canonical")
        if not annotation.display_name.strip() or annotation.display_name != annotation.display_name.strip():
            raise ResourceValidationError("invalid_display_name", "display name must be trimmed")
        if annotation.component_kind not in _COMPONENT_KINDS:
            raise ResourceValidationError("invalid_component_kind", "unsupported component kind")
        if not annotation.build_unit_keys or len(set(annotation.build_unit_keys)) != len(annotation.build_unit_keys):
            raise ResourceValidationError("invalid_build_units", "component build units must be unique and nonempty")
        missing = sorted(set(annotation.build_unit_keys) - set(build_units))
        if missing:
            raise ResourceValidationError("unknown_build_unit", f"unknown scanner candidates: {missing}")
        self._require_observations(annotation.observation_refs, observations)
        if self._contains_forbidden_key(annotation.extra):
            raise ResourceValidationError("authorization_expansion", "annotation contains authorization fields")
        for alias in annotation.aliases:
            if not alias.strip() or alias != alias.strip():
                raise ResourceValidationError("invalid_alias", "aliases must be nonblank and trimmed")

    @staticmethod
    def _require_observations(refs: tuple[str, ...], observations: Mapping[str, Any]) -> None:
        if not refs:
            raise ResourceValidationError("missing_observation", "identity requires scanner observations")
        missing = sorted(set(refs) - set(observations))
        if missing:
            raise ResourceValidationError("unknown_observation", f"unknown observations: {missing}")

    @staticmethod
    def _provenance(refs: tuple[str, ...], observations: Mapping[str, Any]) -> tuple[tuple[str, ...], tuple[str, ...]]:
        roots = tuple(sorted({observations[ref].root_provenance_id for ref in refs}))
        families = tuple(sorted({observations[ref].source_family for ref in refs}))
        return roots, families

    @classmethod
    def _contains_forbidden_key(cls, value: Any) -> bool:
        if isinstance(value, Mapping):
            for key, item in value.items():
                normalized = str(key).lower().replace("-", "_")
                if normalized in _FORBIDDEN_KEYS or cls._contains_forbidden_key(item):
                    return True
        elif isinstance(value, list | tuple):
            return any(cls._contains_forbidden_key(item) for item in value)
        return False
