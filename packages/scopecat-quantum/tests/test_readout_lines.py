"""Unassigned readout paths remain distinct from logical sample objects."""

import pytest
from scopecat import EntityRef, Quantity

from scopecat_quantum import authoring as q
from scopecat_quantum._ids import PulseProgramId, QubitId, ReadoutLineId
from scopecat_quantum.programs import (
    materialize_quantum_pulse_program,
    plan_quantum_pulse_lowering,
)
from scopecat_quantum.pulse_implementations import ResolvedPulseImplementations
from scopecat_quantum.pulses import AcquireSignal, ReadoutSignal, schedule


@q.program
def survey(line: q.ReadoutLine):
    return q.parallel(
        q.play(
            q.readout(line),
            q.constant(duration=Quantity(16, "ns"), amplitude=Quantity(0.1, "arb")),
        ),
        q.acquire(line, duration=Quantity(16, "ns"), result="iq"),
    )


def test_readout_line_binds_and_schedules_without_qubits():
    bound = q.bind(survey, {"line": EntityRef(id="feedline", kind="readout_line")})
    plan = plan_quantum_pulse_lowering(
        bound.verified,
        ResolvedPulseImplementations(),
        output_id=PulseProgramId("survey"),
    )
    scheduled = schedule(materialize_quantum_pulse_program(plan))
    assert {event.instruction.signal for event in scheduled.events} == {
        ReadoutSignal(ReadoutLineId("feedline")),
        AcquireSignal(ReadoutLineId("feedline")),
    }
    assert survey.results[0].owner.id == "line"
    assert ReadoutSignal(ReadoutLineId("feedline")) != ReadoutSignal(
        QubitId("feedline")
    )


def test_readout_line_rejects_logical_qubit_binding():
    with pytest.raises(ValueError, match="readout_line"):
        q.bind(survey, {"line": EntityRef(id="q0", kind="logical_qubit")})
