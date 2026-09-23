# pyright: reportUnknownMemberType=false, reportUnknownParameterType=false
# pyright: reportUnknownVariableType=false
"""Remote run operations backed by the daemon HTTP transport."""

from __future__ import annotations

from base64 import b64encode
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

import pyarrow as pa
from pydantic import JsonValue

from scopecat.analysis.service import (
    AnalysisArtifactOutput,
    AnalysisDatasetOutput,
    AnalysisFactOutput,
    AnalysisFigureOutput,
    AnalysisInput,
    AnalysisOutput,
    AnalysisParameterProposalOutput,
    AnalysisTableOutput,
    ConfigurationAnalysisInput,
    InterpretationAnalysisInput,
    MeasurementAnalysisInput,
    SavedAnalysis,
)
from scopecat.control.models import RunExecutionSegmentPage
from scopecat.daemon.client import DaemonClient
from scopecat.daemon.views import (
    MeasurementArrowQuery,
    RunAnalysisPage,
    RunAnalysisView,
    RunContentPage,
)
from scopecat.daemon.wire import (
    AnalysisArtifactOutputPayload,
    AnalysisDatasetOutputPayload,
    AnalysisFactOutputPayload,
    AnalysisFigureOutputPayload,
    AnalysisInputPayload,
    AnalysisOutputPayload,
    AnalysisParameterProposalOutputPayload,
    AnalysisSaveCommand,
    AnalysisTableOutputPayload,
    ConfigurationAnalysisInputPayload,
    InterpretationAnalysisInputPayload,
    MeasurementAnalysisInputPayload,
    PublishedAnalysisInputPayload,
    RunAttachmentCommand,
    RunDomainJobStatePage,
    RunDomainJobTransitionPage,
)
from scopecat.records.analysis import AnalysisExecution
from scopecat.records.config import ConfigProfileSnapshot
from scopecat.records.content import ContentEntry
from scopecat.records.metadata import validate_json_metadata
from scopecat.records.parameter_change import ParameterChangeProposal
from scopecat.records.run import RunSnapshot
from scopecat.records.run_request import RunRequest
from scopecat.runs.data import (
    RunArtifactBytesResult,
    RunArtifactJsonResult,
    RunArtifactTextResult,
    RunDatasetBytesResult,
    RunMeasurementDatasetResult,
    RunRecordJsonResult,
)


@dataclass(frozen=True, slots=True)
class RemoteRunOperations:
    """Run handle storage operations owned by one daemon transport."""

    client: DaemonClient

    def load_snapshot(self, run_id: str) -> RunSnapshot:
        return self.client.get_run(run_id).snapshot

    def load_config(self, run_id: str) -> ConfigProfileSnapshot:
        return self.client.run_config(run_id).config

    def load_request(self, run_id: str) -> RunRequest:
        return self.client.run_request(run_id).request

    def execution_segments(
        self,
        run_id: str,
        *,
        limit: int,
        before: int | None,
    ) -> RunExecutionSegmentPage:
        return self.client.get_run_execution_segments(
            run_id,
            limit=limit,
            before=before,
        )

    def domain_jobs(
        self,
        run_id: str,
        *,
        limit: int,
        before: int | None,
    ) -> RunDomainJobStatePage:
        return self.client.get_run_domain_jobs(
            run_id,
            limit=limit,
            before=before,
        )

    def domain_job_transitions(
        self,
        run_id: str,
        *,
        limit: int,
        before: int | None,
    ) -> RunDomainJobTransitionPage:
        return self.client.get_run_domain_job_transitions(
            run_id,
            limit=limit,
            before=before,
        )

    def contents(
        self,
        run_id: str,
        *,
        limit: int,
        before: int | None,
        role: Literal["artifact", "dataset", "record"] | None,
        kind: str | None,
    ) -> RunContentPage:
        return self.client.run_contents(
            run_id,
            limit=limit,
            before=before,
            role=role,
            kind=kind,
        )

    def content_entry(
        self,
        run_id: str,
        *,
        role: Literal["artifact", "dataset", "record"],
        content_id: str,
    ) -> ContentEntry:
        return self.client.run_content(
            run_id,
            role=role,
            content_id=content_id,
        )

    def load_measurement_dataset(
        self,
        run_id: str,
        *,
        selector: str = "raw-measurements",
    ) -> RunMeasurementDatasetResult:
        return self.client.dataset_content(run_id, selector)

    def load_dataset_bytes(
        self,
        run_id: str,
        selector: str,
        *,
        expected_kind: str | None,
    ) -> RunDatasetBytesResult:
        view = self.client.dataset_bytes(
            run_id,
            selector,
            expected_kind=expected_kind,
        )
        return RunDatasetBytesResult(
            dataset=view.dataset,
            content=view.content_bytes(),
        )

    def load_measurement_arrow_page(
        self,
        run_id: str,
        *,
        query: MeasurementArrowQuery,
    ) -> tuple[pa.Table, int | None, int]:
        return self.client.measurement_arrow(run_id, query)

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
    ) -> SavedAnalysis:
        payloads = tuple(analysis_output_payload(output) for output in outputs)
        output_proposals = tuple(
            payload.content
            for payload in payloads
            if isinstance(payload, AnalysisParameterProposalOutputPayload)
        )
        if output_proposals != tuple(parameter_proposals):
            raise ValueError("analysis parameter proposals must match proposal outputs")
        receipt = self.client.save_analysis(
            run_id,
            AnalysisSaveCommand(
                title=title,
                analysis_key=analysis_key,
                step_id=step_id,
                inputs=tuple(analysis_input_payload(item) for item in inputs),
                executions=tuple(executions),
                outputs=payloads,
            ),
        )
        saved_proposals = iter(receipt.parameter_proposals)
        saved_outputs = tuple(
            replace(output, content=next(saved_proposals))
            if isinstance(output, AnalysisParameterProposalOutput)
            else output
            for output in outputs
        )
        return SavedAnalysis(
            record=receipt.record,
            analysis_key=receipt.analysis_key,
            inputs=tuple(inputs),
            executions=tuple(executions),
            outputs=saved_outputs,
            parameter_proposals=receipt.parameter_proposals,
        )

    def analyses(
        self,
        run_id: str,
        *,
        limit: int,
        before: int | None,
    ) -> RunAnalysisPage:
        return self.client.analyses(run_id, limit=limit, before=before)

    def analysis(self, run_id: str, selector: str) -> RunAnalysisView:
        return self.client.analysis(run_id, selector)

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
    ) -> ContentEntry:
        source_path = None if path is None else Path(path)
        if source_path is not None:
            if text is not None or content is not None:
                raise ValueError("run attachment requires exactly one content source")
            content = source_path.read_bytes()
            filename = filename or source_path.name
        encoded = None if content is None else b64encode(content).decode("ascii")
        return self.client.attach(
            run_id,
            RunAttachmentCommand(
                key=key,
                kind=kind,
                text=text,
                content_base64=encoded,
                filename=filename,
                media_type=media_type,
                metadata=dict(metadata or {}),
            ),
        )

    def artifact_bytes(
        self,
        run_id: str,
        selector: str,
        *,
        expected_kind: str | None,
    ) -> RunArtifactBytesResult:
        view = self.client.artifact_bytes(
            run_id,
            selector,
            expected_kind=expected_kind,
        )
        return RunArtifactBytesResult(
            artifact=view.artifact,
            content=view.content_bytes(),
        )

    def artifact_text(
        self,
        run_id: str,
        selector: str,
        *,
        expected_kind: str | None,
    ) -> RunArtifactTextResult:
        return self.client.artifact_text(
            run_id,
            selector,
            expected_kind=expected_kind,
        )

    def artifact_json(
        self,
        run_id: str,
        selector: str,
        *,
        expected_kind: str | None,
    ) -> RunArtifactJsonResult:
        return self.client.artifact_json(
            run_id,
            selector,
            expected_kind=expected_kind,
        )

    def record_json(
        self,
        run_id: str,
        selector: str,
        *,
        expected_kind: str | None,
    ) -> RunRecordJsonResult:
        return self.client.record_json(
            run_id,
            selector,
            expected_kind=expected_kind,
        )


