# pyright: reportUnknownArgumentType=false, reportUnknownMemberType=false
# pyright: reportUnknownParameterType=false
# pyright: reportUnknownVariableType=false

from __future__ import annotations

import json
import math
import warnings
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import assert_type, cast

import numpy as np
import pyarrow as pa
import pytest
import xarray as xr

from scopecat.kernel.entity import EntityRef, entity_identity_key
from scopecat.kernel.quantity import Quantity
from scopecat.measurements.datasets import select_measurement_schema
from scopecat.measurements.results import (
    Dataset,
    ImplicitPandasUnitWarning,
    LabeledMeasurementArray,
    PointMask,
    ProjectionSchema,
    Variable,
)
from scopecat.program.measurement_types import (
    MeasurementArrayData,
    MeasurementDType,
)
from scopecat.program.products import ModuleProductDecl, ProductRef, ProductValueSpec
from scopecat.program.record_refs import RecordRef
from scopecat.program.value_refs import ValueRef
from scopecat.program.value_types import Quantity as QuantityType
from scopecat.program.value_types import Scalar
from scopecat.program.values import CoordinateRef, compute, coordinate
from scopecat.records.content import ContentEntry
from scopecat.records.measurement import (
    EntityAcquisitionEvidence,
    InstrumentAcquisitionEvidence,
    MeasurementAcquisitionEvidenceCatalog,
    MeasurementArray,
    MeasurementArrayAvailability,
    MeasurementDataset,
    MeasurementDatasetSchema,
    MeasurementDimension,
    MeasurementEntityAcquisition,
    MeasurementEntityIndex,
    MeasurementEntityProductSource,
    MeasurementPointCloudPointDomain,
    MeasurementPointDomainAxis,
    MeasurementPointDomainColumn,
    MeasurementPointDomainRangeSource,
    MeasurementPointDomainValuesSource,
    MeasurementProductGridPointDomain,
    MeasurementRecord,
    MeasurementResultContract,
    MeasurementResultField,
    MeasurementScalar,
    MeasurementUnavailable,
    MeasurementValue,
    MeasurementVariable,
    MeasurementVariableGroup,
)


def test_dataset_exposes_labeled_variables_and_raw_records() -> None:
    dataset = _dataset()

    assert dataset.entry.id == "raw-measurements"
    assert dataset.schema.dataset_id == "raw-measurements"
    assert dataset.dims == {"point": 3, "sample": 2}
    assert tuple(dataset.coords) == ("bias", "frequency")
    assert tuple(dataset.data_vars) == ("temperature", "signal")
    assert tuple(dataset.variable_groups) == ("readout",)
    assert tuple(variable.id for variable in dataset.variable_groups["readout"]) == (
        "signal",
    )
    assert tuple(dataset) == ("bias", "frequency", "temperature", "signal")
    assert isinstance(dataset["bias"], Variable)
    assert dataset["bias"].values == (0.0, 1.0, 2.0)
    assert dataset["bias"].shape == (3,)

    bias = dataset["bias"].dense
    assert isinstance(bias, LabeledMeasurementArray)
    assert bias.layout == "dense"
    assert bias.declared_dims == ("point",)
    assert bias.dims == ("point",)
    assert bias.valid.tolist() == [True, True, True]
    assert bias.to("mV").values.tolist() == [0.0, 1000.0, 2000.0]
    assert bias.isel(point=slice(1, None)).values.tolist() == [1.0, 2.0]
    temperature = dataset["temperature"].dense
    assert temperature.valid.tolist() == [True, False, True]
    assert temperature.unavailable_reasons.tolist() == [None, "invalid", None]
    with pytest.raises(ValueError, match="is dense"):
        _ = dataset["bias"].observations
    assert dataset["frequency"].shape == (3, 2)
    frequency = dataset["frequency"][1]
    signal = dataset["signal"][0]
    assert isinstance(frequency, np.ndarray)
    assert isinstance(signal, np.ndarray)
    np.testing.assert_array_equal(frequency, np.array([12.0, 13.0]))
    np.testing.assert_array_equal(
        signal,
        np.array([complex(1.0, 0.0), complex(0.5, -0.1)]),
    )
    assert not frequency.flags.writeable
    assert not signal.flags.writeable
    with pytest.raises(ValueError, match="cannot set WRITEABLE flag"):
        frequency.flags.writeable = True
    raw_frequency = dataset.records[1].coordinates["frequency"]
    assert isinstance(raw_frequency, MeasurementArray)
    assert frequency is raw_frequency.values
    assert dataset["temperature"].values == (0.05, None, 0.2)
    assert dataset["temperature"].availability == (None, "invalid", None)
    assert dataset.point_indices == (0, 1, 2)
    assert dataset.logical_point_ids == ("logical-0", "logical-1", "logical-2")
    assert _snapshot(dataset).records[2] == dataset.records[2]
    assert _snapshot(dataset).records[2] is dataset.records[2]

    with pytest.raises(KeyError, match="no variable 'missing'"):
        _ = dataset["missing"]


def test_source_backed_dataset_stays_lazy_until_exact_rows_are_needed() -> None:
    source = _dataset()
    snapshot = MeasurementDataset(
        dataset_schema=source.schema,
        records=source.records,
        metadata=source.metadata,
    )
    calls = {"raw": 0, "projected": 0}

    def load_raw() -> MeasurementDataset:
        calls["raw"] += 1
        return snapshot

    def load_projected(
        projection: ProjectionSchema,
        batch_size: int,
    ) -> pa.RecordBatchReader:
        calls["projected"] += 1
        assert tuple(field.name for field in projection.fields) == ("voltage",)
        return source.project({"voltage": "bias"}).to_record_batch_reader(
            batch_size=batch_size
        )

    dataset = Dataset._from_source(
        schema=source.schema,
        entry=source.entry,
        load_raw=load_raw,
        load_projected_batches=load_projected,
    )

    assert dataset.dims["point"] is None
    assert "points=?" in repr(dataset)
    assert calls == {"raw": 0, "projected": 0}

    batches = list(
        dataset.project({"voltage": "bias"}).to_record_batch_reader(batch_size=2)
    )
    assert [batch.num_rows for batch in batches] == [2, 1]
    assert calls == {"raw": 0, "projected": 1}

    table = dataset.project({"voltage": "bias"}).to_arrow()
    assert table["voltage"].to_pylist() == [0.0, 1.0, 2.0]
    assert calls == {"raw": 0, "projected": 2}

    assert len(dataset) == 3
    assert len(dataset) == 3
    assert dataset.dims["point"] == 3
    assert "points=3" in repr(dataset)
    assert calls == {"raw": 1, "projected": 2}


def test_selected_schema_only_retains_a_complete_result_contract() -> None:
    schema = _dataset().schema
    contract = MeasurementResultContract(
        id="test.result",
        version=f"sha256:{'0' * 64}",
        fields=(
            MeasurementResultField(path=("bias",), variable_id="bias"),
            MeasurementResultField(
                path=("temperature",),
                variable_id="temperature",
            ),
        ),
    )
    schema = schema.model_copy(update={"result": contract})

    assert select_measurement_schema(schema, ("bias",)).result is None
    assert select_measurement_schema(schema, ("bias", "temperature")).result == contract


def test_typed_record_lookup_validates_schema_and_narrows_values() -> None:
    dataset = _dataset_with_record_sources()
    bias_ref: RecordRef[float] = RecordRef(
        id="bias",
        dtype="float64",
        unit="V",
        dims=("point",),
        role="coordinate",
    )
    signal_ref: RecordRef[MeasurementArrayData] = RecordRef(
        id="signal",
        dtype="complex128",
        unit="ratio",
        dims=("point", "sample"),
    )

    bias = dataset[bias_ref]
    signal = dataset[signal_ref]

    assert_type(bias, Variable[float])
    assert_type(signal, Variable[MeasurementArrayData])
    assert bias.require_values() == (0.0, 1.0, 2.0)
    assert bias.require_quantities("mV") == (
        Quantity(0.0, "mV"),
        Quantity(1000.0, "mV"),
        Quantity(2000.0, "mV"),
    )
    assert bias.require_magnitudes("mV") == (0.0, 1000.0, 2000.0)
    assert signal[0] is signal.values[0]
    signal_magnitudes = signal.require_magnitudes()
    assert isinstance(signal_magnitudes[0], np.ndarray)
    np.testing.assert_array_equal(
        signal_magnitudes[0],
        np.asarray([1.0 + 0.0j, 0.5 - 0.1j], dtype=np.complex128),
    )
    traces = dataset.traces(signal_ref)
    assert len(traces) == 3
    assert traces[0].coordinate_id == "frequency"
    assert traces[0].observable_id == "signal"


def test_coordinate_handle_narrows_a_dataset_coordinate() -> None:
    dataset = _dataset()
    bias_ref = coordinate("bias", QuantityType(unit="V"))

    assert_type(bias_ref, CoordinateRef[Quantity])
    bias = dataset[bias_ref]

    assert_type(bias, Variable[float])
    assert bias.require_quantities("mV")[1] == Quantity(1000.0, "mV")


