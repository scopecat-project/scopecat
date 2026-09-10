"""Analysis facade objects for notebook workflows."""

from __future__ import annotations

import inspect
import mimetypes
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, fields, is_dataclass, replace
from pathlib import Path, PurePosixPath
from typing import (
    Concatenate,
    Literal,
    NoReturn,
    Protocol,
    cast,
    get_args,
    get_origin,
    get_type_hints,
    overload,
)

from pydantic import BaseModel, JsonValue

from scopecat.analysis.datasets import (
    DERIVED_DATASET_CODEC,
    DerivedDataset,
    PandasIndexPolicy,
    derived_dataset,
)
from scopecat.analysis.facts import (
    ANALYSIS_FACT_SCHEMA_CODEC,
    QUANTITY_FACT_SCHEMA_HASH,
    QUANTITY_FACT_SCHEMA_ID,
    SCALAR_FACT_SCHEMA_HASH,
    SCALAR_FACT_SCHEMA_ID,
    AnalysisFactSchema,
    ordinary_result_schema,
)
from scopecat.analysis.service import (
    AnalysisArtifactOutput,
    AnalysisDatasetOutput,
    AnalysisFactOutput,
    AnalysisFigureOutput,
    AnalysisInput,
    AnalysisOutput,
    AnalysisParameterProposalOutput,
    AnalysisTableOutput,
    InterpretationAnalysisInput,
    MeasurementAnalysisInput,
    PublishedAnalysisOutputInput,
    SavedAnalysis,
)
from scopecat.api.published_analysis import PublishedAnalysis, PublishedAnalysisArtifact
from scopecat.automation.models import InterpretationOutputRef
from scopecat.config.changes import (
    parameter_change_proposal_from_updates,
)
from scopecat.config.parameter_updates import ParameterUpdate
from scopecat.kernel.content_identity import (
    content_fingerprint,
    model_wire_content_hash,
    sha256_content_hash,
    stable_content_hash,
)
from scopecat.kernel.errors import CheckFailed
from scopecat.kernel.ids import artifact_slug
from scopecat.kernel.problems import (
    LocationPathItem,
    ProblemPhase,
    model_location,
    problem,
)
from scopecat.kernel.python_source import python_source_identity
from scopecat.kernel.quantity import Quantity
from scopecat.measurements.dataset import Dataset, ExperimentResultView
from scopecat.measurements.datasets import MEASUREMENT_DATASET_CODEC
from scopecat.records.analysis import (
    ANALYSIS_ARTIFACT_CODEC,
    AnalysisArtifactRecordOutput,
    AnalysisDatasetDerivation,
    AnalysisDatasetRecordOutput,
    AnalysisDatasetViewSource,
    AnalysisExecution,
    AnalysisExecutionInput,
    AnalysisExecutionOutput,
    AnalysisExecutionOutputReference,
    AnalysisFact,
    AnalysisFactRecordOutput,
    AnalysisField,
    AnalysisFigureLayerSpec,
    AnalysisFigureProjection,
    AnalysisFigureViewSpec,
    AnalysisPublishedDatasetViewSource,
    AnalysisPublishedOutputReference,
    AnalysisTableViewSpec,
    analysis_record_id,
    is_analysis_rows,
)
from scopecat.records.config import ConfigProfileSnapshot
from scopecat.records.content import Sha256ContentHash
from scopecat.records.parameter_change import ParameterChangeProposal
from scopecat.sdk.compute import (
    PYTHON_JSON_CODEC,
    compute_capture_names_internal,
)


@dataclass(frozen=True, slots=True)
class AnalysisPlot:
    """A retained line/scatter view of one returned dataset."""

    dataset: str
    x: str
    y: str
    id: str = "figure"
    kind: Literal["line", "scatter"] = "line"
    title: str = "figure"


@dataclass(frozen=True, slots=True)
class AnalysisProducts[ResultT]:
    """An ordinary conclusion plus optional native tables and plot specifications.

    Tables accept the existing derived_dataset adapters. These values are local
    computation results, not evidence of a retained measurement or verification.
    """

    result: ResultT
    datasets: Mapping[str, object] = field(default_factory=lambda: dict[str, object]())
    plots: tuple[AnalysisPlot, ...] = ()


class _AnalysisOwner(Protocol):
    """Publication owner capabilities independent of analysis subject scope."""

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
    ) -> SavedAnalysis: ...

    def published_analysis(self, selector: str) -> PublishedAnalysis: ...


class _AnalysisRun(_AnalysisOwner, Protocol):
    """Run capabilities consumed by analysis without importing its facade."""

    @property
    def id(self) -> str: ...

    @property
    def config(self) -> ConfigProfileSnapshot: ...

    def _measurements_for_analysis(self) -> Dataset: ...


