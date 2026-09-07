# pyright: reportUnknownMemberType=false, reportUnknownParameterType=false
# pyright: reportUnknownVariableType=false
"""Run facade handles for notebook workflows."""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal, Protocol, overload

import pyarrow as pa
from pydantic import JsonValue

from scopecat.analysis.datasets import DerivedDataset, DerivedDatasetSchema
from scopecat.analysis.service import (
    AnalysisInput,
    AnalysisOutput,
    SavedAnalysis,
)
from scopecat.api.analysis import (
    AnalysisContext,
    AnalysisStep,
)
from scopecat.api.published_analysis import PublishedAnalysis
from scopecat.control.models import RunExecutionSegmentPage
from scopecat.daemon.views import (
    MeasurementArrowColumn,
    MeasurementArrowQuery,
    RunAnalysisPage,
    RunAnalysisView,
    RunContentPage,
)
from scopecat.daemon.wire import (
    RunDomainJobStatePage,
    RunDomainJobTransitionPage,
)
from scopecat.measurements.dataset import (
    Dataset,
    ExperimentResultView,
    StoredExperimentResultView,
)
from scopecat.measurements.datasets import (
    MAX_MEASUREMENT_PAGE_SIZE,
    MEASUREMENT_DATASET_KIND,
    RAW_MEASUREMENTS_DATASET_ID,
)
from scopecat.measurements.interop import ProjectionSchema
from scopecat.records.analysis import AnalysisExecution
from scopecat.records.config import ConfigProfileSnapshot
from scopecat.records.content import ContentEntry
from scopecat.records.measurement import MeasurementDatasetSchema
from scopecat.records.parameter_change import ParameterChangeProposal
from scopecat.records.run import RunSnapshot
from scopecat.records.run_request import RunRequest
from scopecat.records.sample import SampleBinding
from scopecat.runs.data import (
    RunArtifactBytesResult,
    RunArtifactJsonResult,
    RunArtifactTextResult,
    RunDatasetBytesResult,
    RunMeasurementDatasetResult,
    RunRecordJsonResult,
)
from scopecat.runs.selectors import RunSelector, selected_run_id


class RunOperations(Protocol):
    """Storage-neutral operations used by run and data handles."""

    def load_snapshot(self, run_id: str) -> RunSnapshot: ...

    def load_config(self, run_id: str) -> ConfigProfileSnapshot: ...

    def load_request(self, run_id: str) -> RunRequest: ...

    def execution_segments(
        self,
        run_id: str,
        *,
        limit: int,
        before: int | None,
    ) -> RunExecutionSegmentPage: ...

    def domain_jobs(
        self,
        run_id: str,
        *,
        limit: int,
        before: int | None,
    ) -> RunDomainJobStatePage: ...

    def domain_job_transitions(
        self,
        run_id: str,
        *,
        limit: int,
        before: int | None,
    ) -> RunDomainJobTransitionPage: ...

    def contents(
        self,
        run_id: str,
        *,
        limit: int,
        before: int | None,
        role: Literal["artifact", "dataset", "record"] | None,
        kind: str | None,
    ) -> RunContentPage: ...

    def content_entry(
        self,
        run_id: str,
        *,
        role: Literal["artifact", "dataset", "record"],
        content_id: str,
    ) -> ContentEntry: ...

    def load_measurement_dataset(
        self,
        run_id: str,
        *,
        selector: str,
    ) -> RunMeasurementDatasetResult: ...

    def load_dataset_bytes(
        self,
        run_id: str,
        selector: str,
        *,
        expected_kind: str | None,
    ) -> RunDatasetBytesResult: ...

    def load_measurement_arrow_page(
        self,
        run_id: str,
        *,
        query: MeasurementArrowQuery,
    ) -> tuple[pa.Table, int | None, int]: ...

    def save_analysis(
        self,
        *,
        run_id: str,
        title: str,
        analysis_key: str,
        step_id: str | None,
        inputs: Sequence[AnalysisInput],
        executions: Sequence[AnalysisExecution],
        outputs: Sequence[AnalysisOutput],
        parameter_proposals: Sequence[ParameterChangeProposal],
    ) -> SavedAnalysis: ...

    def analyses(
        self,
        run_id: str,
        *,
        limit: int,
        before: int | None,
    ) -> RunAnalysisPage: ...

    def analysis(self, run_id: str, selector: str) -> RunAnalysisView: ...

    def attach(
        self,
        *,
        run_id: str,
        path: str | Path | None,
        key: str,
        kind: str,
        text: str | None,
        content: bytes | None,
        filename: str | None,
        media_type: str | None,
        metadata: Mapping[str, JsonValue] | None,
    ) -> ContentEntry: ...

    def artifact_text(
        self,
        run_id: str,
        selector: str,
        *,
        expected_kind: str | None,
    ) -> RunArtifactTextResult: ...

    def artifact_json(
        self,
        run_id: str,
        selector: str,
        *,
        expected_kind: str | None,
    ) -> RunArtifactJsonResult: ...

    def artifact_bytes(
        self,
        run_id: str,
        selector: str,
        *,
        expected_kind: str | None,
    ) -> RunArtifactBytesResult: ...

    def record_json(
        self,
        run_id: str,
        selector: str,
        *,
        expected_kind: str | None,
    ) -> RunRecordJsonResult: ...


