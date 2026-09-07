# pyright: reportUnknownArgumentType=false, reportUnknownMemberType=false
# pyright: reportUnknownVariableType=false

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Annotated, cast

import pandas as pd
import pytest
import scopecat as sc
from pydantic import ValidationError
from scopecat.analysis.datasets import DERIVED_DATASET_CODEC, DerivedDataset
from scopecat.analysis.service import (
    AnalysisDatasetOutput,
    AnalysisFigureOutput,
    AnalysisTableOutput,
    PublishedAnalysisOutputInput,
)
from scopecat.config.registry import service as config_registry_service
from scopecat.kernel.errors import CheckFailed, NotFound
from scopecat.measurements.results import Dataset
from scopecat.records.analysis import (
    ANALYSIS_ARTIFACT_CODEC,
    MAX_ANALYSIS_FIGURE_POINTS,
    MAX_ANALYSIS_TABLE_ROWS,
    AnalysisArtifactRecordOutput,
    AnalysisDatasetDerivation,
    AnalysisDatasetRecordOutput,
    AnalysisDatasetViewSource,
    AnalysisExecutionOutputReference,
    AnalysisFactRecordOutput,
    AnalysisFigureLayerSpec,
    AnalysisFigureProjection,
    AnalysisFigureRecordOutput,
    AnalysisFigureViewSpec,
    AnalysisPublishedOutputReference,
    AnalysisTableRecordOutput,
    AnalysisTableViewSpec,
    PublishedAnalysisRecordInput,
    RunAnalysisSubject,
)
from scopecat.runs.refs import record_content_ref
from scopecat_testkit.config_registry import activate_candidate_config
from scopecat_testkit.server.in_process_lab import in_process_lab
from scopecat_testkit.server.runtime import (
    sqlite_project_services,
)
from scopecat_testkit.server.signal_testkit import (
    SUMMARY_STATS_STEP,
    BestSignalAnalysisStep,
    SummaryStatsAnalysisStep,
    execute_signal_run,
)
from scopecat_testkit.workflow_fixtures import load_config, load_invocation

from scopecat_server.storage.sqlite.run_repository import (
    PreparedContentPublication,
    SQLiteRunRepository,
)


def _dataset_size(dataset: Dataset) -> int:
    return len(dataset)


def _scaled_dataset_size(*, dataset: Dataset, scale: int) -> int:
    return len(dataset) * scale


def _derived_signal_frame(dataset: Dataset) -> DerivedDataset:
    frame = dataset.project(
        {"frequency": "drive_frequency", "response": "signal"},
        identity=False,
    ).to_pandas()
    frame["score"] = frame["response"] * 2.0
    return sc.derived_dataset(
        frame[["frequency", "response", "score"]],
        fields={
            "frequency": sc.AnalysisField(role="coordinate"),
            "score": sc.AnalysisField(label="Doubled response"),
        },
    )


def _native_signal_frame(dataset: Dataset) -> pd.DataFrame:
    return dataset.project(
        {"frequency": "drive_frequency", "response": "signal"},
        identity=False,
    ).to_pandas()


def _write_fit_report(*, dataset: Dataset, destination: str) -> Path:
    path = Path(destination)
    path.write_text(
        f"# Fit report\n\nPoints: {len(dataset)}\n",
        encoding="utf-8",
        newline="\n",
    )
    return path


def _derived_score_max(dataset: DerivedDataset) -> float:
    return float(cast("float", dataset.to_pandas()["score"].max()))


@dataclass(frozen=True, slots=True)
class _PresentedObservation:
    bias: Annotated[
        sc.Quantity,
        sc.AnalysisField(id="bias_mv", label="Bias", unit="mV"),
    ]
    response: Annotated[float, sc.AnalysisField(label="Response")]


def _annotated_fit_rows(*, dataset: Dataset) -> tuple[_PresentedObservation, ...]:
    _ = dataset
    return (
        _PresentedObservation(sc.Quantity(0.1, "V"), 0.2),
        _PresentedObservation(sc.Quantity(0.2, "V"), 0.8),
    )


def _maximum_annotated_response(
    *,
    rows: tuple[_PresentedObservation, ...],
) -> float:
    return max(row.response for row in rows)


@dataclass(frozen=True, slots=True)
class _StructuredFit:
    resonance: float
    quality: float


@dataclass(frozen=True, slots=True)
class _FitConclusion:
    resonance: sc.Quantity
    quality: float


@dataclass(frozen=True, slots=True)
class _IncompatibleFitConclusion:
    resonance: sc.Quantity
    converged: bool


_FIT_CONCLUSION_SCHEMA = sc.AnalysisFactSchema(
    "tests.fit-conclusion.v1",
    _FitConclusion,
)


def _structured_fit(*, dataset: Dataset) -> _StructuredFit:
    _ = dataset
    return _StructuredFit(
        resonance=5.1,
        quality=0.98,
    )


class _DatasetTraceStep:
    id = "dataset-trace"

    def run(self, context: sc.AnalysisContext) -> sc.Analysis:
        measurements = context.measurements()
        count = context.trace(fn=_dataset_size, dataset=measurements)
        return context.result("Dataset trace").fact("points", count)


