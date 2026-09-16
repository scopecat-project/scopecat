"""Native return annotations survive graph construction and typed point reads."""

# pyright: reportUnnecessaryTypeIgnoreComment=true

from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated, assert_type

import numpy as np
from numpy.typing import NDArray

import scopecat as sc
from scopecat.measurements.dataset import ExperimentResultView
from scopecat.program.products import ProductAxis


@dataclass(frozen=True)
class MeanResult:
    iq: sc.DataRef[complex]


def mean_iq(
    *, iq: NDArray[np.complex128]
) -> Annotated[complex, sc.ScalarType(sc.ComplexType(unit="ratio"))]:
    return complex(iq.mean())


def shots() -> Annotated[
    NDArray[np.complex128],
    sc.ArrayType(
        dtype="complex128",
        unit="ratio",
        dimensions=(sc.ArrayDimension("shot", 2),),
    ),
]:
    return np.array([1 + 2j, 3 + 4j], dtype=np.complex128)


def test_inferred_compute_preserves_native_type_through_experiment_result() -> None:
    @sc.experiment(id="test.inferred-mean")
    def experiment(ctx: sc.ExperimentContext) -> MeanResult:
        iq = assert_type(ctx.compute(fn=shots), sc.DataRef[NDArray[np.complex128]])
        mean = assert_type(ctx.compute(fn=mean_iq, iq=iq), sc.DataRef[complex])
        return MeanResult(mean)

    result = experiment.build().output
    assert_type(result, MeanResult)
    assert isinstance(result.iq, sc.ValueRef)
    assert result.iq.value_type == sc.ScalarType(sc.ComplexType(unit="ratio"))


def test_inferred_compute_preserves_native_type_for_measurement_inputs() -> None:
    @sc.module(id="test.inferred-measured-mean")
    def module(ctx: sc.ModuleContext) -> MeanResult:
        iq = ctx._product(
            "iq",
            dtype="complex128",
            unit="ratio",
            axes=(ProductAxis("shot", 2),),
        )
        return MeanResult(
            assert_type(ctx.compute(fn=mean_iq, iq=iq), sc.DataRef[complex])
        )

    result = module().result
    assert isinstance(result.iq, sc.ProductRef)
    assert result.iq.value_spec.dtype == "complex128"
    assert result.iq.value_spec.unit == "ratio"
    assert result.iq.value_spec.axes == ()


if TYPE_CHECKING:

    def read_result(view: ExperimentResultView[MeanResult]) -> None:
        assert_type(view[0].value(view.output.iq), complex)
        assert_type(
            view.rows(lambda point: point.value(view.output.iq)), tuple[complex, ...]
        )
        # A complex result cannot silently become a real scalar in the reader.
        real: float = view[0].value(view.output.iq)  # pyright: ignore[reportAssignmentType]
        print(real)


def test_inferred_compute_rejects_conflicting_native_and_storage_annotations() -> None:
    import pytest

    def wrong_scalar() -> Annotated[float, sc.ScalarType(sc.ComplexType())]:
        return 1.0

    def wrong_array() -> Annotated[
        NDArray[np.float64],
        sc.ArrayType(dtype="complex128", dimensions=(sc.ArrayDimension("shot", 1),)),
    ]:
        return np.array([1.0])

    def wrong_quantity() -> Annotated[float, sc.ScalarType(sc.QuantityType(unit="V"))]:
        return 1.0

    for fn in (wrong_scalar, wrong_array, wrong_quantity):
        with pytest.raises(TypeError, match="return annotation disagrees"):
            sc.ModuleContext().compute(fn=fn)
