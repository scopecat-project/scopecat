"""Ordinary Python analysis over a portable file, without an application runtime."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from types import TracebackType
from typing import Self, cast

from scopecat.analysis.datasets import DerivedDataset, DerivedDatasetSchema
from scopecat.analysis.figure_views import read_figure_preview
from scopecat.analysis.repository import AnalysisPublication
from scopecat.analysis.service import (
    AnalysisInput,
    AnalysisOutput,
    RetainedAnalysis,
    SavedAnalysis,
    prepare_independent_analysis,
)
from scopecat.api.analysis import AnalysisContext, AnalysisInvocation
from scopecat.api.published_analysis import PublishedAnalysis
from scopecat.data_exchange import ScientificExchange
from scopecat.data_exchange.models import AnalysisEvidence
from scopecat.kernel.json_types import JsonValue
from scopecat.measurements.dataset import Dataset
from scopecat.records.analysis import (
    AnalysisExecution,
    AnalysisFigureProjection,
    AnalysisPublishedDatasetViewSource,
    AnalysisRecord,
    AnalysisSubject,
    ProjectAnalysisSubject,
    RunAnalysisSubject,
)
from scopecat.records.config import ConfigProfileSnapshot
from scopecat.records.content import ContentEntry
from scopecat.records.parameter_change import ParameterChangeProposal
from scopecat.runs.data import (
    RunArtifactBytesResult,
    RunArtifactJsonResult,
    RunArtifactTextResult,
    RunRecordJsonResult,
)
from scopecat.runs.refs import content_entry_ref

_PROJECT_SUBJECT = ProjectAnalysisSubject()


@dataclass(frozen=True)
class _PublicationView:
    entry: ContentEntry
    analysis: AnalysisRecord
    published_at: datetime


class Capture:
    """Read portable data and atomically save analyses to a separate output file.

    Each successful save is durable. Later failures preserve previous saves.
    Only the output created by this handle can be updated; the original source
    and pre-existing destinations are never overwritten.
    """

    def __init__(self, source: Path, *, output: Path | None = None):
        if output is not None and output.exists():
            raise FileExistsError(output)
        self._source_path = source.resolve()
        self._source = ScientificExchange(self._source_path)
        try:
            self._source.verify()
        except BaseException:
            self._source.close()
            raise
        self._output = output.resolve() if output is not None else None
        self._owns_output = False
        self._analyses = list(self._source.evidence.analyses)

    @property
    def run_ids(self) -> tuple[str, ...]:
        return tuple(run.snapshot.run_id for run in self._source.evidence.runs)

    def run(self, run_id: str) -> CapturedRun:
        run = next(
            (
                run
                for run in self._source.evidence.runs
                if run.snapshot.run_id == run_id
            ),
            None,
        )
        if run is None:
            raise KeyError(run_id)
        return CapturedRun(self, run_id, run.configuration)

    def analysis(
        self,
        run_id: str | None = None,
        *,
        title: str = "analysis",
        key: str | None = None,
    ) -> AnalysisContext:
        return AnalysisContext(
            owner=self,
            run=self.run(run_id) if run_id is not None else None,
            default_title=title,
            default_key=key,
        )

    def measurements(self, run_id: str) -> Dataset:
        return self._source.recording(run_id).dataset()

    def analyze(
        self, run_id: str, invocation: AnalysisInvocation, *, key: str | None = None
    ) -> PublishedAnalysis:
        """Run an ordinary analysis function in this Python environment.

        Retain the local implementation identity and interpreter description,
        alongside ordinary argument/input/output evidence. This is provenance,
        not a promise to recreate or execute the original environment.
        """
        implementation = invocation.implementation_fingerprint
        analysis = invocation.run(
            self.analysis(run_id, title=invocation.id, key=key or invocation.id)
        )
        return replace(
            analysis,
            executions=tuple(
                execution.model_copy(
                    update={
                        "metadata": {
                            **execution.metadata,
                            "local_implementation": implementation,
                            "python": sys.version,
                        }
                    }
                )
                for execution in analysis.executions
            ),
        ).save()

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
        if self._output is None:
            raise ValueError("open_capture requires an output path to save analysis")
        if parameter_proposals:
            raise TypeError(
                "independent analysis cannot publish run parameter proposals"
            )
        previous = [
            item
            for item in self._analyses
            if item.record.subject.kind == "project" and item.record.key == analysis_key
        ]
        latest = max(previous, key=lambda item: item.record.revision, default=None)
        prepared = prepare_independent_analysis(
            title=title,
            analysis_key=analysis_key,
            step_id=step_id,
            inputs=inputs,
            executions=executions,
            outputs=outputs,
            existing=RetainedAnalysis(latest.entry, latest.record) if latest else None,
            read_dataset=self._figure_dataset,
        )
        if prepared.publication is not None or not self._owns_output:
            self._publish(prepared.publication)
        return prepared.saved

    def _publish(self, publication: AnalysisPublication | None) -> None:
        output = self._output
        assert output is not None
        with tempfile.TemporaryDirectory(
            prefix=".scopecat-analysis-", dir=output.parent
        ) as directory:
            staged = Path(directory) / "analysis.scopecat"
            self._source.write_analyses(staged, (publication,) if publication else ())
            # Windows cannot replace an archive while this reader holds it open.
            self._source.close()
            try:
                if self._owns_output:
                    staged.replace(output)
                else:
                    os.link(staged, output)
                self._source_path = output
                self._owns_output = True
            finally:
                self._source = ScientificExchange(self._source_path)
            self._analyses = list(self._source.evidence.analyses)

    def analysis_evidence(
        self, selector: str, subject: AnalysisSubject | None = None
    ) -> AnalysisEvidence:
        candidates = [
            item
            for item in self._analyses
            if subject is None or item.record.subject == subject
        ]
        exact = [item for item in candidates if item.entry.id == selector]
        if len(exact) == 1:
            return exact[0]
        if len(exact) > 1:
            raise ValueError(f"analysis ID is ambiguous across subjects: {selector}")
        matching = [item for item in candidates if item.record.key == selector]
        if matching and any(
            item.record.subject != matching[0].record.subject for item in matching
        ):
            raise ValueError(f"analysis key is ambiguous across subjects: {selector}")
        if not matching:
            raise KeyError(selector)
        return max(matching, key=lambda item: item.record.revision)

    def published_analysis(
        self, selector: str, *, subject: AnalysisSubject = _PROJECT_SUBJECT
    ) -> PublishedAnalysis:
        evidence = self.analysis_evidence(selector, subject)
        return PublishedAnalysis(
            source=_PublicationSource(self, evidence.record.subject),
            view=_PublicationView(
                evidence.entry, evidence.record, evidence.published_at
            ),
        )

    def read_analysis_content(
        self, analysis: AnalysisEvidence, selector: str
    ) -> tuple[ContentEntry, bytes]:
        entry = next(item for item in analysis.contents if item.id == selector)
        ref = content_entry_ref(entry)
        subject = analysis.record.subject
        owner_kind = "run" if subject.kind == "run" else "analysis"
        owner_id = subject.run_id if subject.kind == "run" else analysis.entry.id
        reference = next(
            item
            for item in self._source.payloads
            if item.owner_kind == owner_kind
            and item.owner_id == owner_id
            and item.ref == ref
        )
        return entry, self._source.read_payload(reference)

    def _figure_dataset(
        self,
        source: AnalysisPublishedDatasetViewSource,
        projection: AnalysisFigureProjection,
        limit: int,
    ) -> tuple[DerivedDataset, int]:
        analysis = self.analysis_evidence(
            source.source.analysis_record_id, source.source.subject
        )
        entry, content = self.read_analysis_content(analysis, source.dataset.dataset_id)
        return read_figure_preview(
            content,
            schema=DerivedDatasetSchema.model_validate(entry.data_schema),
            projection=projection,
            limit=limit,
        )

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        self._source.close()


@dataclass(frozen=True)
class CapturedRun:
    capture: Capture
    id: str
    config: ConfigProfileSnapshot

    def _measurements_for_analysis(self) -> Dataset:
        return self.capture.measurements(self.id)

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
        return self.capture.save_analysis(
            title=title,
            analysis_key=analysis_key,
            step_id=step_id,
            inputs=inputs,
            executions=executions,
            outputs=outputs,
            parameter_proposals=parameter_proposals,
        )

    def published_analysis(self, selector: str) -> PublishedAnalysis:
        return self.capture.published_analysis(
            selector, subject=RunAnalysisSubject(run_id=self.id)
        )


@dataclass(frozen=True)
class _PublicationSource:
    capture: Capture
    subject: AnalysisSubject

    def _analysis_artifact_entry(self, analysis_id: str, selector: str) -> ContentEntry:
        evidence = self.capture.analysis_evidence(analysis_id, self.subject)
        return next(item for item in evidence.contents if item.id == selector)

    def _load_analysis_dataset(self, analysis_id: str, selector: str) -> DerivedDataset:
        evidence = self.capture.analysis_evidence(analysis_id, self.subject)
        entry, content = self.capture.read_analysis_content(evidence, selector)
        return DerivedDataset.from_arrow_ipc(
            content, schema=DerivedDatasetSchema.model_validate(entry.data_schema)
        )

    def _analysis_artifact_bytes(
        self, analysis_id: str, selector: str, *, expected_kind: str | None = None
    ) -> RunArtifactBytesResult:
        evidence = self.capture.analysis_evidence(analysis_id, self.subject)
        entry, content = self.capture.read_analysis_content(evidence, selector)
        if expected_kind is not None and entry.kind != expected_kind:
            raise TypeError(f"expected {expected_kind}, got {entry.kind}")
        return RunArtifactBytesResult(artifact=entry, content=content)

    def _analysis_artifact_text(
        self, analysis_id: str, selector: str, *, expected_kind: str | None = None
    ) -> RunArtifactTextResult:
        result = self._analysis_artifact_bytes(
            analysis_id, selector, expected_kind=expected_kind
        )
        return RunArtifactTextResult(
            artifact=result.artifact, content=result.content.decode()
        )

    def _analysis_artifact_json(
        self, analysis_id: str, selector: str, *, expected_kind: str | None = None
    ) -> RunArtifactJsonResult:
        result = self._analysis_artifact_text(
            analysis_id, selector, expected_kind=expected_kind
        )
        content = cast("object", json.loads(result.content))
        if not isinstance(content, dict):
            raise TypeError("analysis JSON artifact must contain an object")
        return RunArtifactJsonResult(
            artifact=result.artifact, content=cast("dict[str, JsonValue]", content)
        )

    def _analysis_record_json(
        self, analysis_id: str, selector: str, *, expected_kind: str | None = None
    ) -> RunRecordJsonResult:
        result = self._analysis_artifact_json(
            analysis_id, selector, expected_kind=expected_kind
        )
        return RunRecordJsonResult(record=result.artifact, content=result.content)


def open_capture(source: str | Path, *, output: str | Path | None = None) -> Capture:
    """Open retained data with an optional new file for durable analysis saves."""
    return Capture(Path(source), output=Path(output) if output is not None else None)
