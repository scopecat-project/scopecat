"""Typed answers bound to the exact durable question the author inspected."""

from copy import deepcopy
from dataclasses import dataclass, field

from scopecat.analysis.facts import AnalysisFactSchema
from scopecat.automation import (
    InterpretationActorKind,
    InterpretationRequest,
    ProcedureRun,
    ProcedureStepAttempt,
    ProcedureStepOutputRef,
)
from scopecat.automation.wire import (
    ProcedureInputContinueReceipt,
    ProcedureStepInputSubmitCommand,
    ProcedureStepInputSubmitReceipt,
)
from scopecat.daemon.client import DaemonClient


@dataclass(frozen=True, slots=True)
class PendingProcedureInput[ValueT]:
    """A detached question and its original concurrency fence, not a reservation.

    Obtain through ``AuthorProcedure.pending_input``. A conflict requires reading
    and reviewing a new pending input; ``respond`` never refreshes this one or
    dispatches work. Repeating an identical answer uses the existing idempotent
    submission protocol.
    """

    _session: DaemonClient = field(repr=False, compare=False)
    _run: ProcedureRun = field(repr=False)
    _step: ProcedureStepAttempt = field(repr=False)
    _schema: AnalysisFactSchema[ValueT] = field(repr=False)

    def __post_init__(self) -> None:
        if self._run.state != "waiting_for_input":
            raise ValueError("procedure is not waiting for interpretation input")
        if (
            self._step.procedure_run_id != self._run.procedure_run_id
            or self._step.state != "waiting_for_input"
            or self._step.operation != "interpretation"
        ):
            raise ValueError("step is not a waiting interpretation of this procedure")
        request = self._step.interpretation_request
        if request is None:
            raise RuntimeError("waiting interpretation has no durable request")
        schema = self._schema
        if (
            request.schema_id != schema.id
            or request.schema_codec != schema.schema_codec
            or request.schema_hash != schema.schema_hash
            or request.structure != schema.structure
        ):
            raise ValueError("local response schema does not match the durable request")
        # Frozen models can contain mutable JSON; neither input aliases nor views
        # handed to an editor may change the question or schema we will submit.
        object.__setattr__(self, "_run", self._run.model_copy(deep=True))
        object.__setattr__(self, "_step", self._step.model_copy(deep=True))
        object.__setattr__(self, "_schema", deepcopy(schema))

    @property
    def request(self) -> InterpretationRequest:
        """The captured question, with a separate copy for display/editing code."""
        request = self._step.interpretation_request
        assert request is not None
        return request.model_copy(deep=True)

    @property
    def inputs(self) -> tuple[ProcedureStepOutputRef, ...]:
        """Exact retained evidence references attached to the captured question."""
        return deepcopy(self._step.inputs)

    def respond(
        self,
        value: ValueT,
        *,
        actor: str,
        actor_kind: InterpretationActorKind = "human",
        note: str = "",
    ) -> ProcedureStepInputSubmitReceipt:
        """Answer the captured request without rereading, rebinding or resuming."""
        return self._session.submit_procedure_step_input(
            self._response_command(value, actor=actor, actor_kind=actor_kind, note=note)
        )

    def respond_and_continue(
        self,
        value: ValueT,
        *,
        actor: str,
        actor_kind: InterpretationActorKind = "human",
        note: str = "",
    ) -> ProcedureInputContinueReceipt:
        """Save this exact answer and continue only its normal input wait.

        Inspect continuation/dispatch_error: an answer can be saved even when
        dispatch fails. Repeat this same call to retry scheduling, without a new
        answer or acquisition. Faults, cancellation and other pauses stay blocked.
        """
        return self._session.submit_and_continue_procedure_input(
            self._response_command(value, actor=actor, actor_kind=actor_kind, note=note)
        )

    def _response_command(
        self,
        value: ValueT,
        *,
        actor: str,
        actor_kind: InterpretationActorKind,
        note: str,
    ) -> ProcedureStepInputSubmitCommand:
        request = self._step.interpretation_request
        assert request is not None
        return ProcedureStepInputSubmitCommand(
            procedure_run_id=self._run.procedure_run_id,
            expected_run_revision=self._run.revision,
            step_key=self._step.step_key,
            attempt=self._step.attempt,
            expected_step_revision=self._step.revision,
            request_hash=request.request_hash,
            actor=actor,
            actor_kind=actor_kind,
            value=request.normalize_answer(self._schema.encode(value)),
            note=note,
        )
