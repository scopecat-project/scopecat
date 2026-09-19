"""Immutable calibration-cohort planning and evidence contracts.

Calibration dependencies deliberately point only at exact prior successes.  A
cohort is therefore a bounded atomic admission batch, not a general-purpose
workflow graph: members of the same cohort cannot depend on one another.
"""

from __future__ import annotations

from collections.abc import Hashable, Iterable
from datetime import UTC, datetime
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationInfo,
    field_validator,
    model_validator,
)

from scopecat.automation.models import (
    ProcedureClosure,
    ProcedureDefinitionRef,
    ProcedureIntent,
    ProcedureRunState,
)
from scopecat.config.registry.records import ConfigCompositionPolicyRef
from scopecat.kernel.content_identity import stable_content_hash
from scopecat.records.calibration_scope import (
    CalibrationConfigSourceRef,
    CalibrationOwner,
    CatalogCalibrationOwner,
    WorkingPointCalibrationScope,
)
from scopecat.records.content import Sha256ContentHash
from scopecat.records.experimental_batch import ExperimentalBatchId, absent_batch
from scopecat.records.sample import SampleId, SampleSelector

type _NonEmptyText = Annotated[str, Field(min_length=1)]

MAX_CALIBRATION_COHORT_MEMBERS = 200
MAX_CALIBRATION_STATUS_KEYS = 200

type CalibrationSuccessPolicy = Literal["procedure_success", "published_result"]
type CalibrationCohortFinalizationState = Literal[
    "waiting",
    "ready",
    "attention_required",
    "failed",
    "superseded",
    "published",
]

_CALIBRATION_KEY_CODEC = "scopecat.calibration-key.v2"
_CALIBRATION_FRESHNESS_CODEC = "scopecat.calibration-freshness.v2"
_CALIBRATION_COHORT_SPEC_CODEC = "scopecat.calibration-cohort-spec.v5"
_CALIBRATION_COHORT_MEMBER_REQUEST_CODEC = (
    "scopecat.calibration-cohort-member-request.v1"
)


class _CalibrationModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        allow_inf_nan=False,
    )


class CalibrationDefinitionRef(_CalibrationModel):
    """Version-pinned calibration meaning, independent of its procedure."""

    id: _NonEmptyText
    version: _NonEmptyText
    fingerprint: Sha256ContentHash
    success_policy: CalibrationSuccessPolicy = "procedure_success"

    @field_validator("id", "version")
    @classmethod
    def validate_identity(cls, value: str) -> str:
        return _non_blank(value, field_name="calibration definition identity")


class CalibrationPublicationPolicyRef(_CalibrationModel):
    """Exact automatic publication policy pinned into one cohort decision."""

    id: _NonEmptyText
    version: _NonEmptyText
    fingerprint: Sha256ContentHash
    calibration: CalibrationDefinitionRef
    composition_policy: ConfigCompositionPolicyRef

    @field_validator("id", "version")
    @classmethod
    def validate_identity(cls, value: str) -> str:
        return _non_blank(value, field_name="calibration publication policy identity")

    @model_validator(mode="after")
    def validate_calibration_policy(self) -> CalibrationPublicationPolicyRef:
        if self.calibration.success_policy != "published_result":
            raise ValueError(
                "automatic publication policy requires published-result calibration"
            )
        return self


class CalibrationTargetRef(_CalibrationModel):
    """Stable logical target addressed by one calibration definition."""

    kind: _NonEmptyText
    id: _NonEmptyText
    owner: CalibrationOwner = Field(default_factory=CatalogCalibrationOwner)
    sample_id: SampleId | None = None
    context_id: _NonEmptyText | None = None
    batch_id: ExperimentalBatchId | None = Field(default=None, exclude_if=absent_batch)

    @field_validator("kind", "id")
    @classmethod
    def validate_identity(cls, value: str) -> str:
        return _non_blank(value, field_name="calibration target identity")

    @model_validator(mode="after")
    def validate_sample_scope(self) -> CalibrationTargetRef:
        if (
            self.context_id is not None or self.batch_id is not None
        ) and self.sample_id is None:
            raise ValueError("calibration target context requires a sample")
        return self


def scoped_calibration_target(
    target: CalibrationTargetRef,
    source: CalibrationConfigSourceRef,
) -> CalibrationTargetRef:
    """Resolve a definition's logical descriptor within one planning scope."""
    scope = source.scope
    if (
        not isinstance(target.owner, CatalogCalibrationOwner)
        and target.owner != scope.owner
    ):
        raise ValueError("calibration target belongs to another workspace")
    if isinstance(scope, WorkingPointCalibrationScope):
        sample = scope.sample
        expected = (sample.sample_id, sample.context_id, sample.batch_id)
        actual = (target.sample_id, target.context_id, target.batch_id)
        if any(
            given is not None and given != wanted
            for given, wanted in zip(actual, expected, strict=True)
        ):
            raise ValueError("calibration target differs from the working-point scope")
        return target.model_copy(
            update={
                "owner": scope.owner,
                "sample_id": sample.sample_id,
                "context_id": sample.context_id,
                "batch_id": sample.batch_id,
            }
        )
    if target.sample_id is not None:
        raise ValueError("sample calibration requires an explicit working point")
    return target.model_copy(update={"owner": scope.owner})


def calibration_target_sample_selectors(
    target: CalibrationTargetRef,
) -> tuple[SampleSelector, ...]:
    """Return the child-run sample scope implied by a calibration target."""

    if target.sample_id is None:
        return ()
    return (
        SampleSelector(
            sample_id=target.sample_id,
            context_id=target.context_id,
            batch_id=target.batch_id,
        ),
    )


def calibration_key(
    definition_id: str,
    target: CalibrationTargetRef,
) -> str:
    """Identify one logical calibration across definition/procedure revisions."""

    selected_definition_id = _non_blank(
        definition_id,
        field_name="calibration definition id",
    )
    digest = stable_content_hash(
        {
            "codec": _CALIBRATION_KEY_CODEC,
            "definition_id": selected_definition_id,
            "target": target.model_dump(mode="json"),
        }
    )
    return f"calibration:{digest}"


