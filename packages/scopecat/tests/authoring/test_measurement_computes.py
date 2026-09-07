# pyright: reportUnusedFunction=false

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, assert_type

import numpy as np
import pytest
from numpy.typing import NDArray
from scopecat_testkit.domain import domain_call

import scopecat as sc
from scopecat.compiler.frontend.elaboration import compose_module
from scopecat.compiler.frontend.logical_verification import verify_logical_program
from scopecat.kernel.errors import CheckFailed
from scopecat.measurements.results import (
    MeasurementArray,
    MeasurementScalar,
    MeasurementValue,
)
from scopecat.program.domain import domain_program
from scopecat.program.products import (
    ProductAxis,
    entity_axis,
    product_axis_dimension_id,
    shot_axis,
)


@dataclass(frozen=True, slots=True)
class _ProbabilityProducts(sc.ProductBundle):
    positive: Annotated[
        sc.ProductRef[float],
        sc.ScalarType(sc.QuantityType(unit="ratio")),
    ]
    negative: Annotated[
        sc.ProductRef[float],
        sc.ScalarType(sc.QuantityType(unit="ratio")),
    ]


def _identity(value: MeasurementValue) -> dict[str, MeasurementValue]:
    return {"result": value}


def test_module_requires_compute_products_and_unique_ids() -> None:
    foreign = sc.ModuleContext()._product("raw")

    with pytest.raises(ValueError, match="outside this module"):

        @sc.module(id="test.compute.missing")
        def missing(context: sc.ModuleContext) -> None:
            derived = context._product("derived")
            context._measurement_compute(
                "derive",
                input=foreign,
                outputs={"result": derived},
                kernel=_identity,
            )

    with pytest.raises(
        ValueError,
        match="duplicate module measurement compute ids",
    ):

        @sc.module(id="test.compute.duplicate")
        def duplicate(context: sc.ModuleContext) -> None:
            raw = context._product("raw")
            derived = context._product("derived")
            context._measurement_compute(
                "derive",
                input=raw,
                outputs={"result": derived},
                kernel=_identity,
            )
            context._measurement_compute(
                "derive",
                input=raw,
                outputs={"result": derived},
                kernel=_identity,
            )


def test_compute_reads_child_product_and_is_hygienically_scoped() -> None:
    @sc.module(id="test.compute.source")
    def child(context: sc.ModuleContext) -> sc.ProductRef:
        return context._product("raw")

    nested = child.instantiate("nested")

    @sc.module(id="test.compute.parent")
    def module(context: sc.ModuleContext) -> None:
        context.use(nested)
        derived = context._product("derived")
        context._measurement_compute(
            "derive",
            input=nested.result,
            outputs={"result": derived},
            kernel=_identity,
        )

    [lowered] = compose_module(module.definition).measurement_computes
    assert lowered.inputs[0][1].qualified_name == "nested/raw"
    assert lowered.outputs[0][1].qualified_name == "derived"

    @sc.module(id="test.compute.child")
    def nested_module(context: sc.ModuleContext) -> None:
        raw = context._product("raw")
        derived = context._product("derived")
        context._measurement_compute(
            "derive",
            input=raw,
            outputs={"result": derived},
            kernel=_identity,
        )

    @sc.module(id="test.compute.root")
    def root(context: sc.ModuleContext) -> None:
        context.use(nested_module.instantiate("nested"))

    [scoped] = compose_module(root.definition).measurement_computes
    assert scoped.id.qualified_name == "nested/derive"
    assert scoped.inputs[0][1].qualified_name == "nested/raw"