@dataclass(frozen=True)
class Analysis:
    """Declarative analysis content before one atomic publication."""

    owner: _AnalysisOwner
    proposal_run: _AnalysisRun | None
    title: str
    key: str | None = None
    step_id: str | None = None
    inputs: tuple[AnalysisInput, ...] = ()
    executions: tuple[AnalysisExecution, ...] = ()
    outputs: tuple[AnalysisOutput, ...] = ()
    parameter_proposals: tuple[ParameterChangeProposal, ...] = ()
    _execution_outputs_by_value_hash: dict[
        str,
        tuple[AnalysisExecutionOutputReference, ...],
    ] = field(
        default_factory=dict,
        repr=False,
        compare=False,
    )

    def dataset(
        self,
        id: str,
        content: object,
        *,
        fields: Mapping[str, AnalysisField] | None = None,
        index: PandasIndexPolicy = "auto",
        title: str | None = None,
        metadata: Mapping[str, object] | None = None,
        source: tuple[str, str] | None = None,
    ) -> Analysis:
        """Publish familiar dataframe or array data as one reusable run dataset.

        Pass ``source=(execution_id, output_name)`` only when equal traced
        results make automatic lineage ambiguous.
        """

        if not id.strip():
            _raise_analysis_problem(
                "analysis_dataset_id_invalid",
                "analysis dataset id must be non-empty",
                "id",
            )
        selected_id = artifact_slug(id, fallback="data")
        dataset = derived_dataset(
            content,
            fields=fields,
            index=index,
        )
        produced_by, derived_from = self._dataset_lineage(
            content,
            dataset=dataset,
            fields=fields,
            index=index,
            source=source,
        )
        return self._append_output(
            AnalysisDatasetOutput(
                kind="dataset",
                id=selected_id,
                title=title or selected_id,
                content=dataset,
                metadata=metadata or {},
                produced_by=produced_by,
                derived_from=derived_from,
            )
        )

    def fact[ValueT](
        self,
        id: str,
        value: ValueT,
        *,
        schema: AnalysisFactSchema[ValueT] | None = None,
        title: str | None = None,
        metadata: Mapping[str, object] | None = None,
        source: tuple[str, str] | None = None,
    ) -> Analysis:
        """Publish one small typed conclusion without inventing a dataset.

        Pass ``source=(execution_id, output_name)`` only when equal traced
        results make automatic lineage ambiguous.
        """

        selected_id = _analysis_output_id(id)
        if schema is not None:
            selected_schema = schema.id
            selected_schema_codec = schema.schema_codec
            selected_schema_hash = schema.schema_hash
            selected_value = schema.encode(value)
        elif isinstance(value, Quantity):
            selected_schema = QUANTITY_FACT_SCHEMA_ID
            selected_schema_codec = ANALYSIS_FACT_SCHEMA_CODEC
            selected_schema_hash = QUANTITY_FACT_SCHEMA_HASH
            selected_value = _analysis_json(value)
        elif value is None or isinstance(value, bool | int | float | str):
            selected_schema = SCALAR_FACT_SCHEMA_ID
            selected_schema_codec = ANALYSIS_FACT_SCHEMA_CODEC
            selected_schema_hash = SCALAR_FACT_SCHEMA_HASH
            selected_value = _analysis_json(value)
        else:
            raise TypeError("structured analysis facts require an AnalysisFactSchema")
        return self._append_output(
            AnalysisFactOutput(
                kind="fact",
                id=selected_id,
                title=title or selected_id,
                content=AnalysisFact(
                    schema_id=selected_schema,
                    schema_codec=selected_schema_codec,
                    schema_hash=selected_schema_hash,
                    codec=PYTHON_JSON_CODEC,
                    value=selected_value,
                ),
                metadata=metadata or {},
                produced_by=self._source_for(value, source=source),
            )
        )

    def artifact(
        self,
        id: str,
        *,
        path: str | Path | None = None,
        text: str | None = None,
        content: bytes | None = None,
        filename: str | None = None,
        media_type: str | None = None,
        title: str | None = None,
        metadata: Mapping[str, object] | None = None,
        source: tuple[str, str] | None = None,
    ) -> Analysis:
        """Publish exact file or byte content as part of this analysis.

        Pass ``source=(execution_id, output_name)`` only when equal traced
        byte results make automatic lineage ambiguous.
        """

        selected_id = _analysis_output_id(id)
        selected_sources = (path is not None, text is not None, content is not None)
        if sum(selected_sources) != 1:
            raise TypeError(
                "analysis artifact requires exactly one path, text, or content"
            )
        source_path = None if path is None else Path(path)
        if source_path is not None and not source_path.is_file():
            raise FileNotFoundError(source_path)
        selected_filename = filename or (
            source_path.name
            if source_path is not None
            else f"{selected_id}.{'txt' if text is not None else 'bin'}"
        )
        if not _is_artifact_filename(selected_filename):
            raise ValueError("analysis artifact filename must be a basename")
        selected_content = (
            source_path.read_bytes()
            if source_path is not None
            else (text.encode() if text is not None else content)
        )
        assert selected_content is not None
        selected_media_type = media_type or mimetypes.guess_type(selected_filename)[0]
        if selected_media_type is None:
            selected_media_type = (
                "text/plain" if text is not None else "application/octet-stream"
            )
        return self._append_output(
            AnalysisArtifactOutput(
                kind="artifact",
                id=selected_id,
                title=title or selected_id,
                content=selected_content,
                filename=selected_filename,
                media_type=selected_media_type,
                metadata=metadata or {},
                produced_by=self._source_for(selected_content, source=source),
            )
        )

    def table(
        self,
        *,
        dataset: str,
        id: str = "table",
        columns: Sequence[str] | None = None,
        title: str = "table",
        metadata: Mapping[str, object] | None = None,
    ) -> Analysis:
        """Publish a bounded table view of an authoritative analysis dataset."""

        source = self._dataset(dataset)
        available_columns = tuple(field.name for field in source.schema.fields)
        selected_columns = available_columns if columns is None else tuple(columns)
        unknown = set(selected_columns) - set(available_columns)
        if unknown:
            raise KeyError(
                "derived dataset has no columns: " + ", ".join(sorted(unknown))
            )
        source_ref = AnalysisDatasetViewSource(
            output_id=artifact_slug(dataset, fallback="data")
        )
        return self._append_output(
            AnalysisTableOutput(
                kind="table",
                id=_analysis_output_id(id),
                title=title,
                content=AnalysisTableViewSpec(
                    source=source_ref,
                    columns=selected_columns,
                ),
                metadata=metadata or {},
            )
        )

    def figure(
        self,
        *,
        dataset: str,
        id: str = "figure",
        kind: Literal["line", "scatter"],
        x: str,
        y: str,
        series: str | None = None,
        label: str | None = None,
        title: str = "figure",
        metadata: Mapping[str, object] | None = None,
    ) -> Analysis:
        """Publish a bounded figure view of an authoritative analysis dataset."""

        return self.figure_layers(
            layers=(
                AnalysisFigureLayerSpec(
                    id="data",
                    source=AnalysisDatasetViewSource(
                        output_id=artifact_slug(dataset, fallback="data")
                    ),
                    projection=AnalysisFigureProjection(
                        kind=kind, x=x, y=y, series=series, label=label
                    ),
                ),
            ),
            id=id,
            title=title,
            metadata=metadata,
        )

    def figure_layers(
        self,
        *,
        layers: Sequence[AnalysisFigureLayerSpec],
        id: str = "figure",
        title: str = "figure",
        metadata: Mapping[str, object] | None = None,
    ) -> Analysis:
        """Compose retained datasets without copying them into plotting outputs.

        Use a local ``AnalysisDatasetViewSource`` for a sibling dataset or
        ``published.dataset_view_source(output_id)`` for an immutable publication.
        Uncertainty projections name absolute bounds and their scientific meaning.
        """
        inputs = list(self.inputs)
        for layer in layers:
            source = layer.source
            if isinstance(source, AnalysisPublishedDatasetViewSource):
                input_ref = PublishedAnalysisOutputInput(
                    id=f"figure-{id}-{layer.id}",
                    target=source.dataset.dataset_id,
                    kind="analysis_dataset",
                    content_hash=source.dataset.content_hash,
                    codec=source.dataset.codec,
                    role="figure",
                    source=source.source,
                )
                inputs.append(input_ref)
            else:
                self._dataset(source.output_id)
        return replace(self, inputs=tuple(inputs))._append_output(
            AnalysisFigureOutput(
                kind="figure",
                id=_analysis_output_id(id),
                title=title,
                content=AnalysisFigureViewSpec(layers=tuple(layers)),
                metadata=metadata or {},
            )
        )

    def _dataset(self, id: str) -> DerivedDataset:
        selected_id = artifact_slug(id, fallback="data")
        for output in self.outputs:
            if isinstance(output, AnalysisDatasetOutput) and output.id == selected_id:
                return output.content
        raise KeyError(f"analysis has no dataset output: {selected_id}")

    def _append_output(self, output: AnalysisOutput) -> Analysis:
        if any(existing.id == output.id for existing in self.outputs):
            _raise_analysis_problem(
                "analysis_output_id_duplicated",
                f"analysis output id is duplicated: {output.id}",
                "id",
            )
        return replace(self, outputs=(*self.outputs, output))

    def _source_for(
        self,
        value: object,
        *,
        source: tuple[str, str] | None,
    ) -> AnalysisExecutionOutputReference | None:
        if source is not None:
            return AnalysisExecutionOutputReference(
                execution_id=source[0],
                output_name=source[1],
            )
        try:
            content_hash = _analysis_value_hash(value)
        except TypeError:
            return None
        candidates = self._execution_outputs_by_value_hash.get(content_hash, ())
        return candidates[0] if len(candidates) == 1 else None

    def _dataset_lineage(
        self,
        value: object,
        *,
        dataset: DerivedDataset,
        fields: Mapping[str, AnalysisField] | None,
        index: PandasIndexPolicy,
        source: tuple[str, str] | None,
    ) -> tuple[
        AnalysisExecutionOutputReference | None,
        AnalysisDatasetDerivation | None,
    ]:
        exact = self._source_for(dataset, source=source)
        if exact is not None and self._execution_output_matches(
            exact,
            value=dataset,
        ):
            return exact, None
        traced_source = exact or self._source_for(value, source=source)
        if traced_source is None:
            return None, None
        if not self._execution_output_matches(traced_source, value=value):
            raise ValueError(
                "analysis dataset source content does not match its execution output"
            )
        return None, AnalysisDatasetDerivation(
            source=traced_source,
            source_kind=_analysis_dataset_source_kind(value),
            fields=dict(fields or {}),
            index=index,
        )

    def _execution_output_matches(
        self,
        reference: AnalysisExecutionOutputReference,
        *,
        value: object,
    ) -> bool:
        execution_output = next(
            (
                output
                for execution in self.executions
                if execution.id == reference.execution_id
                for output in execution.outputs
                if output.name == reference.output_name
            ),
            None,
        )
        if execution_output is None:
            raise ValueError("analysis source must identify a traced execution output")
        codec, content_hash = _analysis_value_identity(value)
        return (
            execution_output.codec == codec
            and execution_output.content_hash == content_hash
        )

    @property
    def analysis_key(self) -> str:
        return _analysis_key(self.key, self.title)

    def propose(
        self,
        proposal_id: str,
        *updates: ParameterUpdate,
        reason: str = "",
        confidence: float | None = None,
        evidence: Sequence[str] = (),
    ) -> Analysis:
        if not proposal_id.strip():
            _raise_analysis_problem(
                "analysis_parameter_proposal_id_invalid",
                "analysis parameter proposal id must be non-empty",
                "proposal_id",
            )
        if not updates:
            _raise_analysis_problem(
                "analysis_parameter_proposal_empty",
                "analysis parameter proposal requires at least one update",
                "updates",
            )
        if confidence is not None and not 0 <= confidence <= 1:
            _raise_analysis_problem(
                "analysis_parameter_proposal_confidence_invalid",
                "analysis parameter proposal confidence must be between 0 and 1",
                "confidence",
            )
        evidence_output_ids = tuple(_analysis_output_id(item) for item in evidence)
        if len(evidence_output_ids) != len(set(evidence_output_ids)):
            _raise_analysis_problem(
                "analysis_parameter_proposal_evidence_duplicated",
                "analysis parameter proposal evidence ids must be unique",
                "evidence",
            )
        outputs_by_id = {output.id: output for output in self.outputs}
        for evidence_id in evidence_output_ids:
            output = outputs_by_id.get(evidence_id)
            if output is None:
                _raise_analysis_problem(
                    "analysis_parameter_proposal_evidence_unknown",
                    f"analysis parameter proposal evidence is unknown: {evidence_id}",
                    "evidence",
                )
            if not isinstance(
                output,
                AnalysisFactOutput | AnalysisDatasetOutput | AnalysisArtifactOutput,
            ):
                _raise_analysis_problem(
                    "analysis_parameter_proposal_evidence_not_authoritative",
                    "analysis parameter proposal evidence must identify a fact, "
                    "dataset, or artifact output",
                    "evidence",
                )
        selected_id = artifact_slug(proposal_id, fallback="analysis")
        if any(proposal.id == selected_id for proposal in self.parameter_proposals):
            _raise_analysis_problem(
                "analysis_parameter_proposal_id_duplicated",
                f"analysis parameter proposal id is duplicated: {selected_id}",
                "proposal_id",
            )
        try:
            proposal = parameter_change_proposal_from_updates(
                source_run_id=self._required_proposal_run().id,
                source_config=self._required_proposal_run().config,
                analysis_title=self.title,
                analysis_record_id=analysis_record_id(self.analysis_key, 1),
                proposal_id=proposal_id,
                updates=updates,
                reason=reason,
                confidence=confidence,
                evidence_output_ids=evidence_output_ids,
            )
        except (TypeError, ValueError) as error:
            _raise_analysis_problem(
                "analysis_parameter_proposal_invalid",
                str(error),
                "updates",
            )
        output = AnalysisParameterProposalOutput(
            kind="parameter_change_proposal",
            id=proposal.id,
            title=selected_id,
            content=proposal,
            metadata={},
        )
        analysis = self._append_output(output)
        return replace(
            analysis,
            parameter_proposals=(*self.parameter_proposals, proposal),
        )

    def save(self) -> PublishedAnalysis:
        saved = self.owner.save_analysis(
            title=self.title,
            analysis_key=self.analysis_key,
            step_id=self.step_id,
            inputs=self.inputs,
            executions=self.executions,
            outputs=self.outputs,
            parameter_proposals=self.parameter_proposals,
        )
        return self.owner.published_analysis(saved.record.id)

    def _required_proposal_run(self) -> _AnalysisRun:
        if self.proposal_run is None:
            raise TypeError(
                "project analysis cannot propose parameter changes without "
                "a single source configuration"
            )
        return self.proposal_run


