"""Consumer units mean the same thing for literals, inputs and measurements."""

from collections.abc import Callable
from typing import Annotated, cast

import numpy as np
import pytest
from numpy.typing import NDArray

import scopecat as sc
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
from scopecat.measurements.results import MeasurementArray, MeasurementScalar
from scopecat.planning.local_compute import bind_compute_operations
from scopecat.program.logical import LogicalProgram
from scopecat.program.products import ProductAxis
from scopecat.sdk.payloads import EMPTY_PAYLOAD_CODECS


def _execute(logical: LogicalProgram) -> object:
    verified = verify_logical_program(logical)
    problems: list[Problem] = []
    operations, _ = bind_compute_operations(
        logical.compute_nodes,
        logical.implementations,
        {
            value_id: logical_program_value(verified, value_id)
            for compute in logical.compute_nodes
            for _, value_id in compute.inputs
        },
        operation_prefix="units",
        ctx=EvalContext(),
        demanded_payload_results=frozenset(),
        problems=problems,
    )
    assert problems == []
    boundary = EffectBoundary(run_id="units")
    frame = EffectEvaluationFrame()
    ComputeEffectExecutor(
        boundary=boundary, payload_codecs=EMPTY_PAYLOAD_CODECS
    ).execute(frame, operations)
    assert boundary.problems == []
    return list(frame.compute_results.values())[-1]


def _scalar_program(
    fn: Callable[..., float], source: sc.Quantity, path: str
) -> LogicalProgram:
    if path == "input":

        def input_module(
            ctx: sc.ModuleContext,
            x: Annotated[sc.Input[sc.Quantity], sc.QuantityType(unit=source.unit)],
        ) -> sc.DataRef[float]:
            return ctx.compute(fn=fn, x=x)

        return compose_module(
            sc.module(input_module, id="units.input").definition, x=source
        )

    @sc.module(id="units.scalar")
    def module(ctx: sc.ModuleContext) -> sc.DataRef[float]:
        if path == "literal":
            return ctx.compute(fn=fn, x=source)
        if path == "value":
            x = ctx.compute(
                fn=lambda: source,
                output_type=sc.ScalarType(sc.QuantityType(unit=source.unit)),
            )
            return ctx.compute(fn=fn, x=x)
        return ctx.compute(fn=fn, x=ctx._product("source", unit=source.unit))

    return compose_module(module.definition)


type MHzQuantity = Annotated[sc.Quantity, sc.Unit("MHz")]
type MHzFloat = Annotated[float, sc.Unit("MHz")]


@pytest.mark.parametrize("path", ["literal", "input", "value", "measured"])
@pytest.mark.parametrize("source", [sc.Quantity(5, "MHz"), sc.Quantity(5000, "kHz")])
@pytest.mark.parametrize(
    "annotation",
    [
        sc.Quantity,
        MHzQuantity,
        MHzFloat,
        Annotated[float, sc.ScalarType(sc.QuantityType(unit="MHz"))],
    ],
)
def test_scalar_consumer_units_and_native_types(
    path: str, source: sc.Quantity, annotation: object
) -> None:
    def consume(x: object) -> float:
        if annotation is sc.Quantity:
            assert x == source
            assert isinstance(x, sc.Quantity)
            return x.to("MHz").value
        if annotation is MHzQuantity:
            assert x == sc.Quantity(5, "MHz")
            return cast("sc.Quantity", x).value
        assert type(x) is float
        return x

    consume.__annotations__["x"] = annotation
    logical = _scalar_program(consume, source, path)
    if path == "measured":
        [compute] = logical.measurement_computes
        measurement = MeasurementScalar.create(
            value=source.value, dtype="float64", unit=source.unit
        )
        assert compute.kernel({"x": measurement})["result"] == MeasurementScalar.create(
            value=5.0
        )
        assert measurement.unit == source.unit and measurement.value == source.value
    else:
        assert _execute(logical) == 5.0


@pytest.mark.parametrize("path", ["literal", "input", "value", "measured"])
@pytest.mark.parametrize(
    "annotation,source,message",
    [
        (float, sc.Quantity(5000, "kHz"), "unit-bearing input requires"),
        (MHzFloat, sc.Quantity(5, "ns"), "expects Scalar"),
        (Annotated[float, sc.Unit("W")], sc.Quantity(-20, "dBm"), "expects Scalar"),
    ],
)
def test_invalid_scalar_units_fail_before_execution(
    path: str, annotation: object, source: sc.Quantity, message: str
) -> None:
    def consume(x: float) -> float:
        pytest.fail("invalid inputs must not enter the native function")

    consume.__annotations__["x"] = annotation
    with pytest.raises(TypeError, match=f"compute 'consume' input 'x' {message}"):
        _scalar_program(consume, source, path)