def test_compute_lowers_multiple_measured_inputs_to_the_observation_stage() -> None:
    def pair(*, left: float, right: float) -> NDArray[np.float64]:
        return np.asarray([left, right])

    @sc.module(id="test.measurement-compute")
    def module(context: sc.ModuleContext) -> sc.ProductRef:
        left = context._product("left", unit="V")
        right = context._product("right", unit="V")
        return context.compute(
            "pair",
            fn=pair,
            inputs={"left": left, "right": right},
            output_type=sc.ArrayType(
                dtype="float64",
                dimensions=(sc.ArrayDimension("channel", 2),),
                unit="V",
            ),
        )

    logical = compose_module(module.definition)

    [compute] = logical.measurement_computes
    assert [(name, product.qualified_name) for name, product in compute.inputs] == [
        ("left", "left"),
        ("right", "right"),
    ]
    output = compute.kernel(
        {
            "left": MeasurementScalar.create(
                value=1.0,
                dtype="float64",
                unit="V",
            ),
            "right": MeasurementScalar.create(
                value=2.0,
                dtype="float64",
                unit="V",
            ),
        }
    )["result"]
    assert isinstance(output, MeasurementArray)
    assert output.values.tolist() == [1.0, 2.0]
    assert output.unit == "V"


def test_compute_can_preserve_semantic_axes_from_a_measured_input() -> None:
    entities = (
        sc.EntityRef(id="q0", kind="logical_qubit"),
        sc.EntityRef(id="q1", kind="logical_qubit"),
    )

    def classify(*, values: object) -> NDArray[np.bool_]:
        array = np.asarray(values, dtype=np.complex128)
        return np.asarray(array.real >= 0.0, dtype=np.bool_)

    @sc.module(id="test.measurement-compute-axes")
    def module(context: sc.ModuleContext) -> sc.ProductRef:
        source = context._product(
            "iq_shots",
            dtype="complex128",
            axes=(
                entity_axis("entity", entities, shared_as="targets"),
                shot_axis(4, shared_as="shot"),
            ),
        )
        return context.compute(
            "bit_shots",
            fn=classify,
            inputs={"values": source},
            axes_from=source,
            output_type=sc.ArrayType(
                dtype="bool",
                dimensions=(
                    sc.ArrayDimension("entity", 2),
                    sc.ArrayDimension("shot", 4),
                ),
            ),
        )

    source, derived = compose_module(module.definition).product_declarations

    assert [axis.id for axis in derived.value_spec.axes] == ["entity", "shot"]
    assert [
        product_axis_dimension_id(derived, axis) for axis in derived.value_spec.axes
    ] == [product_axis_dimension_id(source, axis) for axis in source.value_spec.axes]
    assert derived.value_spec.axes[0].entity_values
    assert derived.value_spec.axes[0].shared_as == "targets"


def test_compute_preserves_axes_from_a_nested_product_scope() -> None:
    def classify(*, values: object) -> NDArray[np.bool_]:
        array = np.asarray(values, dtype=np.complex128)
        return np.asarray(array.real >= 0.0, dtype=np.bool_)

    @sc.module(id="test.measurement-compute-nested-axis-source")
    def source_module(context: sc.ModuleContext) -> sc.ProductRef:
        return context._product(
            "iq_shots",
            dtype="complex128",
            axes=(shot_axis(4, shared_as="shot"),),
        )

    source_call = source_module.instantiate("source")

    @sc.module(id="test.measurement-compute-nested-axes")
    def module(context: sc.ModuleContext) -> sc.ProductRef:
        context.use(source_call)
        result = context.compute(
            "bit_shots",
            fn=classify,
            values=source_call.result,
            axes_from=source_call.result,
            output_type=sc.ArrayType(
                dtype="bool",
                dimensions=(sc.ArrayDimension("shot", 4),),
            ),
        )
        assert isinstance(result, sc.ProductRef)
        return result

    source, derived = compose_module(module.definition).product_declarations

    assert product_axis_dimension_id(derived, derived.axes[0]) == (
        product_axis_dimension_id(source, source.axes[0])
    )