def test_dataset_binds_an_experiment_result_to_typed_points() -> None:
    @dataclass(frozen=True, slots=True)
    class ResultSchema:
        bias: CoordinateRef[Quantity]
        temperature: ProductRef[float]

    base = _dataset()
    variables = tuple(
        variable.model_copy(
            update=(
                {"source_product_id": "thermometer/temperature"}
                if variable.id == "temperature"
                else {}
            )
        )
        for variable in base.schema.variables
    )
    schema_with_sources = base.schema.model_copy(update={"variables": variables})
    dataset = Dataset(
        _snapshot(base).model_copy(update={"dataset_schema": schema_with_sources}),
        base.entry,
    )
    schema = ResultSchema(
        bias=coordinate("bias", QuantityType(unit="V")),
        temperature=ProductRef.from_declaration(
            ModuleProductDecl(
                id="temperature",
                scope=("thermometer",),
                value_spec=ProductValueSpec[float](dtype="float64", unit="K"),
            )
        ),
    )

    result = dataset.bind(schema)

    assert result.output is schema
    assert_type(result[0].value(schema.bias), Quantity)
    assert_type(result[0].value(schema.temperature), float)
    assert result[0].value(schema.bias) == Quantity(0.0, "V")
    assert result[0].quantity(schema.temperature, "mK") == Quantity(50.0, "mK")
    assert result.rows(lambda point: point.quantity(schema.bias, "mV")) == (
        Quantity(0.0, "mV"),
        Quantity(1000.0, "mV"),
        Quantity(2000.0, "mV"),
    )
    assert result[0].availability(schema.temperature) is None
    assert result[0].is_available(schema.temperature)
    assert result[1].availability(schema.temperature) == "invalid"
    assert not result[1].is_available(schema.temperature)
    unavailable = result[1].unavailable(schema.temperature)
    assert unavailable is not None
    assert unavailable.metadata == {"cause": "sensor settling"}
    with pytest.raises(
        ValueError,
        match="unavailable at row position 1: invalid",
    ):
        result[1].value(schema.temperature)

    available = result.where_available(schema.temperature)
    assert available.dataset.point_indices == (0, 2)
    assert result.where_available().dataset.point_indices == (0, 2)
    assert result.where_available(schema.bias).dataset.point_indices == (0, 1, 2)
    assert available.rows(lambda point: point.value(schema.temperature)) == (
        0.05,
        0.2,
    )
    usable, rejected = result.partition_available(schema.temperature)
    assert usable.dataset.point_indices == (0, 2)
    assert rejected.dataset.point_indices == (1,)
    assert rejected[0].availability(schema.temperature) == "invalid"

    stored_schema = schema_with_sources.model_copy(
        update={
            "result": MeasurementResultContract(
                id="test.result",
                version=f"sha256:{'0' * 64}",
                fields=(
                    MeasurementResultField(path=("bias",), variable_id="bias"),
                    MeasurementResultField(
                        path=("temperature",),
                        variable_id="temperature",
                    ),
                ),
            )
        }
    )
    stored = Dataset(
        _snapshot(base).model_copy(update={"dataset_schema": stored_schema}),
        base.entry,
    ).result
    stored_usable, stored_rejected = stored.partition_available("temperature")
    assert stored_usable.dataset.point_indices == (0, 2)
    assert stored_usable.rows(lambda point: point.value("temperature")) == (0.05, 0.2)
    assert stored_rejected.dataset.point_indices == (1,)
    assert stored_rejected[0].availability("temperature") == "invalid"
    assert not stored_rejected[0].is_available("temperature")
    stored_unavailable = stored_rejected[0].unavailable("temperature")
    assert stored_unavailable is not None
    assert stored_unavailable.metadata == {"cause": "sensor settling"}

    typed_projection = result.project()
    assert tuple(field.name for field in typed_projection.schema.fields) == (
        "bias",
        "temperature",
    )
    assert tuple(field.source_path for field in typed_projection.schema.fields) == (
        ("bias",),
        ("temperature",),
    )
    assert tuple(
        field.name for field in result.project({"voltage": schema.bias}).schema.fields
    ) == ("voltage",)

    stored_projection = stored.project({"voltage": "bias", "temp": "temperature"})
    assert tuple(field.name for field in stored_projection.schema.fields) == (
        "voltage",
        "temp",
    )
    assert tuple(field.source_path for field in stored_projection.schema.fields) == (
        ("bias",),
        ("temperature",),
    )


def test_logical_product_handle_selects_its_durable_variable() -> None:
    dataset = _dataset_with_record_sources()
    temperature_ref = ProductRef.from_declaration(
        ModuleProductDecl(
            id="temperature",
            scope=("thermometer",),
            value_spec=ProductValueSpec[float](dtype="float64", unit="K"),
        )
    )

    temperature = dataset[temperature_ref]

    assert_type(temperature, Variable[float])
    assert temperature.quantities("mK") == (
        Quantity(50.0, "mK"),
        None,
        Quantity(200.0, "mK"),
    )


def test_logical_value_handle_selects_by_source_independently_of_record_name() -> None:
    temperature_ref = cast(
        "ValueRef[Quantity]",
        compute(
            "analysis-temperature",
            fn=lambda: Quantity(0.0, "K"),
            output_type=Scalar(QuantityType(unit="K")),
        ).output,
    )
    dataset = _dataset_with_value_source(
        variable_id="temperature",
        source_value_id="analysis-temperature",
    )

    temperature = dataset[temperature_ref]

    assert_type(temperature, Variable[float])
    assert temperature.unit == "K"


def test_variable_require_helpers_reject_unavailable_rows() -> None:
    dataset = _dataset_with_record_sources()
    temperature_ref: RecordRef[float] = RecordRef(
        id="temperature",
        dtype="float64",
        unit="K",
        dims=("point",),
    )
    temperature = dataset[temperature_ref]

    assert temperature.quantities("mK") == (
        Quantity(50.0, "mK"),
        None,
        Quantity(200.0, "mK"),
    )
    with pytest.raises(ValueError, match="row positions: 1"):
        temperature.require_values()
    with pytest.raises(ValueError, match="row positions: 1"):
        temperature.require_quantities()


@pytest.mark.parametrize(
    "ref",
    [
        RecordRef[float](
            id="bias",
            dtype="int64",
            unit="V",
            dims=("point",),
            role="coordinate",
        ),
        RecordRef[float](
            id="bias",
            dtype="float64",
            unit="A",
            dims=("point",),
            role="coordinate",
        ),
        RecordRef[float](
            id="bias",
            dtype="float64",
            unit="V",
            dims=("point", "sample"),
            role="coordinate",
        ),
        RecordRef[float](
            id="bias",
            dtype="float64",
            unit="V",
            dims=("point",),
        ),
    ],
)
def test_typed_record_lookup_rejects_schema_drift(ref: RecordRef[float]) -> None:
    dataset = _dataset_with_record_sources()

    with pytest.raises(TypeError, match="does not match the dataset schema"):
        _ = dataset[ref]


def test_dataset_supports_point_isel_sel_and_unit_aware_where() -> None:
    dataset = _dataset()

    selected = dataset.isel(point=[2, 0])
    assert [record.point_index for record in selected.records] == [2, 0]
    assert selected.dims["point"] == 2
    assert (
        next(
            dimension.size
            for dimension in selected.schema.dimensions
            if dimension.id == "point"
        )
        == 3
    )
    assert selected["bias"].values == (2.0, 0.0)

    [exact] = dataset.sel(bias=Quantity(1000.0, "mV")).records
    assert exact.point_index == 1
    [logical] = dataset.sel(point="logical-2").records
    assert logical.point_index == 2
    [nearest] = dataset.sel(
        bias=Quantity(1.6, "V"),
        method="nearest",
        tolerance=Quantity(0.5, "V"),
    ).records
    assert nearest.point_index == 2

    mask = (dataset["bias"] >= Quantity(1.0, "V")) & dataset[
        "temperature"
    ].is_available()
    assert isinstance(mask, PointMask)
    filtered = dataset.where(mask)
    assert [record.point_index for record in filtered.records] == [2]
    assert len(dataset.where(lambda current: current["bias"] < 1.5)) == 2
    grouped = dataset.groupby("bias")
    assert tuple(grouped) == (0.0, 1.0, 2.0)
    assert all(type(key) is float for key in grouped)
    assert grouped[1.0].records == (dataset.records[1],)

    with pytest.raises(KeyError, match="not all values found"):
        dataset.sel(
            bias=Quantity(1.6, "V"),
            method="nearest",
            tolerance=Quantity(0.1, "V"),
        )
    with pytest.raises(ValueError, match="point predicates require a scalar"):
        _ = dataset["frequency"] > 10.0
    with pytest.raises(ValueError, match="point predicates require a scalar"):
        dataset.groupby("signal")


@pytest.mark.parametrize(
    ("indexer", "expected_frequency", "expected_size"),
    [
        (1, ((11.0,), (13.0,), (15.0,)), 1),
        (slice(1, None), ((11.0,), (13.0,), (15.0,)), 1),
        ([1, 0], ((11.0, 10.0), (13.0, 12.0), (15.0, 14.0)), 2),
        ([True, False], ((10.0,), (12.0,), (14.0,)), 1),
    ],
)
def test_dataset_isel_selects_fixed_local_dimensions_without_dropping_them(
    indexer: int | slice | list[int] | list[bool],
    expected_frequency: tuple[tuple[float, ...], ...],
    expected_size: int,
) -> None:
    selected = _dataset().isel(sample=indexer)

    assert selected.dims == {"point": 3, "sample": expected_size}
    assert (
        next(
            dimension.size
            for dimension in selected.schema.dimensions
            if dimension.id == "sample"
        )
        == 2
    )
    _assert_array_values(selected["frequency"].values, expected_frequency)
    assert all(
        isinstance(value, np.ndarray) and len(value) == expected_size
        for value in selected["signal"].values
    )