@dataclass(frozen=True)
class AnalysisContext:
    owner: _AnalysisOwner | None = None
    run: _AnalysisRun | None = None
    default_title: str = "analysis"
    default_key: str | None = None
    step_id: str | None = None
    _executions: list[AnalysisExecution] = field(
        default_factory=list,
        repr=False,
        compare=False,
    )
    _execution_ids: dict[str, int] = field(
        default_factory=dict,
        repr=False,
        compare=False,
    )
    _accessed_inputs: dict[str, AnalysisInput] = field(
        default_factory=dict,
        repr=False,
        compare=False,
    )
    _execution_outputs_by_value_hash: dict[
        str,
        tuple[AnalysisExecutionOutputReference, ...],
    ] = field(
        default_factory=dict,
        repr=False,
        compare=False,
    )
    _execution_targets_by_value_hash: dict[str, tuple[str, ...]] = field(
        default_factory=dict,
        repr=False,
        compare=False,
    )
    _execution_targets_by_object_id: dict[int, tuple[object, str]] = field(
        default_factory=dict,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        if self.owner is None and self.run is None:
            raise TypeError("analysis context requires a publication owner")

    @property
    def _owner(self) -> _AnalysisOwner:
        owner = self.owner or self.run
        assert owner is not None
        return owner

    @property
    def config(self) -> ConfigProfileSnapshot:
        return self._required_run().config

    @property
    def run_id(self) -> str:
        """Return the subject run ID for a run-scoped analysis context."""

        return self._required_run().id

    def measurements(
        self,
        run: _AnalysisRun | None = None,
        *,
        id: str | None = None,
        role: str = "data",
        title: str | None = None,
        metadata: Mapping[str, object] | None = None,
    ) -> Dataset:
        """Load one run's measurements and freeze them as a named input."""

        selected_run = run or self._required_run()
        dataset = selected_run._measurements_for_analysis()  # pyright: ignore[reportPrivateUsage]
        input_id = artifact_slug(id or dataset.entry.id, fallback="data")
        input_ref = MeasurementAnalysisInput(
            id=input_id,
            run_id=selected_run.id,
            target=dataset.entry.id,
            kind="measurement_dataset",
            content_hash=dataset.entry.content_hash,
            codec=MEASUREMENT_DATASET_CODEC,
            role=role,
            title=title or dataset.entry.id,
            metadata=metadata,
        )
        existing = self._accessed_inputs.setdefault(input_id, input_ref)
        if existing != input_ref:
            raise ValueError(f"analysis input id is already bound: {input_id}")
        self._record_input_value(dataset, target=input_id)
        return dataset

    def analysis_dataset(
        self,
        analysis: str | PublishedAnalysis,
        output: str,
        *,
        run: _AnalysisRun | None = None,
        id: str | None = None,
        role: str = "data",
        title: str | None = None,
        metadata: Mapping[str, object] | None = None,
    ) -> DerivedDataset:
        """Load a published analysis dataset and retain its exact revision."""

        if not role.strip():
            _raise_analysis_problem(
                "analysis_input_role_invalid",
                "analysis input role must be a non-empty string",
                "role",
            )
        published = self._resolve_published_analysis(analysis, run=run)
        selected = published.output(output)
        if not isinstance(selected, AnalysisDatasetRecordOutput):
            raise TypeError(
                f"analysis output {published.id!r}:{output!r} is not a dataset"
            )
        dataset = published.dataset(output)
        reference = selected.content
        input_id = artifact_slug(id or reference.dataset_id, fallback="data")
        source = AnalysisPublishedOutputReference(
            subject=published.view.analysis.subject,
            analysis_record_id=published.id,
            output_id=selected.id,
        )
        input_ref = PublishedAnalysisOutputInput(
            id=input_id,
            target=reference.dataset_id,
            kind="analysis_dataset",
            content_hash=reference.content_hash,
            codec=reference.codec,
            role=role,
            title=title or selected.title,
            metadata=metadata,
            source=source,
        )
        existing = self._accessed_inputs.setdefault(input_id, input_ref)
        if existing != input_ref:
            raise ValueError(f"analysis input id is already bound: {input_id}")
        self._record_input_value(dataset, target=input_id)
        return dataset

    @overload
    def analysis_fact(
        self,
        analysis: str | PublishedAnalysis,
        output: str,
        *,
        schema: None = None,
        run: _AnalysisRun | None = None,
        id: str | None = None,
        role: str = "data",
        title: str | None = None,
        metadata: Mapping[str, object] | None = None,
    ) -> AnalysisFact: ...

    @overload
    def analysis_fact[ValueT](
        self,
        analysis: str | PublishedAnalysis,
        output: str,
        *,
        schema: AnalysisFactSchema[ValueT],
        run: _AnalysisRun | None = None,
        id: str | None = None,
        role: str = "data",
        title: str | None = None,
        metadata: Mapping[str, object] | None = None,
    ) -> ValueT: ...

    def analysis_fact[ValueT](
        self,
        analysis: str | PublishedAnalysis,
        output: str,
        *,
        schema: AnalysisFactSchema[ValueT] | None = None,
        run: _AnalysisRun | None = None,
        id: str | None = None,
        role: str = "data",
        title: str | None = None,
        metadata: Mapping[str, object] | None = None,
    ) -> AnalysisFact | ValueT:
        """Load an exact published fact and retain it as an analysis input."""

        published = self._resolve_published_analysis(analysis, run=run)
        selected = published.output(output)
        if not isinstance(selected, AnalysisFactRecordOutput):
            raise TypeError(
                f"analysis output {published.id!r}:{output!r} is not a fact"
            )
        fact = selected.content
        input_id = artifact_slug(id or selected.id, fallback="fact")
        self._retain_input(
            PublishedAnalysisOutputInput(
                id=input_id,
                target=selected.id,
                kind="analysis_fact",
                content_hash=f"sha256:{model_wire_content_hash(fact)}",
                codec=fact.codec,
                role=role,
                title=title or selected.title,
                metadata=metadata,
                source=AnalysisPublishedOutputReference(
                    subject=published.view.analysis.subject,
                    analysis_record_id=published.id,
                    output_id=selected.id,
                ),
            )
        )
        value = fact if schema is None else published.fact_as(output, schema)
        self._record_input_value(value, target=input_id)
        return value

    def interpretation[ValueT](
        self,
        ref: InterpretationOutputRef,
        *,
        schema: AnalysisFactSchema[ValueT],
        id: str | None = None,
        title: str | None = None,
    ) -> ValueT:
        """Consume a typed judgment and retain its exact procedure provenance.

        The daemon verifies the request and complete response at publication.
        Actor labels are provenance, not authentication.
        """
        value = schema.decode(ref.response.value)
        source = ref.analysis_reference
        input_id = artifact_slug(id or ref.step_key, fallback="interpretation")
        self._retain_input(
            InterpretationAnalysisInput(
                id=input_id,
                target=source.step_key,
                kind="interpretation",
                content_hash=source.response_hash,
                codec="scopecat.interpretation-response.v1",
                role="decision",
                title=title,
                source=source,
            )
        )
        self._record_input_value(value, target=input_id)
        return value

    def analysis_artifact(
        self,
        analysis: str | PublishedAnalysis,
        output: str,
        *,
        run: _AnalysisRun | None = None,
        id: str | None = None,
        role: str = "data",
        title: str | None = None,
        metadata: Mapping[str, object] | None = None,
    ) -> PublishedAnalysisArtifact:
        """Load an exact published artifact and retain it as an analysis input."""

        published = self._resolve_published_analysis(analysis, run=run)
        selected = published.output(output)
        if not isinstance(selected, AnalysisArtifactRecordOutput):
            raise TypeError(
                f"analysis output {published.id!r}:{output!r} is not an artifact"
            )
        artifact = published.artifact(output)
        reference = selected.content
        input_id = artifact_slug(id or selected.id, fallback="artifact")
        self._retain_input(
            PublishedAnalysisOutputInput(
                id=input_id,
                target=reference.artifact_id,
                kind="analysis_artifact",
                content_hash=reference.content_hash,
                codec=ANALYSIS_ARTIFACT_CODEC,
                role=role,
                title=title or selected.title,
                metadata=metadata,
                source=AnalysisPublishedOutputReference(
                    subject=published.view.analysis.subject,
                    analysis_record_id=published.id,
                    output_id=selected.id,
                ),
            )
        )
        self._record_input_value(artifact, target=input_id)
        return artifact

    def _resolve_published_analysis(
        self,
        analysis: str | PublishedAnalysis,
        *,
        run: _AnalysisRun | None,
    ) -> PublishedAnalysis:
        if isinstance(analysis, PublishedAnalysis):
            if run is not None:
                raise TypeError("run is only valid when selecting an analysis by name")
            return analysis
        return (run or self._owner).published_analysis(analysis)

    def _retain_input(self, input_ref: AnalysisInput) -> None:
        existing = self._accessed_inputs.setdefault(input_ref.id, input_ref)
        if existing != input_ref:
            raise ValueError(f"analysis input id is already bound: {input_ref.id}")

    @overload
    def trace[ResultT](
        self,
        id: str | None = None,
        *,
        fn: Callable[..., ResultT],
        inputs: None = None,
        **input_bindings: object,
    ) -> ResultT: ...

    @overload
    def trace[ResultT](
        self,
        id: str | None = None,
        *,
        fn: Callable[..., ResultT],
        inputs: Mapping[str, object],
        **input_bindings: object,
    ) -> ResultT: ...

    def trace(
        self,
        id: str | None = None,
        *,
        fn: Callable[..., object],
        inputs: Mapping[str, object] | None = None,
        **input_bindings: object,
    ) -> object:
        """Run eager Python while retaining optional execution evidence.

        The function receives its named inputs directly and returns one native
        value. Scopecat records dataset/input identities, captured nonlocal
        names, a diagnostic local-Python implementation identity, and encoded
        output identity. It does not publish, cache, replay, batch, or remotely
        deploy the call. A later fact, dataset, or artifact publication links
        to the execution when its content matches exactly; first-party dataset
        normalization instead records a derived relation.
        """

        duplicate_inputs = set(inputs or {}) & set(input_bindings)
        if duplicate_inputs:
            rendered = ", ".join(sorted(duplicate_inputs))
            raise TypeError(f"analysis trace inputs were bound twice: {rendered}")
        selected_inputs = {**(inputs or {}), **input_bindings}
        if not selected_inputs:
            raise TypeError("analysis trace requires at least one named input")
        if not any(
            isinstance(value, Dataset | ExperimentResultView)
            or _analysis_dataset_value(value) is not None
            for value in selected_inputs.values()
        ):
            raise TypeError("analysis trace requires at least one dataset input")
        execution_id = self._allocate_execution_id(
            id or getattr(fn, "__name__", "analysis-execution")
        )
        captures = compute_capture_names_internal(fn)
        input_provenance = tuple(
            _analysis_execution_input(
                name,
                value,
                execution_target=self._execution_target_for(value),
            )
            for name, value in selected_inputs.items()
        )
        input_names = tuple(selected_inputs)
        for provenance in input_provenance:
            if provenance.kind != "measurement_dataset":
                continue
            if provenance.target in self._accessed_inputs:
                continue
            run = self._required_run()
            self._accessed_inputs.setdefault(
                provenance.target,
                MeasurementAnalysisInput(
                    id=provenance.target,
                    run_id=run.id,
                    target=provenance.target,
                    kind="measurement_dataset",
                    content_hash=provenance.content_hash,
                    codec=provenance.codec,
                    role="data",
                    title=provenance.target,
                ),
            )

        result = fn(**selected_inputs)
        traced_values, execution_outputs = _analysis_trace_outputs(
            result,
            execution_id=execution_id,
        )
        execution = AnalysisExecution(
            id=execution_id,
            implementation=f"python:{execution_id}",
            deterministic=False,
            inputs=input_names,
            input_bindings=input_provenance,
            outputs=execution_outputs,
            captures=captures,
            access="full",
            metadata={},
        )
        self._executions.append(execution)
        for (output_name, value), execution_output in zip(
            traced_values,
            execution.outputs,
            strict=True,
        ):
            try:
                value_codec, value_hash = _analysis_value_identity(value)
            except TypeError:
                continue
            if (
                value_codec != execution_output.codec
                or value_hash != execution_output.content_hash
            ):
                continue
            reference = AnalysisExecutionOutputReference(
                execution_id=execution.id,
                output_name=output_name,
            )
            self._record_execution_value(
                content_hash=value_hash,
                reference=reference,
            )
        return result

    def _allocate_execution_id(self, requested: str) -> str:
        base = artifact_slug(requested, fallback="analysis-execution")
        count = self._execution_ids.get(base, 0) + 1
        self._execution_ids[base] = count
        return base if count == 1 else f"{base}-{count}"

    def _execution_target_for(self, value: object) -> str | None:
        retained_target = self._execution_targets_by_object_id.get(id(value))
        if retained_target is not None and retained_target[0] is value:
            return retained_target[1]
        try:
            content_hash = _analysis_value_hash(value)
        except TypeError:
            return None
        targets = self._execution_targets_by_value_hash.get(content_hash, ())
        return targets[0] if len(targets) == 1 else None

    def _record_input_value(self, value: object, *, target: str) -> None:
        self._execution_targets_by_object_id[id(value)] = (value, target)
        try:
            content_hash = _analysis_value_hash(value)
        except TypeError:
            return
        targets = self._execution_targets_by_value_hash.get(content_hash, ())
        if target not in targets:
            self._execution_targets_by_value_hash[content_hash] = (*targets, target)

    def _record_execution_value(
        self,
        *,
        content_hash: str,
        reference: AnalysisExecutionOutputReference,
    ) -> None:
        references = self._execution_outputs_by_value_hash.get(content_hash, ())
        if reference not in references:
            self._execution_outputs_by_value_hash[content_hash] = (
                *references,
                reference,
            )
        target = f"execution:{reference.execution_id}:{reference.output_name}"
        targets = self._execution_targets_by_value_hash.get(content_hash, ())
        if target not in targets:
            self._execution_targets_by_value_hash[content_hash] = (*targets, target)

    def result(self, title: str | None = None, *, key: str | None = None) -> Analysis:
        """Start one publication with every input accessed through this context."""

        return Analysis(
            owner=self._owner,
            proposal_run=self.run,
            title=self.default_title if title is None else title,
            key=key or self.default_key,
            step_id=self.step_id,
            inputs=tuple(self._accessed_inputs.values()),
            executions=tuple(self._executions),
            _execution_outputs_by_value_hash=dict(
                self._execution_outputs_by_value_hash
            ),
        )

    def _required_run(self) -> _AnalysisRun:
        if self.run is None:
            raise TypeError("project analysis requires an explicit run input")
        return self.run


def _analysis_trace_outputs(
    result: object,
    *,
    execution_id: str,
) -> tuple[
    tuple[tuple[str, object], ...],
    tuple[AnalysisExecutionOutput, ...],
]:
    if isinstance(result, AnalysisProducts):
        products = cast("AnalysisProducts[object]", result)
        values = (("result", products.result), *products.datasets.items())
        if "result" in products.datasets:
            raise ValueError("analysis dataset name 'result' is reserved")
        outputs = tuple(
            _analysis_trace_outputs(value, execution_id=name)[1][0]
            for name, value in values
        )
        return values, outputs
    artifact_output = _analysis_artifact_value(result)
    dataset_output = _analysis_dataset_value(result)
    if artifact_output is not None:
        output_codec = ANALYSIS_ARTIFACT_CODEC
        output_hash = sha256_content_hash(artifact_output)
        output_kind: Literal["derived_dataset", "artifact", "value"] = "artifact"
    elif dataset_output is not None:
        output_codec = DERIVED_DATASET_CODEC
        output_hash = sha256_content_hash(dataset_output.to_arrow_ipc())
        output_kind = "derived_dataset"
    else:
        encoded = _analysis_json(result)
        output_codec = PYTHON_JSON_CODEC
        output_hash = f"sha256:{stable_content_hash(encoded)}"
        output_kind = "value"
    return ((execution_id, result),), (
        AnalysisExecutionOutput(
            name=execution_id,
            kind=output_kind,
            content_hash=output_hash,
            codec=output_codec,
        ),
    )


def _analysis_execution_input(
    name: str,
    value: object,
    *,
    execution_target: str | None,
) -> AnalysisExecutionInput:
    artifact = _analysis_artifact_value(value)
    if artifact is not None:
        content_hash = sha256_content_hash(artifact)
        return AnalysisExecutionInput(
            name=name,
            kind="artifact",
            target=execution_target or f"content:{content_hash}",
            content_hash=content_hash,
            codec=ANALYSIS_ARTIFACT_CODEC,
        )
    derived_value = _analysis_dataset_value(value)
    if derived_value is not None:
        content_hash = sha256_content_hash(derived_value.to_arrow_ipc())
        return AnalysisExecutionInput(
            name=name,
            kind="derived_dataset",
            target=execution_target or f"content:{content_hash}",
            content_hash=content_hash,
            codec=DERIVED_DATASET_CODEC,
        )
    dataset = (
        cast("ExperimentResultView[object]", value).dataset
        if isinstance(value, ExperimentResultView)
        else value
    )
    if isinstance(dataset, Dataset):
        return AnalysisExecutionInput(
            name=name,
            kind="measurement_dataset",
            target=execution_target or dataset.entry.id,
            content_hash=dataset.entry.content_hash,
            codec=MEASUREMENT_DATASET_CODEC,
        )
    encoded = _analysis_json(cast("object", value))
    content_hash = f"sha256:{stable_content_hash(encoded)}"
    return AnalysisExecutionInput(
        name=name,
        kind="value",
        target=execution_target or f"inline:{name}:{content_hash}",
        content_hash=content_hash,
        codec=PYTHON_JSON_CODEC,
        value=None if execution_target is not None else encoded,
    )


def _analysis_value_hash(value: object) -> str:
    return _analysis_value_identity(value)[1]


def _analysis_value_identity(value: object) -> tuple[str, str]:
    artifact = _analysis_artifact_value(value)
    if artifact is not None:
        return ANALYSIS_ARTIFACT_CODEC, sha256_content_hash(artifact)
    dataset = _analysis_dataset_value(value)
    if dataset is not None:
        return DERIVED_DATASET_CODEC, sha256_content_hash(dataset.to_arrow_ipc())
    return PYTHON_JSON_CODEC, f"sha256:{stable_content_hash(_analysis_json(value))}"


def _analysis_dataset_value(value: object) -> DerivedDataset | None:
    if isinstance(value, DerivedDataset):
        return value
    owner = type(value).__module__.partition(".")[0]
    if owner in {"pandas", "polars", "pyarrow", "xarray"}:
        return derived_dataset(value)
    if is_analysis_rows(value):
        return derived_dataset(value)
    return None


def _analysis_artifact_value(value: object) -> bytes | None:
    if isinstance(value, bytes):
        return value
    if isinstance(value, Path):
        if not value.is_file():
            raise FileNotFoundError(value)
        return value.read_bytes()
    return None


def _analysis_dataset_source_kind(
    value: object,
) -> Literal["arrow", "pandas", "polars", "xarray"]:
    owner = type(value).__module__.partition(".")[0]
    if owner == "pyarrow":
        return "arrow"
    if owner in {"pandas", "polars", "xarray"}:
        return cast("Literal['pandas', 'polars', 'xarray']", owner)
    raise TypeError("analysis dataset derivations require native dataset content")


def _analysis_json(value: object) -> JsonValue:
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, Quantity):
        return {"value": _analysis_json(value.value), "unit": value.unit}
    if isinstance(value, BaseModel):
        return cast("JsonValue", value.model_dump(mode="json"))
    if is_dataclass(value) and not isinstance(value, type):
        return {
            member.name: _analysis_json(cast("object", getattr(value, member.name)))
            for member in fields(value)
        }
    if isinstance(value, Mapping):
        mapping = cast("Mapping[object, object]", value)
        if any(not isinstance(key, str) for key in mapping):
            raise TypeError("analysis derived data mappings require string keys")
        return {cast("str", key): _analysis_json(item) for key, item in mapping.items()}
    if isinstance(value, Sequence):
        return [_analysis_json(item) for item in value]
    tolist = getattr(value, "tolist", None)
    if callable(tolist):
        return _analysis_json(tolist())
    item = getattr(value, "item", None)
    if callable(item):
        return _analysis_json(item())
    raise TypeError(
        f"analysis trace output {type(value).__qualname__} is not JSON encodable"
    )


