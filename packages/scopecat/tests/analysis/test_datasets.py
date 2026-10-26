# pyright: reportUnknownArgumentType=false, reportUnknownMemberType=false
# pyright: reportUnknownVariableType=false

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

import numpy as np
import pyarrow as pa
import pytest
import xarray as xr

import scopecat as sc
from scopecat.analysis.datasets import DERIVED_DATASET_CODEC, DerivedDataset
from scopecat.records.analysis import (
    MAX_ANALYSIS_FIGURE_POINTS,
    MAX_ANALYSIS_TABLE_ROWS,
    AnalysisTable,
)


@dataclass(frozen=True, slots=True)
class _FitRow:
    bias: Annotated[
        sc.Quantity,
        sc.AnalysisField(
            id="bias_mv",
            role="coordinate",
            label="Bias",
            unit="mV",
        ),
    ]
    score: Annotated[
        float,
        sc.AnalysisField(label="Fit score", unit="ratio"),
    ]
    converged: Annotated[bool, sc.AnalysisField(label="Converged")]
    internal_iteration: int


@dataclass(frozen=True, slots=True)
class _OtherFitRow:
    score: Annotated[float, sc.AnalysisField(label="Score")]


@dataclass(frozen=True, slots=True)
class _NullableFitRow:
    score: Annotated[
        float | None,
        sc.AnalysisField(label="Fit score", unit="ratio"),
    ]


def test_derived_dataset_round_trips_exact_arrow_and_semantic_schema() -> None:
    source = pa.table(
        {
            "bias": pa.array([-1.0, 0.0, 1.0], type=pa.float64()),
            "score": pa.array([0.2, 0.8, 0.3], type=pa.float64()),
        }
    )

    dataset = sc.derived_dataset(
        source,
        fields={
            "bias": sc.AnalysisField(
                role="coordinate",
                unit="V",
                label="DC bias",
            ),
            "score": sc.AnalysisField(unit="ratio", label="Fit score"),
        },
    )
    restored = DerivedDataset.from_arrow_ipc(
        dataset.to_arrow_ipc(),
        schema=dataset.schema,
    )

    assert dataset.schema.schema_id == "scopecat.derived-dataset.v3"
    assert dataset.schema.fields[0].source_name == "bias"
    assert dataset.schema.fields[0].role == "coordinate"
    assert dataset.schema.fields[0].unit == "V"
    assert dataset.table.schema.field("score").metadata[b"units"] == b"ratio"
    assert restored.schema == dataset.schema
    assert restored.table.equals(dataset.table, check_metadata=True)

    presentation = restored.to_analysis_table()
    assert [column.id for column in presentation.columns] == ["bias", "score"]
    assert presentation.columns[0].label == "DC bias"
    assert presentation.rows[1].cells == [0.0, 0.8]


def test_derived_dataset_maps_native_names_to_stable_field_ids() -> None:
    source = xr.Dataset(
        data_vars={"raw_signal": (("raw_bias",), [0.2, 0.8])},
        coords={"raw_bias": [0.0, 1.0]},
    )

    dataset = sc.derived_dataset(
        source,
        fields={
            "raw_bias": sc.AnalysisField(
                id="bias",
                role="coordinate",
                unit="V",
                label="DC bias",
            ),
            "raw_signal": sc.AnalysisField(
                id="signal",
                unit="ratio",
                label="Signal",
            ),
        },
    )

    assert dataset.table.column_names == ["bias", "signal"]
    assert [field.source_name for field in dataset.schema.fields] == [
        "raw_bias",
        "raw_signal",
    ]
    assert (
        dataset.table.schema.field("bias").metadata[b"scopecat.source_name"]
        == b"raw_bias"
    )
    restored = dataset.to_xarray()
    assert restored.sizes == {"bias": 2}
    assert tuple(restored.coords) == ("bias",)
    assert tuple(restored.data_vars) == ("signal",)
    assert restored["bias"].attrs == {"units": "V", "long_name": "DC bias"}


def test_derived_dataset_accepts_pandas_results() -> None:
    pd = pytest.importorskip("pandas")
    dataset = sc.derived_dataset(
        pd.DataFrame({"x": [1, 2], "y": [3.0, 4.0]}),
        fields={"x": sc.AnalysisField(role="coordinate")},
    )
    assert dataset.table.to_pylist() == [
        {"x": 1, "y": 3.0},
        {"x": 2, "y": 4.0},
    ]
    assert isinstance(dataset.to_pandas(), pd.DataFrame)


