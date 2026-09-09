"""Accepted run snapshots and terminal outcomes."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from scopecat.kernel.run_outcome import RunOutcome, RunStatus, utc_now
from scopecat.records.config import ConfigContentHash
from scopecat.records.config_context import ContextRunConfigSource
from scopecat.records.sample import SampleBinding


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
        description=(
            "For active: historical activation generation. For an exact entry: "
            "optional observed lab-generation fence, not a claim of activation."
        ),
    )


class AnalysisCandidateRunConfigSource(BaseModel):
    """Analysis candidate resolved for one run without becoming the default."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["analysis_candidate"] = "analysis_candidate"
    source_run_id: str
    analysis_record_id: str
    proposal_id: str
    base_config_content_hash: ConfigContentHash
    content_hash: ConfigContentHash

    @model_validator(mode="after")
    def validate_identity(self) -> AnalysisCandidateRunConfigSource:
        if (
            not self.source_run_id
            or not self.analysis_record_id
            or not self.proposal_id
        ):
            raise ValueError("analysis candidate run source identity must be non-empty")
        return self


type RunConfigSource = Annotated[
    ConfigRegistryRunConfigSource
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
    created_at: datetime = Field(default_factory=utc_now)
    outcome: RunOutcome | None = None
    config_content_hash: ConfigContentHash
    config_source: RunConfigSource | None = None
    samples: tuple[SampleBinding, ...] = ()

    @model_validator(mode="after")
    def validate_identity(self) -> RunSnapshot:
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
    def status(self) -> RunStatus:
        """Return a compact display status from the optional outcome."""

        if self.outcome is None:
            return "planned"
        return self.outcome.status
