from __future__ import annotations

import pytest
from scopecat import Quantity
from scopecat_quantum._ids import (
    AcquisitionSlotId,
    PulseEventId,
    PulseProgramId,
    TargetCompileEntryId,
    TargetCompilerId,
)
from scopecat_quantum.acquisitions import CLASSIFIED_STATE_RESULT
from scopecat_quantum.pulses import (
    Acquire,
    AcquisitionSlot,
    Constant,
    Play,
    PulseProgram,
    schedule,
)
from scopecat_quantum.realtime import (
    ClassifiedStatePredicate,
    RealtimeCase,
    RealtimeConditional,
    RealtimeNoOp,
    RealtimeSequence,
    ScheduledBlock,
    TargetProgram,
)
from scopecat_quantum.targets import (
    TargetCompilationError,
    TargetCompilationIssueDimension,
    TargetCompileEntry,
    TargetCompileRequest,
)

from ._list_mode_test_support import ACQUIRE_Q0, DRIVE_Q0, ListModeTargetCompiler
from ._list_mode_test_support import _target as target_fixture


def test_list_mode_target_explicitly_rejects_realtime_control() -> None:
    slot = AcquisitionSlot(
        AcquisitionSlotId("state"),
        CLASSIFIED_STATE_RESULT,
        ACQUIRE_Q0,
    )
    measurement = schedule(
        PulseProgram(
            PulseProgramId("classify"),
            Acquire(
                PulseEventId("capture"),
                ACQUIRE_Q0,
                slot.id,
                Quantity(8, "ns"),
            ),
            acquisition_slots=(slot,),
        )
    )
    correction = schedule(
        PulseProgram(
            PulseProgramId("conditional-x"),
            Play(
                PulseEventId("x"),
                DRIVE_Q0,
                Constant(Quantity(4, "ns"), Quantity(0.25, "arb")),
            ),
        )
    )
    program = TargetProgram(
        PulseProgramId("active-reset"),
        RealtimeSequence(
            (
                ScheduledBlock(measurement),
                RealtimeConditional(
                    ClassifiedStatePredicate(slot.id),
                    cases=(RealtimeCase(1, ScheduledBlock(correction)),),
                    default=RealtimeNoOp(),
                ),
            )
        ),
    )
    request = TargetCompileRequest(
        entries=(TargetCompileEntry(TargetCompileEntryId("point-0"), program),),
        repetitions=1,
    )
    compiler = ListModeTargetCompiler(
        TargetCompilerId("list-mode-no-feedback.v1"),
        target_fixture(),
    )

    with pytest.raises(TargetCompilationError) as exc_info:
        compiler.compile(request)

    assert program.envelope.has_variable_duration
    assert len(exc_info.value.issues) == 1
    [issue] = exc_info.value.issues
    assert issue.dimension is TargetCompilationIssueDimension.CAPABILITY
    assert issue.code == "list_mode_realtime_control_unsupported"
    assert issue.entry_id == TargetCompileEntryId("point-0")