def test_derived_dataset_accepts_polars_results() -> None:
    pl = pytest.importorskip("polars")
    dataset = sc.derived_dataset(
        pl.DataFrame({"x": [1, 2], "y": [3.0, 4.0]}),
        fields={"x": sc.AnalysisField(role="coordinate")},
    )
    assert dataset.table.to_pylist() == [
        {"x": 1, "y": 3.0},
        {"x": 2, "y": 4.0},
    ]
    assert isinstance(dataset.to_polars(), pl.DataFrame)


def test_derived_dataset_accepts_xarray_results() -> None:
    xarray_dataset = sc.derived_dataset(
        xr.Dataset(
            data_vars={"y": (("x",), [3.0, 4.0], {"quality": "fit"})},
            coords={"x": ("x", [1, 2], {"long_name": "Bias index"})},
            attrs={"model": "linear"},
        ),
        fields={"y": sc.AnalysisField(unit="ratio")},
    )

    assert xarray_dataset.table.to_pylist() == [
        {"x": 1, "y": 3.0},
        {"x": 2, "y": 4.0},
    ]
    restored_xarray = xarray_dataset.to_xarray()
    assert restored_xarray.sizes == {"x": 2}
    assert restored_xarray.attrs == {"model": "linear"}
    assert restored_xarray["x"].attrs == {"long_name": "Bias index"}
    assert restored_xarray["y"].attrs == {
        "quality": "fit",
        "units": "ratio",
    }
    assert restored_xarray.identical(
        xr.Dataset(
            data_vars={
                "y": (
                    ("x",),
                    [3.0, 4.0],
                    {"quality": "fit", "units": "ratio"},
                )
            },
            coords={"x": ("x", [1, 2], {"long_name": "Bias index"})},
            attrs={"model": "linear"},
        )
    )


def test_derived_dataset_projects_annotated_rows_with_table_semantics() -> None:
    rows = (
        _FitRow(sc.Quantity(0.1, "V"), 0.2, True, 4),
        _FitRow(sc.Quantity(0.2, "V"), 0.8, False, 5),
    )

    dataset = sc.derived_dataset(rows)

    assert dataset.table.column_names == ["bias_mv", "score", "converged"]
    assert dataset.table.to_pylist() == [
        {"bias_mv": 100.0, "score": 0.2, "converged": True},
        {"bias_mv": 200.0, "score": 0.8, "converged": False},
    ]
    assert [field.source_name for field in dataset.schema.fields] == [
        "bias",
        "score",
        "converged",
    ]
    assert dataset.schema.fields[0].role == "coordinate"
    assert dataset.schema.fields[0].unit == "mV"
    assert dataset.schema.fields[0].label == "Bias"
    assert dataset.to_analysis_table() == AnalysisTable.from_objects(rows)


def test_annotated_rows_keep_the_declared_type_for_an_all_null_column() -> None:
    rows = (_NullableFitRow(None), _NullableFitRow(None))

    dataset = sc.derived_dataset(rows)

    assert dataset.table.schema.field("score").type == pa.float64()
    assert dataset.table["score"].to_pylist() == [None, None]
    assert dataset.schema.fields[0].unit == "ratio"
    assert dataset.to_analysis_table().rows[0].cells == [None]


def test_derived_dataset_annotated_rows_are_unbounded_and_own_their_fields() -> None:
    rows = tuple(
        _FitRow(sc.Quantity(index, "mV"), 0.5, True, index) for index in range(501)
    )

    dataset = sc.derived_dataset(rows)

    assert len(dataset.table) == 501
    with pytest.raises(ValueError, match="already declare their field semantics"):
        sc.derived_dataset(
            rows,
            fields={"bias": sc.AnalysisField(unit="V")},
        )
    with pytest.raises(TypeError, match="share one dataclass type"):
        sc.derived_dataset((rows[0], _OtherFitRow(0.5)))