def test_compute_joins_measured_products_with_earlier_compute_values() -> None:
    def double(*, base: float) -> float:
        return base * 2.0

    def classify_value(*, signal: float, threshold: float) -> bool:
        return signal > threshold

    @sc.module(id="test.mixed-availability-compute")
    def module(context: sc.ModuleContext) -> sc.ProductRef:
        threshold = context.compute(
            "threshold",
            fn=double,
            inputs={"base": 1.5},
            output_type=sc.ScalarType(sc.FloatType()),
        )
        signal = context._product("signal", unit="V")
        return context.compute(
            "classify",
            fn=classify_value,
            inputs={"signal": signal, "threshold": threshold},
            output_type=sc.ScalarType(sc.BoolType()),
        )

    logical = compose_module(module.definition)

    [threshold] = logical.compute_nodes
    [classify] = logical.measurement_computes
    assert classify.value_inputs == (("threshold", threshold.result_id),)
    result = classify.kernel(
        {
            "signal": MeasurementScalar.create(
                value=4.0,
                dtype="float64",
                unit="V",
            ),
            "threshold": 3.0,
        }
    )["result"]
    assert result == MeasurementScalar.create(value=True, dtype="bool")


def test_convert_preserves_measurement_availability_and_converts_native_values() -> (
    None
):
    @sc.module(id="test.measurement-unit-conversion")
    def module(context: sc.ModuleContext) -> sc.ProductRef:
        signal = context._product("signal", unit="V")
        return context.convert(signal, "mV")

    logical = compose_module(module.definition)

    [conversion] = logical.measurement_computes
    assert conversion.id.local_id == "convert_unit_value"
    assert [name for name, _binding in conversion.value_inputs] == [
        "source_unit",
        "target_unit",
    ]
    result = conversion.kernel(
        {
            "value": MeasurementScalar.create(
                value=0.125,
                dtype="float64",
                unit="V",
            ),
            "source_unit": "V",
            "target_unit": "mV",
        }
    )["result"]
    assert result == MeasurementScalar.create(
        value=125.0,
        dtype="float64",
        unit="mV",
    )


def test_convert_preserves_local_array_shape() -> None:
    @sc.module(id="test.measurement-array-unit-conversion")
    def module(context: sc.ModuleContext) -> sc.ProductRef:
        trace = context._product(
            "trace",
            unit="V",
            axes=(ProductAxis("sample", 2),),
        )
        return context.convert(trace, "mV")

    [conversion] = compose_module(module.definition).measurement_computes
    result = conversion.kernel(
        {
            "value": MeasurementArray.create(
                values=np.asarray([0.1, 0.2]),
                dtype="float64",
                unit="V",
            ),
            "source_unit": "V",
            "target_unit": "mV",
        }
    )["result"]

    assert isinstance(result, MeasurementArray)
    assert result.values.tolist() == [100.0, 200.0]
    assert result.unit == "mV"
    assert result.shape == (2,)


def test_compute_instantiates_an_annotated_product_bundle_schema() -> None:
    @_ProbabilityProducts.kernel
    def probabilities(*, signal: float) -> tuple[float, float]:
        return signal, 1.0 - signal

    @sc.module(id="test.typed-structured-compute")
    def module(context: sc.ModuleContext) -> _ProbabilityProducts:
        signal = context._product("signal", unit="ratio")
        return assert_type(
            context.compute(
                "probabilities",
                fn=probabilities,
                inputs={"signal": signal},
            ),
            _ProbabilityProducts,
        )

    result = module().result
    assert (
        result.positive.product_id.qualified_name
        == "typed-structured-compute/probabilities/positive"
    )
    assert (
        result.negative.product_id.qualified_name
        == "typed-structured-compute/probabilities/negative"
    )

    [compute] = compose_module(module.definition).measurement_computes
    output = compute.kernel(
        {
            "signal": MeasurementScalar.create(
                value=0.25,
                dtype="float64",
                unit="ratio",
            )
        }
    )
    assert output == {
        "positive": MeasurementScalar.create(
            value=0.25,
            dtype="float64",
            unit="ratio",
        ),
        "negative": MeasurementScalar.create(
            value=0.75,
            dtype="float64",
            unit="ratio",
        ),
    }

    with pytest.raises(TypeError, match="output_type must be omitted"):

        @sc.module(id="test.duplicated-structured-compute-schema")
        def duplicated(context: sc.ModuleContext) -> None:
            signal = context._product("signal", unit="ratio")
            context.compute(
                fn=probabilities,
                output_type=_ProbabilityProducts,
                signal=signal,
            )


