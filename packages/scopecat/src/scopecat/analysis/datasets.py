# pyright: reportUnknownArgumentType=false, reportUnknownMemberType=false
# pyright: reportUnknownParameterType=false, reportUnknownVariableType=false
"""Arrow-backed derived datasets crossing the analysis persistence boundary."""

from __future__ import annotations

from base64 import b64decode, b64encode
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from importlib import import_module
from typing import TYPE_CHECKING, Literal, Protocol, cast

import pyarrow as pa
import pyarrow.compute as pc
from pydantic import JsonValue

from scopecat.analysis.dataset_wire import (
    DerivedDatasetField,
    DerivedDatasetPayload,
    DerivedDatasetRole,
    DerivedDatasetSchema,
)
from scopecat.kernel.quantity import Quantity
from scopecat.kernel.units import compatible_units
from scopecat.records.analysis import (
    MAX_ANALYSIS_FIGURE_POINTS,
    MAX_ANALYSIS_TABLE_ROWS,
    AnalysisField,
    AnalysisFigure,
    AnalysisTable,
    AnalysisTableColumn,
    project_analysis_rows,
)
from scopecat.records.metadata import JsonMetadata, validate_json_metadata

if TYPE_CHECKING:
    import pandas as pd
    import polars as pl
    import xarray as xr

DERIVED_DATASET_CODEC = "scopecat.derived-dataset.arrow-ipc.v2"
DERIVED_DATASET_MEDIA_TYPE = "application/vnd.apache.arrow.stream"

type PandasIndexPolicy = Literal["auto", "columns", "drop"]
type PandasDTypeBackend = Literal["numpy", "pyarrow"]


class _FrameModule(Protocol):
    DataFrame: type[object]


class _PandasModule(_FrameModule, Protocol):
    ArrowDtype: Callable[[pa.DataType], object]


class _PolarsModule(_FrameModule, Protocol):
    def from_arrow(self, data: pa.Table) -> object: ...


class _XarrayModule(Protocol):
    Dataset: type[object]


class _PandasRangeIndex(Protocol):
    start: int
    step: int