def test_workflow_analysis_review_activate_and_rerun_active_config(
    tmp_path: Path,
) -> None:
    services = sqlite_project_services(tmp_path)
    run = execute_signal_run(
        config=load_config(),
        experiment=load_invocation(),
        project_root=tmp_path,
    )
    lab = in_process_lab(tmp_path, config=load_config())
    run_handle = lab.get_run(run.run_id)

    summary = run_handle.analyze(SummaryStatsAnalysisStep())
    analysis = run_handle.analyze(BestSignalAnalysisStep())
    candidate = analysis.candidate_config()
    lab.review_parameter_proposal(run_handle, candidate.proposal_id)
    activation = activate_candidate_config(
        candidate=candidate,
        services=services,
        entry_id="candidate-best-signal",
        actor="operator",
    )
    active_config, active_source = (
        config_registry_service.resolve_config_registry_config_source(
            selector="active",
            unit_of_work=services.config_registry,
        )
    )
    next_run = execute_signal_run(
        config=active_config,
        experiment=load_invocation(),
        project_root=tmp_path,
        config_source=active_source,
    )

    assert isinstance(summary, sc.PublishedAnalysis)
    assert tuple(output.kind for output in summary.outputs) == ("dataset", "table")
    [summary_input] = run_handle.published_analysis(SUMMARY_STATS_STEP).inputs
    assert summary_input.target == "raw-measurements"
    assert summary_input.content_hash == run_handle.measurements().entry.content_hash
    assert summary_input.codec == "scopecat.measurement-dataset.v12"
    assert candidate.parameter_proposal.deltas[0].parameter_id == "drive_frequency"
    assert activation.entry.id == "candidate-best-signal"
    assert next_run.status == "completed"
    assert next_run.config_source == active_source


def test_analysis_trace_records_its_analysis_dependency(tmp_path: Path) -> None:
    run = execute_signal_run(
        config=load_config(),
        experiment=load_invocation(),
        project_root=tmp_path,
    )
    handle = in_process_lab(tmp_path, config=load_config()).get_run(run.run_id)
    analysis = handle.analyze(_DatasetTraceStep())

    [dependency] = analysis.inputs
    assert dependency.target == "raw-measurements"
    assert dependency.content_hash == handle.measurements().entry.content_hash
    assert dependency.codec == "scopecat.measurement-dataset.v12"
    assert dependency.role == "data"
    assert dependency.metadata is None
    [execution] = analysis.executions
    assert execution.id == "_dataset_size"
    assert execution.implementation == "python:_dataset_size"
    assert not execution.deterministic
    assert execution.inputs == ("dataset",)
    [execution_output] = execution.outputs
    assert execution_output.name == "_dataset_size"
    assert execution_output.kind == "value"
    assert execution.captures == ()
    [input_binding] = execution.input_bindings
    assert input_binding.name == "dataset"
    assert input_binding.target == "raw-measurements"
    assert input_binding.content_hash == handle.measurements().entry.content_hash
    assert input_binding.codec == "scopecat.measurement-dataset.v12"
    assert execution_output.content_hash.startswith("sha256:")
    [fact_output] = analysis.outputs
    assert fact_output.kind == "fact"
    stored = handle.record_json(
        "analysis-dataset-trace-r1",
        expected_kind="analysis",
    )
    stored_executions = stored.content["executions"]
    assert isinstance(stored_executions, list)
    stored_execution = stored_executions[0]
    assert isinstance(stored_execution, dict)
    assert stored_execution["id"] == "_dataset_size"


def test_analysis_trace_records_one_native_structured_result(
    tmp_path: Path,
) -> None:
    run = execute_signal_run(
        config=load_config(),
        experiment=load_invocation(),
        project_root=tmp_path,
    )
    handle = in_process_lab(tmp_path, config=load_config()).get_run(run.run_id)
    context = sc.AnalysisContext(run=handle)

    fit = context.trace(
        id="fit",
        fn=_structured_fit,
        dataset=context.measurements(),
    )
    analysis = context.result("Structured fit")

    assert fit == _StructuredFit(resonance=5.1, quality=0.98)
    [execution] = analysis.executions
    [output] = execution.outputs
    assert execution.implementation == "python:fit"
    assert execution.access == "full"
    assert output.name == "fit"
    assert output.kind == "value"
    assert output.codec == "scopecat.python-json.v1"