class AnalysisStep(Protocol):
    @property
    def id(self) -> str: ...

    def run(self, context: AnalysisContext) -> Analysis: ...


type AnalysisFunction = Callable[..., Analysis]


@dataclass(frozen=True, slots=True, repr=False)
class AnalysisInvocation:
    """One configured function-backed analysis step."""

    id: str
    _definition: AnalysisFunction
    arguments: tuple[tuple[str, object], ...]
    _source_definition: Callable[..., object] | None = None

    @property
    def implementation_fingerprint(self) -> Sha256ContentHash:
        """Identify the exact Python analysis implementation used by automation."""

        definition = self._source_definition or self._definition
        identity = {
            "codec": "scopecat.analysis-implementation.v1",
            "id": self.id,
            **python_source_identity(
                definition,
                label="analysis implementation",
            ),
            "defaults": content_fingerprint(definition.__defaults__),
            "keyword_defaults": content_fingerprint(definition.__kwdefaults__),
            "closure": content_fingerprint(
                inspect.getclosurevars(definition).nonlocals
            ),
        }
        return f"sha256:{stable_content_hash(identity)}"

    def run(self, context: AnalysisContext) -> Analysis:
        """Evaluate the analysis function against its declared inputs."""

        return self._definition(context, **dict(self.arguments))


