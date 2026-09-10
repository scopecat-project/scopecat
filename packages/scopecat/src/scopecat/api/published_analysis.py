"""Typed read facade for one durable analysis publication."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, cast

from scopecat.analysis.datasets import DerivedDataset
from scopecat.analysis.facts import AnalysisFactSchema, ordinary_result_schema
from scopecat.config.candidates import (
    CandidateConfig,
    CandidateSelection,
    candidate_config_from_proposals,
)
from scopecat.daemon.views import (
    ProjectAnalysisView,
    RunAnalysisView,
    SampleAnalysisView,
)
from scopecat.records.analysis import (
    AnalysisArtifactRecordOutput,
    AnalysisDatasetRecordOutput,
    AnalysisExecution,
    AnalysisFact,
    AnalysisFactRecordOutput,
    AnalysisFigureRecordOutput,
    AnalysisFigureView,
    AnalysisParameterProposalRecordOutput,
    AnalysisPublishedDatasetViewSource,
    AnalysisPublishedOutputReference,
    AnalysisRecordInput,
    AnalysisRecordOutput,
    AnalysisTableRecordOutput,
    AnalysisTableView,
)
from scopecat.records.content import ContentEntry
from scopecat.records.parameter_change import ParameterChangeProposal
from scopecat.runs.data import (
    RunArtifactBytesResult,
    RunArtifactJsonResult,
    RunArtifactTextResult,
    RunRecordJsonResult,
)
from scopecat.sdk.compute import PYTHON_JSON_CODEC


class _PublishedAnalysisSource(Protocol):
    def _analysis_artifact_entry(
        self,
        analysis_id: str,
        selector: str,
    ) -> ContentEntry: ...

    def _load_analysis_dataset(
        self,
        analysis_id: str,
        selector: str,
    ) -> DerivedDataset: ...

    def _analysis_artifact_text(
        self,
        analysis_id: str,
        selector: str,
        *,
        expected_kind: str | None = None,
    ) -> RunArtifactTextResult: ...

    def _analysis_artifact_json(
        self,
        analysis_id: str,
        selector: str,
        *,
        expected_kind: str | None = None,
    ) -> RunArtifactJsonResult: ...

    def _analysis_artifact_bytes(
        self,
        analysis_id: str,
        selector: str,
        *,
        expected_kind: str | None = None,
    ) -> RunArtifactBytesResult: ...

    def _analysis_record_json(
        self,
        analysis_id: str,
        selector: str,
        *,
        expected_kind: str | None = None,
    ) -> RunRecordJsonResult: ...


@dataclass(frozen=True, slots=True)
class PublishedAnalysisArtifact:
    """One analysis-owned artifact with typed payload access."""

    source: _PublishedAnalysisSource
    analysis_id: str
    output: AnalysisArtifactRecordOutput

    @property
    def entry(self) -> ContentEntry:
        return self.source._analysis_artifact_entry(  # pyright: ignore[reportPrivateUsage]
            self.analysis_id,
            self.output.content.artifact_id,
        )

    def bytes(self) -> bytes:
        return self.source._analysis_artifact_bytes(  # pyright: ignore[reportPrivateUsage]
            self.analysis_id,
            self.output.content.artifact_id,
            expected_kind="analysis_artifact",
        ).content

    def text(self) -> str:
        return self.source._analysis_artifact_text(  # pyright: ignore[reportPrivateUsage]
            self.analysis_id,
            self.output.content.artifact_id,
            expected_kind="analysis_artifact",
        ).content

    def json(self) -> dict[str, object]:
        return cast(
            "dict[str, object]",
            self.source._analysis_artifact_json(  # pyright: ignore[reportPrivateUsage]
                self.analysis_id,
                self.output.content.artifact_id,
                expected_kind="analysis_artifact",
            ).content,
        )


@dataclass(frozen=True, slots=True)
class PublishedAnalysis:
    """One immutable analysis record with output-ID based typed access."""

    source: _PublishedAnalysisSource
    view: RunAnalysisView | ProjectAnalysisView | SampleAnalysisView

    @property
    def id(self) -> str:
        return self.view.entry.id

    @property
    def key(self) -> str | None:
        return self.view.analysis.key

    @property
    def title(self) -> str:
        return self.view.analysis.title

    @property
    def revision(self) -> int:
        return self.view.analysis.revision

    @property
    def step_id(self) -> str | None:
        return self.view.analysis.step_id

    @property
    def publication_hash(self) -> str:
        return self.view.analysis.publication_hash

    @property
    def published_at(self) -> datetime:
        return self.view.published_at

    @property
    def outputs(self) -> tuple[AnalysisRecordOutput, ...]:
        return tuple(self.view.analysis.outputs)

    @property
    def inputs(self) -> tuple[AnalysisRecordInput, ...]:
        return tuple(self.view.analysis.inputs)

    @property
    def executions(self) -> tuple[AnalysisExecution, ...]:
        return tuple(self.view.analysis.executions)

    @property
    def parameter_proposals(self) -> tuple[ParameterChangeProposal, ...]:
        return tuple(
            self.proposal(output.id)
            for output in self.outputs
            if isinstance(output, AnalysisParameterProposalRecordOutput)
        )

    def candidate_config(
        self,
        selection: CandidateSelection = None,
    ) -> CandidateConfig:
        return candidate_config_from_proposals(
            self.parameter_proposals,
            selection=selection,
        )

    def output(self, id: str) -> AnalysisRecordOutput:
        try:
            return next(output for output in self.outputs if output.id == id)
        except StopIteration:
            raise KeyError(f"analysis has no output: {id}") from None

    def fact(self, id: str) -> AnalysisFact:
        return self._output(id, AnalysisFactRecordOutput).content

    def fact_as[ValueT](
        self,
        id: str,
        schema: AnalysisFactSchema[ValueT],
    ) -> ValueT:
        """Validate and reconstruct one structured fact with a local type."""

        fact = self.fact(id)
        if fact.schema_id != schema.id:
            raise TypeError(
                f"analysis fact {id!r} uses schema {fact.schema_id!r}, "
                f"not {schema.id!r}"
            )
        if fact.schema_codec != schema.schema_codec:
            raise TypeError(
                f"analysis fact {id!r} uses structural schema codec "
                f"{fact.schema_codec!r}, not {schema.schema_codec!r}"
            )
        if fact.schema_hash != schema.schema_hash:
            raise TypeError(
                f"analysis fact {id!r} schema fingerprint does not match {schema.id!r}"
            )
        if fact.codec != PYTHON_JSON_CODEC:
            raise TypeError(
                f"analysis fact {id!r} uses unsupported codec {fact.codec!r}"
            )
        return schema.decode(fact.value)

    def result_as[ResultT](self, result_type: type[ResultT]) -> AnalysisResult[ResultT]:
        """Reconstruct a saved ordinary conclusion without rerunning analysis."""
        return AnalysisResult(
            self.fact_as("result", ordinary_result_schema(result_type)), self
        )

    def dataset(self, id: str) -> DerivedDataset:
        output = self._output(id, AnalysisDatasetRecordOutput)
        return self.source._load_analysis_dataset(  # pyright: ignore[reportPrivateUsage]
            self.id,
            output.content.dataset_id,
        )

    def artifact(self, id: str) -> PublishedAnalysisArtifact:
        return PublishedAnalysisArtifact(
            source=self.source,
            analysis_id=self.id,
            output=self._output(id, AnalysisArtifactRecordOutput),
        )

    def table(self, id: str) -> AnalysisTableView:
        return self._output(id, AnalysisTableRecordOutput).content

    def dataset_view_source(self, id: str) -> AnalysisPublishedDatasetViewSource:
        """Freeze a plotting source from metadata without downloading its table."""
        output = self._output(id, AnalysisDatasetRecordOutput)
        return AnalysisPublishedDatasetViewSource(
            source=AnalysisPublishedOutputReference(
                subject=self.view.analysis.subject,
                analysis_record_id=self.id,
                output_id=output.id,
            ),
            dataset=output.content,
        )

    def figure(self, id: str) -> AnalysisFigureView:
        return self._output(id, AnalysisFigureRecordOutput).content

    def proposal(self, id: str) -> ParameterChangeProposal:
        output = self._output(id, AnalysisParameterProposalRecordOutput)
        return ParameterChangeProposal.model_validate(
            self.source._analysis_record_json(  # pyright: ignore[reportPrivateUsage]
                self.id,
                output.content.proposal_id,
                expected_kind="parameter_change_proposal",
            ).content
        )

    def _output[OutputT: AnalysisRecordOutput](
        self,
        id: str,
        output_type: type[OutputT],
    ) -> OutputT:
        output = self.output(id)
        if not isinstance(output, output_type):
            raise TypeError(
                f"analysis output {id!r} is {output.kind}, not {output_type.__name__}"
            )
        return output


__all__ = ["AnalysisResult", "PublishedAnalysis", "PublishedAnalysisArtifact"]


@dataclass(frozen=True, slots=True)
class AnalysisResult[ResultT]:
    """Materialized ordinary conclusion and its authoritative publication receipt.

    ``value`` is usable after disconnect. Dataset/artifact methods on
    ``publication`` require its live connection; reopen by publication.id.
    """

    value: ResultT
    publication: PublishedAnalysis
