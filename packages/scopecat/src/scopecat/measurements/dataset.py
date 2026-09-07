# pyright: reportUnknownArgumentType=false, reportUnknownMemberType=false
# pyright: reportUnknownParameterType=false, reportUnknownVariableType=false
# pyright: reportUnnecessaryTypeIgnoreComment=false
# pyright: reportPrivateUsage=false
"""Notebook-facing labeled views over durable measurement records."""

from __future__ import annotations

import json
import math
import operator
from collections.abc import Callable, Iterator, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, field, fields, is_dataclass
from itertools import product as cartesian_product
from types import MappingProxyType
from typing import TYPE_CHECKING, Literal, Self, cast, overload, override

import numpy as np
import pyarrow as pa
import xarray as xr
from numpy.typing import NDArray
from pydantic import JsonValue

from scopecat.kernel.content_identity import model_wire_content_hash
from scopecat.kernel.entity import EntityRef, entity_identity, entity_identity_key
from scopecat.kernel.frozen import thaw_json_value
from scopecat.kernel.quantity import Quantity
from scopecat.measurements.entity_selection import (
    MeasurementEntitySelection,
    bind_entity_selection,
    reindex_entity_evidence,
)
from scopecat.measurements.traces import Trace, measurement_traces
from scopecat.program.measurement_types import (
    MeasurementArrayData,
    MeasurementDType,
    MeasurementSegmentedData,
    NativeMeasurementScalar,
    NativeMeasurementValue,
    measurement_value_spec_from_scalar,
)
from scopecat.program.products import ProductRef, _product_axis_dimension_id
from scopecat.program.record_refs import RecordRef
from scopecat.program.value_refs import (
    CoordinateRef,
    ValueRef,
    internal_coordinate_ref_id,
    internal_value_ref_record_source_id,
)
from scopecat.program.value_types import Array, Payload, Scalar
from scopecat.program.value_types import Quantity as QuantityType
from scopecat.records.content import ContentEntry
from scopecat.records.measurement import (
    MeasurementArray,
    MeasurementArrayAvailability,
    MeasurementArrayUnavailableGroup,
    MeasurementDataset,
    MeasurementDatasetSchema,
    MeasurementDimension,
    MeasurementEntityIndex,
    MeasurementPartitionedArray,
    MeasurementProductGridPointDomain,
    MeasurementRecord,
    MeasurementResultContract,
    MeasurementScalar,
    MeasurementSegmentedArray,
    MeasurementUnavailable,
    MeasurementUnavailableReason,
    MeasurementValue,
    MeasurementVariable,
    measurement_point_axis_values,
)

if TYPE_CHECKING:
    from scopecat.measurements.interop import (
        MeasurementDataProjection,
        ProjectionDiagnostics,
        ProjectionLayout,
        ProjectionSchema,
    )

type NativeScalar = NativeMeasurementScalar
type NativeAvailableValue = NativeMeasurementValue
type NativeValue = NativeAvailableValue | None
type NativeMagnitude = float | complex | MeasurementArrayData | MeasurementSegmentedData
type MeasurementAvailability = (
    MeasurementUnavailableReason | MeasurementArrayAvailability | None
)
type GroupKey = NativeScalar | None
type DimensionIndexer = int | slice | Sequence[int] | Sequence[bool]
type EntityAlignmentJoin = Literal["exact", "inner", "outer"]
type PointIndexer = DimensionIndexer
type PointCondition = PointMask | xr.DataArray | Sequence[bool]
type SelectionMethod = Literal["exact", "nearest"]
type XarrayLayout = Literal["points", "grid"]
type XarrayNonNullFill = bool | int | float | complex | str
type XarrayFill = XarrayNonNullFill | None
type MeasurementArrayLayout = Literal["dense", "ragged"]


@dataclass(frozen=True, slots=True)
class _RaggedXarrayLayout:
    parent_point_indices: tuple[int, ...]
    row_sizes: tuple[int, ...]
    local_indices: tuple[tuple[int, ...], ...]
    local_extents: tuple[tuple[int | None, ...], ...]
    explicit_indices: bool = False


@dataclass(frozen=True, slots=True)
class _RaggedXarrayValues:
    values: object
    valid: object
    unavailable_reasons: object
    layout: _RaggedXarrayLayout


@dataclass(frozen=True, slots=True)
class _RaggedAlignmentKey:
    recording_group_id: str | None
    variable_id: str | None
    local_dimensions: tuple[str, ...]


class PointMask(Sequence[bool]):
    """A point-aligned Xarray boolean selection tied to one dataset view."""

    __slots__ = ("_data", "_dataset")

    def __init__(
        self,
        dataset: Dataset,
        values: xr.DataArray | Sequence[bool],
    ) -> None:
        self._dataset = dataset
        data = (
            values
            if isinstance(values, xr.DataArray)
            else xr.DataArray(
                np.asarray(values, dtype=np.bool_),
                dims=("point",),
                coords={"point": dataset._loaded_xarray.coords["point"]},
            )
        )
        if data.dims != ("point",) or data.sizes["point"] != len(dataset):
            raise ValueError("point masks must align exactly with the point dimension")
        self._data = data.astype(np.bool_).copy(deep=True)

    @property
    def xarray(self) -> xr.DataArray:
        """Return an independent copy of the labeled boolean array."""

        return self._data.copy(deep=True)

    @overload
    def __getitem__(self, index: int) -> bool: ...

    @overload
    def __getitem__(self, index: slice) -> tuple[bool, ...]: ...

    @override
    def __getitem__(self, index: int | slice) -> bool | tuple[bool, ...]:
        return self._values[index]

    @override
    def __len__(self) -> int:
        return self._data.sizes["point"]

    @override
    def __iter__(self) -> Iterator[bool]:
        return iter(self._values)

    def __and__(self, other: PointMask) -> PointMask:
        self._require_same_dataset(other)
        return PointMask(self._dataset, self._data & other._data)

    def __or__(self, other: PointMask) -> PointMask:
        self._require_same_dataset(other)
        return PointMask(self._dataset, self._data | other._data)

    def __invert__(self) -> PointMask:
        return PointMask(self._dataset, ~self._data)

    def __bool__(self) -> bool:
        raise TypeError("a PointMask has no single truth value; pass it to where()")

    @override
    def __repr__(self) -> str:
        return f"PointMask({self._values!r})"

    @property
    def _values(self) -> tuple[bool, ...]:
        values = cast(
            "np.ndarray[tuple[int], np.dtype[np.bool_]]",
            self._data.values,
        )
        return tuple(
            bool(cast("np.bool_", values[index])) for index in range(len(values))
        )

    def belongs_to(self, dataset: Dataset) -> bool:
        return self._dataset is dataset

    def _require_same_dataset(self, other: PointMask) -> None:
        if self._dataset is not other._dataset:
            raise ValueError("cannot combine point masks from different dataset views")


@dataclass(frozen=True, slots=True)
class LabeledMeasurementArray:
    """Native labeled values with explicit dense or observation layout."""

    _data: xr.DataArray = field(repr=False)
    _valid: xr.DataArray = field(repr=False)
    _unavailable_reasons: xr.DataArray = field(repr=False)
    layout: MeasurementArrayLayout
    declared_dims: tuple[str, ...]
    dtype: MeasurementDType
    unit: str | None

    @property
    def id(self) -> str:
        return cast("str", self._data.name)

    @property
    def dims(self) -> tuple[str, ...]:
        return cast("tuple[str, ...]", tuple(self._data.dims))

    @property
    def shape(self) -> tuple[int, ...]:
        return tuple(self._data.shape)

    @property
    def coords(self) -> Mapping[str, NDArray[np.generic]]:
        return MappingProxyType(
            {
                cast("str", name): _read_only_array(coordinate.values)
                for name, coordinate in self._data.coords.items()
            }
        )

    @property
    def values(self) -> NDArray[np.generic]:
        return _read_only_array(self._data.values)

    @property
    def valid(self) -> NDArray[np.bool_]:
        return cast("NDArray[np.bool_]", _read_only_array(self._valid.values))

    @property
    def unavailable_reasons(self) -> NDArray[np.object_]:
        selected = np.asarray(
            self._unavailable_reasons.values,
            dtype=np.object_,
        ).copy(order="C")
        for index in np.ndindex(selected.shape):
            selected_item = cast("object", selected[index])
            if isinstance(selected_item, float | np.floating) and math.isnan(
                float(selected_item)
            ):
                selected[index] = None
        selected.flags.writeable = False
        return selected

    @property
    def xarray(self) -> xr.DataArray:
        return self._data.copy(deep=True)

    def isel(self, **indexers: DimensionIndexer) -> LabeledMeasurementArray:
        """Select by representation dimension without dropping labels."""

        return self._selected("isel", indexers)

    def sel(self, **indexers: object) -> LabeledMeasurementArray:
        """Select by coordinate label without dropping labels."""

        return self._selected("sel", indexers)

    def to(self, unit: str) -> LabeledMeasurementArray:
        """Convert one numeric unit-bearing array while preserving diagnostics."""

        if self.unit is None or self.dtype not in {"int64", "float64", "complex128"}:
            raise TypeError(
                f"labeled array {self.id!r} must be numeric and unit-bearing"
            )
        scale = Quantity(1.0, self.unit).to(unit).value
        converted = (
            self._data.astype(
                np.complex128 if self.dtype == "complex128" else np.float64
            )
            * scale
        )
        converted.attrs = {**self._data.attrs, "units": unit}
        return type(self)(
            _data=converted,
            _valid=self._valid,
            _unavailable_reasons=self._unavailable_reasons,
            layout=self.layout,
            declared_dims=self.declared_dims,
            dtype="complex128" if self.dtype == "complex128" else "float64",
            unit=unit,
        )

    def _selected(
        self,
        method: Literal["isel", "sel"],
        indexers: Mapping[str, object],
    ) -> LabeledMeasurementArray:
        selected_data = cast(
            "xr.DataArray",
            getattr(self._data, method)(indexers, drop=False),
        )
        selected_valid = cast(
            "xr.DataArray",
            getattr(self._valid, method)(indexers, drop=False),
        )
        selected_reasons = cast(
            "xr.DataArray",
            getattr(self._unavailable_reasons, method)(indexers, drop=False),
        )
        return type(self)(
            _data=selected_data,
            _valid=selected_valid,
            _unavailable_reasons=selected_reasons,
            layout=self.layout,
            declared_dims=self.declared_dims,
            dtype=self.dtype,
            unit=self.unit,
        )