def test_dataset_isel_combines_point_and_fixed_local_selection() -> None:
    selected = _dataset().isel(point=[2, 0], sample=[1])

    assert [record.point_index for record in selected.records] == [2, 0]
    assert selected.dims == {"point": 2, "sample": 1}
    _assert_array_values(selected["frequency"].values, ((15.0,), (11.0,)))


@pytest.mark.parametrize("source_backed", [False, True])
def test_arrow_and_record_reads_do_not_build_an_unused_xarray(
    monkeypatch: pytest.MonkeyPatch,
    source_backed: bool,
) -> None:
    builds = 0
    original = Dataset._build_xarray

    def build(dataset: Dataset) -> xr.Dataset:
        nonlocal builds
        builds += 1
        return original(dataset)

    monkeypatch.setattr(Dataset, "_build_xarray", build)
    dataset = _dataset()
    if source_backed:
        source = dataset

        def unexpected_projection(
            _projection: ProjectionSchema,
            _batch_size: int,
        ) -> pa.RecordBatchReader:
            raise AssertionError("materialized records should use local projection")

        dataset = Dataset._from_source(
            schema=source.schema,
            entry=source.entry,
            load_raw=lambda: _snapshot(source),
            load_projected_batches=unexpected_projection,
        )
    assert dataset.records[0].point_index == 0
    assert dataset.project({"voltage": "bias"}).to_arrow().num_rows == 3
    assert builds == 0
    first = dataset.to_xarray()
    second = dataset.to_xarray()
    assert builds == 1
    assert first is not second
    assert first.identical(second)


def test_large_xarray_entity_metadata_preserves_selected_order() -> None:
    entities = tuple(
        EntityRef(id=f"q{i}", kind="qubit", metadata={"labels": [i, "replay"]})
        for i in range(128)
    )
    dataset = _entity_source_dataset(
        "large", entities, tuple(float(i) for i in range(128))
    )
    index = dataset.schema.dimensions[1].index
    assert index is not None
    expected = index.model_dump(mode="json")["values"]
    labeled = dataset.isel(qubit=[127, 0, 2]).to_xarray()
    assert json.loads(labeled.coords["qubit"].attrs["scopecat_entities_json"]) == [
        expected[position] for position in (127, 0, 2)
    ]


def test_dataset_native_xarray_preserves_labels_shapes_and_availability() -> None:
    dataset = _dataset()

    xarray_dataset = dataset.to_xarray()
    assert isinstance(xarray_dataset, xr.Dataset)
    assert xarray_dataset is not dataset.to_xarray()
    assert dataset["signal"].xarray.identical(xarray_dataset["signal"])
    assert xarray_dataset.sizes == {"point": 3, "sample": 2}
    assert tuple(xarray_dataset["frequency"].dims) == ("point", "sample")
    assert xarray_dataset["frequency"].attrs["units"] == "Hz"
    assert xarray_dataset["signal"].values[0, 1] == complex(0.5, -0.1)
    assert math.isnan(float(xarray_dataset["temperature"].values[1]))
    assert xarray_dataset["temperature__unavailable_reason"].values[1] == "invalid"
    assert xarray_dataset.attrs["scopecat_entry_id"] == dataset.entry.id
    assert xarray_dataset.attrs["scopecat_content_hash"] == dataset.entry.content_hash
    schema = json.loads(xarray_dataset.attrs["scopecat_schema_json"])
    assert schema["dataset_id"] == dataset.schema.dataset_id
    metadata = json.loads(xarray_dataset.attrs["scopecat_metadata_json"])
    assert metadata["context"]["tags"] == ["xarray", "netcdf"]
    variable_metadata = json.loads(
        xarray_dataset["bias"].attrs["scopecat_metadata_json"]
    )
    assert variable_metadata == {"calibration": {"revision": 2, "source": "smu"}}


def test_entity_dimensions_support_labeled_selection_and_partial_availability() -> None:
    q0 = EntityRef(id="q0", kind="qubit")
    q1 = EntityRef(id="q1", kind="qubit")
    q2 = EntityRef(id="q2", kind="qubit")
    availability = MeasurementArrayAvailability.create(
        valid=np.asarray([True, False, True], dtype=np.bool_),
        reason="missing",
        metadata={"source": "q1"},
    )
    schema = MeasurementDatasetSchema(
        dataset_id="entity-readout",
        point_domain=MeasurementPointCloudPointDomain(columns=()),
        dimensions=(
            MeasurementDimension(id="point", kind="point", size=2),
            MeasurementDimension(
                id="qubit",
                kind="entity",
                size=3,
                index=MeasurementEntityIndex(
                    values=(q0, q1, q2),
                ),
            ),
        ),
        variables=(
            MeasurementVariable(
                id="readout",
                role="observable",
                dtype="float64",
                unit="V",
                dims=("point", "qubit"),
            ),
        ),
        primary_observables=("readout",),
    )
    raw = MeasurementDataset(
        dataset_schema=schema,
        records=tuple(
            MeasurementRecord(
                run_id="run-entity",
                point_index=point_index,
                coordinates={},
                observables={
                    "readout": MeasurementArray.create(
                        values=np.asarray(values, dtype=np.float64),
                        unit="V",
                        availability=selected_availability,
                    )
                },
            )
            for point_index, values, selected_availability in (
                (0, (1.0, 0.0, 3.0), availability),
                (1, (4.0, 5.0, 6.0), None),
            )
        ),
    )
    dataset = Dataset(
        raw,
        ContentEntry(
            role="dataset",
            id="entity-readout",
            kind="measurement_dataset",
            content_hash="unused-entity-readout",
            schema=schema.model_dump(mode="json"),
        ),
    )

    first = dataset["readout"].values[0]
    assert isinstance(first, np.ma.MaskedArray)
    assert first.mask.tolist() == [False, True, False]
    magnitudes = dataset["readout"].require_magnitudes("mV")
    assert isinstance(magnitudes[0], np.ma.MaskedArray)
    assert magnitudes[0].tolist(fill_value=None) == [1000.0, None, 3000.0]
    assert isinstance(magnitudes[1], np.ndarray)
    np.testing.assert_array_equal(magnitudes[1], np.asarray([4000.0, 5000.0, 6000.0]))
    assert not magnitudes[1].flags.writeable
    assert dataset["readout"].is_available()._values == (False, True)
    labeled = dataset.to_xarray()
    entity_index = schema.dimensions[1].index
    assert entity_index is not None
    assert labeled.coords["qubit"].values.tolist() == ["q0", "q1", "q2"]
    assert (
        labeled.coords["qubit"].attrs["scopecat_entity_axis_fingerprint"]
        == entity_index.fingerprint
    )
    assert json.loads(labeled.coords["qubit"].attrs["scopecat_entity_labels_json"]) == [
        "q0",
        "q1",
        "q2",
    ]
    assert labeled["readout__valid"].values.tolist() == [
        [True, False, True],
        [True, True, True],
    ]
    assert labeled["readout__unavailable_reason"].values[0, 1] == "missing"
    assert math.isnan(float(labeled["readout"].values[0, 1]))

    selected = dataset.sel(qubit=q1)
    assert selected.dims == {"point": 2, "qubit": 1}
    assert selected.to_xarray().coords["qubit"].values.tolist() == ["q1"]
    assert isinstance(
        selected.records[0].observables["readout"],
        MeasurementUnavailable,
    )
    np.testing.assert_array_equal(
        selected["readout"].values[1],
        np.asarray([5.0], dtype=np.float64),
    )

    points = dataset.project(
        {"readout": "readout"},
        diagnostics="reason",
    ).to_arrow()
    assert points["readout"].to_pylist() == [
        [1.0, None, 3.0],
        [4.0, 5.0, 6.0],
    ]
    assert points["readout__unavailable_reason"].to_pylist() == [
        "partial",
        None,
    ]
    observations = dataset.project(
        {"readout": "readout"},
        diagnostics="reason",
        layout="observations",
    ).to_arrow()
    assert observations["readout"].to_pylist() == [1.0, None, 3.0, 4.0, 5.0, 6.0]
    assert observations["readout__unavailable_reason"].to_pylist()[1] == "missing"

    expanded = dataset.reindex_entities(
        "qubit",
        (q2, q1, EntityRef(id="q3", kind="qubit")),
    )
    expanded_value = expanded.records[0].observables["readout"]
    assert isinstance(expanded_value, MeasurementArray)
    assert expanded_value.values.tolist() == [3.0, 0.0, 0.0]
    assert expanded_value.availability is not None
    assert expanded_value.availability.valid.tolist() == [True, False, False]
    assert [group.metadata for group in expanded_value.availability.unavailable] == [
        {"source": "q1"},
        {"entity_alignment": "absent", "dimension_id": "qubit"},
    ]


