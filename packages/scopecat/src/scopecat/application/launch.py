"""Typed project-owned catalog, pure preview and durable submission boundary."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Literal, cast

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    SerializerFunctionWrapHandler,
    model_serializer,
)

from scopecat.application.controls import LaunchControl, LaunchControlValue
from scopecat.automation.interpretations import InterpretationRequest
from scopecat.planning.preflight import PreflightSummary
from scopecat.records.author_revision import AuthorRevisionRef
from scopecat.records.content import Sha256ContentHash
from scopecat.records.launch_request import LaunchConfigSource, LaunchRequest
from scopecat.records.manual_preview import ManualPreviewFence
from scopecat.records.plan_ref import ExperimentPlanRef
from scopecat.records.sample import SampleBinding

if TYPE_CHECKING:
    from scopecat.api.lab import LabClient


class _ProjectSchema(BaseModel):
    model_config = ConfigDict(extra="allow")

    @model_serializer(mode="wrap")
    def supplied_keywords(self, handler: SerializerFunctionWrapHandler):
        # An inferred return type keeps the model's generated field schema.
        # Absent schema keywords must stay absent, not become e.g. minimum=null.
        return {
            name: value
            for name, value in cast("dict[str, JsonValue]", handler(self)).items()
            if name in self.model_fields_set
        }


class LaunchField(_ProjectSchema):
    """The small console form surface; additional project JSON Schema is retained."""

    type: str | tuple[str, ...] | None = None
    title: str | None = None
    description: str | None = None
    default: JsonValue = None
    enum: tuple[JsonValue, ...] | None = None
    items: LaunchField | bool | tuple[LaunchField | bool, ...] | None = None
    minimum: float | None = None
    maximum: float | None = None
    exclusiveMinimum: float | None = None


class LaunchInputSchema(_ProjectSchema):
    """Project-owned top-level request schema, rendered only for supported fields."""

    properties: dict[str, LaunchField | bool] = Field(default_factory=dict)
    required: tuple[str, ...] = Field(default_factory=tuple)


class LaunchCatalogEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    title: str
    description: str
    actions: tuple[Literal["preview", "submit"], ...]
    kind: Literal["diagnostic", "calibration"]
    configuration_effect: Literal["none", "candidate", "activation_after_review"]
    request: LaunchInputSchema
    review: InterpretationRequest | None = None
    controls: tuple[LaunchControl, ...] = ()


class LaunchCatalog(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    code_revision: AuthorRevisionRef | None = None
    entries: tuple[LaunchCatalogEntry, ...] = ()


class LaunchPreview(BaseModel):
    """Compile-only evidence for exactly one request and immutable configuration."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    code_revision: AuthorRevisionRef | None = None
    plan_ref: ExperimentPlanRef | None = None
    definition_hash: Sha256ContentHash | None = None
    sample_binding: SampleBinding | None = None
    experiment_id: str
    request_hash: Sha256ContentHash
    config_source: LaunchConfigSource
    point_count: int = Field(
        ge=0,
        description=(
            "Initial point count of the first previewed experiment, "
            "not a procedure total."
        ),
    )
    preflight: PreflightSummary | None = None
    manual_state: ManualPreviewFence | None = None
    resources: tuple[str, ...] = ()
    summary: str
    resolved_inputs: dict[str, JsonValue] = Field(default_factory=dict)
    controls: tuple[LaunchControlValue, ...] = ()


class LaunchSubmission(BaseModel):
    """Durable procedure identity; existing steps retain its output references."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    procedure_id: str
    dispatch_error: str | None = None


type LaunchResult = LaunchCatalog | LaunchPreview | LaunchSubmission
type LaunchProvider = Callable[[LabClient, LaunchRequest], LaunchResult]


def validate_launch_control_edits(
    catalog: LaunchCatalog, request: LaunchRequest
) -> None:
    """Reject edits outside the maintained declaration at either entry point."""
    if not request.control_edits:
        return
    entry = next(
        (
            entry
            for entry in catalog.entries
            if entry.id == request.experiment and entry.version == request.version
        ),
        None,
    )
    if entry is None:
        raise ValueError("unknown experiment or changed control catalog version")
    fields = {field.id: field for field in entry.controls}
    for name, edit in request.control_edits.items():
        if name not in fields:
            raise ValueError(f"unknown control {name!r} for {entry.id}")
        field = fields[name]
        if field.ownership != "editable":
            raise ValueError(f"{name} is {field.ownership}-owned")
        if edit.mode == "scan" and not field.scannable:
            raise ValueError(f"{name} is not scannable")