def analysis_input_payload(value: AnalysisInput) -> AnalysisInputPayload:
    metadata = (
        None if value.metadata is None else validate_json_metadata(value.metadata)
    )
    if isinstance(value, MeasurementAnalysisInput | ConfigurationAnalysisInput):
        payload_type = (
            MeasurementAnalysisInputPayload
            if isinstance(value, MeasurementAnalysisInput)
            else ConfigurationAnalysisInputPayload
        )
        return payload_type(
            id=value.id,
            run_id=value.run_id,
            target=value.target,
            content_hash=value.content_hash,
            codec=value.codec,
            role=value.role,
            title=value.title,
            metadata=metadata,
        )
    if isinstance(value, InterpretationAnalysisInput):
        return InterpretationAnalysisInputPayload(
            id=value.id,
            target=value.target,
            content_hash=value.content_hash,
            codec=value.codec,
            role=value.role,
            title=value.title,
            metadata=metadata,
            source=value.source,
        )
    return PublishedAnalysisInputPayload(
        id=value.id,
        target=value.target,
        kind=value.kind,
        content_hash=value.content_hash,
        codec=value.codec,
        role=value.role,
        title=value.title,
        metadata=metadata,
        source=value.source,
    )


def analysis_output_payload(value: AnalysisOutput) -> AnalysisOutputPayload:
    metadata = validate_json_metadata(value.metadata)
    if isinstance(value, AnalysisFactOutput):
        return AnalysisFactOutputPayload(
            kind="fact",
            id=value.id,
            title=value.title,
            content=value.content,
            produced_by=value.produced_by,
            metadata=metadata,
        )
    if isinstance(value, AnalysisDatasetOutput):
        return AnalysisDatasetOutputPayload(
            kind="dataset",
            id=value.id,
            title=value.title,
            content=value.content.to_payload(),
            produced_by=value.produced_by,
            derived_from=value.derived_from,
            metadata=metadata,
        )
    if isinstance(value, AnalysisTableOutput):
        return AnalysisTableOutputPayload(
            kind="table",
            id=value.id,
            title=value.title,
            content=value.content,
            metadata=metadata,
        )
    if isinstance(value, AnalysisFigureOutput):
        return AnalysisFigureOutputPayload(
            kind="figure",
            id=value.id,
            title=value.title,
            content=value.content,
            metadata=metadata,
        )
    if isinstance(value, AnalysisArtifactOutput):
        return AnalysisArtifactOutputPayload(
            kind="artifact",
            id=value.id,
            title=value.title,
            content_base64=b64encode(value.content).decode("ascii"),
            filename=value.filename,
            media_type=value.media_type,
            produced_by=value.produced_by,
            metadata=metadata,
        )
    assert isinstance(value, AnalysisParameterProposalOutput)
    return AnalysisParameterProposalOutputPayload(
        kind="parameter_change_proposal",
        id=value.id,
        title=value.title,
        content=value.content,
        metadata=metadata,
    )


__all__ = ["RemoteRunOperations"]
