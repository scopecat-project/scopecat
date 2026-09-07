"""Typed wire contracts shared by daemon servers and Python clients.

Execution keeps ``RunProgram`` and its Python closures in the client process.
Wire receipts distinguish accepted live data from durable evidence explicitly.
"""

from __future__ import annotations

from base64 import b64decode, b64encode
from binascii import Error as BinasciiError
from datetime import datetime
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    field_validator,
    model_validator,
)

from scopecat.analysis.dataset_wire import DerivedDatasetPayload
from scopecat.automation.calibrations import (
    CalibrationPublicationPolicyRef,
    CalibrationSuccessRef,
)
from scopecat.config.inventory import InstrumentInventoryChange
from scopecat.config.parameter_updates import ParameterUpdate
from scopecat.config.registry.records import (
    CalibrationCohortMergeContribution,
    CalibrationCohortMergeRegistrySource,
    CandidateAcceptance,
    ConfigActivationOperation,
    ConfigCompositionPolicyRef,
    ConfigPublishOperation,
    ConfigRegistryActivationRecord,
    ConfigRegistryEntry,
    canonical_calibration_merge_contributions,
    config_activation_intent_hash,
    config_publish_intent_hash,
)
from scopecat.control.models import RunPlanSummary
from scopecat.kernel.content_identity import stable_content_hash
from scopecat.kernel.problems import Problem
from scopecat.kernel.run_outcome import RunOutcome
from scopecat.records.analysis import (
    MAX_ANALYSIS_OUTPUTS,
    AnalysisDatasetDerivation,
    AnalysisExecution,
    AnalysisExecutionOutputReference,
    AnalysisFact,
    AnalysisFigureViewSpec,
    AnalysisInterpretationReference,
    AnalysisPublishedOutputReference,
    AnalysisTableViewSpec,
    ProjectAnalysisSubject,
    SampleAnalysisSubject,
    analysis_record_id,
)
from scopecat.records.config import (
    ConfigContentHash,
    ConfigProfileSnapshot,
    InstrumentBindingSpec,
)
from scopecat.records.content import ContentEntry, Sha256ContentHash
from scopecat.records.execution import (
    DomainJobInvocationTransition,
    DomainJobTransitionRecord,
    RecoveryGroupCompletion,
)
from scopecat.records.instrument import InstrumentStateSnapshot
from scopecat.records.measurement_recording import (
    MeasurementDatasetHeader,
    MeasurementDatasetReceipt,
    MeasurementDatasetSeal,
)
from scopecat.records.parameter_change import (
    ParameterChangeProposal,
    ParameterValueDelta,
)
from scopecat.records.run import (
    RunConfigSource,
    RunSnapshot,
)
from scopecat.records.run_request import RunRequest
from scopecat.records.sample import (
    SampleId,
    SampleRecord,
    SampleRevision,
    SampleRevisionDraft,
)
from scopecat.sdk.instruments.contracts import InstrumentDescription
from scopecat.sdk.instruments.execution import RunHardwareBatch

type NonEmptyText = Annotated[str, Field(min_length=1)]

_CONFIG_PUBLISH_SOURCE_INTENT_CODEC = "scopecat.config-publish-source-intent.v1"


class _WireModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        allow_inf_nan=False,
    )


class ConfigDraftCommand(_WireModel):
    """Typed parameter edits against one observed active registry generation."""

    base_entry_id: NonEmptyText
    base_content_hash: ConfigContentHash
    base_generation: int = Field(ge=1)
    candidate_id: NonEmptyText
    updates: tuple[ParameterUpdate, ...] = Field(min_length=1)


class SampleCreateCommand(_WireModel):
    """Create one stable sample and its first immutable revision."""

    operation_id: NonEmptyText
    sample_id: SampleId
    kind: NonEmptyText
    actor: NonEmptyText
    note: str = ""
    content: SampleRevisionDraft

    @property
    def intent_hash(self) -> Sha256ContentHash:
        content = self.model_dump(mode="json", exclude={"operation_id"})
        return f"sha256:{stable_content_hash(content)}"


class SampleReviseCommand(_WireModel):
    """Append and activate one complete immutable sample revision."""

    operation_id: NonEmptyText
    expected_revision: int = Field(ge=1)
    actor: NonEmptyText
    note: str = ""
    content: SampleRevisionDraft

    @property
    def intent_hash(self) -> Sha256ContentHash:
        content = self.model_dump(mode="json", exclude={"operation_id"})
        return f"sha256:{stable_content_hash(content)}"


class SampleMutationReceipt(_WireModel):
    """Stable sample identity paired with the exact activated revision."""

    operation_id: NonEmptyText
    record: SampleRecord
    revision: SampleRevision


class DirectConfigRevisionSource(_WireModel):
    kind: Literal["direct_config_profile"] = "direct_config_profile"
    config: ConfigProfileSnapshot


class ManualConfigDraftRevisionSource(_WireModel):
    kind: Literal["manual_parameter_updates"] = "manual_parameter_updates"
    draft: ConfigDraftCommand
    expected_result_content_hash: ConfigContentHash


class CandidateConfigRevisionSource(_WireModel):
    kind: Literal["candidate_config"] = "candidate_config"
    run_id: NonEmptyText
    proposal_id: NonEmptyText
    acceptance: CandidateAcceptance


