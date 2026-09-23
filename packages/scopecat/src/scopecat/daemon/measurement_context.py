"""Read-only resolution of mutable choices into exact measurement inputs."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from scopecat.records.measurement_context import MeasurementContext
from scopecat.records.parameter_branch import ParameterBranch
from scopecat.records.sample import SampleSelector
from scopecat.records.setup import SetupRevisionRef
from scopecat.records.target_catalog import TargetRevisionRef


class MeasurementContextResolve(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    branch: str = Field(min_length=1)
    setup: SetupRevisionRef | None = None
    samples: tuple[SampleSelector, ...] = Field(default=(), max_length=32)
    target: TargetRevisionRef | None = None

    @model_validator(mode="after")
    def exact_samples(self) -> MeasurementContextResolve:
        if self.target is not None and self.samples:
            raise ValueError("choose a registered target or inline samples, not both")
        if any(sample.revision is None for sample in self.samples):
            raise ValueError("select exact sample revisions for a measurement context")
        return self


class MeasurementContextResolution(BaseModel):
    """Exact inputs plus the branch generation and setup revision used to resolve."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    context: MeasurementContext
    branch: ParameterBranch
    setup: SetupRevisionRef