def test_measured_compute_infers_bool_output_and_binds_keyword_inputs() -> None:
    def classify(*, signal: float, threshold: float) -> bool:
        return signal >= threshold

    @sc.module(id="test.inferred-measurement-compute")
    def module(context: sc.ModuleContext) -> sc.ProductRef:
        signal = context._product("signal", unit="ratio")
        result = context.compute(
            fn=classify,
            signal=signal,
            threshold=0.5,
        )
        assert isinstance(result, sc.ProductRef)
        return result

    [compute] = module.definition.body.measurement_computes
    assert [name for name, _product in compute.input_bindings] == ["signal"]
    assert [name for name, _value in compute.value_input_bindings] == ["threshold"]
    assert compute.implementation is None
    assert not compute.deterministic
    assert module().result.value_spec.dtype == "bool"


def test_compute_parameter_annotations_validate_measurement_units() -> None:
    def threshold(
        *,
        signal: Annotated[
            float,
            sc.ScalarType(sc.QuantityType(unit="V")),
        ],
    ) -> bool:
        return signal >= 0.5

    @sc.module(id="test.measurement-unit-contract")
    def valid(context: sc.ModuleContext) -> sc.ProductRef:
        signal = context._product("signal", unit="V")
        result = context.compute(fn=threshold, signal=signal)
        assert isinstance(result, sc.ProductRef)
        return result

    assert valid().result.value_spec.dtype == "bool"

    with pytest.raises(TypeError, match="input 'signal' expects Scalar"):

        @sc.module(id="test.measurement-unit-mismatch")
        def invalid(context: sc.ModuleContext) -> None:
            signal = context._product("signal", unit="mV")
            context.compute(fn=threshold, signal=signal)


def test_compute_parameter_annotations_validate_array_dimension_identity() -> None:
    def peak(
        *,
        trace: Annotated[
            NDArray[np.float64],
            sc.ArrayType(
                dtype="float64",
                dimensions=(sc.ArrayDimension("sample", 4, kind="time", unit="ns"),),
                unit="V",
            ),
        ],
    ) -> float:
        return float(np.max(trace))

    with pytest.raises(TypeError, match="input 'trace' expects Array"):

        @sc.module(id="test.measurement-dimension-mismatch")
        def invalid(context: sc.ModuleContext) -> None:
            trace = context._product(
                "trace",
                unit="V",
                axes=(
                    ProductAxis(
                        "frequency",
                        size=4,
                        kind="frequency",
                        unit="Hz",
                    ),
                ),
            )
            context.compute(fn=peak, trace=trace)


def test_compute_chaining_is_sorted_by_dependency() -> None:
    @sc.module(id="test.compute.chain")
    def module(context: sc.ModuleContext) -> None:
        raw = context._product("raw")
        middle = context._product("middle")
        derived = context._product("derived")
        context._measurement_compute(
            "second",
            input=middle,
            outputs={"result": derived},
            kernel=_identity,
        )
        context._measurement_compute(
            "first",
            input=raw,
            outputs={"result": middle},
            kernel=_identity,
        )

    verified = verify_logical_program(compose_module(module.definition))

    assert [
        compute.id.qualified_name for compute in verified.program.measurement_computes
    ] == ["first", "second"]


def test_domain_and_compute_cannot_own_the_same_product() -> None:
    program = domain_program(
        "program",
        dialect_id="test",
        dialect_version="1",
        body=object(),
        results={"raw": None},
    )

    @sc.module(id="test.compute.owner")
    def module(context: sc.ModuleContext) -> None:
        call = domain_call(program)
        source = context._product("source")
        context._measurement_compute(
            "derive",
            input=source,
            outputs={"result": call.results.raw},
            kernel=_identity,
        )
        context.use(call)

    with pytest.raises(CheckFailed) as error:
        verify_logical_program(compose_module(module.definition))
    assert "logical_product_producer_duplicate" in {
        problem.code for problem in error.value.problems
    }


