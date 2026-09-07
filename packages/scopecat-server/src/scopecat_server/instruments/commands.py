"""Execute one validated driver command and synchronize its physical state."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from scopecat.kernel.instrument_members import StateMemberRef
from scopecat.kernel.problems import Problem, ProblemPhase
from scopecat.records.instrument import (
    InstrumentStateCacheReadback,
    InstrumentStateReadback,
    InstrumentStateSnapshot,
    state_member_ref,
    state_member_target,
)
from scopecat.sdk.instruments.backend import (
    BackendAcquisitionPlan,
    BackendApplyRequest,
    BackendCollectRequest,
    BackendInvokeRequest,
    BackendReadRequest,
)
from scopecat.sdk.instruments.commands import (
    AcquisitionPreparationReceipt,
    ApplyReceipt,
    CollectCommand,
    CollectReceipt,
    InstrumentStateAssignment,
    InvokeReceipt,
)
from scopecat.sdk.instruments.contracts import (
    capture_state_members,
    operation_invalidated_state_members,
    state_assignment_satisfied,
    validate_collect_receipt,
    validate_state_capture,
    validate_state_snapshot,
)

from ..errors import BackendConflict
from .actors import OwnedInstrument
from .backend import InstrumentBackendError

type InstrumentCommandFailureReason = Literal[
    "instrument_acquisition_prepare_unknown",
    "instrument_apply_unknown",
    "instrument_apply_state_unknown",
    "instrument_invoke_unknown",
    "instrument_invoke_state_unknown",
    "instrument_invoke_state_mismatch",
    "instrument_collect_unknown",
    "instrument_collect_receipt_invalid",
    "instrument_collect_rejection_state_unknown",
]


def _worker_error_problems(error: Exception) -> tuple[Problem, ...]:
    if not isinstance(error, InstrumentBackendError):
        return ()
    if error.problems:
        return error.problems
    return (
        Problem(
            code="instrument_worker_operation_failed",
            phase=ProblemPhase.EXECUTION,
            message=str(error)[:512],
            details={}
            if error.diagnostic is None
            else {"worker_diagnostic": error.diagnostic},
        ),
    )


def execute_instrument_acquisition_prepare(
    instrument: OwnedInstrument,
    plan: BackendAcquisitionPlan,
) -> AcquisitionPreparationReceipt:
    try:
        return instrument.prepare_acquisitions(plan)
    except Exception as error:
        raise InstrumentCommandExecutionError(
            "instrument_acquisition_prepare_unknown",
            "instrument acquisition preparation failed with unknown state",
            problems=_worker_error_problems(error),
        ) from error


class InstrumentCommandExecutionError(RuntimeError):
    def __init__(
        self,
        reason: InstrumentCommandFailureReason,
        message: str,
        *,
        problems: Sequence[Problem] = (),
    ) -> None:
        self.reason = reason
        self.problems = tuple(problems)
        super().__init__(message)


def execute_instrument_apply(
    instrument: OwnedInstrument,
    request: BackendApplyRequest,
    *,
    assignments: Sequence[InstrumentStateAssignment],
) -> ApplyReceipt:
    try:
        receipt = instrument.apply_state(request)
    except Exception as error:
        raise InstrumentCommandExecutionError(
            "instrument_apply_unknown",
            "instrument apply failed with unknown state",
            problems=_worker_error_problems(error),
        ) from error
    if receipt.status != "applied":
        return receipt
    try:
        _confirm_applied_state(instrument, receipt, assignments)
    except BackendConflict as error:
        raise InstrumentCommandExecutionError(
            "instrument_apply_state_unknown",
            f"instrument apply completed but {error}",
        ) from error
    return receipt


def execute_instrument_invoke(
    instrument: OwnedInstrument,
    request: BackendInvokeRequest,
) -> InvokeReceipt:
    try:
        receipt = instrument.invoke(request)
    except Exception as error:
        raise InstrumentCommandExecutionError(
            "instrument_invoke_unknown",
            "instrument invoke failed with unknown state",
            problems=_worker_error_problems(error),
        ) from error
    if receipt.status != "invoked":
        return receipt
    try:
        if receipt.readback is not None:
            adopt_instrument_readback(instrument, receipt.readback)
        else:
            invalidated = operation_invalidated_state_members(
                instrument.description,
                interface_id=request.interface_id,
                component_path=request.component_path,
                operation_id=request.operation_id,
            )
            if invalidated:
                observe_members(instrument, invalidated)
    except BackendConflict as error:
        raise InstrumentCommandExecutionError(
            "instrument_invoke_state_unknown",
            "instrument invoke completed but state synchronization failed",
        ) from error
    return receipt


def execute_instrument_collect(
    instrument: OwnedInstrument,
    request: BackendCollectRequest,
    *,
    command: CollectCommand,
) -> CollectReceipt:
    try:
        receipt = instrument.collect(request)
    except Exception as error:
        raise InstrumentCommandExecutionError(
            "instrument_collect_unknown",
            "instrument collect failed with unknown state",
            problems=_worker_error_problems(error),
        ) from error
    problems = validate_collect_receipt(
        command=command,
        receipt=receipt,
    )
    if problems:
        raise InstrumentCommandExecutionError(
            "instrument_collect_receipt_invalid",
            "; ".join(item.message for item in problems),
            problems=problems,
        )
    if receipt.status == "not_collected":
        try:
            instrument.adopt_state(observe_instrument(instrument))
        except BackendConflict as error:
            raise InstrumentCommandExecutionError(
                "instrument_collect_rejection_state_unknown",
                "instrument rejected collection and state synchronization failed",
            ) from error
    return receipt


def observe_instrument(
    instrument: OwnedInstrument,
) -> InstrumentStateSnapshot:
    """Refresh the explicit lifecycle capture plan and return its projection."""

    targets = capture_state_members(instrument.description)
    observe_members(instrument, targets)
    state = instrument.assumed_state
    assert state is not None
    problems = validate_state_capture(
        snapshot=state,
        description=instrument.description,
        required=targets,
    )
    if problems:
        raise BackendConflict("; ".join(item.message for item in problems))
    return state


def observe_members(
    instrument: OwnedInstrument,
    targets: Sequence[StateMemberRef],
) -> InstrumentStateReadback:
    selected = tuple(targets)
    if not selected:
        readback = InstrumentStateReadback(instrument_id=instrument.instrument_id)
        instrument.adopt_readback(readback)
        return readback
    try:
        request = BackendReadRequest(
            targets=tuple(state_member_target(target) for target in selected)
        )
        readback = instrument.read_state(request)
    except Exception as error:
        raise BackendConflict("instrument state read failed") from error
    requested = {state_member_ref(target) for target in request.targets}
    snapshot = InstrumentStateSnapshot(
        instrument_id=readback.instrument_id,
        observations=[item.model_copy(deep=True) for item in readback.observations],
    )
    problems = validate_state_capture(
        snapshot=snapshot,
        description=instrument.description,
        required=tuple(requested),
    )
    if problems:
        raise BackendConflict("; ".join(item.message for item in problems))
    instrument.adopt_readback(readback)
    return readback


def observed_members(
    instrument: OwnedInstrument,
    targets: Sequence[StateMemberRef],
) -> InstrumentStateCacheReadback:
    """Project exact members from the current actor cache without hardware I/O."""

    return instrument.state_cache(state_member_target(target) for target in targets)


def adopt_instrument_readback(
    instrument: OwnedInstrument,
    readback: InstrumentStateReadback,
) -> None:
    snapshot = InstrumentStateSnapshot(
        instrument_id=readback.instrument_id,
        observations=[item.model_copy(deep=True) for item in readback.observations],
    )
    problems = validate_state_snapshot(
        snapshot=snapshot,
        description=instrument.description,
    )
    if problems:
        raise BackendConflict("; ".join(item.message for item in problems))
    instrument.adopt_readback(readback)


def _confirm_applied_state(
    instrument: OwnedInstrument,
    receipt: ApplyReceipt,
    assignments: Sequence[InstrumentStateAssignment],
) -> None:
    if receipt.readback is None:
        observe_members(
            instrument,
            tuple(state_member_ref(assignment.target) for assignment in assignments),
        )
    else:
        adopt_instrument_readback(instrument, receipt.readback)
    state = instrument.assumed_state
    if state is None:
        raise BackendConflict("instrument apply produced no synchronized state")
    if not all(
        state_assignment_satisfied(state, assignment) for assignment in assignments
    ):
        raise BackendConflict("instrument apply readback did not match requested state")