class CalibrationDependencyEvidence(_CalibrationModel):
    """Flat identity of one exact successful dependency attempt.

    Keeping this projection flat prevents a success from recursively embedding
    the complete dependency history of every earlier calibration.
    """

    calibration_key: _NonEmptyText
    cohort_id: _NonEmptyText
    member_id: _NonEmptyText
    procedure_run_id: _NonEmptyText
    freshness_fingerprint: Sha256ContentHash
    publication_operation_id: _NonEmptyText | None = None
    succeeded_at: datetime

    @field_validator(
        "calibration_key",
        "cohort_id",
        "member_id",
        "procedure_run_id",
    )
    @classmethod
    def validate_identity(cls, value: str) -> str:
        return _non_blank(value, field_name="calibration dependency identity")

    @field_validator("succeeded_at")
    @classmethod
    def canonicalize_succeeded_at(cls, value: datetime) -> datetime:
        return _canonical_utc(value, field_name="succeeded_at")

    @classmethod
    def from_success(
        cls,
        success: CalibrationSuccessRef,
    ) -> CalibrationDependencyEvidence:
        if not success.is_effective:
            raise ValueError(
                "pending calibration publication cannot be dependency evidence"
            )
        return cls(
            calibration_key=success.attempt.calibration_key,
            cohort_id=success.attempt.cohort_id,
            member_id=success.attempt.member_id,
            procedure_run_id=success.attempt.procedure_run_id,
            freshness_fingerprint=success.effective_freshness_fingerprint,
            publication_operation_id=(
                None
                if success.publication is None
                else success.publication.operation_id
            ),
            succeeded_at=success.succeeded_at,
        )


class CalibrationAttemptRef(_CalibrationModel):
    """Exact durable ProcedureRun admitted for one calibration member."""

    calibration_key: _NonEmptyText
    cohort_id: _NonEmptyText
    member_id: _NonEmptyText
    procedure_run_id: _NonEmptyText
    definition: CalibrationDefinitionRef
    target: CalibrationTargetRef
    procedure: ProcedureDefinitionRef
    input_fingerprint: Sha256ContentHash
    dependencies: tuple[CalibrationDependencyEvidence, ...] = Field(
        default=(),
        max_length=MAX_CALIBRATION_STATUS_KEYS,
    )
    freshness_fingerprint: Sha256ContentHash
    admitted_at: datetime

    @field_validator(
        "calibration_key",
        "cohort_id",
        "member_id",
        "procedure_run_id",
    )
    @classmethod
    def validate_identity(cls, value: str) -> str:
        return _non_blank(value, field_name="calibration attempt identity")

    @field_validator("admitted_at")
    @classmethod
    def canonicalize_admitted_at(cls, value: datetime) -> datetime:
        return _canonical_utc(value, field_name="admitted_at")

    @field_validator("dependencies")
    @classmethod
    def canonicalize_dependencies(
        cls,
        value: tuple[CalibrationDependencyEvidence, ...],
    ) -> tuple[CalibrationDependencyEvidence, ...]:
        return _canonical_dependencies(
            value,
            label="calibration attempt dependency key",
        )

    @model_validator(mode="after")
    def validate_calibration_key(self) -> CalibrationAttemptRef:
        expected = calibration_key(self.definition.id, self.target)
        if self.calibration_key != expected:
            raise ValueError(
                "calibration attempt key must identify its definition and target"
            )
        if any(
            dependency.calibration_key == self.calibration_key
            for dependency in self.dependencies
        ):
            raise ValueError("calibration attempt cannot depend on itself")
        expected_freshness = calibration_freshness_fingerprint(
            definition=self.definition,
            target=self.target,
            procedure=self.procedure,
            input_fingerprint=self.input_fingerprint,
            dependencies=self.dependencies,
        )
        if self.freshness_fingerprint != expected_freshness:
            raise ValueError(
                "calibration attempt freshness must cover its exact inputs"
            )
        return self


class CalibrationSuccessPublication(_CalibrationModel):
    """One successful calibration result atomically published as configuration."""

    operation_id: _NonEmptyText
    source_intent_hash: Sha256ContentHash
    result_input_fingerprint: Sha256ContentHash
    result_freshness_fingerprint: Sha256ContentHash
    result_config_source: CalibrationConfigSourceRef
    published_at: datetime

    @field_validator("operation_id")
    @classmethod
    def validate_identity(cls, value: str) -> str:
        return _non_blank(value, field_name="calibration publication operation id")

    @field_validator("published_at")
    @classmethod
    def canonicalize_published_at(cls, value: datetime) -> datetime:
        return _canonical_utc(value, field_name="published_at")