@dataclass(frozen=True, slots=True, repr=False)
class AnalysisDefinition[**P]:
    """A reusable analysis function retaining its configuration signature."""

    id: str
    _definition: Callable[Concatenate[AnalysisContext, P], Analysis]
    _signature: inspect.Signature

    @property
    def __wrapped__(self) -> Callable[Concatenate[AnalysisContext, P], Analysis]:
        return self._definition

    @property
    def __name__(self) -> str:
        return self._definition.__name__

    @property
    def __signature__(self) -> inspect.Signature:
        return self._signature

    def __call__(self, *args: P.args, **kwargs: P.kwargs) -> AnalysisInvocation:
        """Bind analysis configuration without attaching it to a run yet."""

        bound = self._signature.bind(*args, **kwargs)
        return AnalysisInvocation(
            id=self.id,
            _definition=cast("AnalysisFunction", self._definition),
            arguments=tuple(bound.arguments.items()),
        )


@dataclass(frozen=True, slots=True)
class AnalysisFunctionDefinition[**P, ResultT]:
    """Registered ordinary function; call ``function`` for unretained local use."""

    id: str
    function: Callable[Concatenate[Dataset, P], ResultT]
    _signature: inspect.Signature
    result_type: type[object]

    def __call__(self, *args: P.args, **kwargs: P.kwargs) -> AnalysisInvocation:
        bound = self._signature.bind(*args, **kwargs)
        bound.apply_defaults()
        definition = self.function
        data_name = next(iter(inspect.signature(definition).parameters))

        def execute(context: AnalysisContext, **arguments: object) -> Analysis:
            inputs = {data_name: context.measurements(), **arguments}
            value = context.trace(fn=definition, inputs=inputs)
            result = context.result(title=self.id)
            if isinstance(value, AnalysisProducts):
                products = cast("AnalysisProducts[object]", value)
                result = _ordinary_fact(result, products.result, self.result_type)
                for name, data in products.datasets.items():
                    result = result.dataset(name, data)
                for plot in products.plots:
                    result = result.figure(
                        dataset=plot.dataset,
                        id=plot.id,
                        kind=plot.kind,
                        x=plot.x,
                        y=plot.y,
                        title=plot.title,
                    )
                return result
            return _ordinary_fact(result, value, self.result_type)

        return AnalysisInvocation(
            id=self.id,
            _definition=execute,
            arguments=tuple(bound.arguments.items()),
            _source_definition=definition,
        )


