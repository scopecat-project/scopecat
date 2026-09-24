import pytest
import scopecat as sc
from pydantic import TypeAdapter
from scopecat.config.parameter_reads import compare_parameter_reads

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
from scopecat_quantum.recipe_evidence import RecipeInputEvidence
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
    builds: list[sc.Quantity] = []

    def tracked_drive(target: q.Qubit, *, duration: sc.Quantity) -> q.QuantumFragment:
        builds.append(duration)
        return drive(target, duration=duration)

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
            build=tracked_drive,
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
    compiler = RecipeTargetCompiler(
        profile, baseline, scoped_parameters={"candidate": candidate}
    )
    result = compiler.compile(
        experiment, {"target": target}, entry_id=TargetCompileEntryId("point")
    )
    assert isinstance(result.entry.program.body, ScheduledBlock)
    assert float(result.entry.program.body.program.duration_seconds) == pytest.approx(
        72e-9
    )
    gate, measurement = result.parameter_evidence
    assert gate.scope == "candidate"
    assert gate.resolution.snapshot_id == "candidate"
    assert gate.resolution.inputs == {"duration": sc.Quantity(32, "ns")}
    assert measurement.scope is None
    assert measurement.resolution.snapshot_id == "baseline"
    assert measurement.resolution.inputs == {"duration": sc.Quantity(40, "ns")}
    assert gate.resolution.sources["duration"][0].fields == {"duration": "length"}
    updated = compiler.compile(
        experiment,
        {"target": target},
        entry_id=TargetCompileEntryId("next"),
        scoped_parameters={
            "candidate": candidate.model_copy(update={"id": "next-snapshot"})
        },
    )
    assert builds == [sc.Quantity(32, "ns")]
    assert (
        updated.parameter_evidence[0].implementation_fingerprint
        == gate.implementation_fingerprint
    )
    assert updated.parameter_evidence[0].resolution.snapshot_id == "next-snapshot"
    assert (
        updated.parameter_evidence[0].resolution.sources["duration"][0].snapshot_id
        == "next-snapshot"
    )
    assert gate.resolution.snapshot_id == "candidate"
    codec = TypeAdapter(tuple[RecipeInputEvidence, ...])
    assert (
        codec.validate_json(codec.dump_json(updated.parameter_evidence))
        == updated.parameter_evidence
    )
    retained = codec.validate_json(codec.dump_json(updated.parameter_evidence))
    assert (
        compare_parameter_reads(retained[0].resolution.parameter_reads, candidate) == ()
    )
    changed = compare_parameter_reads(retained[0].resolution.parameter_reads, baseline)
    assert len(changed) == 1
    assert changed[0].reason == "cells_changed"
    assert changed[0].columns == ("length",)