class CalibrationSuccessRef(_CalibrationModel):
    """Exact successful attempt and any effective result publication."""

    attempt: CalibrationAttemptRef
    base_config_source: CalibrationConfigSourceRef
    succeeded_at: datetime
    publication: CalibrationSuccessPublication | None = None

    @field_validator("succeeded_at")
    @classmethod
    def canonicalize_succeeded_at(cls, value: datetime) -> datetime:
        return _canonical_utc(value, field_name="succeeded_at")

    @model_validator(mode="after")
    def validate_success(self) -> CalibrationSuccessRef:
        if self.succeeded_at < self.attempt.admitted_at:
            raise ValueError("calibration success cannot precede attempt admission")
        publication = self.publication
        if self.attempt.definition.success_policy == "procedure_success":
            if publication is not None:
                raise ValueError(
                    "procedure-success calibration cannot have a result publication"
                )
            return self
        if publication is None:
            return self
        if publication.published_at < self.succeeded_at:
            raise ValueError("calibration publication cannot precede its success")
        if (
            not isinstance(self.base_config_source.scope, WorkingPointCalibrationScope)
            or publication.result_config_source.scope != self.base_config_source.scope
            or publication.result_config_source.context_ref
            == self.base_config_source.context_ref
        ):
            raise ValueError(
                "calibration publication must advance the same working point"
            )
        expected_freshness = calibration_freshness_fingerprint(
            definition=self.attempt.definition,
            target=self.attempt.target,
            procedure=self.attempt.procedure,
            input_fingerprint=publication.result_input_fingerprint,
            dependencies=self.attempt.dependencies,
        )
        if publication.result_freshness_fingerprint != expected_freshness:
            raise ValueError(
                "calibration publication freshness must cover its result inputs"
            )
        return self

    @property
    def is_effective(self) -> bool:
        return (
            self.attempt.definition.success_policy == "procedure_success"
            or self.publication is not None
        )

    @property
    def effective_input_fingerprint(self) -> Sha256ContentHash:
        if self.attempt.definition.success_policy == "procedure_success":
            return self.attempt.input_fingerprint
        publication = self.publication
        if publication is None:
            raise ValueError("pending calibration publication has no effective inputs")
        return publication.result_input_fingerprint

    @property
    def effective_freshness_fingerprint(self) -> Sha256ContentHash:
        if self.attempt.definition.success_policy == "procedure_success":
            return self.attempt.freshness_fingerprint
        publication = self.publication
        if publication is None:
            raise ValueError(
                "pending calibration publication has no effective freshness"
            )
        return publication.result_freshness_fingerprint

    @property
    def effective_config_source(self) -> CalibrationConfigSourceRef:
        if self.attempt.definition.success_policy == "procedure_success":
            return self.base_config_source
        publication = self.publication
        if publication is None:
            raise ValueError(
                "pending calibration publication has no effective config source"
            )
        return publication.result_config_source

    @property
    def dependency_evidence(self) -> CalibrationDependencyEvidence:
        return CalibrationDependencyEvidence.from_success(self)


class CalibrationAttemptStatus(_CalibrationModel):
    """Observed ProcedureRun state for one exact calibration attempt."""

    attempt: CalibrationAttemptRef
    procedure_state: ProcedureRunState
    procedure_revision: int = Field(ge=1)
    updated_at: datetime
    closure: ProcedureClosure | None = None

    @field_validator("updated_at")
    @classmethod
    def canonicalize_updated_at(cls, value: datetime) -> datetime:
        return _canonical_utc(value, field_name="updated_at")

    @field_validator("closure")
    @classmethod
    def canonicalize_closure(
        cls,
        value: ProcedureClosure | None,
    ) -> ProcedureClosure | None:
        if value is None:
            return None
        return value.model_copy(
            update={
                "closed_at": _canonical_utc(
                    value.closed_at,
                    field_name="closure.closed_at",
                )
            }
        )

    @model_validator(mode="after")
    def validate_run_state(self) -> CalibrationAttemptStatus:
        if self.updated_at < self.attempt.admitted_at:
            raise ValueError("calibration attempt update cannot precede admission")
        if self.procedure_state == "closed":
            if self.closure is None:
                raise ValueError("closed calibration attempt requires a closure")
            if self.closure.closed_at > self.updated_at:
                raise ValueError("calibration closure cannot follow its update time")
        elif self.closure is not None:
            raise ValueError("only a closed calibration attempt can have a closure")
        return self


class CalibrationStatus(_CalibrationModel):
    """Latest attempt and latest success for one logical calibration key."""

    calibration_key: _NonEmptyText
    latest_attempt: CalibrationAttemptStatus | None = None
    latest_success: CalibrationSuccessRef | None = None

    @field_validator("calibration_key")
    @classmethod
    def validate_identity(cls, value: str) -> str:
        return _non_blank(value, field_name="calibration status key")

    @model_validator(mode="after")
    def validate_evidence(self) -> CalibrationStatus:
        if self.latest_attempt is None and self.latest_success is not None:
            raise ValueError("calibration success requires a latest attempt")
        if (
            self.latest_attempt is not None
            and self.latest_attempt.attempt.calibration_key != self.calibration_key
        ):
            raise ValueError("latest calibration attempt must match the status key")
        if (
            self.latest_success is not None
            and self.latest_success.attempt.calibration_key != self.calibration_key
        ):
            raise ValueError("latest calibration success must match the status key")

        attempt_status = self.latest_attempt
        latest_success = self.latest_success
        if attempt_status is None or latest_success is None:
            if (
                attempt_status is not None
                and attempt_status.procedure_state == "closed"
                and attempt_status.closure is not None
                and attempt_status.closure.status == "succeeded"
            ):
                raise ValueError(
                    "successful latest attempt requires matching success evidence"
                )
            return self

        closure = attempt_status.closure
        if (
            attempt_status.procedure_state == "closed"
            and closure is not None
            and closure.status == "succeeded"
        ):
            if (
                latest_success.attempt != attempt_status.attempt
                or latest_success.succeeded_at != closure.closed_at
            ):
                raise ValueError("successful latest attempt must be the latest success")
        elif (
            attempt_status.attempt.procedure_run_id
            == latest_success.attempt.procedure_run_id
        ):
            raise ValueError("latest success must exactly match its successful attempt")
        return self


class CalibrationStatusSnapshot(_CalibrationModel):
    """One server-clock observation used for cohort admission CAS."""

    observed_at: datetime
    fanout_scope: _NonEmptyText
    fanout_active_count: int = Field(ge=0)
    statuses: tuple[CalibrationStatus, ...] = Field(
        default=(),
        max_length=MAX_CALIBRATION_STATUS_KEYS,
    )

    @field_validator("observed_at")
    @classmethod
    def canonicalize_observed_at(cls, value: datetime) -> datetime:
        return _canonical_utc(value, field_name="observed_at")

    @field_validator("fanout_scope")
    @classmethod
    def validate_fanout_scope(cls, value: str) -> str:
        return _non_blank(value, field_name="calibration fanout scope")

    @field_validator("statuses")
    @classmethod
    def validate_unique_statuses(
        cls,
        value: tuple[CalibrationStatus, ...],
    ) -> tuple[CalibrationStatus, ...]:
        _require_unique(
            (status.calibration_key for status in value),
            label="calibration status key",
        )
        return value

    @model_validator(mode="after")
    def validate_as_of_time(self) -> CalibrationStatusSnapshot:
        _validate_statuses_as_of(self.statuses, self.observed_at)
        return self