def test_mixed_entity_kinds_use_collision_free_coordinate_identities() -> None:
    kindless = EntityRef(id="qubit:q0")
    qubit = EntityRef(id="q0", kind="qubit")
    schema = MeasurementDatasetSchema(
        dataset_id="mixed-entities",
        point_domain=MeasurementPointCloudPointDomain(columns=()),
        dimensions=(
            MeasurementDimension(id="point", kind="point", size=1),
            MeasurementDimension(
                id="entity",
                kind="entity",
                size=2,
                index=MeasurementEntityIndex(values=(kindless, qubit)),
            ),
        ),
        variables=(
            MeasurementVariable(
                id="signal",
                role="observable",
                dtype="float64",
                dims=("point", "entity"),
            ),
        ),
        primary_observables=("signal",),
    )
    raw = MeasurementDataset(
        dataset_schema=schema,
        records=(
            MeasurementRecord(
                run_id="mixed",
                point_index=0,
                coordinates={},
                observables={
                    "signal": MeasurementArray.create(
                        values=np.asarray([1.0, 2.0]),
                    )
                },
            ),
        ),
    )
    dataset = Dataset(
        raw,
        ContentEntry(
            role="dataset",
            id="mixed-entities",
            kind="measurement_dataset",
            content_hash="unused-mixed-entities",
            schema=schema.model_dump(mode="json"),
        ),
    )

    labels = dataset.to_xarray().coords["entity"].values.tolist()
    assert labels == [entity_identity_key(kindless), entity_identity_key(qubit)]
    assert len(set(labels)) == 2
    selected_signal = dataset.sel(entity="q0")["signal"].values[0]
    assert isinstance(selected_signal, np.ndarray)
    assert selected_signal.tolist() == [2.0]


def test_entity_alignment_reindexes_values_provenance_and_evidence() -> None:
    q0 = EntityRef(id="q0", kind="qubit")
    q1 = EntityRef(id="q1", kind="qubit")
    q2 = EntityRef(id="q2", kind="qubit")
    left = _entity_source_dataset("left", (q0, q1), (1.0, 2.0))
    right = _entity_source_dataset("right", (q1, q2), (10.0, 20.0))
    left_index = left.schema.dimensions[1].index
    assert left_index is not None
    stale_ref: RecordRef[MeasurementArrayData] = RecordRef(
        id="signal",
        dtype="float64",
        unit=None,
        dims=("point", "qubit"),
        entity_axis_id="qubit",
        entity_axis_fingerprint=left_index.fingerprint,
    )

    with pytest.raises(ValueError, match="ordered identities differ"):
        left.align_entities(right, "qubit")

    inner_left, inner_right = left.align_entities(right, "qubit", join="inner")
    assert inner_left.to_xarray().coords["qubit"].values.tolist() == ["q1"]
    inner_left_signal = inner_left["signal"].values[0]
    inner_right_signal = inner_right["signal"].values[0]
    assert isinstance(inner_left_signal, np.ndarray)
    assert isinstance(inner_right_signal, np.ndarray)
    assert inner_left_signal.tolist() == [2.0]
    assert inner_right_signal.tolist() == [10.0]

    outer_left, outer_right = left.align_entities(right, "qubit", join="outer")
    assert outer_left.entry.content_hash != left.entry.content_hash
    assert outer_right.entry.content_hash != right.entry.content_hash
    assert outer_left.entry.metadata["scopecat_derivation"] == {
        "source_content_hash": left.entry.content_hash,
        "operation": {
            "kind": "reindex_entities",
            "dimension_id": "qubit",
            "entities": [
                {"id": "q0", "kind": "qubit", "metadata": {}},
                {"id": "q1", "kind": "qubit", "metadata": {}},
                {"id": "q2", "kind": "qubit", "metadata": {}},
            ],
        },
    }
    assert outer_left.to_xarray().coords["qubit"].values.tolist() == [
        "q0",
        "q1",
        "q2",
    ]
    assert outer_right.to_xarray().coords["qubit"].values.tolist() == [
        "q0",
        "q1",
        "q2",
    ]
    outer_left_signal = outer_left["signal"].values[0]
    outer_right_signal = outer_right["signal"].values[0]
    assert isinstance(outer_left_signal, np.ma.MaskedArray)
    assert isinstance(outer_right_signal, np.ma.MaskedArray)
    assert outer_left_signal.tolist(fill_value=None) == [1.0, 2.0, None]
    assert outer_right_signal.tolist(fill_value=None) == [
        None,
        10.0,
        20.0,
    ]
    left_reasons = outer_left.to_xarray()["signal__unavailable_reason"].values
    assert left_reasons[0, 2] == "missing"
    left_source = outer_left["signal"].definition.source_entity_products
    right_source = outer_right["signal"].definition.source_entity_products
    assert left_source is not None and right_source is not None
    assert left_source.product_ids == ("left/q0", "left/q1", None)
    assert right_source.product_ids == (None, "right/q1", "right/q2")
    left_outer_index = outer_left.schema.dimensions[1].index
    right_outer_index = outer_right.schema.dimensions[1].index
    assert left_outer_index is not None and right_outer_index is not None
    assert left_outer_index.fingerprint == right_outer_index.fingerprint
    assert outer_left.schema.result is not None and left.schema.result is not None
    assert outer_left.schema.result.version != left.schema.result.version
    left_evidence = outer_left.records[0].acquisition_evidence.for_variable("signal")
    right_evidence = outer_right.records[0].acquisition_evidence.for_variable("signal")
    assert isinstance(left_evidence, EntityAcquisitionEvidence)
    assert isinstance(right_evidence, EntityAcquisitionEvidence)
    left_evidence_ids = tuple(
        None if item is None else item.result_id for item in left_evidence.values
    )
    assert left_evidence_ids == ("q0", "q1", None)
    assert [
        None if item is None else item.result_id for item in right_evidence.values
    ] == [None, "q1", "q2"]
    assert left.schema.dimensions[1].size == 2
    with pytest.raises(TypeError, match="does not match the dataset schema"):
        outer_left[stale_ref]

    expanded_view = left.sel(qubit=q1).reindex_entities("qubit", (q1, q2))
    repeated = left.sel(qubit=q1).reindex_entities("qubit", (q1, q2))
    assert expanded_view.entry.content_hash == repeated.entry.content_hash
    assert expanded_view.to_xarray().attrs["scopecat_content_hash"] == (
        expanded_view.entry.content_hash
    )
    view_source = expanded_view["signal"].definition.source_entity_products
    view_evidence = expanded_view.records[0].acquisition_evidence.for_variable("signal")
    assert view_source is not None
    assert view_source.product_ids == ("left/q1", None)
    assert isinstance(view_evidence, EntityAcquisitionEvidence)
    assert [
        None if item is None else item.result_id for item in view_evidence.values
    ] == ["q1", None]


def _entity_source_dataset(
    dataset_id: str,
    entities: tuple[EntityRef, ...],
    values: tuple[float, ...],
) -> Dataset:
    acquisition = MeasurementEntityAcquisition()
    schema = MeasurementDatasetSchema(
        dataset_id=dataset_id,
        point_domain=MeasurementPointCloudPointDomain(columns=()),
        dimensions=(
            MeasurementDimension(id="point", kind="point", size=1),
            MeasurementDimension(
                id="qubit",
                kind="entity",
                size=len(entities),
                index=MeasurementEntityIndex(values=entities),
            ),
        ),
        variables=(
            MeasurementVariable(
                id="signal",
                role="observable",
                dtype="float64",
                dims=("point", "qubit"),
                source_entity_products=MeasurementEntityProductSource(
                    dimension_id="qubit",
                    product_ids=tuple(
                        f"{dataset_id}/{entity.id}" for entity in entities
                    ),
                ),
                entity_acquisition=acquisition,
            ),
        ),
        primary_observables=("signal",),
        result=MeasurementResultContract(
            id=f"{dataset_id}.result",
            version=f"sha256:{'0' * 64}",
            fields=(MeasurementResultField(path=("signal",), variable_id="signal"),),
        ),
    )
    started_at = datetime(2026, 8, 15, 10, 0, tzinfo=UTC)
    evidence = EntityAcquisitionEvidence(
        dimension_id="qubit",
        acquisition=acquisition,
        values=tuple(
            InstrumentAcquisitionEvidence(
                command_id=f"{dataset_id}-{entity.id}",
                instrument_id="readout",
                interface_id="test.signal/v1",
                acquisition_id="sample",
                result_id=entity.id,
                started_at=started_at,
                completed_at=started_at,
            )
            for entity in entities
        ),
    )
    raw = MeasurementDataset(
        dataset_schema=schema,
        records=(
            MeasurementRecord(
                run_id=f"run-{dataset_id}",
                point_index=0,
                coordinates={},
                observables={
                    "signal": MeasurementArray.create(
                        values=np.asarray(values, dtype=np.float64)
                    )
                },
                acquisition_evidence=MeasurementAcquisitionEvidenceCatalog.create(
                    {"signal": evidence}
                ),
            ),
        ),
    )
    return Dataset(
        raw,
        ContentEntry(
            role="dataset",
            id=dataset_id,
            kind="measurement_dataset",
            content_hash=f"unused-{dataset_id}",
            schema=schema.model_dump(mode="json"),
        ),
    )