_DIMENSIONS = (sc.ArrayDimension("sample", 2),)
type MHzArray = Annotated[
    NDArray[np.float64],
    sc.ArrayType(dtype="float64", unit="MHz", dimensions=_DIMENSIONS),
]


def _array_program(
    fn: Callable[..., float], source_unit: str | None, path: str
) -> LogicalProgram:
    source_type = sc.ArrayType(
        dtype="float64", unit=source_unit, dimensions=_DIMENSIONS
    )
    if path == "input":

        @sc.module(id="units.array-input")
        def input_module(
            ctx: sc.ModuleContext,
            x: Annotated[sc.Input[NDArray[np.float64]], source_type],
        ) -> sc.DataRef[float]:
            return ctx.compute(fn=fn, x=sc.input_ref(x))

        values = [5000.0, 6000.0] if source_unit == "kHz" else [5.0, 6.0]
        return compose_module(input_module.definition, x=values)

    @sc.module(id="units.array")
    def module(ctx: sc.ModuleContext) -> sc.DataRef[float]:
        if path == "value":
            x = ctx.compute(
                fn=lambda: np.array(
                    [5000.0, 6000.0] if source_unit == "kHz" else [5.0, 6.0]
                ),
                output_type=source_type,
            )
        else:
            x = ctx._product(
                "source",
                dtype="float64",
                unit=source_unit,
                axes=(ProductAxis("sample", 2),),
            )
        return ctx.compute(fn=fn, x=x)

    return compose_module(module.definition)


@pytest.mark.parametrize("path", ["input", "value", "measured"])
@pytest.mark.parametrize("unit", ["kHz", "MHz", None])
def test_array_consumer_converts_only_values_and_preserves_source(
    path: str, unit: str | None
) -> None:
    def consume(x: MHzArray) -> float:
        assert isinstance(x, np.ndarray) and x.dtype == np.float64
        np.testing.assert_array_equal(x, [5.0, 6.0])
        return float(x.mean())

    logical = _array_program(consume, unit, path)
    if path == "measured":
        [compute] = logical.measurement_computes
        raw = np.array([5000.0, 6000.0] if unit == "kHz" else [5.0, 6.0])
        measurement = MeasurementArray.create(values=raw, dtype="float64", unit=unit)
        assert compute.kernel({"x": measurement})["result"] == MeasurementScalar.create(
            value=5.5
        )
        np.testing.assert_array_equal(measurement.values, raw)
        assert measurement.unit == unit
    else:
        assert _execute(logical) == 5.5


@pytest.mark.parametrize("path", ["input", "value", "measured"])
@pytest.mark.parametrize(
    "annotation,unit,message",
    [
        (NDArray[np.float64], "kHz", "unit-bearing input requires"),
        (MHzArray, "ns", "expects Array"),
        (
            Annotated[
                NDArray[np.float64],
                sc.ArrayType(
                    dtype="float64",
                    unit="MHz",
                    dimensions=(sc.ArrayDimension("other", 2),),
                ),
            ],
            "kHz",
            "expects Array",
        ),
        (
            Annotated[
                NDArray[np.int64],
                sc.ArrayType(dtype="int64", unit="MHz", dimensions=_DIMENSIONS),
            ],
            "kHz",
            "expects Array",
        ),
    ],
)
def test_array_contract_rejects_missing_units_wrong_dimension_axes_and_dtype(
    path: str, annotation: object, unit: str, message: str
) -> None:
    def consume(x: object) -> float:
        pytest.fail("invalid inputs must not enter the native function")

    consume.__annotations__["x"] = annotation
    with pytest.raises(TypeError, match=message):
        _array_program(consume, unit, path)


