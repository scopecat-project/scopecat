"""Direct callable computations keep construction separate from native execution."""

from typing import Annotated, assert_type

import pytest

import scopecat as sc
from scopecat.compiler.frontend.resolution import compile_invocation


@sc.compute
def offset(value: complex, *, correction: complex = 1j) -> complex:
    return value + correction


@sc.compute
def declared_unit(
    value: complex,
) -> Annotated[complex, sc.ScalarType(sc.ComplexType(unit="V"))]:
    return value


def test_direct_calls_build_dependencies_and_keep_native_test_entry() -> None:
    assert_type(offset.eager(1 + 2j), complex)
    assert offset.eager(1 + 2j) == 1 + 3j

    @sc.experiment(id="test.direct-compute")
    def experiment(ctx: sc.ExperimentContext) -> sc.DataRef[complex]:
        first = assert_type(offset(1 + 2j), sc.DataRef[complex])
        return offset(first, correction=2j)

    built = experiment.build()
    assert isinstance(built.output, sc.ValueRef)
    logical = compile_invocation(built)
    assert logical is not None
    with pytest.raises(RuntimeError, match=r"use \.eager"):
        offset(1 + 2j)


def test_measured_direct_call_preserves_units_and_context_after_nested_module() -> None:
    @sc.module(id="test.inner-compute")
    def inner(ctx: sc.ModuleContext) -> sc.DataRef[complex]:
        return declared_unit(2j)

    @sc.experiment(id="test.outer-compute")
    def outer(ctx: sc.ExperimentContext) -> sc.DataRef[complex]:
        # Construct another module while the experiment recorder is active.
        @sc.module(id="test.nested-compute")
        def nested(module: sc.ModuleContext) -> sc.DataRef[complex]:
            return declared_unit(1j)

        ctx.use(nested())
        ctx.use(inner())
        source = ctx._product("source", dtype="complex128", unit="V")
        return declared_unit(source)

    output = outer.build().output
    assert isinstance(output, sc.ProductRef)
    assert output.value_spec.unit == "V"
    assert output.value_spec.dtype == "complex128"


def test_failed_build_resets_context_and_bad_calls_name_the_argument() -> None:
    @sc.experiment(id="test.failed-compute")
    def invalid(ctx: sc.ExperimentContext) -> sc.DataRef[complex]:
        return offset(other=1)

    with pytest.raises(TypeError, match="value"):
        invalid.build()
    with pytest.raises(RuntimeError, match="inside an experiment"):
        offset(1j)


def test_variadic_functions_fail_before_building() -> None:
    def variadic(*values: complex) -> complex:
        return sum(values)

    with pytest.raises(TypeError, match="parameter 'values'"):
        sc.compute(variadic)


def test_dictionary_outputs_compose_and_keep_named_paths() -> None:
    @sc.module(id="dict.module")
    def inner(ctx: sc.ModuleContext) -> dict[str, sc.DataRef[complex]]:
        return {"signal": offset(2j)}

    @sc.experiment(id="dict.experiment")
    def experiment(ctx: sc.ExperimentContext) -> dict[str, object]:
        return {"nested": ctx.use(inner()), "other": offset(1j)}

    built = experiment.build()
    assert {field.path for field in built.definition.result_fields} == {
        ("nested", "signal"),
        ("other",),
    }
    assert compile_invocation(built) is not None


@pytest.mark.parametrize("key", ["", "nested/signal", 1])
def test_dictionary_output_rejects_ambiguous_field_names(key: object) -> None:
    @sc.experiment(id="dict.invalid")
    def experiment(ctx: sc.ExperimentContext) -> dict[object, object]:
        return {key: offset(1j)}

    with pytest.raises(TypeError, match="string keys"):
        experiment.build()