def test_measurement_projection_controls_names_units_and_native_adapters() -> None:
    pd = pytest.importorskip("pandas")
    projection = _dataset().project(
        {"voltage": "bias", "temp": "temperature", "response": "signal"},
        units={"voltage": "mV", "temp": "mK", "response": "ratio"},
        diagnostics="full",
    )

    assert tuple(field.name for field in projection.schema.fields) == (
        "voltage",
        "temp",
        "response",
    )
    assert projection.schema.fields[0].variable_id == "bias"
    assert projection.schema.fields[0].unit == "mV"
    assert projection.schema.fields[0].role == "coordinate"
    assert projection.schema.fields[2].dims == ("point", "sample")
    assert projection.units == {
        "voltage": "mV",
        "temp": "mK",
        "response": "ratio",
    }

    table = projection.to_arrow()
    assert table.column_names == [
        "point_index",
        "logical_point_id",
        "voltage",
        "voltage__unavailable_reason",
        "voltage__unavailable_metadata",
        "temp",
        "temp__unavailable_reason",
        "temp__unavailable_metadata",
        "response",
        "response__unavailable_reason",
        "response__unavailable_metadata",
    ]
    assert table["voltage"].to_pylist() == [0.0, 1000.0, 2000.0]
    assert table["temp"].to_pylist() == [50.0, None, 200.0]
    assert table["temp__unavailable_reason"].to_pylist() == [
        None,
        "invalid",
        None,
    ]
    assert json.loads(table["temp__unavailable_metadata"][1].as_py()) == {
        "cause": "sensor settling"
    }
    voltage_field = table.schema.field("voltage")
    assert voltage_field.metadata[b"scopecat.variable_id"] == b"bias"
    assert voltage_field.metadata[b"units"] == b"mV"
    assert voltage_field.metadata[b"scopecat.role"] == b"coordinate"
    encoded_projection = json.loads(
        table.schema.metadata[b"scopecat.projection"].decode()
    )
    assert encoded_projection["fields"][1]["name"] == "temp"

    frame = projection.to_pandas()
    assert isinstance(frame, pd.DataFrame)
    assert list(frame["voltage"]) == [0.0, 1000.0, 2000.0]
    assert frame.loc[1, "temp__unavailable_reason"] == "invalid"
    np.testing.assert_array_equal(
        frame.loc[0, "response"],
        np.array([complex(1.0, 0.0), complex(0.5, -0.1)]),
    )
    assert frame.attrs["scopecat"]["schema_id"] == (
        "scopecat.measurement-data-projection.v2"
    )

    arrow_frame = projection.to_pandas(dtype_backend="pyarrow")
    assert "pyarrow" in str(arrow_frame["temp"].dtype)

    pl = pytest.importorskip("polars")
    polars_frame = projection.to_polars()
    assert isinstance(polars_frame, pl.DataFrame)
    assert polars_frame.columns == table.column_names
    assert polars_frame["voltage"].to_list() == [0.0, 1000.0, 2000.0]

    labeled = projection.to_xarray()
    assert isinstance(labeled, xr.Dataset)
    assert tuple(labeled["voltage"].dims) == ("point",)
    assert tuple(labeled["response"].dims) == ("point", "sample")
    assert labeled["voltage"].attrs["units"] == "mV"
    assert labeled["temp"].values[2] == 200.0
    assert labeled["temp__unavailable_reason"].values[1] == "invalid"
    assert "bias" not in labeled.variables
    assert "temperature" not in labeled.variables

    batches = list(projection.to_record_batch_reader(batch_size=2))
    assert [batch.num_rows for batch in batches] == [2, 1]
    assert all(batch.schema == table.schema for batch in batches)


def test_pandas_projection_reports_units_inherited_as_plain_magnitudes() -> None:
    pytest.importorskip("pandas")
    projection = _dataset().project(
        {"delay": "bias", "response": "signal"},
        identity=False,
    )

    with pytest.warns(
        ImplicitPandasUnitWarning,
        match=(
            r"plain magnitudes.*delay \[V\], response \[ratio\].*"
            r"project\(\.\.\., units=\{\.\.\.\}\)"
        ),
    ):
        frame = projection.to_pandas()

    assert list(frame["delay"]) == [0.0, 1.0, 2.0]


def test_explicit_same_units_silence_pandas_magnitude_warning() -> None:
    pytest.importorskip("pandas")
    projection = _dataset().project(
        {"delay": "bias", "response": "signal"},
        units={"delay": "V", "response": "ratio"},
        identity=False,
    )

    with warnings.catch_warnings():
        warnings.simplefilter("error", ImplicitPandasUnitWarning)
        frame = projection.to_pandas()

    assert list(frame["delay"]) == [0.0, 1.0, 2.0]
    assert projection.implicit_units == {}


def test_projection_exposes_final_units_without_materializing_an_adapter() -> None:
    projection = _dataset().project(
        {"delay": "bias", "response": "signal"},
        units={"delay": "mV"},
        identity=False,
    )

    assert projection.units == {"delay": "mV", "response": "ratio"}
    assert projection.schema.units == projection.units
    assert projection.implicit_units == {"response": "ratio"}


def test_projection_diagnostics_have_one_schema_across_availability_slices() -> None:
    dataset = _dataset()

    available = (
        dataset.isel(point=[0])
        .project(
            {"temp": "temperature"},
            diagnostics="reason",
        )
        .to_arrow()
    )
    unavailable = (
        dataset.isel(point=[1])
        .project(
            {"temp": "temperature"},
            diagnostics="reason",
        )
        .to_arrow()
    )

    assert available.schema == unavailable.schema
    assert available["temp__unavailable_reason"].to_pylist() == [None]
    assert unavailable["temp__unavailable_reason"].to_pylist() == ["invalid"]


def test_observations_projection_aligns_ragged_arrays_and_broadcasts_scalars() -> None:
    pd = pytest.importorskip("pandas")
    dataset = _ragged_dataset()
    unavailable = MeasurementUnavailable.create(
        reason="missing",
        dtype="complex128",
        unit="ratio",
        shape=(None,),
        metadata={"cause": "fit rejected"},
    )
    raw = _snapshot(dataset).model_copy(
        update={
            "records": (
                dataset.records[0],
                _replace_record_values(
                    dataset.records[1],
                    observables={"signal": unavailable},
                ),
                dataset.records[2],
            )
        }
    )
    projection = Dataset(raw, dataset.entry).project(
        {
            "voltage": "bias",
            "frequency": "frequency",
            "response": "signal",
        },
        layout="observations",
        diagnostics="full",
    )

    table = projection.to_arrow()
    assert table.num_rows == 6
    assert table.column_names == [
        "point_index",
        "logical_point_id",
        "sample_index",
        "voltage",
        "voltage__unavailable_reason",
        "voltage__unavailable_metadata",
        "frequency",
        "frequency__unavailable_reason",
        "frequency__unavailable_metadata",
        "response",
        "response__unavailable_reason",
        "response__unavailable_metadata",
    ]
    assert table["point_index"].to_pylist() == [10, 10, 20, 40, 40, 40]
    assert table["sample_index"].to_pylist() == [0, 1, 0, 0, 1, 2]
    assert table["voltage"].to_pylist() == [0.0, 0.0, 1.0, 2.0, 2.0, 2.0]
    assert table["frequency"].to_pylist() == [0.0, 1.0, 10.0, 20.0, 21.0, 22.0]
    assert table["response__unavailable_reason"].to_pylist() == [
        None,
        None,
        "missing",
        None,
        None,
        None,
    ]

    frame = projection.with_units(
        voltage="V",
        frequency="Hz",
        response="ratio",
    ).to_pandas()
    assert isinstance(frame, pd.DataFrame)
    assert frame.loc[2, "response"] is None
    assert frame.loc[4, "response"] == complex(2.0, 1.0)


def test_observations_projection_rejects_unaligned_array_selections() -> None:
    dataset = _dataset()

    with pytest.raises(ValueError, match="at least one array field"):
        dataset.project({"voltage": "bias"}, layout="observations")

    with pytest.raises(ValueError, match="one recording group"):
        dataset.project(
            {"frequency": "frequency", "response": "signal"},
            layout="observations",
        )


@pytest.mark.parametrize(
    ("dtype", "available", "pandas_dtype"),
    [
        ("int64", (1, 3), "Int64"),
        ("bool", (True, False), "boolean"),
        ("string", ("one", "three"), "string"),
    ],
)
def test_projection_pandas_nullable_dtypes_are_stable_across_batches(
    dtype: MeasurementDType,
    available: tuple[object, object],
    pandas_dtype: str,
) -> None:
    pytest.importorskip("pandas")
    base = _dataset()
    variables = tuple(
        variable.model_copy(update={"dtype": dtype, "unit": None})
        if variable.id == "temperature"
        else variable
        for variable in base.schema.variables
    )
    records = tuple(
        _replace_record_values(
            record,
            observables={
                "temperature": (
                    MeasurementUnavailable.create(
                        reason="missing",
                        dtype=dtype,
                        unit=None,
                        shape=(),
                        metadata={},
                    )
                    if position == 1
                    else MeasurementScalar.create(
                        value=available[0 if position == 0 else 1],
                        dtype=dtype,
                    )
                )
            },
        )
        for position, record in enumerate(base.records)
    )
    dataset = Dataset(
        _snapshot(base).model_copy(
            update={
                "dataset_schema": base.schema.model_copy(
                    update={"variables": variables}
                ),
                "records": records,
            }
        ),
        base.entry,
    )

    available_frame = (
        dataset.isel(point=[0]).project({"value": "temperature"}).to_pandas()
    )
    missing_frame = (
        dataset.isel(point=[1]).project({"value": "temperature"}).to_pandas()
    )

    assert str(available_frame["value"].dtype) == pandas_dtype
    assert str(missing_frame["value"].dtype) == pandas_dtype


def test_atomic_projection_options_resolve_or_reject_generated_names() -> None:
    dataset = _dataset()

    projection = dataset.project(
        {"point_index": "bias"},
        identity=False,
    )
    assert projection.to_arrow().column_names == ["point_index"]

    with pytest.raises(ValueError, match="generated column names must be unique"):
        dataset.project(
            {
                "temp": "temperature",
                "temp__unavailable_reason": "bias",
            },
            diagnostics="reason",
        )