def _ordinary_fact(
    analysis: Analysis, value: object, result_type: type[object]
) -> Analysis:
    if is_dataclass(value) and not isinstance(value, type):
        return analysis.fact(
            "result", value, schema=ordinary_result_schema(result_type)
        )
    raise TypeError(
        "ordinary analysis must return a declared dataclass or AnalysisProducts"
    )


@overload
def analysis_function[**P, ResultT](
    definition: Callable[Concatenate[Dataset, P], ResultT],
    /,
    *,
    id: str | None = None,
) -> AnalysisFunctionDefinition[P, ResultT]: ...


@overload
def analysis_function[**P, ResultT](
    definition: None = None,
    /,
    *,
    id: str | None = None,
) -> Callable[
    [Callable[Concatenate[Dataset, P], ResultT]], AnalysisFunctionDefinition[P, ResultT]
]: ...


def analysis_function[**P, ResultT](
    definition: Callable[Concatenate[Dataset, P], ResultT] | None = None,
    /,
    *,
    id: str | None = None,
) -> (
    AnalysisFunctionDefinition[P, ResultT]
    | Callable[
        [Callable[Concatenate[Dataset, P], ResultT]],
        AnalysisFunctionDefinition[P, ResultT],
    ]
):
    """Register a Dataset → dataclass function for retained author-worker analysis.

    Keep functions/helpers in configured author source. Direct local calls do not
    claim complete source capture. Rejected fits should return a declared status
    and None candidate; programming/input errors raise normally.
    """

    def decorate(
        fn: Callable[Concatenate[Dataset, P], ResultT],
    ) -> AnalysisFunctionDefinition[P, ResultT]:
        signature = inspect.signature(fn)
        parameters = tuple(signature.parameters.values())
        hints = cast("Mapping[str, object]", get_type_hints(fn))
        if not parameters or hints.get(parameters[0].name) is not Dataset:
            raise TypeError("ordinary analysis requires a Dataset first parameter")
        if any(
            p.kind
            not in (
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                inspect.Parameter.KEYWORD_ONLY,
            )
            for p in parameters
        ):
            raise TypeError("ordinary analysis requires named parameters")
        result_type = hints.get("return")
        if get_origin(result_type) is AnalysisProducts:
            result_type = cast("object", get_args(result_type)[0])
        if not isinstance(result_type, type):
            raise TypeError(
                "ordinary analysis requires a declared dataclass return type"
            )
        ordinary_result_schema(result_type)
        return AnalysisFunctionDefinition(
            id=id or f"{fn.__module__}.{fn.__qualname__}",
            function=fn,
            result_type=result_type,
            _signature=signature.replace(parameters=parameters[1:]),
        )

    return decorate(definition) if definition is not None else decorate