class Variable[T = NativeAvailableValue]:
    """One labeled coordinate or observable aligned to a :class:`Dataset`."""

    __slots__ = ("_dataset", "_definition")

    def __init__(self, dataset: Dataset, definition: MeasurementVariable) -> None:
        self._dataset = dataset
        self._definition = definition

    @property
    def id(self) -> str:
        return self._definition.id

    @property
    def role(self) -> Literal["coordinate", "observable"]:
        return self._definition.role

    @property
    def dtype(self) -> MeasurementDType:
        return self._definition.dtype

    @property
    def unit(self) -> str | None:
        return self._definition.unit

    @property
    def dims(self) -> tuple[str, ...]:
        return tuple(self._definition.dims)

    @property
    def shape(self) -> tuple[int | None, ...]:
        return tuple(self._dataset.dims[dim] for dim in self.dims)

    @property
    def label(self) -> str | None:
        return self._definition.label

    @property
    def recording_group_id(self) -> str | None:
        return self._definition.recording_group_id

    @property
    def metadata(self) -> Mapping[str, object]:
        return self._definition.metadata

    @property
    def definition(self) -> MeasurementVariable:
        """Return the deeply immutable durable schema definition."""

        return self._definition

    @property
    def xarray(self) -> xr.DataArray:
        """Return an independent Xarray copy of this variable."""

        return self._dataset._loaded_xarray[self.id].copy(deep=True)

    @property
    def layout(self) -> MeasurementArrayLayout:
        return "ragged" if _variable_is_ragged(self) else "dense"

    @property
    def labeled(self) -> LabeledMeasurementArray:
        """Return native labels, validity, reasons, and representation layout."""

        return _labeled_measurement_array(self)

    @property
    def dense(self) -> LabeledMeasurementArray:
        """Return the dense labeled representation, rejecting ragged variables."""

        if self.layout != "dense":
            raise ValueError(
                f"variable {self.id!r} is ragged; use observations instead"
            )
        return self.labeled

    @property
    def observations(self) -> LabeledMeasurementArray:
        """Return the indexed-observation representation for a ragged variable."""

        if self.layout != "ragged":
            raise ValueError(f"variable {self.id!r} is dense; use dense instead")
        return self.labeled

    @property
    def raw_values(self) -> tuple[MeasurementValue, ...]:
        """Return the deeply immutable durable values."""

        return self._raw_values

    @property
    def _raw_values(self) -> tuple[MeasurementValue, ...]:
        field = "coordinates" if self.role == "coordinate" else "observables"
        return tuple(
            getattr(record, field)[self.id] for record in self._dataset._records
        )

    @property
    def values(self) -> tuple[T | None, ...]:
        """Return native scalars or read-only ndarrays; unavailable points are None."""

        return cast(
            "tuple[T | None, ...]",
            tuple(_native_value(value) for value in self._raw_values),
        )

    def require_values(self) -> tuple[T, ...]:
        """Return available values, rejecting incomplete measurement rows."""

        values = self.values
        unavailable = tuple(
            index for index, value in enumerate(values) if value is None
        )
        if unavailable:
            rendered = ", ".join(str(index) for index in unavailable)
            raise ValueError(
                f"variable {self.id!r} is unavailable at row positions: {rendered}"
            )
        return cast("tuple[T, ...]", values)

    def magnitudes(
        self,
        unit: str | None = None,
    ) -> tuple[NativeMagnitude | None, ...]:
        """Return numeric scalar or array magnitudes in the requested unit."""

        source_unit = self.unit
        if source_unit is None or self.dtype not in {"float64", "int64", "complex128"}:
            raise TypeError(f"variable {self.id!r} must be numeric and unit-bearing")
        selected_unit = source_unit if unit is None else unit
        scale = Quantity(1.0, source_unit).to(selected_unit).value
        selected: list[NativeMagnitude | None] = []
        for value in self._raw_values:
            if isinstance(value, MeasurementUnavailable):
                selected.append(None)
            elif isinstance(value, MeasurementScalar):
                scalar = cast("int | float | complex", value.value)
                selected.append(
                    complex(scalar) * scale
                    if self.dtype == "complex128"
                    else float(cast("int | float", scalar)) * scale
                )
            else:
                dtype: MeasurementDType = (
                    "complex128" if self.dtype == "complex128" else "float64"
                )
                if isinstance(value, MeasurementSegmentedArray):
                    converted = MeasurementSegmentedArray.create(
                        segments=tuple(
                            segment.model_copy(
                                update={"dtype": dtype, "unit": selected_unit}
                            )
                            if isinstance(segment, MeasurementUnavailable)
                            else MeasurementArray.create(
                                values=np.asarray(
                                    segment.values,
                                    dtype=(
                                        np.complex128
                                        if dtype == "complex128"
                                        else np.float64
                                    ),
                                )
                                * scale,
                                dtype=dtype,
                                unit=selected_unit,
                                availability=segment.availability,
                                metadata=segment.metadata,
                            )
                            for segment in value.segments
                        ),
                        dtype=dtype,
                        unit=selected_unit,
                        metadata=value.metadata,
                    )
                else:
                    converted = MeasurementArray.create(
                        values=np.asarray(
                            value.values,
                            dtype=(
                                np.complex128 if dtype == "complex128" else np.float64
                            ),
                        )
                        * scale,
                        dtype=dtype,
                        unit=selected_unit,
                        availability=value.availability,
                        metadata=value.metadata,
                    )
                selected.append(cast("NativeMagnitude", _native_value(converted)))
        return tuple(selected)

    def require_magnitudes(
        self,
        unit: str | None = None,
    ) -> tuple[NativeMagnitude, ...]:
        """Return complete scalar or array magnitudes in the requested unit."""

        values = self.magnitudes(unit)
        unavailable = tuple(
            index for index, value in enumerate(values) if value is None
        )
        if unavailable:
            rendered = ", ".join(str(index) for index in unavailable)
            raise ValueError(
                f"variable {self.id!r} is unavailable at row positions: {rendered}"
            )
        return cast("tuple[NativeMagnitude, ...]", values)

    def quantities(
        self,
        unit: str | None = None,
    ) -> tuple[Quantity | None, ...]:
        """Return point-scalar numeric values with their declared unit."""

        self.require_point_scalar()
        source_unit = self.unit
        if source_unit is None or self.dtype not in {"float64", "int64"}:
            raise TypeError(f"variable {self.id!r} must be numeric and unit-bearing")
        selected_unit = source_unit if unit is None else unit
        selected: list[Quantity | None] = []
        for value in self.values:
            if value is None:
                selected.append(None)
                continue
            if isinstance(value, bool) or not isinstance(value, int | float):
                raise TypeError(
                    f"variable {self.id!r} contains a non-numeric scalar value"
                )
            selected.append(Quantity(float(value), source_unit).to(selected_unit))
        return tuple(selected)

    def require_quantities(
        self,
        unit: str | None = None,
    ) -> tuple[Quantity, ...]:
        """Return complete point-scalar quantities in the requested unit."""

        values = self.quantities(unit)
        unavailable = tuple(
            index for index, value in enumerate(values) if value is None
        )
        if unavailable:
            rendered = ", ".join(str(index) for index in unavailable)
            raise ValueError(
                f"variable {self.id!r} is unavailable at row positions: {rendered}"
            )
        return cast("tuple[Quantity, ...]", values)

    @property
    def availability(
        self,
    ) -> tuple[MeasurementAvailability, ...]:
        return tuple(
            (
                value.reason
                if isinstance(value, MeasurementUnavailable)
                else value.availability
                if isinstance(value, MeasurementArray | MeasurementPartitionedArray)
                else value.availability
                or next(
                    segment.reason
                    for segment in value.segments
                    if isinstance(segment, MeasurementUnavailable)
                )
                if isinstance(value, MeasurementSegmentedArray)
                and value.has_unavailable_segments
                else None
            )
            for value in self._raw_values
        )

    def is_available(self) -> PointMask:
        return PointMask(
            self._dataset,
            tuple(_measurement_value_is_complete(value) for value in self._raw_values),
        )

    def is_unavailable(
        self,
        reason: MeasurementUnavailableReason | None = None,
    ) -> PointMask:
        return PointMask(
            self._dataset,
            tuple(
                _measurement_value_is_unavailable(value, reason=reason)
                for value in self._raw_values
            ),
        )

    @overload
    def __getitem__(self, index: int) -> T | None: ...

    @overload
    def __getitem__(self, index: slice) -> tuple[T | None, ...]: ...

    def __getitem__(
        self,
        index: int | slice,
    ) -> T | tuple[T | None, ...] | None:
        return self.values[index]

    def __lt__(self, other: object) -> PointMask:
        return self._compare(
            other, cast("Callable[[object, object], object]", operator.lt)
        )

    def __le__(self, other: object) -> PointMask:
        return self._compare(
            other, cast("Callable[[object, object], object]", operator.le)
        )

    @override
    def __eq__(  # pyright: ignore[reportIncompatibleMethodOverride]
        self, other: object
    ) -> PointMask:
        return self._compare(
            other, cast("Callable[[object, object], object]", operator.eq)
        )

    @override
    def __ne__(  # pyright: ignore[reportIncompatibleMethodOverride]
        self, other: object
    ) -> PointMask:
        return self._compare(
            other, cast("Callable[[object, object], object]", operator.ne)
        )

    def __gt__(self, other: object) -> PointMask:
        return self._compare(
            other, cast("Callable[[object, object], object]", operator.gt)
        )

    def __ge__(self, other: object) -> PointMask:
        return self._compare(
            other, cast("Callable[[object, object], object]", operator.ge)
        )

    @override
    def __repr__(self) -> str:
        unit = "" if self.unit is None else f", unit={self.unit!r}"
        return f"Variable(id={self.id!r}, role={self.role!r}, dims={self.dims!r}{unit})"

    def _compare(
        self,
        other: object,
        comparison: Callable[[object, object], object],
    ) -> PointMask:
        self.require_point_scalar()
        query = _selection_value(other, self)
        selected = cast(
            "xr.DataArray",
            comparison(self._dataset._loaded_xarray[self.id], query),
        )
        return PointMask(self._dataset, selected.fillna(False))

    def require_point_scalar(self) -> None:
        if self.dims != ("point",):
            raise ValueError(
                f"variable {self.id!r} has dimensions {self.dims!r}; "
                "point predicates require a scalar variable"
            )