@dataclass(frozen=True, slots=True)
class DerivedDataset:
    """A native-library result normalized for durable analysis reuse."""

    table: pa.Table
    schema: DerivedDatasetSchema

    @classmethod
    def from_arrow(
        cls,
        table: pa.Table,
        *,
        fields: Mapping[str, AnalysisField] | None = None,
    ) -> DerivedDataset:
        """Normalize an Arrow table and bind its external field semantics."""

        selected = pa.Table.from_arrays(table.columns, schema=table.schema)
        return _bind_semantics(
            selected.combine_chunks(),
            fields=fields,
        )

    @classmethod
    def from_pandas(
        cls,
        frame: pd.DataFrame,
        *,
        fields: Mapping[str, AnalysisField] | None = None,
        index: PandasIndexPolicy = "auto",
    ) -> DerivedDataset:
        """Normalize a frame, retaining meaningful index levels as coordinates."""

        selected, index_coordinates = _pandas_columns(frame, policy=index)
        inherited = _pandas_semantics(frame)
        return _bind_semantics(
            pa.Table.from_pandas(selected, preserve_index=False),
            fields=fields,
            inherited_coordinates=(*index_coordinates, *inherited.coordinates),
            inherited_units=inherited.units,
            inherited_labels=inherited.labels,
        )

    @classmethod
    def from_polars(
        cls,
        frame: pl.DataFrame,
        *,
        fields: Mapping[str, AnalysisField] | None = None,
    ) -> DerivedDataset:
        """Normalize one Polars frame through its native Arrow representation."""

        return cls.from_arrow(
            cast("pa.Table", frame.to_arrow()),
            fields=fields,
        )

    @classmethod
    def from_xarray(
        cls,
        dataset: xr.Dataset,
        *,
        fields: Mapping[str, AnalysisField] | None = None,
    ) -> DerivedDataset:
        """Normalize an exactly reversible one-dimensional Xarray dataset."""

        dimension = _xarray_dimension(dataset)
        inherited_units: dict[str, str] = {}
        inherited_labels: dict[str, str] = {}
        field_attributes: dict[str, JsonMetadata] = {}
        for raw_name in dataset.variables:
            name = str(cast("object", raw_name))
            variable = dataset[raw_name]
            unit = cast("object | None", variable.attrs.get("units"))
            label = cast("object | None", variable.attrs.get("long_name"))
            if unit is not None:
                inherited_units[name] = str(unit)
            if label is not None:
                inherited_labels[name] = str(label)
            field_attributes[name] = _xarray_attributes(
                variable.attrs,
                owner=f"variable {name!r}",
            )
        names = tuple(
            dict.fromkeys(
                (
                    *(str(name) for name in dataset.coords),
                    *(str(name) for name in dataset.data_vars),
                )
            )
        )
        table = pa.table({name: pa.array(dataset[name].values) for name in names})
        return _bind_semantics(
            table,
            fields=fields,
            inherited_coordinates=tuple(str(name) for name in dataset.coords),
            inherited_units=inherited_units,
            inherited_labels=inherited_labels,
            layout="xarray_1d",
            dimension=dimension,
            attributes=_xarray_attributes(dataset.attrs, owner="dataset"),
            field_attributes=field_attributes,
        )

    @classmethod
    def from_objects(cls, rows: Sequence[object]) -> DerivedDataset:
        """Normalize homogeneous annotated dataclass rows without a dataframe."""

        projection = project_analysis_rows(rows)
        arrow_types = {
            "bool": pa.bool_(),
            "int64": pa.int64(),
            "float64": pa.float64(),
            "string": pa.string(),
        }
        dtypes = dict(projection.dtypes)
        table = pa.table(
            {
                name: pa.array(
                    [row[name] for row in projection.rows],
                    type=arrow_types[dtypes[name]],
                )
                for name, _ in projection.fields
            }
        )
        return _bind_semantics(
            table,
            fields=dict(projection.fields),
        )

    @classmethod
    def from_payload(cls, payload: DerivedDatasetPayload) -> DerivedDataset:
        """Restore an uploaded derived dataset exactly."""

        try:
            encoded = b64decode(payload.arrow_ipc_base64, validate=True)
        except ValueError as error:
            raise ValueError("derived dataset Arrow IPC is invalid") from error
        return cls.from_arrow_ipc(encoded, schema=payload.dataset_schema)

    @classmethod
    def from_arrow_ipc(
        cls,
        content: bytes,
        *,
        schema: DerivedDatasetSchema,
    ) -> DerivedDataset:
        """Restore persisted Arrow bytes against their semantic schema."""

        try:
            with pa.ipc.open_stream(content) as reader:
                table = reader.read_all()
        except (ValueError, pa.ArrowException) as error:
            raise ValueError("derived dataset Arrow IPC is invalid") from error
        expected = tuple(
            (field.name, field.arrow_type, field.nullable) for field in schema.fields
        )
        actual = tuple(
            (field.name, str(field.type), field.nullable) for field in table.schema
        )
        if actual != expected:
            raise ValueError("derived dataset semantic and Arrow schemas disagree")
        return cls(table=table, schema=schema)

    @classmethod
    def preview_from_arrow_ipc(
        cls,
        content: bytes,
        *,
        schema: DerivedDatasetSchema,
        columns: Sequence[str],
        limit: int,
    ) -> tuple[DerivedDataset, int]:
        """Retain only selected columns/rows from IPC batches, never read_all.

        The existing blob port reads whole IPC bytes, and decoding one record
        batch may touch its full buffers. Only bounded selected data is assembled.
        """
        with pa.ipc.open_stream(content) as reader:
            selected_schema = pa.schema([reader.schema.field(name) for name in columns])
            batches: list[pa.RecordBatch] = []
            remaining = limit
            total_rows = 0
            for batch in reader:
                total_rows += batch.num_rows
                if remaining:
                    selected = batch.select(columns).slice(0, remaining)
                    batches.append(selected)
                    remaining -= selected.num_rows
            table = pa.Table.from_batches(batches, schema=selected_schema)
        fields = {field.name: field for field in schema.fields}
        return cls(
            table=table,
            schema=schema.model_copy(
                update={"fields": tuple(fields[name] for name in columns)}
            ),
        ), total_rows

    def to_arrow_ipc(self) -> bytes:
        """Encode exact Arrow values for content-addressed run storage."""

        sink = pa.BufferOutputStream()
        with pa.ipc.new_stream(sink, self.table.schema) as writer:
            writer.write_table(self.table)
        return sink.getvalue().to_pybytes()

    def to_payload(self) -> DerivedDatasetPayload:
        """Encode the temporary daemon command payload used during publication."""

        return DerivedDatasetPayload(
            dataset_schema=self.schema,
            arrow_ipc_base64=b64encode(self.to_arrow_ipc()).decode("ascii"),
        )

    def __len__(self) -> int:
        return len(self.table)

    def to_analysis_table(
        self,
        *,
        columns: Sequence[str] | None = None,
    ) -> AnalysisTable:
        """Create a bounded scalar presentation from explicitly selected columns."""

        fields = {field.name: field for field in self.schema.fields}
        selected_names = (
            tuple(self.table.column_names) if columns is None else tuple(columns)
        )
        if not selected_names:
            raise ValueError("analysis table requires at least one dataset column")
        if len(selected_names) != len(set(selected_names)):
            raise ValueError("analysis table dataset columns must be unique")
        unknown = set(selected_names) - set(self.table.column_names)
        if unknown:
            raise KeyError(
                "derived dataset has no columns: " + ", ".join(sorted(unknown))
            )
        selected = self.table.select(selected_names).slice(
            0,
            MAX_ANALYSIS_TABLE_ROWS,
        )
        return AnalysisTable.from_rows(
            cast("list[Mapping[str, object]]", selected.to_pylist()),
            columns=tuple(
                AnalysisTableColumn(
                    id=name,
                    label=fields[name].label,
                    unit=fields[name].unit,
                )
                for name in selected_names
            ),
        )

    def to_analysis_figure(
        self,
        *,
        kind: Literal["line", "scatter"],
        x: str,
        y: str,
        series: str | None = None,
        label: str | None = None,
    ) -> AnalysisFigure:
        """Create a bounded numeric figure preview from dataset columns."""

        selected_names = tuple(
            dict.fromkeys((x, y) if series is None else (x, y, series))
        )
        unknown = set(selected_names) - set(self.table.column_names)
        if unknown:
            raise KeyError(
                "derived dataset has no columns: " + ", ".join(sorted(unknown))
            )
        fields = {field.name: field for field in self.schema.fields}
        selected = self.table.select(selected_names).slice(
            0,
            MAX_ANALYSIS_FIGURE_POINTS,
        )
        return AnalysisFigure.from_rows(
            cast("list[Mapping[str, object]]", selected.to_pylist()),
            columns=tuple(
                AnalysisTableColumn(
                    id=name,
                    label=fields[name].label,
                    unit=fields[name].unit,
                )
                for name in selected_names
            ),
            kind=kind,
            x=x,
            y=y,
            series=series,
            label=label,
        )

    def to_pandas(
        self,
        *,
        dtype_backend: PandasDTypeBackend = "numpy",
    ) -> pd.DataFrame:
        """Return pandas-native values or preserve Arrow extension dtypes."""

        if dtype_backend not in {"numpy", "pyarrow"}:
            raise ValueError("pandas dtype_backend must be numpy or pyarrow")
        module = cast("_PandasModule", _optional_module("pandas", extra="pandas"))
        return cast(
            "pd.DataFrame",
            self.table.to_pandas(
                types_mapper=(None if dtype_backend == "numpy" else module.ArrowDtype)
            ),
        )

    def to_polars(self) -> pl.DataFrame:
        """Return a normal Polars frame for further analysis."""

        module = cast(
            "_PolarsModule",
            _optional_module("polars", extra="polars"),
        )
        return cast("pl.DataFrame", module.from_arrow(self.table))

    def to_xarray(self) -> xr.Dataset:
        """Restore a native Xarray dataset when its topology was preserved."""

        xarray = cast("_XarrayModule", _optional_module("xarray", extra="xarray"))

        if self.schema.layout != "xarray_1d" or self.schema.dimension is None:
            raise ValueError("only Xarray-authored derived datasets can restore Xarray")
        dimension = self.schema.dimension
        coordinates: dict[str, object] = {}
        data_variables: dict[str, object] = {}
        for field in self.schema.fields:
            variable = (
                dimension,
                self.table[field.name].combine_chunks().to_numpy(zero_copy_only=False),
                dict(field.attributes),
            )
            if field.role == "coordinate":
                coordinates[field.name] = variable
            else:
                data_variables[field.name] = variable
        constructor = cast("Callable[..., object]", xarray.Dataset)
        return cast(
            "xr.Dataset",
            constructor(
                data_vars=data_variables,
                coords=coordinates,
                attrs=dict(self.schema.attributes),
            ),
        )


