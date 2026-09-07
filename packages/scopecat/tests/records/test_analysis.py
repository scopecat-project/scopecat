from __future__ import annotations

import sys
from dataclasses import dataclass
from types import ModuleType
from typing import Annotated

import numpy as np
import pytest
from pydantic import ValidationError

from scopecat.analysis.facts import (
    ANALYSIS_FACT_SCHEMA_CODEC,
    SCALAR_FACT_SCHEMA_HASH,
)
from scopecat.kernel.content_identity import stable_content_hash
from scopecat.kernel.quantity import Quantity
from scopecat.records.analysis import (
    MAX_ANALYSIS_FIGURE_POINTS,
    MAX_ANALYSIS_OUTPUTS,
    MAX_ANALYSIS_TABLE_COLUMNS,
    MAX_ANALYSIS_TABLE_ROWS,
    MAX_ANALYSIS_TABLE_SAFE_INTEGER,
    MAX_ANALYSIS_TOTAL_FIGURE_POINTS,
    MAX_ANALYSIS_TOTAL_TABLE_CELLS,
    AnalysisDatasetDerivation,
    AnalysisDatasetRecordOutput,
    AnalysisDatasetReference,
    AnalysisDatasetViewSource,
    AnalysisExecution,
    AnalysisExecutionInput,
    AnalysisExecutionOutput,
    AnalysisExecutionOutputReference,
    AnalysisFact,
    AnalysisFactRecordOutput,
    AnalysisField,
    AnalysisFigure,
    AnalysisFigureAxis,
    AnalysisFigureLayerView,
    AnalysisFigureProjection,
    AnalysisFigureRecordOutput,
    AnalysisFigureSeries,
    AnalysisFigureView,
    AnalysisParameterProposalRecordOutput,
    AnalysisParameterProposalReference,
    AnalysisPublishedOutputReference,
    AnalysisRecord,
    AnalysisTable,
    AnalysisTableColumn,
    AnalysisTableRecordOutput,
    AnalysisTableRow,
    AnalysisTableView,
    MeasurementAnalysisRecordInput,
    PublishedAnalysisRecordInput,
    RunAnalysisSubject,
)

_PUBLICATION_HASH = f"sha256:{'0' * 64}"
_FIT_VALUE_HASH = f"sha256:{stable_content_hash(5.1)}"
_DATASET_HASH = f"sha256:{'1' * 64}"


def _dataset_output(id: str = "fit-data") -> AnalysisDatasetRecordOutput:
    return AnalysisDatasetRecordOutput(
        kind="dataset",
        id=id,
        title="Fit data",
        content=AnalysisDatasetReference(
            dataset_id=f"analysis-{id}",
            content_hash=_DATASET_HASH,
            codec="scopecat.derived-dataset.arrow-ipc.v2",
        ),
    )


@dataclass(frozen=True)
class _Observation:
    bias: Annotated[
        Quantity,
        AnalysisField(id="bias_mv", label="Bias", unit="mV"),
    ]
    response: Annotated[float, AnalysisField(label="Response", unit="ratio")]
    group: Annotated[int, AnalysisField(label="Group")]


def test_analysis_projects_annotated_objects_into_tables_and_figures() -> None:
    table = AnalysisTable.from_objects(
        [
            _Observation(Quantity(0.1, "V"), 0.2, 1),
            _Observation(Quantity(0.2, "V"), 0.4, 1),
            _Observation(Quantity(0.3, "V"), 0.6, 2),
        ]
    )
    figure = AnalysisFigure.from_table(
        table,
        kind="scatter",
        x="bias_mv",
        y="response",
        series="group",
    )

    assert [column.id for column in table.columns] == [
        "bias_mv",
        "response",
        "group",
    ]
    assert [row.cells for row in table.rows] == [
        [100.0, 0.2, 1],
        [200.0, 0.4, 1],
        [300.0, 0.6, 2],
    ]
    assert [(series.id, series.x, series.y) for series in figure.series] == [
        ("1", [100.0, 200.0], [0.2, 0.4]),
        ("2", [300.0], [0.6]),
    ]


def test_analysis_projects_postponed_fields_after_their_module_is_unloaded() -> None:
    module_name = "test_detached_analysis_result"
    module = ModuleType(module_name)
    module.__dict__.update(
        Annotated=Annotated,
        AnalysisField=AnalysisField,
        dataclass=dataclass,
    )
    sys.modules[module_name] = module
    exec(  # noqa: S102 - isolate a real unloaded project-module boundary
        """
from __future__ import annotations

@dataclass(frozen=True, slots=True)
class Result:
    score: Annotated[float, AnalysisField(label="Score")]
""",
        module.__dict__,
    )
    result_type = module.Result
    del sys.modules[module_name]

    table = AnalysisTable.from_objects([result_type(0.5)])

    assert table.columns == [AnalysisTableColumn(id="score", label="Score")]
    assert table.rows == [AnalysisTableRow(cells=[0.5])]


