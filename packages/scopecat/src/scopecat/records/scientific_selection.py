"""Scientific intent and the exact evidence retained by a checked preview."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from scopecat.records.config_context import ConfigContextRef, ContextRunConfigSource
from scopecat.records.parameter_revision import ParameterRevisionRef
from scopecat.records.parameter_update import ParameterUpdate
from scopecat.records.plan_ref import PlanConfigRef
from scopecat.records.run import (
    AnalysisCandidateRunConfigSource,
    ConfigRegistryRunConfigSource,
    ParameterRunConfigSource,
)
from scopecat.records.sample import SampleId
from scopecat.records.scientific_binding import (
    InlineSamplesSubject,
    RegisteredTargetSubject,
    ResolvedScientificBinding,
    UnboundSubject,
)
from scopecat.records.scientific_scope import BatchScope, DeclaredBatch, UnscopedBatch
from scopecat.records.setup import SetupRevisionRef
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


class ParameterConfiguration(_SelectionModel):
    """Independent parameters; omitted setup resolves current authority at preview."""

    kind: Literal["parameters"] = "parameters"
    ref: ParameterRevisionRef
    setup: SetupRevisionRef | None = None
    overrides: tuple[ParameterUpdate, ...] = Field(default=(), max_length=256)


class WorkingPointConfiguration(_SelectionModel):
    kind: Literal["working_point"] = "working_point"
    ref: ConfigContextRef
    overrides: tuple[ParameterUpdate, ...] = Field(default=(), max_length=256)


class CandidateConfiguration(_SelectionModel):
    kind: Literal["candidate"] = "candidate"
    source: AnalysisCandidateRunConfigSource


type ConfigurationChoice = Annotated[
    ActiveConfiguration
    | ParameterConfiguration
    | SavedConfiguration
    | WorkingPointConfiguration
    | CandidateConfiguration,
    Field(discriminator="kind"),
]

type LaunchConfigSource = (
    ConfigRegistryRunConfigSource
    | ParameterRunConfigSource
    | ContextRunConfigSource
    | AnalysisCandidateRunConfigSource
)


class ScientificSelection(_SelectionModel):
    subject: SubjectChoice = Field(default_factory=UnboundSubjectChoice)
    configuration: ConfigurationChoice = Field(default_factory=ActiveConfiguration)
    batch: BatchScope = Field(default_factory=UnscopedBatch)

    def intent_content(self) -> dict[str, JsonValue]:
        return self.model_dump(mode="json")


class ReviewedScientificSelection(_SelectionModel):
    binding: ResolvedScientificBinding
    config_source: LaunchConfigSource


def require_selection_binding(
    selection: ScientificSelection, binding: ResolvedScientificBinding
) -> None:
    """Validate intent against its exact projection, without reading mutable heads."""
    subject = selection.subject
    evidence = binding.subject
    if isinstance(subject, UnboundSubjectChoice):
        if not isinstance(evidence, UnboundSubject) or isinstance(
            selection.batch, DeclaredBatch
        ):
            raise ValueError("unbound selection cannot carry sample or batch evidence")
    elif isinstance(subject, RegisteredTargetChoice):
        if (
            not isinstance(evidence, RegisteredTargetSubject)
            or evidence.ref != subject.ref
        ):
            raise ValueError("selected target differs from reviewed target")
    else:
        if not isinstance(evidence, InlineSamplesSubject) or len(evidence.samples) != 1:
            raise ValueError("sample selection requires one inline subject")
        sample = evidence.samples[0]
        if (
            sample.role != "subject"
            or sample.sample_id != subject.sample_id
            or subject.revision not in (None, sample.revision)
        ):
            raise ValueError("selected sample differs from reviewed sample")
    batch_id = (
        selection.batch.id if isinstance(selection.batch, DeclaredBatch) else None
    )
    if any(sample.batch_id != batch_id for sample in binding.samples):
        raise ValueError("selected batch differs from reviewed scientific evidence")