def derived_dataset(
    data: object,
    *,
    fields: Mapping[str, AnalysisField] | None = None,
    index: PandasIndexPolicy = "auto",
) -> DerivedDataset:
    """Normalize native tabular data or annotated dataclass rows."""

    if isinstance(data, DerivedDataset):
        if fields or index != "auto":
            raise ValueError("an existing derived dataset already owns its schema")
        return data
    if isinstance(data, pa.Table):
        if index != "auto":
            raise ValueError("index policy only applies to pandas data")
        return DerivedDataset.from_arrow(
            data,
            fields=fields,
        )
    owner = type(data).__module__.partition(".")[0]
    if owner == "xarray":
        xarray = cast("_XarrayModule", _optional_module("xarray", extra="xarray"))
        if not isinstance(data, xarray.Dataset):
            raise TypeError("unsupported Xarray derived dataset object")
        if index != "auto":
            raise ValueError("index policy only applies to pandas data")
        return DerivedDataset.from_xarray(
            cast("xr.Dataset", data),
            fields=fields,
        )
    if owner == "pandas":
        pandas = cast("_FrameModule", _optional_module("pandas", extra="pandas"))
        if not isinstance(data, pandas.DataFrame):
            raise TypeError("unsupported pandas derived dataset object")
        return DerivedDataset.from_pandas(
            cast("pd.DataFrame", data),
            fields=fields,
            index=index,
        )
    if owner == "polars":
        if index != "auto":
            raise ValueError("index policy only applies to pandas data")
        polars = cast("_PolarsModule", _optional_module("polars", extra="polars"))
        if not isinstance(data, polars.DataFrame):
            raise TypeError("unsupported Polars derived dataset object")
        return DerivedDataset.from_polars(
            cast("pl.DataFrame", data),
            fields=fields,
        )
    if isinstance(data, Sequence) and not isinstance(
        data,
        str | bytes | bytearray,
    ):
        if fields:
            raise ValueError(
                "annotated analysis rows already declare their field semantics"
            )
        if index != "auto":
            raise ValueError("index policy only applies to pandas data")
        return DerivedDataset.from_objects(data)
    raise TypeError(
        "derived_dataset requires Arrow, pandas, Polars, Xarray, or annotated "
        "dataclass rows"
    )