def test_analysis_record_outputs_round_trip_as_discriminated_display_contracts() -> (
    None
):
    record = AnalysisRecord(
        subject=RunAnalysisSubject(run_id="run-analysis"),
        title="Fit review",
        revision=1,
        publication_hash=_PUBLICATION_HASH,
        executions=[
            AnalysisExecution(
                id="fit",
                implementation="python:lab.fit",
                deterministic=True,
                inputs=("dataset",),
                input_bindings=(
                    AnalysisExecutionInput(
                        name="dataset",
                        kind="measurement_dataset",
                        target="measurement-dataset",
                        content_hash="sha256:measurements",
                        codec="scopecat.measurement-dataset.v12",
                    ),
                ),
                outputs=(
                    AnalysisExecutionOutput(
                        name="fit",
                        kind="value",
                        content_hash=_FIT_VALUE_HASH,
                        codec="scopecat.python-json.v1",
                    ),
                ),
            )
        ],
        outputs=[
            AnalysisFactRecordOutput(
                kind="fact",
                id="fitted-frequency",
                title="Fitted frequency",
                produced_by=AnalysisExecutionOutputReference(
                    execution_id="fit",
                    output_name="fit",
                ),
                content=AnalysisFact(
                    schema_id="scopecat.scalar.v1",
                    schema_codec=ANALYSIS_FACT_SCHEMA_CODEC,
                    schema_hash=SCALAR_FACT_SCHEMA_HASH,
                    codec="scopecat.python-json.v1",
                    value=5.1,
                ),
            ),
            _dataset_output(),
            AnalysisTableRecordOutput(
                kind="table",
                id="fit-parameters",
                title="Fit parameters",
                content=AnalysisTableView(
                    source=AnalysisDatasetViewSource(output_id="fit-data"),
                    columns=("frequency", "converged"),
                    preview=AnalysisTable.from_rows(
                        [{"frequency": 5.1, "converged": True}],
                        columns=[
                            AnalysisTableColumn(
                                id="frequency",
                                label="Frequency",
                                unit="GHz",
                            ),
                            AnalysisTableColumn(id="converged", label="Converged"),
                        ],
                    ),
                    total_rows=1,
                    truncated=False,
                ),
            ),
            AnalysisFigureRecordOutput(
                kind="figure",
                id="fit-curve",
                title="Fit curve",
                content=AnalysisFigureView(
                    layers=(
                        AnalysisFigureLayerView(
                            id="data",
                            source=AnalysisDatasetViewSource(output_id="fit-data"),
                            projection=AnalysisFigureProjection(
                                kind="line", x="bias", y="frequency"
                            ),
                            preview=AnalysisFigure(
                                kind="line",
                                x_axis=AnalysisFigureAxis(label="Bias", unit="V"),
                                y_axis=AnalysisFigureAxis(
                                    label="Frequency", unit="GHz"
                                ),
                                series=[
                                    AnalysisFigureSeries(
                                        id="fit", x=[-0.1, 0.0, 0.1], y=[5.0, 5.1, 5.0]
                                    )
                                ],
                            ),
                            total_points=3,
                            truncated=False,
                        ),
                    ),
                    total_points=3,
                    truncated=False,
                ),
            ),
            AnalysisParameterProposalRecordOutput(
                kind="parameter_change_proposal",
                id="readout-fit",
                title="readout-fit",
                content=AnalysisParameterProposalReference(
                    proposal_id="readout-fit",
                    record_ref="records/parameter-change-proposal/readout-fit.json",
                ),
            ),
        ],
    )

    restored = AnalysisRecord.model_validate_json(record.model_dump_json())

    assert isinstance(restored.outputs[0], AnalysisFactRecordOutput)
    assert restored.outputs[0].produced_by == AnalysisExecutionOutputReference(
        execution_id="fit",
        output_name="fit",
    )
    assert restored.outputs[0].content.value == 5.1
    assert restored.executions[0].input_bindings[0].target == "measurement-dataset"
    assert isinstance(restored.outputs[1], AnalysisDatasetRecordOutput)
    assert isinstance(restored.outputs[2], AnalysisTableRecordOutput)
    assert restored.outputs[2].content.preview.rows[0].cells == [5.1, True]
    assert isinstance(restored.outputs[3], AnalysisFigureRecordOutput)
    assert restored.outputs[3].content.layers[0].preview.series[0].y == [5.0, 5.1, 5.0]
    assert isinstance(restored.outputs[4], AnalysisParameterProposalRecordOutput)
    assert restored.outputs[4].content.proposal_id == "readout-fit"