class CalibrationCohortMergeRevisionSource(_WireModel):
    """Compose individually verified cohort proposals against one exact base."""

    kind: Literal["calibration_cohort_merge"] = "calibration_cohort_merge"
    cohort_id: NonEmptyText
    spec_hash: Sha256ContentHash
    automatic_publication: CalibrationPublicationPolicyRef | None = None
    composition_policy_ref: ConfigCompositionPolicyRef
    merge_policy: Literal["common_base_cells_v1"] = "common_base_cells_v1"
    base_entry_id: NonEmptyText
    base_content_hash: ConfigContentHash
    base_generation: int = Field(ge=1)
    candidate_id: NonEmptyText
    contributions: tuple[CalibrationCohortMergeContribution, ...] = Field(
        min_length=1,
        max_length=200,
    )
    expected_result_content_hash: ConfigContentHash

    @field_validator("contributions")
    @classmethod
    def canonicalize_contributions(
        cls,
        value: tuple[CalibrationCohortMergeContribution, ...],
    ) -> tuple[CalibrationCohortMergeContribution, ...]:
        return canonical_calibration_merge_contributions(value)

    @model_validator(mode="after")
    def validate_automatic_publication(
        self,
    ) -> CalibrationCohortMergeRevisionSource:
        policy = self.automatic_publication
        if (
            policy is not None
            and policy.composition_policy != self.composition_policy_ref
        ):
            raise ValueError(
                "automatic publication and merge composition policies must match"
            )
        return self


type ConfigPublishSource = Annotated[
    DirectConfigRevisionSource
    | ManualConfigDraftRevisionSource
    | CandidateConfigRevisionSource,
    Field(discriminator="kind"),
]

type ConfigRevisionSource = Annotated[
    DirectConfigRevisionSource
    | ManualConfigDraftRevisionSource
    | CandidateConfigRevisionSource
    | CalibrationCohortMergeRevisionSource,
    Field(discriminator="kind"),
]


class ConfigPublishCommand(_WireModel):
    """Validate, save, and select one revision in a single transaction."""

    operation_id: NonEmptyText
    source: ConfigPublishSource
    actor: NonEmptyText
    expected_generation: int = Field(ge=0)
    entry_id: NonEmptyText
    note: str = ""

    @property
    def source_intent_hash(self) -> Sha256ContentHash:
        identity = {
            "codec": _CONFIG_PUBLISH_SOURCE_INTENT_CODEC,
            "source": self.source.model_dump(mode="json"),
        }
        return f"sha256:{stable_content_hash(identity)}"

    @property
    def intent_hash(self) -> Sha256ContentHash:
        return config_publish_intent_hash(
            source_intent_hash=self.source_intent_hash,
            entry_id=self.entry_id,
            expected_generation=self.expected_generation,
            actor=self.actor,
            note=self.note,
        )


class ConfigPublishReceipt(_WireModel):
    operation: ConfigPublishOperation
    entry: ConfigRegistryEntry
    deltas: tuple[ParameterValueDelta, ...] = ()
    activation: ConfigRegistryActivationRecord

    @model_validator(mode="after")
    def validate_identity(self) -> ConfigPublishReceipt:
        if (
            self.operation.entry_id != self.entry.id
            or self.operation.activation_generation != self.activation.generation
            or self.entry.id != self.activation.entry_id
            or self.entry.content_hash != self.activation.entry_content_hash
            or self.operation.actor != self.entry.actor
            or self.operation.note != self.entry.note
        ):
            raise ValueError(
                "config publish receipt operation, entry, and activation do not match"
            )
        return self


class CalibrationPublicationCommand(_WireModel):
    """Publish one verified calibration cohort under its finalization fence."""

    operation_id: NonEmptyText
    source: CalibrationCohortMergeRevisionSource
    actor: NonEmptyText
    expected_generation: int = Field(ge=0)
    expected_finalization_revision: int | None = Field(default=None, ge=1)
    entry_id: NonEmptyText
    note: str = ""

    @model_validator(mode="after")
    def validate_calibration_contract(self) -> CalibrationPublicationCommand:
        if self.expected_generation != self.source.base_generation:
            raise ValueError(
                "calibration cohort merge expected_generation must equal its "
                "base_generation"
            )
        automatic_merge = self.source.automatic_publication is not None
        if automatic_merge and self.expected_finalization_revision is None:
            raise ValueError(
                "automatic calibration publication requires an expected "
                "finalization revision"
            )
        if not automatic_merge and self.expected_finalization_revision is not None:
            raise ValueError(
                "expected finalization revision is only valid for automatic "
                "calibration publications"
            )
        return self

    @property
    def source_intent_hash(self) -> Sha256ContentHash:
        identity = {
            "codec": _CONFIG_PUBLISH_SOURCE_INTENT_CODEC,
            "source": self.source.model_dump(mode="json"),
        }
        return f"sha256:{stable_content_hash(identity)}"

    @property
    def intent_hash(self) -> Sha256ContentHash:
        # The finalization revision is an execution fence, not publication
        # meaning. A retry from a newer ready occurrence keeps the operation.
        return config_publish_intent_hash(
            source_intent_hash=self.source_intent_hash,
            entry_id=self.entry_id,
            expected_generation=self.expected_generation,
            actor=self.actor,
            note=self.note,
        )