def test_native_dataframe_trace_returns_one_reusable_derived_dataset(
    tmp_path: Path,
) -> None:
    run = execute_signal_run(
        config=load_config(),
        experiment=load_invocation(),
        project_root=tmp_path,
    )
    handle = in_process_lab(tmp_path, config=load_config()).get_run(run.run_id)
    context = sc.AnalysisContext(run=handle)

    derived = context.trace(
        fn=_derived_signal_frame,
        dataset=context.measurements(),
    )
    maximum = context.trace(fn=_derived_score_max, dataset=derived)
    outcome = (
        context.result("Native derived data", key="native-derived-data")
        .dataset("derived-signal", derived)
        .fact("maximum-score", maximum)
        .table(
            dataset="derived-signal",
            columns=("frequency", "score"),
            title="Derived rows",
        )
        .figure(
            dataset="derived-signal",
            kind="line",
            x="frequency",
            y="score",
            title="Derived score",
        )
        .save()
    )

    assert isinstance(derived, DerivedDataset)
    assert derived.table.column_names == ["frequency", "response", "score"]
    assert maximum == 2.0
    data_output, maximum_output, table_output, figure_output = outcome.outputs
    assert isinstance(data_output, AnalysisDatasetRecordOutput)
    assert data_output.produced_by == AnalysisExecutionOutputReference(
        execution_id="_derived_signal_frame",
        output_name="_derived_signal_frame",
    )
    dataset_id = "analysis-native-derived-data-r1-derived-signal"
    assert isinstance(maximum_output, AnalysisFactRecordOutput)
    assert maximum_output.content.value == 2.0
    assert maximum_output.produced_by == AnalysisExecutionOutputReference(
        execution_id="_derived_score_max",
        output_name="_derived_score_max",
    )
    first_execution, execution = outcome.executions
    assert first_execution.id == "_derived_signal_frame"
    [derived_input] = execution.input_bindings
    assert derived_input.kind == "derived_dataset"
    assert derived_input.target == (
        "execution:_derived_signal_frame:_derived_signal_frame"
    )
    assert derived_input.codec == DERIVED_DATASET_CODEC
    assert derived_input.value is None
    assert isinstance(table_output, AnalysisTableRecordOutput)
    assert table_output.content.source is not None
    assert table_output.content.source.output_id == "derived-signal"
    assert table_output.content.columns == ("frequency", "score")
    assert isinstance(figure_output, AnalysisFigureRecordOutput)
    assert isinstance(figure_output.content.layers[0].source, AnalysisDatasetViewSource)
    assert figure_output.content.layers[0].source.output_id == "derived-signal"
    assert figure_output.content.layers[0].projection is not None
    assert figure_output.content.layers[0].projection.x == "frequency"
    assert figure_output.content.layers[0].projection.y == "score"

    stored = handle.record_json(
        "analysis-native-derived-data-r1",
        expected_kind="analysis",
    )
    outputs = stored.content["outputs"]
    assert isinstance(outputs, list)
    persisted = outputs[0]
    assert isinstance(persisted, dict)
    content = persisted["content"]
    assert isinstance(content, dict)
    assert content["codec"] == DERIVED_DATASET_CODEC
    assert persisted["id"] == "derived-signal"
    assert persisted["produced_by"] == {
        "execution_id": "_derived_signal_frame",
        "output_name": "_derived_signal_frame",
    }
    assert content["dataset_id"] == dataset_id
    assert "value" not in content
    assert "arrow_ipc_base64" not in str(stored.content)
    stored_table = cast("dict[str, object]", outputs[2])
    table_view = cast("dict[str, object]", stored_table["content"])
    assert table_view["source"] == {
        "kind": "dataset",
        "output_id": "derived-signal",
    }
    assert table_view["columns"] == ["frequency", "score"]
    assert "preview" in table_view
    published = handle.published_analysis("native-derived-data")
    assert outcome.id == published.id
    assert published.id == "analysis-native-derived-data-r1"
    assert published.dataset("derived-signal").table.equals(
        derived.table,
        check_metadata=True,
    )
    assert published.fact("maximum-score").value == 2.0
    assert [execution.id for execution in published.executions] == [
        "_derived_signal_frame",
        "_derived_score_max",
    ]
    assert published.table("table").source is not None
    assert published.figure("figure").layers[0].projection is not None
    catalog_entry = handle.content("dataset", dataset_id)
    assert catalog_entry.kind == "analysis_dataset"
    assert catalog_entry.content_hash == content["content_hash"]


def test_analysis_trace_retains_native_dataframe_identity_until_publication(
    tmp_path: Path,
) -> None:
    run = execute_signal_run(
        config=load_config(),
        experiment=load_invocation(),
        project_root=tmp_path,
    )
    handle = in_process_lab(tmp_path, config=load_config()).get_run(run.run_id)
    context = sc.AnalysisContext(run=handle)

    frame = context.trace(
        fn=_native_signal_frame,
        dataset=context.measurements(),
    )
    outcome = (
        context.result("Native frame", key="native-frame")
        .dataset(
            "fits",
            frame,
        )
        .save()
    )

    [execution] = outcome.executions
    [execution_output] = execution.outputs
    assert execution_output.kind == "derived_dataset"
    assert execution_output.codec == DERIVED_DATASET_CODEC
    [output] = outcome.outputs
    assert isinstance(output, AnalysisDatasetRecordOutput)
    assert output.produced_by == AnalysisExecutionOutputReference(
        execution_id="_native_signal_frame",
        output_name="_native_signal_frame",
    )
    assert (
        handle.published_analysis("native-frame")
        .dataset("fits")
        .table.equals(
            sc.derived_dataset(frame).table,
            check_metadata=True,
        )
    )


def test_analysis_dataset_records_first_party_normalization_from_trace(
    tmp_path: Path,
) -> None:
    run = execute_signal_run(
        config=load_config(),
        experiment=load_invocation(),
        project_root=tmp_path,
    )
    handle = in_process_lab(tmp_path, config=load_config()).get_run(run.run_id)
    context = sc.AnalysisContext(run=handle)

    frame = context.trace(
        id="fit-frame",
        fn=_native_signal_frame,
        dataset=context.measurements(),
    )
    outcome = (
        context.result("Mapped frame", key="mapped-frame")
        .dataset(
            "fits",
            frame,
            fields={
                "frequency": sc.AnalysisField(
                    id="drive_frequency",
                    role="coordinate",
                    label="Drive frequency",
                ),
                "response": sc.AnalysisField(id="signal", label="Signal"),
            },
        )
        .save()
    )

    [output] = outcome.outputs
    assert isinstance(output, AnalysisDatasetRecordOutput)
    assert output.produced_by is None
    assert output.derived_from == AnalysisDatasetDerivation(
        source=AnalysisExecutionOutputReference(
            execution_id="fit-frame",
            output_name="fit-frame",
        ),
        source_kind="pandas",
        fields={
            "frequency": sc.AnalysisField(
                id="drive_frequency",
                role="coordinate",
                label="Drive frequency",
            ),
            "response": sc.AnalysisField(id="signal", label="Signal"),
        },
    )
    restored = outcome.dataset("fits")
    assert restored.table.column_names == ["drive_frequency", "signal"]
    assert [field.source_name for field in restored.schema.fields] == [
        "frequency",
        "response",
    ]


