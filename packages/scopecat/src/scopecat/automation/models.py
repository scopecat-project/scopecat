"""Domain-neutral durable contracts for multi-run procedures."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Annotated, Literal, cast

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    PlainSerializer,
    field_validator,
    model_validator,
)

from scopecat.automation.interpretations import (
    InterpretationRequest,
    InterpretationResponse,
)
from scopecat.kernel.content_identity import (
    model_wire_content_hash,
    stable_content_hash,
)
from scopecat.kernel.frozen import freeze_json_mapping, thaw_json_value
from scopecat.kernel.run_outcome import utc_now
from scopecat.records.analysis import AnalysisInterpretationReference, AnalysisSubject
from scopecat.records.config import ConfigContentHash
from scopecat.records.content import Sha256ContentHash
from scopecat.records.plan_ref import ExperimentPlanRef
from scopecat.records.sample import SampleSelector

type _NonEmptyText = Annotated[str, Field(min_length=1)]


def _freeze_procedure_intent(value: Mapping[str, object]) -> Mapping[str, object]:
    return freeze_json_mapping(value, path="intent")


def _serialize_procedure_intent(value: Mapping[str, object]) -> object:
    return thaw_json_value(value)


type ProcedureIntent = Annotated[
    Mapping[str, object],
    AfterValidator(_freeze_procedure_intent),
    PlainSerializer(
        _serialize_procedure_intent,
        return_type=dict[str, JsonValue],
    ),
]

type ProcedureRunState = Literal[
    "ready",
    "leased",
    "waiting_for_input",
    "attention_required",
    "closed",
]
type ProcedureStepOperation = Literal[
    "run",
    "analysis",
    "config_activation",
    "config_publish",
    "interpretation",
]
type ProcedureStepAttemptState = Literal[
    "running",
    "succeeded",
    "failed",
    "waiting_for_input",
    "attention_required",
]
type ProcedureCloseStatus = Literal["succeeded", "failed", "cancelled"]


class _ProcedureModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ProcedureDefinitionRef(_ProcedureModel):
    """Versioned executable definition pinned by its implementation fingerprint."""

    id: _NonEmptyText
    version: _NonEmptyText
    fingerprint: Sha256ContentHash

    @field_validator("id", "version")
    @classmethod
    def validate_identity(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("procedure definition identity must be non-empty")
        return value


def procedure_intent_hash(
    definition: ProcedureDefinitionRef,
    intent: Mapping[str, object],
    *,
    samples: tuple[SampleSelector, ...] = (),
    recovery: ProcedureRecoverySource | None = None,
    plan_ref: ExperimentPlanRef | None = None,
) -> Sha256ContentHash:
    """Hash the exact definition, intent, and sample scope used by a worker."""

    identity = {
        "definition": definition.model_dump(mode="json"),
        "intent": cast("dict[str, JsonValue]", thaw_json_value(intent)),
        "samples": [sample.model_dump(mode="json") for sample in samples],
    }
    if plan_ref is not None:
        identity["plan_ref"] = plan_ref.model_dump(mode="json")
    if recovery is not None:
        identity["recovery"] = recovery.model_dump(mode="json")
    return f"sha256:{stable_content_hash(identity)}"


class ProcedureClosure(_ProcedureModel):
    """Terminal result of a procedure run."""

    status: ProcedureCloseStatus
    actor: _NonEmptyText | None = None
    closed_at: datetime = Field(default_factory=utc_now)
    reason: str | None = None

    @model_validator(mode="after")
    def validate_reason(self) -> ProcedureClosure:
        if self.status == "succeeded":
            if self.reason is not None:
                raise ValueError("successful procedure closure cannot have a reason")
        elif self.reason is None or not self.reason.strip():
            raise ValueError("failed or cancelled procedure closure requires a reason")
        return self


class RunOutputRef(_ProcedureModel):
    """Exact child run produced by a procedure step."""

    kind: Literal["run"] = "run"
    run_id: _NonEmptyText


class AnalysisPublicationOutputRef(_ProcedureModel):
    """Exact analysis publication produced by a procedure step."""

    kind: Literal["analysis"] = "analysis"
    subject: AnalysisSubject
    analysis_record_id: _NonEmptyText


class ConfigActivationOutputRef(_ProcedureModel):
    """Exact configuration-registry activation produced by a procedure step."""

    kind: Literal["config_activation"] = "config_activation"
    generation: int = Field(ge=1)
    entry_id: _NonEmptyText
    entry_content_hash: ConfigContentHash


class ConfigPublishOutputRef(_ProcedureModel):
    """Exact configuration revision published and activated by a procedure step."""

    kind: Literal["config_publish"] = "config_publish"
    generation: int = Field(ge=1)
    entry_id: _NonEmptyText
    entry_content_hash: ConfigContentHash


class InterpretationOutputRef(_ProcedureModel):
    """Exact typed judgment supplied for one durable interpretation step."""

    kind: Literal["interpretation"] = "interpretation"
    procedure_run_id: _NonEmptyText
    step_key: _NonEmptyText
    request_hash: Sha256ContentHash
    response: InterpretationResponse

    @property
    def analysis_reference(self) -> AnalysisInterpretationReference:
        return AnalysisInterpretationReference(
            procedure_run_id=self.procedure_run_id,
            step_key=self.step_key,
            request_hash=self.request_hash,
            response_hash=f"sha256:{model_wire_content_hash(self.response)}",
        )


type ProcedureStepOutputRef = Annotated[
    RunOutputRef
    | AnalysisPublicationOutputRef
    | ConfigActivationOutputRef
    | ConfigPublishOutputRef
    | InterpretationOutputRef,
    Field(discriminator="kind"),
]


class ProcedureCancellation(_ProcedureModel):
    """Durable request to stop after the current step has settled."""

    actor: _NonEmptyText
    reason: _NonEmptyText
    requested_at: datetime
    requested_revision: int = Field(ge=1)


class ProcedureResourceWait(_ProcedureModel):
    """Exact unstarted child retained while its worker is released."""

    step_key: _NonEmptyText
    run_id: _NonEmptyText


class ProcedureRecoveryStep(_ProcedureModel):
    """Exact immutable attempt whose contract is retained across recovery."""

    step_key: _NonEmptyText
    attempt: int = Field(ge=1)
    revision: int = Field(ge=1)
    intent_hash: Sha256ContentHash


class ProcedureRecoverySource(_ProcedureModel):
    """Audited link to one closed software failure; never authority to retry it."""

    adapter_id: _NonEmptyText
    procedure_run_id: _NonEmptyText
    revision: int = Field(ge=1)
    definition: ProcedureDefinitionRef
    run_step: ProcedureRecoveryStep
    failed_analysis_step: ProcedureRecoveryStep
    retained_run: RunOutputRef


class ProcedureRun(_ProcedureModel):
    """Current durable state of one version-pinned procedure invocation."""

    procedure_run_id: _NonEmptyText
    request_key: _NonEmptyText
    definition: ProcedureDefinitionRef
    intent: ProcedureIntent
    intent_hash: Sha256ContentHash
    samples: tuple[SampleSelector, ...] = ()
    revision: int = Field(ge=1)
    state: ProcedureRunState
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    attention_reason: str | None = None
    closure: ProcedureClosure | None = None
    cancellation: ProcedureCancellation | None = None
    resource_wait: ProcedureResourceWait | None = None
    recovery: ProcedureRecoverySource | None = None
    plan_ref: ExperimentPlanRef | None = None

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

    @model_validator(mode="after")
    def validate_state_details(self) -> ProcedureRun:
        expected_intent_hash = procedure_intent_hash(
            self.definition,
            self.intent,
            samples=self.samples,
            recovery=self.recovery,
            plan_ref=self.plan_ref,
        )
        if self.intent_hash != expected_intent_hash:
            raise ValueError(
                "procedure intent hash must cover its definition, intent, and samples"
            )
        if self.updated_at < self.created_at:
            raise ValueError("procedure run cannot be updated before it is created")

        if self.cancellation is not None:
            if self.cancellation.requested_revision > self.revision:
                raise ValueError("cancellation revision cannot be in the future")
            if not self.created_at <= self.cancellation.requested_at <= self.updated_at:
                raise ValueError("cancellation time must be within the run lifetime")
        if self.state == "attention_required":
            if self.attention_reason is None or not self.attention_reason.strip():
                raise ValueError("attention-required procedure run requires a reason")
        elif self.attention_reason is not None:
            raise ValueError(
                "attention reason is only valid for an attention-required procedure run"
            )

        if self.state == "closed":
            if self.closure is None:
                raise ValueError("closed procedure run requires a closure")
            if not self.created_at <= self.closure.closed_at <= self.updated_at:
                raise ValueError(
                    "procedure closure time must be within its run lifetime"
                )
        elif self.closure is not None:
            raise ValueError("closure is only valid for a closed procedure run")
        return self


class ProcedureStepAttempt(_ProcedureModel):
    """One revisioned attempt at a stable, intent-identified procedure step."""

    procedure_run_id: _NonEmptyText
    step_key: _NonEmptyText
    attempt: int = Field(ge=1)
    operation: ProcedureStepOperation
    intent_hash: Sha256ContentHash
    inputs: tuple[ProcedureStepOutputRef, ...] = ()
    revision: int = Field(ge=1)
    state: ProcedureStepAttemptState
    started_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    finished_at: datetime | None = None
    output: ProcedureStepOutputRef | None = None
    interpretation_request: InterpretationRequest | None = None
    failure_reason: str | None = None
    attention_reason: str | None = None

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

    @model_validator(mode="after")
    def validate_state_details(self) -> ProcedureStepAttempt:
        if self.updated_at < self.started_at:
            raise ValueError("procedure step cannot be updated before it starts")
        if self.finished_at is not None and not (
            self.started_at <= self.finished_at <= self.updated_at
        ):
            raise ValueError("procedure step finish time must be within its lifetime")

        if self.state == "running":
            if self.finished_at is not None:
                raise ValueError("running procedure step cannot be finished")
        elif self.state == "waiting_for_input":
            if self.finished_at is not None:
                raise ValueError("waiting procedure step cannot be finished")
            if self.interpretation_request is None:
                raise ValueError(
                    "waiting procedure step requires an interpretation request"
                )
        elif self.state == "succeeded":
            if self.finished_at is None or self.output is None:
                raise ValueError(
                    "successful procedure step requires output and finish time"
                )
        elif self.state == "failed":
            if self.finished_at is None:
                raise ValueError("failed procedure step requires a finish time")
            if self.failure_reason is None or not self.failure_reason.strip():
                raise ValueError("failed procedure step requires a reason")
        else:
            if self.attention_reason is None or not self.attention_reason.strip():
                raise ValueError("attention-required procedure step requires a reason")
            if self.finished_at is not None:
                raise ValueError("attention-required procedure step cannot be finished")

        if self.state != "succeeded" and self.output is not None:
            raise ValueError("output is only valid for a successful procedure step")
        if self.state != "failed" and self.failure_reason is not None:
            raise ValueError("failure reason is only valid for a failed procedure step")
        if self.state != "attention_required" and self.attention_reason is not None:
            raise ValueError(
                "attention reason is only valid for an attention-required "
                "procedure step"
            )
        if self.output is not None and self.output.kind != self.operation:
            raise ValueError("procedure step output kind must match its operation")
        self._validate_interpretation()
        return self

    def _validate_interpretation(self) -> None:
        if self.operation == "interpretation":
            request = self.interpretation_request
            if self.state in {"waiting_for_input", "succeeded"} and request is None:
                raise ValueError("durable interpretation step requires its request")
            if request is not None and self.intent_hash != request.request_hash:
                raise ValueError(
                    "interpretation step intent must identify its durable request"
                )
            if self.output is not None:
                if not isinstance(self.output, InterpretationOutputRef):
                    raise ValueError(
                        "interpretation step requires an interpretation output"
                    )
                if (
                    self.output.procedure_run_id != self.procedure_run_id
                    or self.output.step_key != self.step_key
                ):
                    raise ValueError(
                        "interpretation output must belong to its procedure step"
                    )
                if request is None or self.output.request_hash != request.request_hash:
                    raise ValueError(
                        "interpretation output must answer the durable request"
                    )
        elif self.interpretation_request is not None:
            raise ValueError(
                "interpretation request is only valid for an interpretation step"
            )