class CalibrationPublicationReceipt(ConfigPublishReceipt):
    """Config publication plus the effective successes anchored by that commit."""

    calibration_successes: tuple[CalibrationSuccessRef, ...]

    @field_validator("calibration_successes")
    @classmethod
    def canonicalize_calibration_successes(
        cls,
        value: tuple[CalibrationSuccessRef, ...],
    ) -> tuple[CalibrationSuccessRef, ...]:
        selected = tuple(
            sorted(
                value,
                key=lambda success: (
                    success.attempt.member_id,
                    success.attempt.procedure_run_id,
                ),
            )
        )
        identities = (
            tuple(success.attempt.member_id for success in selected),
            tuple(success.attempt.procedure_run_id for success in selected),
        )
        if any(len(items) != len(set(items)) for items in identities):
            raise ValueError(
                "calibration publication success identities must be unique"
            )
        return selected

    @model_validator(mode="after")
    def validate_calibration_identity(self) -> CalibrationPublicationReceipt:
        source = self.entry.source
        if not isinstance(source, CalibrationCohortMergeRegistrySource):
            raise ValueError(
                "calibration publication receipt requires a cohort merge entry"
            )

        contributions = {item.member_id: item for item in source.contributions}
        successes = {
            success.attempt.member_id: success for success in self.calibration_successes
        }
        if successes.keys() != contributions.keys():
            raise ValueError(
                "merge config publication must cover every resolved contribution"
            )
        if (
            self.operation.expected_generation != source.base_registry_generation
            or self.activation.previous_entry_id != source.base_entry_id
            or self.activation.previous_entry_content_hash
            != source.base_config_content_hash
        ):
            raise ValueError("merge config publication does not follow its exact base")
        for member_id, contribution in contributions.items():
            success = successes[member_id]
            publication = success.publication
            result_source = (
                None if publication is None else publication.result_config_source
            )
            if (
                publication is None
                or success.attempt.cohort_id != source.cohort_id
                or success.attempt.procedure_run_id
                != contribution.proof.evidence_step.procedure_run_id
                or success.base_config_source.entry_id != source.base_entry_id
                or success.base_config_source.content_hash
                != source.base_config_content_hash
                or success.base_config_source.registry_generation
                != source.base_registry_generation
                or contribution.result_input_fingerprint
                != publication.result_input_fingerprint
                or publication.operation_id != self.operation.operation_id
                or publication.source_intent_hash != self.operation.source_intent_hash
                or result_source is None
                or result_source.entry_id != self.entry.id
                or result_source.config_ref != self.entry.config_ref
                or result_source.content_hash != self.entry.content_hash
                or result_source.registry_generation != self.activation.generation
                or publication.published_at != self.activation.recorded_at
            ):
                raise ValueError(
                    "merge calibration success does not match its config receipt"
                )
        return self


class InstrumentInventoryMigrationCommand(_WireModel):
    """Publish a complete config through an explicitly drained migration."""

    config: ConfigProfileSnapshot
    entry_id: NonEmptyText
    changes: tuple[InstrumentInventoryChange, ...] = Field(min_length=1)
    actor: NonEmptyText
    expected_generation: int = Field(ge=1)
    note: str = ""


class InstrumentInventoryMigrationReceipt(_WireModel):
    entry: ConfigRegistryEntry
    activation: ConfigRegistryActivationRecord
    changes: tuple[InstrumentInventoryChange, ...] = Field(min_length=1)


class ConfigEntryActivationCommand(_WireModel):
    """Select a saved revision with generation compare-and-swap."""

    operation_id: NonEmptyText
    entry_id: NonEmptyText
    actor: NonEmptyText
    expected_generation: int = Field(ge=0)
    note: str = ""

    @property
    def intent_hash(self) -> Sha256ContentHash:
        return config_activation_intent_hash(
            entry_id=self.entry_id,
            expected_generation=self.expected_generation,
            actor=self.actor,
            note=self.note,
        )


class ConfigActivationReceipt(_WireModel):
    operation: ConfigActivationOperation
    activation: ConfigRegistryActivationRecord

    @model_validator(mode="after")
    def validate_identity(self) -> ConfigActivationReceipt:
        if (
            self.operation.activation_generation != self.activation.generation
            or self.operation.entry_id != self.activation.entry_id
        ):
            raise ValueError(
                "config activation receipt operation and activation do not match"
            )
        return self


class _AnalysisInputPayload(_WireModel):
    """Shared JSON-safe identity consumed by a durable analysis record."""

    id: NonEmptyText
    target: NonEmptyText
    content_hash: NonEmptyText
    codec: NonEmptyText
    role: NonEmptyText
    title: str | None = None
    metadata: dict[str, JsonValue] | None = None


class MeasurementAnalysisInputPayload(_AnalysisInputPayload):
    kind: Literal["measurement_dataset"] = "measurement_dataset"
    run_id: NonEmptyText


class PublishedAnalysisInputPayload(_AnalysisInputPayload):
    kind: Literal["analysis_dataset", "analysis_fact", "analysis_artifact"]
    source: AnalysisPublishedOutputReference


class InterpretationAnalysisInputPayload(_AnalysisInputPayload):
    kind: Literal["interpretation"] = "interpretation"
    source: AnalysisInterpretationReference


type AnalysisInputPayload = Annotated[
    MeasurementAnalysisInputPayload
    | PublishedAnalysisInputPayload
    | InterpretationAnalysisInputPayload,
    Field(discriminator="kind"),
]


class AnalysisTableOutputPayload(_WireModel):
    kind: Literal["table"]
    id: NonEmptyText
    title: NonEmptyText
    content: AnalysisTableViewSpec
    metadata: dict[str, JsonValue] = Field(default_factory=dict)


class AnalysisFactOutputPayload(_WireModel):
    kind: Literal["fact"]
    id: NonEmptyText
    title: NonEmptyText
    content: AnalysisFact
    produced_by: AnalysisExecutionOutputReference | None = None
    metadata: dict[str, JsonValue] = Field(default_factory=dict)