def test_annotated_rows_are_traceable_datasets_without_a_dataframe(
    tmp_path: Path,
) -> None:
    run = execute_signal_run(
        config=load_config(),
        experiment=load_invocation(),
        project_root=tmp_path,
    )
    handle = in_process_lab(tmp_path, config=load_config()).get_run(run.run_id)
    context = sc.AnalysisContext(run=handle)

    rows = context.trace(
        id="fit-rows",
        fn=_annotated_fit_rows,
        dataset=context.measurements(),
    )
    maximum = context.trace(
        id="maximum-response",
        fn=_maximum_annotated_response,
        rows=rows,
    )
    published = (
        context.result("Annotated fits", key="annotated-fits")
        .dataset("fits", rows)
        .fact("maximum-response", maximum)
        .save()
    )

    fit_execution, maximum_execution = published.executions
    [fit_execution_output] = fit_execution.outputs
    assert fit_execution_output.kind == "derived_dataset"
    [rows_input] = maximum_execution.input_bindings
    assert rows_input.kind == "derived_dataset"
    assert rows_input.target == "execution:fit-rows:fit-rows"
    fits_output = published.output("fits")
    assert isinstance(fits_output, AnalysisDatasetRecordOutput)
    assert fits_output.produced_by == AnalysisExecutionOutputReference(
        execution_id="fit-rows",
        output_name="fit-rows",
    )
    assert fits_output.derived_from is None
    assert published.dataset("fits").table.to_pylist() == [
        {"bias_mv": 100.0, "response": 0.2},
        {"bias_mv": 200.0, "response": 0.8},
    ]


def test_analysis_dataset_publishes_a_native_frame_once(tmp_path: Path) -> None:
    run = execute_signal_run(
        config=load_config(),
        experiment=load_invocation(),
        project_root=tmp_path,
    )
    handle = in_process_lab(tmp_path, config=load_config()).get_run(run.run_id)
    frame = (
        handle.measurements()
        .project(
            {"frequency": "drive_frequency", "response": "signal"},
            identity=False,
        )
        .to_pandas()
    )
    frame["score"] = frame["response"] * 2.0

    outcome = (
        handle.analysis("Native fits", key="native-fits")
        .result()
        .dataset(
            "fits",
            frame[["frequency", "score"]],
            fields={"score": sc.AnalysisField(label="Fit score")},
        )
        .save()
    )

    [output] = outcome.outputs
    assert isinstance(output, AnalysisDatasetRecordOutput)
    restored = handle.published_analysis("native-fits").dataset("fits")
    assert restored.table.to_pylist() == [
        {"frequency": frequency, "score": score}
        for frequency, score in zip(
            frame["frequency"],
            frame["score"],
            strict=True,
        )
    ]
    assert restored.schema.fields[0].role == "coordinate"
    assert restored.schema.fields[0].unit == "GHz"
    assert restored.schema.fields[1].label == "Fit score"


def test_analysis_artifact_links_exact_traced_file_bytes(tmp_path: Path) -> None:
    run = execute_signal_run(
        config=load_config(),
        experiment=load_invocation(),
        project_root=tmp_path,
    )
    handle = in_process_lab(tmp_path, config=load_config()).get_run(run.run_id)
    context = sc.AnalysisContext(run=handle)

    report_path = context.trace(
        id="write-report",
        fn=_write_fit_report,
        dataset=context.measurements(),
        destination=str(tmp_path / "fit-report.md"),
    )
    outcome = (
        context.result("Fit report", key="fit-report")
        .artifact("report", path=report_path, media_type="text/markdown")
        .save()
    )

    [execution] = outcome.executions
    [execution_output] = execution.outputs
    assert execution_output.kind == "artifact"
    assert execution_output.codec == ANALYSIS_ARTIFACT_CODEC
    [output] = outcome.outputs
    assert isinstance(output, AnalysisArtifactRecordOutput)
    assert output.produced_by == AnalysisExecutionOutputReference(
        execution_id="write-report",
        output_name="write-report",
    )
    report = outcome.artifact("report")
    assert report.bytes() == b"# Fit report\n\nPoints: 3\n"
    assert report.text() == "# Fit report\n\nPoints: 3\n"


def test_analysis_publishes_typed_facts_and_owned_artifacts(tmp_path: Path) -> None:
    run = execute_signal_run(
        config=load_config(),
        experiment=load_invocation(),
        project_root=tmp_path,
    )
    handle = in_process_lab(tmp_path, config=load_config()).get_run(run.run_id)

    outcome = (
        handle.analysis("Publication", key="publication")
        .result()
        .fact(
            "resonance",
            sc.Quantity(5.1, "GHz"),
            title="Fitted resonance",
        )
        .artifact(
            "fit-report",
            text="# Fit report\n\nConverged.\n",
            filename="fit-report.md",
            media_type="text/markdown",
        )
        .save()
    )

    fact, artifact = outcome.outputs
    assert isinstance(fact, AnalysisFactRecordOutput)
    assert fact.id == "resonance"
    assert fact.content.schema_id == "scopecat.quantity.v1"
    assert fact.content.value == {"value": 5.1, "unit": "GHz"}
    assert artifact.id == "fit-report"
    stored = handle.record_json("analysis-publication-r1", expected_kind="analysis")
    stored_outputs = cast("list[object]", stored.content["outputs"])
    artifact_output = cast("dict[str, object]", stored_outputs[1])
    assert artifact_output["id"] == "fit-report"
    assert artifact_output["kind"] == "artifact"
    artifact_ref = cast("dict[str, object]", artifact_output["content"])
    artifact_id = "analysis-publication-r1-fit-report"
    assert artifact_ref["artifact_id"] == artifact_id
    restored = handle.artifact_text(
        artifact_id,
        expected_kind="analysis_artifact",
    )
    assert restored.content == "# Fit report\n\nConverged.\n"
    entry = handle.content("artifact", artifact_id)
    assert entry.filename == "fit-report.md"
    assert entry.produced_by == "analysis-publication-r1"
    published = handle.published_analysis("publication")
    assert published.fact("resonance").value == {"value": 5.1, "unit": "GHz"}
    assert published.artifact("fit-report").text() == ("# Fit report\n\nConverged.\n")
    assert published.artifact("fit-report").entry == entry
    assert handle.analysis_summaries().items[0].entry.id == "analysis-publication-r1"
    with pytest.raises(TypeError, match="is fact"):
        published.dataset("resonance")