def test_request_sweep_checks_actual_recipe_cells_without_building_pulses() -> None:
    from scopecat.application.request_sweeps import compose_request_sweeps

    target = sc.EntityRef(id="q0", kind="logical_qubit")
    snapshot = sc.parameter_snapshot(
        "baseline",
        tables={
            Calibration: (
                Calibration(target=target, operation="x90", length=24),
                Calibration(target=target, operation="readout", length=40),
            )
        },
    )
    builds: list[sc.Quantity] = []

    def tracked_drive(target: q.Qubit, *, duration: sc.Quantity) -> q.QuantumFragment:
        builds.append(duration)
        return drive(target, duration=duration)

    profile = PulseRecipeProfile(
        bind_gate_pulse_recipe(
            of=X90,
            build=tracked_drive,
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

    @q.program
    def measured(target: q.Qubit) -> q.QuantumFragment:
        return q.sequence(X90(target), q.measure(target, result="iq"))

    @sc.experiment
    def probe(ctx: sc.ExperimentContext) -> None:
        _ = ctx.alias(ctx.use(measured("q0").with_recipes(profile)).iq, record_id="iq")

    for operation in ("x90", "readout"):
        request = probe().sweep_parameter(
            Calibration.length, (target, operation), [24, 32], name="length"
        )
        composed = compose_request_sweeps(
            probe.build(),
            mode="cartesian",
            parameters=request.parameter_sweeps,
            snapshot=snapshot,
        )
        assert composed.point_plan.domain.axes[-1].id == "length"
    for key in ((target, "x"), (sc.EntityRef(id="q1", kind="logical_qubit"), "x90")):
        request = probe().sweep_parameter(
            Calibration.length, key, [24, 32], name="length"
        )
        with pytest.raises(ValueError, match="does not consume"):
            compose_request_sweeps(
                probe.build(),
                mode="cartesian",
                parameters=request.parameter_sweeps,
                snapshot=snapshot,
            )
    assert builds == []


class ShapeChoice(sc.ParameterModel, table="shape_choice"):
    target: sc.Param[sc.EntityRef] = sc.param(key=True, entity_kind="logical_qubit")
    shape: sc.Param[float] = sc.param()


class Shape(sc.ParameterModel, table="shape"):
    id: sc.Param[float] = sc.param(key=True)
    length: sc.Magnitude[float] = sc.quantity(unit="ns")


def test_request_sweep_rejects_a_cell_whose_lookup_key_is_also_scanned() -> None:
    from scopecat.application.request_sweeps import compose_request_sweeps

    target = sc.EntityRef(id="q0", kind="logical_qubit")
    snapshot = sc.parameter_snapshot(
        "baseline",
        tables={
            ShapeChoice: (ShapeChoice(target=target, shape=1.0),),
            Shape: (Shape(id=1.0, length=24), Shape(id=2.0, length=32)),
        },
    )
    profile = PulseRecipeProfile(
        bind_gate_pulse_recipe(
            of=X90,
            build=drive,
            inputs=recipe_parameter_inputs(
                sc.parameter_table(Shape)
                .lookup(
                    id=sc.parameter_table(ShapeChoice).lookup(target=recipe_operand())[
                        "shape"
                    ]
                )
                .select(duration="length")
            ),
        )
    )

    @q.program
    def gate(target: q.Qubit) -> q.QuantumFragment:
        return X90(target)

    @sc.experiment
    def probe(ctx: sc.ExperimentContext) -> None:
        _ = ctx.use(gate("q0").with_recipes(profile))

    request = probe().sweep_parameter(Shape.length, 1.0, [24, 32], name="length")
    _ = compose_request_sweeps(
        probe.build(),
        mode="cartesian",
        parameters=request.parameter_sweeps,
        snapshot=snapshot,
    )
    changed_key = request.sweep_parameter(
        ShapeChoice.shape, target, [1.0, 2.0], name="shape"
    )
    with pytest.raises(ValueError, match="does not consume"):
        compose_request_sweeps(
            probe.build(),
            mode="cartesian",
            parameters=changed_key.parameter_sweeps,
            snapshot=snapshot,
        )
    # The same restriction applies to an existing authored overlay, not only
    # another request-level sweep. Reading the selector itself is supported.
    authored = probe.build().with_axis(
        sc.axis(
            sc.coordinate("shape", sc.FloatType()),
            [1.0, 2.0],
            overlay=sc.parameter_ref(ShapeChoice.shape, target),
        )
    )
    with pytest.raises(ValueError, match="does not consume"):
        compose_request_sweeps(
            authored,
            mode="cartesian",
            parameters=request.parameter_sweeps,
            snapshot=snapshot,
        )
    selector = probe().sweep_parameter(
        ShapeChoice.shape, target, [1.0, 2.0], name="shape"
    )
    _ = compose_request_sweeps(
        probe.build(),
        mode="cartesian",
        parameters=selector.parameter_sweeps,
        snapshot=snapshot,
    )


@pytest.mark.parametrize("mode", ["scoped", "zero", "opaque"])
def test_request_sweep_does_not_infer_unknown_or_shadowed_gate_reads(mode: str) -> None:
    from scopecat.application.request_sweeps import compose_request_sweeps

    target = sc.EntityRef(id="q0", kind="logical_qubit")
    snapshot = sc.parameter_snapshot(
        "base",
        tables={
            Calibration: (
                Calibration(target=target, operation="x90", length=24),
                Calibration(target=target, operation="readout", length=40),
            )
        },
    )
    profile = PulseRecipeProfile(
        bind_gate_pulse_recipe(
            of=X90,
            build=drive,
            inputs=(
                (lambda _snapshot, _call: {"duration": sc.Quantity(24, "ns")})
                if mode == "opaque"
                else recipe_parameter_inputs(
                    sc.parameter_table(Calibration)
                    .lookup(target=recipe_operand(), operation=recipe_operation())
                    .select(duration="length")
                )
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

    @q.program
    def selected(target: q.Qubit) -> q.QuantumFragment:
        if mode == "scoped":
            return q.recipe_scope(
                "candidate", q.sequence(X90(target), q.measure(target, result="iq"))
            )
        return q.sequence(
            q.repeat(X90(target), 0) if mode == "zero" else X90(target),
            q.measure(target, result="iq"),
        )

    @sc.experiment
    def probe(ctx: sc.ExperimentContext) -> None:
        _ = ctx.use(selected("q0").with_recipes(profile))

    gate_request = probe().sweep_parameter(
        Calibration.length, (target, "x90"), [24, 32], name="length"
    )
    with pytest.raises(ValueError, match="does not consume"):
        compose_request_sweeps(
            probe.build(),
            mode="cartesian",
            parameters=gate_request.parameter_sweeps,
            snapshot=snapshot,
        )
    readout_request = probe().sweep_parameter(
        Calibration.length, (target, "readout"), [32, 40], name="length"
    )
    _ = compose_request_sweeps(
        probe.build(),
        mode="cartesian",
        parameters=readout_request.parameter_sweeps,
        snapshot=snapshot,
    )