class AnalysisDatasetOutputPayload(_WireModel):
    kind: Literal["dataset"]
    id: NonEmptyText
    title: NonEmptyText
    content: DerivedDatasetPayload
    produced_by: AnalysisExecutionOutputReference | None = None
    derived_from: AnalysisDatasetDerivation | None = None
    metadata: dict[str, JsonValue] = Field(default_factory=dict)


class AnalysisFigureOutputPayload(_WireModel):
    kind: Literal["figure"]
    id: NonEmptyText
    title: NonEmptyText
    content: AnalysisFigureViewSpec
    metadata: dict[str, JsonValue] = Field(default_factory=dict)


class AnalysisParameterProposalOutputPayload(_WireModel):
    kind: Literal["parameter_change_proposal"]
    id: NonEmptyText
    title: NonEmptyText
    content: ParameterChangeProposal
    metadata: dict[str, JsonValue] = Field(default_factory=dict)


class AnalysisArtifactOutputPayload(_WireModel):
    kind: Literal["artifact"]
    id: NonEmptyText
    title: NonEmptyText
    content_base64: str
    filename: NonEmptyText
    media_type: NonEmptyText
    produced_by: AnalysisExecutionOutputReference | None = None
    metadata: dict[str, JsonValue] = Field(default_factory=dict)

    @field_validator("content_base64")
    @classmethod
    def validate_content_base64(cls, value: str) -> str:
        try:
            decoded = b64decode(value, validate=True)
        except (BinasciiError, ValueError) as error:
            raise ValueError(
                "analysis artifact content must be valid base64"
            ) from error
        if b64encode(decoded).decode("ascii") != value:
            raise ValueError("analysis artifact content must use canonical base64")
        return value

    def content_bytes(self) -> bytes:
        return b64decode(self.content_base64, validate=True)


type AnalysisOutputPayload = Annotated[
    AnalysisFactOutputPayload
    | AnalysisDatasetOutputPayload
    | AnalysisArtifactOutputPayload
    | AnalysisTableOutputPayload
    | AnalysisFigureOutputPayload
    | AnalysisParameterProposalOutputPayload,
    Field(discriminator="kind"),
]


class AnalysisSaveCommand(_WireModel):
    """Persist JSON analysis results against a daemon-owned run."""

    title: NonEmptyText
    analysis_key: NonEmptyText
    subject: ProjectAnalysisSubject | SampleAnalysisSubject = Field(
        default_factory=ProjectAnalysisSubject,
        discriminator="kind",
    )
    step_id: NonEmptyText | None = None
    inputs: tuple[AnalysisInputPayload, ...] = ()
    executions: tuple[AnalysisExecution, ...] = ()
    outputs: tuple[AnalysisOutputPayload, ...] = Field(
        default=(),
        max_length=MAX_ANALYSIS_OUTPUTS,
    )

    @model_validator(mode="after")
    def validate_outputs(self) -> AnalysisSaveCommand:
        proposals = tuple(
            output.content
            for output in self.outputs
            if isinstance(output, AnalysisParameterProposalOutputPayload)
        )
        expected_analysis_record_id = analysis_record_id(self.analysis_key, 1)
        if any(
            proposal.analysis_record_id != expected_analysis_record_id
            for proposal in proposals
        ):
            raise ValueError("analysis proposal must identify the command analysis")
        ids = tuple(proposal.id for proposal in proposals)
        if len(ids) != len(set(ids)):
            raise ValueError("analysis proposal ids must be unique")
        output_ids = tuple(output.id for output in self.outputs)
        if len(output_ids) != len(set(output_ids)):
            raise ValueError("analysis output ids must be unique")
        execution_ids = tuple(execution.id for execution in self.executions)
        if len(execution_ids) != len(set(execution_ids)):
            raise ValueError("analysis execution ids must be unique")
        materialized_outputs = tuple(
            output
            for output in self.outputs
            if isinstance(
                output,
                AnalysisFactOutputPayload
                | AnalysisDatasetOutputPayload
                | AnalysisArtifactOutputPayload,
            )
        )
        if any(
            isinstance(output, AnalysisDatasetOutputPayload)
            and output.produced_by is not None
            and output.derived_from is not None
            for output in materialized_outputs
        ):
            raise ValueError(
                "analysis dataset output cannot be both produced and derived"
            )
        output_sources = tuple(
            (
                output.derived_from.source
                if isinstance(output, AnalysisDatasetOutputPayload)
                and output.derived_from is not None
                else output.produced_by
            )
            for output in materialized_outputs
        )
        if any(
            source is not None and source.execution_id not in execution_ids
            for source in output_sources
        ):
            raise ValueError("analysis output producer must identify an execution")
        execution_outputs = {
            (execution.id, output.name)
            for execution in self.executions
            for output in execution.outputs
        }
        if any(
            source is not None
            and (
                source.execution_id,
                source.output_name,
            )
            not in execution_outputs
            for source in output_sources
        ):
            raise ValueError(
                "analysis output producer must identify an execution output"
            )
        dataset_ids = {
            output.id
            for output in self.outputs
            if isinstance(output, AnalysisDatasetOutputPayload)
        }
        for output in self.outputs:
            if not isinstance(
                output,
                AnalysisTableOutputPayload | AnalysisFigureOutputPayload,
            ):
                continue
            source = output.content.source
            if source.output_id not in dataset_ids:
                raise ValueError("analysis view source must identify a dataset output")
        return self


class AnalysisSaveReceipt(_WireModel):
    record: ContentEntry
    analysis_key: NonEmptyText
    inputs: tuple[AnalysisInputPayload, ...] = ()
    parameter_proposals: tuple[ParameterChangeProposal, ...] = ()