@overload
def analysis_step[**P](
    definition: Callable[Concatenate[AnalysisContext, P], Analysis],
    /,
    *,
    id: str | None = None,
) -> AnalysisDefinition[P]: ...


@overload
def analysis_step[**P](
    definition: None = None,
    /,
    *,
    id: str | None = None,
) -> Callable[
    [Callable[Concatenate[AnalysisContext, P], Analysis]],
    AnalysisDefinition[P],
]: ...


def analysis_step[**P](
    definition: Callable[Concatenate[AnalysisContext, P], Analysis] | None = None,
    /,
    *,
    id: str | None = None,
) -> (
    AnalysisDefinition[P]
    | Callable[
        [Callable[Concatenate[AnalysisContext, P], Analysis]],
        AnalysisDefinition[P],
    ]
):
    """Define a reusable analysis step from a typed Python function."""

    def decorate(
        fn: Callable[Concatenate[AnalysisContext, P], Analysis],
    ) -> AnalysisDefinition[P]:
        return _analysis_definition(fn, id=id)

    return decorate(definition) if definition is not None else decorate


def _analysis_definition[**P](
    fn: Callable[Concatenate[AnalysisContext, P], Analysis],
    *,
    id: str | None,
) -> AnalysisDefinition[P]:
    signature = inspect.signature(fn)
    parameters = tuple(signature.parameters.values())
    if not parameters or parameters[0].kind not in (
        inspect.Parameter.POSITIONAL_ONLY,
        inspect.Parameter.POSITIONAL_OR_KEYWORD,
    ):
        raise TypeError("analysis functions require AnalysisContext first")
    for parameter in parameters[1:]:
        if parameter.kind in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        ):
            raise TypeError("analysis configuration requires named parameters")
    hints = cast("Mapping[str, object]", get_type_hints(fn))
    context_annotation = hints.get(
        parameters[0].name,
        cast("object", parameters[0].annotation),
    )
    if context_annotation is not AnalysisContext:
        raise TypeError("analysis functions require an AnalysisContext annotation")
    return_annotation = hints.get(
        "return",
        cast("object", signature.return_annotation),
    )
    if return_annotation is not Analysis:
        raise TypeError("analysis functions must return Analysis")
    selected_id = id or f"{fn.__module__}.{fn.__qualname__}"
    if not selected_id.strip():
        raise ValueError("analysis id must be non-empty")
    return AnalysisDefinition(
        id=selected_id,
        _definition=fn,
        _signature=signature.replace(
            parameters=parameters[1:],
            return_annotation=AnalysisInvocation,
        ),
    )