@dataclass(frozen=True, slots=True, init=False)
class Dataset:
    """A labeled, sliceable measurement dataset for notebook workflows.

    Durable persistence models stay behind this facade; labeled variables,
    records, result contracts, and ecosystem projections form the notebook API.
    """

    _raw: MeasurementDataset | None
    _entry: ContentEntry
    _load_raw: Callable[[], MeasurementDataset] | None = field(
        repr=False,
    )
    _load_projected_batches: (
        Callable[[ProjectionSchema, int], pa.RecordBatchReader] | None
    ) = field(
        repr=False,
    )
    _schema_value: MeasurementDatasetSchema = field(repr=False)
    _view_dimensions: Mapping[str, int | None] = field(init=False, repr=False)
    _view_dimension_positions: Mapping[str, tuple[int, ...]] = field(
        init=False,
        repr=False,
    )
    _variables: Mapping[str, Variable[NativeAvailableValue]] = field(
        init=False,
        repr=False,
    )
    _xarray: xr.Dataset | None = field(init=False, repr=False)

    def __init__(
        self,
        raw: MeasurementDataset,
        entry: ContentEntry,
        *,
        view_dimensions: Mapping[str, int | None] | None = None,
        view_dimension_positions: Mapping[str, Sequence[int]] | None = None,
    ) -> None:
        self._initialize(
            raw=raw,
            entry=entry,
            schema=raw.dataset_schema,
            load_raw=None,
            load_projected_batches=None,
            view_dimensions=view_dimensions,
            view_dimension_positions=view_dimension_positions,
        )

    @classmethod
    def _from_source(
        cls,
        *,
        schema: MeasurementDatasetSchema,
        entry: ContentEntry,
        load_raw: Callable[[], MeasurementDataset],
        load_projected_batches: Callable[[ProjectionSchema, int], pa.RecordBatchReader],
    ) -> Self:
        dataset = cls.__new__(cls)
        dataset._initialize(
            raw=None,
            entry=entry,
            schema=schema,
            load_raw=load_raw,
            load_projected_batches=load_projected_batches,
            view_dimensions=None,
            view_dimension_positions=None,
        )
        return dataset

    def _initialize(
        self,
        *,
        raw: MeasurementDataset | None,
        entry: ContentEntry,
        schema: MeasurementDatasetSchema,
        load_raw: Callable[[], MeasurementDataset] | None,
        load_projected_batches: (
            Callable[[ProjectionSchema, int], pa.RecordBatchReader] | None
        ),
        view_dimensions: Mapping[str, int | None] | None,
        view_dimension_positions: Mapping[str, Sequence[int]] | None,
    ) -> None:
        object.__setattr__(self, "_raw", raw)
        object.__setattr__(self, "_entry", entry.model_copy(deep=True))
        object.__setattr__(self, "_load_raw", load_raw)
        object.__setattr__(self, "_load_projected_batches", load_projected_batches)
        object.__setattr__(self, "_schema_value", schema)
        dimensions = {dimension.id: dimension.size for dimension in schema.dimensions}
        dimensions["point"] = None if raw is None else len(raw.records)
        if view_dimensions is not None:
            unknown = set(view_dimensions) - set(dimensions)
            if unknown:
                raise ValueError(
                    "dataset view references unknown dimensions: "
                    + ", ".join(sorted(unknown))
                )
            dimensions.update(view_dimensions)
            dimensions["point"] = 0 if raw is None else len(raw.records)
        object.__setattr__(
            self,
            "_view_dimensions",
            MappingProxyType(dimensions),
        )
        fixed_local_dimensions = {
            dimension.id: dimension.size
            for dimension in schema.dimensions
            if dimension.id != "point" and dimension.size is not None
        }
        selected_positions = {
            dimension_id: tuple(range(cast("int", dimensions[dimension_id])))
            for dimension_id in fixed_local_dimensions
        }
        if view_dimension_positions is not None:
            unknown = set(view_dimension_positions) - set(fixed_local_dimensions)
            if unknown:
                raise ValueError(
                    "dataset view references unknown fixed dimensions: "
                    + ", ".join(sorted(unknown))
                )
            selected_positions.update(
                {
                    dimension_id: tuple(positions)
                    for dimension_id, positions in view_dimension_positions.items()
                }
            )
        object.__setattr__(
            self,
            "_view_dimension_positions",
            MappingProxyType(selected_positions),
        )
        variables = {
            definition.id: Variable(self, definition) for definition in schema.variables
        }
        object.__setattr__(self, "_variables", MappingProxyType(variables))
        object.__setattr__(self, "_xarray", None)

    def _materialize(self) -> MeasurementDataset:
        raw = self._raw
        if raw is not None:
            return raw
        load_raw = self._load_raw
        if load_raw is None:
            raise RuntimeError("measurement dataset has no content loader")
        raw = load_raw()
        object.__setattr__(self, "_raw", raw)
        object.__setattr__(self, "_load_raw", None)
        object.__setattr__(
            self,
            "_view_dimensions",
            MappingProxyType(
                {
                    **{
                        dimension.id: dimension.size
                        for dimension in self._schema_value.dimensions
                    },
                    "point": len(raw.records),
                }
            ),
        )
        object.__setattr__(
            self,
            "_view_dimension_positions",
            MappingProxyType(
                {
                    dimension.id: tuple(range(dimension.size))
                    for dimension in self._schema_value.dimensions
                    if dimension.id != "point" and dimension.size is not None
                }
            ),
        )
        return raw

    @property
    def _loaded_xarray(self) -> xr.Dataset:
        self._materialize()
        xarray = self._xarray
        if xarray is None:
            xarray = self._build_xarray()
            object.__setattr__(self, "_xarray", xarray)
        return xarray

    @property
    def entry(self) -> ContentEntry:
        """Return detached run-entry provenance for this snapshot."""

        return self._entry.model_copy(deep=True)

    @property
    def schema(self) -> MeasurementDatasetSchema:
        """Return the complete planned schema, independent of current view rows."""

        return self._schema

    @property
    def records(self) -> tuple[MeasurementRecord, ...]:
        return self._records

    @property
    def metadata(self) -> Mapping[str, object]:
        return self._materialize().metadata

    @property
    def _schema(self) -> MeasurementDatasetSchema:
        return self._schema_value

    @property
    def _records(self) -> tuple[MeasurementRecord, ...]:
        return cast("tuple[MeasurementRecord, ...]", self._materialize().records)

    @property
    def point_indices(self) -> tuple[int, ...]:
        """Return durable point indices in this view's row order."""

        return tuple(record.point_index for record in self._records)

    @property
    def logical_point_ids(self) -> tuple[str | None, ...]:
        """Return logical point identities in this view's row order."""

        return tuple(record.logical_point_id for record in self._records)

    @property
    def dims(self) -> Mapping[str, int | None]:
        """Return known view dimensions without forcing source materialization."""

        return self._view_dimensions

    def _read_projection_batches(
        self,
        projection: ProjectionSchema,
        *,
        batch_size: int,
    ) -> pa.RecordBatchReader | None:
        """Use the source's bounded Arrow reader when this view is unsliced."""

        load_projected_batches = self._load_projected_batches
        if self._raw is not None or load_projected_batches is None:
            return None
        return load_projected_batches(projection, batch_size)

    @property
    def variables(self) -> Mapping[str, Variable[NativeAvailableValue]]:
        return self._variables

    @property
    def variable_groups(
        self,
    ) -> Mapping[str, tuple[Variable[NativeAvailableValue], ...]]:
        """Return named recording groups with members in durable schema order."""

        return MappingProxyType(
            {
                group.id: tuple(
                    variable
                    for variable in self.variables.values()
                    if variable.recording_group_id == group.id
                )
                for group in self.schema.variable_groups
            }
        )

    @property
    def coords(self) -> Mapping[str, Variable[NativeAvailableValue]]:
        return MappingProxyType(
            {
                variable_id: variable
                for variable_id, variable in self.variables.items()
                if variable.role == "coordinate"
            }
        )

    @property
    def data_vars(self) -> Mapping[str, Variable[NativeAvailableValue]]:
        return MappingProxyType(
            {
                variable_id: variable
                for variable_id, variable in self.variables.items()
                if variable.role == "observable"
            }
        )

    @overload
    def __getitem__[T: NativeAvailableValue](
        self,
        variable_id: RecordRef[T],
    ) -> Variable[T]: ...

    @overload
    def __getitem__[T: NativeAvailableValue](
        self,
        variable_id: ProductRef[T],
    ) -> Variable[T]: ...

    @overload
    def __getitem__(self, variable_id: ValueRef[Quantity]) -> Variable[float]: ...

    @overload
    def __getitem__(
        self,
        variable_id: ValueRef[EntityRef | str],
    ) -> Variable[str]: ...

    @overload
    def __getitem__[T: bool | int | float | str](
        self,
        variable_id: ValueRef[T],
    ) -> Variable[T]: ...

    @overload
    def __getitem__(
        self,
        variable_id: ValueRef[object],
    ) -> Variable[NativeAvailableValue]: ...

    @overload
    def __getitem__(self, variable_id: str) -> Variable[NativeAvailableValue]: ...

    def __getitem__(
        self,
        variable_id: (
            str | RecordRef[NativeAvailableValue] | ProductRef | ValueRef[object]
        ),
    ) -> Variable[object]:
        if isinstance(variable_id, CoordinateRef):
            return self._variable_from_point_ref(variable_id)
        if isinstance(variable_id, RecordRef):
            return self._variable_from_record_ref(variable_id)
        if isinstance(variable_id, ProductRef):
            return self._variable_from_product_ref(variable_id)
        if isinstance(variable_id, ValueRef):
            return self._variable_from_value_ref(variable_id)
        try:
            return self.variables[variable_id]
        except KeyError as error:
            raise KeyError(
                f"measurement dataset has no variable {variable_id!r}"
            ) from error

    def bind[ResultT](self, output: ResultT, /) -> ExperimentResultView[ResultT]:
        """Bind an experiment's returned schema to this dataset.

        Every returned data reference is validated immediately. The resulting
        point views preserve each reference's native Python type without making
        callers manually align independent variable tuples.
        """

        return ExperimentResultView(self, output)

    def project(
        self,
        columns: Mapping[
            str,
            str | ProductRef | RecordRef | ValueRef[object],
        ]
        | None = None,
        /,
        *,
        units: Mapping[str, str] | None = None,
        diagnostics: ProjectionDiagnostics = "none",
        identity: bool = True,
        layout: ProjectionLayout = "points",
    ) -> MeasurementDataProjection:
        """Bind explicit external names before entering an ecosystem adapter."""

        from scopecat.measurements.interop import ProjectionSpec, bind_projection

        selected = (
            tuple(
                (name, cast("Variable[object]", self[ref]), None)
                for name, ref in columns.items()
            )
            if columns
            else tuple(
                (variable.id, cast("Variable[object]", variable), None)
                for variable in self.variables.values()
            )
        )
        return bind_projection(
            self,
            selected,
            spec=ProjectionSpec.create(
                units=units,
                diagnostics=diagnostics,
                include_identity=identity,
                layout=layout,
            ),
        )

    @property
    def result(self) -> StoredExperimentResultView:
        """Return the self-describing result view persisted with this dataset."""

        contract = self.schema.result
        if contract is None:
            raise ValueError("measurement dataset has no experiment result contract")
        return StoredExperimentResultView(self, contract)

    def _variable_from_record_ref[T: NativeAvailableValue](
        self,
        ref: RecordRef[T],
    ) -> Variable[T]:
        try:
            variable = self.variables[ref.id]
        except KeyError as error:
            raise KeyError(f"measurement dataset has no variable {ref.id!r}") from error
        _require_record_ref_matches(ref, variable.definition, schema=self.schema)
        return cast("Variable[T]", variable)

    def _variable_from_product_ref[T: NativeAvailableValue](
        self,
        ref: ProductRef[T],
    ) -> Variable[T]:
        variable = self._variable_from_source(
            "product",
            ref.id,
            field="source_product_id",
        )
        _require_product_ref_matches(ref, variable.definition)
        return cast("Variable[T]", variable)

    def _variable_from_value_ref(self, ref: ValueRef[object]) -> Variable[object]:
        source_id = internal_value_ref_record_source_id(ref)
        variable = self._variable_from_source(
            "value",
            source_id,
            field="source_value_id",
        )
        _require_value_ref_matches(ref, variable.definition)
        return cast("Variable[object]", variable)

    def _variable_from_source(
        self,
        source: str,
        source_id: str,
        *,
        field: Literal["source_product_id", "source_value_id"],
    ) -> Variable[NativeAvailableValue]:
        matches = tuple(
            variable
            for variable in self.variables.values()
            if getattr(variable.definition, field) == source_id
        )
        if not matches:
            raise KeyError(
                f"measurement dataset has no variable from {source} {source_id!r}"
            )
        if len(matches) > 1:
            ids = ", ".join(repr(variable.id) for variable in matches)
            raise KeyError(
                f"measurement dataset has multiple variables from {source} "
                f"{source_id!r}: {ids}; select one by name or RecordRef"
            )
        return next(iter(matches))

    def _variable_from_point_ref(self, ref: CoordinateRef[object]) -> Variable[object]:
        point_id = internal_coordinate_ref_id(ref)
        try:
            variable = self.variables[point_id]
        except KeyError as error:
            raise KeyError(
                f"measurement dataset has no variable {point_id!r}"
            ) from error
        _require_point_ref_matches(ref, variable.definition)
        return cast("Variable[object]", variable)

    def __len__(self) -> int:
        return len(self._records)

    def __iter__(self) -> Iterator[str]:
        return iter(self.variables)

    @override
    def __repr__(self) -> str:
        coordinates = ", ".join(self.coords)
        observables = ", ".join(self.data_vars)
        point_count = self._view_dimensions["point"]
        rendered_count = "?" if point_count is None else str(point_count)
        return (
            f"Dataset(id={self._schema.dataset_id!r}, points={rendered_count}, "
            f"coords=[{coordinates}], data_vars=[{observables}])"
        )

    def isel(
        self,
        indexers: Mapping[str, PointIndexer] | None = None,
        /,
        **indexer_kwargs: PointIndexer,
    ) -> Self:
        """Select fixed dimensions by position while preserving every dimension.

        Integer indexers select a one-element extent rather than dropping the
        dimension. Variable-length dimensions require :meth:`isel_ragged`,
        whose indexer is applied independently inside each point-local value.
        """

        selected = _merge_indexers(indexers, indexer_kwargs)
        if not selected:
            raise ValueError("isel requires at least one dimension indexer")
        unknown = sorted(set(selected) - set(self.dims))
        if unknown:
            raise ValueError(
                "isel references unknown dimensions: " + ", ".join(unknown)
            )
        ragged = sorted(
            dimension_id for dimension_id in selected if self.dims[dimension_id] is None
        )
        if ragged:
            raise ValueError(
                "isel cannot apply one global indexer to variable-length "
                f"dimensions {', '.join(ragged)}; use isel_ragged() with a "
                "recording group or variable"
            )

        selected_xarray = self._loaded_xarray.isel(
            {
                dimension_id: _preserving_xarray_indexer(indexer)
                for dimension_id, indexer in selected.items()
            }
        )
        point_indexer = selected.get("point")
        result = (
            self
            if point_indexer is None
            else self._select_indices(_point_positions(self, selected_xarray))
        )
        local_indices = {
            dimension_id: _dimension_indices(
                indexer,
                size=cast("int", self.dims[dimension_id]),
                label=dimension_id,
            )
            for dimension_id, indexer in selected.items()
            if dimension_id != "point"
        }
        return result._select_fixed_local_dimensions(local_indices)

    def isel_ragged(
        self,
        indexers: Mapping[str, DimensionIndexer] | None = None,
        /,
        *,
        group: str | None = None,
        variable: str | None = None,
        **indexer_kwargs: DimensionIndexer,
    ) -> Self:
        """Select samples independently inside each point-local ragged value.

        Use ``group=`` to keep a recorded coordinate/observable bundle aligned,
        or ``variable=`` for one ungrouped variable. Integer indexers preserve a
        one-element local dimension, matching :meth:`isel`.
        """

        selected = _merge_indexers(indexers, indexer_kwargs)
        if not selected:
            raise ValueError("isel_ragged requires at least one dimension indexer")
        if (group is None) == (variable is None):
            raise ValueError("isel_ragged requires exactly one of group= or variable=")
        unknown = sorted(set(selected) - set(self.dims))
        if unknown:
            raise ValueError(
                "isel_ragged references unknown dimensions: " + ", ".join(unknown)
            )
        fixed = sorted(
            dimension_id
            for dimension_id in selected
            if self.dims[dimension_id] is not None
        )
        if fixed:
            raise ValueError(
                "isel_ragged only accepts variable-length dimensions; use isel() "
                f"for {', '.join(fixed)}"
            )

        if variable is not None:
            try:
                selected_variable = self[variable]
            except KeyError as error:
                raise ValueError(
                    f"unknown measurement variable {variable!r}"
                ) from error
            if selected_variable.recording_group_id is not None:
                raise ValueError(
                    f"variable {variable!r} belongs to recording group "
                    f"{selected_variable.recording_group_id!r}; select that group "
                    "to keep its variables aligned"
                )
            target_variables = (selected_variable,)
        else:
            target_variables = tuple(
                candidate
                for candidate in self.variables.values()
                if candidate.recording_group_id == group
            )
            if not target_variables:
                raise ValueError(
                    f"measurement dataset has no recording group {group!r}"
                )
        target_ids = {
            candidate.id
            for candidate in target_variables
            if set(selected) & set(candidate.dims[1:])
        }
        unused = sorted(
            dimension_id
            for dimension_id in selected
            if not any(dimension_id in candidate.dims for candidate in target_variables)
        )
        if unused:
            target = (
                f"variable {variable!r}" if variable is not None else f"group {group!r}"
            )
            raise ValueError(
                f"isel_ragged dimensions {', '.join(unused)} are not used by {target}"
            )
        return self._select_ragged_local_dimensions(
            selected,
            target_variable_ids=target_ids,
        )

    def sel(
        self,
        indexers: Mapping[str, object] | None = None,
        /,
        *,
        method: SelectionMethod | None = None,
        tolerance: float | Quantity | None = None,
        **indexer_kwargs: object,
    ) -> Self:
        """Select point rows or entity dimensions through labeled indexes."""

        selected = _merge_indexers(indexers, indexer_kwargs)
        if not selected:
            raise ValueError("sel requires at least one coordinate indexer")
        dimensions_by_id = {
            dimension.id: dimension for dimension in self._schema.dimensions
        }
        entity_selected = {
            dimension_id: query
            for dimension_id, query in selected.items()
            if (
                (dimension := dimensions_by_id.get(dimension_id)) is not None
                and dimension.index is not None
            )
        }
        point_selected = {
            variable_id: query
            for variable_id, query in selected.items()
            if variable_id not in entity_selected
        }
        selected_method = "exact" if method is None else method
        if selected_method not in {"exact", "nearest"}:
            raise ValueError("sel method must be 'exact' or 'nearest'")
        if entity_selected and selected_method != "exact":
            raise ValueError("entity dimension selection only supports exact matching")
        if selected_method == "nearest":
            if len(point_selected) != 1 or "point" in point_selected:
                raise ValueError(
                    "nearest selection requires exactly one scalar coordinate"
                )
        elif tolerance is not None:
            raise ValueError("sel tolerance is only valid with method='nearest'")

        selected_xarray = self._loaded_xarray
        for variable_id, query in point_selected.items():
            if variable_id == "point" and isinstance(query, str):
                coordinate_id = "logical_point_id"
                normalized = query
                selected_tolerance = None
            elif variable_id == "point":
                coordinate_id = "point"
                normalized = query
                selected_tolerance = None
            else:
                variable = self._require_scalar_coordinate(variable_id)
                coordinate_id = variable_id
                normalized = _selection_value(query, variable)
                selected_tolerance = _selection_tolerance(tolerance, variable)
            if coordinate_id not in selected_xarray.xindexes:
                selected_xarray = selected_xarray.set_xindex(coordinate_id)
            selected_xarray = selected_xarray.sel(
                {coordinate_id: normalized},
                method=None if selected_method == "exact" else selected_method,
                tolerance=selected_tolerance,
            )
            if "point" not in selected_xarray.dims:
                selected_xarray = selected_xarray.expand_dims("point")
        result = (
            self
            if not point_selected
            else self._select_indices(_point_positions(self, selected_xarray))
        )
        if not entity_selected:
            return result
        local_positions = {
            dimension_id: _entity_selection_positions(
                cast("MeasurementEntityIndex", dimensions_by_id[dimension_id].index),
                result._view_dimension_positions[dimension_id],
                query,
                dimension_id=dimension_id,
            )
            for dimension_id, query in entity_selected.items()
        }
        return result.isel(local_positions)

    def reindex_entities(
        self,
        dimension_id: str,
        entities: Sequence[EntityRef],
        /,
    ) -> Self:
        """Return a materialized dataset on one explicit ordered entity index.

        Existing entities are reordered by complete ``(kind, id)`` identity.
        Entities absent from this dataset receive unavailable array leaves and
        null product/evidence provenance. Existing entity metadata is retained;
        only absent entities use the requested description.
        """

        bound = bind_entity_selection(
            self.schema,
            MeasurementEntitySelection(
                dimension_id=dimension_id, entities=tuple(entities)
            ),
            source_positions=self._view_dimension_positions.get(dimension_id),
        )
        target_entities = bound.selection.entities
        target_to_source = bound.target_to_source
        target_to_schema = bound.target_to_schema
        schema = bound.schema
        variables = schema.variables
        variable_by_id = {variable.id: variable for variable in variables}
        dimension_sizes = {
            dimension.id: dimension.size for dimension in schema.dimensions
        }
        records = tuple(
            _reindex_record_entities(
                record,
                variables=variable_by_id,
                dimension_sizes=dimension_sizes,
                dimension_id=dimension_id,
                source_count=bound.source_count,
                target_to_source=target_to_source,
                target_to_schema=target_to_schema,
            )
            for record in self.records
        )
        raw = MeasurementDataset(
            dataset_schema=schema,
            records=records,
            metadata=self.metadata,
        )
        entry = _derived_measurement_dataset_entry(
            self._entry,
            raw,
            operation={
                "kind": "reindex_entities",
                "dimension_id": dimension_id,
                "entities": [
                    entity.model_dump(mode="json") for entity in target_entities
                ],
            },
        )
        return type(self)(raw, entry)

    def align_entities(
        self,
        other: Dataset,
        dimension_id: str,
        /,
        *,
        join: EntityAlignmentJoin = "exact",
    ) -> tuple[Self, Dataset]:
        """Align two datasets on complete entity identity.

        ``inner`` preserves this dataset's order. ``outer`` appends identities
        seen only in ``other`` and marks absent leaves unavailable on each side.
        """

        if join not in {"exact", "inner", "outer"}:
            raise ValueError("entity alignment join must be exact, inner, or outer")
        left = _visible_entity_values(self, dimension_id)
        right = _visible_entity_values(other, dimension_id)
        left_identities = tuple(entity_identity(entity) for entity in left)
        right_identities = tuple(entity_identity(entity) for entity in right)
        if join == "exact":
            if left_identities != right_identities:
                raise ValueError(
                    f"entity dimension {dimension_id!r} ordered identities differ; "
                    "use join='inner' or join='outer'"
                )
            return self, other
        right_identity_set = set(right_identities)
        if join == "inner":
            target = tuple(
                entity
                for entity in left
                if entity_identity(entity) in right_identity_set
            )
            if not target:
                raise ValueError(
                    f"entity dimension {dimension_id!r} has no shared identities"
                )
        else:
            left_identity_set = set(left_identities)
            target = (
                *left,
                *(
                    entity
                    for entity in right
                    if entity_identity(entity) not in left_identity_set
                ),
            )
        return (
            self.reindex_entities(dimension_id, target),
            other.reindex_entities(dimension_id, target),
        )

    def where(
        self,
        condition: PointCondition | Callable[[xr.Dataset], xr.DataArray],
    ) -> Self:
        """Keep point rows selected by an Xarray-aligned boolean condition."""

        if callable(condition):
            source = self.to_xarray()
            selected = condition(source)
        else:
            source = self._loaded_xarray
            selected = condition
        if isinstance(selected, PointMask):
            if not selected.belongs_to(self):
                raise ValueError("point mask belongs to a different dataset view")
            mask = selected._data
        elif isinstance(selected, xr.DataArray):
            mask = selected
        else:
            mask = xr.DataArray(
                np.asarray(selected, dtype=np.bool_),
                dims=("point",),
                coords={"point": source.coords["point"]},
            )
        if mask.dims != ("point",) or mask.sizes["point"] != len(self):
            raise ValueError(
                "where condition must align exactly with the point dimension"
            )
        selected_xarray = source.where(mask.astype(np.bool_), drop=True)
        return self._select_indices(
            _point_positions(self, selected_xarray),
        )

    def groupby(self, variable_id: str) -> Mapping[GroupKey, Self]:
        """Group point rows with Xarray's labeled grouping semantics."""

        variable = self[variable_id]
        variable.require_point_scalar()
        groups = cast(
            "Mapping[object, Sequence[int] | slice]",
            cast("object", self._loaded_xarray.groupby(variable_id).groups),
        )
        return MappingProxyType(
            {
                _native_group_key(key): self._select_indices(
                    _group_positions(group_positions, size=len(self))
                )
                for key, group_positions in groups.items()
            }
        )

    def traces(
        self,
        observable: (
            str
            | RecordRef[MeasurementArrayData]
            | ProductRef[MeasurementArrayData]
            | ValueRef[MeasurementArrayData]
            | None
        ) = None,
        *,
        coordinate: (
            str
            | RecordRef[MeasurementArrayData]
            | ProductRef[MeasurementArrayData]
            | ValueRef[MeasurementArrayData]
            | None
        ) = None,
        group: str | None = None,
        entities: Sequence[EntityRef] | None = None,
    ) -> tuple[Trace, ...]:
        """Select traces by logical result, durable handle, id, or group.

        ``entities`` selects (kind, id) identities in request order, preserving
        stored metadata and acquisition evidence. Missing identities raise.
        """

        reference_groups: set[str] = set()
        if observable is not None and not isinstance(observable, str):
            observable_variable = self[observable]
            selected_observable = observable_variable.id
            if observable_variable.recording_group_id is not None:
                reference_groups.add(observable_variable.recording_group_id)
        else:
            selected_observable = observable
        if coordinate is not None and not isinstance(coordinate, str):
            coordinate_variable = self[coordinate]
            selected_coordinate = coordinate_variable.id
            if coordinate_variable.recording_group_id is not None:
                reference_groups.add(coordinate_variable.recording_group_id)
        else:
            selected_coordinate = coordinate
        if group is not None:
            reference_groups.add(group)
        if len(reference_groups) > 1:
            raise ValueError("trace record handles must belong to one recording group")
        selected_group = next(iter(reference_groups), None)

        return measurement_traces(
            self._materialize(),
            selected_observable,
            coordinate=selected_coordinate,
            group=selected_group,
            entities=entities,
        )

    def to_xarray(self, *, layout: XarrayLayout = "points") -> xr.Dataset:
        """Return a point-row or complete product-grid Xarray snapshot."""

        if layout == "points":
            return self._loaded_xarray.copy(deep=True)
        if layout == "grid":
            return self._product_grid_xarray()
        raise ValueError("Xarray layout must be 'points' or 'grid'")

    def _product_grid_xarray(self) -> xr.Dataset:
        domain = self._schema.point_domain
        if not isinstance(domain, MeasurementProductGridPointDomain):
            raise ValueError("grid Xarray layout requires a product-grid point domain")

        axis_ids = tuple(axis.id for axis in domain.axes)
        local_dimensions = set(self.dims) - {"point"}
        conflicts = sorted(set(axis_ids) & ({"point"} | local_dimensions))
        if conflicts:
            raise ValueError(
                "product-grid axes conflict with measurement dimensions: "
                + ", ".join(conflicts)
            )

        axis_shape = tuple(axis.size for axis in domain.axes)
        cardinality = math.prod(axis_shape)
        point_indices = self.point_indices
        if len(self) != cardinality or sorted(point_indices) != list(
            range(cardinality)
        ):
            raise ValueError(
                "grid Xarray layout requires every product-grid point exactly once"
            )
        ordered_positions = tuple(
            position
            for position, _point_index in sorted(
                enumerate(point_indices),
                key=operator.itemgetter(1),
            )
        )
        source = self._loaded_xarray.isel(point=list(ordered_positions))

        axis_coordinates: dict[str, object] = {}
        for axis_index, axis in enumerate(domain.axes):
            axis_values = measurement_point_axis_values(axis)
            variable = self.variables.get(axis.id)
            if variable is not None:
                if variable.role != "coordinate" or variable.dims != ("point",):
                    raise ValueError(
                        f"product-grid axis {axis.id!r} conflicts with a non-scalar "
                        "measurement variable"
                    )
                expected = _product_axis_flat_values(
                    domain,
                    axis_index=axis_index,
                    variable=variable,
                )
                if not _array_values_equal(source[axis.id].values, expected):
                    raise ValueError(
                        f"measurement coordinate {axis.id!r} does not match its "
                        "product-grid axis"
                    )
                attrs = {
                    **_variable_attrs(variable),
                    "scopecat_product_grid_axis": True,
                }
            else:
                if axis.id in source.variables:
                    raise ValueError(
                        f"product-grid axis {axis.id!r} conflicts with an Xarray "
                        "variable"
                    )
                attrs = _product_axis_attrs(axis_values)
            axis_coordinates[axis.id] = (
                (axis.id,),
                _product_axis_values(axis_values, variable=variable),
                attrs,
            )

        coords: dict[str, object] = dict(axis_coordinates)
        data_vars: dict[str, object] = {}
        for raw_name, array in source.coords.items():
            name = cast("str", raw_name)
            if name in axis_coordinates:
                continue
            coords[name] = _reshape_point_data_array(
                array,
                axis_ids=axis_ids,
                axis_shape=axis_shape,
            )
        for raw_name, array in source.data_vars.items():
            name = cast("str", raw_name)
            if name in axis_coordinates:
                raise ValueError(
                    f"product-grid axis {name!r} conflicts with an Xarray data variable"
                )
            data_vars[name] = _reshape_point_data_array(
                array,
                axis_ids=axis_ids,
                axis_shape=axis_shape,
            )

        return xr.Dataset(
            data_vars=data_vars,
            coords=coords,
            attrs={
                **deepcopy(dict(source.attrs)),
                "scopecat_xarray_layout": "product_grid",
            },
        )

    def _build_xarray(self) -> xr.Dataset:
        """Materialize the private canonical Xarray snapshot once."""

        coords: dict[str, object] = {
            "point": (
                ("point",),
                np.asarray(
                    tuple(record.point_index for record in self._records),
                    dtype=np.int64,
                ),
                {
                    "long_name": "durable measurement point_index",
                    "scopecat_role": "point_index",
                    "scopecat_identity": "durable_point_index",
                },
            )
        }
        for dimension in self._schema.dimensions:
            view_size = self.dims[dimension.id]
            if (
                dimension.id != "point"
                and view_size is not None
                and dimension.id not in self.variables
            ):
                positions = self._view_dimension_positions[dimension.id]
                if len(positions) != view_size:
                    raise ValueError(
                        f"dataset view dimension {dimension.id!r} position count "
                        "does not match its size"
                    )
                if dimension.index is None:
                    coordinate_values = np.asarray(positions, dtype=np.int64)
                    coordinate_attrs = {
                        "long_name": f"positional index for {dimension.id}",
                        "scopecat_role": "dimension_index",
                    }
                else:
                    coordinate_values = np.asarray(
                        tuple(
                            _entity_coordinate_label(
                                dimension.index,
                                dimension.index.values[position],
                            )
                            for position in positions
                        ),
                        dtype=np.object_,
                    )
                    coordinate_attrs = {
                        "long_name": f"entity index for {dimension.id}",
                        "scopecat_role": "entity_index",
                        "scopecat_entity_kind": dimension.index.entity_kind or "",
                        "scopecat_entity_axis_fingerprint": (
                            dimension.index.fingerprint
                        ),
                        "scopecat_entity_labels_json": _stable_json(
                            tuple(
                                dimension.index.values[position].id
                                for position in positions
                            )
                        ),
                        "scopecat_entities_json": _stable_json(
                            tuple(
                                dimension.index.values[position].model_dump(mode="json")
                                for position in positions
                            )
                        ),
                    }
                coords[dimension.id] = (
                    (dimension.id,),
                    coordinate_values,
                    coordinate_attrs,
                )
        if "logical_point_id" not in self.variables and any(
            record.logical_point_id is not None for record in self._records
        ):
            coords["logical_point_id"] = (
                ("point",),
                np.asarray(
                    tuple(record.logical_point_id for record in self._records),
                    dtype=np.object_,
                ),
                {"long_name": "Scopecat logical point id"},
            )
        data_vars: dict[str, object] = {}
        ragged_layouts: dict[_RaggedAlignmentKey, _RaggedXarrayLayout] = {}
        for variable in self.variables.values():
            if not _variable_is_ragged(variable):
                continue
            alignment = _ragged_alignment_key(variable)
            variable_layout = _ragged_xarray_layout(
                variable,
                records=self._records,
            )
            existing_layout = ragged_layouts.get(alignment)
            if existing_layout is None:
                ragged_layouts[alignment] = variable_layout
                continue
            try:
                ragged_layouts[alignment] = _merge_ragged_xarray_layouts(
                    existing_layout,
                    variable_layout,
                    records=self._records,
                )
            except ValueError as error:
                owner = (
                    f"recording group {alignment.recording_group_id!r}"
                    if alignment.recording_group_id is not None
                    else f"variable {variable.id!r}"
                )
                raise ValueError(
                    f"ragged values in {owner} do not share one point-local "
                    f"{alignment.local_dimensions!r} layout"
                ) from error
        emitted_ragged_layouts: set[_RaggedAlignmentKey] = set()
        for variable in self.variables.values():
            target = coords if variable.role == "coordinate" else data_vars
            availability = variable.availability
            if _variable_is_ragged(variable):
                alignment = _ragged_alignment_key(variable)
                alignment_id = _ragged_alignment_id(alignment)
                observation_dim = _ragged_observation_dim(alignment_id)
                ragged = _xarray_ragged_values(
                    variable,
                    layout=ragged_layouts[alignment],
                )
                target[variable.id] = (
                    (observation_dim,),
                    ragged.values,
                    {
                        **_variable_attrs(variable),
                        "scopecat_ragged_representation": "indexed_observation",
                        "scopecat_ragged_alignment": alignment_id,
                    },
                )
                data_vars[_ragged_valid_name(variable.id)] = (
                    (observation_dim,),
                    ragged.valid,
                    {
                        "long_name": f"valid observations for {variable.id}",
                        "scopecat_role": "ragged_observation_validity",
                        "source_variable": variable.id,
                    },
                )
                if any(reason is not None for reason in availability):
                    data_vars[_ragged_unavailable_reason_name(variable.id)] = (
                        (observation_dim,),
                        ragged.unavailable_reasons,
                        {
                            "long_name": (
                                "unavailable reason for each observation of "
                                f"{variable.id}"
                            ),
                            "scopecat_role": ("ragged_observation_unavailable_reason"),
                            "source_variable": variable.id,
                        },
                    )
                if alignment not in emitted_ragged_layouts:
                    emitted_ragged_layouts.add(alignment)
                    source_attrs = _ragged_alignment_attrs(alignment)
                    coords[_ragged_parent_point_index_name(alignment_id)] = (
                        (observation_dim,),
                        np.asarray(
                            ragged.layout.parent_point_indices,
                            dtype=np.int64,
                        ),
                        {
                            "long_name": (
                                "durable measurement point_index owning each "
                                f"{alignment_id} observation"
                            ),
                            "scopecat_role": "ragged_parent_point_index",
                            "scopecat_parent_identity": "durable_point_index",
                            **source_attrs,
                        },
                    )
                    coords[_ragged_row_size_name(alignment_id)] = (
                        ("point",),
                        np.asarray(
                            ragged.layout.row_sizes,
                            dtype=np.int64,
                        ),
                        {
                            "long_name": (
                                f"point-local observation count for {alignment_id}"
                            ),
                            "scopecat_role": "ragged_row_size",
                            **source_attrs,
                        },
                    )
                    for local_axis, dimension_id in enumerate(variable.dims[1:]):
                        coords[_ragged_local_index_name(alignment_id, dimension_id)] = (
                            (observation_dim,),
                            np.asarray(
                                ragged.layout.local_indices[local_axis],
                                dtype=np.int64,
                            ),
                            {
                                "long_name": (
                                    f"point-local {dimension_id} index for "
                                    f"{alignment_id}"
                                ),
                                "scopecat_role": "ragged_local_index",
                                "source_dimension": dimension_id,
                                **source_attrs,
                            },
                        )
                        coords[
                            _ragged_local_extent_name(alignment_id, dimension_id)
                        ] = (
                            ("point",),
                            np.asarray(
                                ragged.layout.local_extents[local_axis],
                                dtype=(
                                    np.object_
                                    if any(
                                        extent is None
                                        for extent in ragged.layout.local_extents[
                                            local_axis
                                        ]
                                    )
                                    else np.int64
                                ),
                            ),
                            {
                                "long_name": (
                                    f"point-local {dimension_id} extent for "
                                    f"{alignment_id}"
                                ),
                                "scopecat_role": "ragged_local_extent",
                                "source_dimension": dimension_id,
                                **source_attrs,
                            },
                        )
            else:
                target[variable.id] = (
                    variable.dims,
                    _xarray_values(variable),
                    _variable_attrs(variable),
                )
            fixed_availability = (
                None
                if _variable_is_ragged(variable)
                else _xarray_fixed_availability(variable)
            )
            if fixed_availability is not None:
                valid, unavailable_reasons = fixed_availability
                data_vars[_valid_name(variable.id)] = (
                    variable.dims,
                    valid,
                    {
                        "long_name": f"valid values for {variable.id}",
                        "scopecat_role": "value_validity",
                        "source_variable": variable.id,
                    },
                )
                data_vars[_unavailable_reason_name(variable.id)] = (
                    variable.dims,
                    unavailable_reasons,
                    {
                        "long_name": f"unavailable reason for {variable.id}",
                        "scopecat_role": "availability",
                        "source_variable": variable.id,
                    },
                )
            elif _variable_is_ragged(variable):
                point_reasons = tuple(
                    item if isinstance(item, str) else None for item in availability
                )
                if any(reason is not None for reason in point_reasons):
                    data_vars[_unavailable_reason_name(variable.id)] = (
                        ("point",),
                        np.asarray(point_reasons, dtype=np.object_),
                        {
                            "long_name": f"unavailable reason for {variable.id}",
                            "scopecat_role": "availability",
                            "source_variable": variable.id,
                        },
                    )
        return xr.Dataset(
            data_vars=data_vars,
            coords=coords,
            attrs=_dataset_attrs(self),
        )

    def _require_scalar_coordinate(self, variable_id: str) -> Variable:
        try:
            variable = self.coords[variable_id]
        except KeyError as error:
            raise KeyError(
                f"measurement dataset has no coordinate {variable_id!r}"
            ) from error
        variable.require_point_scalar()
        return variable

    def _select_indices(self, indices: Sequence[int]) -> Self:
        raw_source = self._materialize()
        selected_records = [raw_source.records[index] for index in indices]
        raw = MeasurementDataset(
            dataset_schema=self._schema,
            records=tuple(selected_records),
            metadata=raw_source.metadata,
        )
        return type(self)(
            raw,
            self._entry,
            view_dimensions={**self.dims, "point": len(selected_records)},
            view_dimension_positions=self._view_dimension_positions,
        )

    def _select_fixed_local_dimensions(
        self,
        indices_by_dimension: Mapping[str, tuple[int, ...]],
    ) -> Self:
        if not indices_by_dimension:
            return self
        variables = {variable.id: variable for variable in self.variables.values()}
        records = [
            _slice_record_local_values(
                record,
                variables=variables,
                target_variable_ids=set(variables),
                indices_for=lambda _record, variable, _value: {
                    dimension_id: indices
                    for dimension_id, indices in indices_by_dimension.items()
                    if dimension_id in variable.dims[1:]
                },
            )
            for record in self._records
        ]
        dimensions = {
            **self.dims,
            **{
                dimension_id: len(indices)
                for dimension_id, indices in indices_by_dimension.items()
            },
        }
        positions = {
            **self._view_dimension_positions,
            **{
                dimension_id: tuple(
                    self._view_dimension_positions[dimension_id][index]
                    for index in indices
                )
                for dimension_id, indices in indices_by_dimension.items()
            },
        }
        return self._copy_with(
            records=records,
            view_dimensions=dimensions,
            view_dimension_positions=positions,
        )

    def _select_ragged_local_dimensions(
        self,
        indexers_by_dimension: Mapping[str, DimensionIndexer],
        *,
        target_variable_ids: set[str],
    ) -> Self:
        variables = {variable.id: variable for variable in self.variables.values()}

        def indices_for(
            record: MeasurementRecord,
            variable: Variable,
            value: MeasurementValue,
        ) -> Mapping[str, tuple[int, ...]]:
            local_shape = _measurement_local_shape(value)
            selected: dict[str, tuple[int, ...]] = {}
            for local_axis, dimension_id in enumerate(variable.dims[1:]):
                indexer = indexers_by_dimension.get(dimension_id)
                if indexer is None:
                    continue
                try:
                    extent = local_shape[local_axis]
                    if extent is None:
                        raise ValueError(
                            f"cannot index dimension {dimension_id!r}: unavailable "
                            "value has an unknown point-local extent"
                        )
                    selected[dimension_id] = _dimension_indices(
                        indexer,
                        size=extent,
                        label=dimension_id,
                    )
                except (IndexError, TypeError, ValueError) as error:
                    raise type(error)(
                        f"point_index {record.point_index}, variable "
                        f"{variable.id!r}: {error}"
                    ) from error
            return selected

        records = [
            _slice_record_local_values(
                record,
                variables=variables,
                target_variable_ids=target_variable_ids,
                indices_for=indices_for,
            )
            for record in self._records
        ]
        return self._copy_with(
            records=records,
            view_dimensions=self.dims,
            view_dimension_positions=self._view_dimension_positions,
        )

    def _copy_with(
        self,
        *,
        records: Sequence[MeasurementRecord],
        view_dimensions: Mapping[str, int | None],
        view_dimension_positions: Mapping[str, Sequence[int]],
    ) -> Self:
        raw = MeasurementDataset(
            dataset_schema=self._schema,
            records=tuple(records),
            metadata=self._materialize().metadata,
        )
        return type(self)(
            raw,
            self._entry,
            view_dimensions=view_dimensions,
            view_dimension_positions=view_dimension_positions,
        )


