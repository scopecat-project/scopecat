# pyright: reportUnknownArgumentType=false, reportUnknownMemberType=false
# pyright: reportUnknownParameterType=false, reportUnknownVariableType=false
"""Adapter-neutral measurement projections and ecosystem exports."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from dataclasses import field as dataclass_field
from importlib import import_module
from types import MappingProxyType
from typing import TYPE_CHECKING, Literal, Protocol, Self, cast
from warnings import warn

import numpy as np
import pyarrow as pa
import xarray as xr

from scopecat.kernel.entity import EntityRef
from scopecat.kernel.frozen import thaw_json_value
from scopecat.kernel.quantity import Quantity
from scopecat.measurements.arrow_values import measurement_values_to_arrow_array
from scopecat.measurements.datasets import MAX_MEASUREMENT_PAGE_SIZE
from scopecat.measurements.entity_selection import (
    MeasurementEntitySelection,
    bind_entity_selection,
)
from scopecat.program.measurement_types import MeasurementDType, MeasurementVariableRole
from scopecat.records.measurement import (
    MeasurementArray,
    MeasurementDatasetSchema,
    MeasurementPartitionedArray,
    MeasurementScalar,
    MeasurementSegmentedArray,
    MeasurementUnavailable,
    MeasurementValue,
)

if TYPE_CHECKING:
    import pandas as pd
    import polars as pl

type ProjectionDiagnostics = Literal["none", "reason", "full"]
type ProjectionLayout = Literal["points", "observations"]
type PandasDTypeBackend = Literal["numpy", "pyarrow"]


class ImplicitPandasUnitWarning(UserWarning):
    """Pandas magnitudes inherited units not selected at projection time."""


class _PolarsModule(Protocol):
    def from_arrow(self, data: pa.Table) -> object: ...


class _PandasModule(Protocol):
    ArrowDtype: Callable[[pa.DataType], object]


class _ProjectionVariable(Protocol):
    @property
    def id(self) -> str: ...

    @property
    def dtype(self) -> MeasurementDType: ...

    @property
    def unit(self) -> str | None: ...

    @property
    def dims(self) -> tuple[str, ...]: ...

    @property
    def shape(self) -> tuple[int | None, ...]: ...

    @property
    def role(self) -> MeasurementVariableRole: ...

    @property
    def recording_group_id(self) -> str | None: ...

    @property
    def label(self) -> str | None: ...

    @property
    def raw_values(self) -> tuple[MeasurementValue, ...]: ...


class _ProjectionDataset(Protocol):
    @property
    def schema(self) -> MeasurementDatasetSchema: ...

    @property
    def metadata(self) -> Mapping[str, object]: ...

    @property
    def point_indices(self) -> tuple[int, ...]: ...

    @property
    def logical_point_ids(self) -> tuple[str | None, ...]: ...

    def __getitem__(self, variable_id: str) -> _ProjectionVariable: ...

    def to_xarray(self) -> xr.Dataset: ...

    def reindex_entities(
        self, dimension_id: str, entities: Sequence[EntityRef], /
    ) -> _ProjectionDataset: ...

    def _read_projection_batches(
        self,
        projection: ProjectionSchema,
        *,
        batch_size: int,
    ) -> pa.RecordBatchReader | None: ...


@dataclass(frozen=True, slots=True)
class ProjectionField:
    """One stable external column bound to a durable measurement variable."""

    name: str
    variable_id: str
    dtype: MeasurementDType
    unit: str | None
    dims: tuple[str, ...]
    role: MeasurementVariableRole
    source_path: tuple[str, ...] | None = None
    recording_group_id: str | None = None
    label: str | None = None


@dataclass(frozen=True, slots=True)
class ProjectionSpec:
    """Atomic adapter configuration applied while external names are bound."""

    units: tuple[tuple[str, str], ...] = ()
    diagnostics: ProjectionDiagnostics = "none"
    include_identity: bool = True
    layout: ProjectionLayout = "points"

    @classmethod
    def create(
        cls,
        *,
        units: Mapping[str, str] | None = None,
        diagnostics: ProjectionDiagnostics = "none",
        include_identity: bool = True,
        layout: ProjectionLayout = "points",
    ) -> ProjectionSpec:
        if diagnostics not in {"none", "reason", "full"}:
            raise ValueError("projection diagnostics must be none, reason, or full")
        if layout not in {"points", "observations"}:
            raise ValueError("projection layout must be points or observations")
        return cls(
            units=tuple((units or {}).items()),
            diagnostics=diagnostics,
            include_identity=include_identity,
            layout=layout,
        )


@dataclass(frozen=True, slots=True)
class ProjectionSchema:
    """Runtime semantic schema retained when data enters an ecosystem library."""

    dataset_id: str
    fields: tuple[ProjectionField, ...]
    diagnostics: ProjectionDiagnostics = "none"
    include_identity: bool = True
    layout: ProjectionLayout = "points"
    entity_selection: MeasurementEntitySelection | None = None
    schema_id: str = "scopecat.measurement-data-projection.v2"

    @property
    def units(self) -> Mapping[str, str | None]:
        """Return external field names mapped to their projected units."""

        return MappingProxyType({field.name: field.unit for field in self.fields})


@dataclass(frozen=True, slots=True)
class MeasurementDataProjection:
    """A semantic selection that exports native Arrow, Xarray, and frames."""

    dataset: _ProjectionDataset
    schema: ProjectionSchema
    _explicit_unit_fields: frozenset[str] = dataclass_field(
        default_factory=frozenset,
        repr=False,
    )

    def select_entities(
        self, dimension_id: str, entities: Sequence[EntityRef], /
    ) -> Self:
        """Select one entity axis before bounded reads, retaining missing identities.

        Order follows the request. Stored metadata and acquisition evidence remain
        authoritative; only absent identities use the requested description.
        """
        selection = MeasurementEntitySelection(
            dimension_id=dimension_id, entities=tuple(entities)
        )
        bind_entity_selection(self.dataset.schema, selection)
        return replace(self, schema=replace(self.schema, entity_selection=selection))

    def _selected_local(self) -> Self:
        selection = self.schema.entity_selection
        assert selection is not None
        return replace(
            self,
            dataset=self.dataset.reindex_entities(
                selection.dimension_id, selection.entities
            ),
            schema=replace(self.schema, entity_selection=None),
        )

    @property
    def units(self) -> Mapping[str, str | None]:
        """Return the final unit contract used by every ecosystem adapter."""

        return self.schema.units

    @property
    def implicit_units(self) -> Mapping[str, str]:
        """Return unit-bearing fields not explicitly selected by the caller."""

        return MappingProxyType(
            {
                field.name: field.unit
                for field in self.schema.fields
                if field.unit is not None
                and field.name not in self._explicit_unit_fields
            }
        )

    def with_units(self, **units: str) -> Self:
        """Return a projection with explicit output units for named fields."""

        unknown = set(units) - {field.name for field in self.schema.fields}
        if unknown:
            raise KeyError("projection has no fields: " + ", ".join(sorted(unknown)))
        selected: list[ProjectionField] = []
        for field in self.schema.fields:
            target = units.get(field.name)
            if target is None or target == field.unit:
                selected.append(field)
                continue
            if field.unit is None:
                raise TypeError(f"projection field {field.name!r} is not unit-bearing")
            Quantity(1.0, field.unit).to(target)
            dtype: MeasurementDType = (
                "float64" if field.dtype == "int64" else field.dtype
            )
            selected.append(replace(field, dtype=dtype, unit=target))
        return replace(
            self,
            schema=replace(self.schema, fields=tuple(selected)),
            _explicit_unit_fields=(self._explicit_unit_fields | frozenset(units)),
        )

    def with_diagnostics(self, diagnostics: ProjectionDiagnostics) -> Self:
        """Choose a stable unavailable-value representation for every field."""

        if diagnostics not in {"none", "reason", "full"}:
            raise ValueError("projection diagnostics must be none, reason, or full")
        _validate_external_names(
            self.schema.fields,
            diagnostics=diagnostics,
            include_identity=self.schema.include_identity,
            layout=self.schema.layout,
        )
        return replace(self, schema=replace(self.schema, diagnostics=diagnostics))

    def with_identity(self, include: bool = True) -> Self:
        """Choose whether tabular exports include durable point identity columns."""

        _validate_external_names(
            self.schema.fields,
            diagnostics=self.schema.diagnostics,
            include_identity=include,
            layout=self.schema.layout,
        )
        return replace(self, schema=replace(self.schema, include_identity=include))

    def with_layout(self, layout: ProjectionLayout) -> Self:
        """Choose point rows or aligned point-local observation rows."""

        if layout not in {"points", "observations"}:
            raise ValueError("projection layout must be points or observations")
        _validate_external_names(
            self.schema.fields,
            diagnostics=self.schema.diagnostics,
            include_identity=self.schema.include_identity,
            layout=layout,
        )
        if layout == "observations":
            _observation_dimensions(self.schema.fields)
        return replace(self, schema=replace(self.schema, layout=layout))

    def to_arrow(self) -> pa.Table:
        """Materialize the selected stable Arrow representation."""

        reader = self.dataset._read_projection_batches(  # pyright: ignore[reportPrivateUsage]
            self.schema,
            batch_size=MAX_MEASUREMENT_PAGE_SIZE,
        )
        if reader is not None:
            return reader.read_all()
        return self._to_local_arrow()

    def _to_local_arrow(self) -> pa.Table:
        """Project an already materialized or sliced dataset locally."""

        if self.schema.entity_selection is not None:
            return self._selected_local()._to_local_arrow()
        if self.schema.layout == "observations":
            return self._to_observations_arrow()
        return self._to_points_arrow()

    def _to_points_arrow(self) -> pa.Table:
        """Materialize the canonical point-row Arrow representation."""

        arrays: list[pa.Array] = []
        arrow_fields: list[pa.Field] = []
        if self.schema.include_identity:
            arrays.extend(
                (
                    pa.array(self.dataset.point_indices, type=pa.int64()),
                    pa.array(self.dataset.logical_point_ids, type=pa.string()),
                )
            )
            arrow_fields.extend(
                (
                    pa.field(
                        "point_index",
                        pa.int64(),
                        nullable=False,
                        metadata={b"scopecat.role": b"point_identity"},
                    ),
                    pa.field(
                        "logical_point_id",
                        pa.string(),
                        metadata={b"scopecat.role": b"logical_point_identity"},
                    ),
                )
            )
        for field in self.schema.fields:
            variable = self.dataset[field.variable_id]
            values = _projected_values(variable, field)
            array = measurement_values_to_arrow_array(
                values,
                dtype=field.dtype,
                shape=variable.shape[1:],
            )
            arrays.append(array)
            arrow_fields.append(
                pa.field(
                    field.name,
                    array.type,
                    metadata=_field_metadata(field),
                )
            )
            if self.schema.diagnostics != "none":
                reasons, metadata = _unavailable_columns(variable.raw_values)
                arrays.append(pa.array(reasons, type=pa.string()))
                arrow_fields.append(
                    pa.field(
                        f"{field.name}__unavailable_reason",
                        pa.string(),
                        metadata={
                            b"scopecat.role": b"availability",
                            b"scopecat.source": field.name.encode(),
                        },
                    )
                )
                if self.schema.diagnostics == "full":
                    arrays.append(pa.array(metadata, type=pa.large_string()))
                    arrow_fields.append(
                        pa.field(
                            f"{field.name}__unavailable_metadata",
                            pa.large_string(),
                            metadata={
                                b"scopecat.role": b"availability_metadata",
                                b"scopecat.source": field.name.encode(),
                            },
                        )
                    )
        schema = pa.schema(arrow_fields, metadata=self._arrow_schema_metadata())
        return pa.Table.from_arrays(arrays, schema=schema)

    def _to_observations_arrow(self) -> pa.Table:
        local_dimensions = _observation_dimensions(self.schema.fields)
        projected = {
            field.name: _projected_values(self.dataset[field.variable_id], field)
            for field in self.schema.fields
        }
        declared_shapes = {
            field.name: self.dataset[field.variable_id].shape[1:]
            for field in self.schema.fields
            if len(field.dims) > 1
        }
        values_by_field: dict[str, list[MeasurementValue]] = {
            field.name: [] for field in self.schema.fields
        }
        point_indices: list[int] = []
        logical_point_ids: list[str | None] = []
        local_indices: dict[str, list[int]] = {
            dimension: [] for dimension in local_dimensions
        }
        for position, (point_index, logical_point_id) in enumerate(
            zip(
                self.dataset.point_indices,
                self.dataset.logical_point_ids,
                strict=True,
            )
        ):
            shape = _point_observation_shape(
                self.schema.fields,
                projected=projected,
                declared_shapes=declared_shapes,
                position=position,
            )
            for raw_local_index in np.ndindex(shape):
                local_index = cast("tuple[int, ...]", raw_local_index)
                point_indices.append(point_index)
                logical_point_ids.append(logical_point_id)
                for dimension, index in zip(
                    local_dimensions,
                    local_index,
                    strict=True,
                ):
                    local_indices[dimension].append(index)
                for field in self.schema.fields:
                    values_by_field[field.name].append(
                        _observation_value(
                            projected[field.name][position],
                            field=field,
                            local_index=local_index,
                        )
                    )

        arrays: list[pa.Array] = []
        arrow_fields: list[pa.Field] = []
        if self.schema.include_identity:
            arrays.extend(
                (
                    pa.array(point_indices, type=pa.int64()),
                    pa.array(logical_point_ids, type=pa.string()),
                )
            )
            arrow_fields.extend(
                (
                    pa.field(
                        "point_index",
                        pa.int64(),
                        nullable=False,
                        metadata={b"scopecat.role": b"point_identity"},
                    ),
                    pa.field(
                        "logical_point_id",
                        pa.string(),
                        metadata={b"scopecat.role": b"logical_point_identity"},
                    ),
                )
            )
        for dimension in local_dimensions:
            arrays.append(pa.array(local_indices[dimension], type=pa.int64()))
            arrow_fields.append(
                pa.field(
                    f"{dimension}_index",
                    pa.int64(),
                    nullable=False,
                    metadata={
                        b"scopecat.role": b"local_dimension_index",
                        b"scopecat.dimension": dimension.encode(),
                    },
                )
            )
        for field in self.schema.fields:
            values = values_by_field[field.name]
            array = measurement_values_to_arrow_array(
                values,
                dtype=field.dtype,
                shape=(),
            )
            arrays.append(array)
            arrow_fields.append(
                pa.field(field.name, array.type, metadata=_field_metadata(field))
            )
            if self.schema.diagnostics != "none":
                reasons, metadata = _unavailable_columns(values)
                arrays.append(pa.array(reasons, type=pa.string()))
                arrow_fields.append(
                    pa.field(
                        f"{field.name}__unavailable_reason",
                        pa.string(),
                        metadata={
                            b"scopecat.role": b"availability",
                            b"scopecat.source": field.name.encode(),
                        },
                    )
                )
                if self.schema.diagnostics == "full":
                    arrays.append(pa.array(metadata, type=pa.large_string()))
                    arrow_fields.append(
                        pa.field(
                            f"{field.name}__unavailable_metadata",
                            pa.large_string(),
                            metadata={
                                b"scopecat.role": b"availability_metadata",
                                b"scopecat.source": field.name.encode(),
                            },
                        )
                    )
        return pa.Table.from_arrays(
            arrays,
            schema=pa.schema(
                arrow_fields,
                metadata=self._arrow_schema_metadata(),
            ),
        )

    def _arrow_schema_metadata(self) -> dict[bytes, bytes]:
        projection = asdict(self.schema)
        if self.schema.entity_selection is not None:
            projection["entity_selection"] = self.schema.entity_selection.model_dump(
                mode="json"
            )
        return {
            b"scopecat.dataset_id": self.schema.dataset_id.encode(),
            b"scopecat.projection": _stable_json(projection).encode(),
            b"scopecat.schema": self.dataset.schema.model_dump_json().encode(),
            b"scopecat.metadata": _stable_json(dict(self.dataset.metadata)).encode(),
        }

    def to_record_batch_reader(self, *, batch_size: int = 100) -> pa.RecordBatchReader:
        """Read this projection as one finite, snapshot-pinned Arrow stream.

        A lazy run-backed dataset pushes the selected variables, names, units,
        diagnostics, identity policy, and layout into daemon paging. The first
        page pins the durable point count. A materialized or sliced dataset is
        projected once locally and divided into batches. ``batch_size`` bounds
        source points; observation layout can expand a point into multiple
        returned rows, which are split again to keep Arrow batches bounded.
        """

        if not 1 <= batch_size <= MAX_MEASUREMENT_PAGE_SIZE:
            raise ValueError(
                f"record batch_size must be between 1 and {MAX_MEASUREMENT_PAGE_SIZE}"
            )
        reader = self.dataset._read_projection_batches(  # pyright: ignore[reportPrivateUsage]
            self.schema,
            batch_size=batch_size,
        )
        if reader is not None:
            return reader
        table = self._to_local_arrow()
        return pa.RecordBatchReader.from_batches(
            table.schema,
            table.to_batches(max_chunksize=batch_size),
        )

    def to_xarray(self) -> xr.Dataset:
        """Project aliases and units while preserving Xarray dimension semantics."""

        if self.schema.entity_selection is not None:
            return self._selected_local().to_xarray()
        source = self.dataset.to_xarray()
        point = source.coords["point"]
        coords: dict[str, object] = {
            "point": (point.dims, point.values.copy(), dict(point.attrs))
        }
        if self.schema.include_identity and "logical_point_id" in source.coords:
            logical_point_id = source.coords["logical_point_id"]
            coords["logical_point_id"] = (
                logical_point_id.dims,
                logical_point_id.values.copy(),
                dict(logical_point_id.attrs),
            )
        data_vars: dict[str, object] = {}
        for field in self.schema.fields:
            variable = self.dataset[field.variable_id]
            array = source[field.variable_id].copy(deep=True)
            if field.unit != variable.unit:
                if variable.unit is None or field.unit is None:
                    raise AssertionError("projection unit conversion lost its source")
                scale = Quantity(1.0, variable.unit).to(field.unit).value
                array = array * scale
            attrs = dict(array.attrs)
            if field.unit is None:
                attrs.pop("units", None)
            else:
                attrs["units"] = field.unit
            attrs["scopecat_source_variable"] = field.variable_id
            target = coords if field.role == "coordinate" else data_vars
            target[field.name] = (array.dims, array.values, attrs)
            if self.schema.diagnostics != "none":
                reasons, metadata = _unavailable_columns(variable.raw_values)
                data_vars[f"{field.name}__unavailable_reason"] = (
                    ("point",),
                    np.asarray(reasons, dtype=np.object_),
                    {"scopecat_role": "availability", "source_variable": field.name},
                )
                if self.schema.diagnostics == "full":
                    data_vars[f"{field.name}__unavailable_metadata"] = (
                        ("point",),
                        np.asarray(metadata, dtype=np.object_),
                        {
                            "scopecat_role": "availability_metadata",
                            "source_variable": field.name,
                        },
                    )
        return xr.Dataset(
            data_vars=data_vars,
            coords=coords,
            attrs={
                "scopecat_dataset_id": self.schema.dataset_id,
                "scopecat_projection_json": _stable_json(asdict(self.schema)),
            },
        )

    def to_pandas(
        self,
        *,
        dtype_backend: PandasDTypeBackend = "numpy",
    ) -> pd.DataFrame:
        """Convert through Arrow, warning when magnitude units remain implicit."""

        if dtype_backend not in {"numpy", "pyarrow"}:
            raise ValueError("pandas dtype_backend must be numpy or pyarrow")
        if self.implicit_units:
            rendered = ", ".join(
                f"{name} [{unit}]" for name, unit in self.implicit_units.items()
            )
            warn(
                "to_pandas() is returning plain magnitudes for fields whose units "
                "were inherited rather than explicitly selected at the projection "
                f"boundary: {rendered}. Select units with project(..., units={{...}}) "
                "or with_units(...).",
                ImplicitPandasUnitWarning,
                stacklevel=2,
            )
        module = cast("_PandasModule", _optional_module("pandas", extra="pandas"))
        table = self.to_arrow()
        frame = cast(
            "pd.DataFrame",
            table.to_pandas(
                types_mapper=(None if dtype_backend == "numpy" else module.ArrowDtype)
            ),
        )
        if dtype_backend == "numpy":
            _restore_numpy_columns(
                frame,
                table=table,
                fields=self.schema.fields,
                layout=self.schema.layout,
            )
        frame.attrs["scopecat"] = thaw_json_value(asdict(self.schema))
        return frame

    def to_polars(self) -> pl.DataFrame:
        """Convert through Arrow without making Polars a core dependency."""

        module = cast("_PolarsModule", _optional_module("polars", extra="polars"))
        return cast("pl.DataFrame", module.from_arrow(self.to_arrow()))


def bind_projection(
    dataset: _ProjectionDataset,
    fields: Sequence[tuple[str, _ProjectionVariable, tuple[str, ...] | None]],
    *,
    spec: ProjectionSpec | None = None,
) -> MeasurementDataProjection:
    """Bind explicit external names to already validated dataset variables."""

    selected = tuple(
        ProjectionField(
            name=name,
            variable_id=variable.id,
            dtype=variable.dtype,
            unit=variable.unit,
            dims=variable.dims,
            role=variable.role,
            source_path=source_path,
            recording_group_id=variable.recording_group_id,
            label=variable.label,
        )
        for name, variable, source_path in fields
    )
    if not selected:
        raise ValueError("measurement projections require at least one field")
    selected_spec = ProjectionSpec() if spec is None else spec
    projection = MeasurementDataProjection(
        dataset=dataset,
        schema=ProjectionSchema(
            dataset_id=dataset.schema.dataset_id,
            fields=selected,
            diagnostics=selected_spec.diagnostics,
            include_identity=selected_spec.include_identity,
            layout=selected_spec.layout,
        ),
    )
    projection = projection.with_units(**dict(selected_spec.units))
    _validate_external_names(
        projection.schema.fields,
        diagnostics=projection.schema.diagnostics,
        include_identity=projection.schema.include_identity,
        layout=projection.schema.layout,
    )
    if projection.schema.layout == "observations":
        _observation_dimensions(projection.schema.fields)
    return projection


def _projected_values(
    variable: _ProjectionVariable,
    field: ProjectionField,
) -> Sequence[MeasurementValue]:
    if field.unit == variable.unit and field.dtype == variable.dtype:
        return variable.raw_values
    if variable.unit is None or field.unit is None:
        raise AssertionError(
            "projection unit conversion requires source and target units"
        )
    scale = Quantity(1.0, variable.unit).to(field.unit).value
    selected: list[MeasurementValue] = []
    for value in variable.raw_values:
        if isinstance(value, MeasurementUnavailable):
            selected.append(
                value.model_copy(update={"dtype": field.dtype, "unit": field.unit})
            )
        elif isinstance(value, MeasurementScalar):
            selected.append(
                MeasurementScalar.create(
                    value=cast("int | float | complex", value.value) * scale,
                    dtype=field.dtype,
                    unit=field.unit,
                    metadata=value.metadata,
                )
            )
        else:
            array = value
            if isinstance(array, MeasurementSegmentedArray):
                selected.append(
                    MeasurementSegmentedArray.create(
                        segments=tuple(
                            segment.model_copy(
                                update={"dtype": field.dtype, "unit": field.unit}
                            )
                            if isinstance(segment, MeasurementUnavailable)
                            else MeasurementArray.create(
                                values=np.asarray(
                                    segment.values,
                                    dtype=(
                                        np.complex128
                                        if field.dtype == "complex128"
                                        else np.float64
                                    ),
                                )
                                * scale,
                                dtype=field.dtype,
                                unit=field.unit,
                                availability=segment.availability,
                                metadata=segment.metadata,
                            )
                            for segment in array.segments
                        ),
                        dtype=field.dtype,
                        unit=field.unit,
                        metadata=array.metadata,
                    )
                )
                continue
            selected.append(
                MeasurementArray.create(
                    values=np.asarray(
                        array.values,
                        dtype=(
                            np.complex128 if field.dtype == "complex128" else np.float64
                        ),
                    )
                    * scale,
                    dtype=field.dtype,
                    unit=field.unit,
                    availability=array.availability,
                    metadata=array.metadata,
                )
            )
    return selected


def _unavailable_columns(
    values: Sequence[MeasurementValue],
) -> tuple[tuple[str | None, ...], tuple[str | None, ...]]:
    reasons: list[str | None] = []
    metadata: list[str | None] = []
    for value in values:
        if isinstance(value, MeasurementUnavailable):
            reasons.append(value.reason)
            metadata.append(_stable_json(value.metadata))
        elif isinstance(
            value,
            MeasurementArray | MeasurementPartitionedArray | MeasurementSegmentedArray,
        ) and (
            value.availability is not None
            or (
                isinstance(value, MeasurementSegmentedArray)
                and value.has_unavailable_segments
            )
        ):
            reasons.append("partial")
            availability = value.availability
            metadata.append(
                _stable_json(
                    {
                        "unavailable": [
                            group.model_dump(mode="json")
                            for group in (
                                () if availability is None else availability.unavailable
                            )
                        ],
                        "segments": [
                            {
                                "index": index,
                                "reason": segment.reason,
                                "metadata": segment.metadata,
                            }
                            for index, segment in enumerate(value.segments)
                            if isinstance(segment, MeasurementUnavailable)
                        ]
                        if isinstance(value, MeasurementSegmentedArray)
                        else [],
                    }
                )
            )
        else:
            reasons.append(None)
            metadata.append(None)
    return tuple(reasons), tuple(metadata)


def _observation_dimensions(fields: Sequence[ProjectionField]) -> tuple[str, ...]:
    arrays = tuple(field for field in fields if len(field.dims) > 1)
    if not arrays:
        raise ValueError("observations layout requires at least one array field")
    dimensions = arrays[0].dims[1:]
    if any(field.dims[1:] != dimensions for field in arrays[1:]):
        raise ValueError(
            "observations layout requires array fields with identical local dimensions"
        )
    groups = {field.recording_group_id for field in arrays}
    if len(groups) > 1:
        raise ValueError(
            "observations layout requires array fields from one recording group"
        )
    return dimensions


def _point_observation_shape(
    fields: Sequence[ProjectionField],
    *,
    projected: Mapping[str, Sequence[MeasurementValue]],
    declared_shapes: Mapping[str, tuple[int | None, ...]],
    position: int,
) -> tuple[int, ...]:
    candidates: list[tuple[int | None, ...]] = []
    for field in fields:
        if len(field.dims) == 1:
            continue
        value = projected[field.name][position]
        if isinstance(value, MeasurementArray | MeasurementPartitionedArray):
            shape = tuple(np.asarray(value.values).shape)
        elif isinstance(value, MeasurementUnavailable):
            shape = tuple(value.shape)
        else:
            raise TypeError(f"observation field {field.name!r} is not array-valued")
        if len(shape) != len(field.dims) - 1:
            raise ValueError(
                f"field {field.name!r} has an invalid point-local observation shape"
            )
        candidates.extend((shape, declared_shapes[field.name]))
    known = {
        cast("tuple[int, ...]", shape)
        for shape in candidates
        if all(size is not None for size in shape)
    }
    if len(known) > 1:
        raise ValueError("observation fields have different point-local shapes")
    if not known:
        raise ValueError("observation shape is unknown for this measurement point")
    [selected] = known
    for candidate in candidates:
        if any(
            size is not None and size != selected[index]
            for index, size in enumerate(candidate)
        ):
            raise ValueError("observation fields have incompatible local extents")
    return selected


def _observation_value(
    value: MeasurementValue,
    *,
    field: ProjectionField,
    local_index: tuple[int, ...],
) -> MeasurementValue:
    if len(field.dims) == 1:
        return value
    if isinstance(value, MeasurementUnavailable):
        return value.model_copy(update={"shape": ()})
    if isinstance(value, MeasurementPartitionedArray):
        value = value.materialize()
    if not isinstance(value, MeasurementArray):
        raise TypeError(f"observation field {field.name!r} is not array-valued")
    if value.availability is not None:
        flat_index = int(np.ravel_multi_index(local_index, value.values.shape))
        for group in value.availability.unavailable:
            if flat_index in group.flat_indices:
                return MeasurementUnavailable.create(
                    reason=group.reason,
                    dtype=field.dtype,
                    unit=field.unit,
                    shape=(),
                    metadata=group.metadata,
                )
    return MeasurementScalar.create(
        value=cast("object", value.values[local_index]),
        dtype=field.dtype,
        unit=field.unit,
        metadata=value.metadata,
    )


def _restore_numpy_columns(
    frame: pd.DataFrame,
    *,
    table: pa.Table,
    fields: Sequence[ProjectionField],
    layout: ProjectionLayout,
) -> None:
    for field in fields:
        values = table[field.name].to_pylist()
        if len(field.dims) > 1 and layout == "points":
            restored = np.empty(len(values), dtype=np.object_)
            restored[:] = tuple(
                None
                if value is None
                else np.asarray(
                    _complex_nested(value) if field.dtype == "complex128" else value,
                    dtype=_numpy_dtype(field.dtype),
                )
                for value in values
            )
            frame[field.name] = restored
        elif field.dtype == "complex128":
            restored = np.empty(len(values), dtype=np.object_)
            restored[:] = tuple(
                None if value is None else _complex_scalar(value) for value in values
            )
            frame[field.name] = restored
        elif field.dtype in {"bool", "int64", "string"}:
            frame[field.name] = frame[field.name].astype(
                {"bool": "boolean", "int64": "Int64", "string": "string"}[field.dtype]
            )


def _numpy_dtype(dtype: MeasurementDType) -> np.dtype[np.generic]:
    return np.dtype(
        {
            "bool": np.bool_,
            "complex128": np.complex128,
            "float64": np.float64,
            "int64": np.int64,
            "string": np.str_,
        }[dtype]
    )


def _complex_nested(value: object) -> object:
    if isinstance(value, Mapping):
        return _complex_scalar(value)
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        return tuple(_complex_nested(item) for item in value)
    raise TypeError("Arrow complex arrays must contain struct or sequence values")


def _complex_scalar(value: object) -> complex:
    if not isinstance(value, Mapping):
        raise TypeError("Arrow complex scalars must use real/imag struct values")
    return complex(cast("float", value["real"]), cast("float", value["imag"]))


def _field_metadata(field: ProjectionField) -> dict[bytes, bytes]:
    metadata = {
        b"scopecat.variable_id": field.variable_id.encode(),
        b"scopecat.dtype": field.dtype.encode(),
        b"scopecat.role": field.role.encode(),
        b"scopecat.dims": _stable_json(field.dims).encode(),
    }
    if field.unit is not None:
        metadata[b"units"] = field.unit.encode()
    if field.source_path is not None:
        metadata[b"scopecat.result_path"] = "/".join(field.source_path).encode()
    if field.recording_group_id is not None:
        metadata[b"scopecat.recording_group_id"] = field.recording_group_id.encode()
    if field.label is not None:
        metadata[b"long_name"] = field.label.encode()
    return metadata


def _validate_external_names(
    fields: Sequence[ProjectionField],
    *,
    diagnostics: ProjectionDiagnostics,
    include_identity: bool,
    layout: ProjectionLayout,
) -> None:
    names = [field.name for field in fields]
    if any(not name for name in names):
        raise ValueError("projection field names must be non-empty")
    generated = list(names)
    if include_identity:
        generated.extend(("point_index", "logical_point_id"))
    if layout == "observations":
        generated.extend(
            f"{dimension}_index" for dimension in _observation_dimensions(fields)
        )
    if diagnostics != "none":
        generated.extend(f"{name}__unavailable_reason" for name in names)
    if diagnostics == "full":
        generated.extend(f"{name}__unavailable_metadata" for name in names)
    if len(generated) != len(set(generated)):
        raise ValueError("projection field and generated column names must be unique")


def _stable_json(value: object) -> str:
    return json.dumps(
        thaw_json_value(value),
        separators=(",", ":"),
        sort_keys=True,
    )


def _optional_module(name: str, *, extra: str) -> object:
    try:
        return import_module(name)
    except ModuleNotFoundError as error:
        if error.name != name:
            raise
        raise ModuleNotFoundError(
            f"{name} is required for this conversion; install scopecat[{extra}]"
        ) from error


__all__ = [
    "ImplicitPandasUnitWarning",
    "MeasurementDataProjection",
    "PandasDTypeBackend",
    "ProjectionDiagnostics",
    "ProjectionField",
    "ProjectionLayout",
    "ProjectionSchema",
    "ProjectionSpec",
]