def test_analysis_dataset_derivation_round_trips_its_adapter_contract() -> None:
    record = AnalysisRecord(
        subject=RunAnalysisSubject(run_id="run-analysis"),
        title="Mapped dataset",
        revision=1,
        publication_hash=_PUBLICATION_HASH,
        executions=[
            AnalysisExecution(
                id="fit",
                implementation="python:lab.fit",
                deterministic=True,
                inputs=(),
                input_bindings=(),
                outputs=(
                    AnalysisExecutionOutput(
                        name="frame",
                        kind="derived_dataset",
                        content_hash="sha256:source-frame",
                        codec="scopecat.derived-dataset.arrow-ipc.v2",
                    ),
                ),
            )
        ],
        outputs=[
            AnalysisDatasetRecordOutput(
                kind="dataset",
                id="fits",
                title="Fits",
                content=AnalysisDatasetReference(
                    dataset_id="analysis-fit-fits",
                    content_hash="sha256:mapped-frame",
                    codec="scopecat.derived-dataset.arrow-ipc.v2",
                ),
                derived_from=AnalysisDatasetDerivation(
                    source=AnalysisExecutionOutputReference(
                        execution_id="fit",
                        output_name="frame",
                    ),
                    source_kind="pandas",
                    fields={
                        "raw_frequency": AnalysisField(
                            id="frequency",
                            role="coordinate",
                            unit="GHz",
                        )
                    },
                    index="drop",
                ),
            )
        ],
    )

    restored = AnalysisRecord.model_validate_json(record.model_dump_json())

    assert isinstance(restored.outputs[0], AnalysisDatasetRecordOutput)
    assert isinstance(record.outputs[0], AnalysisDatasetRecordOutput)
    assert restored.outputs[0].derived_from == record.outputs[0].derived_from


def test_analysis_embedded_outputs_have_gui_safe_size_limits() -> None:
    with pytest.raises(ValidationError, match="at most 500 items"):
        AnalysisTable(
            columns=[AnalysisTableColumn(id="value")],
            rows=[
                AnalysisTableRow(cells=[index])
                for index in range(MAX_ANALYSIS_TABLE_ROWS + 1)
            ],
        )

    points_per_series = MAX_ANALYSIS_FIGURE_POINTS // 2 + 1
    values = [float(index) for index in range(points_per_series)]
    with pytest.raises(ValidationError, match="total point count"):
        AnalysisFigure(
            kind="scatter",
            x_axis=AnalysisFigureAxis(label="x"),
            y_axis=AnalysisFigureAxis(label="y"),
            series=[
                AnalysisFigureSeries(id="first", x=values, y=values),
                AnalysisFigureSeries(id="second", x=values, y=values),
            ],
        )


def test_analysis_table_integer_rejects_values_outside_javascript_safe_range() -> None:
    row = AnalysisTableRow(
        cells=[
            -MAX_ANALYSIS_TABLE_SAFE_INTEGER,
            MAX_ANALYSIS_TABLE_SAFE_INTEGER,
        ]
    )

    assert row.cells == [
        -MAX_ANALYSIS_TABLE_SAFE_INTEGER,
        MAX_ANALYSIS_TABLE_SAFE_INTEGER,
    ]
    with pytest.raises(ValidationError, match="JavaScript safe range"):
        AnalysisTableRow(cells=[MAX_ANALYSIS_TABLE_SAFE_INTEGER + 2])
    with pytest.raises(ValidationError, match="JavaScript safe range"):
        AnalysisTableRow.model_validate_json('{"cells":[9007199254740993]}')


def test_analysis_helpers_normalize_numpy_arrays_and_scalars() -> None:
    table = AnalysisTable.from_rows([{"count": np.int64(7), "ratio": np.float64(0.25)}])
    series = AnalysisFigureSeries.from_arrays(
        id="numpy",
        x=np.asarray([1, 2], dtype=np.int64),
        y=np.asarray([0.25, 0.5], dtype=np.float64),
    )

    assert table.rows[0].cells == [7, 0.25]
    assert series.x == [1.0, 2.0]
    assert series.y == [0.25, 0.5]


