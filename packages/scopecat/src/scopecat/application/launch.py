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
    model_validator,
)

from scopecat.application.controls import ControlEdit, LaunchControl, LaunchControlValue
from scopecat.automation.interpretations import InterpretationRequest
from scopecat.config.parameter_updates import ParameterUpdate
from scopecat.kernel.content_identity import sha256_json_hash
from scopecat.planning.preflight import PreflightSummary
from scopecat.records.author_revision import AuthorRevisionRef
from scopecat.records.config_context import ConfigContextRef, ContextRunConfigSource
from scopecat.records.content import Sha256ContentHash
from scopecat.records.run import ConfigRegistryRunConfigSource

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


type LaunchConfigSource = ConfigRegistryRunConfigSource | ContextRunConfigSource


class LaunchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    code_revision: AuthorRevisionRef | None = None
    action: Literal["list", "preview", "submit"]
    experiment: str = ""
    version: str = ""
    request_key: str = ""
    sample: str | None = None
    actor: str = "operator"
    inputs: dict[str, JsonValue] = Field(default_factory=dict)
    control_edits: dict[str, ControlEdit] = Field(default_factory=dict)
    expected_request_hash: Sha256ContentHash | None = None
    context: ConfigContextRef | None = None
    overrides: tuple[ParameterUpdate, ...] = Field(default=(), max_length=256)
    config_source: LaunchConfigSource | None = None

    @model_validator(mode="after")
    def validate_action(self) -> LaunchRequest:
        if self.action != "list" and not (
            self.experiment and self.version and self.actor.strip()
        ):
            raise ValueError("launch requires experiment, version and actor")
        if self.overrides and self.context is None:
            raise ValueError("parameter overrides require an explicit context")
        if self.action == "submit":
            if not self.request_key.strip() or self.expected_request_hash is None:
                raise ValueError(
                    "submit requires a request key and preview request hash"
                )
            if self.config_source is None or (
                isinstance(self.config_source, ConfigRegistryRunConfigSource)
                and self.config_source.registry_generation is None
            ):
                raise ValueError("submit requires the preview's configuration binding")
            if self.expected_request_hash != self.request_hash:
                raise ValueError("request changed since preview; preview again")
        return self

    @property
    def request_hash(self) -> Sha256ContentHash:
        """Identify visible intent, excluding retry key and preview fences."""
        return sha256_json_hash(
            {
                "experiment": self.experiment,
                "version": self.version,
                "inputs": self.inputs,
                "sample": self.sample,
                "actor": self.actor,
                **(
                    {
                        "context": self.context.model_dump(mode="json"),
                        "overrides": [
                            edit.model_dump(mode="json") for edit in self.overrides
                        ],
                    }
                    if self.context is not None
                    else {}
                ),
                **(
                    {
                        "control_edits": {
                            name: edit.model_dump(mode="json")
                            for name, edit in self.control_edits.items()
                        }
                    }
                    if self.control_edits
                    else {}
                ),
            }
        )


class LaunchPreview(BaseModel):
    """Compile-only evidence for exactly one request and immutable configuration."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    code_revision: AuthorRevisionRef | None = None
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
