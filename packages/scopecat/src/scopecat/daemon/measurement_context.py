"""Read-only resolution of mutable choices into exact measurement inputs."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from scopecat.records.candidate_input import AnalysisCandidateRunConfigSource
from scopecat.records.measurement_context import MeasurementContext
from scopecat.records.parameter_branch import ParameterBranch
from scopecat.records.parameter_revision import ParameterRevisionRef
from scopecat.records.sample import SampleSelector
from scopecat.records.setup import SetupRevisionRef
from scopecat.records.target_catalog import TargetRevisionRef


class MeasurementContextResolve(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    branch: str | None = Field(default=None, min_length=1)
    parameters: ParameterRevisionRef | AnalysisCandidateRunConfigSource | None = None
    setup: SetupRevisionRef | None = None
    samples: tuple[SampleSelector, ...] = Field(default=(), max_length=32)
    target: TargetRevisionRef | None = None

    @model_validator(mode="after")
    def validate_selection(self) -> MeasurementContextResolve:
        if (self.branch is None) == (self.parameters is None):
            raise ValueError("choose a parameter branch or an exact parameter input")
        if self.target is not None and self.samples:
            raise ValueError("choose a registered target or inline samples, not both")
        if any(sample.revision is None for sample in self.samples):
            raise ValueError("select exact sample revisions for a measurement context")
        return self


class MeasurementContextResolution(BaseModel):
    """Exact inputs; candidates have no branch/setup selection receipt."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    context: MeasurementContext
    branch: ParameterBranch | None = None
    setup: SetupRevisionRef | None