class CalibrationMissingSuccessDueReason(_CalibrationModel):
    kind: Literal["missing_success"] = "missing_success"


class CalibrationExpiredDueReason(_CalibrationModel):
    kind: Literal["expired"] = "expired"
    previous_success: CalibrationSuccessRef
    expired_at: datetime

    @field_validator("expired_at")
    @classmethod
    def canonicalize_expired_at(cls, value: datetime) -> datetime:
        return _canonical_utc(value, field_name="expired_at")

    @model_validator(mode="after")
    def validate_expiry(self) -> CalibrationExpiredDueReason:
        if self.expired_at < self.previous_success.succeeded_at:
            raise ValueError("calibration expiry cannot precede its success")
        return self


class CalibrationDefinitionChangedDueReason(_CalibrationModel):
    kind: Literal["definition_changed"] = "definition_changed"
    previous_success: CalibrationSuccessRef


class CalibrationInputsChangedDueReason(_CalibrationModel):
    kind: Literal["inputs_changed"] = "inputs_changed"
    previous_success: CalibrationSuccessRef


class CalibrationPublicationBaseChangedDueReason(_CalibrationModel):
    kind: Literal["publication_base_changed"] = "publication_base_changed"
    previous_success: CalibrationSuccessRef
    current_config_source: CalibrationConfigSourceRef

    @model_validator(mode="after")
    def validate_pending_publication(
        self,
    ) -> CalibrationPublicationBaseChangedDueReason:
        previous = self.previous_success
        if (
            previous.attempt.definition.success_policy != "published_result"
            or previous.publication is not None
        ):
            raise ValueError(
                "publication-base change requires an unpublished result success"
            )
        if previous.base_config_source == self.current_config_source:
            raise ValueError(
                "publication-base change requires a different current config source"
            )
        return self


class CalibrationDependencyChangedDueReason(_CalibrationModel):
    kind: Literal["dependency_changed"] = "dependency_changed"
    dependency_key: _NonEmptyText
    previous_success: CalibrationDependencyEvidence | None = None
    current_success: CalibrationDependencyEvidence | None = None

    @field_validator("dependency_key")
    @classmethod
    def validate_identity(cls, value: str) -> str:
        return _non_blank(value, field_name="changed calibration dependency key")

    @model_validator(mode="after")
    def validate_evidence(self) -> CalibrationDependencyChangedDueReason:
        if self.previous_success is None and self.current_success is None:
            raise ValueError("changed dependency requires previous or current success")
        if (
            self.current_success is not None
            and self.current_success.calibration_key != self.dependency_key
        ):
            raise ValueError("current dependency success must match its key")
        if (
            self.previous_success is not None
            and self.previous_success.calibration_key != self.dependency_key
        ):
            raise ValueError("previous dependency success must match its key")
        if self.previous_success == self.current_success:
            raise ValueError("changed dependency successes must differ")
        return self


class CalibrationForcedDueReason(_CalibrationModel):
    kind: Literal["forced"] = "forced"
    reason: _NonEmptyText

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, value: str) -> str:
        return _non_blank(value, field_name="forced calibration reason")


type CalibrationDueReason = Annotated[
    CalibrationMissingSuccessDueReason
    | CalibrationExpiredDueReason
    | CalibrationDefinitionChangedDueReason
    | CalibrationInputsChangedDueReason
    | CalibrationPublicationBaseChangedDueReason
    | CalibrationDependencyChangedDueReason
    | CalibrationForcedDueReason,
    Field(discriminator="kind"),
]


def calibration_freshness_fingerprint(
    *,
    definition: CalibrationDefinitionRef,
    target: CalibrationTargetRef,
    procedure: ProcedureDefinitionRef,
    input_fingerprint: Sha256ContentHash,
    dependencies: tuple[CalibrationDependencyEvidence, ...],
) -> Sha256ContentHash:
    """Hash every exact input that determines calibration freshness.

    Dependency evidence is a set keyed by ``calibration_key``; authoring order
    therefore cannot change freshness identity.
    """

    canonical_dependencies = _canonical_dependencies(
        dependencies,
        label="calibration freshness dependency key",
    )

    digest = stable_content_hash(
        {
            "codec": _CALIBRATION_FRESHNESS_CODEC,
            "definition": definition.model_dump(mode="json"),
            "target": target.model_dump(mode="json"),
            "procedure": procedure.model_dump(mode="json"),
            "input_fingerprint": input_fingerprint,
            "dependencies": [
                dependency.model_dump(mode="json")
                for dependency in canonical_dependencies
            ],
        }
    )
    return f"sha256:{digest}"


