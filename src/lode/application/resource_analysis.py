"""Strict V1 semantic resource-analysis protocol."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from lode.structured_output import StrictResponseModel


class ResourceComponentAnnotation(StrictResponseModel):
    component_key: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{0,99}$")
    display_name: str = Field(min_length=1, max_length=200)
    component_kind: Literal[
        "service", "worker", "job", "gateway", "library_runtime", "unknown"
    ]
    build_unit_keys: tuple[str, ...] = Field(min_length=1, max_length=50)
    observation_refs: tuple[str, ...] = Field(min_length=1, max_length=100)
    aliases: tuple[str, ...] = Field(max_length=50)
    description: str = Field(max_length=4_000)
    entrypoints: tuple[str, ...] = Field(max_length=100)
    dependencies: tuple[str, ...] = Field(max_length=100)
    runbooks: tuple[str, ...] = Field(max_length=50)
    owners: tuple[str, ...] = Field(max_length=50)

    @field_validator("display_name", "description")
    @classmethod
    def text_is_trimmed(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("resource analysis text must be trimmed")
        return value

    @model_validator(mode="after")
    def references_are_unique(self):
        for field_name in (
            "build_unit_keys",
            "observation_refs",
            "aliases",
            "entrypoints",
            "dependencies",
            "runbooks",
            "owners",
        ):
            values = getattr(self, field_name)
            if len(values) != len(set(values)):
                raise ValueError(f"{field_name} must be unique")
        return self


class ResourceAnalysisPayload(StrictResponseModel):
    components: tuple[ResourceComponentAnnotation, ...] = Field(max_length=200)

    @model_validator(mode="after")
    def component_identities_are_unambiguous(self):
        component_keys = [item.component_key for item in self.components]
        if len(component_keys) != len(set(component_keys)):
            raise ValueError("component keys must be unique")
        build_unit_keys = [
            key for component in self.components for key in component.build_unit_keys
        ]
        if len(build_unit_keys) != len(set(build_unit_keys)):
            raise ValueError("a build unit may belong to only one component")
        return self


def resource_analysis_json_schema() -> dict[str, Any]:
    schema = ResourceAnalysisPayload.response_json_schema()
    schema["title"] = "resource-analysis.v1"
    return schema