@dataclass(frozen=True, slots=True)
class ExperimentResultPoint:
    """One complete typed point from a schema-bound experiment result."""

    _dataset: Dataset = field(repr=False)
    index: int

    def availability(
        self,
        ref: ProductRef | RecordRef | ValueRef[object],
        /,
    ) -> MeasurementUnavailableReason | None:
        """Return the typed unavailability reason for one result field."""

        unavailable = self.unavailable(ref)
        return None if unavailable is None else unavailable.reason

    def unavailable(
        self,
        ref: ProductRef | RecordRef | ValueRef[object],
        /,
    ) -> MeasurementUnavailable | None:
        """Return the complete unavailable diagnostic for one result field."""

        value = self._dataset[ref]._raw_values[self.index]
        return value if isinstance(value, MeasurementUnavailable) else None

    def is_available(
        self,
        ref: ProductRef | RecordRef | ValueRef[object],
        /,
    ) -> bool:
        """Return whether one result field has a usable value at this point."""

        return self.availability(ref) is None

    @overload
    def value(self, ref: ValueRef[Quantity], /) -> Quantity: ...

    @overload
    def value[T: NativeAvailableValue](
        self,
        ref: ProductRef[T] | RecordRef[T] | ValueRef[T],
        /,
    ) -> T: ...

    def value(
        self,
        ref: ProductRef | RecordRef | ValueRef[object],
        /,
    ) -> object:
        """Return one available native value, preserving Quantity semantics."""

        variable = self._dataset[ref]
        value = variable[self.index]
        if value is None:
            reason = self.availability(ref)
            raise ValueError(
                f"variable {variable.id!r} is unavailable at row position "
                f"{self.index}: {reason}"
            )
        if (
            isinstance(ref, ValueRef)
            and isinstance(ref.value_type, Scalar)
            and isinstance(ref.value_type.atom, QuantityType)
        ):
            if isinstance(value, bool) or not isinstance(value, int | float):
                raise TypeError(
                    f"quantity variable {variable.id!r} must contain numbers"
                )
            if variable.unit is None:
                raise TypeError(
                    f"quantity variable {variable.id!r} must declare a unit"
                )
            return Quantity(float(value), variable.unit)
        return value

    def quantity(
        self,
        ref: ProductRef | RecordRef | ValueRef[object],
        /,
        unit: str | None = None,
    ) -> Quantity:
        """Return one available point scalar as a quantity."""

        variable = self._dataset[ref]
        value = variable.quantities(unit)[self.index]
        if value is None:
            reason = self.availability(ref)
            raise ValueError(
                f"variable {variable.id!r} is unavailable at row position "
                f"{self.index}: {reason}"
            )
        return value