class CalibrationCohortMemberSpec(_CalibrationModel):
    """Immutable project-side decision to admit one calibration procedure."""

    member_id: _NonEmptyText
    calibration_key: _NonEmptyText
    definition: CalibrationDefinitionRef
    target: CalibrationTargetRef
    procedure: ProcedureDefinitionRef
    intent: ProcedureIntent
    input_fingerprint: Sha256ContentHash
    dependencies: tuple[CalibrationDependencyEvidence, ...] = Field(
        default=(),
        max_length=MAX_CALIBRATION_STATUS_KEYS,
    )
    freshness_fingerprint: Sha256ContentHash
    due_reasons: tuple[CalibrationDueReason, ...] = Field(
        min_length=1,
        max_length=MAX_CALIBRATION_STATUS_KEYS,
    )

    @field_validator("member_id", "calibration_key")
    @classmethod
    def validate_identity(cls, value: str) -> str:
        return _non_blank(value, field_name="calibration cohort member identity")

    @field_validator("dependencies")
    @classmethod
    def validate_unique_dependencies(
        cls,
        value: tuple[CalibrationDependencyEvidence, ...],
    ) -> tuple[CalibrationDependencyEvidence, ...]:
        return _canonical_dependencies(
            value,
            label="calibration dependency key",
        )

    @field_validator("due_reasons")
    @classmethod
    def validate_unique_due_reasons(
        cls,
        value: tuple[CalibrationDueReason, ...],
    ) -> tuple[CalibrationDueReason, ...]:
        _require_unique(
            (reason.model_dump_json() for reason in value),
            label="calibration due reason",
        )
        return value

    @model_validator(mode="after")
    def validate_member(self) -> CalibrationCohortMemberSpec:
        expected_key = calibration_key(self.definition.id, self.target)
        if self.calibration_key != expected_key:
            raise ValueError(
                "calibration member key must identify its definition and target"
            )
        if any(
            dependency.calibration_key == self.calibration_key
            for dependency in self.dependencies
        ):
            raise ValueError("calibration member cannot depend on itself")

        expected_freshness = calibration_freshness_fingerprint(
            definition=self.definition,
            target=self.target,
            procedure=self.procedure,
            input_fingerprint=self.input_fingerprint,
            dependencies=self.dependencies,
        )
        if self.freshness_fingerprint != expected_freshness:
            raise ValueError(
                "calibration freshness fingerprint must cover exact member inputs"
            )

        dependencies_by_key = {
            dependency.calibration_key: dependency for dependency in self.dependencies
        }
        for reason in self.due_reasons:
            if isinstance(reason, CalibrationExpiredDueReason):
                if (
                    reason.previous_success.attempt.calibration_key
                    != self.calibration_key
                ):
                    raise ValueError(
                        "member due reason success must match its calibration key"
                    )
            elif isinstance(
                reason,
                CalibrationDefinitionChangedDueReason
                | CalibrationInputsChangedDueReason,
            ):
                previous = reason.previous_success.attempt
                if previous.calibration_key != self.calibration_key:
                    raise ValueError(
                        "member due reason success must match its calibration key"
                    )
            elif isinstance(reason, CalibrationPublicationBaseChangedDueReason):
                if (
                    reason.previous_success.attempt.calibration_key
                    != self.calibration_key
                ):
                    raise ValueError(
                        "member due reason success must match its calibration key"
                    )
            elif isinstance(reason, CalibrationDependencyChangedDueReason):
                dependency = dependencies_by_key.get(reason.dependency_key)
                if reason.current_success != dependency:
                    raise ValueError(
                        "changed due reason must match current dependency evidence"
                    )
        return self


def calibration_cohort_member_request_key(
    cohort_id: str,
    spec: CalibrationCohortMemberSpec,
) -> str:
    """Derive the ProcedureRun request key for one exact cohort member."""

    selected_cohort_id = _non_blank(cohort_id, field_name="calibration cohort id")
    digest = stable_content_hash(
        {
            "codec": _CALIBRATION_COHORT_MEMBER_REQUEST_CODEC,
            "cohort_id": selected_cohort_id,
            "member": spec.model_dump(mode="json"),
        }
    )
    return f"calibration-cohort-member:{digest}"


class CalibrationCohortMember(_CalibrationModel):
    """Durable association between one cohort member and its ProcedureRun."""

    cohort_id: _NonEmptyText
    index: int = Field(ge=0)
    spec: CalibrationCohortMemberSpec
    procedure_run_id: _NonEmptyText
    request_key: _NonEmptyText
    admitted_at: datetime

    @field_validator("cohort_id", "procedure_run_id", "request_key")
    @classmethod
    def validate_identity(cls, value: str) -> str:
        return _non_blank(value, field_name="calibration cohort member identity")

    @field_validator("admitted_at")
    @classmethod
    def canonicalize_admitted_at(cls, value: datetime) -> datetime:
        return _canonical_utc(value, field_name="admitted_at")

    @model_validator(mode="after")
    def validate_request_key(self) -> CalibrationCohortMember:
        expected = calibration_cohort_member_request_key(self.cohort_id, self.spec)
        if self.request_key != expected:
            raise ValueError(
                "cohort member request key must identify its exact immutable spec"
            )
        return self

    @property
    def attempt_ref(self) -> CalibrationAttemptRef:
        return CalibrationAttemptRef(
            calibration_key=self.spec.calibration_key,
            cohort_id=self.cohort_id,
            member_id=self.spec.member_id,
            procedure_run_id=self.procedure_run_id,
            definition=self.spec.definition,
            target=self.spec.target,
            procedure=self.spec.procedure,
            input_fingerprint=self.spec.input_fingerprint,
            dependencies=self.spec.dependencies,
            freshness_fingerprint=self.spec.freshness_fingerprint,
            admitted_at=self.admitted_at,
        )