def _bind_semantics(
    table: pa.Table,
    *,
    fields: Mapping[str, AnalysisField] | None,
    inherited_coordinates: Sequence[str] = (),
    inherited_units: Mapping[str, str] | None = None,
    inherited_labels: Mapping[str, str] | None = None,
    layout: Literal["table", "xarray_1d"] = "table",
    dimension: str | None = None,
    attributes: Mapping[str, object] | None = None,
    field_attributes: Mapping[str, JsonMetadata] | None = None,
) -> DerivedDataset:
    source_names = tuple(table.column_names)
    if not source_names or len(source_names) != len(set(source_names)):
        raise ValueError("derived dataset columns must be non-empty and unique")
    configured_fields = dict(fields or {})
    unknown_fields = set(configured_fields) - set(source_names)
    if unknown_fields:
        raise KeyError(
            "derived dataset has no source columns: "
            + ", ".join(sorted(unknown_fields))
        )
    policies = {
        source_name: configured_fields.get(source_name, AnalysisField())
        for source_name in source_names
    }
    names = tuple(
        policies[source_name].id or source_name for source_name in source_names
    )
    if len(names) != len(set(names)):
        raise ValueError("derived dataset field ids must be unique")
    arrow_semantics = _arrow_semantics(table.schema)
    coordinate_names = set(inherited_coordinates) | set(arrow_semantics.coordinates)
    selected_units = {
        **arrow_semantics.units,
        **(inherited_units or {}),
    }
    selected_labels = {
        **arrow_semantics.labels,
        **(inherited_labels or {}),
    }
    configured = set(coordinate_names) | set(selected_units) | set(selected_labels)
    unknown = configured - set(source_names)
    if unknown:
        raise KeyError("derived dataset has no columns: " + ", ".join(sorted(unknown)))
    table = _convert_field_units(
        table,
        policies=policies,
        source_units=selected_units,
    )
    semantic_fields: list[DerivedDatasetField] = []
    for field, source_name, name in zip(
        table.schema,
        source_names,
        names,
        strict=True,
    ):
        policy = policies[source_name]
        unit = policy.unit or selected_units.get(source_name)
        label = policy.label or selected_labels.get(source_name)
        semantic_fields.append(
            DerivedDatasetField(
                name=name,
                source_name=source_name,
                arrow_type=str(field.type),
                nullable=field.nullable,
                role=(
                    policy.role
                    or (
                        "coordinate"
                        if source_name in coordinate_names
                        else "observable"
                    )
                ),
                unit=unit,
                label=label,
                attributes={
                    **(field_attributes or {}).get(source_name, {}),
                    **({} if unit is None else {"units": cast("JsonValue", unit)}),
                    **(
                        {} if label is None else {"long_name": cast("JsonValue", label)}
                    ),
                },
            )
        )
    arrow_fields = tuple(
        field.with_name(semantic.name).with_metadata(_field_metadata(field, semantic))
        for field, semantic in zip(table.schema, semantic_fields, strict=True)
    )
    schema = pa.schema(
        arrow_fields,
        metadata={b"scopecat.schema": b"scopecat.derived-dataset.v3"},
    )
    return DerivedDataset(
        table=pa.Table.from_arrays(table.columns, schema=schema),
        schema=DerivedDatasetSchema(
            fields=tuple(semantic_fields),
            layout=layout,
            dimension=(
                None
                if dimension is None
                else policies.get(dimension, AnalysisField()).id or dimension
            ),
            attributes=validate_json_metadata(attributes or {}),
        ),
    )