def test_analysis_validates_and_reconstructs_structured_facts(tmp_path: Path) -> None:
    run = execute_signal_run(
        config=load_config(),
        experiment=load_invocation(),
        project_root=tmp_path,
    )
    handle = in_process_lab(tmp_path, config=load_config()).get_run(run.run_id)
    fit = _FitConclusion(
        resonance=sc.Quantity(5.1, "GHz"),
        quality=0.98,
    )

    published = (
        handle.analysis("Structured fact", key="structured-fact")
        .result()
        .fact("fit", fit, schema=_FIT_CONCLUSION_SCHEMA)
        .save()
    )

    stored = published.fact("fit")
    assert stored.schema_id == "tests.fit-conclusion.v1"
    assert stored.schema_codec == "scopecat.analysis-fact-schema.v1"
    assert stored.schema_hash == _FIT_CONCLUSION_SCHEMA.schema_hash
    assert stored.value == {
        "resonance": {"value": 5.1, "unit": "GHz"},
        "quality": 0.98,
    }
    assert published.fact_as("fit", _FIT_CONCLUSION_SCHEMA) == fit

    incompatible_schema = sc.AnalysisFactSchema(
        "tests.fit-conclusion.v1",
        _IncompatibleFitConclusion,
    )
    with pytest.raises(TypeError, match="fingerprint does not match"):
        published.fact_as("fit", incompatible_schema)

    with pytest.raises(TypeError, match="require an AnalysisFactSchema"):
        handle.analysis("Invalid").result().fact("fit", fit)


def test_analysis_key_appends_only_changed_publication_revisions(
    tmp_path: Path,
) -> None:
    run = execute_signal_run(
        config=load_config(),
        experiment=load_invocation(),
        project_root=tmp_path,
    )
    handle = in_process_lab(tmp_path, config=load_config()).get_run(run.run_id)

    first = (
        handle.analysis("Fit result", key="fit-result")
        .result()
        .fact("score", 0.5)
        .artifact("report", text="first")
        .save()
    )
    retried = (
        handle.analysis("Fit result", key="fit-result")
        .result()
        .fact("score", 0.5)
        .artifact("report", text="first")
        .save()
    )
    second = (
        handle.analysis("Fit result", key="fit-result")
        .result()
        .fact("score", 0.75)
        .artifact("report", text="second")
        .save()
    )

    assert first.id == retried.id == "analysis-fit-result-r1"
    assert second.id == "analysis-fit-result-r2"
    assert retried.published_at == first.published_at
    assert second.published_at >= first.published_at
    first_publication = handle.published_analysis("analysis-fit-result-r1")
    latest = handle.published_analysis("fit-result")
    assert first_publication.revision == 1
    assert latest.revision == 2
    assert first_publication.published_at == first.published_at
    assert first_publication.publication_hash != latest.publication_hash
    assert first_publication.fact("score").value == 0.5
    assert latest.fact("score").value == 0.75
    assert first_publication.artifact("report").text() == "first"
    assert latest.artifact("report").text() == "second"
    assert [
        item.id for item in handle.contents(role="record", kind="analysis").items
    ] == ["analysis-fit-result-r2", "analysis-fit-result-r1"]


def test_analysis_revision_ids_do_not_collide_with_revision_shaped_keys(
    tmp_path: Path,
) -> None:
    run = execute_signal_run(
        config=load_config(),
        experiment=load_invocation(),
        project_root=tmp_path,
    )
    handle = in_process_lab(tmp_path, config=load_config()).get_run(run.run_id)

    first = handle.analysis("History", key="history").result().fact("score", 0.5).save()
    second = (
        handle.analysis("History", key="history").result().fact("score", 0.75).save()
    )
    shaped_key = (
        handle.analysis("Separate history", key="history-r2")
        .result()
        .fact("score", 1.0)
        .save()
    )

    assert first.id == "analysis-history-r1"
    assert second.id == "analysis-history-r2"
    assert shaped_key.id == "analysis-history-r2-r1"
    assert handle.published_analysis("history").id == second.id
    assert handle.published_analysis("history-r2").id == shaped_key.id


