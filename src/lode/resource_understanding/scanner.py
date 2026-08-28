"""Bounded, non-executing scanner for repository manifests."""

from __future__ import annotations

import json
import os
import re
import stat
import tomllib
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from lode.resource_understanding.types import (
    BuildUnitCandidate,
    ObservationDraft,
    ScanIssue,
    ScanResult,
)


_REVISION = re.compile(r"^[0-9a-f]{40}$")
_MANIFEST_NAMES = {
    "package.json",
    "pnpm-workspace.yaml",
    "pyproject.toml",
    "go.mod",
    "Cargo.toml",
    "pom.xml",
    "settings.gradle",
    "settings.gradle.kts",
    "build.gradle",
    "build.gradle.kts",
    "Dockerfile",
    "compose.yaml",
    "compose.yml",
    "docker-compose.yaml",
    "docker-compose.yml",
    "Chart.yaml",
}
_YAML_SUFFIXES = {".yaml", ".yml"}
_DEPLOYMENT_KINDS = {"Deployment", "StatefulSet", "DaemonSet", "Job", "CronJob"}
_BUILD_PRECEDENCE = {
    "pnpm": 0,
    "npm": 1,
    "python": 2,
    "go": 3,
    "cargo": 4,
    "maven": 5,
    "gradle": 6,
    "docker": 7,
    "other": 8,
}


class _UniqueKeySafeLoader(yaml.SafeLoader):
    pass


def _construct_mapping(loader: yaml.SafeLoader, node: yaml.MappingNode, deep: bool = False) -> dict:
    loader.flatten_mapping(node)
    result: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in result:
            raise yaml.YAMLError(f"duplicate mapping key: {key!r}")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_UniqueKeySafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_mapping,
)


@dataclass(frozen=True, slots=True)
class ScanLimits:
    max_files: int = 10_000
    max_manifest_bytes: int = 1_048_576
    max_directory_depth: int = 24
    max_structure_depth: int = 64
    max_structure_nodes: int = 20_000


class RepositoryScanLimitError(ValueError):
    """The complete repository scan exceeded a server-owned safety budget."""


@dataclass(slots=True)
class _UnitAccumulator:
    source_root: str
    systems: set[str]
    manifest_paths: set[str]
    entrypoints: set[str]
    artifact_hints: dict[str, Any]
    observation_refs: set[str]