@pytest.mark.parametrize(
    "values,target,expected",
    [
        ([5000, 6000], "MHz", [5, 6]),
        ([5001, 6000], "MHz", "truncate integer"),
        ([np.iinfo(np.int64).max, 1], "Hz", "overflows integer"),
    ],
)
def test_integer_array_conversion_is_exact_or_rejected(
    values: list[int], target: str, expected: list[int] | str
) -> None:
    def consume(x: object) -> float:
        assert isinstance(x, np.ndarray) and x.dtype == np.int64
        np.testing.assert_array_equal(x, expected)
        return 1.0

    consume.__annotations__["x"] = Annotated[
        NDArray[np.int64],
        sc.ArrayType(dtype="int64", unit=target, dimensions=_DIMENSIONS),
    ]

    @sc.module(id="units.integer")
    def module(ctx: sc.ModuleContext) -> sc.DataRef[float]:
        return ctx.compute(
            fn=consume,
            x=ctx._product(
                "source", dtype="int64", unit="kHz", axes=(ProductAxis("sample", 2),)
            ),
        )

    [compute] = compose_module(module.definition).measurement_computes
    value = MeasurementArray.create(
        values=np.asarray(values, dtype=np.int64), dtype="int64", unit="kHz"
    )
    if isinstance(expected, str):
        with pytest.raises(ValueError, match=expected):
            compute.kernel({"x": value})
    else:
        assert compute.kernel({"x": value})["result"] == MeasurementScalar.create(
            value=1.0
        )


def test_defaults_and_explicit_numeric_magnitudes() -> None:
    def consume(x: Annotated[float, sc.Unit("MHz")] = 5.0) -> float:
        assert type(x) is float
        return x

    @sc.module(id="units.defaults")
    def module(ctx: sc.ModuleContext) -> sc.DataRef[float]:
        return ctx.compute(fn=consume)

    assert _execute(compose_module(module.definition)) == 5.0

    @sc.module(id="units.numeric-magnitude")
    def numeric(ctx: sc.ModuleContext) -> sc.DataRef[float]:
        return ctx.compute(fn=consume, x=5)

    assert _execute(compose_module(numeric.definition)) == 5.0

    default = sc.Quantity(5000, "kHz")

    def quantity(x: MHzQuantity = default) -> float:
        assert x.unit == "MHz"
        return x.value

    @sc.module(id="units.quantity-default")
    def quantity_module(ctx: sc.ModuleContext) -> sc.DataRef[float]:
        return ctx.compute(fn=quantity)

    assert _execute(compose_module(quantity_module.definition)) == 5.0


def test_unitless_numbers_and_arrays_keep_native_types() -> None:
    def consume(x: float) -> float:
        assert type(x) is float
        return x

    @sc.module(id="units.unitless")
    def module(ctx: sc.ModuleContext) -> sc.DataRef[float]:
        return ctx.compute(fn=consume, x=5)

    assert _execute(compose_module(module.definition)) == 5.0

    def mean(x: NDArray[np.float64]) -> float:
        assert isinstance(x, np.ndarray) and x.dtype == np.float64
        return float(x.mean())

    @sc.module(id="units.unitless-array")
    def measured(ctx: sc.ModuleContext) -> sc.DataRef[float]:
        return ctx.compute(
            fn=mean,
            x=ctx._product("source", unit=None, axes=(ProductAxis("sample", 2),)),
        )

    [compute] = compose_module(measured.definition).measurement_computes
    assert compute.kernel(
        {"x": MeasurementArray.create(values=np.array([5.0, 6.0]), dtype="float64")}
    )["result"] == MeasurementScalar.create(value=5.5)


def test_array_runtime_checks_shape_and_conversion_overflow() -> None:
    def consume(
        x: Annotated[
            NDArray[np.float64],
            sc.ArrayType(dtype="float64", unit="Hz", dimensions=_DIMENSIONS),
        ],
    ) -> float:
        pytest.fail("invalid array must not enter native function")

    logical = _array_program(consume, "MHz", "measured")
    [compute] = logical.measurement_computes
    for values, message in [
        (np.array([1.0, 2.0, 3.0]), "must have size"),
        (np.array([np.finfo(np.float64).max, 1.0]), "overflow"),
    ]:
        with pytest.raises(ValueError, match=message):
            compute.kernel(
                {
                    "x": MeasurementArray.create(
                        values=values, dtype="float64", unit="MHz"
                    )
                }
            )


def test_typed_array_input_build_snapshots_native_values_before_conversion() -> None:
    from scopecat.compiler.frontend.resolution import compile_invocation

    def consume(x: MHzArray) -> float:
        np.testing.assert_array_equal(x, [5.0, 6.0])
        return float(x.mean())

    @sc.experiment(id="units.array-input-build")
    def experiment(
        ctx: sc.ExperimentContext,
        x: Annotated[
            sc.Input[NDArray[np.float64]],
            sc.ArrayType(dtype="float64", unit="kHz", dimensions=_DIMENSIONS),
        ],
    ) -> sc.DataRef[float]:
        return ctx.compute(fn=consume, x=sc.input_ref(x))

    raw = np.array([5000.0, 6000.0])
    invocation = experiment.build(x=raw)
    raw[:] = 0.0
    assert _execute(compile_invocation(invocation).program.program) == 5.5