def _convert_field_units(
    table: pa.Table,
    *,
    policies: Mapping[str, AnalysisField],
    source_units: Mapping[str, str],
) -> pa.Table:
    columns: list[pa.ChunkedArray] = []
    fields: list[pa.Field] = []
    for field, column in zip(table.schema, table.columns, strict=True):
        source_unit = source_units.get(field.name)
        target_unit = policies[field.name].unit or source_unit
        if target_unit is not None and not _is_numeric_arrow_type(field.type):
            raise TypeError(
                f"derived dataset field {field.name!r} cannot assign unit "
                f"{target_unit!r} to non-numeric Arrow type {field.type}"
            )
        if source_unit is None or target_unit is None or source_unit == target_unit:
            columns.append(column)
            fields.append(field)
            continue
        if not compatible_units(source_unit, target_unit):
            raise ValueError(
                f"derived dataset field {field.name!r} cannot convert "
                f"{source_unit!r} to {target_unit!r}"
            )
        factor = Quantity(value=1.0, unit=source_unit).to(target_unit).value
        converted = cast(
            "pa.ChunkedArray",
            pc.call_function("multiply", [column, pa.scalar(factor)]),
        )
        columns.append(converted)
        fields.append(
            pa.field(
                field.name,
                converted.type,
                nullable=field.nullable,
                metadata=field.metadata,
            )
        )
    return pa.Table.from_arrays(
        columns,
        schema=pa.schema(fields, metadata=table.schema.metadata),
    )


def _is_numeric_arrow_type(value: pa.DataType) -> bool:
    return (
        pa.types.is_integer(value)
        or pa.types.is_floating(value)
        or pa.types.is_decimal(value)
    )


def _field_metadata(
    field: pa.Field,
    semantic: DerivedDatasetField,
) -> dict[bytes, bytes]:
    metadata = dict(field.metadata or {})
    for name in (
        b"scopecat.role",
        b"scopecat.source_name",
        b"units",
        b"long_name",
    ):
        metadata.pop(name, None)
    metadata[b"scopecat.role"] = semantic.role.encode()
    metadata[b"scopecat.source_name"] = semantic.source_name.encode()
    if semantic.unit is not None:
        metadata[b"units"] = semantic.unit.encode()
    if semantic.label is not None:
        metadata[b"long_name"] = semantic.label.encode()
    return metadata


def _xarray_dimension(dataset: xr.Dataset) -> str:
    if any(not isinstance(name, str) for name in dataset.sizes):
        raise TypeError("derived Xarray dimension names must be strings")
    if any(not isinstance(name, str) for name in dataset.variables):
        raise TypeError("derived Xarray variable names must be strings")
    dimensions = tuple(dataset.sizes)
    if len(dimensions) != 1:
        raise ValueError(
            "derived Xarray datasets require exactly one dimension; "
            "publish a deliberate tabular projection or preserve the native "
            "dataset as an analysis artifact"
        )
    dimension = cast("str", dimensions[0])
    index = dataset.indexes.get(dimension)
    if index is not None and cast("int", index.nlevels) != 1:
        raise ValueError(
            "derived Xarray datasets do not flatten multi-index dimensions; "
            "publish a deliberate projection or preserve the native dataset "
            "as an analysis artifact"
        )
    for raw_name in dataset.variables:
        name = cast("str", raw_name)
        if tuple(dataset[raw_name].dims) != (dimension,):
            raise ValueError(
                f"derived Xarray variable {name!r} must use dimension "
                f"{dimension!r} exactly; publish a deliberate projection or "
                "preserve the native dataset as an analysis artifact"
            )
    return dimension