def test_empty_arrow_export_keeps_declared_scientific_types() -> None:
    base = _dataset()
    raw = _snapshot(base).model_copy(update={"records": ()})

    table = Dataset(raw, base.entry).project().to_arrow()
    complex_type = pa.struct(
        [
            pa.field("real", pa.float64(), nullable=False),
            pa.field("imag", pa.float64(), nullable=False),
        ]
    )

    assert table.num_rows == 0
    assert table.schema.field("point_index").type == pa.int64()
    assert table.schema.field("logical_point_id").type == pa.string()
    assert table.schema.field("bias").type == pa.float64()
    assert table.schema.field("frequency").type == pa.list_(pa.float64(), 2)
    assert table.schema.field("temperature").type == pa.float64()
    assert table.schema.field("signal").type == pa.list_(complex_type, 2)


def test_all_unavailable_arrow_column_keeps_declared_nested_type() -> None:
    base = _dataset()
    unavailable = MeasurementUnavailable.create(
        reason="missing",
        dtype="complex128",
        unit="ratio",
        shape=(2,),
        metadata={},
    )
    raw = _snapshot(base).model_copy(
        update={
            "records": tuple(
                _replace_record_values(
                    record,
                    observables={"signal": unavailable},
                )
                for record in base.records
            )
        }
    )

    table = Dataset(raw, base.entry).project(diagnostics="reason").to_arrow()

    complex_type = pa.struct(
        [
            pa.field("real", pa.float64(), nullable=False),
            pa.field("imag", pa.float64(), nullable=False),
        ]
    )
    assert table.schema.field("signal").type == pa.list_(complex_type, 2)
    assert table["signal"].null_count == 3
    assert table["signal__unavailable_reason"].to_pylist() == [
        "missing",
        "missing",
        "missing",
    ]


def test_product_grid_xarray_layout_restores_axis_dimensions_and_record_order() -> None:
    dataset = _product_grid_dataset()

    points = dataset.to_xarray()
    grid = dataset.to_xarray(layout="grid")

    assert points.sizes == {"point": 6, "sample": 2}
    assert grid.sizes == {"x": 2, "y": 3, "sample": 2}
    assert tuple(grid["x"].dims) == ("x",)
    assert tuple(grid["y"].dims) == ("y",)
    assert grid["x"].attrs["units"] == "V"
    np.testing.assert_array_equal(grid["x"].values, np.array([0.0, 1.0]))
    np.testing.assert_array_equal(grid["y"].values, np.array([10, 20, 30]))
    assert tuple(grid["signal"].dims) == ("x", "y")
    np.testing.assert_array_equal(
        grid["signal"].values,
        np.arange(6, dtype=np.float64).reshape(2, 3),
    )
    assert tuple(grid["trace"].dims) == ("x", "y", "sample")
    np.testing.assert_array_equal(
        grid["point"].values,
        np.arange(6, dtype=np.int64).reshape(2, 3),
    )
    assert grid.attrs["scopecat_xarray_layout"] == "product_grid"


def test_product_grid_xarray_layout_rejects_partial_or_inconsistent_grids() -> None:
    dataset = _product_grid_dataset()

    with pytest.raises(ValueError, match="every product-grid point exactly once"):
        dataset.isel(point=slice(0, 5)).to_xarray(layout="grid")

    raw = _snapshot(dataset).model_copy(
        update={
            "records": (
                _replace_record_values(
                    dataset.records[0],
                    coordinates={
                        "x": MeasurementScalar.create(
                            value=99.0,
                            dtype="float64",
                            unit="V",
                        )
                    },
                ),
                *dataset.records[1:],
            )
        }
    )
    inconsistent = Dataset(raw, dataset.entry)
    with pytest.raises(ValueError, match="does not match its product-grid axis"):
        inconsistent.to_xarray(layout="grid")


def test_dataset_shares_immutable_models_and_detaches_mutable_entry() -> None:
    original = _dataset()
    source = _snapshot(original)
    entry = original.entry
    dataset = Dataset(source, entry)

    assert dataset.schema is source.dataset_schema
    assert dataset.records is source.records
    assert dataset["bias"].definition is source.dataset_schema.variables[0]
    assert (
        dataset["temperature"].raw_values[1]
        is source.records[1].observables["temperature"]
    )
    assert dataset.metadata is source.metadata
    assert dataset["bias"].metadata is source.dataset_schema.variables[0].metadata

    entry.id = "mutated-entry"
    assert dataset.entry.id == "raw-measurements"
    detached_entry = dataset.entry
    detached_entry.id = "mutated-output"
    assert dataset.entry.id == "raw-measurements"
    assert dataset.metadata["context"] == {
        "operator": "test",
        "tags": ("xarray", "netcdf"),
    }


def test_xarray_exports_are_independent_copies_of_cached_snapshot() -> None:
    dataset = _dataset()
    first = dataset.to_xarray()

    first["bias"].values[0] = -100.0
    first.attrs["scopecat_dataset_id"] = "mutated"

    refreshed = dataset.to_xarray()
    assert refreshed is not first
    assert float(refreshed["bias"].values[0]) == 0.0
    assert float(first["bias"].values[0]) == -100.0
    assert refreshed.attrs["scopecat_dataset_id"] == "raw-measurements"


def test_xarray_snapshot_round_trips_through_netcdf(tmp_path: Path) -> None:
    dataset = _dataset()
    path = tmp_path / "measurements.nc"

    serializable = dataset.to_xarray().drop_vars("signal")
    serializable.to_netcdf(path)
    restored = xr.load_dataset(path)

    schema = json.loads(restored.attrs["scopecat_schema_json"])
    assert schema["dataset_id"] == dataset.schema.dataset_id
    assert json.loads(restored.attrs["scopecat_metadata_json"])["context"] == {
        "operator": "test",
        "tags": ["xarray", "netcdf"],
    }
    assert (
        json.loads(restored["bias"].attrs["scopecat_metadata_json"])["calibration"][
            "revision"
        ]
        == 2
    )
    assert restored.attrs["scopecat_entry_id"] == dataset.entry.id
    assert float(restored["frequency"].values[0, 1]) == 11.0


def test_ragged_dataset_exports_nested_arrow_lists() -> None:
    dataset = _ragged_dataset()

    arrow = dataset.project().to_arrow()
    assert isinstance(arrow, pa.Table)
    assert [len(value.as_py()) for value in arrow["signal"]] == [2, 1, 3]


def test_ragged_dataset_exports_indexed_xarray_observations() -> None:
    dataset = _ragged_dataset()

    xarray_dataset = dataset.to_xarray()
    assert isinstance(xarray_dataset, xr.Dataset)
    observation = "readout__sample__observation"
    assert tuple(xarray_dataset["frequency"].dims) == (observation,)
    assert tuple(xarray_dataset["signal"].dims) == (observation,)
    parent = xarray_dataset["readout__sample__parent_point_index"]
    assert list(parent.values) == [10, 10, 20, 40, 40, 40]
    assert parent.attrs["scopecat_parent_identity"] == "durable_point_index"
    assert parent.attrs["source_recording_group_id"] == "readout"
    assert list(xarray_dataset["readout__sample__row_size"].values) == [2, 1, 3]
    assert list(xarray_dataset["readout__sample__sample_extent"].values) == [2, 1, 3]
    assert list(xarray_dataset["readout__sample__sample_index"].values) == [
        0,
        1,
        0,
        0,
        1,
        2,
    ]
    assert (
        xarray_dataset["signal"].attrs["scopecat_ragged_representation"]
        == "indexed_observation"
    )
    assert xarray_dataset["frequency__observation_valid"].values.tolist() == [
        True,
        True,
        True,
        True,
        True,
        True,
    ]
    assert xarray_dataset["signal__observation_valid"].values.tolist() == [
        True,
        True,
        True,
        True,
        True,
        True,
    ]

    signal = dataset["signal"].observations
    assert signal.layout == "ragged"
    assert signal.declared_dims == ("point", "sample")
    assert signal.dims == (observation,)
    assert signal.shape == (6,)
    assert signal.valid.tolist() == [True] * 6
    assert "readout__sample__parent_point_index" in signal.coords
    assert signal.isel(**{observation: slice(2, 4)}).shape == (2,)
    with pytest.raises(ValueError, match="is ragged"):
        _ = dataset["signal"].dense

    native_point_subset = xarray_dataset.isel(point=[1])
    assert native_point_subset.sizes[observation] == 6
    facade_point_subset = dataset.isel(point=[1]).to_xarray()
    assert facade_point_subset.sizes[observation] == 1
    assert list(facade_point_subset["readout__sample__parent_point_index"].values) == [
        20
    ]


def test_ragged_unavailable_unknown_extent_uses_recording_group_layout() -> None:
    dataset = _ragged_dataset()
    unavailable = MeasurementUnavailable.create(
        reason="missing",
        dtype="complex128",
        unit="ratio",
        shape=(None,),
        metadata={},
    )
    raw = _snapshot(dataset).model_copy(
        update={
            "records": (
                dataset.records[0],
                _replace_record_values(
                    dataset.records[1],
                    observables={"signal": unavailable},
                ),
                dataset.records[2],
            )
        }
    )
    dataset = Dataset(raw, dataset.entry)

    arrow = dataset.project(diagnostics="reason").to_arrow()
    xarray_dataset = dataset.to_xarray()

    assert isinstance(arrow, pa.Table)
    assert arrow["signal"][1].as_py() is None
    assert isinstance(xarray_dataset, xr.Dataset)
    assert list(xarray_dataset["readout__sample__row_size"].values) == [2, 1, 3]
    assert list(xarray_dataset["readout__sample__sample_extent"].values) == [
        2,
        1,
        3,
    ]
    missing_value = complex(xarray_dataset["signal"].values[2])
    assert math.isnan(missing_value.real)
    assert math.isnan(missing_value.imag)
    assert xarray_dataset["signal__unavailable_reason"].values[1] == "missing"
    assert xarray_dataset["signal__observation_valid"].values.tolist() == [
        True,
        True,
        False,
        True,
        True,
        True,
    ]
    reasons = xarray_dataset["signal__observation_unavailable_reason"]
    assert reasons.values[2] == "missing"
    assert reasons.isnull().values.tolist() == [True, True, False, True, True, True]


