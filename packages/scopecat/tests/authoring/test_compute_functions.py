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