def _xarray_attributes(
    value: Mapping[object, object],
    *,
    owner: str,
) -> JsonMetadata:
    if any(not isinstance(key, str) for key in value):
        raise TypeError(f"derived Xarray {owner} attributes require string keys")
    try:
        return validate_json_metadata(value)
    except ValueError as error:
        raise TypeError(
            f"derived Xarray {owner} attributes must be finite JSON values; "
            "preserve the native dataset as an analysis artifact"
        ) from error


@dataclass(frozen=True, slots=True)
class _InheritedSemantics:
    coordinates: tuple[str, ...] = ()
    units: Mapping[str, str] = dataclass_field(default_factory=dict)
    labels: Mapping[str, str] = dataclass_field(default_factory=dict)


def _arrow_semantics(schema: pa.Schema) -> _InheritedSemantics:
    coordinates: list[str] = []
    units: dict[str, str] = {}
    labels: dict[str, str] = {}
    for field in schema:
        metadata = field.metadata or {}
        if metadata.get(b"scopecat.role") == b"coordinate":
            coordinates.append(field.name)
        if (unit := metadata.get(b"units")) is not None:
            units[field.name] = unit.decode()
        if (label := metadata.get(b"long_name")) is not None:
            labels[field.name] = label.decode()
    return _InheritedSemantics(tuple(coordinates), units, labels)


def _pandas_columns(
    frame: pd.DataFrame,
    *,
    policy: PandasIndexPolicy,
) -> tuple[pd.DataFrame, tuple[str, ...]]:
    if policy not in {"auto", "columns", "drop"}:
        raise ValueError("pandas index policy must be auto, columns, or drop")
    non_text_columns = tuple(
        column for column in frame.columns if not isinstance(column, str)
    )
    if non_text_columns:
        raise TypeError("derived dataset pandas columns must be strings")
    if policy == "drop":
        return frame, ()
    index = frame.index
    range_index = cast("_PandasRangeIndex", cast("object", index))
    is_implicit = (
        policy == "auto"
        and type(index).__name__ == "RangeIndex"
        and index.name is None
        and range_index.start == 0
        and range_index.step == 1
    )
    if is_implicit:
        return frame, ()
    index_names = cast("Sequence[object]", index.names)
    names = tuple(
        str(name)
        if name is not None
        else ("index" if index.nlevels == 1 else f"level_{i}")
        for i, name in enumerate(index_names)
    )
    frame_columns = cast("Sequence[object]", cast("object", frame.columns))
    conflicts = set(names) & {str(column) for column in frame_columns}
    if conflicts:
        raise ValueError(
            "pandas index names conflict with columns: " + ", ".join(sorted(conflicts))
        )
    selected = frame.copy(deep=False)
    selected.index = selected.index.set_names(names)
    return selected.reset_index(), names


def _pandas_semantics(frame: pd.DataFrame) -> _InheritedSemantics:
    value = frame.attrs.get("scopecat")
    if not isinstance(value, Mapping):
        return _InheritedSemantics()
    raw_fields = value.get("fields")
    if not isinstance(raw_fields, Sequence):
        return _InheritedSemantics()
    coordinates: list[str] = []
    units: dict[str, str] = {}
    labels: dict[str, str] = {}
    available = {str(column) for column in frame.columns}
    for raw_field in raw_fields:
        if not isinstance(raw_field, Mapping):
            continue
        name = raw_field.get("name")
        if not isinstance(name, str) or name not in available:
            continue
        if raw_field.get("role") == "coordinate":
            coordinates.append(name)
        if isinstance(unit := raw_field.get("unit"), str):
            units[name] = unit
        if isinstance(label := raw_field.get("label"), str):
            labels[name] = label
    return _InheritedSemantics(tuple(coordinates), units, labels)


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
    "DERIVED_DATASET_CODEC",
    "DERIVED_DATASET_MEDIA_TYPE",
    "DerivedDataset",
    "DerivedDatasetField",
    "DerivedDatasetPayload",
    "DerivedDatasetRole",
    "DerivedDatasetSchema",
    "PandasDTypeBackend",
    "PandasIndexPolicy",
    "derived_dataset",
]
