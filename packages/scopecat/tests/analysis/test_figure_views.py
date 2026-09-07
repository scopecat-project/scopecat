# pyright: reportUnknownArgumentType=false, reportUnknownMemberType=false
# pyright: reportUnknownVariableType=false
from __future__ import annotations

import pyarrow as pa
import pytest

from scopecat.analysis.datasets import DerivedDataset
from scopecat.analysis.figure_views import project_figure_layers
from scopecat.records.analysis import (
    AnalysisDatasetViewSource,
    AnalysisField,
    AnalysisFigureLayerSpec,
    AnalysisFigureProjection,
    AnalysisUncertaintyProjection,
)


def layer(id: str, *, kind: str = "line") -> AnalysisFigureLayerSpec:
    return AnalysisFigureLayerSpec(
        id=id,
        source=AnalysisDatasetViewSource(output_id=id),
        projection=AnalysisFigureProjection.model_validate(
            {"kind": kind, "x": "x", "y": "y"}
        ),
    )


def test_each_large_layer_has_a_share_of_the_aggregate_budget() -> None:
    data = DerivedDataset.from_arrow(
        pa.table({"x": range(100_000), "y": range(100_000)})
    )
    view = project_figure_layers(
        [
            (layer("measured", kind="scatter"), data, len(data)),
            (layer("fit"), data, len(data)),
        ]
    )
    assert type(view).model_validate(view.model_dump(mode="json")) == view
    assert view.total_points == 200_000
    assert view.truncated
    assert [len(item.preview.series[0].x) for item in view.layers] == [2048, 2048]
    assert [
        item.source.output_id
        for item in view.layers
        if isinstance(item.source, AnalysisDatasetViewSource)
    ] == ["measured", "fit"]
    assert [item.preview.kind for item in view.layers] == ["scatter", "line"]
    assert [item.preview.series[0].x[-1] for item in view.layers] == [2047, 2047]


def test_layer_axes_and_absolute_uncertainty_convert_to_first_layer_units() -> None:
    measured = DerivedDataset.from_arrow(
        pa.table({"x": [1.0], "y": [2.0]}),
        fields={"x": AnalysisField(unit="us"), "y": AnalysisField(unit="V")},
    )
    fit = DerivedDataset.from_arrow(
        pa.table({"x": [1000.0], "y": [2000.0], "lo": [1900.0], "hi": [2100.0]}),
        fields={
            "x": AnalysisField(unit="ns"),
            "y": AnalysisField(unit="mV"),
            "lo": AnalysisField(unit="mV"),
            "hi": AnalysisField(unit="mV"),
        },
    )
    fit_layer = layer("fit").model_copy(
        update={
            "projection": AnalysisFigureProjection(
                kind="line",
                x="x",
                y="y",
                uncertainty=AnalysisUncertaintyProjection(
                    lower="lo",
                    upper="hi",
                    meaning="95% confidence interval from project fit",
                    style="band",
                ),
            )
        }
    )
    view = project_figure_layers(
        [
            (layer("measured", kind="scatter"), measured, len(measured)),
            (fit_layer, fit, len(fit)),
        ]
    )
    series = view.layers[1].preview.series[0]
    assert series.x == [1.0]
    assert series.y == [2.0]
    assert series.y_lower == pytest.approx([1.9])
    assert series.y_upper == pytest.approx([2.1])
    assert view.layers[1].projection.uncertainty == fit_layer.projection.uncertainty
    assert view.layers[1].preview.y_axis.unit == "V"


def test_incompatible_axes_are_rejected_instead_of_rescaled() -> None:
    voltage = DerivedDataset.from_arrow(
        pa.table({"x": [1], "y": [2]}), fields={"y": AnalysisField(unit="V")}
    )
    time = DerivedDataset.from_arrow(
        pa.table({"x": [1], "y": [2]}), fields={"y": AnalysisField(unit="s")}
    )
    with pytest.raises(ValueError, match="compatible units"):
        project_figure_layers(
            [
                (layer("voltage"), voltage, len(voltage)),
                (layer("time"), time, len(time)),
            ]
        )


def test_external_ipc_preview_counts_batches_but_materializes_only_selected_rows() -> (
    None
):
    data = DerivedDataset.from_arrow(
        pa.table({"x": range(12_000), "y": range(12_000), "unused": ["large"] * 12_000})
    )
    sink = pa.BufferOutputStream()
    with pa.ipc.new_stream(sink, data.table.schema) as writer:
        writer.write_table(data.table, max_chunksize=1000)
    selected, total = DerivedDataset.preview_from_arrow_ipc(
        sink.getvalue().to_pybytes(), schema=data.schema, columns=("x", "y"), limit=2048
    )
    assert total == 12_000
    assert len(selected) == 2048
    assert selected.table.column_names == ["x", "y"]
    assert selected.table["x"].to_pylist()[-1] == 2047
