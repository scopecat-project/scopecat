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

from scopecat.automation.interpretations import InterpretationRequest
from scopecat.kernel.content_identity import sha256_json_hash
from scopecat.planning.preflight import PreflightSummary
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


class LaunchCatalog(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    entries: tuple[LaunchCatalogEntry, ...] = ()


class LaunchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    action: Literal["list", "preview", "submit"]
    experiment: str = ""
    version: str = ""
    request_key: str = ""
    sample: str | None = None
    actor: str = "operator"
    inputs: dict[str, JsonValue] = Field(default_factory=dict)
    expected_request_hash: Sha256ContentHash | None = None
    config_source: ConfigRegistryRunConfigSource | None = None

    @model_validator(mode="after")
    def validate_action(self) -> LaunchRequest:
        if self.action != "list" and not (
            self.experiment and self.version and self.actor.strip()
        ):
            raise ValueError("launch requires experiment, version and actor")
        if self.action == "submit":
            if not self.request_key.strip() or self.expected_request_hash is None:
                raise ValueError(
                    "submit requires a request key and preview request hash"
                )
            if (
                self.config_source is None
                or self.config_source.registry_generation is None
            ):
                raise ValueError(
                    "submit requires the preview's active configuration binding"
                )
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
            }
        )


class LaunchPreview(BaseModel):
    """Compile-only evidence for exactly one request and immutable configuration."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    experiment_id: str
    request_hash: Sha256ContentHash
    config_source: ConfigRegistryRunConfigSource
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


class LaunchSubmission(BaseModel):
    """Durable procedure identity; existing steps retain its output references."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    procedure_id: str
    dispatch_error: str | None = None


type LaunchResult = LaunchCatalog | LaunchPreview | LaunchSubmission
type LaunchProvider = Callable[[LabClient, LaunchRequest], LaunchResult]