def test_analysis_record_bounds_total_embedded_display_content() -> None:
    table = AnalysisTable(
        columns=[
            AnalysisTableColumn(id=f"column-{index}")
            for index in range(MAX_ANALYSIS_TABLE_COLUMNS)
        ],
        rows=[
            AnalysisTableRow(cells=[index] * MAX_ANALYSIS_TABLE_COLUMNS)
            for index in range(MAX_ANALYSIS_TABLE_ROWS)
        ],
    )
    table_output = AnalysisTableRecordOutput(
        kind="table",
        id="large-table",
        title="large table",
        content=AnalysisTableView(
            source=AnalysisDatasetViewSource(output_id="fit-data"),
            columns=tuple(column.id for column in table.columns),
            preview=table,
            total_rows=len(table.rows),
            truncated=False,
        ),
    )
    with pytest.raises(ValidationError, match="total table cell count"):
        AnalysisRecord(
            subject=RunAnalysisSubject(run_id="run-table-budget"),
            title="large tables",
            revision=1,
            publication_hash=_PUBLICATION_HASH,
            outputs=[
                _dataset_output(),
                *(
                    table_output.model_copy(update={"id": f"table-{index}"})
                    for index in range(
                        MAX_ANALYSIS_TOTAL_TABLE_CELLS
                        // (MAX_ANALYSIS_TABLE_COLUMNS * MAX_ANALYSIS_TABLE_ROWS)
                        + 1
                    )
                ),
            ],
        )

    values = [float(index) for index in range(MAX_ANALYSIS_FIGURE_POINTS)]
    figure_output = AnalysisFigureRecordOutput(
        kind="figure",
        id="large-figure",
        title="large figure",
        content=AnalysisFigureView(
            layers=(
                AnalysisFigureLayerView(
                    id="data",
                    source=AnalysisDatasetViewSource(output_id="fit-data"),
                    projection=AnalysisFigureProjection(kind="line", x="x", y="y"),
                    preview=AnalysisFigure(
                        kind="line",
                        x_axis=AnalysisFigureAxis(label="x"),
                        y_axis=AnalysisFigureAxis(label="y"),
                        series=[AnalysisFigureSeries(id="large", x=values, y=values)],
                    ),
                    total_points=len(values),
                    truncated=False,
                ),
            ),
            total_points=len(values),
            truncated=False,
        ),
    )
    with pytest.raises(ValidationError, match="total figure point count"):
        AnalysisRecord(
            subject=RunAnalysisSubject(run_id="run-figure-budget"),
            title="large figures",
            revision=1,
            publication_hash=_PUBLICATION_HASH,
            outputs=[
                _dataset_output(),
                *(
                    figure_output.model_copy(update={"id": f"figure-{index}"})
                    for index in range(
                        MAX_ANALYSIS_TOTAL_FIGURE_POINTS // MAX_ANALYSIS_FIGURE_POINTS
                        + 1
                    )
                ),
            ],
        )

    with pytest.raises(ValidationError, match=f"at most {MAX_ANALYSIS_OUTPUTS} items"):
        AnalysisRecord(
            subject=RunAnalysisSubject(run_id="run-output-budget"),
            title="too many outputs",
            revision=1,
            publication_hash=_PUBLICATION_HASH,
            outputs=[
                table_output.model_copy(update={"id": f"output-{index}"})
                for index in range(MAX_ANALYSIS_OUTPUTS + 1)
            ],
        )


def test_analysis_record_rejects_empty_required_text_like_the_wire_contract() -> None:
    with pytest.raises(ValidationError, match="at least 1 character"):
        AnalysisRecord(
            subject=RunAnalysisSubject(run_id="run-analysis"),
            title="",
            revision=1,
            publication_hash=_PUBLICATION_HASH,
            outputs=[],
        )
    with pytest.raises(ValidationError, match="at least 1 character"):
        AnalysisRecord(
            subject=RunAnalysisSubject(run_id="run-analysis"),
            title="Analysis",
            revision=1,
            publication_hash=_PUBLICATION_HASH,
            step_id="",
            outputs=[],
        )
    with pytest.raises(ValidationError, match="at least 1 character"):
        MeasurementAnalysisRecordInput(
            id="source",
            run_id="run-analysis",
            target="",
            kind="measurement_dataset",
            content_hash=f"sha256:{'0' * 64}",
            codec="scopecat.measurement-dataset.v12",
            role="data",
        )
    with pytest.raises(ValidationError, match="at least 1 character"):
        AnalysisTableRecordOutput(
            kind="table",
            id="table",
            title="",
            content=AnalysisTableView(
                source=AnalysisDatasetViewSource(output_id="values"),
                columns=("value",),
                preview=AnalysisTable.from_rows([{"value": 1}]),
                total_rows=1,
                truncated=False,
            ),
        )


