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

from scopecat.kernel.content_identity import stable_content_hash
from scopecat.kernel.run_outcome import utc_now
from scopecat.records.analysis import ProjectAnalysisDecisionReference
from scopecat.records.config import ConfigContentHash
from scopecat.records.config_context import ConfigContextMetadata, ConfigContextRef
from scopecat.records.content import Sha256ContentHash
from scopecat.records.parameter_revision import ParameterRevisionRef
from scopecat.records.setup import SetupRevisionRef

_CONFIG_ACTIVATION_INTENT_CODEC = "scopecat.config-activation-intent.v1"
_CONFIG_PUBLISH_INTENT_CODEC = "scopecat.config-publish-intent.v1"
_MAX_CALIBRATION_MERGE_CONTRIBUTIONS = 200

type _NonEmptyText = Annotated[str, Field(min_length=1)]


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


def _exclude_none(value: object) -> bool:
    return value is None


class ConfigRegistryActivationRecord(_FrozenRegistryModel):
    generation: int = Field(ge=1)
    action: Literal["activation", "inventory_migration"]
    entry_id: str
    entry_content_hash: ConfigContentHash
    # Most recent activation of this exact entry before this restoration.
    # No new calibration decision or validity is implied by restoring content.
    restored_from_generation: int | None = Field(
        default=None, ge=1, exclude_if=_exclude_none
    )
    previous_entry_id: str | None = None
    previous_entry_content_hash: ConfigContentHash | None = None
    actor: str
    note: str = ""
    recorded_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_identity(self) -> ConfigRegistryActivationRecord:
        if not self.entry_id or not self.actor.strip():
            msg = "config registry activation identity fields must be non-empty"
            raise ValueError(msg)
        if (self.previous_entry_id is None) != (
            self.previous_entry_content_hash is None
        ):
            msg = "previous registry entry id and content hash must be paired"
            raise ValueError(msg)
        return self


def config_activation_intent_hash(
    *,
    entry_id: str,
    expected_generation: int,
    actor: str,
    note: str = "",
) -> Sha256ContentHash:
    """Identify one exact activate-entry intent independently of its retry key."""

    identity = {
        "codec": _CONFIG_ACTIVATION_INTENT_CODEC,
        "entry_id": entry_id,
        "expected_generation": expected_generation,
        "actor": actor,
        "note": note,
    }
    return f"sha256:{stable_content_hash(identity)}"


def config_publish_intent_hash(
    *,
    source_intent_hash: Sha256ContentHash,
    entry_id: str,
    expected_generation: int,
    actor: str,
    note: str = "",
) -> Sha256ContentHash:
    """Identify one exact publish intent independently of its retry key."""

    identity = {
        "codec": _CONFIG_PUBLISH_INTENT_CODEC,
        "source_intent_hash": source_intent_hash,
        "entry_id": entry_id,
        "expected_generation": expected_generation,
        "actor": actor,
        "note": note,
    }
    return f"sha256:{stable_content_hash(identity)}"


class ConfigPublishOperation(_FrozenRegistryModel):
    """Durable result identity for one idempotent config publication."""

    operation_id: str
    intent_hash: Sha256ContentHash
    source_intent_hash: Sha256ContentHash
    entry_id: str
    expected_generation: int = Field(ge=0)
    actor: str
    note: str = ""
    activation_generation: int = Field(ge=1)
    recorded_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_identity(self) -> ConfigPublishOperation:
        if not self.operation_id or not self.entry_id or not self.actor.strip():
            raise ValueError("config publish operation identity must be non-empty")
        expected_hash = config_publish_intent_hash(
            source_intent_hash=self.source_intent_hash,
            entry_id=self.entry_id,
            expected_generation=self.expected_generation,
            actor=self.actor,
            note=self.note,
        )
        if self.intent_hash != expected_hash:
            raise ValueError("config publish operation intent hash is inconsistent")
        if self.activation_generation not in {
            self.expected_generation,
            self.expected_generation + 1,
        }:
            raise ValueError(
                "config publish operation generation must be the observed or next "
                "generation"
            )
        return self


class ConfigActivationOperation(_FrozenRegistryModel):
    """Durable result identity for one idempotent activate-entry command."""

    operation_id: str
    intent_hash: Sha256ContentHash
    entry_id: str
    expected_generation: int = Field(ge=0)
    actor: str
    note: str = ""
    activation_generation: int = Field(ge=1)
    recorded_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_identity(self) -> ConfigActivationOperation:
        if not self.operation_id or not self.entry_id or not self.actor.strip():
            raise ValueError("config activation operation identity must be non-empty")
        expected_hash = config_activation_intent_hash(
            entry_id=self.entry_id,
            expected_generation=self.expected_generation,
            actor=self.actor,
            note=self.note,
        )
        if self.intent_hash != expected_hash:
            raise ValueError("config activation operation intent hash is inconsistent")
        if self.activation_generation not in {
            self.expected_generation,
            self.expected_generation + 1,
        }:
            raise ValueError(
                "config activation operation generation must be the observed or next "
                "generation"
            )
        return self


class ConfigRegistryEntryPage(_FrozenRegistryModel):
    """Newest-first keyset page of saved configuration revisions."""

    items: tuple[ConfigRegistryEntry, ...] = ()
    next_cursor: int | None = Field(default=None, ge=1)


class ConfigRegistryActivationPage(_FrozenRegistryModel):
    """Newest-first keyset page of default configuration changes."""

    items: tuple[ConfigRegistryActivationRecord, ...] = ()
    next_cursor: int | None = Field(default=None, ge=1)


class ConfigContextPublishOperation(_FrozenRegistryModel):
    """Publication advances one working point, never the global default."""

    operation_id: _NonEmptyText
    intent_hash: Sha256ContentHash
    base: ConfigContextRef
    entry_id: _NonEmptyText
    actor: _NonEmptyText
    note: str = ""
    recorded_at: datetime = Field(default_factory=utc_now)
