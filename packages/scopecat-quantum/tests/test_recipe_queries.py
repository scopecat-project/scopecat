import pytest
import scopecat as sc

from scopecat_quantum import authoring as q
from scopecat_quantum._ids import TargetCompileEntryId
from scopecat_quantum.acquisitions import AcquisitionKind
from scopecat_quantum.compilation import RecipeTargetCompiler
from scopecat_quantum.pulse_recipes import PulseRecipeProfile
from scopecat_quantum.realtime import ScheduledBlock
from scopecat_quantum.recipe_bindings import (
    bind_gate_pulse_recipe,
    bind_measurement_pulse_recipe,
)
from scopecat_quantum.recipe_queries import (
    recipe_operand,
    recipe_operation,
    recipe_parameter_inputs,
)
from scopecat_quantum.standard_gates import X90


class Calibration(sc.ParameterModel, table="calibration"):
    target: sc.Param[sc.EntityRef] = sc.param(key=True, entity_kind="logical_qubit")
    operation: sc.Param[str] = sc.param(key=True)
    length: sc.Magnitude[float] = sc.quantity(unit="ns")


def drive(target: q.Qubit, *, duration: sc.Quantity) -> q.QuantumFragment:
    return q.play(
        q.drive(target),
        q.constant(duration=duration, amplitude=sc.Quantity(0.1, "arb")),
    )


def readout(target: q.Qubit, *, duration: sc.Quantity) -> q.QuantumFragment:
    return q.parallel(
        q.play(
            q.readout(target),
            q.constant(duration=duration, amplitude=sc.Quantity(0.1, "arb")),
        ),
        q.acquire(target, duration=duration, result="iq"),
    )


@q.program
def experiment(target: q.Qubit) -> q.QuantumFragment:
    return q.recipe_scope(
        "candidate", q.sequence(X90(target), q.measure(target, result="iq"))
    )


def test_declarative_inputs_resolve_operation_context_and_scope() -> None:
    target = sc.EntityRef(id="q0", kind="logical_qubit")
    baseline = sc.parameter_snapshot(
        "baseline",
        tables={
            Calibration: (
                Calibration(target=target, operation="x90", length=24),
                Calibration(target=target, operation="readout", length=40),
            )
        },
    )
    candidate = sc.parameter_snapshot(
        "candidate",
        tables={
            Calibration: (
                Calibration(target=target, operation="x90", length=32),
                Calibration(target=target, operation="readout", length=100),
            )
        },
    )
    profile = PulseRecipeProfile(
        bind_gate_pulse_recipe(
            of=X90,
            build=drive,
            inputs=recipe_parameter_inputs(
                sc.parameter_table(Calibration)
                .lookup(target=recipe_operand(), operation=recipe_operation())
                .select(duration="length")
            ),
        ),
        bind_measurement_pulse_recipe(
            kind=AcquisitionKind.INTEGRATED_IQ,
            build=readout,
            inputs=recipe_parameter_inputs(
                sc.parameter_table(Calibration)
                .lookup(target=recipe_operand(), operation="readout")
                .select(duration="length")
            ),
        ),
    )
    result = RecipeTargetCompiler(
        profile, baseline, scoped_parameters={"candidate": candidate}
    ).compile(experiment, {"target": target}, entry_id=TargetCompileEntryId("point"))
    assert isinstance(result.entry.program.body, ScheduledBlock)
    assert float(result.entry.program.body.program.duration_seconds) == pytest.approx(
        72e-9
    )
