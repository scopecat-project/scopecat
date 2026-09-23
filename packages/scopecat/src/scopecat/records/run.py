"""Accepted run snapshots and terminal outcomes."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from scopecat.kernel.run_outcome import RunOutcome, RunStatus, utc_now
from scopecat.records.candidate_input import (
    AnalysisCandidateRunConfigSource as AnalysisCandidateRunConfigSource,
)
from scopecat.records.config import ConfigContentHash
from scopecat.records.config_context import ContextRunConfigSource
from scopecat.records.measurement_context import MeasurementContext
from scopecat.records.parameter_revision import ParameterRevisionRef
from scopecat.records.parameter_update import ParameterUpdate
from scopecat.records.sample import SampleBinding
from scopecat.records.scientific_binding import ResolvedScientificBinding
from scopecat.records.setup import SetupRevisionRef


class ConfigRegistryRunConfigSource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["config_registry"] = "config_registry"
    selector: str
    entry_id: str
    config_ref: str
    content_hash: ConfigContentHash
    registry_generation: int | None = Field(
        default=None,
        ge=1,
        description=("Historical activation generation for the active selector only."),
    )


class ParameterRunConfigSource(BaseModel):
    """Exact independent inputs; carries no working-point calibration ownership."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["parameter_revision"] = "parameter_revision"
    parameters: ParameterRevisionRef
    setup: SetupRevisionRef
    content_hash: ConfigContentHash
    overrides: tuple[ParameterUpdate, ...] = Field(default=(), max_length=256)


type RunConfigSource = Annotated[
    ConfigRegistryRunConfigSource
    | ParameterRunConfigSource
    | AnalysisCandidateRunConfigSource
    | ContextRunConfigSource,
    Field(discriminator="kind"),
]


class RunSnapshot(BaseModel):
    """Accepted run identity, configuration binding, and terminal outcome."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
    )

    run_id: str
    scientific_binding: ResolvedScientificBinding
    created_at: datetime = Field(default_factory=utc_now)
    outcome: RunOutcome | None = None
    config_content_hash: ConfigContentHash
    config_source: RunConfigSource | None = None
    samples: tuple[SampleBinding, ...] = ()

    @model_validator(mode="after")
    def validate_identity(self) -> RunSnapshot:
        if self.scientific_binding.config_content_hash != self.config_content_hash:
            raise ValueError("scientific binding config hash does not match run")
        if sorted(
            self.scientific_binding.samples, key=lambda item: item.role
        ) != sorted(self.samples, key=lambda item: item.role):
            raise ValueError("scientific binding samples do not match run")
        if (
            self.config_source is not None
            and self.config_source.content_hash != self.config_content_hash
        ):
            msg = "run config source hash does not match its accepted snapshot hash"
            raise ValueError(msg)
        if self.outcome is not None and self.outcome.run_id != self.run_id:
            msg = "run outcome run_id does not match its snapshot"
            raise ValueError(msg)
        roles = tuple(binding.role for binding in self.samples)
        if len(roles) != len(set(roles)):
            raise ValueError("run sample binding roles must be unique")
        sample_ids = tuple(binding.sample_id for binding in self.samples)
        if len(sample_ids) != len(set(sample_ids)):
            raise ValueError("one sample cannot fill multiple run roles")
        return self

    @property
    def measurement_context(self) -> MeasurementContext | None:
        """Exact saved/candidate inputs, or None for overrides and unsaved config.

        This derives evidence from the retained run, never current branch heads.
        A context alone says nothing about execution success or calibration.
        """
        source = self.config_source
        if isinstance(source, AnalysisCandidateRunConfigSource):
            return MeasurementContext.from_binding(source, self.scientific_binding)
        if not isinstance(source, ParameterRunConfigSource) or source.overrides:
            return None
        return MeasurementContext.from_binding(
            source.parameters, self.scientific_binding
        )

    @property
    def status(self) -> RunStatus:
        """Return a compact display status from the optional outcome."""

        if self.outcome is None:
            return "planned"
        return self.outcome.status