def test_analysis_dataset_input_freezes_the_exact_same_run_output_revision(
    tmp_path: Path,
) -> None:
    run = execute_signal_run(
        config=load_config(),
        experiment=load_invocation(),
        project_root=tmp_path,
    )
    handle = in_process_lab(tmp_path, config=load_config()).get_run(run.run_id)
    source_dataset = _derived_signal_frame(handle.measurements())

    source_v1 = (
        handle.analysis("Source fit", key="source-fit")
        .result()
        .dataset("fits", source_dataset, metadata={"review": 1})
        .save()
    )
    first_context = sc.AnalysisContext(run=handle)
    first_fits = first_context.analysis_dataset(
        "source-fit",
        "fits",
        metadata={"purpose": "quality review"},
    )
    first_score = first_context.trace(
        id="review-score",
        fn=_derived_score_max,
        dataset=first_fits,
    )
    review_v1 = (
        first_context.result("Fit quality review", key="fit-quality-review")
        .fact("maximum-score", first_score)
        .save()
    )

    [first_input] = review_v1.inputs
    assert isinstance(first_input, PublishedAnalysisRecordInput)
    source_v1_output = source_v1.output("fits")
    assert isinstance(source_v1_output, AnalysisDatasetRecordOutput)
    assert first_input.kind == "analysis_dataset"
    assert first_input.target == source_v1_output.content.dataset_id
    assert first_input.content_hash == source_v1_output.content.content_hash
    assert first_input.codec == DERIVED_DATASET_CODEC
    assert first_input.source == AnalysisPublishedOutputReference(
        subject=RunAnalysisSubject(run_id=handle.id),
        analysis_record_id=source_v1.id,
        output_id="fits",
    )
    assert first_input.metadata == {"purpose": "quality review"}
    [first_execution] = review_v1.executions
    [first_binding] = first_execution.input_bindings
    assert first_binding.kind == "derived_dataset"
    assert first_binding.target == source_v1_output.content.dataset_id

    source_v2 = (
        handle.analysis("Source fit", key="source-fit")
        .result()
        .dataset("fits", source_dataset, metadata={"review": 2})
        .save()
    )
    second_context = sc.AnalysisContext(run=handle)
    second_fits = second_context.analysis_dataset("source-fit", "fits")
    second_score = second_context.trace(
        id="review-score",
        fn=_derived_score_max,
        dataset=second_fits,
    )
    review_v2 = (
        second_context.result("Fit quality review", key="fit-quality-review")
        .fact("maximum-score", second_score)
        .save()
    )

    assert source_v2.id == "analysis-source-fit-r2"
    source_v2_output = source_v2.output("fits")
    assert isinstance(source_v2_output, AnalysisDatasetRecordOutput)
    assert (
        source_v1_output.content.content_hash == source_v2_output.content.content_hash
    )
    assert review_v2.id == "analysis-fit-quality-review-r2"
    assert review_v2.fact("maximum-score").value == first_score
    [second_input] = review_v2.inputs
    assert isinstance(second_input, PublishedAnalysisRecordInput)
    assert second_input.source == AnalysisPublishedOutputReference(
        subject=RunAnalysisSubject(run_id=handle.id),
        analysis_record_id=source_v2.id,
        output_id="fits",
    )
    assert second_input.target != first_input.target
    assert review_v2.publication_hash != review_v1.publication_hash


def test_analysis_dataset_input_rejects_a_source_from_another_run(
    tmp_path: Path,
) -> None:
    first_run = execute_signal_run(
        config=load_config(),
        experiment=load_invocation(),
        project_root=tmp_path,
    )
    lab = in_process_lab(tmp_path, config=load_config())
    first_handle = lab.get_run(first_run.run_id)
    source = (
        first_handle.analysis("Source fit", key="source-fit")
        .result()
        .dataset("fits", _derived_signal_frame(first_handle.measurements()))
        .save()
    )
    source_output = source.output("fits")
    assert isinstance(source_output, AnalysisDatasetRecordOutput)
    second_run = execute_signal_run(
        config=load_config(),
        experiment=load_invocation(),
        project_root=tmp_path,
    )
    second_handle = lab.get_run(second_run.run_id)
    invalid = replace(
        second_handle.analysis("Invalid consumer", key="invalid-consumer").result(),
        inputs=(
            PublishedAnalysisOutputInput(
                id=source_output.content.dataset_id,
                target=source_output.content.dataset_id,
                kind="analysis_dataset",
                content_hash=source_output.content.content_hash,
                codec=source_output.content.codec,
                role="data",
                source=AnalysisPublishedOutputReference(
                    subject=RunAnalysisSubject(run_id=first_handle.id),
                    analysis_record_id=source.id,
                    output_id="fits",
                ),
            ),
        ),
    ).fact("score", 1.0)

    with pytest.raises(CheckFailed) as rejected:
        invalid.save()

    assert rejected.value.problems[0].code == "analysis_input_source_unknown"


def test_analysis_revision_owns_its_parameter_proposal_identity(tmp_path: Path) -> None:
    run = execute_signal_run(
        config=load_config(),
        experiment=load_invocation(),
        project_root=tmp_path,
    )
    handle = in_process_lab(tmp_path, config=load_config()).get_run(run.run_id)
    update = sc.replace_scalar_parameter(
        "drive_frequency",
        sc.Quantity(5.4, "GHz"),
    )

    first = (
        handle.analysis("Fit", key="fit")
        .result()
        .fact("fit-quality", 0.8)
        .propose(
            "drive-frequency",
            update,
            reason="initial fit",
            evidence=("fit-quality",),
        )
        .save()
    )
    second = (
        handle.analysis("Fit", key="fit")
        .result()
        .fact("fit-quality", 0.9)
        .propose(
            "drive-frequency",
            update,
            reason="reviewed fit",
            evidence=("fit-quality",),
        )
        .save()
    )
    retried = (
        handle.analysis("Fit", key="fit")
        .result()
        .fact("fit-quality", 0.9)
        .propose(
            "drive-frequency",
            update,
            reason="reviewed fit",
            evidence=("fit-quality",),
        )
        .save()
    )

    [first_proposal] = first.parameter_proposals
    [second_proposal] = second.parameter_proposals
    [retried_proposal] = retried.parameter_proposals
    assert first_proposal.id == "drive-frequency"
    assert first_proposal.analysis_record_id == "analysis-fit-r1"
    assert first_proposal.evidence_output_ids == ("fit-quality",)
    assert second_proposal.id == "drive-frequency-r2"
    assert second_proposal.analysis_record_id == "analysis-fit-r2"
    assert retried_proposal == second_proposal
    assert retried.id == second.id
    assert [
        item.id
        for item in handle.contents(
            role="record",
            kind="parameter_change_proposal",
        ).items
    ] == ["drive-frequency-r2", "drive-frequency"]