def test_complex_scalar_mean_and_linear_unit_conversion_have_no_local_axis() -> None:
    def iq_mean(
        *, values: object
    ) -> Annotated[complex, sc.ScalarType(sc.ComplexType(unit="mV"))]:
        return complex(np.mean(np.asarray(values, dtype=np.complex128)))

    @sc.module(id="test.complex-scalar-mean")
    def module(context: sc.ModuleContext) -> sc.ProductRef:
        source = context._product(
            "iq", dtype="complex128", unit="mV", axes=(shot_axis(2),)
        )
        mean = context.compute("mean", fn=iq_mean, inputs={"values": source})
        assert isinstance(mean, sc.ProductRef)
        return context.convert(mean, "V", id="mean_volts")

    logical = compose_module(module.definition)
    output = logical.product_declarations[-1]
    assert output.value_spec.dtype == "complex128"
    assert output.value_spec.unit == "V"
    assert output.value_spec.axes == ()
    mean_compute, conversion = logical.measurement_computes
    mean = mean_compute.kernel(
        {
            "values": MeasurementArray.create(
                values=np.asarray([1 + 2j, 3 + 6j]),
                dtype="complex128",
                unit="mV",
            )
        }
    )["result"]
    assert isinstance(mean, MeasurementScalar)
    assert mean.value == 2 + 4j
    converted = conversion.kernel(
        {"value": mean, "source_unit": "mV", "target_unit": "V"}
    )["result"]
    assert isinstance(converted, MeasurementScalar)
    assert converted.value == 0.002 + 0.004j
    assert converted.dtype == "complex128"
    assert converted.unit == "V"


def test_complex_scalar_rejects_real_output_and_nonlinear_conversion() -> None:
    def identity(value: object) -> object:
        return value

    @sc.module(id="test.complex-real-output")
    def module(context: sc.ModuleContext) -> sc.ProductRef:
        source = context._product("iq", dtype="complex128")
        return context.compute(
            "wrong",
            fn=identity,
            inputs={"value": source},
            output_type=sc.ScalarType(sc.FloatType()),
        )

    [compute] = compose_module(module.definition).measurement_computes
    with pytest.raises(ValueError, match="expected float"):
        compute.kernel(
            {"value": MeasurementScalar.create(value=1 + 2j, dtype="complex128")}
        )

    with pytest.raises(ValueError, match="compatible linear units"):

        @sc.module(id="test.complex-nonlinear")
        def nonlinear(context: sc.ModuleContext) -> sc.ProductRef:
            return context.convert(
                context._product("iq", dtype="complex128", unit="dBm"), "W"
            )


def test_complex_literal_compute_evaluates_and_rejects_expression_arithmetic() -> None:
    def conjugate(value: complex) -> complex:
        return value.conjugate()

    @sc.module(id="test.complex-literal-compute")
    def module(context: sc.ModuleContext) -> sc.ValueRef:
        return context.compute(
            "conjugate",
            fn=conjugate,
            inputs={"value": 1 + 2j},
            output_type=sc.ScalarType(sc.ComplexType()),
        )

    logical = compose_module(module.definition)
    [compute] = logical.compute_nodes
    assert compute.result_type == sc.ScalarType(sc.ComplexType())
    verified = verify_logical_program(logical)
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

    problems: list[Problem] = []
    operations, payloads = bind_compute_operations(
        logical.compute_nodes,
        logical.implementations,
        {
            value_id: logical_program_value(verified, value_id)
            for _, value_id in compute.inputs
        },
        operation_prefix="literal",
        ctx=EvalContext(),
        demanded_payload_results=frozenset(),
        problems=problems,
    )
    assert problems == [] and payloads == {}
    boundary = EffectBoundary(run_id="complex-literal")
    frame = EffectEvaluationFrame()
    ComputeEffectExecutor(
        boundary=boundary, payload_codecs=EMPTY_PAYLOAD_CODECS
    ).execute(frame, operations)
    assert boundary.problems == []
    assert frame.compute_results[compute.result_id] == 1 - 2j

    with pytest.raises(TypeError):

        @sc.module(id="test.complex-unsupported-expression")
        def unsupported(context: sc.ModuleContext) -> sc.ValueRef:
            value = context.compute(
                "conjugate",
                fn=conjugate,
                inputs={"value": 1 + 2j},
                output_type=sc.ScalarType(sc.ComplexType()),
            )
            return value + 1.0