class RunAttachmentCommand(_WireModel):
    """Ingest client-owned content without exposing a client filesystem path."""

    key: NonEmptyText
    kind: NonEmptyText = "attachment"
    text: str | None = None
    content_base64: str | None = None
    filename: NonEmptyText | None = None
    media_type: NonEmptyText | None = None
    metadata: dict[str, JsonValue] = Field(default_factory=dict)

    @field_validator("content_base64")
    @classmethod
    def validate_content_base64(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _validated_base64(value)

    @model_validator(mode="after")
    def validate_source(self) -> RunAttachmentCommand:
        if (self.text is None) == (self.content_base64 is None):
            raise ValueError("run attachment requires exactly one content source")
        return self


class PayloadObjectReceipt(_WireModel):
    """One immutable command-payload body accepted by the daemon."""

    ref: Sha256ContentHash
    content_hash: Sha256ContentHash
    size_bytes: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_reference(self) -> PayloadObjectReceipt:
        if self.ref != self.content_hash:
            raise ValueError("payload object ref must equal its content hash")
        return self


class RunSubmission(_WireModel):
    """Admit a client-planned run while retaining executable Python in the client.

    Admission authorizes declared resources; it cannot infer work omitted by the
    client planner.
    """

    submission_id: NonEmptyText
    config: ConfigProfileSnapshot
    config_source: RunConfigSource | None = None
    request: RunRequest
    plan: RunPlanSummary

    @property
    def intent_content_hash(self) -> str:
        """Identify submission content independently of its retry key."""

        return stable_content_hash(
            self.model_dump(mode="json", exclude={"submission_id"})
        )


class RunAdmission(_WireModel):
    """Canonical run snapshot returned for an idempotent submission."""

    submission_id: NonEmptyText
    snapshot: RunSnapshot

    @property
    def run_id(self) -> str:
        return self.snapshot.run_id


class ExecutorStartRequest(_WireModel):
    """Start one daemon-owned execution session."""

    executor_id: NonEmptyText
    on_resource_busy: Literal["keep_queued", "fail"] = "keep_queued"


class ExecutorLease(_WireModel):
    """Renewable authority to report effects for one run."""

    lease_id: NonEmptyText
    segment_id: NonEmptyText
    run_id: NonEmptyText
    executor_id: NonEmptyText
    issued_at: datetime
    expires_at: datetime
    heartbeat_interval_seconds: float = Field(gt=0)
    cancellation_requested_at: datetime | None = None

    @model_validator(mode="after")
    def validate_interval(self) -> ExecutorLease:
        issued_at = _aware_datetime(self.issued_at, field_name="issued_at")
        expires_at = _aware_datetime(self.expires_at, field_name="expires_at")
        if expires_at <= issued_at:
            raise ValueError("executor lease must expire after it is issued")
        if self.cancellation_requested_at is not None:
            _aware_datetime(
                self.cancellation_requested_at,
                field_name="cancellation_requested_at",
            )
        return self


class ExecutorHeartbeat(_WireModel):
    """Renew a lease using its unique identity as the fencing token."""

    lease_id: NonEmptyText


class _FencedCommand(_WireModel):
    lease_id: NonEmptyText


class RunCoverageState(_WireModel):
    """Durable contiguous logical-point prefix for one run."""

    run_id: NonEmptyText
    completed_point_count: int = Field(ge=0)


class RunCoverageAdvanceCommand(_FencedCommand):
    """Commit the next contiguous logical-point range."""

    start_index: int = Field(ge=0)
    point_count: int = Field(gt=0)


class RunRecoveryGroupCommitCommand(_FencedCommand):
    """Atomically publish bounded recovery-group completion proofs."""

    groups: tuple[RecoveryGroupCompletion, ...] = Field(
        min_length=1,
        max_length=256,
    )

    @field_validator("groups")
    @classmethod
    def validate_groups(
        cls,
        value: tuple[RecoveryGroupCompletion, ...],
    ) -> tuple[RecoveryGroupCompletion, ...]:
        group_ids = tuple(group.group_id for group in value)
        if len(group_ids) != len(set(group_ids)):
            raise ValueError("recovery group commit ids must be unique")
        return value


class RunRecoveryGroupView(_WireModel):
    """One accepted recovery-group proof in durable commit order."""

    sequence: int = Field(ge=1)
    run_id: NonEmptyText
    segment_id: NonEmptyText
    completion: RecoveryGroupCompletion


class RunRecoveryGroupPage(_WireModel):
    """One bounded page of durable recovery-group proofs."""

    run_id: NonEmptyText
    items: tuple[RunRecoveryGroupView, ...]
    next_cursor: int | None = Field(default=None, ge=1)


class RunRecoveryGroupCommitReceipt(_WireModel):
    """Recovery groups accepted by one idempotent fenced commit."""

    run_id: NonEmptyText
    items: tuple[RunRecoveryGroupView, ...] = Field(min_length=1)


class RunDomainJobTransitionItem(_WireModel):
    """One correlated target-job transition inside a durable append batch."""

    logical_compute_node_id: NonEmptyText
    point_ordinals: tuple[int, ...] = Field(min_length=1)
    transition: DomainJobTransitionRecord

    @field_validator("point_ordinals")
    @classmethod
    def validate_point_ordinals(cls, value: tuple[int, ...]) -> tuple[int, ...]:
        if any(ordinal < 0 for ordinal in value):
            raise ValueError("domain job point ordinals must be non-negative")
        if len(value) != len(set(value)):
            raise ValueError("domain job point ordinals must be unique")
        return value


class RunDomainJobTransitionBatchCommand(_FencedCommand):
    """Commit one bounded ordered transition batch in a single transaction."""

    items: tuple[RunDomainJobTransitionItem, ...] = Field(
        min_length=1,
        max_length=256,
    )


class RunDomainJobTransitionView(_WireModel):
    """One durable transition accepted under a fenced executor lease."""

    sequence: int = Field(ge=1)
    run_id: NonEmptyText
    logical_compute_node_id: NonEmptyText
    point_ordinals: tuple[int, ...] = Field(min_length=1)
    transition: DomainJobTransitionRecord


class RunDomainJobTransitionBatchReceipt(_WireModel):
    """Ordered durable views corresponding to one transition append batch."""

    run_id: NonEmptyText
    items: tuple[RunDomainJobTransitionView, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_run(self) -> RunDomainJobTransitionBatchReceipt:
        if any(item.run_id != self.run_id for item in self.items):
            raise ValueError("domain job transition batch spans multiple runs")
        return self


class RunDomainJobTransitionPage(_WireModel):
    """A bounded ascending slice of durable target-job transitions."""

    run_id: NonEmptyText
    items: tuple[RunDomainJobTransitionView, ...] = ()
    next_cursor: int | None = Field(default=None, ge=1)


class RunDomainJobStateView(_WireModel):
    """Current durable fact for one observed domain-job execution.

    This projection is diagnostic evidence, not authority to replay an
    invocation or resume a provider job.
    """

    run_id: NonEmptyText
    invocation: DomainJobInvocationTransition
    point_ordinals: tuple[int, ...] = Field(min_length=1)
    state: Literal["invocation_unknown", "pending", "terminal"]
    invocation_sequence: int = Field(ge=1)
    latest_sequence: int = Field(ge=1)
    transition_count: int = Field(ge=1)
    latest_transition: DomainJobTransitionRecord

    @model_validator(mode="after")
    def validate_projection(self) -> RunDomainJobStateView:
        execution_id = self.invocation.execution_id
        if execution_id.run_id != self.run_id:
            raise ValueError("domain job state execution belongs to another run")
        if self.latest_transition.execution_key != execution_id.execution_key:
            raise ValueError("domain job state transition belongs to another execution")
        if self.latest_sequence < self.invocation_sequence:
            raise ValueError("domain job latest transition predates its invocation")
        expected_state = {
            "invocation": "invocation_unknown",
            "checkpoint": "pending",
            "terminal": "terminal",
        }[self.latest_transition.kind]
        if self.state != expected_state:
            raise ValueError("domain job state does not match its latest transition")
        return self


class RunDomainJobStatePage(_WireModel):
    """A bounded invocation-ordered slice of observed domain-job states."""

    run_id: NonEmptyText
    items: tuple[RunDomainJobStateView, ...] = ()
    next_cursor: int | None = Field(default=None, ge=1)


class _FencedOperationCommand(_FencedCommand):
    operation_id: NonEmptyText


class RunInstrumentProvisionCommand(_FencedOperationCommand):
    """Acquire daemon-owned instrument epochs from the admitted run snapshot."""


class RunInstrumentProvisionReceipt(_WireModel):
    """State evidence around provisioning daemon-owned run instruments.

    ``observed_state`` is read after exclusive ownership is acquired.
    ``baseline_state`` is the execution baseline after the run policy is applied.
    """

    run_id: NonEmptyText
    operation_id: NonEmptyText
    status: Literal["ready", "rejected"]
    instrument_ids: tuple[NonEmptyText, ...] = ()
    problems: tuple[Problem, ...] = ()
    observed_state: tuple[InstrumentStateSnapshot, ...] = ()
    baseline_state: tuple[InstrumentStateSnapshot, ...] = ()

    @model_validator(mode="after")
    def validate_result(self) -> RunInstrumentProvisionReceipt:
        if len(self.instrument_ids) != len(set(self.instrument_ids)):
            raise ValueError("run instrument provisioning ids must be unique")
        if self.status == "ready":
            if tuple(state.instrument_id for state in self.observed_state) != (
                self.instrument_ids
            ):
                raise ValueError(
                    "ready run observed state must match instrument ids in order"
                )
            if tuple(state.instrument_id for state in self.baseline_state) != (
                self.instrument_ids
            ):
                raise ValueError(
                    "ready run baseline state must match instrument ids in order"
                )
            if self.problems:
                raise ValueError(
                    "ready run instrument provisioning cannot contain problems"
                )
        elif not self.problems:
            raise ValueError("rejected run instrument provisioning requires a problem")
        elif self.observed_state or self.baseline_state:
            raise ValueError(
                "rejected run instrument provisioning cannot expose state evidence"
            )
        return self


class RunHardwareBatchCommand(_FencedCommand):
    """Execute the next batch or retry the immediately preceding sequence."""

    sequence: int = Field(ge=0)
    batch: RunHardwareBatch


class RunHardwareFinishCommand(_FencedOperationCommand):
    failed: bool


class MeasurementHeaderCommand(_FencedCommand):
    header: MeasurementDatasetHeader


class MeasurementIngestReceipt(_WireModel):
    """Acknowledge records received by the daemon and any completed flushes."""

    run_id: NonEmptyText
    received_record_count: int = Field(ge=0)
    durable_record_count: int = Field(ge=0)
    durable_receipts: tuple[MeasurementDatasetReceipt, ...] = ()


class MeasurementFlushCommand(_FencedCommand):
    """Force all daemon-buffered measurement records to durable storage."""


class MeasurementFlushReceipt(_WireModel):
    """Report the durable prefix after an explicit buffer flush."""

    run_id: NonEmptyText
    durable_record_count: int = Field(ge=0)
    durable_receipts: tuple[MeasurementDatasetReceipt, ...] = ()


class MeasurementSealCommand(_FencedCommand):
    seal: MeasurementDatasetSeal


class TerminalModelWrite(_WireModel):
    ref: NonEmptyText
    value: dict[str, JsonValue]


class TerminalRunCommitCommand(_FencedCommand):
    """Lossless JSON projection of one interpreter terminal delta."""

    outcome: RunOutcome
    contents: tuple[ContentEntry, ...] = ()
    models: tuple[TerminalModelWrite, ...] = ()


class AttentionResolutionCommand(_WireModel):
    """Choose the run disposition after external state was reconciled."""

    disposition: Literal["close", "continue"]
    run_contract_fingerprint: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )

    @classmethod
    def close_run(cls) -> AttentionResolutionCommand:
        return cls(disposition="close")

    @classmethod
    def continue_run(
        cls,
        *,
        run_contract_fingerprint: str,
    ) -> AttentionResolutionCommand:
        return cls(
            disposition="continue",
            run_contract_fingerprint=run_contract_fingerprint,
        )

    @model_validator(mode="after")
    def validate_continuation_contract(self) -> AttentionResolutionCommand:
        if (self.disposition == "continue") != (
            self.run_contract_fingerprint is not None
        ):
            raise ValueError(
                "only continuation attention resolution requires a run contract"
            )
        return self


class AttentionResolutionReceipt(_WireModel):
    run_id: NonEmptyText
    disposition: Literal["close", "continue"]
    state: Literal["queued", "closed"]
    released_resource_count: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_disposition(self) -> AttentionResolutionReceipt:
        expected_state = "queued" if self.disposition == "continue" else "closed"
        if self.state != expected_state:
            raise ValueError("attention disposition does not match scheduler state")
        return self


class RunCancellationState(_WireModel):
    """Lightweight read-only executor cancellation signal."""

    run_id: NonEmptyText
    requested: bool


class RunCancellationReceipt(_WireModel):
    """Durable result of an idempotent operator cancellation request."""

    run_id: NonEmptyText
    status: Literal["cancel_requested", "cancelled", "not_accepted"]
    cancellation_requested_at: datetime | None = None
    outcome: RunOutcome | None = None

    @model_validator(mode="after")
    def validate_result(self) -> RunCancellationReceipt:
        if self.cancellation_requested_at is not None:
            _aware_datetime(
                self.cancellation_requested_at,
                field_name="cancellation_requested_at",
            )
        if self.status == "cancel_requested":
            if self.cancellation_requested_at is None or self.outcome is not None:
                raise ValueError(
                    "pending cancellation requires a request time and no outcome"
                )
        elif self.status == "cancelled":
            if (
                self.cancellation_requested_at is None
                or self.outcome is None
                or self.outcome.result != "cancelled"
            ):
                raise ValueError(
                    "completed cancellation requires its request and cancelled outcome"
                )
        elif self.outcome is None:
            raise ValueError(
                "non-accepted cancellation requires the existing terminal outcome"
            )
        return self


class InstrumentContractCatalogRequest(_WireModel):
    """Resolve the exact contracts advertised for one config snapshot."""

    config: ConfigProfileSnapshot


class InstrumentDriverProbeCommand(_WireModel):
    """Open, identify, and close one candidate instrument binding."""

    binding: InstrumentBindingSpec


class InstrumentDriverProbeReceipt(_WireModel):
    status: Literal["connected", "rejected"]
    description: InstrumentDescription | None = None
    problems: tuple[Problem, ...] = ()

    @model_validator(mode="after")
    def validate_outcome(self) -> InstrumentDriverProbeReceipt:
        if self.status == "connected":
            valid = self.description is not None and not self.problems
        else:
            valid = self.description is None and bool(self.problems)
        if not valid:
            raise ValueError("driver probe status and outcome disagree")
        return self


class InstrumentReleaseCommand(_WireModel):
    """Disconnect idle configured instruments; active owners block release."""

    instrument_ids: tuple[NonEmptyText, ...] = Field(min_length=1)

    @field_validator("instrument_ids")
    @classmethod
    def unique_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("instrument ids must be unique")
        return value


class InstrumentReleaseReceipt(_WireModel):
    instrument_ids: tuple[NonEmptyText, ...]


class InstrumentSessionOpenCommand(_WireModel):
    """Acquire configured instruments plus optional session-only bindings."""

    operation_id: NonEmptyText
    actor: NonEmptyText
    instrument_ids: tuple[NonEmptyText, ...] = Field(min_length=1)
    temporary_bindings: tuple[InstrumentBindingSpec, ...] = ()

    @field_validator("instrument_ids")
    @classmethod
    def validate_instrument_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("instrument session ids must be unique")
        return value

    @model_validator(mode="after")
    def validate_temporary_bindings(self) -> InstrumentSessionOpenCommand:
        binding_ids = tuple(binding.id for binding in self.temporary_bindings)
        if len(binding_ids) != len(set(binding_ids)):
            raise ValueError("temporary instrument binding ids must be unique")
        unknown = tuple(
            instrument_id
            for instrument_id in binding_ids
            if instrument_id not in self.instrument_ids
        )
        if unknown:
            raise ValueError(
                "temporary bindings must belong to the opened session: "
                + ", ".join(unknown)
            )
        return self


class InstrumentSessionLeaseReceipt(_WireModel):
    session_id: NonEmptyText
    renewed_at: datetime
    expires_at: datetime

    @model_validator(mode="after")
    def validate_lease(self) -> InstrumentSessionLeaseReceipt:
        _validate_instrument_session_lease(self.renewed_at, self.expires_at)
        return self


class InstrumentSessionOpenReceipt(_WireModel):
    """Daemon-owned direct-control session opened against one config revision."""

    session_id: NonEmptyText
    actor: NonEmptyText
    config_entry_id: NonEmptyText
    config_content_hash: ConfigContentHash
    instrument_ids: tuple[NonEmptyText, ...] = Field(min_length=1)
    configured_default_instrument_ids: tuple[NonEmptyText, ...]
    descriptions: tuple[InstrumentDescription, ...]
    observed_state: tuple[InstrumentStateSnapshot, ...]
    opened_at: datetime
    renewed_at: datetime
    expires_at: datetime

    @model_validator(mode="after")
    def validate_contents(self) -> InstrumentSessionOpenReceipt:
        _aware_datetime(self.opened_at, field_name="opened_at")
        _validate_instrument_session_lease(self.renewed_at, self.expires_at)
        described_ids = tuple(
            description.instrument_id for description in self.descriptions
        )
        if described_ids != self.instrument_ids:
            raise ValueError(
                "instrument session descriptions must match instrument_ids in order"
            )
        observed_ids = tuple(state.instrument_id for state in self.observed_state)
        if observed_ids != self.instrument_ids:
            raise ValueError(
                "instrument session observed state must match instrument_ids in order"
            )
        configured = set(self.configured_default_instrument_ids)
        if self.configured_default_instrument_ids != tuple(
            instrument_id
            for instrument_id in self.instrument_ids
            if instrument_id in configured
        ):
            raise ValueError(
                "configured default ids must be an ordered subset of instrument_ids"
            )
        return self


class InstrumentConfiguredDefaultsApplyCommand(_WireModel):
    """Reconcile one session instrument with its pinned configured defaults."""

    operation_id: NonEmptyText


class InstrumentSessionEndReceipt(_WireModel):
    session_id: NonEmptyText
    status: Literal["closed", "aborted"]


def _aware_datetime(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must include a UTC offset")
    return value


def _validate_instrument_session_lease(
    renewed_at: datetime,
    expires_at: datetime,
) -> None:
    renewed_at = _aware_datetime(renewed_at, field_name="renewed_at")
    expires_at = _aware_datetime(expires_at, field_name="expires_at")
    if expires_at <= renewed_at:
        raise ValueError("expires_at must follow renewed_at")


def _validated_base64(value: str) -> str:
    try:
        b64decode(value, validate=True)
    except (BinasciiError, ValueError) as error:
        raise ValueError("content_base64 must be valid base64") from error
    return value


__all__ = [
    "AnalysisArtifactOutputPayload",
    "AnalysisDatasetOutputPayload",
    "AnalysisFactOutputPayload",
    "AnalysisFigureOutputPayload",
    "AnalysisInputPayload",
    "AnalysisOutputPayload",
    "AnalysisParameterProposalOutputPayload",
    "AnalysisSaveCommand",
    "AnalysisSaveReceipt",
    "AnalysisTableOutputPayload",
    "AttentionResolutionCommand",
    "AttentionResolutionReceipt",
    "CalibrationCohortMergeRevisionSource",
    "CalibrationPublicationCommand",
    "CalibrationPublicationReceipt",
    "CandidateConfigRevisionSource",
    "ConfigActivationReceipt",
    "ConfigDraftCommand",
    "ConfigEntryActivationCommand",
    "ConfigPublishCommand",
    "ConfigPublishReceipt",
    "ConfigPublishSource",
    "ConfigRevisionSource",
    "DirectConfigRevisionSource",
    "ExecutorHeartbeat",
    "ExecutorLease",
    "ExecutorStartRequest",
    "InstrumentConfiguredDefaultsApplyCommand",
    "InstrumentContractCatalogRequest",
    "InstrumentDriverProbeCommand",
    "InstrumentDriverProbeReceipt",
    "InstrumentInventoryMigrationCommand",
    "InstrumentInventoryMigrationReceipt",
    "InstrumentReleaseCommand",
    "InstrumentReleaseReceipt",
    "InstrumentSessionEndReceipt",
    "InstrumentSessionLeaseReceipt",
    "InstrumentSessionOpenCommand",
    "InstrumentSessionOpenReceipt",
    "ManualConfigDraftRevisionSource",
    "MeasurementAnalysisInputPayload",
    "MeasurementFlushCommand",
    "MeasurementFlushReceipt",
    "MeasurementHeaderCommand",
    "MeasurementIngestReceipt",
    "MeasurementSealCommand",
    "PayloadObjectReceipt",
    "PublishedAnalysisInputPayload",
    "RunAdmission",
    "RunAttachmentCommand",
    "RunCancellationReceipt",
    "RunCancellationState",
    "RunCoverageAdvanceCommand",
    "RunCoverageState",
    "RunDomainJobStatePage",
    "RunDomainJobStateView",
    "RunDomainJobTransitionBatchCommand",
    "RunDomainJobTransitionBatchReceipt",
    "RunDomainJobTransitionItem",
    "RunDomainJobTransitionPage",
    "RunDomainJobTransitionView",
    "RunInstrumentProvisionCommand",
    "RunInstrumentProvisionReceipt",
    "RunRecoveryGroupCommitCommand",
    "RunRecoveryGroupCommitReceipt",
    "RunRecoveryGroupPage",
    "RunRecoveryGroupView",
    "RunSubmission",
    "SampleCreateCommand",
    "SampleMutationReceipt",
    "SampleReviseCommand",
    "TerminalModelWrite",
    "TerminalRunCommitCommand",
]
