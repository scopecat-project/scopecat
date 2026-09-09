"""Durable records owned by the configuration registry."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from scopecat.kernel.content_identity import stable_content_hash
from scopecat.kernel.run_outcome import utc_now
from scopecat.records.analysis import ProjectAnalysisDecisionReference
from scopecat.records.config import ConfigContentHash
from scopecat.records.content import Sha256ContentHash

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


class ConfigCompositionPolicyRef(_FrozenRegistryModel):
    """Exact project-owned policy that selected one config composition."""

    id: _NonEmptyText
    version: _NonEmptyText
    fingerprint: Sha256ContentHash

    @field_validator("id", "version")
    @classmethod
    def validate_identity(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("config composition policy identity must be non-empty")
        return value


class ConfigCompositionEvidenceStepRef(_FrozenRegistryModel):
    """Self-contained exact checkpoint in one contribution procedure."""

    procedure_run_id: _NonEmptyText
    step_key: _NonEmptyText
    attempt: int = Field(ge=1)

    @field_validator("procedure_run_id", "step_key")
    @classmethod
    def validate_identity(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("config composition evidence identity must be non-empty")
        return value


class VerifiedParameterProposalProofV1(_FrozenRegistryModel):
    """One exact project decision accepting a parameter-proposal candidate."""

    kind: Literal["verified_parameter_proposal_v1"] = "verified_parameter_proposal_v1"
    evidence_step: ConfigCompositionEvidenceStepRef
    decision: ProjectAnalysisDecisionReference


class ResolvedVerifiedParameterProposalProofV1(_FrozenRegistryModel):
    """Server-resolved exact lineage behind one accepted proposal proof."""

    kind: Literal["verified_parameter_proposal_v1"] = "verified_parameter_proposal_v1"
    evidence_step: ConfigCompositionEvidenceStepRef
    baseline_run_id: _NonEmptyText
    fit_analysis_record_id: _NonEmptyText
    proposal_id: _NonEmptyText
    candidate_run_id: _NonEmptyText
    decision: ProjectAnalysisDecisionReference

    @field_validator(
        "baseline_run_id",
        "fit_analysis_record_id",
        "proposal_id",
        "candidate_run_id",
    )
    @classmethod
    def validate_identity(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("resolved parameter proposal proof identity is empty")
        return value


class CalibrationCohortMergeContribution(_FrozenRegistryModel):
    """Exact proof for one individually verified calibration contribution."""

    member_id: _NonEmptyText
    proof: VerifiedParameterProposalProofV1
    result_input_fingerprint: Sha256ContentHash

    @field_validator("member_id")
    @classmethod
    def validate_identity(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("calibration merge contribution identity is empty")
        return value


class ResolvedCalibrationCohortMergeContribution(_FrozenRegistryModel):
    """Server-resolved exact outputs behind one wire contribution."""

    member_id: _NonEmptyText
    proof: ResolvedVerifiedParameterProposalProofV1
    result_input_fingerprint: Sha256ContentHash

    @field_validator("member_id")
    @classmethod
    def validate_identity(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("resolved calibration contribution identity is empty")
        return value


def canonical_calibration_merge_contributions(
    contributions: tuple[CalibrationCohortMergeContribution, ...],
) -> tuple[CalibrationCohortMergeContribution, ...]:
    """Validate and sort exact contributions independently of caller order."""

    selected = tuple(
        sorted(
            contributions,
            key=lambda item: (
                item.member_id,
                item.proof.evidence_step.procedure_run_id,
                item.proof.decision.analysis_record_id,
            ),
        )
    )
    identities = (
        ("member", tuple(item.member_id for item in selected)),
        (
            "procedure run",
            tuple(item.proof.evidence_step.procedure_run_id for item in selected),
        ),
        (
            "decision",
            tuple(item.proof.decision.analysis_record_id for item in selected),
        ),
    )
    for label, values in identities:
        if len(values) != len(set(values)):
            raise ValueError(f"calibration merge {label} identities must be unique")
    return selected


def canonical_resolved_calibration_merge_contributions(
    contributions: tuple[ResolvedCalibrationCohortMergeContribution, ...],
) -> tuple[ResolvedCalibrationCohortMergeContribution, ...]:
    """Validate and sort resolved contributions by their member identity."""

    selected = tuple(
        sorted(
            contributions,
            key=lambda item: (
                item.member_id,
                item.proof.evidence_step.procedure_run_id,
                item.proof.proposal_id,
            ),
        )
    )
    identity_groups: tuple[tuple[str, tuple[object, ...]], ...] = (
        ("member", tuple(item.member_id for item in selected)),
        (
            "procedure run",
            tuple(item.proof.evidence_step.procedure_run_id for item in selected),
        ),
        ("baseline run", tuple(item.proof.baseline_run_id for item in selected)),
        ("candidate run", tuple(item.proof.candidate_run_id for item in selected)),
        (
            "proposal",
            tuple(
                (item.proof.baseline_run_id, item.proof.proposal_id)
                for item in selected
            ),
        ),
    )
    for label, values in identity_groups:
        if len(values) != len(set(values)):
            raise ValueError(
                f"resolved calibration merge {label} identities must be unique"
            )
    return selected


class CalibrationCohortMergeRegistrySource(_FrozenRegistryModel):
    """Durable provenance for an individually verified cohort composition."""

    kind: Literal["calibration_cohort_merge"] = "calibration_cohort_merge"
    cohort_id: _NonEmptyText
    spec_hash: Sha256ContentHash
    automatic_publication_policy_id: _NonEmptyText | None = None
    automatic_publication_policy_version: _NonEmptyText | None = None
    automatic_publication_policy_fingerprint: Sha256ContentHash | None = None
    composition_policy_ref: ConfigCompositionPolicyRef
    merge_policy: Literal["common_base_cells_v1"] = "common_base_cells_v1"
    base_entry_id: _NonEmptyText
    base_config_content_hash: ConfigContentHash
    base_registry_generation: int = Field(ge=1)
    candidate_id: _NonEmptyText
    contributions: tuple[ResolvedCalibrationCohortMergeContribution, ...] = Field(
        min_length=1,
        max_length=_MAX_CALIBRATION_MERGE_CONTRIBUTIONS,
    )

    @field_validator("cohort_id", "base_entry_id", "candidate_id")
    @classmethod
    def validate_identity(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("calibration merge registry identity must be non-empty")
        return value

    @field_validator("contributions")
    @classmethod
    def canonicalize_contributions(
        cls,
        value: tuple[ResolvedCalibrationCohortMergeContribution, ...],
    ) -> tuple[ResolvedCalibrationCohortMergeContribution, ...]:
        return canonical_resolved_calibration_merge_contributions(value)

    @model_validator(mode="after")
    def validate_automatic_publication(
        self,
    ) -> CalibrationCohortMergeRegistrySource:
        identity = (
            self.automatic_publication_policy_id,
            self.automatic_publication_policy_version,
            self.automatic_publication_policy_fingerprint,
        )
        if any(value is None for value in identity) and any(
            value is not None for value in identity
        ):
            raise ValueError("calibration publication policy identity must be complete")
        return self


ConfigRegistryEntrySource = Annotated[
    DirectConfigRegistrySource
    | ManualConfigDraftRegistrySource
    | CandidateConfigRegistrySource
    | CalibrationCohortMergeRegistrySource,
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

    @model_validator(mode="after")
    def validate_identity(self) -> ConfigRegistryEntry:
        if not self.id or not self.config_ref or not self.actor.strip():
            msg = "config registry entry identity fields must be non-empty"
            raise ValueError(msg)
        return self


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