def _analysis_key(key: str | None, title: str) -> str:
    selected = key if key is not None else title
    if not selected.strip():
        _raise_analysis_problem(
            "analysis_key_invalid",
            "analysis key must be a non-empty string",
            "key",
        )
    return artifact_slug(selected, fallback="analysis")


def _analysis_output_id(value: str) -> str:
    if not value.strip():
        _raise_analysis_problem(
            "analysis_output_id_invalid",
            "analysis output id must be a non-empty string",
            "id",
        )
    return artifact_slug(value, fallback="output")


def _is_artifact_filename(filename: str) -> bool:
    if not filename or "\\" in filename:
        return False
    path = PurePosixPath(filename)
    return path.name == filename and not path.is_absolute() and ".." not in path.parts


def _raise_analysis_problem(
    code: str,
    message: str,
    *path: LocationPathItem,
) -> NoReturn:
    raise CheckFailed(
        [
            problem(
                code,
                message,
                phase=ProblemPhase.ANALYSIS,
                location=model_location("analysis", *path),
            )
        ]
    )


__all__ = [
    "Analysis",
    "AnalysisContext",
    "AnalysisDefinition",
    "AnalysisFactSchema",
    "AnalysisField",
    "AnalysisFunctionDefinition",
    "AnalysisInput",
    "AnalysisInvocation",
    "AnalysisOutput",
    "AnalysisPlot",
    "AnalysisProducts",
    "AnalysisStep",
    "DerivedDataset",
    "analysis_function",
    "analysis_step",
]
