"""Scientific intent and the exact evidence retained by a checked preview."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from scopecat.records.config_context import ConfigContextRef, ContextRunConfigSource
from scopecat.records.parameter_update import ParameterUpdate
from scopecat.records.plan_ref import PlanConfigRef
from scopecat.records.run import (
    AnalysisCandidateRunConfigSource,
    ConfigRegistryRunConfigSource,
)
from scopecat.records.sample import SampleId
from scopecat.records.scientific_binding import ResolvedScientificBinding
from scopecat.records.scientific_scope import BatchScope, UnscopedBatch
from scopecat.records.target_catalog import TargetRevisionRef


class _SelectionModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class UnboundSubjectChoice(_SelectionModel):
    kind: Literal["unbound"] = "unbound"


class SampleSubjectChoice(_SelectionModel):
    kind: Literal["sample"] = "sample"
    sample_id: SampleId
    revision: int | None = Field(default=None, ge=1)


class RegisteredTargetChoice(_SelectionModel):
    kind: Literal["registered_target"] = "registered_target"
    ref: TargetRevisionRef


type SubjectChoice = Annotated[
    UnboundSubjectChoice | SampleSubjectChoice | RegisteredTargetChoice,
    Field(discriminator="kind"),
]


class ActiveConfiguration(_SelectionModel):
    kind: Literal["active"] = "active"


class SavedConfiguration(_SelectionModel):
    kind: Literal["saved"] = "saved"
    ref: PlanConfigRef


class WorkingPointConfiguration(_SelectionModel):
    kind: Literal["working_point"] = "working_point"
    ref: ConfigContextRef
    overrides: tuple[ParameterUpdate, ...] = Field(default=(), max_length=256)


class CandidateConfiguration(_SelectionModel):
    kind: Literal["candidate"] = "candidate"
    source: AnalysisCandidateRunConfigSource


type ConfigurationChoice = Annotated[
    ActiveConfiguration
    | SavedConfiguration
    | WorkingPointConfiguration
    | CandidateConfiguration,
    Field(discriminator="kind"),
]

type LaunchConfigSource = (
    ConfigRegistryRunConfigSource
    | ContextRunConfigSource
    | AnalysisCandidateRunConfigSource
)


class ScientificSelection(_SelectionModel):
    subject: SubjectChoice = Field(default_factory=UnboundSubjectChoice)
    configuration: ConfigurationChoice = Field(default_factory=ActiveConfiguration)
    batch: BatchScope = Field(default_factory=UnscopedBatch)

    def intent_content(self) -> dict[str, JsonValue]:
        content = self.model_dump(mode="json")
        if isinstance(self.configuration, CandidateConfiguration):
            content["configuration"]["source"].pop("registry_generation", None)
        return content


class ReviewedScientificSelection(_SelectionModel):
    binding: ResolvedScientificBinding
    config_source: LaunchConfigSource