class CalibrationCohortSpec(_CalibrationModel):
    """Complete immutable basis for one bounded cohort admission."""

    definition: CalibrationDefinitionRef
    automatic_publication: CalibrationPublicationPolicyRef | None = None
    config_source: CalibrationConfigSourceRef
    fanout_scope: _NonEmptyText
    max_in_flight: int = Field(ge=1, le=MAX_CALIBRATION_COHORT_MEMBERS)
    observed_fanout_active_count: int = Field(ge=0)
    evaluated_at: datetime
    observations: tuple[CalibrationStatus, ...] = Field(
        max_length=MAX_CALIBRATION_STATUS_KEYS,
    )
    members: tuple[CalibrationCohortMemberSpec, ...] = Field(
        min_length=1,
        max_length=MAX_CALIBRATION_COHORT_MEMBERS,
    )

    @field_validator("fanout_scope")
    @classmethod
    def validate_fanout_scope(cls, value: str) -> str:
        return _non_blank(value, field_name="calibration fanout scope")

    @field_validator("evaluated_at")
    @classmethod
    def canonicalize_evaluated_at(cls, value: datetime) -> datetime:
        return _canonical_utc(value, field_name="evaluated_at")

    @model_validator(mode="after")
    def validate_spec(self) -> CalibrationCohortSpec:
        if self.definition.success_policy == "published_result" and not isinstance(
            self.config_source.scope, WorkingPointCalibrationScope
        ):
            raise ValueError("published calibration results require a working point")
        if (
            self.automatic_publication is not None
            and self.automatic_publication.calibration != self.definition
        ):
            raise ValueError(
                "automatic publication policy must pin the exact calibration definition"
            )
        if self.observed_fanout_active_count + len(self.members) > self.max_in_flight:
            raise ValueError(
                "calibration cohort members exceed observed fanout capacity"
            )
        _require_unique(
            (observation.calibration_key for observation in self.observations),
            label="calibration cohort observation key",
        )
        _require_unique(
            (member.member_id for member in self.members),
            label="calibration cohort member id",
        )
        _require_unique(
            (member.calibration_key for member in self.members),
            label="calibration cohort member key",
        )
        _validate_statuses_as_of(self.observations, self.evaluated_at)

        observations_by_key = {
            observation.calibration_key: observation
            for observation in self.observations
        }
        for member in self.members:
            if (
                scoped_calibration_target(member.target, self.config_source)
                != member.target
            ):
                raise ValueError("cohort member must match its exact calibration scope")
            if member.definition != self.definition:
                raise ValueError(
                    "calibration cohort members must use its exact definition"
                )
            observation = observations_by_key.get(member.calibration_key)
            if observation is None:
                raise ValueError(
                    "cohort observations must cover every member calibration key"
                )
            _validate_member_due_observation(
                member,
                observation,
                config_source=self.config_source,
            )
            for reason in member.due_reasons:
                if (
                    isinstance(reason, CalibrationExpiredDueReason)
                    and reason.expired_at > self.evaluated_at
                ):
                    raise ValueError(
                        "expired calibration must be due by cohort evaluation"
                    )
            for dependency in member.dependencies:
                if dependency.succeeded_at > self.evaluated_at:
                    raise ValueError(
                        "cohort dependency success cannot follow its evaluation"
                    )
                dependency_observation = observations_by_key.get(
                    dependency.calibration_key
                )
                if dependency_observation is None:
                    raise ValueError(
                        "cohort observations must cover every dependency key"
                    )
                latest_success = dependency_observation.latest_success
                if (
                    latest_success is None
                    or not latest_success.is_effective
                    or latest_success.dependency_evidence != dependency
                ):
                    raise ValueError(
                        "member dependency must equal observed latest success"
                    )
                if latest_success.attempt.target.owner != member.target.owner:
                    raise ValueError("calibration dependency belongs to another owner")
                if latest_success.attempt.target.batch_id != member.target.batch_id:
                    raise ValueError(
                        "calibration dependency belongs to another batch; "
                        "cross-batch and unscoped dependencies require an explicit "
                        "applicability policy, which is not supported yet"
                    )
        return self


def calibration_cohort_spec_hash(
    spec: CalibrationCohortSpec,
) -> Sha256ContentHash:
    """Hash the complete immutable cohort decision, excluding caller identity."""

    digest = stable_content_hash(
        {
            "codec": _CALIBRATION_COHORT_SPEC_CODEC,
            "spec": spec.model_dump(mode="json"),
        }
    )
    return f"sha256:{digest}"


class CalibrationCohort(_CalibrationModel):
    """Durable immutable cohort accepted under a caller-owned identity."""

    cohort_id: _NonEmptyText
    spec: CalibrationCohortSpec
    spec_hash: Sha256ContentHash
    created_at: datetime

    @field_validator("cohort_id")
    @classmethod
    def validate_identity(cls, value: str) -> str:
        return _non_blank(value, field_name="calibration cohort id")

    @field_validator("created_at")
    @classmethod
    def canonicalize_created_at(cls, value: datetime) -> datetime:
        return _canonical_utc(value, field_name="created_at")

    @model_validator(mode="after")
    def validate_spec_hash(self) -> CalibrationCohort:
        if self.spec_hash != calibration_cohort_spec_hash(self.spec):
            raise ValueError("calibration cohort spec hash must cover its exact spec")
        if self.created_at < self.spec.evaluated_at:
            raise ValueError("calibration cohort cannot precede its evaluation")
        if any(
            dependency.cohort_id == self.cohort_id
            for member in self.spec.members
            for dependency in member.dependencies
        ):
            raise ValueError(
                "calibration cohort cannot consume a same-cohort dependency"
            )
        return self


class CalibrationCohortSummary(_CalibrationModel):
    """Bounded list projection without repeating the complete member specs."""

    cohort_id: _NonEmptyText
    definition: CalibrationDefinitionRef
    fanout_scope: _NonEmptyText
    spec_hash: Sha256ContentHash
    member_count: int = Field(ge=1, le=MAX_CALIBRATION_COHORT_MEMBERS)
    evaluated_at: datetime
    created_at: datetime

    @field_validator("cohort_id", "fanout_scope")
    @classmethod
    def validate_identity(cls, value: str) -> str:
        return _non_blank(value, field_name="calibration cohort summary identity")

    @field_validator("evaluated_at", "created_at")
    @classmethod
    def canonicalize_time(cls, value: datetime, info: ValidationInfo) -> datetime:
        return _canonical_utc(
            value,
            field_name=info.field_name or "calibration cohort time",
        )

    @classmethod
    def from_cohort(cls, cohort: CalibrationCohort) -> CalibrationCohortSummary:
        return cls(
            cohort_id=cohort.cohort_id,
            definition=cohort.spec.definition,
            fanout_scope=cohort.spec.fanout_scope,
            spec_hash=cohort.spec_hash,
            member_count=len(cohort.spec.members),
            evaluated_at=cohort.spec.evaluated_at,
            created_at=cohort.created_at,
        )


class CalibrationPublicationAttention(_CalibrationModel):
    """Operator-visible deterministic publication failure."""

    actor: _NonEmptyText
    reason: _NonEmptyText
    required_at: datetime

    @field_validator("actor", "reason")
    @classmethod
    def validate_identity(cls, value: str) -> str:
        return _non_blank(value, field_name="calibration publication attention")

    @field_validator("required_at")
    @classmethod
    def canonicalize_required_at(cls, value: datetime) -> datetime:
        return _canonical_utc(value, field_name="required_at")


class CalibrationPublicationFailure(_CalibrationModel):
    """Terminal cohort-member failure observed by publication finalization."""

    failed_at: datetime

    @field_validator("failed_at")
    @classmethod
    def canonicalize_failed_at(cls, value: datetime) -> datetime:
        return _canonical_utc(value, field_name="failed_at")


