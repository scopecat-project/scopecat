"""Immutable scientific evidence shared by run producers and admission."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from scopecat.records.content import Sha256ContentHash
from scopecat.records.execution_scenario import SoftwareExecutionScenario
from scopecat.records.sample import SampleBinding, SampleSelector
from scopecat.records.scientific_scope import MeasurementTarget, TargetEntity
from scopecat.records.target_catalog import TargetRevisionRef


class _BindingModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class UnboundSubject(_BindingModel):
    kind: Literal["unbound"] = "unbound"


class InlineSamplesSubject(_BindingModel):
    kind: Literal["inline_samples"] = "inline_samples"
    catalog_id: str = Field(min_length=1)
    samples: tuple[SampleBinding, ...] = Field(min_length=1)


class EntityProjection(_BindingModel):
    target_entity: TargetEntity
    runtime_entity_id: str


class RegisteredTargetSubject(_BindingModel):
    kind: Literal["registered_target"] = "registered_target"
    ref: TargetRevisionRef
    content: MeasurementTarget
    sample: SampleBinding
    projection: tuple[EntityProjection, ...]


type ResolvedSubject = Annotated[
    UnboundSubject | InlineSamplesSubject | RegisteredTargetSubject,
    Field(discriminator="kind"),
]


class ResolvedScientificBinding(_BindingModel):
    codec: Literal["scopecat.scientific-binding.v2"] = "scopecat.scientific-binding.v2"
    subject: ResolvedSubject
    scenario: SoftwareExecutionScenario | None = None
    config_content_hash: Sha256ContentHash
    setup_content_hash: Sha256ContentHash

    @property
    def samples(self) -> tuple[SampleBinding, ...]:
        if isinstance(self.subject, InlineSamplesSubject):
            return self.subject.samples
        if isinstance(self.subject, RegisteredTargetSubject):
            return (self.subject.sample,)
        return ()

    def sample_selectors(self) -> tuple[SampleSelector, ...]:
        return tuple(
            SampleSelector(
                role=sample.role,
                sample_id=sample.sample_id,
                revision=sample.revision,
                context_id=sample.context_id,
                batch_id=sample.batch_id,
            )
            for sample in self.samples
        )