def test_derived_dataset_presentation_previews_have_independent_budgets() -> None:
    row_count = MAX_ANALYSIS_FIGURE_POINTS + 10
    dataset = sc.derived_dataset(
        pa.table(
            {
                "x": range(row_count),
                "y": range(row_count),
            }
        )
    )

    table = dataset.to_analysis_table()
    figure = dataset.to_analysis_figure(kind="line", x="x", y="y")

    assert len(dataset.table) == row_count
    assert len(table.rows) == MAX_ANALYSIS_TABLE_ROWS
    assert len(figure.series[0].x) == MAX_ANALYSIS_FIGURE_POINTS
    assert figure.series[0].x[-1] == MAX_ANALYSIS_FIGURE_POINTS - 1


def test_derived_dataset_retains_only_meaningful_pandas_indexes() -> None:
    pd = pytest.importorskip("pandas")

    implicit = sc.derived_dataset(pd.DataFrame({"score": [0.2, 0.8]}))
    indexed = sc.derived_dataset(
        pd.DataFrame(
            {"score": [0.2, 0.8]},
            index=pd.Index([4.0, 5.0], name="bias"),
        )
    )
    dropped = sc.derived_dataset(
        pd.DataFrame(
            {"score": [0.2, 0.8]},
            index=pd.Index([4.0, 5.0], name="bias"),
        ),
        index="drop",
    )

    assert implicit.table.column_names == ["score"]
    assert indexed.table.column_names == ["bias", "score"]
    assert indexed.schema.fields[0].role == "coordinate"
    assert dropped.table.column_names == ["score"]


def test_derived_dataset_pandas_backend_can_preserve_arrow_dtypes() -> None:
    pd = pytest.importorskip("pandas")
    frame = pd.DataFrame(
        {
            "category": pd.Categorical(
                ["low", "high"],
                categories=["low", "high"],
                ordered=True,
            ),
            "timestamp": pd.to_datetime(
                ["2026-01-01T00:00:00Z", "2026-01-02T00:00:00Z"],
                utc=True,
            ),
            "nullable_int": pd.Series([1, None], dtype="Int64"),
        }
    )

    dataset = sc.derived_dataset(frame)
    familiar = dataset.to_pandas()
    lossless = dataset.to_pandas(dtype_backend="pyarrow")

    assert str(dataset.table.schema.field("category").type).startswith("dictionary<")
    assert str(dataset.table.schema.field("timestamp").type) == "timestamp[us, tz=UTC]"
    assert str(dataset.table.schema.field("nullable_int").type) == "int64"
    assert str(familiar["nullable_int"].dtype) == "float64"
    assert str(lossless["nullable_int"].dtype) == "int64[pyarrow]"
    assert lossless["nullable_int"].iloc[0] == 1
    assert pd.isna(lossless["nullable_int"].iloc[1])


def test_derived_dataset_rejects_ambiguous_pandas_column_names() -> None:
    pd = pytest.importorskip("pandas")

    with pytest.raises(ValueError, match="index names conflict"):
        sc.derived_dataset(
            pd.DataFrame(
                {"bias": [0.2, 0.8]},
                index=pd.Index([4.0, 5.0], name="bias"),
            )
        )
    with pytest.raises(TypeError, match="columns must be strings"):
        sc.derived_dataset(pd.DataFrame({1: [0.2, 0.8]}), index="drop")


def test_derived_dataset_inherits_and_overrides_pandas_semantics() -> None:
    pd = pytest.importorskip("pandas")
    frame = pd.DataFrame({"bias": [0.0, 1.0], "score": [0.2, 0.8]})
    frame.attrs["scopecat"] = {
        "fields": [
            {
                "name": "bias",
                "role": "coordinate",
                "unit": "V",
                "label": "Bias",
            },
            {
                "name": "score",
                "role": "observable",
                "unit": "ratio",
                "label": "Score",
            },
        ]
    }

    dataset = sc.derived_dataset(
        frame,
        fields={
            "bias": sc.AnalysisField(unit="mV"),
            "score": sc.AnalysisField(label="Fit score"),
        },
    )

    assert dataset.schema.fields[0].role == "coordinate"
    assert dataset.schema.fields[0].unit == "mV"
    assert dataset.schema.fields[0].label == "Bias"
    assert dataset.table["bias"].to_pylist() == [0.0, 1000.0]
    assert dataset.schema.fields[1].unit == "ratio"
    assert dataset.schema.fields[1].label == "Fit score"