@dataclass(frozen=True, slots=True)
class ExperimentResultView[ResultT](Sequence[ExperimentResultPoint]):
    """A dataset validated against one experiment's returned result schema."""

    dataset: Dataset
    output: ResultT

    def __post_init__(self) -> None:
        refs = tuple(_experiment_result_refs(self.output))
        if not refs:
            raise TypeError("experiment result schemas must contain data references")
        for ref in refs:
            self.dataset[ref]

    @override
    def __len__(self) -> int:
        return len(self.dataset)

    @overload
    def __getitem__(self, index: int) -> ExperimentResultPoint: ...

    @overload
    def __getitem__(self, index: slice) -> tuple[ExperimentResultPoint, ...]: ...

    @override
    def __getitem__(
        self,
        index: int | slice,
    ) -> ExperimentResultPoint | tuple[ExperimentResultPoint, ...]:
        if isinstance(index, slice):
            return tuple(
                ExperimentResultPoint(self.dataset, position)
                for position in range(*index.indices(len(self)))
            )
        position = _normalize_index(index, size=len(self), label="result point")
        return ExperimentResultPoint(self.dataset, position)

    def rows[RowT](
        self,
        build: Callable[[ExperimentResultPoint], RowT],
        /,
    ) -> tuple[RowT, ...]:
        """Materialize typed rows without independently aligning columns."""

        return tuple(build(point) for point in self)

    def project(
        self,
        columns: Mapping[str, ProductRef | RecordRef | ValueRef[object]] | None = None,
        /,
        *,
        units: Mapping[str, str] | None = None,
        diagnostics: ProjectionDiagnostics = "none",
        identity: bool = True,
        layout: ProjectionLayout = "points",
    ) -> MeasurementDataProjection:
        """Project typed result fields under exact user-controlled column names."""

        from scopecat.measurements.interop import ProjectionSpec, bind_projection

        selected = (
            tuple(
                (name, cast("Variable[object]", self.dataset[ref]), None)
                for name, ref in columns.items()
            )
            if columns
            else tuple(
                (
                    ".".join(path),
                    cast("Variable[object]", self.dataset[ref]),
                    path,
                )
                for path, ref in _experiment_result_ref_paths(self.output)
            )
        )
        return bind_projection(
            self.dataset,
            selected,
            spec=ProjectionSpec.create(
                units=units,
                diagnostics=diagnostics,
                include_identity=identity,
                layout=layout,
            ),
        )

    def where_available(
        self,
        *refs: ProductRef | RecordRef | ValueRef[object],
    ) -> ExperimentResultView[ResultT]:
        """Keep points where the selected result fields all have usable values."""

        selected = tuple(refs) or tuple(_experiment_result_refs(self.output))
        mask = self.dataset[selected[0]].is_available()
        for ref in selected[1:]:
            mask &= self.dataset[ref].is_available()
        return ExperimentResultView(self.dataset.where(mask), self.output)

    def partition_available(
        self,
        *refs: ProductRef | RecordRef | ValueRef[object],
    ) -> tuple[ExperimentResultView[ResultT], ExperimentResultView[ResultT]]:
        """Split usable and unavailable points while preserving the result schema."""

        selected = tuple(refs) or tuple(_experiment_result_refs(self.output))
        mask = self.dataset[selected[0]].is_available()
        for ref in selected[1:]:
            mask &= self.dataset[ref].is_available()
        return (
            ExperimentResultView(self.dataset.where(mask), self.output),
            ExperimentResultView(self.dataset.where(~mask), self.output),
        )


type ResultPath = str | tuple[str, ...]


@dataclass(frozen=True, slots=True)
class StoredExperimentResultPoint:
    """One point addressed through a dataset's persisted return paths."""

    _view: StoredExperimentResultView = field(repr=False)
    index: int

    def availability(
        self,
        path: ResultPath,
        /,
    ) -> MeasurementUnavailableReason | None:
        """Return the typed unavailability reason for one persisted result field."""

        unavailable = self.unavailable(path)
        return None if unavailable is None else unavailable.reason

    def unavailable(
        self,
        path: ResultPath,
        /,
    ) -> MeasurementUnavailable | None:
        """Return the complete unavailable diagnostic for one persisted field."""

        value = self._view.variable(path)._raw_values[self.index]
        return value if isinstance(value, MeasurementUnavailable) else None

    def is_available(self, path: ResultPath, /) -> bool:
        return self.availability(path) is None

    def value(self, path: ResultPath, /) -> NativeAvailableValue:
        variable = self._view.variable(path)
        value = variable[self.index]
        if value is None:
            raise ValueError(
                f"result field {_normalize_result_path(path)!r} is unavailable at "
                f"row position {self.index}: {self.availability(path)}"
            )
        return value

    def quantity(self, path: ResultPath, /, unit: str | None = None) -> Quantity:
        variable = self._view.variable(path)
        value = variable.quantities(unit)[self.index]
        if value is None:
            raise ValueError(
                f"result field {_normalize_result_path(path)!r} is unavailable at "
                f"row position {self.index}: {self.availability(path)}"
            )
        return value


@dataclass(frozen=True, slots=True)
class StoredExperimentResultView(Sequence[StoredExperimentResultPoint]):
    """Historical result paths resolved without rebuilding symbolic Python refs."""

    dataset: Dataset
    contract: MeasurementResultContract
    _variables: Mapping[tuple[str, ...], Variable[NativeAvailableValue]] = field(
        init=False,
        repr=False,
    )

    def __post_init__(self) -> None:
        variables = {
            tuple(result_field.path): self.dataset[result_field.variable_id]
            for result_field in self.contract.fields
        }
        object.__setattr__(self, "_variables", MappingProxyType(variables))

    @property
    def paths(self) -> tuple[tuple[str, ...], ...]:
        return tuple(self._variables)

    def variable(self, path: ResultPath, /) -> Variable[NativeAvailableValue]:
        selected = _normalize_result_path(path)
        try:
            return self._variables[selected]
        except KeyError:
            raise KeyError(
                f"experiment result has no field {'/'.join(selected)!r}"
            ) from None

    @override
    def __len__(self) -> int:
        return len(self.dataset)

    @overload
    def __getitem__(self, index: int) -> StoredExperimentResultPoint: ...

    @overload
    def __getitem__(self, index: slice) -> tuple[StoredExperimentResultPoint, ...]: ...

    @override
    def __getitem__(
        self,
        index: int | slice,
    ) -> StoredExperimentResultPoint | tuple[StoredExperimentResultPoint, ...]:
        if isinstance(index, slice):
            return tuple(
                StoredExperimentResultPoint(self, position)
                for position in range(*index.indices(len(self)))
            )
        position = _normalize_index(index, size=len(self), label="result point")
        return StoredExperimentResultPoint(self, position)

    def rows[RowT](
        self,
        build: Callable[[StoredExperimentResultPoint], RowT],
        /,
    ) -> tuple[RowT, ...]:
        return tuple(build(point) for point in self)

    def project(
        self,
        columns: Mapping[str, ResultPath] | None = None,
        /,
        *,
        units: Mapping[str, str] | None = None,
        diagnostics: ProjectionDiagnostics = "none",
        identity: bool = True,
        layout: ProjectionLayout = "points",
    ) -> MeasurementDataProjection:
        """Project persisted result paths under exact external column names."""

        from scopecat.measurements.interop import ProjectionSpec, bind_projection

        selected = (
            tuple(
                (
                    name,
                    cast("Variable[object]", self.variable(path)),
                    _normalize_result_path(path),
                )
                for name, path in columns.items()
            )
            if columns
            else tuple(
                (
                    ".".join(path),
                    cast("Variable[object]", variable),
                    path,
                )
                for path, variable in self._variables.items()
            )
        )
        return bind_projection(
            self.dataset,
            selected,
            spec=ProjectionSpec.create(
                units=units,
                diagnostics=diagnostics,
                include_identity=identity,
                layout=layout,
            ),
        )

    def where_available(
        self,
        *paths: ResultPath,
    ) -> StoredExperimentResultView:
        """Keep points where the selected persisted result paths are available."""

        selected = tuple(paths) or self.paths
        mask = self.variable(selected[0]).is_available()
        for path in selected[1:]:
            mask &= self.variable(path).is_available()
        return StoredExperimentResultView(self.dataset.where(mask), self.contract)

    def partition_available(
        self,
        *paths: ResultPath,
    ) -> tuple[StoredExperimentResultView, StoredExperimentResultView]:
        """Split usable and unavailable points addressed by persisted paths."""

        selected = tuple(paths) or self.paths
        mask = self.variable(selected[0]).is_available()
        for path in selected[1:]:
            mask &= self.variable(path).is_available()
        return (
            StoredExperimentResultView(self.dataset.where(mask), self.contract),
            StoredExperimentResultView(self.dataset.where(~mask), self.contract),
        )


def _normalize_result_path(path: ResultPath) -> tuple[str, ...]:
    selected = tuple(path.split("/")) if isinstance(path, str) else tuple(path)
    if not selected or any(not segment for segment in selected):
        raise ValueError("experiment result paths must contain non-empty segments")
    return selected


def _experiment_result_refs(
    value: object,
) -> Iterator[ProductRef | RecordRef | ValueRef[object]]:
    if isinstance(value, ProductRef | RecordRef | ValueRef):
        yield value
        return
    if is_dataclass(value) and not isinstance(value, type):
        for member in fields(value):
            yield from _experiment_result_refs(
                cast("object", getattr(value, member.name))
            )
        return
    if isinstance(value, Mapping):
        for item in value.values():
            yield from _experiment_result_refs(item)
        return
    if isinstance(value, tuple):
        for item in value:
            yield from _experiment_result_refs(item)
        return


def _experiment_result_ref_paths(
    value: object,
    path: tuple[str, ...] = (),
) -> Iterator[tuple[tuple[str, ...], ProductRef | RecordRef | ValueRef[object]]]:
    if isinstance(value, ProductRef | RecordRef | ValueRef):
        yield path or ("result",), value
        return
    if is_dataclass(value) and not isinstance(value, type):
        for member in fields(value):
            yield from _experiment_result_ref_paths(
                cast("object", getattr(value, member.name)),
                (*path, member.name),
            )
        return
    if isinstance(value, Mapping):
        for name, item in value.items():
            yield from _experiment_result_ref_paths(
                item,
                (*path, str(name)),
            )
        return
    if isinstance(value, tuple):
        for index, item in enumerate(value):
            yield from _experiment_result_ref_paths(item, (*path, str(index)))
        return
    raise TypeError(
        "experiment result schemas must be tuple/dataclass/mapping trees of "
        "data references"
    )


def _native_value(value: MeasurementValue) -> NativeValue:
    if isinstance(value, MeasurementUnavailable):
        return None
    if isinstance(value, MeasurementScalar):
        return cast("NativeValue", _native_leaf(value.value))
    if isinstance(value, MeasurementSegmentedArray):
        return cast(
            "NativeValue",
            tuple(_native_value(segment) for segment in value.segments),
        )
    if value.availability is not None:
        masked = np.ma.MaskedArray(
            value.values,
            mask=~value.availability.valid,
            copy=False,
        )
        masked.data.flags.writeable = False
        masked.mask.flags.writeable = False
        return cast("NativeValue", masked)
    return value.values