@pytest.mark.parametrize(
    ("dtype", "missing_fill", "dtype_kind"),
    [
        ("int64", 0, "i"),
        ("bool", False, "b"),
        ("string", "", "U"),
    ],
)
def test_ragged_non_nullable_dtypes_mark_filled_observations_invalid(
    dtype: MeasurementDType,
    missing_fill: object,
    dtype_kind: str,
) -> None:
    base = _ragged_dataset()
    signal = next(
        variable for variable in base.schema.variables if variable.id == "signal"
    )
    schema = base.schema.model_copy(
        update={
            "variables": tuple(
                variable.model_copy(update={"dtype": dtype, "unit": None})
                if variable.id == signal.id
                else variable
                for variable in base.schema.variables
            )
        }
    )
    records: list[MeasurementRecord] = []
    for position, record in enumerate(base.records):
        length = (2, 1, 3)[position]
        if position == 1:
            records.append(
                _replace_record_values(
                    record,
                    observables={
                        "signal": MeasurementUnavailable.create(
                            reason="missing",
                            dtype=dtype,
                            unit=None,
                            shape=(None,),
                            metadata={},
                        )
                    },
                )
            )
            continue
        if dtype == "int64":
            values: tuple[object, ...] = tuple(range(length))
        elif dtype == "bool":
            values = tuple(index % 2 == 0 for index in range(length))
        else:
            values = tuple(f"value-{position}-{index}" for index in range(length))
        records.append(
            _replace_record_values(
                record,
                observables={
                    "signal": MeasurementArray.create(
                        values=values,
                        dtype=dtype,
                        unit=None,
                        metadata={},
                    )
                },
            )
        )

    raw = _snapshot(base).model_copy(
        update={"dataset_schema": schema, "records": tuple(records)}
    )
    xarray_dataset = Dataset(raw, base.entry).to_xarray()

    assert xarray_dataset["signal"].dtype.kind == dtype_kind
    assert xarray_dataset["signal"].values[2] == missing_fill
    assert xarray_dataset["signal__observation_valid"].values.tolist() == [
        True,
        True,
        False,
        True,
        True,
        True,
    ]
    reasons = xarray_dataset["signal__observation_unavailable_reason"]
    assert reasons.values[2] == "missing"
    assert reasons.isnull().values.tolist() == [True, True, False, True, True, True]


def test_ungrouped_ragged_unavailable_preserves_unknown_extent_in_xarray() -> None:
    dataset = _ragged_dataset()
    schema = dataset.schema.model_copy(
        update={
            "variables": tuple(
                variable.model_copy(update={"recording_group_id": None})
                if variable.id == "signal"
                else variable
                for variable in dataset.schema.variables
            ),
            "variable_groups": (
                MeasurementVariableGroup(
                    id="readout",
                ),
            ),
        }
    )
    unavailable = MeasurementUnavailable.create(
        reason="missing",
        dtype="complex128",
        unit="ratio",
        shape=(None,),
        metadata={},
    )
    raw = _snapshot(dataset).model_copy(
        update={
            "dataset_schema": schema,
            "records": (
                dataset.records[0],
                _replace_record_values(
                    dataset.records[1],
                    observables={"signal": unavailable},
                ),
                dataset.records[2],
            ),
        }
    )
    dataset = Dataset(raw, dataset.entry)

    xarray_dataset = dataset.to_xarray()

    assert isinstance(xarray_dataset, xr.Dataset)
    assert list(xarray_dataset["signal__sample__row_size"].values) == [2, 0, 3]
    assert list(xarray_dataset["signal__sample__sample_extent"].values) == [
        2,
        None,
        3,
    ]
    with pytest.raises(ValueError, match="unknown point-local extent"):
        dataset.isel_ragged(sample=0, variable="signal")


def test_ungrouped_ragged_variables_keep_independent_xarray_observations() -> None:
    dataset = _ragged_dataset()
    schema = dataset.schema.model_copy(
        update={
            "variables": tuple(
                variable.model_copy(update={"recording_group_id": None})
                if variable.id in {"frequency", "signal"}
                else variable
                for variable in dataset.schema.variables
            ),
            "variable_groups": (),
        }
    )
    raw = _snapshot(dataset).model_copy(update={"dataset_schema": schema})
    dataset = Dataset(raw, dataset.entry)

    xarray_dataset = dataset.to_xarray()

    assert isinstance(xarray_dataset, xr.Dataset)
    assert tuple(xarray_dataset["frequency"].dims) == (
        "frequency__sample__observation",
    )
    assert tuple(xarray_dataset["signal"].dims) == ("signal__sample__observation",)


def test_grouped_ragged_xarray_rejects_misaligned_point_local_shapes() -> None:
    dataset = _ragged_dataset()
    misaligned = MeasurementArray.create(
        values=(
            complex(1.0, 0.0),
            complex(1.0, 1.0),
        ),
        dtype="complex128",
        unit="ratio",
    )
    raw = _snapshot(dataset).model_copy(
        update={
            "records": (
                dataset.records[0],
                _replace_record_values(
                    dataset.records[1],
                    observables={"signal": misaligned},
                ),
                dataset.records[2],
            )
        }
    )

    with pytest.raises(
        ValueError,
        match=r"recording group 'readout'.*do not share one point-local",
    ):
        Dataset(raw, dataset.entry).to_xarray()


def test_ragged_sample_selection_applies_independently_per_point_and_group() -> None:
    dataset = _ragged_dataset()

    with pytest.raises(ValueError, match=r"use isel_ragged\(\)"):
        dataset.isel(sample=slice(0, 2))

    selected = dataset.isel_ragged(
        sample=slice(0, 2),
        group="readout",
    )

    assert selected.dims["sample"] is None
    frequency_values = selected["frequency"].values
    signal_values = selected["signal"].values
    assert all(isinstance(value, np.ndarray) for value in frequency_values)
    assert all(isinstance(value, np.ndarray) for value in signal_values)
    assert [
        len(value) for value in frequency_values if isinstance(value, np.ndarray)
    ] == [2, 1, 2]
    assert [len(value) for value in signal_values if isinstance(value, np.ndarray)] == [
        2,
        1,
        2,
    ]
    with pytest.raises(
        IndexError,
        match=r"point_index 20, variable 'frequency'.*sample index 1",
    ):
        dataset.isel_ragged(sample=1, group="readout")
    with pytest.raises(ValueError, match=r"belongs to recording group 'readout'"):
        dataset.isel_ragged(sample=slice(0, 1), variable="signal")


def _assert_array_values(
    actual: tuple[object, ...],
    expected: tuple[tuple[object, ...], ...],
) -> None:
    assert len(actual) == len(expected)
    for value, expected_value in zip(actual, expected, strict=True):
        assert isinstance(value, np.ndarray)
        np.testing.assert_array_equal(value, np.asarray(expected_value))
        assert not value.flags.writeable


def _replace_record_values(
    record: MeasurementRecord,
    *,
    coordinates: Mapping[str, MeasurementValue] | None = None,
    observables: Mapping[str, MeasurementValue] | None = None,
    point_index: int | None = None,
) -> MeasurementRecord:
    updates: dict[str, object] = {}
    if coordinates is not None:
        updates["coordinates"] = {**record.coordinates, **coordinates}
    if observables is not None:
        updates["observables"] = {**record.observables, **observables}
    if point_index is not None:
        updates["point_index"] = point_index
    return record.model_copy(update=updates)


def _ragged_dataset() -> Dataset:
    dataset = _dataset()
    schema = dataset.schema.model_copy(
        update={
            "dimensions": (
                dataset.schema.dimensions[0],
                dataset.schema.dimensions[1].model_copy(update={"size": None}),
            ),
            "variables": tuple(
                variable.model_copy(update={"recording_group_id": "readout"})
                if variable.id == "frequency"
                else variable
                for variable in dataset.schema.variables
            ),
            "variable_groups": (
                MeasurementVariableGroup(
                    id="readout",
                ),
            ),
        }
    )
    lengths = (2, 1, 3)
    records = tuple(
        _replace_record_values(
            record,
            point_index=(10, 20, 40)[point],
            coordinates={
                "frequency": MeasurementArray.create(
                    values=tuple(10.0 * point + index for index in range(length)),
                    dtype="float64",
                    unit="Hz",
                )
            },
            observables={
                "signal": MeasurementArray.create(
                    values=tuple(
                        complex(float(point), float(index)) for index in range(length)
                    ),
                    dtype="complex128",
                    unit="ratio",
                )
            },
        )
        for point, (record, length) in enumerate(
            zip(dataset.records, lengths, strict=True)
        )
    )
    raw = _snapshot(dataset).model_copy(
        update={"dataset_schema": schema, "records": records}
    )
    return Dataset(raw, dataset.entry)