def test_output_schema_factory_preserves_structural_shape_and_native_call() -> None:
    import numpy as np
    from numpy.typing import NDArray

    def shape(shots: int) -> sc.ArrayType:
        return sc.ArrayType(
            dtype="complex128", dimensions=(sc.ArrayDimension("shot", shots),)
        )

    @sc.compute(output_type=shape)
    def samples(shots: int = 2) -> NDArray[np.complex128]:
        return np.ones(shots, dtype=np.complex128)

    assert_type(samples.eager(3), NDArray[np.complex128])
    assert samples.eager(3).shape == (3,)

    @sc.experiment(id="schema.factory")
    def experiment(
        ctx: sc.ExperimentContext,
    ) -> dict[str, sc.DataRef[NDArray[np.complex128]]]:
        return {"two": samples(), "three": samples(3)}

    built = experiment.build()
    two, three = built.output["two"], built.output["three"]
    assert isinstance(two, sc.ValueRef) and isinstance(three, sc.ValueRef)
    assert isinstance(two.value_type, sc.ArrayType) and isinstance(
        three.value_type, sc.ArrayType
    )
    assert two.value_type.dimensions[0].size == 2
    assert three.value_type.dimensions[0].size == 3
    assert compile_invocation(built) is not None

    def invalid_shape(unknown: int) -> sc.ArrayType:
        return shape(unknown)

    with pytest.raises(TypeError, match="unknown"):
        sc.compute(samples.eager, output_type=invalid_shape)


@sc.compute
def lorentzian(frequency: sc.Quantity, center: sc.Quantity) -> float:
    delta = frequency.to("MHz").value - center.to("MHz").value
    return 1 / (1 + delta**2)


@pytest.mark.parametrize("value", ["wrong", 5.0])
def test_quantity_parameter_rejects_wrong_literals_during_build(
    value: str | float,
) -> None:
    @sc.experiment(id="test.invalid-frequency")
    def experiment(ctx: sc.ExperimentContext) -> sc.DataRef[float]:
        return lorentzian(value, center=sc.Quantity(5, "MHz"))

    with pytest.raises(
        TypeError, match="compute 'lorentzian' input 'frequency' expects Quantity"
    ):
        experiment.build()


def test_quantity_parameter_accepts_input_value_and_measured_references() -> None:
    @sc.experiment(id="test.quantity-references")
    def experiment(
        ctx: sc.ExperimentContext,
        frequency: Annotated[sc.Input[sc.Quantity], sc.QuantityType(unit="MHz")],
    ) -> dict[str, sc.DataRef[float]]:
        center = sc.Quantity(5000, "kHz")
        value = ctx.compute(
            fn=lambda: center, output_type=sc.ScalarType(sc.QuantityType(unit="kHz"))
        )
        measured = ctx._product("frequency", dtype="float64", unit="MHz")
        return {
            "input": lorentzian(frequency, center),
            "value": lorentzian(value, center),
            "measured": lorentzian(measured, center),
        }

    assert (
        compile_invocation(experiment.build(frequency=sc.Quantity(5, "MHz")))
        is not None
    )


@pytest.mark.parametrize("measured", [False, True])
def test_quantity_parameter_rejects_incompatible_reference_schema(
    measured: bool,
) -> None:
    @sc.experiment(id="test.invalid-frequency-reference")
    def experiment(ctx: sc.ExperimentContext) -> sc.DataRef[float]:
        value = (
            ctx._product("frequency", dtype="float64", unit=None)
            if measured
            else ctx.compute(
                fn=lambda: "wrong", output_type=sc.ScalarType(sc.StringType())
            )
        )
        return lorentzian(value, sc.Quantity(5, "MHz"))

    with pytest.raises(TypeError, match="input 'frequency' expects Quantity"):
        experiment.build()