def _measurement_value_is_complete(value: MeasurementValue) -> bool:
    return not isinstance(value, MeasurementUnavailable) and not (
        isinstance(
            value,
            MeasurementArray | MeasurementPartitionedArray | MeasurementSegmentedArray,
        )
        and (
            value.availability is not None
            or (
                isinstance(value, MeasurementSegmentedArray)
                and value.has_unavailable_segments
            )
        )
    )


def _measurement_value_is_unavailable(
    value: MeasurementValue,
    *,
    reason: MeasurementUnavailableReason | None,
) -> bool:
    if isinstance(value, MeasurementUnavailable):
        return reason is None or value.reason == reason
    if not isinstance(
        value,
        MeasurementArray | MeasurementPartitionedArray | MeasurementSegmentedArray,
    ):
        return False
    availability = value.availability
    return (
        (
            reason is None
            and isinstance(value, MeasurementSegmentedArray)
            and value.has_unavailable_segments
        )
        or (
            availability is not None
            and (
                reason is None
                or any(group.reason == reason for group in availability.unavailable)
            )
        )
        or (
            isinstance(value, MeasurementSegmentedArray)
            and any(
                isinstance(segment, MeasurementUnavailable)
                and (reason is None or segment.reason == reason)
                for segment in value.segments
            )
        )
    )


def _require_record_ref_matches(
    ref: RecordRef[NativeAvailableValue],
    definition: MeasurementVariable,
    *,
    schema: MeasurementDatasetSchema,
) -> None:
    """Validate an authored type promise at the durable-data boundary."""

    expected = {
        "id": ref.id,
        "role": ref.role,
        "dtype": ref.dtype,
        "unit": ref.unit,
        "dims": ref.dims,
        "entity_axis_id": ref.entity_axis_id,
        "entity_axis_fingerprint": ref.entity_axis_fingerprint,
    }
    actual = {
        "id": definition.id,
        "role": definition.role,
        "dtype": definition.dtype,
        "unit": definition.unit,
        "dims": tuple(definition.dims),
        "entity_axis_id": (
            None
            if definition.source_entity_products is None
            else definition.source_entity_products.dimension_id
        ),
        "entity_axis_fingerprint": (
            None
            if definition.source_entity_products is None
            else _entity_dimension_fingerprint(
                schema,
                definition.source_entity_products.dimension_id,
            )
        ),
    }
    mismatches = tuple(
        name for name, value in expected.items() if actual[name] != value
    )
    if not mismatches:
        return
    rendered = ", ".join(
        f"{name}={actual[name]!r} (expected {expected[name]!r})" for name in mismatches
    )
    raise TypeError(
        f"record reference {ref.id!r} does not match the dataset schema: {rendered}"
    )


def _entity_dimension_fingerprint(
    schema: MeasurementDatasetSchema,
    dimension_id: str,
) -> str:
    dimension = next(
        dimension for dimension in schema.dimensions if dimension.id == dimension_id
    )
    if dimension.index is None:
        raise AssertionError(
            f"entity source dimension {dimension_id!r} has no entity index"
        )
    return dimension.index.fingerprint


def _require_product_ref_matches(
    ref: ProductRef[NativeAvailableValue],
    definition: MeasurementVariable,
) -> None:
    expected = {
        "dtype": ref.value_spec.dtype,
        "unit": ref.value_spec.unit,
        "dims": (
            "point",
            *(
                _product_axis_dimension_id(ref.product_id, axis)
                for axis in ref.value_spec.axes
            ),
        ),
        "source_product_id": ref.id,
    }
    actual = {
        "dtype": definition.dtype,
        "unit": definition.unit,
        "dims": tuple(definition.dims),
        "source_product_id": definition.source_product_id,
    }
    _require_logical_ref_matches("product", ref.id, expected, actual)


def _require_value_ref_matches(
    ref: ValueRef[object],
    definition: MeasurementVariable,
) -> None:
    value_type = ref.value_type
    if isinstance(value_type, Scalar):
        if isinstance(value_type.atom, Payload):
            raise TypeError("opaque payload values cannot identify dataset variables")
        dtype, unit = measurement_value_spec_from_scalar(value_type)
        dims = ("point",)
    elif isinstance(value_type, Array):
        dtype, unit = value_type.dtype, value_type.unit
        dims = ("point", *(dimension.id for dimension in value_type.dimensions))
    else:
        raise TypeError("only scalar or array values can identify dataset variables")
    source_id = internal_value_ref_record_source_id(ref)
    expected = {
        "dtype": dtype,
        "unit": unit,
        "dims": dims,
        "source_value_id": source_id,
    }
    actual = {
        "dtype": definition.dtype,
        "unit": definition.unit,
        "dims": tuple(definition.dims),
        "source_value_id": definition.source_value_id,
    }
    _require_logical_ref_matches("value", source_id, expected, actual)


def _require_logical_ref_matches(
    source: str,
    source_id: str,
    expected: Mapping[str, object],
    actual: Mapping[str, object],
) -> None:
    mismatches = tuple(
        name for name, value in expected.items() if actual[name] != value
    )
    if not mismatches:
        return
    rendered = ", ".join(
        f"{name}={actual[name]!r} (expected {expected[name]!r})" for name in mismatches
    )
    raise TypeError(
        f"{source} reference {source_id!r} does not match the dataset schema: "
        f"{rendered}"
    )


def _require_point_ref_matches(
    ref: CoordinateRef[object],
    definition: MeasurementVariable,
) -> None:
    point_id = internal_coordinate_ref_id(ref)
    value_type = ref.value_type
    dtype, unit = measurement_value_spec_from_scalar(value_type)
    expected = {
        "id": point_id,
        "role": "coordinate",
        "dtype": dtype,
        "unit": unit,
        "dims": ("point",),
        "source_product_id": None,
        "source_value_id": None,
        "recording_group_id": None,
    }
    actual = {
        "id": definition.id,
        "role": definition.role,
        "dtype": definition.dtype,
        "unit": definition.unit,
        "dims": tuple(definition.dims),
        "source_product_id": definition.source_product_id,
        "source_value_id": definition.source_value_id,
        "recording_group_id": definition.recording_group_id,
    }
    mismatches = tuple(
        name for name, value in expected.items() if actual[name] != value
    )
    if not mismatches:
        return
    rendered = ", ".join(
        f"{name}={actual[name]!r} (expected {expected[name]!r})" for name in mismatches
    )
    raise TypeError(
        f"point coordinate {point_id!r} does not match the dataset schema: {rendered}"
    )


def _native_leaf(value: object) -> object:
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, list | tuple):
        return tuple(_native_leaf(item) for item in value)
    return value


def _reshape_point_data_array(
    array: xr.DataArray,
    *,
    axis_ids: tuple[str, ...],
    axis_shape: tuple[int, ...],
) -> object:
    attrs = deepcopy(dict(array.attrs))
    if "point" not in array.dims:
        return (array.dims, np.array(array.values, copy=True), attrs)

    remaining_dims = tuple(dim for dim in array.dims if dim != "point")
    ordered = array.transpose("point", *remaining_dims)
    remaining_shape = tuple(ordered.sizes[dim] for dim in remaining_dims)
    values = np.asarray(ordered.values).reshape((*axis_shape, *remaining_shape))
    return ((*axis_ids, *remaining_dims), values, attrs)


def _product_axis_values(
    values: Sequence[MeasurementScalar | None],
    *,
    variable: Variable | None,
) -> np.ndarray:
    native = tuple(
        None if value is None else _native_leaf(value.value) for value in values
    )
    if variable is None:
        return np.asarray(native, dtype=np.object_ if not native else None)
    return np.asarray(
        native,
        dtype=_xarray_dtype(
            variable.dtype,
            nullable=any(value is None for value in values),
        ),
    )


def _product_axis_flat_values(
    domain: MeasurementProductGridPointDomain,
    *,
    axis_index: int,
    variable: Variable,
) -> np.ndarray:
    axis = domain.axes[axis_index]
    values = _product_axis_values(
        measurement_point_axis_values(axis),
        variable=variable,
    )
    repeated = np.repeat(
        values,
        math.prod(item.size for item in domain.axes[axis_index + 1 :]),
    )
    return np.tile(
        repeated,
        math.prod(item.size for item in domain.axes[:axis_index]),
    )


def _product_axis_attrs(
    values: Sequence[MeasurementScalar | None],
) -> dict[str, object]:
    attrs: dict[str, object] = {
        "scopecat_role": "point_domain_axis",
        "scopecat_product_grid_axis": True,
    }
    available = tuple(value for value in values if value is not None)
    units = {value.unit for value in available}
    dtypes = {value.dtype for value in available}
    if len(units) == 1:
        [unit] = units
        if unit is not None:
            attrs["units"] = unit
    if len(dtypes) == 1:
        [dtype] = dtypes
        attrs["scopecat_dtype"] = dtype
    return attrs


def _array_values_equal(left: np.ndarray, right: np.ndarray) -> bool:
    try:
        return bool(np.array_equal(left, right, equal_nan=True))
    except TypeError:
        return bool(np.array_equal(left, right))


def _require_entity_dimension(
    dataset: Dataset,
    dimension_id: str,
) -> MeasurementDimension:
    try:
        dimension = next(
            item for item in dataset.schema.dimensions if item.id == dimension_id
        )
    except StopIteration as error:
        raise KeyError(
            f"measurement dataset has no dimension {dimension_id!r}"
        ) from error
    if dimension.index is None:
        raise ValueError(
            f"measurement dimension {dimension_id!r} is not entity-indexed"
        )
    return dimension


def _visible_entity_values(
    dataset: Dataset,
    dimension_id: str,
) -> tuple[EntityRef, ...]:
    dimension = _require_entity_dimension(dataset, dimension_id)
    index = cast("MeasurementEntityIndex", dimension.index)
    return tuple(
        index.values[position]
        for position in dataset._view_dimension_positions[dimension_id]
    )


def _derived_measurement_dataset_entry(
    source: ContentEntry,
    dataset: MeasurementDataset,
    *,
    operation: Mapping[str, object],
) -> ContentEntry:
    """Describe one materialized semantic transform without reusing source identity."""

    metadata = deepcopy(source.metadata)
    metadata["scopecat_derivation"] = cast(
        "JsonValue",
        {
            "source_content_hash": source.content_hash,
            "operation": operation,
        },
    )
    return source.model_copy(
        update={
            "data_schema": dataset.dataset_schema.model_dump(mode="json"),
            "content_hash": f"sha256:{model_wire_content_hash(dataset)}",
            "metadata": metadata,
        }
    )


def _reindex_record_entities(
    record: MeasurementRecord,
    *,
    variables: Mapping[str, MeasurementVariable],
    dimension_sizes: Mapping[str, int | None],
    dimension_id: str,
    source_count: int,
    target_to_source: Sequence[int | None],
    target_to_schema: Sequence[int | None],
) -> MeasurementRecord:
    coordinates = dict(record.coordinates)
    observables = dict(record.observables)
    for variable in variables.values():
        if dimension_id not in variable.dims[1:]:
            continue
        values = coordinates if variable.role == "coordinate" else observables
        values[variable.id] = _reindex_measurement_value_entities(
            values[variable.id],
            entity_axis=variable.dims[1:].index(dimension_id),
            absent_segment_shape=tuple(
                dimension_sizes[local_dimension_id]
                for local_dimension_id in variable.dims[2:]
            ),
            dimension_id=dimension_id,
            source_count=source_count,
            target_to_source=target_to_source,
        )
    return record.model_copy(
        update={
            "coordinates": coordinates,
            "observables": observables,
            "acquisition_evidence": reindex_entity_evidence(
                record.acquisition_evidence,
                dimension_id=dimension_id,
                target_to_schema=target_to_schema,
            ),
        }
    )


def _reindex_measurement_value_entities(
    value: MeasurementValue,
    *,
    entity_axis: int,
    absent_segment_shape: tuple[int | None, ...],
    dimension_id: str,
    source_count: int,
    target_to_source: Sequence[int | None],
) -> MeasurementValue:
    if isinstance(value, MeasurementScalar):
        raise ValueError(
            f"scalar measurement value cannot use entity dimension {dimension_id!r}"
        )
    if len(value.shape) <= entity_axis:
        raise ValueError(
            f"measurement value has no axis for entity dimension {dimension_id!r}"
        )
    if value.shape[entity_axis] != source_count:
        raise ValueError(
            f"measurement value entity extent for {dimension_id!r} does not match "
            "the visible dataset index"
        )
    selected_indices = tuple(
        source_index for source_index in target_to_source if source_index is not None
    )
    if len(selected_indices) == len(target_to_source):
        return _slice_measurement_value(
            value,
            local_dimensions=tuple(
                dimension_id if axis == entity_axis else f"__axis_{axis}"
                for axis in range(len(value.shape))
            ),
            indices_by_dimension={dimension_id: selected_indices},
        )
    target_shape = tuple(
        len(target_to_source) if axis == entity_axis else extent
        for axis, extent in enumerate(value.shape)
    )
    if isinstance(value, MeasurementUnavailable):
        return value.model_copy(update={"shape": target_shape})
    if isinstance(value, MeasurementSegmentedArray):
        if entity_axis != 0:
            raise ValueError(
                "segmented measurement values require the entity dimension first"
            )
        return MeasurementSegmentedArray.create(
            segments=tuple(
                MeasurementUnavailable.create(
                    reason="missing",
                    dtype=value.dtype,
                    unit=value.unit,
                    shape=absent_segment_shape,
                    metadata={
                        "entity_alignment": "absent",
                        "dimension_id": dimension_id,
                    },
                )
                if source_position is None
                else value.segments[source_position]
                for source_position in target_to_source
            ),
            dtype=value.dtype,
            unit=value.unit,
            metadata=value.metadata,
        )
    dense_shape = cast("tuple[int, ...]", target_shape)
    target_values = np.zeros(dense_shape, dtype=value.values.dtype)
    target_valid = np.zeros(dense_shape, dtype=np.bool_)
    target_groups = np.full(dense_shape, -2, dtype=np.int64)
    source_valid = (
        np.ones(value.values.shape, dtype=np.bool_)
        if value.availability is None
        else value.availability.valid
    )
    source_groups = (
        np.full(value.values.shape, -1, dtype=np.int64)
        if value.availability is None
        else _array_unavailable_group_indices(value.availability)
    )
    target_positions = tuple(
        target_position
        for target_position, source_position in enumerate(target_to_source)
        if source_position is not None
    )
    target_values_by_entity = np.moveaxis(target_values, entity_axis, 0)
    target_valid_by_entity = np.moveaxis(target_valid, entity_axis, 0)
    target_groups_by_entity = np.moveaxis(target_groups, entity_axis, 0)
    target_values_by_entity[list(target_positions)] = np.take(
        np.moveaxis(value.values, entity_axis, 0),
        selected_indices,
        axis=0,
    )
    target_valid_by_entity[list(target_positions)] = np.take(
        np.moveaxis(source_valid, entity_axis, 0),
        selected_indices,
        axis=0,
    )
    target_groups_by_entity[list(target_positions)] = np.take(
        np.moveaxis(source_groups, entity_axis, 0),
        selected_indices,
        axis=0,
    )
    flattened_groups = cast("NDArray[np.int64]", target_groups.reshape(-1))
    unavailable: list[MeasurementArrayUnavailableGroup] = []
    if value.availability is not None:
        for group_index, group in enumerate(value.availability.unavailable):
            matching = np.flatnonzero(
                cast(
                    "NDArray[np.bool_]",
                    np.equal(flattened_groups, np.int64(group_index)),
                )
            )
            if matching.size:
                unavailable.append(
                    MeasurementArrayUnavailableGroup(
                        reason=group.reason,
                        flat_indices=tuple(int(index) for index in matching),
                        metadata=group.metadata,
                    )
                )
    absent = np.flatnonzero(
        cast(
            "NDArray[np.bool_]",
            np.equal(flattened_groups, np.int64(-2)),
        )
    )
    if absent.size:
        unavailable.append(
            MeasurementArrayUnavailableGroup(
                reason="missing",
                flat_indices=tuple(int(index) for index in absent),
                metadata={
                    "entity_alignment": "absent",
                    "dimension_id": dimension_id,
                },
            )
        )
    availability = (
        None
        if bool(np.all(target_valid))
        else MeasurementArrayAvailability(
            valid=target_valid,
            unavailable=tuple(unavailable),
        )
    )
    return MeasurementArray.create(
        dtype=value.dtype,
        unit=value.unit,
        values=target_values,
        availability=availability,
        metadata=value.metadata,
    )


