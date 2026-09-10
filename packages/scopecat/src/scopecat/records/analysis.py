"""Persisted analysis record models."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, fields, is_dataclass
from typing import (
    Annotated,
    Literal,
    Self,
    cast,
    get_args,
    get_origin,
    get_type_hints,
)

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    FiniteFloat,
    JsonValue,
    field_validator,
    model_validator,
)

from scopecat.kernel.content_identity import canonical_json, stable_content_hash
from scopecat.kernel.quantity import Quantity
from scopecat.records.content import Sha256ContentHash
from scopecat.records.metadata import JsonMetadata
from scopecat.records.sample import SampleId

type _NonEmptyText = Annotated[str, Field(min_length=1)]

MAX_ANALYSIS_TABLE_SAFE_INTEGER = 2**53 - 1
MAX_ANALYSIS_TABLE_COLUMNS = 32
MAX_ANALYSIS_TABLE_ROWS = 500
MAX_ANALYSIS_FIGURE_SERIES = 16
MAX_ANALYSIS_FIGURE_POINTS = 4096
MAX_ANALYSIS_OUTPUTS = 32
MAX_ANALYSIS_DATA_BYTES = 1_000_000
MAX_ANALYSIS_TOTAL_TABLE_CELLS = 64_000
MAX_ANALYSIS_TOTAL_FIGURE_POINTS = 16_384
ANALYSIS_ARTIFACT_CODEC = "scopecat.artifact-bytes.v1"


def analysis_record_id(analysis_key: str, revision: int) -> str:
    """Return the unambiguous persisted record identity for one revision."""

    return f"analysis-{analysis_key}-r{revision}"


def _reject_unsafe_table_integer(value: object) -> object:
    if (
        isinstance(value, int)
        and not isinstance(value, bool)
        and abs(value) > MAX_ANALYSIS_TABLE_SAFE_INTEGER
    ):
        raise ValueError(
            "analysis table integers must be within the JavaScript safe range"
        )
    return value


type _AnalysisTableInteger = Annotated[
    int,
    Field(
        ge=-MAX_ANALYSIS_TABLE_SAFE_INTEGER,
        le=MAX_ANALYSIS_TABLE_SAFE_INTEGER,
    ),
]
type AnalysisTableCell = Annotated[
    bool | _AnalysisTableInteger | FiniteFloat | str | None,
    BeforeValidator(_reject_unsafe_table_integer),
]
type AnalysisRowDType = Literal["bool", "int64", "float64", "string"]


def _normalized_external_scalar(value: object) -> bool | int | float | str | None:
    if value is None or isinstance(value, bool | int | float | str):
        return value
    item = getattr(value, "item", None)
    if item is None:
        raise TypeError("analysis values must be scalar JSON values")
    try:
        normalized = cast("Callable[[], object]", item)()
    except (TypeError, ValueError) as error:
        raise TypeError("analysis values must be scalar JSON values") from error
    if normalized is None or isinstance(normalized, bool | int | float | str):
        return normalized
    raise TypeError("analysis values must be scalar JSON values")


def _normalized_figure_value(value: object) -> float:
    normalized = _normalized_external_scalar(value)
    if isinstance(normalized, bool) or not isinstance(normalized, int | float):
        raise TypeError("analysis figure values must be real numbers")
    return float(normalized)


class _AnalysisContentModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        allow_inf_nan=False,
    )


class AnalysisField(_AnalysisContentModel):
    """Stable identity and semantics for one analysis data field."""

    id: _NonEmptyText | None = None
    role: Literal["coordinate", "observable"] | None = None
    label: _NonEmptyText | None = None
    unit: _NonEmptyText | None = None


class AnalysisTableColumn(_AnalysisContentModel):
    """One stable scalar column in an analysis-authored table."""

    id: _NonEmptyText
    label: _NonEmptyText | None = None
    unit: _NonEmptyText | None = None


class AnalysisTableRow(_AnalysisContentModel):
    """Cells aligned positionally with an :class:`AnalysisTable` schema."""

    cells: list[AnalysisTableCell]


@dataclass(frozen=True, slots=True)
class AnalysisRowProjection:
    """One homogeneous annotated dataclass sequence projected by field policy."""

    fields: tuple[tuple[str, AnalysisField], ...]
    dtypes: tuple[tuple[str, AnalysisRowDType], ...]
    rows: tuple[Mapping[str, object], ...]


class AnalysisTable(_AnalysisContentModel):
    """A bounded, display-ready scalar table with an explicit column schema."""

    columns: list[AnalysisTableColumn] = Field(
        min_length=1,
        max_length=MAX_ANALYSIS_TABLE_COLUMNS,
    )
    rows: list[AnalysisTableRow] = Field(
        default_factory=list,
        max_length=MAX_ANALYSIS_TABLE_ROWS,
    )

    @model_validator(mode="after")
    def validate_layout(self) -> AnalysisTable:
        column_ids = tuple(column.id for column in self.columns)
        if len(column_ids) != len(set(column_ids)):
            raise ValueError("analysis table column ids must be unique")
        if any(len(row.cells) != len(self.columns) for row in self.rows):
            raise ValueError("analysis table rows must match the column count")
        return self

    @classmethod
    def from_rows(
        cls,
        rows: Sequence[Mapping[str, object]],
        *,
        columns: Sequence[AnalysisTableColumn] | None = None,
    ) -> Self:
        """Build the canonical table layout, normalizing array-library scalars."""

        selected_rows = tuple(rows)
        if columns is None:
            if not selected_rows:
                raise ValueError(
                    "analysis table columns are required for an empty table"
                )
            selected_columns = tuple(
                AnalysisTableColumn(id=column_id) for column_id in selected_rows[0]
            )
        else:
            selected_columns = tuple(columns)
        expected_ids = tuple(column.id for column in selected_columns)
        expected_id_set = set(expected_ids)
        for row in selected_rows:
            if set(row) != expected_id_set:
                raise ValueError(
                    "analysis table rows must define exactly the declared columns"
                )
        return cls(
            columns=list(selected_columns),
            rows=[
                AnalysisTableRow(
                    cells=[
                        _normalized_external_scalar(row[column_id])
                        for column_id in expected_ids
                    ]
                )
                for row in selected_rows
            ],
        )

    @classmethod
    def from_objects(cls, rows: Sequence[object]) -> Self:
        """Project annotated dataclass fields into one scalar table."""

        projection = project_analysis_rows(rows)
        columns = tuple(
            AnalysisTableColumn(
                id=policy.id or name,
                label=policy.label,
                unit=policy.unit,
            )
            for name, policy in projection.fields
        )
        return cls.from_rows(
            [
                {
                    column.id: row[name]
                    for (name, _), column in zip(
                        projection.fields,
                        columns,
                        strict=True,
                    )
                }
                for row in projection.rows
            ],
            columns=columns,
        )


def is_analysis_rows(value: object) -> bool:
    """Return whether a sequence declares a dataclass row schema."""

    if not isinstance(value, Sequence) or isinstance(value, str | bytes | bytearray):
        return False
    if not value:
        return False
    row_type = type(value[0])
    return is_dataclass(row_type)


def project_analysis_rows(rows: Sequence[object]) -> AnalysisRowProjection:
    """Infer ordinary scalar rows or apply explicit annotated column selection."""

    selected_rows = tuple(rows)
    if not selected_rows:
        raise ValueError("analysis object rows must not be empty")
    row_type = type(selected_rows[0])
    if not is_dataclass(row_type) or any(
        type(row) is not row_type for row in selected_rows
    ):
        raise TypeError("analysis object rows must share one dataclass type")
    hints = _dataclass_type_hints(row_type)
    annotated = any(
        _analysis_field(hints.get(member.name)) is not None
        for member in fields(row_type)
    )
    selected: list[tuple[str, AnalysisField]] = []
    for member in fields(row_type):
        policy = _analysis_field(hints.get(member.name))
        if policy is None and not annotated:
            policy = AnalysisField.model_validate(dict(member.metadata))
        if policy is not None:
            selected.append((member.name, policy))
    selected_fields = tuple(selected)
    if not selected_fields:
        raise TypeError(
            "analysis object rows require at least one scalar dataclass field"
        )
    return AnalysisRowProjection(
        fields=selected_fields,
        dtypes=tuple(
            (name, _analysis_row_dtype(hints[name])) for name, _ in selected_fields
        ),
        rows=tuple(
            {
                name: _analysis_field_value(
                    cast("object", getattr(row, name)),
                    policy,
                )
                for name, policy in selected_fields
            }
            for row in selected_rows
        ),
    )


def _dataclass_type_hints(row_type: type[object]) -> Mapping[str, object]:
    """Resolve postponed fields even after a project module is unloaded."""

    initializer = getattr(row_type, "__init__", None)
    retained_globals = getattr(initializer, "__globals__", None)
    globalns = cast(
        "dict[str, object] | None",
        retained_globals if isinstance(retained_globals, dict) else None,
    )
    return get_type_hints(
        row_type,
        globalns=globalns,
        include_extras=True,
    )


class AnalysisFigureAxis(_AnalysisContentModel):
    """One labeled numeric figure axis."""

    label: _NonEmptyText
    unit: _NonEmptyText | None = None


class AnalysisFigureSeries(_AnalysisContentModel):
    """One embedded numeric series; x and y values are point-aligned."""

    id: _NonEmptyText
    label: _NonEmptyText | None = None
    x: list[FiniteFloat] = Field(
        min_length=1,
        max_length=MAX_ANALYSIS_FIGURE_POINTS,
    )
    y: list[FiniteFloat] = Field(
        min_length=1,
        max_length=MAX_ANALYSIS_FIGURE_POINTS,
    )

    y_lower: list[FiniteFloat] | None = None
    y_upper: list[FiniteFloat] | None = None

    @model_validator(mode="after")
    def validate_points(self) -> AnalysisFigureSeries:
        if len(self.x) != len(self.y):
            raise ValueError(
                "analysis figure series x and y values must have equal length"
            )
        if (self.y_lower is None) != (self.y_upper is None):
            raise ValueError("uncertainty requires both lower and upper bounds")
        if self.y_lower is not None and self.y_upper is not None:
            if len(self.y_lower) != len(self.x) or len(self.y_upper) != len(self.x):
                raise ValueError("uncertainty bounds must be point-aligned")
            if any(lo > hi for lo, hi in zip(self.y_lower, self.y_upper, strict=True)):
                raise ValueError(
                    "uncertainty lower bounds must not exceed upper bounds"
                )
        return self

    @classmethod
    def from_arrays(
        cls,
        *,
        id: str,
        x: Iterable[object],
        y: Iterable[object],
        label: str | None = None,
    ) -> Self:
        """Build a series from Python or array-library numeric iterables."""

        return cls(
            id=id,
            label=label,
            x=[_normalized_figure_value(value) for value in x],
            y=[_normalized_figure_value(value) for value in y],
        )


class AnalysisFigure(_AnalysisContentModel):
    """A finite embedded line or scatter figure ready for local rendering."""

    kind: Literal["line", "scatter"]
    x_axis: AnalysisFigureAxis
    y_axis: AnalysisFigureAxis
    series: list[AnalysisFigureSeries] = Field(
        min_length=1,
        max_length=MAX_ANALYSIS_FIGURE_SERIES,
    )

    @model_validator(mode="after")
    def validate_series(self) -> AnalysisFigure:
        series_ids = tuple(series.id for series in self.series)
        if len(series_ids) != len(set(series_ids)):
            raise ValueError("analysis figure series ids must be unique")
        if sum(len(series.x) for series in self.series) > MAX_ANALYSIS_FIGURE_POINTS:
            raise ValueError(
                "analysis figure total point count must not exceed "
                f"{MAX_ANALYSIS_FIGURE_POINTS}"
            )
        return self

    @classmethod
    def from_table(
        cls,
        table: AnalysisTable,
        *,
        kind: Literal["line", "scatter"],
        x: str,
        y: str,
        series: str | None = None,
        label: str | None = None,
    ) -> Self:
        """Project aligned numeric columns from a bounded table."""

        column_ids = tuple(column.id for column in table.columns)
        return cls.from_rows(
            (dict(zip(column_ids, row.cells, strict=True)) for row in table.rows),
            columns=table.columns,
            kind=kind,
            x=x,
            y=y,
            series=series,
            label=label,
        )

    @classmethod
    def from_rows(
        cls,
        rows: Iterable[Mapping[str, object]],
        *,
        columns: Sequence[AnalysisTableColumn],
        kind: Literal["line", "scatter"],
        x: str,
        y: str,
        series: str | None = None,
        label: str | None = None,
    ) -> Self:
        """Project a bounded scalar-row preview without a table row limit."""

        column_by_id = {column.id: column for column in columns}
        try:
            x_column = column_by_id[x]
            y_column = column_by_id[y]
            if series is not None:
                column_by_id[series]
        except KeyError as error:
            raise KeyError(
                f"analysis figure column is missing: {error.args[0]}"
            ) from None
        grouped: dict[object, tuple[list[float], list[float]]] = {}
        for row in rows:
            try:
                group = label or y if series is None else row[series]
                x_value = row[x]
                y_value = row[y]
            except KeyError as error:
                raise KeyError(
                    f"analysis figure column is missing: {error.args[0]}"
                ) from None
            normalized_group = _normalized_external_scalar(group)
            grouped_x, grouped_y = grouped.setdefault(normalized_group, ([], []))
            grouped_x.append(_figure_cell(x_value, column=x))
            grouped_y.append(_figure_cell(y_value, column=y))
        return cls(
            kind=kind,
            x_axis=AnalysisFigureAxis(
                label=x_column.label or x_column.id, unit=x_column.unit
            ),
            y_axis=AnalysisFigureAxis(
                label=y_column.label or y_column.id, unit=y_column.unit
            ),
            series=[
                AnalysisFigureSeries(
                    id=str(group),
                    label=str(group),
                    x=grouped_x,
                    y=grouped_y,
                )
                for group, (grouped_x, grouped_y) in grouped.items()
            ],
        )


class AnalysisDatasetViewSource(_AnalysisContentModel):
    """Stable analysis-local dataset referenced by a presentation view."""

    kind: Literal["dataset"] = "dataset"
    output_id: _NonEmptyText


class AnalysisTableViewSpec(_AnalysisContentModel):
    """Authoritative dataset projection requested for one table view."""

    source: AnalysisDatasetViewSource
    columns: Sequence[_NonEmptyText] = Field(
        min_length=1,
        max_length=MAX_ANALYSIS_TABLE_COLUMNS,
    )

    @field_validator("columns")
    @classmethod
    def freeze_columns(
        cls,
        value: Sequence[str],
    ) -> Sequence[str]:
        return tuple(value)


class AnalysisTableView(AnalysisTableViewSpec):
    """Server-generated bounded table preview of an authoritative dataset."""

    preview: AnalysisTable
    total_rows: int = Field(ge=0)
    truncated: bool

    @model_validator(mode="after")
    def validate_preview(self) -> AnalysisTableView:
        if tuple(self.columns) != tuple(column.id for column in self.preview.columns):
            raise ValueError(
                "analysis table projected columns must match its preview columns"
            )
        returned_rows = len(self.preview.rows)
        if self.total_rows < returned_rows:
            raise ValueError("analysis table total rows must cover its preview")
        if self.truncated != (self.total_rows > returned_rows):
            raise ValueError("analysis table truncation must match its row counts")
        return self


class AnalysisUncertaintyProjection(_AnalysisContentModel):
    """Absolute y bounds supplied by project analysis, not framework statistics."""

    lower: _NonEmptyText
    upper: _NonEmptyText
    meaning: _NonEmptyText
    style: Literal["band", "bars"]


class AnalysisFigureProjection(_AnalysisContentModel):
    """Dataset column roles used to produce a bounded figure layer."""

    kind: Literal["line", "scatter"]
    x: _NonEmptyText
    y: _NonEmptyText
    series: _NonEmptyText | None = None
    label: _NonEmptyText | None = None
    uncertainty: AnalysisUncertaintyProjection | None = None


class AnalysisPublishedDatasetViewSource(_AnalysisContentModel):
    """A frozen, already published dataset; never a reference to this publication."""

    kind: Literal["published_dataset"] = "published_dataset"
    source: AnalysisPublishedOutputReference
    dataset: AnalysisDatasetReference


type AnalysisFigureSource = Annotated[
    AnalysisDatasetViewSource | AnalysisPublishedDatasetViewSource,
    Field(discriminator="kind"),
]


class AnalysisFigureLayerSpec(_AnalysisContentModel):
    id: _NonEmptyText
    source: AnalysisFigureSource
    projection: AnalysisFigureProjection


class AnalysisFigureViewSpec(_AnalysisContentModel):
    """Layers share one bounded preview budget and compatible numeric axes."""

    layers: Sequence[AnalysisFigureLayerSpec] = Field(
        min_length=1, max_length=MAX_ANALYSIS_FIGURE_SERIES
    )

    @field_validator("layers")
    @classmethod
    def freeze_layers(
        cls, value: Sequence[AnalysisFigureLayerSpec]
    ) -> Sequence[AnalysisFigureLayerSpec]:
        return tuple(value)

    @model_validator(mode="after")
    def validate_layers(self) -> Self:
        if len({layer.id for layer in self.layers}) != len(self.layers):
            raise ValueError("analysis figure layer ids must be unique")
        return self


class AnalysisFigureLayerView(AnalysisFigureLayerSpec):
    preview: AnalysisFigure
    total_points: int = Field(ge=0)
    truncated: bool

    @model_validator(mode="after")
    def validate_preview(self) -> Self:
        returned = sum(len(series.x) for series in self.preview.series)
        if self.preview.kind != self.projection.kind:
            raise ValueError("figure layer preview kind must match its projection")
        if returned > self.total_points or self.truncated != (
            returned < self.total_points
        ):
            raise ValueError("figure layer truncation must match its point counts")
        return self


class AnalysisFigureView(_AnalysisContentModel):
    """Resolved layers retain source identities without self-publication hashes."""

    layers: Sequence[AnalysisFigureLayerView] = Field(
        min_length=1, max_length=MAX_ANALYSIS_FIGURE_SERIES
    )
    total_points: int = Field(ge=0)
    truncated: bool

    @field_validator("layers")
    @classmethod
    def freeze_layers(
        cls, value: Sequence[AnalysisFigureLayerView]
    ) -> Sequence[AnalysisFigureLayerView]:
        return tuple(value)

    @model_validator(mode="after")
    def validate_preview(self) -> Self:
        if len({layer.id for layer in self.layers}) != len(self.layers):
            raise ValueError("analysis figure layer ids must be unique")
        returned = sum(
            len(series.x) for layer in self.layers for series in layer.preview.series
        )
        if returned > MAX_ANALYSIS_FIGURE_POINTS:
            raise ValueError("analysis figure layers exceed the shared point budget")
        if self.total_points != sum(layer.total_points for layer in self.layers):
            raise ValueError("analysis figure total points must match its layers")
        if self.truncated != (returned < self.total_points):
            raise ValueError("analysis figure truncation must match its point counts")
        return self


def _analysis_field(annotation: object) -> AnalysisField | None:
    if get_origin(annotation) is not Annotated:
        return None
    metadata_items = cast("tuple[object, ...]", get_args(annotation)[1:])
    return next(
        (
            metadata
            for metadata in metadata_items
            if isinstance(metadata, AnalysisField)
        ),
        None,
    )


def _analysis_row_dtype(annotation: object) -> AnalysisRowDType:
    declared = (
        cast("object", get_args(annotation)[0])
        if get_origin(annotation) is Annotated
        else annotation
    )
    declared_args = cast("tuple[object, ...]", get_args(declared))
    union_members = tuple(
        member for member in declared_args if member is not type(None)
    )
    if union_members:
        if len(union_members) != 1 or len(union_members) == len(declared_args):
            raise TypeError(
                "analysis row fields must be scalar values or optional scalar values"
            )
        [declared] = union_members
    if declared is bool:
        return "bool"
    if declared is int:
        return "int64"
    if declared is float or declared is Quantity:
        return "float64"
    if declared is str:
        return "string"
    raise TypeError(
        "analysis row fields must be bool, int, float, str, Quantity, or optional"
    )


def _analysis_field_value(value: object, policy: AnalysisField) -> object:
    if isinstance(value, Quantity):
        if policy.unit is None:
            raise TypeError("Quantity analysis fields require a presentation unit")
        return value.to(policy.unit).value
    return value


def _figure_cell(value: object, *, column: str) -> float:
    normalized = _normalized_external_scalar(value)
    if isinstance(normalized, bool) or not isinstance(normalized, int | float):
        raise TypeError(f"analysis figure column {column!r} must contain numbers")
    return float(normalized)


class AnalysisParameterProposalReference(_AnalysisContentModel):
    """Persisted reference to the separately stored proposal record."""

    proposal_id: _NonEmptyText
    record_ref: _NonEmptyText


class AnalysisExecutionInput(_AnalysisContentModel):
    """One named, content-identified input consumed by an analysis execution."""

    name: _NonEmptyText
    kind: Literal["measurement_dataset", "derived_dataset", "artifact", "value"]
    target: _NonEmptyText
    content_hash: _NonEmptyText
    codec: _NonEmptyText
    value: JsonValue | None = None


class AnalysisExecutionOutput(_AnalysisContentModel):
    """The content identity produced by one successful analysis execution."""

    name: _NonEmptyText
    kind: Literal["derived_dataset", "artifact", "value"]
    content_hash: _NonEmptyText
    codec: _NonEmptyText


class AnalysisExecutionOutputReference(_AnalysisContentModel):
    """Exact named result of one analysis execution."""

    execution_id: _NonEmptyText
    output_name: _NonEmptyText


class AnalysisDatasetDerivation(_AnalysisContentModel):
    """First-party normalization from one traced native dataset result."""

    source: AnalysisExecutionOutputReference
    source_kind: Literal["arrow", "pandas", "polars", "xarray"]
    fields: dict[str, AnalysisField] = Field(default_factory=dict)
    index: Literal["auto", "columns", "drop"] = "auto"
    adapter: Literal["scopecat.native-dataset.v2"] = "scopecat.native-dataset.v2"


class AnalysisExecution(_AnalysisContentModel):
    """Optional execution evidence retained by an analysis publication."""

    id: _NonEmptyText
    implementation: _NonEmptyText
    deterministic: bool
    inputs: Sequence[_NonEmptyText]
    input_bindings: Sequence[AnalysisExecutionInput]
    outputs: Sequence[AnalysisExecutionOutput]
    captures: Sequence[_NonEmptyText] = ()
    access: Literal["full", "batches"] = "full"
    metadata: JsonMetadata = Field(default_factory=dict)

    @field_validator("inputs", "input_bindings", "outputs", "captures")
    @classmethod
    def freeze_edges[T](cls, value: Sequence[T]) -> Sequence[T]:
        return tuple(value)

    @model_validator(mode="after")
    def validate_input_bindings(self) -> AnalysisExecution:
        if tuple(self.inputs) != tuple(binding.name for binding in self.input_bindings):
            raise ValueError("analysis execution inputs must match its input bindings")
        output_names = tuple(output.name for output in self.outputs)
        if not output_names:
            raise ValueError("analysis execution must produce at least one output")
        if len(output_names) != len(set(output_names)):
            raise ValueError("analysis execution output names must be unique")
        return self


class AnalysisFact(_AnalysisContentModel):
    """Small typed conclusion retained directly in an analysis record."""

    schema_id: _NonEmptyText
    schema_codec: Literal["scopecat.analysis-fact-schema.v1"]
    schema_hash: Sha256ContentHash
    codec: _NonEmptyText
    value: JsonValue

    @model_validator(mode="after")
    def validate_budget(self) -> AnalysisFact:
        size = len(canonical_json(self.value).encode("utf-8"))
        if size > MAX_ANALYSIS_DATA_BYTES:
            raise ValueError(
                f"analysis fact must not exceed {MAX_ANALYSIS_DATA_BYTES} bytes"
            )
        return self


class AnalysisDatasetReference(_AnalysisContentModel):
    """Reference to one separately stored, content-addressed derived dataset."""

    dataset_id: _NonEmptyText
    content_hash: _NonEmptyText
    codec: _NonEmptyText


class AnalysisArtifactReference(_AnalysisContentModel):
    """Reference to exact bytes published as an analysis-owned artifact."""

    artifact_id: _NonEmptyText
    content_hash: _NonEmptyText
    media_type: _NonEmptyText
    filename: _NonEmptyText


class RunAnalysisSubject(_AnalysisContentModel):
    """A publication whose scientific subject is one run."""

    kind: Literal["run"] = "run"
    run_id: _NonEmptyText


class ProjectAnalysisSubject(_AnalysisContentModel):
    """A publication over explicit immutable inputs owned by a project."""

    kind: Literal["project"] = "project"


class SampleAnalysisSubject(_AnalysisContentModel):
    """A longitudinal publication whose scientific subject is one sample."""

    kind: Literal["sample"] = "sample"
    sample_id: SampleId


type AnalysisSubject = Annotated[
    RunAnalysisSubject | ProjectAnalysisSubject | SampleAnalysisSubject,
    Field(discriminator="kind"),
]


class AnalysisPublishedOutputReference(_AnalysisContentModel):
    """Exact output revision consumed from a run or project analysis."""

    subject: AnalysisSubject
    analysis_record_id: _NonEmptyText
    output_id: _NonEmptyText


class _AnalysisRecordInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: _NonEmptyText
    target: _NonEmptyText
    content_hash: _NonEmptyText
    codec: _NonEmptyText
    role: _NonEmptyText
    title: str | None = None
    metadata: JsonMetadata | None = None


class MeasurementAnalysisRecordInput(_AnalysisRecordInput):
    """One exact measurement dataset owned by a run."""

    kind: Literal["measurement_dataset"] = "measurement_dataset"
    run_id: _NonEmptyText


class AnalysisInterpretationReference(_AnalysisContentModel):
    """Exact durable judgment identity; values remain in the procedure journal."""

    procedure_run_id: _NonEmptyText
    step_key: _NonEmptyText
    request_hash: Sha256ContentHash
    response_hash: Sha256ContentHash


class InterpretationAnalysisRecordInput(_AnalysisRecordInput):
    kind: Literal["interpretation"] = "interpretation"
    source: AnalysisInterpretationReference


class PublishedAnalysisRecordInput(_AnalysisRecordInput):
    """One exact dataset, fact, or artifact output from an analysis revision."""

    kind: Literal["analysis_dataset", "analysis_fact", "analysis_artifact"]
    source: AnalysisPublishedOutputReference


type AnalysisRecordInput = Annotated[
    MeasurementAnalysisRecordInput
    | PublishedAnalysisRecordInput
    | InterpretationAnalysisRecordInput,
    Field(discriminator="kind"),
]


class ProjectAnalysisOutputReference(_AnalysisContentModel):
    """One exact project-analysis output used as decision evidence."""

    analysis_record_id: _NonEmptyText
    output_id: _NonEmptyText


class ProjectAnalysisDecisionReference(ProjectAnalysisOutputReference):
    """One exact typed fact interpreted as a project-level decision."""

    schema_id: _NonEmptyText
    schema_hash: Sha256ContentHash


class _AnalysisRecordOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: _NonEmptyText
    title: _NonEmptyText
    metadata: JsonMetadata = Field(default_factory=dict)


class AnalysisTableRecordOutput(_AnalysisRecordOutput):
    kind: Literal["table"]
    content: AnalysisTableView


class AnalysisFigureRecordOutput(_AnalysisRecordOutput):
    kind: Literal["figure"]
    content: AnalysisFigureView


class AnalysisParameterProposalRecordOutput(_AnalysisRecordOutput):
    kind: Literal["parameter_change_proposal"]
    content: AnalysisParameterProposalReference


class AnalysisFactRecordOutput(_AnalysisRecordOutput):
    kind: Literal["fact"]
    content: AnalysisFact
    produced_by: AnalysisExecutionOutputReference | None = None


class AnalysisDatasetRecordOutput(_AnalysisRecordOutput):
    kind: Literal["dataset"]
    content: AnalysisDatasetReference
    produced_by: AnalysisExecutionOutputReference | None = None
    derived_from: AnalysisDatasetDerivation | None = None


class AnalysisArtifactRecordOutput(_AnalysisRecordOutput):
    kind: Literal["artifact"]
    content: AnalysisArtifactReference
    produced_by: AnalysisExecutionOutputReference | None = None


type AnalysisRecordOutput = Annotated[
    AnalysisFactRecordOutput
    | AnalysisDatasetRecordOutput
    | AnalysisArtifactRecordOutput
    | AnalysisTableRecordOutput
    | AnalysisFigureRecordOutput
    | AnalysisParameterProposalRecordOutput,
    Field(discriminator="kind"),
]


def validate_analysis_output_content_budget(
    contents: Iterable[AnalysisTableView | AnalysisFigureView],
) -> None:
    """Reject a group of embedded outputs too large for one analysis view."""

    table_cells = 0
    figure_points = 0
    for content in contents:
        if isinstance(content, AnalysisTableView):
            table_cells += len(content.preview.columns) * len(content.preview.rows)
        else:
            figure_points += sum(
                len(series.x)
                for layer in content.layers
                for series in layer.preview.series
            )
    if table_cells > MAX_ANALYSIS_TOTAL_TABLE_CELLS:
        raise ValueError(
            "analysis total table cell count must not exceed "
            f"{MAX_ANALYSIS_TOTAL_TABLE_CELLS}"
        )
    if figure_points > MAX_ANALYSIS_TOTAL_FIGURE_POINTS:
        raise ValueError(
            "analysis total figure point count must not exceed "
            f"{MAX_ANALYSIS_TOTAL_FIGURE_POINTS}"
        )


class AnalysisRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject: AnalysisSubject
    title: _NonEmptyText
    key: _NonEmptyText | None = None
    revision: int = Field(ge=1)
    publication_hash: _NonEmptyText
    step_id: _NonEmptyText | None = None
    inputs: list[AnalysisRecordInput] = Field(default_factory=list)
    executions: list[AnalysisExecution] = Field(default_factory=list)
    outputs: list[AnalysisRecordOutput] = Field(max_length=MAX_ANALYSIS_OUTPUTS)

    @model_validator(mode="after")
    def validate_output_budget(self) -> AnalysisRecord:
        output_ids = tuple(output.id for output in self.outputs)
        if len(output_ids) != len(set(output_ids)):
            raise ValueError("analysis output ids must be unique")
        execution_ids = tuple(execution.id for execution in self.executions)
        if len(execution_ids) != len(set(execution_ids)):
            raise ValueError("analysis execution ids must be unique")
        executions_by_id = {execution.id: execution for execution in self.executions}
        materialized_outputs = tuple(
            output
            for output in self.outputs
            if isinstance(
                output,
                AnalysisDatasetRecordOutput
                | AnalysisFactRecordOutput
                | AnalysisArtifactRecordOutput,
            )
        )
        if any(
            isinstance(output, AnalysisDatasetRecordOutput)
            and output.produced_by is not None
            and output.derived_from is not None
            for output in materialized_outputs
        ):
            raise ValueError(
                "analysis dataset output cannot be both produced and derived"
            )
        output_sources = tuple(
            (
                output.derived_from.source
                if isinstance(output, AnalysisDatasetRecordOutput)
                and output.derived_from is not None
                else output.produced_by
            )
            for output in materialized_outputs
        )
        unknown_producers = {
            source.execution_id for source in output_sources if source is not None
        } - set(executions_by_id)
        if unknown_producers:
            raise ValueError("analysis output producer must identify an execution")
        for output, source in zip(
            materialized_outputs,
            output_sources,
            strict=True,
        ):
            if source is None:
                continue
            execution = executions_by_id[source.execution_id]
            try:
                execution_output = next(
                    item
                    for item in execution.outputs
                    if item.name == source.output_name
                )
            except StopIteration:
                raise ValueError(
                    "analysis output producer must identify an execution output"
                ) from None
            if (
                isinstance(output, AnalysisDatasetRecordOutput)
                and output.derived_from is not None
            ):
                if (
                    execution_output.kind != "derived_dataset"
                    or execution_output.codec != output.content.codec
                ):
                    raise ValueError(
                        "analysis dataset derivation source must be a dataset result"
                    )
                continue
            if isinstance(output, AnalysisDatasetRecordOutput):
                kind = "derived_dataset"
                content_hash = output.content.content_hash
                codec = output.content.codec
            elif isinstance(output, AnalysisArtifactRecordOutput):
                kind = "artifact"
                content_hash = output.content.content_hash
                codec = ANALYSIS_ARTIFACT_CODEC
            else:
                kind = "value"
                content_hash = f"sha256:{stable_content_hash(output.content.value)}"
                codec = output.content.codec
            if (
                execution_output.kind != kind
                or execution_output.content_hash != content_hash
                or execution_output.codec != codec
            ):
                raise ValueError(
                    "analysis output content must match its producing execution"
                )
        dataset_ids = {
            output.id
            for output in self.outputs
            if isinstance(output, AnalysisDatasetRecordOutput)
        }
        for output in self.outputs:
            if not isinstance(
                output,
                AnalysisTableRecordOutput | AnalysisFigureRecordOutput,
            ):
                continue
            sources = (
                tuple(layer.source for layer in output.content.layers)
                if isinstance(output, AnalysisFigureRecordOutput)
                else (output.content.source,)
            )
            for source in sources:
                if (
                    isinstance(source, AnalysisDatasetViewSource)
                    and source.output_id not in dataset_ids
                ):
                    raise ValueError(
                        "analysis view source must identify a dataset output"
                    )
        validate_analysis_output_content_budget(
            output.content
            for output in self.outputs
            if isinstance(
                output, AnalysisTableRecordOutput | AnalysisFigureRecordOutput
            )
        )
        return self


AnalysisPublishedDatasetViewSource.model_rebuild()
AnalysisFigureLayerSpec.model_rebuild()
AnalysisFigureViewSpec.model_rebuild()
AnalysisFigureLayerView.model_rebuild()
AnalysisFigureView.model_rebuild()