def test_derived_dataset_rejects_incompatible_or_non_numeric_units() -> None:
    pd = pytest.importorskip("pandas")
    frame = pd.DataFrame({"bias": [0.0], "label": ["origin"]})
    frame.attrs["scopecat"] = {
        "fields": [{"name": "bias", "unit": "V"}],
    }

    with pytest.raises(ValueError, match="cannot convert 'V' to 'Hz'"):
        sc.derived_dataset(
            frame,
            fields={"bias": sc.AnalysisField(unit="Hz")},
        )

    with pytest.raises(TypeError, match="cannot assign unit 'V' to non-numeric"):
        sc.derived_dataset(
            frame,
            fields={"label": sc.AnalysisField(unit="V")},
        )


def test_derived_dataset_inherits_arrow_and_xarray_semantics() -> None:
    arrow_schema = pa.schema(
        [
            pa.field(
                "bias",
                pa.float64(),
                metadata={
                    b"units": b"V",
                    b"long_name": b"Bias",
                    b"scopecat.role": b"coordinate",
                },
            ),
            pa.field("score", pa.float64(), metadata={b"units": b"ratio"}),
        ]
    )
    arrow = sc.derived_dataset(
        pa.Table.from_arrays([[0.0, 1.0], [0.2, 0.8]], schema=arrow_schema),
        fields={"score": sc.AnalysisField(label="Fit score")},
    )
    xarray = sc.derived_dataset(
        xr.Dataset(
            data_vars={
                "score": (
                    ("bias",),
                    [0.2, 0.8],
                    {"units": "ratio", "long_name": "Fit score"},
                )
            },
            coords={"bias": ("bias", [0.0, 1.0], {"units": "V"})},
        )
    )

    assert arrow.schema.fields[0].role == "coordinate"
    assert arrow.schema.fields[0].unit == "V"
    assert arrow.schema.fields[1].label == "Fit score"
    assert xarray.schema.fields[0].role == "coordinate"
    assert xarray.schema.fields[0].unit == "V"
    assert xarray.schema.fields[1].unit == "ratio"
    assert xarray.schema.fields[1].label == "Fit score"


def test_derived_dataset_rejects_implicit_lossy_xarray_flattening() -> None:
    multidimensional = xr.Dataset(
        data_vars={"signal": (("bias", "frequency"), [[1.0, 2.0], [3.0, 4.0]])},
        coords={"bias": [0.0, 1.0], "frequency": [5.0, 6.0]},
    )
    mixed_shape = xr.Dataset(
        data_vars={
            "signal": (("bias",), [1.0, 2.0]),
            "fit_quality": 0.9,
        },
        coords={"bias": [0.0, 1.0]},
    )

    with pytest.raises(ValueError, match="exactly one dimension"):
        sc.derived_dataset(multidimensional)
    with pytest.raises(ValueError, match="must use dimension"):
        sc.derived_dataset(mixed_shape)


def test_derived_xarray_round_trip_preserves_physical_dtype() -> None:
    source = xr.Dataset(
        data_vars={"score": (("bias",), np.asarray([0.2, 0.8], dtype=np.float32))},
        coords={"bias": np.asarray([1, 2], dtype=np.int32)},
    )

    restored = sc.derived_dataset(source).to_xarray()

    assert restored["bias"].dtype == np.dtype(np.int32)
    assert restored["score"].dtype == np.dtype(np.float32)
    assert restored.identical(source)


def test_derived_dataset_round_trips_raw_arrow_content() -> None:
    dataset = sc.derived_dataset(pa.table({"score": [0.2, 0.8]}))

    restored = DerivedDataset.from_arrow_ipc(
        dataset.to_arrow_ipc(),
        schema=dataset.schema,
    )

    assert restored.schema == dataset.schema
    assert restored.table.equals(dataset.table, check_metadata=True)


def test_derived_dataset_presentation_selects_only_scalar_view_columns() -> None:
    dataset = sc.derived_dataset(
        pa.table(
            {
                "bias": [0.0, 1.0],
                "score": [0.2, 0.8],
                "trace": [[1.0, 2.0], [3.0, 4.0]],
            }
        ),
        fields={"bias": sc.AnalysisField(role="coordinate")},
    )

    table = dataset.to_analysis_table(columns=("bias", "score"))

    assert [column.id for column in table.columns] == ["bias", "score"]
    assert table.rows[1].cells == [1.0, 0.8]


def test_derived_dataset_codec_is_public_and_versioned() -> None:
    assert DERIVED_DATASET_CODEC == "scopecat.derived-dataset.arrow-ipc.v2"