def _slice_record_local_values(
    record: MeasurementRecord,
    *,
    variables: Mapping[str, Variable],
    target_variable_ids: set[str],
    indices_for: Callable[
        [MeasurementRecord, Variable, MeasurementValue],
        Mapping[str, tuple[int, ...]],
    ],
) -> MeasurementRecord:
    coordinates = dict(record.coordinates)
    observables = dict(record.observables)
    for variable_id, variable in variables.items():
        if variable_id not in target_variable_ids:
            continue
        values = coordinates if variable.role == "coordinate" else observables
        value = values[variable_id]
        selected = indices_for(record, variable, value)
        if selected:
            values[variable_id] = _slice_measurement_value(
                value,
                local_dimensions=variable.dims[1:],
                indices_by_dimension=selected,
            )
    return record.model_copy(
        update={
            "coordinates": coordinates,
            "observables": observables,
        },
    )


def _measurement_local_shape(value: MeasurementValue) -> tuple[int | None, ...]:
    if isinstance(value, MeasurementScalar):
        return ()
    return tuple(value.shape)


def _slice_measurement_value(
    value: MeasurementValue,
    *,
    local_dimensions: Sequence[str],
    indices_by_dimension: Mapping[str, tuple[int, ...]],
) -> MeasurementValue:
    if isinstance(value, MeasurementScalar):
        raise ValueError("cannot apply a local dimension indexer to a scalar value")
    if isinstance(value, MeasurementSegmentedArray):
        return _slice_segmented_array(
            value,
            indices_by_axis={
                local_dimensions.index(dimension_id): indices
                for dimension_id, indices in indices_by_dimension.items()
            },
        )
    if isinstance(value, MeasurementPartitionedArray):
        value = value.materialize()
    shape = tuple(value.shape)
    indices_by_axis = {
        local_dimensions.index(dimension_id): indices
        for dimension_id, indices in indices_by_dimension.items()
    }
    selected_shape = tuple(
        len(indices_by_axis[axis]) if axis in indices_by_axis else extent
        for axis, extent in enumerate(shape)
    )
    if isinstance(value, MeasurementUnavailable):
        return MeasurementUnavailable.create(
            reason=value.reason,
            dtype=value.dtype,
            unit=value.unit,
            shape=selected_shape,
            metadata=value.metadata,
        )
    selected_values = value.values
    selected_valid = None if value.availability is None else value.availability.valid
    selected_groups = (
        None
        if value.availability is None
        else _array_unavailable_group_indices(value.availability)
    )
    for axis, indices in sorted(indices_by_axis.items()):
        selected_values = np.take(selected_values, indices, axis=axis)
        if selected_valid is not None and selected_groups is not None:
            selected_valid = np.take(selected_valid, indices, axis=axis)
            selected_groups = np.take(selected_groups, indices, axis=axis)
    selected_availability = (
        None
        if selected_valid is None or selected_groups is None
        else _sliced_array_availability(
            selected_valid,
            selected_groups,
            cast("MeasurementArrayAvailability", value.availability),
        )
    )
    if (
        selected_availability is None
        and selected_valid is not None
        and selected_groups is not None
        and not bool(np.all(selected_valid))
    ):
        group_indices = tuple(
            dict.fromkeys(
                int(group_index)
                for group_index in selected_groups.reshape(-1)
                if int(group_index) >= 0
            )
        )
        if len(group_indices) != 1:
            raise AssertionError("all-invalid array selection lost its diagnostics")
        group = cast("MeasurementArrayAvailability", value.availability).unavailable[
            group_indices[0]
        ]
        return MeasurementUnavailable.create(
            reason=group.reason,
            dtype=value.dtype,
            unit=value.unit,
            shape=selected_shape,
            metadata={**value.metadata, **group.metadata},
        )
    return MeasurementArray.create(
        dtype=value.dtype,
        unit=value.unit,
        values=selected_values,
        availability=selected_availability,
        metadata=value.metadata,
    )


def _slice_segmented_array(
    value: MeasurementSegmentedArray,
    *,
    indices_by_axis: Mapping[int, tuple[int, ...]],
) -> MeasurementSegmentedArray:
    entity_indices = indices_by_axis.get(0, tuple(range(len(value.segments))))
    if not entity_indices:
        raise ValueError("segmented array selection must retain at least one entity")
    local_dimensions = tuple(f"__axis_{axis}" for axis in range(len(value.shape) - 1))
    local_indices = {
        local_dimensions[axis - 1]: indices
        for axis, indices in indices_by_axis.items()
        if axis != 0
    }
    segments: list[MeasurementArray | MeasurementUnavailable] = []
    for entity_index in entity_indices:
        segment = value.segments[entity_index]
        selected = (
            segment
            if not local_indices
            else _slice_measurement_value(
                segment,
                local_dimensions=local_dimensions,
                indices_by_dimension=local_indices,
            )
        )
        if isinstance(
            selected,
            MeasurementScalar | MeasurementPartitionedArray | MeasurementSegmentedArray,
        ):
            raise AssertionError("segmented array slicing produced an invalid segment")
        segments.append(selected)
    return MeasurementSegmentedArray.create(
        segments=segments,
        dtype=value.dtype,
        unit=value.unit,
        metadata=value.metadata,
    )


def _array_unavailable_group_indices(
    availability: MeasurementArrayAvailability,
) -> NDArray[np.int64]:
    selected = np.full(availability.valid.shape, -1, dtype=np.int64)
    flattened = selected.reshape(-1)
    for group_index, group in enumerate(availability.unavailable):
        flattened[list(group.flat_indices)] = group_index
    return selected


def _array_unavailable_reasons(
    availability: MeasurementArrayAvailability,
) -> NDArray[np.object_]:
    selected = np.full(availability.valid.shape, None, dtype=np.object_)
    flattened = selected.reshape(-1)
    for group in availability.unavailable:
        flattened[list(group.flat_indices)] = group.reason
    return selected


def _sliced_array_availability(
    valid: NDArray[np.bool_],
    group_indices: NDArray[np.int64],
    source: MeasurementArrayAvailability,
    *,
    collapse_all_unavailable: bool = True,
) -> MeasurementArrayAvailability | None:
    if bool(np.all(valid)):
        return None
    flattened_groups = group_indices.reshape(-1)
    unavailable_groups: list[MeasurementArrayUnavailableGroup] = []
    for group_index, group in enumerate(source.unavailable):
        matching = cast(
            "NDArray[np.bool_]",
            np.equal(flattened_groups, np.int64(group_index)),
        )
        if not bool(np.any(matching)):
            continue
        unavailable_groups.append(
            MeasurementArrayUnavailableGroup(
                reason=group.reason,
                flat_indices=tuple(int(index) for index in np.flatnonzero(matching)),
                metadata=group.metadata,
            )
        )
    unavailable = tuple(unavailable_groups)
    if collapse_all_unavailable and not bool(np.any(valid)) and len(unavailable) == 1:
        return None
    return MeasurementArrayAvailability(valid=valid, unavailable=unavailable)


def _merge_indexers[T](
    indexers: Mapping[str, T] | None,
    kwargs: Mapping[str, T],
) -> dict[str, T]:
    selected = {} if indexers is None else dict(indexers)
    duplicates = set(selected) & set(kwargs)
    if duplicates:
        raise ValueError(
            "indexers were provided twice: " + ", ".join(sorted(duplicates))
        )
    selected.update(kwargs)
    return selected


def _preserving_xarray_indexer(indexer: DimensionIndexer) -> object:
    """Use Xarray indexing while retaining durable dataset dimensions."""

    if isinstance(indexer, bool):
        raise TypeError("dimension indexers must not be a single bool")
    if isinstance(indexer, int):
        return [indexer]
    if isinstance(indexer, slice):
        return indexer
    selected = tuple(indexer)
    if not selected:
        return np.empty((0,), dtype=np.int64)
    return list(selected)


def _point_positions(dataset: Dataset, selected: xr.Dataset) -> tuple[int, ...]:
    positions = {
        record.point_index: position for position, record in enumerate(dataset._records)
    }
    point_indices = cast(
        "np.ndarray[tuple[int], np.dtype[np.int64]]",
        selected.coords["point"].values,
    )
    return tuple(
        positions[int(cast("np.int64", point_indices[index]))]
        for index in range(len(point_indices))
    )


def _group_positions(
    positions: Sequence[int] | slice,
    *,
    size: int,
) -> tuple[int, ...]:
    if isinstance(positions, slice):
        return tuple(range(size)[positions])
    return tuple(positions)


def _native_group_key(value: object) -> GroupKey:
    if isinstance(value, np.generic):
        value = value.item()
    return cast("GroupKey", value)


def _dimension_indices(
    indexer: DimensionIndexer,
    *,
    size: int,
    label: str,
) -> tuple[int, ...]:
    if isinstance(indexer, slice):
        return tuple(range(size)[indexer])
    if isinstance(indexer, int) and not isinstance(indexer, bool):
        return (_normalize_index(indexer, size=size, label=label),)
    if isinstance(indexer, bool):
        raise TypeError(f"{label} indexer must not be a single bool")
    selected = tuple(indexer)
    if selected and all(type(value) is bool for value in selected):
        if len(selected) != size:
            raise ValueError(
                f"{label} boolean indexer has length {len(selected)}; expected {size}"
            )
        return tuple(index for index, keep in enumerate(selected) if keep)
    if any(isinstance(value, bool) for value in selected):
        raise TypeError(f"{label} indexer cannot mix bool and integer values")
    return tuple(
        _normalize_index(int(value), size=size, label=label) for value in selected
    )


def _normalize_index(index: int, *, size: int, label: str) -> int:
    selected = index + size if index < 0 else index
    if selected < 0 or selected >= size:
        raise IndexError(f"{label} index {index} is out of range for extent {size}")
    return selected


def _selection_value(value: object, variable: Variable[object]) -> object:
    if isinstance(value, Quantity):
        if variable.unit is None:
            raise ValueError(
                f"coordinate {variable.id!r} is unitless; select it with a raw value"
            )
        return value.to(variable.unit).value
    if isinstance(value, MeasurementScalar):
        native = _native_value(value)
        if value.unit is None:
            return native
        if variable.unit is None:
            raise ValueError(
                f"coordinate {variable.id!r} is unitless; selected value has a unit"
            )
        if not isinstance(native, int | float) or isinstance(native, bool):
            raise TypeError("unit-bearing coordinate selectors must be numeric")
        return Quantity(float(native), value.unit).to(variable.unit).value
    return value


def _entity_coordinate_label(
    index: MeasurementEntityIndex,
    entity: EntityRef,
) -> str:
    if index.entity_kind is not None:
        return entity.id
    return entity_identity_key(entity)


def _entity_selection_positions(
    index: MeasurementEntityIndex,
    current_positions: Sequence[int],
    query: object,
    *,
    dimension_id: str,
) -> tuple[int, ...]:
    if isinstance(query, EntityRef | str):
        requested = (query,)
    elif isinstance(query, Sequence):
        requested = tuple(query)
    else:
        raise TypeError(
            f"entity dimension {dimension_id!r} selectors must be an entity, "
            "string label, or sequence of them"
        )
    available = tuple(index.values[position] for position in current_positions)
    selected: list[int] = []
    for item in requested:
        matches = tuple(
            position
            for position, entity in enumerate(available)
            if (
                entity_identity(entity) == entity_identity(item)
                if isinstance(item, EntityRef)
                else isinstance(item, str)
                and (
                    _entity_coordinate_label(index, entity) == item
                    or (
                        item == entity.id
                        and sum(candidate.id == item for candidate in available) == 1
                    )
                )
            )
        )
        if not matches:
            raise KeyError(f"entity dimension {dimension_id!r} has no label {item!r}")
        if len(matches) != 1:
            raise KeyError(
                f"entity dimension {dimension_id!r} label {item!r} is ambiguous"
            )
        selected.append(matches[0])
    return tuple(selected)


def _selection_tolerance(
    tolerance: float | Quantity | None,
    variable: Variable[object],
) -> float | None:
    if tolerance is None:
        return None
    selected = _selection_value(tolerance, variable)
    if not isinstance(selected, int | float) or isinstance(selected, bool):
        raise TypeError("nearest-selection tolerance must be numeric")
    numeric = float(selected)
    if numeric < 0:
        raise ValueError("nearest-selection tolerance must not be negative")
    return numeric


def _xarray_values(variable: Variable) -> object:
    fixed_shape = tuple(cast("int", extent) for extent in variable.shape)
    raw_values = variable._raw_values
    has_unavailable = any(
        isinstance(value, MeasurementUnavailable)
        or (
            isinstance(value, MeasurementArray | MeasurementPartitionedArray)
            and value.availability is not None
        )
        for value in raw_values
    )
    dtype = _xarray_dtype(variable.dtype, nullable=has_unavailable)
    if not raw_values:
        return np.empty(fixed_shape, dtype=dtype)

    local_shape = fixed_shape[1:]
    if not local_shape:
        values: list[object] = []
        for value in raw_values:
            if isinstance(value, MeasurementUnavailable):
                values.append(_xarray_nullable_fill(variable.dtype))
            else:
                values.append(cast("MeasurementScalar", value).value)
        return np.asarray(values, dtype=dtype)

    arrays: list[np.ndarray] = []
    for value in raw_values:
        if isinstance(value, MeasurementUnavailable):
            arrays.append(
                np.full(
                    local_shape,
                    _xarray_nullable_fill(variable.dtype),
                    dtype=dtype,
                )
            )
        else:
            array = cast("MeasurementArray | MeasurementPartitionedArray", value)
            if array.availability is None:
                arrays.append(array.values)
                continue
            selected = array.values.astype(dtype, copy=True)
            selected[~array.availability.valid] = np.asarray(
                _xarray_nullable_fill(variable.dtype),
                dtype=dtype,
            )
            arrays.append(selected)
    return np.stack(arrays, axis=0).astype(dtype, copy=False)


