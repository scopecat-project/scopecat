"""Typed wire contracts for durable procedure control."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    field_validator,
    model_validator,
)

from scopecat.automation.definition import MAX_PROCEDURE_REGISTRY_SIZE
from scopecat.automation.interpretations import (
    InterpretationActorKind,
    InterpretationRequest,
)
from scopecat.automation.models import (
    InterpretationOutputRef,
    ProcedureCloseStatus,
    ProcedureDefinitionRef,
    ProcedureIntent,
    ProcedureRecoverySource,
    ProcedureRun,
    ProcedureRunState,
    ProcedureStepAttempt,
    ProcedureStepOperation,
    ProcedureStepOutputRef,
    procedure_intent_hash,
)
from scopecat.kernel.content_identity import stable_content_hash
from scopecat.records.content import Sha256ContentHash
from scopecat.records.sample import SampleSelector

type _NonEmptyText = Annotated[str, Field(min_length=1)]


class _WireModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        allow_inf_nan=False,
    )


class ProcedureSubmitCommand(_WireModel):
    """Submit one canonical invocation under a caller-owned retry key."""

    request_key: _NonEmptyText
    definition: ProcedureDefinitionRef
    intent: ProcedureIntent
    samples: tuple[SampleSelector, ...] = ()
    expected_config_generation: int | None = Field(default=None, ge=1)
    recovery: ProcedureRecoverySource | None = None

    @property
    def intent_hash(self) -> Sha256ContentHash:
        return procedure_intent_hash(
            self.definition,
            self.intent,
            samples=self.samples,
            recovery=self.recovery,
        )

    @field_validator("samples")
    @classmethod
    def validate_samples(
        cls,
        value: tuple[SampleSelector, ...],
    ) -> tuple[SampleSelector, ...]:
        roles = tuple(selector.role for selector in value)
        if len(roles) != len(set(roles)):
            raise ValueError("procedure sample selector roles must be unique")
        sample_ids = tuple(selector.sample_id for selector in value)
        if len(sample_ids) != len(set(sample_ids)):
            raise ValueError("one sample cannot fill multiple procedure roles")
        return value


class ProcedureSubmitReceipt(_WireModel):
    """Canonical snapshot returned for an idempotent procedure submission."""

    run: ProcedureRun


class ProcedureRunListQuery(_WireModel):
    """Bounded keyset query over procedure runs."""

    cursor: int | None = Field(default=None, ge=1)
    limit: int = Field(default=50, ge=1, le=200)
    state: ProcedureRunState | None = None
    request_key: str | None = Field(default=None, min_length=1)


class ProcedureRunPage(_WireModel):
    """One newest-first page of procedure run snapshots."""

    items: tuple[ProcedureRun, ...] = ()
    next_cursor: int | None = Field(default=None, ge=1)

    @field_validator("items")
    @classmethod
    def validate_unique_runs(
        cls,
        value: tuple[ProcedureRun, ...],
    ) -> tuple[ProcedureRun, ...]:
        ids = tuple(item.procedure_run_id for item in value)
        if len(ids) != len(set(ids)):
            raise ValueError("procedure run page ids must be unique")
        return value


class ProcedureRunnableQuery(_WireModel):
    """Bound work discovery to exact definitions available in one worker."""

    definitions: tuple[ProcedureDefinitionRef, ...] = Field(
        max_length=MAX_PROCEDURE_REGISTRY_SIZE
    )
    limit: int = Field(default=50, ge=1, le=200)

    @field_validator("definitions")
    @classmethod
    def validate_unique_definitions(
        cls,
        value: tuple[ProcedureDefinitionRef, ...],
    ) -> tuple[ProcedureDefinitionRef, ...]:
        identities = tuple(item.model_dump_json() for item in value)
        if len(identities) != len(set(identities)):
            raise ValueError("runnable procedure definitions must be unique")
        return value


class ProcedureRunnablePage(_WireModel):
    """Oldest-first runnable snapshots, bounded independently of work claims."""

    items: tuple[ProcedureRun, ...] = ()
    has_more: bool = False

    @field_validator("items")
    @classmethod
    def validate_runnable_runs(
        cls,
        value: tuple[ProcedureRun, ...],
    ) -> tuple[ProcedureRun, ...]:
        ids = tuple(item.procedure_run_id for item in value)
        if len(ids) != len(set(ids)):
            raise ValueError("runnable procedure page ids must be unique")
        if any(item.state not in {"ready", "leased"} for item in value):
            raise ValueError("runnable procedure page requires runnable states")
        return value


class ProcedureStepAttemptListQuery(_WireModel):
    """Bounded keyset query over attempts belonging to one procedure run."""

    cursor: int | None = Field(default=None, ge=1)
    limit: int = Field(default=50, ge=1, le=200)


class ProcedureStepAttemptPage(_WireModel):
    """One newest-first page of exact attempts for one procedure run."""

    procedure_run_id: _NonEmptyText
    items: tuple[ProcedureStepAttempt, ...] = ()
    next_cursor: int | None = Field(default=None, ge=1)

    @field_validator("items")
    @classmethod
    def validate_unique_attempts(
        cls,
        value: tuple[ProcedureStepAttempt, ...],
    ) -> tuple[ProcedureStepAttempt, ...]:
        identities = tuple((item.step_key, item.attempt) for item in value)
        if len(identities) != len(set(identities)):
            raise ValueError("procedure step attempt page identities must be unique")
        return value

    @model_validator(mode="after")
    def validate_ownership(self) -> ProcedureStepAttemptPage:
        if any(item.procedure_run_id != self.procedure_run_id for item in self.items):
            raise ValueError("procedure step attempt page items must belong to its run")
        return self


class ProcedureWorkerLease(_WireModel):
    """Renewable fencing authority for one procedure worker."""

    procedure_run_id: _NonEmptyText
    worker_id: _NonEmptyText
    lease_token: _NonEmptyText
    issued_at: datetime
    renewed_at: datetime
    expires_at: datetime
    heartbeat_interval_seconds: float = Field(gt=0)

    @model_validator(mode="after")
    def validate_lifetime(self) -> ProcedureWorkerLease:
        issued_at = _aware_datetime(self.issued_at, field_name="issued_at")
        renewed_at = _aware_datetime(self.renewed_at, field_name="renewed_at")
        expires_at = _aware_datetime(self.expires_at, field_name="expires_at")
        if renewed_at < issued_at:
            raise ValueError("procedure lease cannot be renewed before it is issued")
        if expires_at <= renewed_at:
            raise ValueError("procedure lease must expire after it is renewed")
        return self


class ProcedureWorkerLeaseAcquireCommand(_WireModel):
    procedure_run_id: _NonEmptyText
    worker_id: _NonEmptyText
    expected_run_revision: int = Field(ge=1)


class ProcedureWorkerLeaseHeartbeatCommand(_WireModel):
    procedure_run_id: _NonEmptyText
    lease_token: _NonEmptyText


class ProcedureWorkerLeaseReleaseCommand(_WireModel):
    procedure_run_id: _NonEmptyText
    lease_token: _NonEmptyText
    expected_run_revision: int = Field(ge=1)


class _ProcedureWorkerLeaseReceipt(_WireModel):
    run: ProcedureRun
    lease: ProcedureWorkerLease

    @model_validator(mode="after")
    def validate_alignment(self) -> _ProcedureWorkerLeaseReceipt:
        if self.run.procedure_run_id != self.lease.procedure_run_id:
            raise ValueError("procedure lease receipt identities must match")
        if self.run.state != "leased":
            raise ValueError("procedure lease receipt requires a leased run")
        return self


class ProcedureWorkerLeaseAcquireReceipt(_ProcedureWorkerLeaseReceipt):
    """Lease and run snapshot returned after successful acquisition."""


class ProcedureWorkerLeaseHeartbeatReceipt(_ProcedureWorkerLeaseReceipt):
    """Renewed lease and unchanged leased procedure snapshot."""


class ProcedureWorkerLeaseReleaseReceipt(_WireModel):
    """Procedure snapshot returned after a clean worker yield."""

    run: ProcedureRun

    @model_validator(mode="after")
    def validate_released(self) -> ProcedureWorkerLeaseReleaseReceipt:
        if self.run.state != "ready":
            raise ValueError("procedure lease release receipt requires a ready run")
        return self


class _FencedProcedureCommand(_WireModel):
    procedure_run_id: _NonEmptyText
    lease_token: _NonEmptyText
    expected_run_revision: int = Field(ge=1)


class _FencedProcedureStepCommand(_FencedProcedureCommand):
    step_key: _NonEmptyText
    attempt: int = Field(ge=1)
    expected_step_revision: int = Field(ge=1)


class ProcedureStepBeginCommand(_FencedProcedureCommand):
    """Begin or replay one stable, intent-identified procedure step."""

    step_key: _NonEmptyText
    operation: ProcedureStepOperation
    intent_hash: Sha256ContentHash
    inputs: tuple[ProcedureStepOutputRef, ...] = ()

    @field_validator("inputs")
    @classmethod
    def validate_unique_inputs(
        cls,
        value: tuple[ProcedureStepOutputRef, ...],
    ) -> tuple[ProcedureStepOutputRef, ...]:
        identities = tuple(item.model_dump_json() for item in value)
        if len(identities) != len(set(identities)):
            raise ValueError("procedure step input references must be unique")
        return value


def procedure_step_operation_id(
    procedure_run_id: str,
    step_key: str,
) -> str:
    """Return the stable side-effect id shared by retries of one step."""

    digest = stable_content_hash(
        {
            "schema": "scopecat.procedure_step_operation.v2",
            "procedure_run_id": procedure_run_id,
            "step_key": step_key,
        }
    )
    return f"procedure-step:{digest}"


class ProcedureStepBeginReceipt(_WireModel):
    run: ProcedureRun
    step: ProcedureStepAttempt
    operation_id: _NonEmptyText

    @model_validator(mode="after")
    def validate_alignment(self) -> ProcedureStepBeginReceipt:
        _validate_step_receipt_alignment(self.run, self.step)
        if self.run.state != "leased":
            raise ValueError("procedure step begin receipt requires a leased run")
        expected_operation_id = procedure_step_operation_id(
            self.step.procedure_run_id,
            self.step.step_key,
        )
        if self.operation_id != expected_operation_id:
            raise ValueError("procedure step operation id must be deterministic")
        return self


class ProcedureStepCompleteCommand(_FencedProcedureStepCommand):
    output: ProcedureStepOutputRef


class ProcedureStepResourceWaitCommand(_FencedProcedureStepCommand):
    run_id: _NonEmptyText


class ProcedureStepResourceWaitReceipt(_WireModel):
    run: ProcedureRun


class ProcedureStepCompleteReceipt(_WireModel):
    run: ProcedureRun
    step: ProcedureStepAttempt

    @model_validator(mode="after")
    def validate_result(self) -> ProcedureStepCompleteReceipt:
        _validate_step_receipt_alignment(self.run, self.step)
        if self.run.state != "leased" or self.step.state != "succeeded":
            raise ValueError(
                "procedure step completion requires a leased run and successful step"
            )
        return self


class ProcedureStepFailCommand(_FencedProcedureStepCommand):
    reason: _NonEmptyText

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, value: str) -> str:
        return _non_blank(value, field_name="procedure step failure reason")


class ProcedureStepFailReceipt(_WireModel):
    run: ProcedureRun
    step: ProcedureStepAttempt

    @model_validator(mode="after")
    def validate_result(self) -> ProcedureStepFailReceipt:
        _validate_step_receipt_alignment(self.run, self.step)
        if self.run.state != "leased" or self.step.state != "failed":
            raise ValueError(
                "procedure step failure requires a leased run and failed step"
            )
        return self


class ProcedureStepAttentionCommand(_FencedProcedureStepCommand):
    reason: _NonEmptyText

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, value: str) -> str:
        return _non_blank(value, field_name="procedure step attention reason")


class ProcedureStepAttentionReceipt(_WireModel):
    run: ProcedureRun
    step: ProcedureStepAttempt

    @model_validator(mode="after")
    def validate_result(self) -> ProcedureStepAttentionReceipt:
        _validate_step_receipt_alignment(self.run, self.step)
        if (
            self.run.state != "attention_required"
            or self.step.state != "attention_required"
        ):
            raise ValueError(
                "procedure step attention receipt requires attention states"
            )
        return self


class ProcedureStepInputWaitCommand(_FencedProcedureStepCommand):
    """Publish a typed request and release the worker while input is pending."""

    request: InterpretationRequest


class ProcedureStepInputWaitReceipt(_WireModel):
    run: ProcedureRun
    step: ProcedureStepAttempt

    @model_validator(mode="after")
    def validate_result(self) -> ProcedureStepInputWaitReceipt:
        _validate_step_receipt_alignment(self.run, self.step)
        if (
            not (
                self.run.state == "waiting_for_input"
                or (
                    self.run.closure is not None
                    and self.run.closure.status == "cancelled"
                )
            )
            or self.step.state != "waiting_for_input"
        ):
            raise ValueError("procedure input wait receipt requires waiting states")
        return self


class ProcedureStepInputSubmitCommand(_WireModel):
    """Answer one exact waiting request without holding a worker lease."""

    procedure_run_id: _NonEmptyText
    expected_run_revision: int = Field(ge=1)
    step_key: _NonEmptyText
    attempt: int = Field(ge=1)
    expected_step_revision: int = Field(ge=1)
    request_hash: Sha256ContentHash
    actor: _NonEmptyText
    actor_kind: InterpretationActorKind
    value: JsonValue
    note: str = ""

    @field_validator("actor")
    @classmethod
    def validate_actor(cls, value: str) -> str:
        return _non_blank(value, field_name="interpretation actor")


class ProcedureStepInputSubmitReceipt(_WireModel):
    run: ProcedureRun
    step: ProcedureStepAttempt
    output: InterpretationOutputRef

    @model_validator(mode="after")
    def validate_result(self) -> ProcedureStepInputSubmitReceipt:
        _validate_step_receipt_alignment(self.run, self.step)
        if self.step.state != "succeeded":
            raise ValueError("procedure input submission requires a successful step")
        if self.step.output != self.output:
            raise ValueError("procedure input receipt output must match its step")
        return self


class ProcedureStepAttentionRetryCommand(_WireModel):
    """Authorize a new attempt of one exact quarantined step."""

    procedure_run_id: _NonEmptyText
    expected_run_revision: int = Field(ge=1)
    step_key: _NonEmptyText
    attempt: int = Field(ge=1)
    expected_step_revision: int = Field(ge=1)


class ProcedureStepAttentionRetryReceipt(_WireModel):
    """Ready procedure state after one exact attention retry authorization."""

    run: ProcedureRun
    step: ProcedureStepAttempt

    @model_validator(mode="after")
    def validate_result(self) -> ProcedureStepAttentionRetryReceipt:
        _validate_step_receipt_alignment(self.run, self.step)
        if self.run.state != "ready" or self.step.state != "attention_required":
            raise ValueError(
                "procedure step retry requires a ready run and quarantined prior "
                "attempt"
            )
        return self


class ProcedureRunAttentionCommand(_FencedProcedureCommand):
    reason: _NonEmptyText

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, value: str) -> str:
        return _non_blank(value, field_name="procedure run attention reason")


class ProcedureRunAttentionReceipt(_WireModel):
    run: ProcedureRun

    @model_validator(mode="after")
    def validate_result(self) -> ProcedureRunAttentionReceipt:
        if self.run.state != "attention_required":
            raise ValueError("procedure run attention receipt requires attention")
        return self


class ProcedureCancelCommand(_WireModel):
    """Request cancellation at an exact observed revision."""

    procedure_run_id: _NonEmptyText
    expected_run_revision: int = Field(ge=1)
    actor: _NonEmptyText
    reason: _NonEmptyText

    @field_validator("actor", "reason")
    @classmethod
    def validate_nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("cancellation actor and reason must be nonblank")
        return value


class ProcedureCancelReceipt(_WireModel):
    """Current run: a pending request does not mean execution has stopped."""

    run: ProcedureRun


class ProcedureCloseCommand(_FencedProcedureCommand):
    status: ProcedureCloseStatus
    reason: str | None = None

    @model_validator(mode="after")
    def validate_reason(self) -> ProcedureCloseCommand:
        if self.status == "succeeded":
            if self.reason is not None:
                raise ValueError("successful procedure close cannot have a reason")
        elif self.reason is None or not self.reason.strip():
            raise ValueError("failed or cancelled procedure close requires a reason")
        return self


class ProcedureCloseReceipt(_WireModel):
    run: ProcedureRun

    @model_validator(mode="after")
    def validate_result(self) -> ProcedureCloseReceipt:
        if self.run.state != "closed":
            raise ValueError("procedure close receipt requires a closed run")
        return self


def _validate_step_receipt_alignment(
    run: ProcedureRun,
    step: ProcedureStepAttempt,
) -> None:
    if run.procedure_run_id != step.procedure_run_id:
        raise ValueError("procedure step receipt identities must match")


def _aware_datetime(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must include a UTC offset")
    return value


def _non_blank(value: str, *, field_name: str) -> str:
    if not value.strip():
        raise ValueError(f"{field_name} must be non-empty")
    return value