class RunSession(Protocol):
    @property
    def run_operations(self) -> RunOperations: ...


@dataclass(frozen=True)
class RunHandle:
    """Live run identity whose lazy reads use its creating session.

    Retain ``run.id`` to reattach through a new session's ``get_run``. Capture
    ``snapshot = run.snapshot`` before closing for immutable local state.
    """

    session: RunSession
    id: str

    @property
    def snapshot(self) -> RunSnapshot:
        """Capture immutable run identity and the outcome currently retained.

        Every access loads fresh state. The returned value performs no HTTP and
        remains readable after closing the session. A completed run has an
        outcome; a snapshot taken earlier stays unchanged as the run progresses.
        """

        return self.session.run_operations.load_snapshot(self.id)

    @property
    def status(self) -> str:
        return self.snapshot.status

    @property
    def config(self) -> ConfigProfileSnapshot:
        return self.session.run_operations.load_config(self.id)

    @property
    def request(self) -> RunRequest:
        """Load the operator request accepted with this run."""

        return self.session.run_operations.load_request(self.id)

    @property
    def samples(self) -> tuple[SampleBinding, ...]:
        """Load exact sample revisions frozen into this run."""

        return self.snapshot.samples

    def execution_segments(
        self,
        *,
        limit: int = 64,
        before: int | None = None,
    ) -> RunExecutionSegmentPage:
        """List newest continuous executor-ownership intervals for this run."""

        return self.session.run_operations.execution_segments(
            self.id,
            limit=limit,
            before=before,
        )

    def domain_jobs(
        self,
        *,
        limit: int = 64,
        before: int | None = None,
    ) -> RunDomainJobStatePage:
        """List current durable domain-job states in invocation order."""

        return self.session.run_operations.domain_jobs(
            self.id,
            limit=limit,
            before=before,
        )

    def domain_job_transitions(
        self,
        *,
        limit: int = 64,
        before: int | None = None,
    ) -> RunDomainJobTransitionPage:
        """List the durable transition timeline for this run's domain jobs."""

        return self.session.run_operations.domain_job_transitions(
            self.id,
            limit=limit,
            before=before,
        )

    def contents(
        self,
        *,
        limit: int = 100,
        before: int | None = None,
        role: Literal["artifact", "dataset", "record"] | None = None,
        kind: str | None = None,
    ) -> RunContentPage:
        """List a bounded newest-first page of this run's published content."""

        return self.session.run_operations.contents(
            self.id,
            limit=limit,
            before=before,
            role=role,
            kind=kind,
        )

    def content(
        self,
        role: Literal["artifact", "dataset", "record"],
        content_id: str,
    ) -> ContentEntry:
        """Load one exact entry from this run's content catalog."""

        return self.session.run_operations.content_entry(
            self.id,
            role=role,
            content_id=content_id,
        )

    def measurements(self) -> Dataset:
        """Open a lazy measurement dataset using this handle's connection.

        Read it while the session is open, or reattach the run in a new session
        and open its measurements there. Materialized values remain local data.
        """

        entry = self.content("dataset", RAW_MEASUREMENTS_DATASET_ID)
        if entry.kind != MEASUREMENT_DATASET_KIND:
            raise ValueError("measurement dataset content has the wrong kind")
        if entry.data_schema is None:
            raise ValueError("measurement dataset is missing its semantic schema")
        schema = MeasurementDatasetSchema.model_validate(entry.data_schema)
        return Dataset._from_source(  # pyright: ignore[reportPrivateUsage]
            schema=schema,
            entry=entry,
            load_raw=lambda: (
                self.session.run_operations.load_measurement_dataset(
                    self.id,
                    selector=entry.id,
                ).dataset
            ),
            load_projected_batches=self._measurement_projection_batches,
        )

    def _measurements_for_analysis(self) -> Dataset:
        """Open a source-backed dataset for one active analysis context."""

        return self.measurements()

    def _load_analysis_dataset(
        self,
        analysis_id: str,
        selector: str,
    ) -> DerivedDataset:
        """Load one published analysis dataset for the typed read facade."""

        del analysis_id
        loaded = self.session.run_operations.load_dataset_bytes(
            self.id,
            selector,
            expected_kind="analysis_dataset",
        )
        if loaded.dataset.data_schema is None:
            raise ValueError("analysis dataset is missing its semantic schema")
        return DerivedDataset.from_arrow_ipc(
            loaded.content,
            schema=DerivedDatasetSchema.model_validate(loaded.dataset.data_schema),
        )

    @overload
    def result(
        self,
        /,
    ) -> StoredExperimentResultView: ...

    @overload
    def result[ResultT](
        self,
        output: ResultT,
        /,
    ) -> ExperimentResultView[ResultT]: ...

    def result[ResultT](
        self,
        output: ResultT | None = None,
        /,
    ) -> StoredExperimentResultView | ExperimentResultView[ResultT]:
        """Load the experiment return value as a historical or typed result."""

        dataset = self.measurements()
        if output is None:
            return dataset.result
        return dataset.bind(output)

    def _measurement_projection_batches(
        self,
        projection: ProjectionSchema,
        batch_size: int,
    ) -> pa.RecordBatchReader:
        """Read one already-bound projection through the Arrow page transport."""

        if not 1 <= batch_size <= MAX_MEASUREMENT_PAGE_SIZE:
            raise ValueError(
                "measurement batch_size must be between 1 and "
                f"{MAX_MEASUREMENT_PAGE_SIZE}"
            )
        query = MeasurementArrowQuery(
            columns=tuple(
                MeasurementArrowColumn(
                    name=field.name,
                    variable_id=field.variable_id,
                )
                for field in projection.fields
            ),
            units={
                field.name: field.unit
                for field in projection.fields
                if field.unit is not None
            },
            entity_selection=projection.entity_selection,
            diagnostics=projection.diagnostics,
            include_identity=projection.include_identity,
            layout=projection.layout,
            limit=batch_size,
        )
        first, next_offset, snapshot_size = (
            self.session.run_operations.load_measurement_arrow_page(
                self.id,
                query=query,
            )
        )

        def batches() -> Iterator[pa.RecordBatch]:
            yield from first.to_batches(max_chunksize=batch_size)
            offset = next_offset
            previous_offset = query.offset
            while offset is not None:
                if offset <= previous_offset:
                    raise ValueError("measurement Arrow page next_offset must advance")
                table, following_offset, page_snapshot_size = (
                    self.session.run_operations.load_measurement_arrow_page(
                        self.id,
                        query=query.model_copy(
                            update={
                                "offset": offset,
                                "snapshot_size": snapshot_size,
                            }
                        ),
                    )
                )
                if page_snapshot_size != snapshot_size:
                    raise ValueError("measurement Arrow snapshot changed between pages")
                if table.schema != first.schema:
                    raise ValueError("measurement pages produced different projections")
                yield from table.to_batches(max_chunksize=batch_size)
                previous_offset = offset
                offset = following_offset

        return pa.RecordBatchReader.from_batches(first.schema, batches())

    def analysis(
        self,
        title: str,
        *,
        key: str | None = None,
    ) -> AnalysisContext:
        """Start an exploratory analysis through the regular analysis context."""

        return AnalysisContext(run=self, default_title=title, default_key=key)

    def analysis_summaries(
        self,
        *,
        limit: int = 100,
        before: int | None = None,
    ) -> RunAnalysisPage:
        """Load one bounded history page without fetching publication bodies."""

        return self.session.run_operations.analyses(
            self.id,
            limit=limit,
            before=before,
        )

    def published_analysis(self, selector: str) -> PublishedAnalysis:
        """Load an exact analysis record ID or the latest matching logical key."""

        return PublishedAnalysis(
            source=self,
            view=self.session.run_operations.analysis(self.id, selector),
        )

    def save_analysis(
        self,
        *,
        title: str,
        analysis_key: str,
        step_id: str | None,
        inputs: Sequence[AnalysisInput],
        executions: Sequence[AnalysisExecution],
        outputs: Sequence[AnalysisOutput],
        parameter_proposals: Sequence[ParameterChangeProposal],
    ) -> SavedAnalysis:
        """Persist analysis through this run's owning execution boundary."""

        return self.session.run_operations.save_analysis(
            run_id=self.id,
            title=title,
            analysis_key=analysis_key,
            step_id=step_id,
            inputs=inputs,
            executions=executions,
            outputs=outputs,
            parameter_proposals=parameter_proposals,
        )

    def analyze(
        self,
        step: AnalysisStep,
        *,
        key: str | None = None,
    ) -> PublishedAnalysis:
        """Run and durably publish one declared analysis step."""

        analysis = step.run(
            AnalysisContext(
                run=self,
                default_key=key or step.id,
                step_id=step.id,
            )
        )
        if analysis.key is None or analysis.step_id is None:
            analysis = replace(
                analysis,
                key=analysis.key or key or step.id,
                step_id=analysis.step_id or step.id,
            )
        return analysis.save()

    def attach(
        self,
        path: str | Path | None = None,
        *,
        key: str,
        kind: str = "attachment",
        text: str | None = None,
        content: bytes | None = None,
        filename: str | None = None,
        media_type: str | None = None,
        metadata: Mapping[str, JsonValue] | None = None,
    ) -> ContentEntry:
        return self.session.run_operations.attach(
            run_id=self.id,
            path=path,
            key=key,
            kind=kind,
            text=text,
            content=content,
            filename=filename,
            media_type=media_type,
            metadata=metadata,
        )

    def artifact_text(
        self,
        selector: str,
        *,
        expected_kind: str | None = None,
    ) -> RunArtifactTextResult:
        return self.session.run_operations.artifact_text(
            self.id,
            selector=selector,
            expected_kind=expected_kind,
        )

    def artifact_bytes(
        self,
        selector: str,
        *,
        expected_kind: str | None = None,
    ) -> RunArtifactBytesResult:
        return self.session.run_operations.artifact_bytes(
            self.id,
            selector=selector,
            expected_kind=expected_kind,
        )

    def artifact_json(
        self,
        selector: str,
        *,
        expected_kind: str | None = None,
    ) -> RunArtifactJsonResult:
        return self.session.run_operations.artifact_json(
            self.id,
            selector=selector,
            expected_kind=expected_kind,
        )

    def record_json(
        self,
        selector: str,
        *,
        expected_kind: str | None = None,
    ) -> RunRecordJsonResult:
        return self.session.run_operations.record_json(
            self.id,
            selector=selector,
            expected_kind=expected_kind,
        )

    def _analysis_artifact_text(
        self,
        analysis_id: str,
        selector: str,
        *,
        expected_kind: str | None = None,
    ) -> RunArtifactTextResult:
        del analysis_id
        return self.artifact_text(selector, expected_kind=expected_kind)

    def _analysis_artifact_entry(
        self,
        analysis_id: str,
        selector: str,
    ) -> ContentEntry:
        del analysis_id
        entry = self.content("artifact", selector)
        if entry.kind != "analysis_artifact":
            raise ValueError("analysis artifact content has the wrong kind")
        return entry

    def _analysis_artifact_json(
        self,
        analysis_id: str,
        selector: str,
        *,
        expected_kind: str | None = None,
    ) -> RunArtifactJsonResult:
        del analysis_id
        return self.artifact_json(selector, expected_kind=expected_kind)

    def _analysis_artifact_bytes(
        self,
        analysis_id: str,
        selector: str,
        *,
        expected_kind: str | None = None,
    ) -> RunArtifactBytesResult:
        del analysis_id
        return self.artifact_bytes(selector, expected_kind=expected_kind)

    def _analysis_record_json(
        self,
        analysis_id: str,
        selector: str,
        *,
        expected_kind: str | None = None,
    ) -> RunRecordJsonResult:
        del analysis_id
        return self.record_json(selector, expected_kind=expected_kind)


@dataclass(frozen=True, slots=True)
class RunHandlePage:
    """Bounded page of notebook run handles."""

    items: tuple[RunHandle, ...] = ()
    next_cursor: int | None = None


def run_handle_id(run: RunHandle | RunSelector) -> str:
    if isinstance(run, RunHandle):
        return run.id
    return selected_run_id(run)