def _product_grid_dataset() -> Dataset:
    x_values = (0.0, 1.0)
    y_values = (10, 20, 30)
    schema = MeasurementDatasetSchema(
        dataset_id="product-grid",
        point_domain=MeasurementProductGridPointDomain(
            axes=[
                MeasurementPointDomainAxis(
                    id="x",
                    size=len(x_values),
                    source=MeasurementPointDomainRangeSource(
                        start=MeasurementScalar.create(
                            value=x_values[0],
                            dtype="float64",
                            unit="V",
                        ),
                        stop=MeasurementScalar.create(
                            value=x_values[-1],
                            dtype="float64",
                            unit="V",
                        ),
                    ),
                ),
                MeasurementPointDomainAxis(
                    id="y",
                    size=len(y_values),
                    source=MeasurementPointDomainValuesSource(
                        values=[
                            MeasurementScalar.create(value=value, dtype="int64")
                            for value in y_values
                        ]
                    ),
                ),
            ]
        ),
        dimensions=[
            MeasurementDimension(id="point", kind="point", size=6),
            MeasurementDimension(id="sample", kind="sample", size=2),
        ],
        variables=[
            MeasurementVariable(
                id="x",
                role="coordinate",
                dtype="float64",
                unit="V",
                dims=["point"],
            ),
            MeasurementVariable(
                id="y",
                role="coordinate",
                dtype="int64",
                dims=["point"],
            ),
            MeasurementVariable(
                id="signal",
                role="observable",
                dtype="float64",
                dims=["point"],
            ),
            MeasurementVariable(
                id="trace",
                role="observable",
                dtype="float64",
                dims=["point", "sample"],
            ),
        ],
        primary_coordinates=["x", "y"],
        primary_observables=["signal", "trace"],
    )
    records = [
        MeasurementRecord(
            run_id="run-product-grid",
            logical_point_id=f"logical-{point_index}",
            point_index=point_index,
            coordinates={
                "x": MeasurementScalar.create(
                    value=x_values[point_index // len(y_values)],
                    dtype="float64",
                    unit="V",
                ),
                "y": MeasurementScalar.create(
                    value=y_values[point_index % len(y_values)],
                    dtype="int64",
                ),
            },
            observables={
                "signal": MeasurementScalar.create(
                    value=float(point_index),
                    dtype="float64",
                ),
                "trace": MeasurementArray.create(
                    values=np.array(
                        [float(point_index), float(point_index) + 0.5],
                        dtype=np.float64,
                    ),
                    dtype="float64",
                ),
            },
        )
        for point_index in range(6)
    ]
    raw = MeasurementDataset(
        dataset_schema=schema,
        records=[records[index] for index in (5, 0, 3, 1, 4, 2)],
    )
    entry = ContentEntry(
        role="dataset",
        id="product-grid",
        kind="measurement_dataset",
        content_hash="unused-product-grid",
        schema=schema.model_dump(mode="json"),
    )
    return Dataset(raw, entry)


def _snapshot(dataset: Dataset) -> MeasurementDataset:
    return MeasurementDataset(
        dataset_schema=dataset.schema,
        records=dataset.records,
        metadata=dataset.metadata,
    )


def _dataset() -> Dataset:
    schema = MeasurementDatasetSchema(
        dataset_id="raw-measurements",
        point_domain=MeasurementPointCloudPointDomain(
            columns=(MeasurementPointDomainColumn(id="bias"),)
        ),
        dimensions=[
            MeasurementDimension(id="point", kind="point", size=3),
            MeasurementDimension(id="sample", kind="frequency", size=2),
        ],
        variables=[
            MeasurementVariable(
                id="bias",
                role="coordinate",
                dtype="float64",
                unit="V",
                dims=["point"],
                label="DC bias",
                metadata={
                    "calibration": {
                        "source": "smu",
                        "revision": 2,
                    }
                },
            ),
            MeasurementVariable(
                id="frequency",
                role="coordinate",
                dtype="float64",
                unit="Hz",
                dims=["point", "sample"],
            ),
            MeasurementVariable(
                id="temperature",
                role="observable",
                dtype="float64",
                unit="K",
                dims=["point"],
            ),
            MeasurementVariable(
                id="signal",
                role="observable",
                dtype="complex128",
                unit="ratio",
                dims=["point", "sample"],
                recording_group_id="readout",
            ),
        ],
        variable_groups=[MeasurementVariableGroup(id="readout")],
        primary_coordinates=["bias", "frequency"],
        primary_observables=["temperature", "signal"],
    )
    records = [
        MeasurementRecord(
            run_id="run-dataset",
            logical_point_id=f"logical-{point_index}",
            point_index=point_index,
            coordinates={
                "bias": MeasurementScalar.create(
                    value=float(point_index), dtype="float64", unit="V"
                ),
                "frequency": MeasurementArray.create(
                    values=(
                        10.0 + 2.0 * point_index,
                        11.0 + 2.0 * point_index,
                    ),
                    dtype="float64",
                    unit="Hz",
                ),
            },
            observables={
                "temperature": (
                    MeasurementUnavailable.create(
                        reason="invalid",
                        dtype="float64",
                        unit="K",
                        shape=(),
                        metadata={"cause": "sensor settling"},
                    )
                    if point_index == 1
                    else MeasurementScalar.create(
                        value=0.05 if point_index == 0 else 0.2,
                        dtype="float64",
                        unit="K",
                    )
                ),
                "signal": MeasurementArray.create(
                    values=(
                        complex(1.0 + point_index, 0.0),
                        complex(0.5 + point_index, -0.1),
                    ),
                    dtype="complex128",
                    unit="ratio",
                ),
            },
        )
        for point_index in range(3)
    ]
    raw = MeasurementDataset(
        dataset_schema=schema,
        records=records,
        metadata={
            "experiment": "facade-test",
            "context": {
                "operator": "test",
                "tags": ["xarray", "netcdf"],
            },
        },
    )
    entry = ContentEntry(
        role="dataset",
        id="raw-measurements",
        kind="measurement_dataset",
        content_hash="unused",
        schema=schema.model_dump(mode="json"),
    )
    return Dataset(raw, entry)


def _dataset_with_record_sources() -> Dataset:
    dataset = _dataset()
    source_fields = {
        "bias": {"source_value_id": "bias"},
        "frequency": {
            "source_product_id": "readout/frequency",
            "recording_group_id": "readout",
        },
        "temperature": {"source_product_id": "thermometer/temperature"},
        "signal": {"source_product_id": "readout/signal"},
    }
    variables = tuple(
        variable.model_copy(update=source_fields.get(variable.id, {}))
        for variable in dataset.schema.variables
    )
    schema = dataset.schema.model_copy(
        update={
            "variables": variables,
            "variable_groups": (
                MeasurementVariableGroup(
                    id="readout",
                ),
            ),
        }
    )
    raw = _snapshot(dataset).model_copy(update={"dataset_schema": schema})
    return Dataset(raw, dataset.entry)


def _dataset_with_value_source(
    *,
    variable_id: str,
    source_value_id: str,
) -> Dataset:
    dataset = _dataset()
    variables = tuple(
        variable.model_copy(
            update={
                "source_product_id": None,
                "source_value_id": source_value_id,
            }
        )
        if variable.id == variable_id
        else variable
        for variable in dataset.schema.variables
    )
    schema = dataset.schema.model_copy(update={"variables": variables})
    raw = _snapshot(dataset).model_copy(update={"dataset_schema": schema})
    return Dataset(raw, dataset.entry)


def test_cross_run_alignment_preserves_each_sources_entity_metadata() -> None:
    left_q = EntityRef(id="q0", kind="qubit", metadata={"label": "before"})
    right_q = EntityRef(id="q0", kind="qubit", metadata={"label": "after"})
    added = EntityRef(id="q1", kind="qubit", metadata={"label": "new"})
    left = _entity_source_dataset("before", (left_q,), (1.0,))
    right = _entity_source_dataset("after", (added, right_q), (20.0, 10.0))
    aligned_left, aligned_right = left.align_entities(right, "qubit", join="outer")
    left_index = aligned_left.schema.dimensions[1].index
    right_index = aligned_right.schema.dimensions[1].index
    assert left_index is not None and right_index is not None
    assert left_index.values == (left_q, added)
    assert right_index.values == (right_q, added)
    assert left_index.fingerprint == right_index.fingerprint
    assert aligned_right.records[0].run_id == "run-after"
    source = aligned_right["signal"].definition.source_entity_products
    assert source is not None
    assert source.product_ids == ("after/q0", "after/q1")
    requested = left.reindex_entities("qubit", (right_q,))
    assert requested.schema.dimensions[1].index == left.schema.dimensions[1].index


def test_explicit_materialization_detaches_ragged_and_missing_values() -> None:
    source = _ragged_dataset()
    raw = _snapshot(source)
    connected = True

    def read() -> MeasurementDataset:
        assert connected
        return raw

    def projected(
        projection: ProjectionSchema, batch_size: int
    ) -> pa.RecordBatchReader:
        raise AssertionError("materialized data must not request remote projection")

    lazy = Dataset._from_source(
        schema=source.schema,
        entry=source.entry,
        load_raw=read,
        load_projected_batches=projected,
    )
    assert lazy.materialize() is lazy
    connected = False
    assert lazy["frequency"].unit == "Hz"
    assert (
        lazy["frequency"].observations.shape == source["frequency"].observations.shape
    )
    assert lazy["temperature"].availability == (None, "invalid", None)
    assert lazy.logical_point_ids == source.logical_point_ids
    np.testing.assert_array_equal(
        lazy["frequency"].observations.values, source["frequency"].observations.values
    )
