"""Durable records owned by the configuration registry."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)

from scopecat.kernel.run_outcome import utc_now
from scopecat.records.analysis import ProjectAnalysisDecisionReference
from scopecat.records.config import ConfigContentHash
from scopecat.records.config_context import ConfigContextMetadata, ConfigContextRef
from scopecat.records.parameter_revision import ParameterRevisionRef
from scopecat.records.setup import SetupRevisionRef

_MAX_CALIBRATION_MERGE_CONTRIBUTIONS = 200


class _FrozenRegistryModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
    )


class DirectConfigRegistrySource(_FrozenRegistryModel):
    kind: Literal["direct_config_profile"] = "direct_config_profile"


class ParameterConfigRegistrySource(_FrozenRegistryModel):
    """Parameter input composed against one exact saved executable setup."""

    kind: Literal["parameter_revision"] = "parameter_revision"
    setup: SetupRevisionRef


class BoundParameterRegistrySource(_FrozenRegistryModel):
    """Execution combination of two independently saved exact revisions."""

    kind: Literal["bound_parameters"] = "bound_parameters"
    parameters: ParameterRevisionRef
    setup: SetupRevisionRef


class ManualConfigDraftRegistrySource(_FrozenRegistryModel):
    """Provenance for typed parameter edits derived from an active entry."""

    kind: Literal["manual_parameter_updates"] = "manual_parameter_updates"
    base_entry_id: str
    base_config_content_hash: ConfigContentHash
    base_registry_generation: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_identity(self) -> ManualConfigDraftRegistrySource:
        if not self.base_entry_id:
            raise ValueError("manual config draft base entry id must be non-empty")
        return self


class ManualCandidateAcceptance(_FrozenRegistryModel):
    """Operator-reviewed acceptance without an automated verification run."""

    kind: Literal["manual_review"] = "manual_review"


class CrossRunCandidateAcceptance(_FrozenRegistryModel):
    """Automated acceptance backed by one positive cross-run decision fact."""

    kind: Literal["cross_run_verification"] = "cross_run_verification"
    decision: ProjectAnalysisDecisionReference


CandidateAcceptance = Annotated[
    ManualCandidateAcceptance | CrossRunCandidateAcceptance,
    Field(discriminator="kind"),
]


class CandidateConfigRegistrySource(_FrozenRegistryModel):
    kind: Literal["candidate_config"] = "candidate_config"
    run_id: str
    proposal_id: str
    base_config_content_hash: ConfigContentHash
    acceptance: CandidateAcceptance

    @model_validator(mode="after")
    def validate_evidence(self) -> CandidateConfigRegistrySource:
        if not self.run_id or not self.proposal_id:
            msg = "candidate registry source identity fields must be non-empty"
            raise ValueError(msg)
        return self


class SetupRebindRegistrySource(_FrozenRegistryModel):
    """Explicitly composed setup and parameter input; no calibration acceptance."""

    kind: Literal["setup_rebind"] = "setup_rebind"
    base: ConfigContextRef
    setup: SetupRevisionRef


class ContextConfigRegistrySource(_FrozenRegistryModel):
    kind: Literal["parameter_context"] = "parameter_context"
    context: ConfigContextMetadata
    rebind: SetupRebindRegistrySource | None = None
    publication: CandidateConfigRegistrySource | None = None


ConfigRegistryEntrySource = Annotated[
    DirectConfigRegistrySource
    | BoundParameterRegistrySource
    | ParameterConfigRegistrySource
    | ManualConfigDraftRegistrySource
    | CandidateConfigRegistrySource
    | ContextConfigRegistrySource
    | SetupRebindRegistrySource,
    Field(discriminator="kind"),
]


class ConfigRegistryEntry(_FrozenRegistryModel):
    id: str
    config_ref: str
    content_hash: ConfigContentHash
    source: ConfigRegistryEntrySource
    actor: str
    note: str = ""
    recorded_at: datetime = Field(default_factory=utc_now)


class ConfigRegistryEntryPage(_FrozenRegistryModel):
    """Newest-first keyset page of saved configuration revisions."""

    items: tuple[ConfigRegistryEntry, ...] = ()
    next_cursor: int | None = Field(default=None, ge=1)
