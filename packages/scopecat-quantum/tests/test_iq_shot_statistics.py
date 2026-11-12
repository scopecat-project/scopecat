"""The first statistics contract is one complete integrated-IQ shot array."""

from __future__ import annotations

import math
from typing import assert_type

import numpy as np
import pytest
import scopecat as sc
from scopecat.measurements.results import (
    MeasurementArray,
    MeasurementArrayAvailability,
    MeasurementPartitionedArray,
    MeasurementScalar,
)
from scopecat.program.measurement_types import MeasurementDType
from scopecat.program.measurements import MeasurementCompute
from scopecat.program.products import ProductAxis

from scopecat_quantum.measurement_computes import IqShotStatistics, iq_shot_statistics

_SHOT_AXES = (ProductAxis("shot", None, kind="shot", unit="count"),)


def _compute(
    *,
    dtype: MeasurementDType = "complex128",
    unit: str | None = "ratio",
    axes: tuple[ProductAxis, ...] = _SHOT_AXES,
) -> MeasurementCompute:
    @sc.module
    def statistics(context: sc.ModuleContext) -> IqShotStatistics:
        source = context._product("source", dtype=dtype, unit=unit, axes=axes)
        result = assert_type(iq_shot_statistics(context, source), IqShotStatistics)
        assert_type(result.count, sc.ProductRef[int])
        assert_type(result.mean, sc.ProductRef[complex])
        return result

    [compute] = statistics.definition.body.measurement_computes
    return compute


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        ((1 + 2j,), 1 + 2j),
        ((1 + 2j, 3 + 4j, -1 + 6j), 1 + 4j),
        ((1 + 0j, -1 + 0j), 0j),
        ((1e308 + 1e308j, 1e308 + 1e308j), 1e308 + 1e308j),
        (
            (complex(float(np.finfo(float).max), 0),) * 3,
            complex(float(np.finfo(float).max), 0),
        ),
        (
            (complex(math.ulp(0.0), 0),) * 2,
            complex(math.ulp(0.0), 0),
        ),
    ],
)
def test_iq_statistics_counts_shots_and_preserves_complex_mean(
    values: tuple[complex, ...], expected: complex
) -> None:
    source = MeasurementArray.create(dtype="complex128", unit="ratio", values=values)
    output = _compute().kernel({"iq_shots": source})
    assert output == {
        "count": MeasurementScalar.create(dtype="int64", value=len(values)),
        "mean": MeasurementScalar.create(
            dtype="complex128", unit="ratio", value=expected
        ),
    }
    np.testing.assert_array_equal(source.values, values)


@pytest.mark.parametrize(
    "values", [(), (complex(float("nan"), 0),), (complex(0, float("inf")),)]
)
def test_iq_statistics_rejects_empty_or_nonfinite_arrays(
    values: tuple[complex, ...],
) -> None:
    with pytest.raises(ValueError, match=r"at least one shot|finite"):
        source = MeasurementArray.create(
            dtype="complex128", unit="ratio", values=values
        )
        _compute().kernel({"iq_shots": source})


@pytest.mark.parametrize("dtype", ["float64", "complex64", "int64"])
def test_iq_statistics_rejects_other_dtypes(dtype: MeasurementDType) -> None:
    with pytest.raises(TypeError, match="expects Array"):
        _compute(dtype=dtype)


@pytest.mark.parametrize("unit", [None, "V"])
def test_iq_statistics_rejects_other_value_units(unit: str | None) -> None:
    with pytest.raises(TypeError, match="explicit ratio units"):
        _compute(unit=unit)


@pytest.mark.parametrize(
    "axes",
    [
        (),
        (ProductAxis("sample", 4, kind="shot", unit="count"),),
        (ProductAxis("shot", 4, kind="index", unit="count"),),
        (ProductAxis("shot", 4, kind="shot", unit="ns"),),
        (ProductAxis("shot", 4, kind="shot", unit="count"), ProductAxis("channel", 2)),
    ],
)
def test_iq_statistics_rejects_ambiguous_axes(axes: tuple[ProductAxis, ...]) -> None:
    with pytest.raises(TypeError, match="expects Array"):
        _compute(axes=axes)


@pytest.mark.parametrize("partitioned", [False, True])
def test_iq_statistics_rejects_missing_shots_instead_of_counting_placeholders(
    partitioned: bool,
) -> None:
    incomplete = MeasurementArray.create(
        dtype="complex128",
        unit="ratio",
        values=(1 + 2j, 0j),
        availability=MeasurementArrayAvailability.create(valid=(True, False)),
    )
    source = (
        MeasurementPartitionedArray.create(
            partitions=(incomplete,), axis=0, dtype="complex128", unit="ratio"
        )
        if partitioned
        else incomplete
    )
    with pytest.raises(ValueError, match="fully available"):
        _compute().kernel({"iq_shots": source})


def test_iq_statistics_materializes_complete_transport_partitions_once() -> None:
    source = MeasurementPartitionedArray.create(
        partitions=tuple(
            MeasurementArray.create(dtype="complex128", unit="ratio", values=values)
            for values in ((1 + 2j,), (3 + 4j, -1 + 6j))
        ),
        axis=0,
        dtype="complex128",
        unit="ratio",
    )
    output = _compute().kernel({"iq_shots": source})
    assert output["count"] == MeasurementScalar.create(dtype="int64", value=3)
    assert output["mean"] == MeasurementScalar.create(
        dtype="complex128", unit="ratio", value=1 + 4j
    )