class CalibrationWorkingPointSupersession(_CalibrationModel):
    kind: Literal["working_point_changed"] = "working_point_changed"
    superseded_by: CalibrationConfigSourceRef


class CalibrationSetupSupersession(_CalibrationModel):
    kind: Literal["setup_changed"] = "setup_changed"
    setup_content_hash: Sha256ContentHash


type CalibrationSupersessionEvidence = Annotated[
    CalibrationWorkingPointSupersession | CalibrationSetupSupersession,
    Field(discriminator="kind"),
]


class CalibrationPublicationSupersession(_CalibrationModel):
    """Terminal base drift that makes this cohort unsafe to publish."""

    evidence: CalibrationSupersessionEvidence
    superseded_at: datetime

    @field_validator("superseded_at")
    @classmethod
    def canonicalize_superseded_at(cls, value: datetime) -> datetime:
        return _canonical_utc(value, field_name="superseded_at")


class CalibrationPublicationCompletion(_CalibrationModel):
    """Exact durable config publication that completed finalization."""

    operation_id: _NonEmptyText
    published_at: datetime

    @field_validator("operation_id")
    @classmethod
    def validate_operation_id(cls, value: str) -> str:
        return _non_blank(value, field_name="calibration publication operation id")

    @field_validator("published_at")
    @classmethod
    def canonicalize_published_at(cls, value: datetime) -> datetime:
        return _canonical_utc(value, field_name="published_at")


class CalibrationCohortFinalization(_CalibrationModel):
    """Durable automatic-publication state for one immutable cohort."""

    cohort_id: _NonEmptyText
    spec_hash: Sha256ContentHash
    policy: CalibrationPublicationPolicyRef
    base_config_source: CalibrationConfigSourceRef
    revision: int = Field(ge=1)
    state: CalibrationCohortFinalizationState
    attempt_count: int = Field(ge=0)
    created_at: datetime
    updated_at: datetime
    ready_at: datetime | None = None
    available_at: datetime | None = None
    attention: CalibrationPublicationAttention | None = None
    failure: CalibrationPublicationFailure | None = None
    supersession: CalibrationPublicationSupersession | None = None
    publication: CalibrationPublicationCompletion | None = None

    @field_validator("cohort_id")
    @classmethod
    def validate_identity(cls, value: str) -> str:
        return _non_blank(value, field_name="calibration publication cohort id")

    @field_validator("created_at", "updated_at", "ready_at", "available_at")
    @classmethod
    def canonicalize_time(
        cls,
        value: datetime | None,
        info: ValidationInfo,
    ) -> datetime | None:
        if value is None:
            return None
        return _canonical_utc(
            value,
            field_name=info.field_name or "calibration publication time",
        )

    @model_validator(mode="after")
    def validate_state(self) -> CalibrationCohortFinalization:
        if self.updated_at < self.created_at:
            raise ValueError("calibration publication update cannot precede creation")
        if self.ready_at is not None and not (
            self.created_at <= self.ready_at <= self.updated_at
        ):
            raise ValueError(
                "calibration publication readiness must fall within its lifetime"
            )

        if self.state == "waiting":
            self._validate_waiting()
        elif self.state == "ready":
            self._validate_ready()
        elif self.state == "attention_required":
            self._validate_attention()
        elif self.state == "failed":
            self._validate_failure()
        elif self.state == "superseded":
            self._validate_supersession()
        else:
            self._validate_completion()
        if self.state != "ready" and self.available_at is not None:
            raise ValueError("only ready publication may have an available time")
        return self

    def _validate_waiting(self) -> None:
        if self.ready_at is not None or self.available_at is not None:
            raise ValueError("waiting publication cannot be ready or available")
        self._require_only_detail()

    def _validate_ready(self) -> None:
        if self.ready_at is None or self.available_at is None:
            raise ValueError("ready publication requires ready and available times")
        if self.available_at < self.updated_at:
            raise ValueError("ready publication availability cannot precede its update")
        self._require_only_detail()

    def _validate_attention(self) -> None:
        if self.ready_at is None or self.attention is None:
            raise ValueError(
                "publication attention requires prior readiness and audit detail"
            )
        self._require_only_detail("attention")
        if self.attention.required_at != self.updated_at:
            raise ValueError("publication attention time must match its state update")

    def _validate_failure(self) -> None:
        if self.ready_at is not None or self.failure is None:
            raise ValueError("failed publication requires failure before readiness")
        self._require_only_detail("failure")
        if self.failure.failed_at != self.updated_at:
            raise ValueError("publication failure time must match its state update")

    def _validate_supersession(self) -> None:
        if self.supersession is None:
            raise ValueError("superseded publication requires audit detail")
        self._require_only_detail("supersession")
        evidence = self.supersession.evidence
        if isinstance(evidence, CalibrationWorkingPointSupersession) and (
            evidence.superseded_by.scope != self.base_config_source.scope
            or evidence.superseded_by.context_ref == self.base_config_source.context_ref
        ):
            raise ValueError(
                "publication supersession must name another head "
                "of the same working point"
            )
        if self.supersession.superseded_at != self.updated_at:
            raise ValueError(
                "publication supersession time must match its state update"
            )

    def _validate_completion(self) -> None:
        if self.ready_at is None or self.publication is None:
            raise ValueError(
                "published finalization requires prior readiness and completion"
            )
        self._require_only_detail("publication")
        if self.publication.published_at != self.updated_at:
            raise ValueError("publication completion time must match its state update")

    def _require_only_detail(self, selected: str | None = None) -> None:
        details = {
            "attention": self.attention,
            "failure": self.failure,
            "supersession": self.supersession,
            "publication": self.publication,
        }
        if any(value is not None for key, value in details.items() if key != selected):
            raise ValueError("publication state contains incompatible audit detail")