def _xarray_fixed_availability(
    variable: Variable,
) -> tuple[np.ndarray, np.ndarray] | None:
    raw_values = variable._raw_values
    if not any(
        isinstance(value, MeasurementUnavailable)
        or (
            isinstance(value, MeasurementArray | MeasurementPartitionedArray)
            and value.availability is not None
        )
        for value in raw_values
    ):
        return None
    fixed_shape = tuple(cast("int", extent) for extent in variable.shape)
    local_shape = fixed_shape[1:]
    valid_chunks: list[np.ndarray] = []
    reason_chunks: list[np.ndarray] = []
    for value in raw_values:
        if isinstance(value, MeasurementUnavailable):
            valid_chunks.append(np.zeros(local_shape or (), dtype=np.bool_))
            reason_chunks.append(
                np.full(local_shape or (), value.reason, dtype=np.object_)
            )
        elif isinstance(value, MeasurementArray | MeasurementPartitionedArray):
            if value.availability is None:
                valid_chunks.append(np.ones(local_shape, dtype=np.bool_))
                reason_chunks.append(np.full(local_shape, None, dtype=np.object_))
            else:
                valid_chunks.append(value.availability.valid)
                reason_chunks.append(_array_unavailable_reasons(value.availability))
        else:
            valid_chunks.append(np.asarray(True, dtype=np.bool_))
            reason_chunks.append(np.asarray(None, dtype=np.object_))
    return np.stack(valid_chunks, axis=0), np.stack(reason_chunks, axis=0)


def _variable_is_ragged(variable: Variable[object]) -> bool:
    return any(extent is None for extent in variable.shape[1:])


def _labeled_measurement_array(variable: Variable[object]) -> LabeledMeasurementArray:
    dataset = variable._dataset._loaded_xarray
    data = dataset[variable.id].copy(deep=True)
    layout: MeasurementArrayLayout = variable.layout
    valid_name = (
        _ragged_valid_name(variable.id)
        if layout == "ragged"
        else _valid_name(variable.id)
    )
    reason_name = (
        _ragged_unavailable_reason_name(variable.id)
        if layout == "ragged"
        else _unavailable_reason_name(variable.id)
    )
    valid = (
        dataset[valid_name].copy(deep=True)
        if valid_name in dataset
        else xr.DataArray(
            np.ones(data.shape, dtype=np.bool_),
            dims=data.dims,
            coords=data.coords,
        )
    )
    unavailable_reasons = (
        dataset[reason_name].copy(deep=True)
        if reason_name in dataset
        else xr.DataArray(
            np.full(data.shape, None, dtype=np.object_),
            dims=data.dims,
            coords=data.coords,
        )
    )
    return LabeledMeasurementArray(
        _data=data,
        _valid=valid,
        _unavailable_reasons=unavailable_reasons,
        layout=layout,
        declared_dims=variable.dims,
        dtype=variable.dtype,
        unit=variable.unit,
    )


def _read_only_array(value: object) -> NDArray[np.generic]:
    selected = np.asarray(value).copy(order="C")
    selected.flags.writeable = False
    return selected


def _xarray_ragged_values(
    variable: Variable,
    *,
    layout: _RaggedXarrayLayout,
) -> _RaggedXarrayValues:
    """Flatten point-local arrays without constructing a NumPy object array."""

    raw_values = variable._raw_values
    dtype = _xarray_dtype(variable.dtype, nullable=False)
    chunks: list[np.ndarray[tuple[int], np.dtype[np.generic]]] = []
    valid_chunks: list[np.ndarray[tuple[int], np.dtype[np.bool_]]] = []
    reason_chunks: list[np.ndarray[tuple[int], np.dtype[np.object_]]] = []
    for row_size, raw_value in zip(
        layout.row_sizes,
        raw_values,
        strict=True,
    ):
        if isinstance(raw_value, MeasurementScalar):
            raise ValueError(
                f"ragged variable {variable.id!r} must contain point-local arrays"
            )
        if isinstance(raw_value, MeasurementUnavailable):
            chunks.append(np.full(row_size, _xarray_fill(variable.dtype), dtype=dtype))
            valid_chunks.append(np.zeros(row_size, dtype=np.bool_))
            reason_chunks.append(np.full(row_size, raw_value.reason, dtype=np.object_))
        else:
            flattened = raw_value.values.reshape(-1)
            if flattened.size != row_size:
                raise ValueError(
                    f"ragged variable {variable.id!r} contributes "
                    f"{flattened.size} values to a {row_size}-observation row"
                )
            if raw_value.availability is None:
                chunks.append(flattened)
                valid_chunks.append(np.ones(row_size, dtype=np.bool_))
                reason_chunks.append(np.full(row_size, None, dtype=np.object_))
            else:
                flattened_valid = raw_value.availability.valid.reshape(-1)
                flattened_reasons = _array_unavailable_reasons(
                    raw_value.availability
                ).reshape(-1)
                selected = flattened.copy()
                selected[~flattened_valid] = _xarray_fill(variable.dtype)
                chunks.append(selected)
                valid_chunks.append(flattened_valid)
                reason_chunks.append(flattened_reasons)

    values = _concatenate_or_empty(chunks, dtype=dtype)
    valid = _concatenate_or_empty(valid_chunks, dtype=np.dtype(np.bool_))
    unavailable_reasons = _concatenate_or_empty(
        reason_chunks,
        dtype=np.dtype(np.object_),
    )
    return _RaggedXarrayValues(
        values=values,
        valid=valid,
        unavailable_reasons=unavailable_reasons,
        layout=layout,
    )


def _xarray_dtype(
    dtype: MeasurementDType,
    *,
    nullable: bool,
) -> np.dtype[np.generic]:
    if nullable and dtype in {"int64", "bool", "string"}:
        return np.dtype(np.object_)
    return {
        "float64": np.dtype(np.float64),
        "int64": np.dtype(np.int64),
        "complex128": np.dtype(np.complex128),
        "bool": np.dtype(np.bool_),
        "string": np.dtype(np.str_),
    }[dtype]


def _xarray_fill(dtype: MeasurementDType) -> XarrayNonNullFill:
    if dtype == "float64":
        return math.nan
    if dtype == "complex128":
        return complex(math.nan, math.nan)
    if dtype == "int64":
        return 0
    if dtype == "bool":
        return False
    return ""


def _xarray_nullable_fill(dtype: MeasurementDType) -> XarrayFill:
    if dtype in {"int64", "bool", "string"}:
        return None
    return _xarray_fill(dtype)


def _concatenate_or_empty(
    chunks: Sequence[np.ndarray],
    *,
    dtype: np.dtype[np.generic],
) -> np.ndarray:
    if not chunks:
        return np.empty((0,), dtype=dtype)
    return np.concatenate(chunks).astype(dtype, copy=False)


def _ragged_xarray_layout(
    variable: Variable,
    *,
    records: Sequence[MeasurementRecord],
) -> _RaggedXarrayLayout:
    if any(
        isinstance(value, MeasurementSegmentedArray) for value in variable._raw_values
    ):
        return _entity_ragged_xarray_layout(variable, records=records)
    local_extents: list[list[int | None]] = [[] for _dimension_id in variable.dims[1:]]
    for raw_value in variable._raw_values:
        if isinstance(raw_value, MeasurementScalar):
            raise ValueError(
                f"ragged variable {variable.id!r} must contain point-local arrays"
            )
        local_shape = tuple(raw_value.shape)
        if len(local_shape) != len(variable.dims) - 1:
            raise ValueError(
                f"ragged variable {variable.id!r} value rank {len(local_shape)} "
                f"does not match {len(variable.dims) - 1} local dimensions"
            )
        for axis, extent in enumerate(local_shape):
            local_extents[axis].append(extent)
    return _ragged_layout_from_extents(
        tuple(tuple(extents) for extents in local_extents),
        records=records,
    )


def _merge_ragged_xarray_layouts(
    left: _RaggedXarrayLayout,
    right: _RaggedXarrayLayout,
    *,
    records: Sequence[MeasurementRecord],
) -> _RaggedXarrayLayout:
    merged_axes: list[tuple[int | None, ...]] = []
    for left_axis, right_axis in zip(
        left.local_extents,
        right.local_extents,
        strict=True,
    ):
        merged_axis: list[int | None] = []
        for left_extent, right_extent in zip(left_axis, right_axis, strict=True):
            if (
                left_extent is not None
                and right_extent is not None
                and left_extent != right_extent
            ):
                raise ValueError("ragged point-local extents differ")
            merged_axis.append(left_extent if left_extent is not None else right_extent)
        merged_axes.append(tuple(merged_axis))
    if left.explicit_indices or right.explicit_indices:
        explicit = left if left.explicit_indices else right
        other = right if left.explicit_indices else left
        if (
            left.explicit_indices
            and right.explicit_indices
            and (
                left.row_sizes != right.row_sizes
                or left.local_indices != right.local_indices
            )
        ):
            raise ValueError("ragged entity-local indices differ")
        if any(
            other_size not in {0, explicit_size}
            for other_size, explicit_size in zip(
                other.row_sizes,
                explicit.row_sizes,
                strict=True,
            )
        ):
            raise ValueError("ragged point-local extents differ")
        return _RaggedXarrayLayout(
            parent_point_indices=explicit.parent_point_indices,
            row_sizes=explicit.row_sizes,
            local_indices=explicit.local_indices,
            local_extents=tuple(merged_axes),
            explicit_indices=True,
        )
    return _ragged_layout_from_extents(tuple(merged_axes), records=records)


def _entity_ragged_xarray_layout(
    variable: Variable,
    *,
    records: Sequence[MeasurementRecord],
) -> _RaggedXarrayLayout:
    local_extents: list[list[int | None]] = [[] for _dimension_id in variable.dims[1:]]
    parent_points: list[int] = []
    row_sizes: list[int] = []
    local_indices: list[list[int]] = [[] for _dimension_id in variable.dims[1:]]
    for record, raw_value in zip(records, variable._raw_values, strict=True):
        if isinstance(raw_value, MeasurementScalar):
            raise ValueError(
                f"ragged variable {variable.id!r} must contain point-local arrays"
            )
        local_shape = tuple(raw_value.shape)
        if len(local_shape) != len(variable.dims) - 1:
            raise ValueError(
                f"ragged variable {variable.id!r} value rank {len(local_shape)} "
                f"does not match {len(variable.dims) - 1} local dimensions"
            )
        for axis, extent in enumerate(local_shape):
            local_extents[axis].append(extent)
        if isinstance(raw_value, MeasurementUnavailable):
            row_sizes.append(0)
            continue
        if not isinstance(raw_value, MeasurementSegmentedArray):
            concrete_shape = cast("tuple[int, ...]", local_shape)
            indices = cartesian_product(*(range(extent) for extent in concrete_shape))
        else:
            indices = (
                (entity_index, *local_index)
                for entity_index, segment in enumerate(raw_value.segments)
                if all(extent is not None for extent in segment.shape)
                for local_index in cartesian_product(
                    *(
                        range(extent)
                        for extent in cast("tuple[int, ...]", segment.shape)
                    )
                )
            )
        count = 0
        for local_index in indices:
            count += 1
            parent_points.append(record.point_index)
            for axis, index in enumerate(local_index):
                local_indices[axis].append(index)
        row_sizes.append(count)
    return _RaggedXarrayLayout(
        parent_point_indices=tuple(parent_points),
        row_sizes=tuple(row_sizes),
        local_indices=tuple(tuple(indices) for indices in local_indices),
        local_extents=tuple(tuple(extents) for extents in local_extents),
        explicit_indices=True,
    )


def _ragged_layout_from_extents(
    local_extents: tuple[tuple[int | None, ...], ...],
    *,
    records: Sequence[MeasurementRecord],
) -> _RaggedXarrayLayout:
    parent_points: list[int] = []
    row_sizes: list[int] = []
    local_indices: list[list[int]] = [[] for _axis in local_extents]
    for point_position, record in enumerate(records):
        local_shape = tuple(
            axis_extents[point_position] for axis_extents in local_extents
        )
        if any(extent is None for extent in local_shape):
            row_sizes.append(0)
            continue
        concrete_shape = cast("tuple[int, ...]", local_shape)
        indices = tuple(
            cartesian_product(*(range(extent) for extent in concrete_shape))
        )
        row_sizes.append(len(indices))
        for local_index in indices:
            parent_points.append(record.point_index)
            for axis, index in enumerate(local_index):
                local_indices[axis].append(index)
    return _RaggedXarrayLayout(
        parent_point_indices=tuple(parent_points),
        row_sizes=tuple(row_sizes),
        local_indices=tuple(tuple(indices) for indices in local_indices),
        local_extents=local_extents,
    )


def _ragged_alignment_key(variable: Variable) -> _RaggedAlignmentKey:
    group = variable.recording_group_id
    return _RaggedAlignmentKey(
        recording_group_id=group,
        variable_id=variable.id if group is None else None,
        local_dimensions=variable.dims[1:],
    )


def _ragged_alignment_id(alignment: _RaggedAlignmentKey) -> str:
    owner = alignment.recording_group_id or cast("str", alignment.variable_id)
    return "__".join((owner, *alignment.local_dimensions))


def _ragged_alignment_attrs(
    alignment: _RaggedAlignmentKey,
) -> dict[str, object]:
    attrs: dict[str, object] = {
        "source_dimensions": alignment.local_dimensions,
    }
    if alignment.recording_group_id is not None:
        attrs["source_recording_group_id"] = alignment.recording_group_id
    else:
        attrs["source_variable"] = cast("str", alignment.variable_id)
    return attrs


def _ragged_observation_dim(alignment_id: str) -> str:
    return f"{alignment_id}__observation"


def _ragged_parent_point_index_name(alignment_id: str) -> str:
    return f"{alignment_id}__parent_point_index"


def _ragged_row_size_name(variable_id: str) -> str:
    return f"{variable_id}__row_size"


def _ragged_local_index_name(variable_id: str, dimension_id: str) -> str:
    return f"{variable_id}__{dimension_id}_index"


def _ragged_local_extent_name(variable_id: str, dimension_id: str) -> str:
    return f"{variable_id}__{dimension_id}_extent"


def _ragged_valid_name(variable_id: str) -> str:
    return f"{variable_id}__observation_valid"


def _ragged_unavailable_reason_name(variable_id: str) -> str:
    return f"{variable_id}__observation_unavailable_reason"


def _variable_attrs(variable: Variable) -> dict[str, object]:
    attrs: dict[str, object] = {
        "scopecat_role": variable.role,
        "scopecat_dtype": variable.dtype,
        "scopecat_dims_json": _stable_json(variable.dims),
        "scopecat_metadata_json": _stable_json(variable.metadata),
    }
    if variable.unit is not None:
        attrs["units"] = variable.unit
    if variable.label is not None:
        attrs["long_name"] = variable.label
    if variable.recording_group_id is not None:
        attrs["scopecat_recording_group_id"] = variable.recording_group_id
    return attrs


def _dataset_attrs(dataset: Dataset) -> dict[str, object]:
    return {
        "scopecat_dataset_id": dataset._schema.dataset_id,
        "scopecat_format_version": dataset._schema.format_version,
        "scopecat_entry_id": dataset._entry.id,
        "scopecat_entry_role": dataset._entry.role,
        "scopecat_entry_kind": dataset._entry.kind,
        "scopecat_content_hash": dataset._entry.content_hash,
        "scopecat_schema_json": _stable_json(dataset._schema.model_dump(mode="json")),
        "scopecat_metadata_json": _stable_json(dataset._materialize().metadata),
    }


def _stable_json(value: object) -> str:
    return json.dumps(
        thaw_json_value(value),
        separators=(",", ":"),
        sort_keys=True,
    )


def _unavailable_reason_name(variable_id: str) -> str:
    return f"{variable_id}__unavailable_reason"


def _valid_name(variable_id: str) -> str:
    return f"{variable_id}__valid"


__all__ = [
    "Dataset",
    "NativeAvailableValue",
    "NativeMagnitude",
    "PointMask",
    "Variable",
]
