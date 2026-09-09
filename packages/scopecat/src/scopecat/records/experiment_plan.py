"""Named immutable launch intentions; preview/admission permissions are not stored."""

from collections.abc import Mapping
from datetime import datetime
from typing import Annotated, cast

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    PlainSerializer,
    model_validator,
)

from scopecat.kernel.frozen import FrozenMapping, freeze_json_mapping, thaw_json_value
from scopecat.records.author_revision import AuthorRevisionRef
from scopecat.records.config_context import ConfigContextRef
from scopecat.records.content import Sha256ContentHash
from scopecat.records.control_edit import ControlEdit
from scopecat.records.parameter_update import ParameterUpdate
from scopecat.records.plan_ref import (
    ExperimentPlanRef,
    PlanAnalysisSource,
    PlanConfigRef,
)
from scopecat.records.sample import SampleBinding


def _freeze_inputs(value: Mapping[str, JsonValue]) -> Mapping[str, JsonValue]:
    return freeze_json_mapping(value, path="plan.inputs")


def _input_json(value: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    return cast("dict[str, JsonValue]", thaw_json_value(value))


def _freeze_controls(value: Mapping[str, ControlEdit]) -> Mapping[str, ControlEdit]:
    return FrozenMapping(value.items())


def _control_json(value: Mapping[str, ControlEdit]) -> dict[str, ControlEdit]:
    return dict(value)


type PlanInputs = Annotated[
    Mapping[str, JsonValue],
    AfterValidator(_freeze_inputs),
    PlainSerializer(_input_json, return_type=dict[str, JsonValue]),
]
type PlanControlEdits = Annotated[
    Mapping[str, ControlEdit],
    AfterValidator(_freeze_controls),
    PlainSerializer(_control_json, return_type=dict[str, ControlEdit]),
]


class ExperimentPlanDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    experiment: str = Field(min_length=1)
    version: str = Field(min_length=1)
    definition_hash: Sha256ContentHash
    code_revision: AuthorRevisionRef | None = None
    inputs: PlanInputs = Field(default_factory=dict)
    control_edits: PlanControlEdits = Field(default_factory=dict)
    configuration: PlanConfigRef | None = None
    context: ConfigContextRef | None = None
    overrides: tuple[ParameterUpdate, ...] = Field(default=(), max_length=256)
    sample: SampleBinding | None = None
    source: PlanAnalysisSource | None = None

    @model_validator(mode="after")
    def exact_configuration(self) -> ExperimentPlanDefinition:
        if (self.configuration is None) == (self.context is None):
            raise ValueError("plan requires exactly one saved configuration or context")
        if self.overrides and self.context is None:
            raise ValueError("plan overrides require an explicit context")
        return self


class ExperimentPlanSave(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str = Field(min_length=1, max_length=160)
    definition: ExperimentPlanDefinition
    saved_by: str = Field(min_length=1)
    previous: ExperimentPlanRef | None = None
    copied_from: ExperimentPlanRef | None = None

    @model_validator(mode="after")
    def one_parent(self) -> ExperimentPlanSave:
        if self.previous is not None and self.copied_from is not None:
            raise ValueError("choose edit or copy, not both")
        if not self.name.strip() or not self.saved_by.strip():
            raise ValueError("plan name and saver must not be blank")
        return self


class ExperimentPlanRevision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    ref: ExperimentPlanRef
    name: str
    definition: ExperimentPlanDefinition
    saved_by: str
    saved_at: datetime
    previous: ExperimentPlanRef | None = None
    copied_from: ExperimentPlanRef | None = None


class ExperimentPlanList(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    items: tuple[ExperimentPlanRevision, ...]