def _validate_member_due_observation(
    member: CalibrationCohortMemberSpec,
    observation: CalibrationStatus,
    *,
    config_source: CalibrationConfigSourceRef,
) -> None:
    latest_success = observation.latest_success
    latest_attempt = observation.latest_attempt
    if (
        latest_attempt is not None
        and latest_attempt.procedure_state == "closed"
        and latest_attempt.closure is not None
        and latest_attempt.closure.status != "succeeded"
        and latest_attempt.attempt.freshness_fingerprint == member.freshness_fingerprint
        and not any(
            isinstance(reason, CalibrationForcedDueReason)
            for reason in member.due_reasons
        )
    ):
        raise ValueError(
            "retrying a failed calibration with unchanged freshness requires "
            "an explicit forced due reason"
        )
    previous_dependencies = (
        {}
        if latest_success is None
        else {
            dependency.calibration_key: dependency
            for dependency in latest_success.attempt.dependencies
        }
    )
    current_dependencies = {
        dependency.calibration_key: dependency for dependency in member.dependencies
    }
    for reason in member.due_reasons:
        if isinstance(reason, CalibrationMissingSuccessDueReason):
            if latest_success is not None:
                raise ValueError(
                    "missing-success due reason requires no observed success"
                )
        elif isinstance(reason, CalibrationExpiredDueReason):
            if reason.previous_success != latest_success:
                raise ValueError(
                    "member due reason must reference observed latest success"
                )
        elif isinstance(reason, CalibrationDefinitionChangedDueReason):
            if reason.previous_success != latest_success:
                raise ValueError(
                    "member due reason must reference observed latest success"
                )
            previous = reason.previous_success.attempt
            if (
                previous.definition == member.definition
                and previous.procedure == member.procedure
            ):
                raise ValueError(
                    "definition-changed due reason requires a prior definition "
                    "or procedure change"
                )
        elif isinstance(reason, CalibrationInputsChangedDueReason):
            if reason.previous_success != latest_success:
                raise ValueError(
                    "member due reason must reference observed latest success"
                )
            previous_input = (
                reason.previous_success.effective_input_fingerprint
                if reason.previous_success.is_effective
                else reason.previous_success.attempt.input_fingerprint
            )
            if previous_input == member.input_fingerprint:
                raise ValueError(
                    "inputs-changed due reason requires a prior input change"
                )
        elif isinstance(reason, CalibrationPublicationBaseChangedDueReason):
            if reason.previous_success != latest_success:
                raise ValueError(
                    "member due reason must reference observed latest success"
                )
            if reason.current_config_source != config_source:
                raise ValueError(
                    "publication-base due reason must match the cohort config source"
                )
        elif isinstance(reason, CalibrationDependencyChangedDueReason):
            if latest_success is None:
                raise ValueError(
                    "dependency-changed due reason requires an observed latest success"
                )
            if reason.previous_success != previous_dependencies.get(
                reason.dependency_key
            ):
                raise ValueError(
                    "dependency-changed due reason must reference observed prior "
                    "dependency evidence"
                )
            if reason.current_success != current_dependencies.get(
                reason.dependency_key
            ):
                raise ValueError(
                    "dependency-changed due reason must reference current member "
                    "dependency evidence"
                )


def _validate_statuses_as_of(
    statuses: tuple[CalibrationStatus, ...],
    observed_at: datetime,
) -> None:
    for status in statuses:
        success = status.latest_success
        if success is not None and success.succeeded_at > observed_at:
            raise ValueError("calibration success cannot follow its status observation")
        if (
            success is not None
            and success.publication is not None
            and success.publication.published_at > observed_at
        ):
            raise ValueError(
                "calibration publication cannot follow its status observation"
            )
        attempt = status.latest_attempt
        if attempt is not None and attempt.updated_at > observed_at:
            raise ValueError(
                "calibration attempt update cannot follow its status observation"
            )


def _canonical_dependencies(
    dependencies: tuple[CalibrationDependencyEvidence, ...],
    *,
    label: str,
) -> tuple[CalibrationDependencyEvidence, ...]:
    _require_unique(
        (dependency.calibration_key for dependency in dependencies),
        label=label,
    )
    return tuple(
        sorted(dependencies, key=lambda dependency: dependency.calibration_key)
    )


def _canonical_utc(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must include a UTC offset")
    return value.astimezone(UTC)


def _non_blank(value: str, *, field_name: str) -> str:
    if not value.strip():
        raise ValueError(f"{field_name} must be non-empty")
    return value


def _require_unique(values: Iterable[Hashable], *, label: str) -> None:
    selected = tuple(values)
    if len(selected) != len(set(selected)):
        raise ValueError(f"{label}s must be unique")


__all__ = [
    "MAX_CALIBRATION_COHORT_MEMBERS",
    "MAX_CALIBRATION_STATUS_KEYS",
    "CalibrationAttemptRef",
    "CalibrationAttemptStatus",
    "CalibrationCohort",
    "CalibrationCohortFinalization",
    "CalibrationCohortFinalizationState",
    "CalibrationCohortMember",
    "CalibrationCohortMemberSpec",
    "CalibrationCohortSpec",
    "CalibrationCohortSummary",
    "CalibrationConfigSourceRef",
    "CalibrationDefinitionChangedDueReason",
    "CalibrationDefinitionRef",
    "CalibrationDependencyChangedDueReason",
    "CalibrationDependencyEvidence",
    "CalibrationDueReason",
    "CalibrationExpiredDueReason",
    "CalibrationForcedDueReason",
    "CalibrationInputsChangedDueReason",
    "CalibrationMissingSuccessDueReason",
    "CalibrationPublicationAttention",
    "CalibrationPublicationBaseChangedDueReason",
    "CalibrationPublicationCompletion",
    "CalibrationPublicationFailure",
    "CalibrationPublicationPolicyRef",
    "CalibrationPublicationSupersession",
    "CalibrationStatus",
    "CalibrationStatusSnapshot",
    "CalibrationSuccessPolicy",
    "CalibrationSuccessPublication",
    "CalibrationSuccessRef",
    "CalibrationTargetRef",
    "calibration_cohort_member_request_key",
    "calibration_cohort_spec_hash",
    "calibration_freshness_fingerprint",
    "calibration_key",
    "calibration_target_sample_selectors",
]
