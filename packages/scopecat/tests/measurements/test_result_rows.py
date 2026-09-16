"""Native dataclass reads check old schema without loading an author declaration."""

from dataclasses import dataclass
from typing import Annotated, assert_type

import numpy as np
import pytest
from numpy.typing import NDArray

import scopecat as sc
from scopecat.measurements.dataset import Dataset
from scopecat.records.content import ContentEntry
from scopecat.records.measurement import (
    MeasurementArray,
    MeasurementArrayAvailability,
    MeasurementArrayUnavailableGroup,
    MeasurementDataset,
    MeasurementDatasetSchema,
    MeasurementDimension,
    MeasurementPointCloudPointDomain,
    MeasurementPointDomainColumn,
    MeasurementRecord,
    MeasurementResultContract,
    MeasurementResultField,
    MeasurementScalar,
    MeasurementUnavailable,
    MeasurementVariable,
)


@dataclass(frozen=True)
class Signal[T]:
    iq: T


@dataclass(frozen=True)
class Reading[T]:
    bias: sc.Quantity
    data: Signal[T]


type IQ = Annotated[complex, sc.ScalarType(sc.ComplexType(unit="V"))]
type Shots = Annotated[
    NDArray[np.complex128],
    sc.ArrayType(
        dtype="complex128", unit="V", dimensions=(sc.ArrayDimension("shot", 2),)
    ),
]


def retained(*, array: bool = False, unavailable: bool = False) -> Dataset:
    schema = MeasurementDatasetSchema(
        dataset_id="reading",
        point_domain=MeasurementPointCloudPointDomain(
            columns=(MeasurementPointDomainColumn(id="bias"),)
        ),
        dimensions=(
            MeasurementDimension(id="point", kind="point", size=1),
            *(
                (MeasurementDimension(id="product/signal/shot", kind="index", size=2),)
                if array
                else ()
            ),
        ),
        variables=(
            MeasurementVariable(
                id="bias", role="coordinate", dtype="float64", unit="V", dims=("point",)
            ),
            MeasurementVariable(
                id="signal",
                role="observable",
                dtype="complex128",
                unit="V",
                dims=("point", "product/signal/shot") if array else ("point",),
            ),
        ),
        primary_observables=("signal",),
        result=MeasurementResultContract(
            id="reading.result",
            version="sha256:" + "0" * 64,
            fields=(
                MeasurementResultField(path=("bias",), variable_id="bias"),
                MeasurementResultField(path=("data", "iq"), variable_id="signal"),
            ),
        ),
    )
    value = (
        MeasurementUnavailable.create(
            reason="invalid", dtype="complex128", unit="V", shape=(), metadata={}
        )
        if unavailable
        else (
            MeasurementArray.create(
                values=np.array([1 + 2j, 3 + 4j]), dtype="complex128", unit="V"
            )
            if array
            else MeasurementScalar.create(dtype="complex128", value=2 + 3j, unit="V")
        )
    )
    raw = MeasurementDataset(
        dataset_schema=schema,
        records=(
            MeasurementRecord(
                run_id="old-run",
                point_index=0,
                coordinates={
                    "bias": MeasurementScalar.create(
                        dtype="float64", value=0.5, unit="V"
                    )
                },
                observables={"signal": value},
            ),
        ),
    )
    return Dataset(
        raw,
        ContentEntry(
            role="dataset",
            id="measurements",
            kind="measurement_dataset",
            content_hash="unused",
            schema=schema.model_dump(mode="json"),
        ),
    )


def test_generic_nested_complex_and_quantity_rows() -> None:
    rows = retained().result.rows_as(Reading[IQ])
    assert_type(rows, tuple[Reading[IQ], ...])
    assert_type(rows[0].data.iq, complex)
    assert rows == (Reading(sc.Quantity(0.5, "V"), Signal(2 + 3j)),)


def test_typed_arrays_keep_dtype_shape_and_immutability() -> None:
    rows = retained(array=True).result.rows_as(Reading[Shots])
    assert_type(rows[0].data.iq, NDArray[np.complex128])
    assert rows[0].data.iq.dtype == np.complex128
    assert rows[0].data.iq.shape == (2,)
    assert not rows[0].data.iq.flags.writeable


@pytest.mark.parametrize(
    "row_type",
    [Reading[float], Reading[NDArray[np.float64]], Reading[NDArray[np.complex128]]],
)
def test_scalar_cannot_be_read_as_another_native_type(row_type: type[object]) -> None:
    with pytest.raises(TypeError, match="does not match"):
        retained().result.rows_as(row_type)


type WrongUnit = Annotated[complex, sc.ScalarType(sc.ComplexType(unit="mV"))]
type WrongSize = Annotated[
    NDArray[np.complex128],
    sc.ArrayType(
        dtype="complex128", unit="V", dimensions=(sc.ArrayDimension("shot", 3),)
    ),
]
type WrongAxis = Annotated[
    NDArray[np.complex128],
    sc.ArrayType(
        dtype="complex128", unit="V", dimensions=(sc.ArrayDimension("frequency", 2),)
    ),
]


def test_units_axes_and_extents_are_checked_before_reading() -> None:
    with pytest.raises(TypeError, match="dtype/unit"):
        retained().result.rows_as(Reading[WrongUnit])
    with pytest.raises(TypeError, match="extent"):
        retained(array=True).result.rows_as(Reading[WrongSize])
    with pytest.raises(TypeError, match="axis"):
        retained(array=True).result.rows_as(Reading[WrongAxis])


def test_changed_fields_and_defaults_cannot_hide_schema_drift() -> None:
    @dataclass
    class Renamed:
        bias: sc.Quantity
        average: complex = 0j

    with pytest.raises(TypeError, match="no persisted field"):
        retained().result.rows_as(Renamed)

    @dataclass
    class Incomplete:
        bias: sc.Quantity

    with pytest.raises(TypeError, match="unread"):
        retained().result.rows_as(Incomplete)


def test_missing_values_require_explicit_availability_selection() -> None:
    view = retained(unavailable=True).result
    with pytest.raises(ValueError, match="unavailable"):
        view.rows_as(Reading[complex])
    assert view.where_available().rows_as(Reading[complex]) == ()


def test_partial_array_mask_cannot_be_lost_in_a_native_row() -> None:
    original = retained(array=True)
    raw = MeasurementArray.create(
        values=np.array([1 + 2j, 0j]),
        dtype="complex128",
        unit="V",
        availability=MeasurementArrayAvailability(
            valid=np.array([True, False]),
            unavailable=(
                MeasurementArrayUnavailableGroup(
                    reason="invalid",
                    flat_indices=(1,),
                    metadata={},
                ),
            ),
        ),
    )
    record = original.records[0].model_copy(update={"observables": {"signal": raw}})
    dataset = Dataset(
        MeasurementDataset(dataset_schema=original.schema, records=(record,)),
        original.entry,
    )
    with pytest.raises(ValueError, match="unavailable"):
        dataset.result.rows_as(Reading[Shots])
    assert dataset.result.where_available().rows_as(Reading[Shots]) == ()