def test_analysis_proposal_rejects_unknown_or_view_evidence(tmp_path: Path) -> None:
    run = execute_signal_run(
        config=load_config(),
        experiment=load_invocation(),
        project_root=tmp_path,
    )
    handle = in_process_lab(tmp_path, config=load_config()).get_run(run.run_id)
    update = sc.replace_scalar_parameter(
        "drive_frequency",
        sc.Quantity(5.4, "GHz"),
    )

    with pytest.raises(CheckFailed) as unknown:
        handle.analysis("Fit").result().propose(
            "drive-frequency",
            update,
            evidence=("missing",),
        )
    assert unknown.value.problems[0].code == (
        "analysis_parameter_proposal_evidence_unknown"
    )

    analysis = (
        handle.analysis("Fit")
        .result()
        .dataset("fit-data", pd.DataFrame({"score": [0.9]}))
        .table(dataset="fit-data", id="fit-table")
    )
    with pytest.raises(CheckFailed) as view:
        analysis.propose(
            "drive-frequency",
            update,
            evidence=("fit-table",),
        )
    assert view.value.problems[0].code == (
        "analysis_parameter_proposal_evidence_not_authoritative"
    )


def test_analysis_save_rejects_views_with_unknown_dataset_fields(
    tmp_path: Path,
) -> None:
    run = execute_signal_run(
        config=load_config(),
        experiment=load_invocation(),
        project_root=tmp_path,
    )
    analysis = (
        in_process_lab(tmp_path, config=load_config())
        .get_run(run.run_id)
        .analysis("Forged views")
        .result()
        .dataset("values", pd.DataFrame({"value": [1.0]}))
    )
    source = AnalysisDatasetViewSource(output_id="values")
    forged_outputs = (
        AnalysisTableOutput(
            kind="table",
            id="forged-table",
            title="forged table",
            content=AnalysisTableViewSpec(
                source=source,
                columns=("missing",),
            ),
            metadata={},
        ),
        AnalysisFigureOutput(
            kind="figure",
            id="forged-figure",
            title="forged figure",
            content=AnalysisFigureViewSpec(
                layers=(
                    AnalysisFigureLayerSpec(
                        id="data",
                        source=source,
                        projection=AnalysisFigureProjection(
                            kind="line", x="value", y="missing"
                        ),
                    ),
                )
            ),
            metadata={},
        ),
    )

    for forged_output in forged_outputs:
        with pytest.raises(CheckFailed) as rejected:
            replace(
                analysis,
                outputs=(*analysis.outputs, forged_output),
            ).save()
        assert rejected.value.problems[0].code == ("analysis_view_projection_unknown")


def test_analysis_trace_records_named_inline_inputs(tmp_path: Path) -> None:
    run = execute_signal_run(
        config=load_config(),
        experiment=load_invocation(),
        project_root=tmp_path,
    )
    handle = in_process_lab(tmp_path, config=load_config()).get_run(run.run_id)
    context = sc.AnalysisContext(run=handle)

    count = context.trace(
        fn=_scaled_dataset_size,
        dataset=context.measurements(),
        scale=2,
    )

    assert count == 6
    analysis = context.result()
    assert analysis.outputs == ()
    [execution] = analysis.executions
    assert execution.inputs == ("dataset", "scale")
    dataset_input, scale_input = execution.input_bindings
    assert dataset_input.kind == "measurement_dataset"
    assert scale_input.kind == "value"
    assert scale_input.codec == "scopecat.python-json.v1"
    assert scale_input.value == 2


def test_analysis_save_rolls_back_refs_after_publication_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    services = sqlite_project_services(tmp_path)
    run = execute_signal_run(
        config=load_config(),
        experiment=load_invocation(),
        project_root=tmp_path,
    )
    handle = in_process_lab(tmp_path, config=load_config()).get_run(run.run_id)
    analysis = SummaryStatsAnalysisStep().run(
        sc.AnalysisContext(run=handle),
    )
    analysis_record_id = "analysis-summary-stats-r1"
    original_publish = SQLiteRunRepository.publish_prepared_content_in_transaction
    failed = False

    def fail_first_analysis_publication(
        storage: SQLiteRunRepository,
        connection: sqlite3.Connection,
        prepared: PreparedContentPublication,
    ) -> None:
        nonlocal failed
        original_publish(storage, connection, prepared)
        if not failed and any(
            record.id == analysis_record_id for record in prepared.publication.entries
        ):
            failed = True
            raise OSError("injected analysis publication failure")

    monkeypatch.setattr(
        SQLiteRunRepository,
        "publish_prepared_content_in_transaction",
        fail_first_analysis_publication,
    )

    with pytest.raises(OSError, match="injected analysis publication failure"):
        analysis.save()

    storage = services.runs
    analysis_ref = record_content_ref(
        record_id=analysis_record_id,
        kind="analysis",
    )
    assert not storage.exists(run.run_id, analysis_ref)
    with pytest.raises(NotFound):
        storage.read_content(
            run.run_id,
            role="record",
            content_id=analysis_record_id,
        )

    saved = analysis.save()

    assert (
        storage.read_content(
            run.run_id,
            role="record",
            content_id=analysis_record_id,
        ).id
        == analysis_record_id
    )
    assert saved.id == analysis_record_id


def test_local_analysis_rejects_metadata_outside_the_remote_json_contract(
    tmp_path: Path,
) -> None:
    run = execute_signal_run(
        config=load_config(),
        experiment=load_invocation(),
        project_root=tmp_path,
    )
    analysis = (
        in_process_lab(tmp_path, config=load_config())
        .get_run(run.run_id)
        .analysis("Strict metadata")
        .result()
        .dataset("values", pd.DataFrame({"value": [1]}))
        .table(
            dataset="values",
            metadata={"opaque": object()},
        )
    )

    with pytest.raises(ValidationError):
        analysis.save()