class ManifestScanner:
    """Read a frozen checkout without following links or executing repository code."""

    def __init__(self, limits: ScanLimits | None = None) -> None:
        self.limits = limits or ScanLimits()

    def scan(
        self,
        repository_root: Path,
        source_revision: str,
        *,
        candidate_namespace: str = "",
    ) -> ScanResult:
        if not _REVISION.fullmatch(source_revision):
            raise ValueError("source_revision must be a lowercase 40-character SHA")
        if repository_root.is_symlink():
            raise ValueError("repository_root must not be a symbolic link")
        root = repository_root.resolve(strict=True)
        if not root.is_dir():
            raise ValueError("repository_root must be a directory")

        issues: list[ScanIssue] = []
        candidates: list[str] = []
        scanned_count = 0
        for current, directories, files in os.walk(root, followlinks=False):
            current_path = Path(current)
            relative_dir = current_path.relative_to(root)
            depth = len(relative_dir.parts)
            if depth >= self.limits.max_directory_depth and directories:
                raise RepositoryScanLimitError("repository directory depth limit exceeded")
            safe_directories: list[str] = []
            for name in sorted(directories):
                path = current_path / name
                if path.is_symlink():
                    issues.append(ScanIssue("unsafe_symlink", self._relative(root, path), "directory link skipped"))
                else:
                    safe_directories.append(name)
            directories[:] = safe_directories
            for name in sorted(files):
                scanned_count += 1
                if scanned_count > self.limits.max_files:
                    raise RepositoryScanLimitError("repository file limit exceeded")
                path = current_path / name
                relative = self._relative(root, path)
                if path.is_symlink():
                    issues.append(ScanIssue("unsafe_symlink", relative, "file link skipped"))
                    continue
                if self._is_candidate(relative):
                    candidates.append(relative)

        observations: list[ObservationDraft] = []
        units: dict[str, _UnitAccumulator] = {}
        for relative in sorted(candidates):
            try:
                raw = self._read(root, relative)
            except RepositoryScanLimitError:
                raise
            except ValueError:
                issues.append(ScanIssue("invalid_manifest", relative, "manifest could not be read safely"))
                continue
            try:
                parsed = self._parse(relative, raw)
            except RepositoryScanLimitError:
                raise
            except (
                ValueError,
                RecursionError,
                UnicodeDecodeError,
                json.JSONDecodeError,
                tomllib.TOMLDecodeError,
                yaml.YAMLError,
                ET.ParseError,
            ):
                issues.append(ScanIssue("invalid_manifest", relative, "manifest format is invalid or unsupported"))
                continue
            for kind, family, payload in parsed:
                local_ref = relative if len(parsed) == 1 else f"{relative}#{kind}"
                source_ref = f"{candidate_namespace}/{local_ref}" if candidate_namespace else local_ref
                observation = ObservationDraft(
                    source_kind="repository",
                    source_ref=source_ref,
                    observation_kind=kind,
                    structured_payload=payload,
                    path=relative,
                    root_provenance_id=(
                        f"{candidate_namespace}:repository:{source_revision}:{relative}"
                        if candidate_namespace else f"repository:{source_revision}:{relative}"
                    ),
                    source_family=family,
                    parser_name=f"manifest_scanner.{payload.get('format', 'unknown')}",
                )
                observations.append(observation)
                if kind in {"manifest", "build_unit"}:
                    self._accumulate_unit(units, relative, observation, payload)

        build_units = self._finalize_units(units, candidate_namespace)
        return ScanResult(
            source_revision=source_revision,
            observations=tuple(observations),
            build_units=build_units,
            issues=tuple(issues),
            scanned_file_count=scanned_count,
        )

    @staticmethod
    def _relative(root: Path, path: Path) -> str:
        return path.relative_to(root).as_posix()

    @staticmethod
    def _is_candidate(relative: str) -> bool:
        path = PurePosixPath(relative)
        name = path.name
        if name in _MANIFEST_NAMES:
            return True
        if path.suffix.lower() not in _YAML_SUFFIXES:
            return False
        parts = set(path.parts)
        return bool(parts & {".github", "k8s", "kubernetes", "deploy", "charts", "helm"})

    def _read(self, root: Path, relative: str) -> bytes:
        pure = PurePosixPath(relative)
        if pure.is_absolute() or ".." in pure.parts or "\\" in relative:
            raise ValueError("path escapes repository")
        path = root.joinpath(*pure.parts)
        current = root
        for part in pure.parts:
            current = current / part
            mode = current.lstat().st_mode
            if stat.S_ISLNK(mode):
                raise ValueError("symbolic links are not readable")
        resolved = path.resolve(strict=True)
        if not resolved.is_relative_to(root):
            raise ValueError("path escapes repository")
        if not stat.S_ISREG(resolved.stat().st_mode):
            raise ValueError("manifest is not a regular file")
        size = resolved.stat().st_size
        if size > self.limits.max_manifest_bytes:
            raise RepositoryScanLimitError("repository manifest size limit exceeded")
        return resolved.read_bytes()

    def _parse(self, relative: str, raw: bytes) -> list[tuple[str, str, dict[str, Any]]]:
        name = PurePosixPath(relative).name
        if name == "package.json":
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise ValueError("package.json root must be an object")
            manager = str(value.get("packageManager", ""))
            system = "pnpm" if manager.startswith("pnpm@") else "npm"
            entrypoints = self._json_entrypoints(value)
            workspaces = value.get("workspaces", [])
            if isinstance(workspaces, dict):
                workspaces = workspaces.get("packages", [])
            return [("manifest", "source_manifest", {
                "format": "package_json", "build_system": system,
                "name": value.get("name"), "private": value.get("private", False),
                "workspace_patterns": self._string_list(workspaces),
                "entrypoints": entrypoints,
            })]
        if name == "pnpm-workspace.yaml":
            value = self._yaml(raw)
            return [("manifest", "source_manifest", {
                "format": "pnpm_workspace", "build_system": "pnpm",
                "workspace_patterns": self._string_list(value.get("packages", [])),
            })]
        if name == "pyproject.toml":
            value = tomllib.loads(raw.decode("utf-8"))
            project = value.get("project", {}) if isinstance(value.get("project", {}), dict) else {}
            tool = value.get("tool", {}) if isinstance(value.get("tool", {}), dict) else {}
            return [("manifest", "source_manifest", {
                "format": "pyproject", "build_system": "python", "name": project.get("name"),
                "script_names": sorted((project.get("scripts") or {}).keys()),
                "workspace": self._python_workspace(tool),
            })]
        if name == "go.mod":
            text = raw.decode("utf-8")
            module = next((line.split(None, 1)[1] for line in text.splitlines() if line.startswith("module ")), None)
            return [("manifest", "source_manifest", {"format": "go_mod", "build_system": "go", "name": module})]
        if name == "Cargo.toml":
            value = tomllib.loads(raw.decode("utf-8"))
            package = value.get("package", {}) if isinstance(value.get("package", {}), dict) else {}
            workspace = value.get("workspace", {}) if isinstance(value.get("workspace", {}), dict) else {}
            return [("manifest", "source_manifest", {
                "format": "cargo_toml", "build_system": "cargo", "name": package.get("name"),
                "workspace_patterns": self._string_list(workspace.get("members", [])),
            })]
        if name == "pom.xml":
            if b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
                raise ValueError("XML document types and entities are forbidden")
            root = ET.fromstring(raw)
            modules = [node.text.strip() for node in root.iter() if node.tag.endswith("module") and node.text]
            artifact = next((node.text.strip() for node in root if node.tag.endswith("artifactId") and node.text), None)
            return [("manifest", "source_manifest", {
                "format": "maven_pom", "build_system": "maven", "name": artifact,
                "workspace_patterns": modules,
            })]
        if name in {"settings.gradle", "settings.gradle.kts", "build.gradle", "build.gradle.kts"}:
            text = raw.decode("utf-8")
            modules = sorted(set(re.findall(r"['\"]:([A-Za-z0-9_.:-]+)['\"]", text)))
            return [("manifest", "source_manifest", {
                "format": "gradle", "build_system": "gradle", "workspace_patterns": modules,
            })]
        if name == "Dockerfile":
            return [("build_unit", "build_manifest", self._dockerfile(raw))]
        if PurePosixPath(relative).suffix.lower() in _YAML_SUFFIXES:
            return self._yaml_documents(relative, raw)
        raise ValueError("unsupported manifest")

    def _yaml(self, raw: bytes) -> dict[str, Any]:
        text = raw.decode("utf-8")
        self._reject_yaml_aliases(text)
        value = yaml.load(text, Loader=_UniqueKeySafeLoader)
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise ValueError("YAML root must be a mapping")
        self._validate_structure(value)
        return value

    def _yaml_documents(self, relative: str, raw: bytes) -> list[tuple[str, str, dict[str, Any]]]:
        text = raw.decode("utf-8")
        self._reject_yaml_aliases(text)
        documents = list(yaml.load_all(text, Loader=_UniqueKeySafeLoader))
        results: list[tuple[str, str, dict[str, Any]]] = []
        for index, value in enumerate(documents):
            if value is None:
                continue
            if not isinstance(value, dict):
                raise ValueError("YAML document root must be a mapping")
            self._validate_structure(value)
            kind = value.get("kind")
            metadata = value.get("metadata", {}) if isinstance(value.get("metadata"), dict) else {}
            if kind in _DEPLOYMENT_KINDS:
                results.append(("deployment", "deployment_manifest", {
                    "format": "kubernetes", "document": index, "kind": kind,
                    "name": metadata.get("name"), "namespace": metadata.get("namespace"),
                    "container_images": self._container_images(value), "build_system": "other",
                }))
            else:
                results.append(("runtime_config", "runtime_config", {
                    "format": "yaml", "document": index, "kind": kind,
                    "name": metadata.get("name"), "path": relative,
                }))
        return results

    @staticmethod
    def _reject_yaml_aliases(text: str) -> None:
        if any(isinstance(event, yaml.events.AliasEvent) for event in yaml.parse(text)):
            raise ValueError("YAML aliases are forbidden")

    def _validate_structure(self, value: Any) -> None:
        count = 0
        stack = [(value, 0)]
        while stack:
            item, depth = stack.pop()
            count += 1
            if count > self.limits.max_structure_nodes:
                raise RepositoryScanLimitError("repository manifest structure node limit exceeded")
            if depth > self.limits.max_structure_depth:
                raise RepositoryScanLimitError("repository manifest structure depth limit exceeded")
            if isinstance(item, dict):
                stack.extend((key, depth + 1) for key in item)
                stack.extend((child, depth + 1) for child in item.values())
            elif isinstance(item, list):
                stack.extend((child, depth + 1) for child in item)

    @staticmethod
    def _container_images(value: Any) -> list[str]:
        images: list[str] = []
        if isinstance(value, dict):
            for key, item in value.items():
                if key == "image" and isinstance(item, str):
                    images.append(item)
                else:
                    images.extend(ManifestScanner._container_images(item))
        elif isinstance(value, list):
            for item in value:
                images.extend(ManifestScanner._container_images(item))
        return sorted(set(images))

    @staticmethod
    def _dockerfile(raw: bytes) -> dict[str, Any]:
        instructions: dict[str, list[str]] = {}
        for raw_line in raw.decode("utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            key, _, value = line.partition(" ")
            upper = key.upper()
            if upper in {"FROM", "COPY", "EXPOSE", "ENTRYPOINT", "CMD"}:
                instructions.setdefault(upper.lower(), []).append(value.strip())
        return {"format": "dockerfile", "build_system": "docker", "instructions": instructions}

    @staticmethod
    def _json_entrypoints(value: dict[str, Any]) -> list[str]:
        entries: list[str] = []
        main = value.get("main")
        if isinstance(main, str):
            entries.append(main)
        binary = value.get("bin")
        if isinstance(binary, str):
            entries.append(binary)
        elif isinstance(binary, dict):
            entries.extend(item for item in binary.values() if isinstance(item, str))
        return sorted(set(item for item in entries if ManifestScanner._safe_relative(item)))

    @staticmethod
    def _python_workspace(tool: dict[str, Any]) -> list[str]:
        uv_config = tool.get("uv", {}) if isinstance(tool.get("uv"), dict) else {}
        workspace = uv_config.get("workspace", {}) if isinstance(uv_config.get("workspace"), dict) else {}
        return ManifestScanner._string_list(workspace.get("members", []))

    @staticmethod
    def _string_list(value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        return sorted(set(item for item in value if isinstance(item, str) and item.strip()))

    @staticmethod
    def _safe_relative(value: str) -> bool:
        path = PurePosixPath(value)
        return bool(value) and not path.is_absolute() and ".." not in path.parts and "\\" not in value

    def _accumulate_unit(
        self,
        units: dict[str, _UnitAccumulator],
        manifest_path: str,
        observation: ObservationDraft,
        payload: dict[str, Any],
    ) -> None:
        root = PurePosixPath(manifest_path).parent.as_posix()
        if root == ".":
            root = "."
        unit = units.setdefault(root, _UnitAccumulator(root, set(), set(), set(), {}, set()))
        unit.systems.add(str(payload.get("build_system", "other")))
        unit.manifest_paths.add(manifest_path)
        unit.entrypoints.update(
            item for item in payload.get("entrypoints", [])
            if isinstance(item, str) and self._safe_relative(item)
        )
        unit.observation_refs.add(observation.source_ref)
        if payload.get("name"):
            unit.artifact_hints.setdefault("names", []).append(str(payload["name"]))
        if payload.get("container_images"):
            unit.artifact_hints.setdefault("container_images", []).extend(payload["container_images"])

    @staticmethod
    def _finalize_units(
        units: dict[str, _UnitAccumulator],
        namespace: str,
    ) -> tuple[BuildUnitCandidate, ...]:
        roots = sorted(units, key=lambda value: (value.count("/"), value))
        result: list[BuildUnitCandidate] = []
        for root in roots:
            unit = units[root]
            system = min(unit.systems, key=lambda item: _BUILD_PRECEDENCE.get(item, 99))
            hints = {
                key: sorted(set(values)) if isinstance(values, list) else values
                for key, values in unit.artifact_hints.items()
            }
            key = f"build-unit:{root}"
            result.append(BuildUnitCandidate(
                candidate_key=f"{namespace}/{key}" if namespace else key,
                source_root=root,
                build_system=system,
                manifest_paths=tuple(sorted(unit.manifest_paths)),
                entrypoints=tuple(sorted(unit.entrypoints)),
                artifact_hints=hints,
                observation_refs=tuple(sorted(unit.observation_refs)),
                ownership_priority=root.count("/") + (0 if root == "." else 1),
            ))
        return tuple(result)