@pytest.mark.parametrize("unit, succeeds", [("kHz", True), ("ns", False)])
def test_plain_quantity_keeps_conversion_at_native_execution(
    unit: str, succeeds: bool
) -> None:
    from scopecat.compiler.frontend.elaboration import compose_module
    from scopecat.compiler.frontend.logical_verification import verify_logical_program
    from scopecat.compiler.relations.context import EvalContext
    from scopecat.compiler.value_resolution import logical_program_value
    from scopecat.execution.effects.boundary import EffectBoundary
    from scopecat.execution.effects.compute import (
        ComputeEffectExecutor,
        EffectEvaluationFrame,
    )
    from scopecat.kernel.problems import Problem
    from scopecat.planning.local_compute import bind_compute_operations
    from scopecat.sdk.payloads import EMPTY_PAYLOAD_CODECS

    @sc.module(id="test.quantity-execution")
    def module(ctx: sc.ModuleContext) -> sc.DataRef[float]:
        return lorentzian(sc.Quantity(5000, unit), sc.Quantity(5, "MHz"))

    logical = compose_module(module.definition)
    verified = verify_logical_program(logical)
    [compute] = logical.compute_nodes
    problems: list[Problem] = []
    operations, _ = bind_compute_operations(
        logical.compute_nodes,
        logical.implementations,
        {
            value_id: logical_program_value(verified, value_id)
            for _, value_id in compute.inputs
        },
        operation_prefix="quantity",
        ctx=EvalContext(),
        demanded_payload_results=frozenset(),
        problems=problems,
    )
    assert problems == []
    boundary = EffectBoundary(run_id="quantity")
    frame = EffectEvaluationFrame()
    ComputeEffectExecutor(
        boundary=boundary, payload_codecs=EMPTY_PAYLOAD_CODECS
    ).execute(frame, operations)
    if succeeds:
        assert boundary.problems == []
        assert frame.compute_results[compute.result_id] == 1.0
    else:
        assert [problem.code for problem in boundary.problems] == [
            "compute_operation_failed"
        ]
        assert frame.compute_results == {}


type FrequencyQuantity = sc.Quantity


@pytest.mark.parametrize(
    "annotation", [FrequencyQuantity, Annotated[FrequencyQuantity, "frequency"]]
)
def test_quantity_alias_contract_also_applies_to_explicit_compute(
    annotation: object,
) -> None:
    def response(frequency: sc.Quantity) -> float:
        return frequency.to("MHz").value

    response.__annotations__["frequency"] = annotation
    with pytest.raises(
        TypeError, match="compute 'response' input 'frequency' expects Quantity"
    ):
        sc.ModuleContext().compute(fn=response, frequency="wrong")


def test_quantity_keyword_and_default_arguments_are_checked() -> None:
    default_frequency = sc.Quantity(5, "MHz")

    @sc.compute
    def response(*, frequency: sc.Quantity = default_frequency) -> float:
        return frequency.to("MHz").value

    @sc.experiment(id="test.quantity-default")
    def valid(ctx: sc.ExperimentContext) -> sc.DataRef[float]:
        return response()

    assert compile_invocation(valid.build()) is not None

    @sc.experiment(id="test.quantity-keyword")
    def invalid(ctx: sc.ExperimentContext) -> sc.DataRef[float]:
        return response(frequency="wrong")

    with pytest.raises(
        TypeError, match="compute 'response' input 'frequency' expects Quantity"
    ):
        invalid.build()


@pytest.mark.parametrize("unit", ["kHz", "ns"])
def test_measured_quantity_executes_without_changing_float_consumers(unit: str) -> None:
    from scopecat.compiler.frontend.elaboration import compose_module
    from scopecat.measurements.results import MeasurementScalar

    @sc.compute
    def magnitude(frequency: float) -> float:
        assert isinstance(frequency, float)
        return frequency

    @sc.module(id="test.measured-quantity-native")
    def module(ctx: sc.ModuleContext) -> dict[str, sc.DataRef[float]]:
        measured = ctx._product("frequency", dtype="float64", unit=unit)
        return {
            "quantity": lorentzian(measured, sc.Quantity(5, "MHz")),
            "magnitude": magnitude(measured),
        }

    quantity_compute, magnitude_compute = compose_module(
        module.definition
    ).measurement_computes
    measured_value = MeasurementScalar.create(value=5000.0, dtype="float64", unit=unit)
    magnitude_result = magnitude_compute.kernel({"frequency": measured_value})
    assert magnitude_result["result"] == MeasurementScalar.create(value=5000.0)
    inputs = {"frequency": measured_value, "center": sc.Quantity(5, "MHz")}
    if unit == "kHz":
        result = quantity_compute.kernel(inputs)
        assert result["result"] == MeasurementScalar.create(value=1.0)
    else:
        # The measurement wrapper preserves the source unit; the function's
        # existing native conversion rejects a time quantity as a frequency.
        with pytest.raises(ValueError, match="convert"):
            quantity_compute.kernel(inputs)