def test_analysis_facade_projects_annotated_results_directly(tmp_path: Path) -> None:
    run = execute_signal_run(
        config=load_config(),
        experiment=load_invocation(),
        project_root=tmp_path,
    )
    observations = (
        _PresentedObservation(sc.Quantity(0.1, "V"), 0.2),
        _PresentedObservation(sc.Quantity(0.2, "V"), 0.4),
    )

    analysis = (
        in_process_lab(tmp_path, config=load_config())
        .get_run(run.run_id)
        .analysis("Direct presentation")
        .result()
        .dataset("observations", observations)
        .table(dataset="observations")
        .figure(
            dataset="observations",
            kind="line",
            x="bias_mv",
            y="response",
        )
    )

    dataset_output, table_output, figure_output = analysis.outputs
    assert isinstance(dataset_output, AnalysisDatasetOutput)
    assert isinstance(table_output, AnalysisTableOutput)
    assert isinstance(figure_output, AnalysisFigureOutput)
    assert table_output.content.source.output_id == "observations"
    assert figure_output.content.layers[0].source == AnalysisDatasetViewSource(
        output_id="observations"
    )

    published = analysis.save()
    table_view = published.table("table")
    figure_view = published.figure("figure")
    assert [row.cells for row in table_view.preview.rows] == [
        [100.0, 0.2],
        [200.0, 0.4],
    ]
    assert table_view.total_rows == 2
    assert not table_view.truncated
    assert figure_view.layers[0].preview.series[0].x == [100.0, 200.0]
    assert figure_view.total_points == 2
    assert not figure_view.truncated


def test_analysis_publication_generates_bounded_preview_counts(tmp_path: Path) -> None:
    run = execute_signal_run(
        config=load_config(),
        experiment=load_invocation(),
        project_root=tmp_path,
    )
    row_count = MAX_ANALYSIS_FIGURE_POINTS + 1
    published = (
        in_process_lab(tmp_path, config=load_config())
        .get_run(run.run_id)
        .analysis("Bounded presentation")
        .result()
        .dataset(
            "values",
            pd.DataFrame(
                {
                    "x": range(row_count),
                    "y": range(row_count),
                }
            ),
        )
        .table(dataset="values")
        .figure(dataset="values", kind="line", x="x", y="y")
        .save()
    )

    table = published.table("table")
    assert len(table.preview.rows) == MAX_ANALYSIS_TABLE_ROWS
    assert table.total_rows == row_count
    assert table.truncated

    figure = published.figure("figure")
    assert len(figure.layers[0].preview.series[0].x) == MAX_ANALYSIS_FIGURE_POINTS
    assert figure.total_points == row_count
    assert figure.truncated


def test_layered_figure_freezes_published_sources_without_copying_datasets(
    tmp_path: Path,
) -> None:
    from scopecat.records.analysis import (
        AnalysisPublishedDatasetViewSource,
        AnalysisUncertaintyProjection,
    )

    run = execute_signal_run(
        config=load_config(), experiment=load_invocation(), project_root=tmp_path
    )
    lab = in_process_lab(tmp_path, config=load_config())
    handle = lab.get_run(run.run_id)
    measured = (
        handle.analysis("Measured", key="measured")
        .result()
        .dataset(
            "data",
            pd.DataFrame({"x": range(10_000), "y": range(10_000)}),
            fields={"x": sc.AnalysisField(unit="us"), "y": sc.AnalysisField(unit="V")},
        )
        .figure(dataset="data", kind="scatter", x="x", y="y")
        .save()
    )
    fit = (
        handle.analysis("Fit", key="fit")
        .result()
        .dataset(
            "curve",
            pd.DataFrame(
                {
                    "x": [0.0, 1000.0],
                    "y": [0.0, 1000.0],
                    "lo": [-10.0, 990.0],
                    "hi": [10.0, 1010.0],
                }
            ),
            fields={
                "x": sc.AnalysisField(unit="ns"),
                "y": sc.AnalysisField(unit="mV"),
                "lo": sc.AnalysisField(unit="mV"),
                "hi": sc.AnalysisField(unit="mV"),
            },
        )
        .save()
    )
    result = (
        handle.analysis("Layered review", key="layered-review")
        .result()
        .figure_layers(
            layers=(
                AnalysisFigureLayerSpec(
                    id="measured",
                    source=measured.dataset_view_source("data"),
                    projection=AnalysisFigureProjection(kind="scatter", x="x", y="y"),
                ),
                AnalysisFigureLayerSpec(
                    id="fit",
                    source=fit.dataset_view_source("curve"),
                    projection=AnalysisFigureProjection(
                        kind="line",
                        x="x",
                        y="y",
                        uncertainty=AnalysisUncertaintyProjection(
                            lower="lo",
                            upper="hi",
                            meaning="Declared fixture bounds",
                            style="bars",
                        ),
                    ),
                ),
            )
        )
        .save()
    )
    assert len(result.inputs) == 2
    assert [output.kind for output in result.outputs] == ["figure"]
    view = result.figure("figure")
    assert view.total_points == 10_002
    assert [
        sum(len(series.x) for series in layer.preview.series) for layer in view.layers
    ] == [2048, 2]
    assert view.layers[1].preview.series[0].x == [0.0, 1.0]
    assert view.layers[1].preview.series[0].y_upper == [0.01, 1.01]
    original_source = view.layers[0].source
    assert isinstance(original_source, AnalysisPublishedDatasetViewSource)
    assert original_source.source.analysis_record_id == measured.id
    handle.analysis("Changed measured", key="measured").result().dataset(
        "data", pd.DataFrame({"x": [0.0], "y": [999.0]})
    ).save()
    reopened = handle.published_analysis(result.id).figure("figure")
    assert reopened == view
    assert reopened.layers[0].preview.series[0].y[:2] == [0.0, 1.0]