def test_analysis_record_inputs_distinguish_measurements_from_published_outputs() -> (
    None
):
    source = AnalysisPublishedOutputReference(
        subject=RunAnalysisSubject(run_id="run-analysis"),
        analysis_record_id="analysis-fit-r2",
        output_id="fits",
    )
    retained = PublishedAnalysisRecordInput(
        id="fits",
        target="analysis-fit-r2-fits",
        kind="analysis_dataset",
        content_hash=f"sha256:{'0' * 64}",
        codec="scopecat.derived-dataset.arrow-ipc.v2",
        role="data",
        source=source,
    )

    assert retained.source == source
    with pytest.raises(ValidationError, match="Field required"):
        PublishedAnalysisRecordInput.model_validate(
            {
                "id": "fits",
                "target": "analysis-fit-r2-fits",
                "kind": "analysis_dataset",
                "content_hash": f"sha256:{'0' * 64}",
                "codec": "scopecat.derived-dataset.arrow-ipc.v2",
                "role": "data",
            }
        )
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        MeasurementAnalysisRecordInput.model_validate(
            {
                "id": "measurements",
                "run_id": "run-analysis",
                "target": "raw-measurements",
                "kind": "measurement_dataset",
                "content_hash": f"sha256:{'0' * 64}",
                "codec": "scopecat.measurement-dataset.v12",
                "role": "data",
                "source": source,
            }
        )


def test_analysis_record_rejects_view_without_its_dataset_output() -> None:
    with pytest.raises(ValidationError, match="source must identify a dataset"):
        AnalysisRecord(
            subject=RunAnalysisSubject(run_id="run-analysis"),
            title="Dangling view",
            revision=1,
            publication_hash=_PUBLICATION_HASH,
            outputs=[
                AnalysisTableRecordOutput(
                    kind="table",
                    id="table",
                    title="Table",
                    content=AnalysisTableView(
                        source=AnalysisDatasetViewSource(output_id="missing"),
                        columns=("value",),
                        preview=AnalysisTable.from_rows([{"value": 1}]),
                        total_rows=1,
                        truncated=False,
                    ),
                )
            ],
        )


def test_analysis_record_rejects_unknown_output_producer() -> None:
    with pytest.raises(ValidationError, match="producer must identify an execution"):
        AnalysisRecord(
            subject=RunAnalysisSubject(run_id="run-analysis"),
            title="Dangling producer",
            revision=1,
            publication_hash=_PUBLICATION_HASH,
            outputs=[
                AnalysisFactRecordOutput(
                    kind="fact",
                    id="fact",
                    title="Fact",
                    produced_by=AnalysisExecutionOutputReference(
                        execution_id="missing",
                        output_name="result",
                    ),
                    content=AnalysisFact(
                        schema_id="scopecat.scalar.v1",
                        schema_codec=ANALYSIS_FACT_SCHEMA_CODEC,
                        schema_hash=SCALAR_FACT_SCHEMA_HASH,
                        codec="scopecat.python-json.v1",
                        value=1,
                    ),
                )
            ],
        )


def test_analysis_record_rejects_unknown_execution_output_producer() -> None:
    with pytest.raises(
        ValidationError,
        match="producer must identify an execution output",
    ):
        AnalysisRecord(
            subject=RunAnalysisSubject(run_id="run-analysis"),
            title="Dangling execution output",
            revision=1,
            publication_hash=_PUBLICATION_HASH,
            executions=[
                AnalysisExecution(
                    id="execution-1",
                    implementation="python:test.compute",
                    deterministic=True,
                    inputs=(),
                    input_bindings=(),
                    outputs=(
                        AnalysisExecutionOutput(
                            name="result",
                            kind="value",
                            content_hash=(f"sha256:{stable_content_hash(1)}"),
                            codec="scopecat.python-json.v1",
                        ),
                    ),
                )
            ],
            outputs=[
                AnalysisFactRecordOutput(
                    kind="fact",
                    id="fact",
                    title="Fact",
                    produced_by=AnalysisExecutionOutputReference(
                        execution_id="execution-1",
                        output_name="missing",
                    ),
                    content=AnalysisFact(
                        schema_id="scopecat.scalar.v1",
                        schema_codec=ANALYSIS_FACT_SCHEMA_CODEC,
                        schema_hash=SCALAR_FACT_SCHEMA_HASH,
                        codec="scopecat.python-json.v1",
                        value=1,
                    ),
                )
            ],
        )