@pytest.mark.parametrize("direct", [False, True])
def test_native_optional_and_list_defaults_do_not_require_scalar_schemas(
    direct: bool,
) -> None:
    @sc.compute
    def optional(x: float | None = None) -> float:
        assert x is None
        return 7.0

    default_values = [2.0, 3.0]

    @sc.compute
    def sequence(x: list[float] = default_values) -> float:
        assert isinstance(x, list)
        return sum(x)

    default_array = np.array([4.0, 6.0])

    @sc.compute
    def array_default(x: NDArray[np.float64] = default_array) -> float:
        assert isinstance(x, np.ndarray)
        return float(x.mean())

    @sc.module(id="units.native-defaults")
    def module(ctx: sc.ModuleContext) -> sc.DataRef[float]:
        if direct:
            ctx.compute(fn=optional.eager)
            ctx.compute(fn=sequence.eager)
            return ctx.compute(fn=array_default.eager)
        optional()
        optional(None)
        sequence()
        return array_default()

    assert _execute(compose_module(module.definition)) == 5.0


@pytest.mark.parametrize("container", ["ndarray", "list"])
@pytest.mark.parametrize("default", [False, True])
def test_direct_array_literals_and_defaults_use_consumer_contract(
    container: str, default: bool
) -> None:
    raw = np.array([5.0, 6.0])
    values = raw if container == "ndarray" else [5.0, 6.0]

    default_values = cast("MHzArray", values)

    @sc.compute
    def consume(x: MHzArray = default_values) -> float:
        assert x.dtype == np.float64
        np.testing.assert_array_equal(x, [5.0, 6.0])
        return float(x.mean())

    @sc.module(id="units.literal-array")
    def module(ctx: sc.ModuleContext) -> sc.DataRef[float]:
        return consume() if default else consume(values)

    logical = compose_module(module.definition)
    raw[:] = 0.0
    if isinstance(values, list):
        values[:] = [0.0, 0.0]
    assert _execute(logical) == 5.5


type ComplexMHzArray = Annotated[
    NDArray[np.complex128],
    sc.ArrayType(dtype="complex128", unit="MHz", dimensions=_DIMENSIONS),
]


@pytest.mark.parametrize("path", ["input", "literal", "default"])
def test_complex_array_snapshots_execute_with_native_dtype(path: str) -> None:
    from scopecat.compiler.frontend.resolution import compile_invocation

    raw = np.array([5000 + 1000j, 6000 + 2000j], dtype=np.complex128)

    default_values = raw / 1000

    @sc.compute
    def consume(x: ComplexMHzArray = default_values) -> float:
        assert x.dtype == np.complex128
        np.testing.assert_array_equal(x, [5 + 1j, 6 + 2j])
        return float(x.real.mean())

    @sc.experiment(id="units.complex-array")
    def experiment(
        ctx: sc.ExperimentContext,
        x: Annotated[
            sc.Input[NDArray[np.complex128]],
            sc.ArrayType(dtype="complex128", unit="kHz", dimensions=_DIMENSIONS),
        ],
    ) -> sc.DataRef[float]:
        if path == "input":
            return consume(sc.input_ref(x))
        if path == "default":
            return consume()
        return consume(raw / 1000)

    invocation = experiment.build(x=raw)
    if path == "input":
        raw[:] = 0
    logical = compile_invocation(invocation).program.program
    raw[:] = 0
    assert _execute(logical) == 5.5


@pytest.mark.parametrize("values", [[5.0, 6.0, 7.0], [5 + 1j, 6 + 2j]])
def test_direct_array_literals_still_validate_shape_and_dtype(values: object) -> None:
    @sc.compute
    def consume(x: MHzArray) -> float:
        raise AssertionError("invalid literals must fail before execution")

    def module(ctx: sc.ModuleContext) -> sc.DataRef[float]:
        return consume(cast("list[object]", values))

    with pytest.raises(TypeError, match=r"compute.*consume.*input.*x"):
        compose_module(sc.module(module, id="units.invalid-literal-array").definition)
