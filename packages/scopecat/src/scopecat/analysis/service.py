"""Application use cases for authoring and persisting run analyses."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Literal, NoReturn

from scopecat.analysis.dataset_wire import DerivedDatasetSchema
from scopecat.analysis.datasets import (
    DERIVED_DATASET_CODEC,
    DERIVED_DATASET_MEDIA_TYPE,
    DerivedDataset,
)
from scopecat.analysis.figure_views import figure_layer_budget, project_figure_layers
from scopecat.analysis.repository import (
    AnalysisPublication,
    AnalysisRepository,
)
from scopecat.config.changes import (
    load_parameter_change_proposal,
    parameter_change_proposal_record_ref,
    prepare_parameter_change_proposal_contents,
)
from scopecat.kernel.content_identity import (
    content_fingerprint,
    model_wire_content_hash,
    sha256_content_hash,
    stable_content_hash,
)
from scopecat.kernel.errors import CheckFailed, NotFound
from scopecat.kernel.ids import artifact_slug
from scopecat.kernel.problems import (
    LocationPathItem,
    ProblemPhase,
    model_location,
    problem,
)
from scopecat.measurements.datasets import MEASUREMENT_DATASET_CODEC
from scopecat.project_state import ProjectStateServices
from scopecat.records.analysis import (
    ANALYSIS_ARTIFACT_CODEC,
    AnalysisArtifactRecordOutput,
    AnalysisArtifactReference,
    AnalysisDatasetDerivation,
    AnalysisDatasetRecordOutput,
    AnalysisDatasetReference,
    AnalysisDatasetViewSource,
    AnalysisExecution,
    AnalysisExecutionOutputReference,
    AnalysisFact,
    AnalysisFactRecordOutput,
    AnalysisFigureLayerSpec,
    AnalysisFigureProjection,
    AnalysisFigureRecordOutput,
    AnalysisFigureView,
    AnalysisFigureViewSpec,
    AnalysisInterpretationReference,
    AnalysisParameterProposalRecordOutput,
    AnalysisParameterProposalReference,
    AnalysisPublishedDatasetViewSource,
    AnalysisPublishedOutputReference,
    AnalysisRecord,
    AnalysisRecordInput,
    AnalysisRecordOutput,
    AnalysisSubject,
    AnalysisTableRecordOutput,
    AnalysisTableView,
    AnalysisTableViewSpec,
    InterpretationAnalysisRecordInput,
    MeasurementAnalysisRecordInput,
    ProjectAnalysisSubject,
    PublishedAnalysisRecordInput,
    RunAnalysisSubject,
    SampleAnalysisSubject,
    analysis_record_id,
    validate_analysis_output_content_budget,
)
from scopecat.records.content import BytesWrite, ContentEntry, ModelWrite
from scopecat.records.metadata import validate_json_metadata
from scopecat.records.parameter_change import ParameterChangeProposal
from scopecat.runs.refs import (
    artifact_content_ref,
    dataset_content_ref,
    record_content_ref,
)

_PROJECT_ANALYSIS_SUBJECT = ProjectAnalysisSubject()


@dataclass(frozen=True)
class MeasurementAnalysisInput:
    """One exact run-owned measurement dataset consumed by an analysis."""

    id: str
    run_id: str
    target: str
    kind: Literal["measurement_dataset"]
    content_hash: str
    codec: str
    role: str
    title: str | None = None
    metadata: Mapping[str, object] | None = None


@dataclass(frozen=True)
class PublishedAnalysisOutputInput:
    """One exact output from an immutable run or project analysis revision."""

    id: str
    target: str
    kind: Literal["analysis_dataset", "analysis_fact", "analysis_artifact"]
    content_hash: str
    codec: str
    role: str
    source: AnalysisPublishedOutputReference
    title: str | None = None
    metadata: Mapping[str, object] | None = None


@dataclass(frozen=True)
class InterpretationAnalysisInput:
    id: str
    target: str
    kind: Literal["interpretation"]
    content_hash: str
    codec: str
    role: str
    source: AnalysisInterpretationReference
    title: str | None = None
    metadata: Mapping[str, object] | None = None


type AnalysisInput = (
    MeasurementAnalysisInput
    | PublishedAnalysisOutputInput
    | InterpretationAnalysisInput
)


@dataclass(frozen=True)
class AnalysisTableOutput:
    kind: Literal["table"]
    id: str
    title: str
    content: AnalysisTableViewSpec
    metadata: Mapping[str, object]


@dataclass(frozen=True)
class AnalysisFigureOutput:
    kind: Literal["figure"]
    id: str
    title: str
    content: AnalysisFigureViewSpec
    metadata: Mapping[str, object]


@dataclass(frozen=True)
class AnalysisParameterProposalOutput:
    kind: Literal["parameter_change_proposal"]
    id: str
    title: str
    content: ParameterChangeProposal
    metadata: Mapping[str, object]


@dataclass(frozen=True)
class AnalysisFactOutput:
    kind: Literal["fact"]
    id: str
    title: str
    content: AnalysisFact
    metadata: Mapping[str, object]
    produced_by: AnalysisExecutionOutputReference | None = None


@dataclass(frozen=True)
class AnalysisDatasetOutput:
    kind: Literal["dataset"]
    id: str
    title: str
    content: DerivedDataset
    metadata: Mapping[str, object]
    produced_by: AnalysisExecutionOutputReference | None = None
    derived_from: AnalysisDatasetDerivation | None = None


@dataclass(frozen=True)
class AnalysisArtifactOutput:
    kind: Literal["artifact"]
    id: str
    title: str
    content: bytes
    filename: str
    media_type: str
    metadata: Mapping[str, object]
    produced_by: AnalysisExecutionOutputReference | None = None


type AnalysisOutput = (
    AnalysisFactOutput
    | AnalysisDatasetOutput
    | AnalysisArtifactOutput
    | AnalysisTableOutput
    | AnalysisFigureOutput
    | AnalysisParameterProposalOutput
)


@dataclass(frozen=True)
class SavedAnalysis:
    record: ContentEntry
    analysis_key: str
    inputs: tuple[AnalysisInput, ...] = ()
    executions: tuple[AnalysisExecution, ...] = ()
    outputs: tuple[AnalysisOutput, ...] = ()
    parameter_proposals: tuple[ParameterChangeProposal, ...] = ()


@dataclass(frozen=True, slots=True)
class PreparedAnalysis:
    saved: SavedAnalysis
    publication: AnalysisPublication | None


@dataclass(frozen=True, slots=True)
class PreparedProjectAnalysis:
    """Project-level analysis ready for repository publication."""

    saved: SavedAnalysis
    publication: AnalysisPublication | None


@dataclass(frozen=True, slots=True)
class _PreparedAnalysisContents:
    """Immutable record and output contents shared by both publication owners."""

    record: ContentEntry
    entries: tuple[ContentEntry, ...]
    models: tuple[ModelWrite, ...]
    bytes: tuple[BytesWrite, ...]


def save_analysis(
    *,
    services: ProjectStateServices,
    run_id: str,
    title: str,
    analysis_key: str,
    step_id: str | None,
    inputs: Sequence[AnalysisInput],
    executions: Sequence[AnalysisExecution],
    outputs: Sequence[AnalysisOutput],
    parameter_proposals: Sequence[ParameterChangeProposal],
) -> SavedAnalysis:
    """Prepare and publish one analysis using the repository's local unit."""

    prepared = prepare_analysis(
        services=services,
        run_id=run_id,
        title=title,
        analysis_key=analysis_key,
        step_id=step_id,
        inputs=inputs,
        executions=executions,
        outputs=outputs,
        parameter_proposals=parameter_proposals,
    )
    if prepared.publication is not None:
        services.runs.publish_analysis(prepared.publication)
    return prepared.saved


def prepare_analysis(
    *,
    services: ProjectStateServices,
    run_id: str,
    title: str,
    analysis_key: str,
    step_id: str | None,
    inputs: Sequence[AnalysisInput],
    executions: Sequence[AnalysisExecution],
    outputs: Sequence[AnalysisOutput],
    parameter_proposals: Sequence[ParameterChangeProposal],
) -> PreparedAnalysis:
    """Prepare analysis content for publication in a caller-owned unit."""

    proposed_record_id = analysis_record_id(analysis_key, 1)
    _validate_analysis_output_ids(outputs)
    _validate_analysis_input_ids(inputs)
    _validate_analysis_execution_outputs(executions, outputs)
    _validate_analysis_inputs(
        services=services,
        run_id=run_id,
        inputs=inputs,
    )
    analysis_views = _prepare_analysis_views(
        outputs, inputs=inputs, services=services, repository=None
    )
    output_proposals = tuple(
        output.content
        for output in outputs
        if isinstance(output, AnalysisParameterProposalOutput)
    )
    if output_proposals != tuple(parameter_proposals):
        _raise_analysis_problem(
            "analysis_parameter_proposals_mismatch",
            "analysis parameter proposals must match proposal outputs",
            "parameter_proposals",
        )
    _validate_analysis_proposal_evidence(outputs, parameter_proposals)
    if any(
        proposal.analysis_record_id != proposed_record_id
        for proposal in parameter_proposals
    ):
        _raise_analysis_problem(
            "analysis_parameter_proposal_source_invalid",
            "analysis parameter proposal does not identify its producing analysis",
            "parameter_proposals",
        )
    publication_hash = _analysis_publication_hash(
        title=title,
        analysis_key=analysis_key,
        step_id=step_id,
        inputs=inputs,
        executions=executions,
        outputs=outputs,
    )
    existing = _latest_analysis(
        services=services,
        run_id=run_id,
        analysis_key=analysis_key,
    )
    if existing is not None and existing.record.publication_hash == publication_hash:
        saved_outputs = _load_existing_outputs(
            services=services,
            run_id=run_id,
            outputs=outputs,
            record=existing.record,
        )
        saved_proposals = tuple(
            output.content
            for output in saved_outputs
            if isinstance(output, AnalysisParameterProposalOutput)
        )
        return PreparedAnalysis(
            saved=SavedAnalysis(
                record=existing.entry,
                analysis_key=analysis_key,
                inputs=tuple(inputs),
                executions=tuple(existing.record.executions),
                outputs=saved_outputs,
                parameter_proposals=saved_proposals,
            ),
            publication=None,
        )

    revision = 1 if existing is None else existing.record.revision + 1
    selected_record_id = analysis_record_id(analysis_key, revision)
    saved_outputs = _revisioned_outputs(
        outputs,
        analysis_record_id=selected_record_id,
        revision=revision,
    )
    saved_proposals = tuple(
        output.content
        for output in saved_outputs
        if isinstance(output, AnalysisParameterProposalOutput)
    )
    storage = services.runs
    prepared_contents = _prepare_analysis_contents(
        subject=RunAnalysisSubject(run_id=run_id),
        record_id=selected_record_id,
        title=title,
        analysis_key=analysis_key,
        revision=revision,
        publication_hash=publication_hash,
        step_id=step_id,
        inputs=inputs,
        executions=executions,
        outputs=saved_outputs,
        analysis_views=analysis_views,
    )
    prepared_proposals = prepare_parameter_change_proposal_contents(
        storage=storage,
        run_id=run_id,
        proposals=saved_proposals,
    )
    saved = SavedAnalysis(
        record=prepared_contents.record,
        analysis_key=analysis_key,
        inputs=tuple(inputs),
        executions=tuple(executions),
        outputs=saved_outputs,
        parameter_proposals=saved_proposals,
    )
    return PreparedAnalysis(
        saved=saved,
        publication=AnalysisPublication(
            subject=RunAnalysisSubject(run_id=run_id),
            record=prepared_contents.record,
            entries=(
                *prepared_proposals.entries,
                *prepared_contents.entries,
            ),
            analysis_key=analysis_key,
            revision=revision,
            publication_hash=publication_hash,
            title=title,
            step_id=step_id,
            input_count=len(inputs),
            output_count=len(outputs),
            models=(
                *prepared_proposals.writes,
                *prepared_contents.models,
            ),
            bytes=prepared_contents.bytes,
        ),
    )


def prepare_project_analysis(
    *,
    services: ProjectStateServices,
    repository: AnalysisRepository,
    title: str,
    analysis_key: str,
    step_id: str | None,
    inputs: Sequence[AnalysisInput],
    executions: Sequence[AnalysisExecution],
    outputs: Sequence[AnalysisOutput],
    subject: ProjectAnalysisSubject | SampleAnalysisSubject = _PROJECT_ANALYSIS_SUBJECT,
    validate_interpretation: Callable[[AnalysisInterpretationReference], None]
    | None = None,
) -> PreparedProjectAnalysis:
    """Prepare one immutable publication over explicit project inputs."""

    if not inputs:
        _raise_analysis_problem(
            "project_analysis_input_missing",
            "project analysis requires at least one explicit input",
            "inputs",
        )
    if any(isinstance(output, AnalysisParameterProposalOutput) for output in outputs):
        _raise_analysis_problem(
            "project_analysis_parameter_proposal_unsupported",
            "project analysis cannot publish parameter proposals yet",
            "outputs",
        )
    _validate_analysis_output_ids(outputs)
    _validate_analysis_input_ids(inputs)
    _validate_analysis_execution_outputs(executions, outputs)
    for index, item in enumerate(inputs):
        if isinstance(item, InterpretationAnalysisInput):
            if (
                not isinstance(subject, ProjectAnalysisSubject)
                or validate_interpretation is None
            ):
                _raise_analysis_problem(
                    "analysis_interpretation_unsupported",
                    "interpretation inputs require a project procedure authority",
                    "inputs",
                    index,
                )
            if (
                item.target != item.source.step_key
                or item.content_hash != item.source.response_hash
                or item.codec != "scopecat.interpretation-response.v1"
            ):
                _raise_analysis_problem(
                    "analysis_input_content_mismatch",
                    "interpretation input identity does not match its source",
                    "inputs",
                    index,
                )
            validate_interpretation(item.source)
    _validate_project_analysis_inputs(
        services=services,
        repository=repository,
        inputs=inputs,
    )
    analysis_views = _prepare_analysis_views(
        outputs, inputs=inputs, services=services, repository=repository
    )
    publication_hash = _analysis_publication_hash(
        title=title,
        analysis_key=analysis_key,
        step_id=step_id,
        inputs=inputs,
        executions=executions,
        outputs=outputs,
    )
    existing = _latest_project_analysis(
        repository=repository,
        analysis_key=analysis_key,
        subject=subject,
    )
    if existing is not None and existing.record.publication_hash == publication_hash:
        return PreparedProjectAnalysis(
            saved=SavedAnalysis(
                record=existing.entry,
                analysis_key=analysis_key,
                inputs=tuple(inputs),
                executions=tuple(existing.record.executions),
                outputs=tuple(outputs),
            ),
            publication=None,
        )

    revision = 1 if existing is None else existing.record.revision + 1
    record_id = _project_owned_analysis_record_id(
        subject,
        analysis_key=analysis_key,
        revision=revision,
    )
    prepared_contents = _prepare_analysis_contents(
        subject=subject,
        record_id=record_id,
        title=title,
        analysis_key=analysis_key,
        revision=revision,
        publication_hash=publication_hash,
        step_id=step_id,
        inputs=inputs,
        executions=executions,
        outputs=outputs,
        analysis_views=analysis_views,
    )
    saved = SavedAnalysis(
        record=prepared_contents.record,
        analysis_key=analysis_key,
        inputs=tuple(inputs),
        executions=tuple(executions),
        outputs=tuple(outputs),
    )
    return PreparedProjectAnalysis(
        saved=saved,
        publication=AnalysisPublication(
            subject=subject,
            record=prepared_contents.record,
            entries=prepared_contents.entries,
            analysis_key=analysis_key,
            revision=revision,
            publication_hash=publication_hash,
            title=title,
            step_id=step_id,
            input_count=len(inputs),
            output_count=len(outputs),
            models=prepared_contents.models,
            bytes=prepared_contents.bytes,
        ),
    )


def _prepare_analysis_contents(
    *,
    subject: AnalysisSubject,
    record_id: str,
    title: str,
    analysis_key: str,
    revision: int,
    publication_hash: str,
    step_id: str | None,
    inputs: Sequence[AnalysisInput],
    executions: Sequence[AnalysisExecution],
    outputs: Sequence[AnalysisOutput],
    analysis_views: Mapping[str, AnalysisTableView | AnalysisFigureView],
) -> _PreparedAnalysisContents:
    prepared_datasets = _prepare_analysis_datasets(
        analysis_record_id=record_id,
        outputs=outputs,
    )
    prepared_artifacts = _prepare_analysis_artifacts(
        analysis_record_id=record_id,
        outputs=outputs,
    )
    analysis_record = AnalysisRecord(
        subject=subject,
        title=title,
        key=analysis_key,
        revision=revision,
        publication_hash=publication_hash,
        step_id=step_id,
        inputs=_analysis_record_inputs(inputs),
        executions=list(executions),
        outputs=_analysis_record_outputs(
            outputs,
            dataset_references=prepared_datasets.references,
            artifact_references=prepared_artifacts.references,
            analysis_views=analysis_views,
        ),
    )
    record = ContentEntry(
        role="record",
        id=record_id,
        kind="analysis",
        media_type="application/json",
        content_hash=model_wire_content_hash(analysis_record),
    )
    return _PreparedAnalysisContents(
        record=record,
        entries=(
            *prepared_datasets.entries,
            *prepared_artifacts.entries,
            record,
        ),
        models=(
            ModelWrite(
                ref=record_content_ref(record_id=record_id, kind="analysis"),
                value=analysis_record,
            ),
        ),
        bytes=(*prepared_datasets.writes, *prepared_artifacts.writes),
    )


@dataclass(frozen=True, slots=True)
class _ExistingAnalysis:
    entry: ContentEntry
    record: AnalysisRecord


def _validate_analysis_input_ids(inputs: Sequence[AnalysisInput]) -> None:
    ids = tuple(input_ref.id for input_ref in inputs)
    if len(ids) != len(set(ids)):
        _raise_analysis_problem(
            "analysis_input_id_duplicated",
            "analysis input ids must be unique",
            "inputs",
        )


def _validate_project_analysis_inputs(
    *,
    services: ProjectStateServices,
    repository: AnalysisRepository,
    inputs: Sequence[AnalysisInput],
) -> None:
    storage = services.runs
    for index, input_ref in enumerate(inputs):
        if isinstance(input_ref, InterpretationAnalysisInput):
            continue
        if isinstance(input_ref, MeasurementAnalysisInput):
            snapshot = storage.read_snapshot(input_ref.run_id)
            _require_completed_project_input_run(snapshot.status, index=index)
            _validate_measurement_analysis_input(
                services=services,
                input_ref=input_ref,
                index=index,
            )
            continue
        source_output = _load_published_analysis_output(
            services=services,
            repository=repository,
            input_ref=input_ref,
            index=index,
        )
        _validate_published_analysis_output_input(
            input_ref=input_ref,
            source_output=source_output,
            index=index,
        )


def _require_completed_project_input_run(status: str, *, index: int) -> None:
    if status != "completed":
        _raise_analysis_problem(
            "project_analysis_input_run_incomplete",
            "project analysis run inputs must belong to completed runs",
            "inputs",
            index,
        )


def _validate_measurement_analysis_input(
    *,
    services: ProjectStateServices,
    input_ref: MeasurementAnalysisInput,
    index: int,
) -> None:
    try:
        entry = services.runs.read_content(
            input_ref.run_id,
            role="dataset",
            content_id=input_ref.target,
        )
    except NotFound:
        _raise_analysis_problem(
            "analysis_input_content_mismatch",
            "measurement dataset input must match its exact run content",
            "inputs",
            index,
        )
    if (
        entry.kind != "measurement_dataset"
        or entry.content_hash != input_ref.content_hash
        or input_ref.codec != MEASUREMENT_DATASET_CODEC
    ):
        _raise_analysis_problem(
            "analysis_input_content_mismatch",
            "measurement dataset input must match its exact run content",
            "inputs",
            index,
        )


def _load_published_analysis_output(
    *,
    services: ProjectStateServices,
    repository: AnalysisRepository,
    input_ref: PublishedAnalysisOutputInput,
    index: int,
) -> AnalysisRecordOutput | None:
    source = input_ref.source
    if isinstance(source.subject, RunAnalysisSubject):
        run_id = source.subject.run_id
        snapshot = services.runs.read_snapshot(run_id)
        _require_completed_project_input_run(snapshot.status, index=index)
        try:
            analysis_entry = services.runs.read_content(
                run_id,
                role="record",
                content_id=source.analysis_record_id,
            )
        except NotFound:
            _raise_analysis_problem(
                "analysis_input_source_unknown",
                "analysis input must identify an existing run analysis",
                "inputs",
                index,
            )
        if analysis_entry.kind != "analysis":
            _raise_analysis_problem(
                "analysis_input_source_unknown",
                "analysis input must identify an existing run analysis",
                "inputs",
                index,
            )
        source_record = services.runs.read_model(
            run_id,
            record_content_ref(
                record_id=source.analysis_record_id,
                kind="analysis",
            ),
            AnalysisRecord,
        )
    else:
        publication = repository.read_publication(
            source.analysis_record_id,
            subject=source.subject,
        )
        source_record = repository.read_model(
            publication.record.id,
            record_content_ref(
                record_id=publication.record.id,
                kind="analysis",
            ),
            AnalysisRecord,
        )
    if source_record.subject != source.subject:
        _raise_analysis_problem(
            "analysis_input_source_scope_mismatch",
            "analysis input source subject does not match its publication",
            "inputs",
            index,
        )
    return next(
        (output for output in source_record.outputs if output.id == source.output_id),
        None,
    )


def _validate_published_analysis_output_input(
    *,
    input_ref: PublishedAnalysisOutputInput,
    source_output: AnalysisRecordOutput | None,
    index: int,
) -> None:
    if input_ref.kind == "analysis_dataset":
        if not isinstance(source_output, AnalysisDatasetRecordOutput):
            _raise_analysis_problem(
                "analysis_input_source_kind_mismatch",
                "analysis_dataset input source identifies a different output kind",
                "inputs",
                index,
            )
        target = source_output.content.dataset_id
        content_hash = source_output.content.content_hash
        codec = source_output.content.codec
    elif input_ref.kind == "analysis_fact":
        if not isinstance(source_output, AnalysisFactRecordOutput):
            _raise_analysis_problem(
                "analysis_input_source_kind_mismatch",
                "analysis_fact input source identifies a different output kind",
                "inputs",
                index,
            )
        target = source_output.id
        content_hash = f"sha256:{model_wire_content_hash(source_output.content)}"
        codec = source_output.content.codec
    else:
        if not isinstance(source_output, AnalysisArtifactRecordOutput):
            _raise_analysis_problem(
                "analysis_input_source_kind_mismatch",
                "analysis_artifact input source identifies a different output kind",
                "inputs",
                index,
            )
        target = source_output.content.artifact_id
        content_hash = source_output.content.content_hash
        codec = ANALYSIS_ARTIFACT_CODEC
    if (
        input_ref.target != target
        or input_ref.content_hash != content_hash
        or input_ref.codec != codec
    ):
        _raise_analysis_problem(
            "analysis_input_content_mismatch",
            "analysis input must match its exact published output content",
            "inputs",
            index,
        )


def _validate_analysis_inputs(
    *,
    services: ProjectStateServices,
    run_id: str,
    inputs: Sequence[AnalysisInput],
) -> None:
    storage = services.runs
    for index, input_ref in enumerate(inputs):
        if isinstance(input_ref, InterpretationAnalysisInput):
            _raise_analysis_problem(
                "analysis_interpretation_unsupported",
                "interpretation inputs require project analysis",
                "inputs",
                index,
            )
        if isinstance(input_ref, MeasurementAnalysisInput):
            if input_ref.run_id != run_id:
                _raise_analysis_problem(
                    "analysis_input_run_invalid",
                    "run analysis inputs must belong to their subject run",
                    "inputs",
                    index,
                )
            _validate_measurement_analysis_input(
                services=services,
                input_ref=input_ref,
                index=index,
            )
            continue
        source = input_ref.source
        if not isinstance(source.subject, RunAnalysisSubject) or (
            source.subject.run_id != run_id
        ):
            _raise_analysis_problem(
                "analysis_input_source_unknown",
                "analysis input must identify an earlier analysis on this run",
                "inputs",
                index,
            )
        try:
            source_entry = storage.read_content(
                run_id,
                role="record",
                content_id=source.analysis_record_id,
            )
        except NotFound:
            _raise_analysis_problem(
                "analysis_input_source_unknown",
                "analysis input must identify an earlier analysis on this run",
                "inputs",
                index,
            )
        if source_entry.kind != "analysis":
            _raise_analysis_problem(
                "analysis_input_source_unknown",
                "analysis input must identify an earlier analysis on this run",
                "inputs",
                index,
            )
        source_record = storage.read_model(
            run_id,
            record_content_ref(
                record_id=source.analysis_record_id,
                kind="analysis",
            ),
            AnalysisRecord,
        )
        source_output = next(
            (
                output
                for output in source_record.outputs
                if output.id == source.output_id
            ),
            None,
        )
        _validate_published_analysis_output_input(
            input_ref=input_ref,
            source_output=source_output,
            index=index,
        )


def _latest_analysis(
    *,
    services: ProjectStateServices,
    run_id: str,
    analysis_key: str,
) -> _ExistingAnalysis | None:
    storage = services.runs
    publication = storage.latest_analysis_publication(run_id, analysis_key)
    if publication is None:
        return None
    entry = publication.record
    return _ExistingAnalysis(
        entry=entry,
        record=storage.read_model(
            run_id,
            record_content_ref(record_id=entry.id, kind="analysis"),
            AnalysisRecord,
        ),
    )


def _latest_project_analysis(
    *,
    repository: AnalysisRepository,
    analysis_key: str,
    subject: ProjectAnalysisSubject | SampleAnalysisSubject,
) -> _ExistingAnalysis | None:
    publication = repository.latest_publication(analysis_key, subject=subject)
    if publication is None:
        return None
    entry = publication.record
    return _ExistingAnalysis(
        entry=entry,
        record=repository.read_model(
            entry.id,
            record_content_ref(record_id=entry.id, kind="analysis"),
            AnalysisRecord,
        ),
    )


def _project_owned_analysis_record_id(
    subject: ProjectAnalysisSubject | SampleAnalysisSubject,
    *,
    analysis_key: str,
    revision: int,
) -> str:
    if isinstance(subject, ProjectAnalysisSubject):
        return analysis_record_id(analysis_key, revision)
    scope = stable_content_hash({"kind": "sample", "sample_id": subject.sample_id})[:12]
    return analysis_record_id(f"sample-{scope}-{analysis_key}", revision)


def _revisioned_outputs(
    outputs: Sequence[AnalysisOutput],
    *,
    analysis_record_id: str,
    revision: int,
) -> tuple[AnalysisOutput, ...]:
    selected: list[AnalysisOutput] = []
    for output in outputs:
        if not isinstance(output, AnalysisParameterProposalOutput):
            selected.append(output)
            continue
        proposal_id = output.id if revision == 1 else f"{output.id}-r{revision}"
        selected.append(
            replace(
                output,
                content=output.content.model_copy(
                    update={
                        "id": proposal_id,
                        "analysis_record_id": analysis_record_id,
                    }
                ),
            )
        )
    return tuple(selected)


def _load_existing_outputs(
    *,
    services: ProjectStateServices,
    run_id: str,
    outputs: Sequence[AnalysisOutput],
    record: AnalysisRecord,
) -> tuple[AnalysisOutput, ...]:
    proposal_ids = {
        output.id: output.content.proposal_id
        for output in record.outputs
        if isinstance(output, AnalysisParameterProposalRecordOutput)
    }
    selected: list[AnalysisOutput] = []
    for output in outputs:
        if not isinstance(output, AnalysisParameterProposalOutput):
            selected.append(output)
            continue
        selected.append(
            replace(
                output,
                content=load_parameter_change_proposal(
                    run_id=run_id,
                    selector=proposal_ids[output.id],
                    services=services,
                ),
            )
        )
    return tuple(selected)


def _analysis_publication_hash(
    *,
    title: str,
    analysis_key: str,
    step_id: str | None,
    inputs: Sequence[AnalysisInput],
    executions: Sequence[AnalysisExecution],
    outputs: Sequence[AnalysisOutput],
) -> str:
    identity = {
        "title": title,
        "key": analysis_key,
        "step_id": step_id,
        "inputs": [
            {
                "target": item.target,
                "id": item.id,
                "run_id": (
                    item.run_id if isinstance(item, MeasurementAnalysisInput) else None
                ),
                "kind": item.kind,
                "content_hash": item.content_hash,
                "codec": item.codec,
                "role": item.role,
                "title": item.title,
                "metadata": validate_json_metadata(item.metadata or {}),
                "source": (
                    None if isinstance(item, MeasurementAnalysisInput) else item.source
                ),
            }
            for item in inputs
        ],
        "executions": list(executions),
        "outputs": [_analysis_output_identity(output) for output in outputs],
    }
    return f"sha256:{stable_content_hash(content_fingerprint(identity))}"


def _analysis_output_identity(output: AnalysisOutput) -> dict[str, object]:
    shared: dict[str, object] = {
        "kind": output.kind,
        "id": output.id,
        "title": output.title,
        "metadata": validate_json_metadata(output.metadata),
    }
    if isinstance(output, AnalysisDatasetOutput):
        shared["produced_by"] = output.produced_by
        shared["derived_from"] = output.derived_from
        shared["content"] = {
            "content_hash": sha256_content_hash(output.content.to_arrow_ipc()),
            "schema": output.content.schema.model_dump(mode="json"),
        }
    elif isinstance(output, AnalysisArtifactOutput):
        shared["produced_by"] = output.produced_by
        shared["content"] = {
            "content_hash": sha256_content_hash(output.content),
            "filename": output.filename,
            "media_type": output.media_type,
        }
    elif isinstance(output, AnalysisParameterProposalOutput):
        proposal = output.content
        shared["content"] = {
            "source_run_id": proposal.source_run_id,
            "base_config_id": proposal.base_config_id,
            "base_config_content_hash": proposal.base_config_content_hash,
            "reason": proposal.reason,
            "confidence": proposal.confidence,
            "deltas": proposal.deltas,
        }
    elif isinstance(output, AnalysisFactOutput):
        shared["produced_by"] = output.produced_by
        shared["content"] = output.content
    else:
        shared["content"] = output.content
    return shared


def _analysis_record_inputs(
    inputs: Sequence[AnalysisInput],
) -> list[AnalysisRecordInput]:
    record_inputs: list[AnalysisRecordInput] = []
    for input_ref in inputs:
        metadata = input_ref.metadata
        validated_metadata = (
            validate_json_metadata(metadata) if metadata is not None else None
        )
        if isinstance(input_ref, MeasurementAnalysisInput):
            record_inputs.append(
                MeasurementAnalysisRecordInput(
                    id=input_ref.id,
                    run_id=input_ref.run_id,
                    target=input_ref.target,
                    content_hash=input_ref.content_hash,
                    codec=input_ref.codec,
                    role=input_ref.role,
                    title=input_ref.title,
                    metadata=validated_metadata,
                )
            )
        elif isinstance(input_ref, InterpretationAnalysisInput):
            record_inputs.append(
                InterpretationAnalysisRecordInput(
                    id=input_ref.id,
                    target=input_ref.target,
                    content_hash=input_ref.content_hash,
                    codec=input_ref.codec,
                    role=input_ref.role,
                    title=input_ref.title,
                    metadata=validated_metadata,
                    source=input_ref.source,
                )
            )
        else:
            record_inputs.append(
                PublishedAnalysisRecordInput(
                    id=input_ref.id,
                    target=input_ref.target,
                    kind=input_ref.kind,
                    content_hash=input_ref.content_hash,
                    codec=input_ref.codec,
                    role=input_ref.role,
                    title=input_ref.title,
                    metadata=validated_metadata,
                    source=input_ref.source,
                )
            )
    return record_inputs


def _analysis_record_outputs(
    outputs: Sequence[AnalysisOutput],
    *,
    dataset_references: Mapping[str, AnalysisDatasetReference],
    artifact_references: Mapping[str, AnalysisArtifactReference],
    analysis_views: Mapping[str, AnalysisTableView | AnalysisFigureView],
) -> list[AnalysisRecordOutput]:
    selected: list[AnalysisRecordOutput] = []
    for output in outputs:
        metadata = validate_json_metadata(output.metadata)
        if isinstance(output, AnalysisFactOutput):
            selected.append(
                AnalysisFactRecordOutput(
                    kind="fact",
                    id=output.id,
                    title=output.title,
                    content=output.content,
                    produced_by=output.produced_by,
                    metadata=metadata,
                )
            )
        elif isinstance(output, AnalysisDatasetOutput):
            selected.append(
                AnalysisDatasetRecordOutput(
                    kind="dataset",
                    id=output.id,
                    title=output.title,
                    content=dataset_references[output.id],
                    produced_by=output.produced_by,
                    derived_from=output.derived_from,
                    metadata=metadata,
                )
            )
        elif isinstance(output, AnalysisArtifactOutput):
            selected.append(
                AnalysisArtifactRecordOutput(
                    kind="artifact",
                    id=output.id,
                    title=output.title,
                    content=artifact_references[output.id],
                    produced_by=output.produced_by,
                    metadata=metadata,
                )
            )
        elif isinstance(output, AnalysisTableOutput):
            content = analysis_views[output.id]
            assert isinstance(content, AnalysisTableView)
            selected.append(
                AnalysisTableRecordOutput(
                    kind="table",
                    id=output.id,
                    title=output.title,
                    content=content,
                    metadata=metadata,
                )
            )
        elif isinstance(output, AnalysisFigureOutput):
            content = analysis_views[output.id]
            assert isinstance(content, AnalysisFigureView)
            selected.append(
                AnalysisFigureRecordOutput(
                    kind="figure",
                    id=output.id,
                    title=output.title,
                    content=content,
                    metadata=metadata,
                )
            )
        else:
            selected.append(
                AnalysisParameterProposalRecordOutput(
                    kind="parameter_change_proposal",
                    id=output.id,
                    title=output.title,
                    content=AnalysisParameterProposalReference(
                        proposal_id=output.content.id,
                        record_ref=parameter_change_proposal_record_ref(
                            output.content.id
                        ),
                    ),
                    metadata=metadata,
                )
            )
    return selected


@dataclass(frozen=True, slots=True)
class _PreparedAnalysisDatasets:
    entries: tuple[ContentEntry, ...]
    writes: tuple[BytesWrite, ...]
    references: Mapping[str, AnalysisDatasetReference]


def _prepare_analysis_datasets(
    *,
    analysis_record_id: str,
    outputs: Sequence[AnalysisOutput],
) -> _PreparedAnalysisDatasets:
    entries: list[ContentEntry] = []
    writes: list[BytesWrite] = []
    references: dict[str, AnalysisDatasetReference] = {}
    for output in outputs:
        if not isinstance(output, AnalysisDatasetOutput):
            continue
        output_id = artifact_slug(output.id, fallback="data")
        if output_id != output.id:
            _raise_analysis_problem(
                "analysis_dataset_id_invalid",
                f"analysis dataset id is not normalized: {output.id}",
                "outputs",
            )
        if output.id in references:
            _raise_analysis_problem(
                "analysis_dataset_id_duplicated",
                f"analysis dataset id is duplicated: {output.id}",
                "outputs",
            )
        dataset_id = f"{analysis_record_id}-{output.id}"
        content = output.content.to_arrow_ipc()
        content_hash = sha256_content_hash(content)
        metadata = validate_json_metadata(output.metadata)
        entries.append(
            ContentEntry(
                role="dataset",
                id=dataset_id,
                kind="analysis_dataset",
                title=output.title,
                media_type=DERIVED_DATASET_MEDIA_TYPE,
                filename=f"{output.id}.arrow",
                schema=output.content.schema.model_dump(mode="json"),
                content_hash=content_hash,
                produced_by=analysis_record_id,
                metadata=metadata,
            )
        )
        writes.append(
            BytesWrite(
                ref=dataset_content_ref(
                    dataset_id=dataset_id,
                    kind="analysis_dataset",
                ),
                content=content,
            )
        )
        references[output.id] = AnalysisDatasetReference(
            dataset_id=dataset_id,
            content_hash=content_hash,
            codec=DERIVED_DATASET_CODEC,
        )
    return _PreparedAnalysisDatasets(
        entries=tuple(entries),
        writes=tuple(writes),
        references=references,
    )


@dataclass(frozen=True, slots=True)
class _PreparedAnalysisArtifacts:
    entries: tuple[ContentEntry, ...]
    writes: tuple[BytesWrite, ...]
    references: Mapping[str, AnalysisArtifactReference]


def _prepare_analysis_artifacts(
    *,
    analysis_record_id: str,
    outputs: Sequence[AnalysisOutput],
) -> _PreparedAnalysisArtifacts:
    entries: list[ContentEntry] = []
    writes: list[BytesWrite] = []
    references: dict[str, AnalysisArtifactReference] = {}
    for output in outputs:
        if not isinstance(output, AnalysisArtifactOutput):
            continue
        artifact_id = f"{analysis_record_id}-{output.id}"
        content_hash = sha256_content_hash(output.content)
        metadata = validate_json_metadata(output.metadata)
        entries.append(
            ContentEntry(
                role="artifact",
                id=artifact_id,
                kind="analysis_artifact",
                title=output.title,
                media_type=output.media_type,
                filename=output.filename,
                content_hash=content_hash,
                produced_by=analysis_record_id,
                metadata=metadata,
            )
        )
        writes.append(
            BytesWrite(
                ref=artifact_content_ref(
                    artifact_id=artifact_id,
                    kind="analysis_artifact",
                ),
                content=output.content,
            )
        )
        references[output.id] = AnalysisArtifactReference(
            artifact_id=artifact_id,
            content_hash=content_hash,
            media_type=output.media_type,
            filename=output.filename,
        )
    return _PreparedAnalysisArtifacts(
        entries=tuple(entries),
        writes=tuple(writes),
        references=references,
    )


def _validate_analysis_output_ids(outputs: Sequence[AnalysisOutput]) -> None:
    ids: list[str] = []
    for output in outputs:
        selected_id = artifact_slug(output.id, fallback="output")
        if output.id != selected_id:
            _raise_analysis_problem(
                "analysis_output_id_invalid",
                f"analysis output id is not normalized: {output.id}",
                "outputs",
            )
        ids.append(output.id)
    if len(ids) != len(set(ids)):
        _raise_analysis_problem(
            "analysis_output_id_duplicated",
            "analysis output ids must be unique",
            "outputs",
        )


def _read_figure_dataset(
    services: ProjectStateServices,
    repository: AnalysisRepository | None,
    source: AnalysisPublishedDatasetViewSource,
    projection: AnalysisFigureProjection,
    limit: int,
) -> tuple[DerivedDataset, int]:
    """Read validated immutable IPC on the server; only bounded previews leave it."""
    ref = dataset_content_ref(
        dataset_id=source.dataset.dataset_id, kind="analysis_dataset"
    )
    subject = source.source.subject
    if isinstance(subject, RunAnalysisSubject):
        entry = services.runs.read_content(
            subject.run_id, role="dataset", content_id=source.dataset.dataset_id
        )
        content = services.runs.read_bytes(subject.run_id, ref)
    else:
        # Run analyses admit only same-run inputs. Project analyses supply this port.
        assert repository is not None
        record_id = source.source.analysis_record_id
        entry = repository.read_content(record_id, source.dataset.dataset_id)
        content = repository.read_bytes(record_id, ref)
    columns = [projection.x, projection.y]
    if projection.series is not None:
        columns.append(projection.series)
    if projection.uncertainty is not None:
        columns.extend((projection.uncertainty.lower, projection.uncertainty.upper))
    return DerivedDataset.preview_from_arrow_ipc(
        content,
        schema=DerivedDatasetSchema.model_validate(entry.data_schema),
        columns=tuple(dict.fromkeys(columns)),
        limit=limit,
    )


def _prepare_analysis_views(
    outputs: Sequence[AnalysisOutput],
    *,
    inputs: Sequence[AnalysisInput],
    services: ProjectStateServices,
    repository: AnalysisRepository | None,
) -> Mapping[str, AnalysisTableView | AnalysisFigureView]:
    datasets = {
        output.id: output.content
        for output in outputs
        if isinstance(output, AnalysisDatasetOutput)
    }
    selected: dict[str, AnalysisTableView | AnalysisFigureView] = {}
    for index, output in enumerate(outputs):
        if not isinstance(output, AnalysisTableOutput | AnalysisFigureOutput):
            continue
        try:
            if isinstance(output, AnalysisTableOutput):
                dataset = datasets[output.content.source.output_id]
                preview = dataset.to_analysis_table(columns=output.content.columns)
                selected[output.id] = AnalysisTableView(
                    source=output.content.source,
                    columns=output.content.columns,
                    preview=preview,
                    total_rows=len(dataset),
                    truncated=len(dataset) > len(preview.rows),
                )
            else:
                layers: list[tuple[AnalysisFigureLayerSpec, DerivedDataset, int]] = []
                for layer_index, layer in enumerate(output.content.layers):
                    source = layer.source
                    if isinstance(source, AnalysisDatasetViewSource):
                        dataset = datasets[source.output_id]
                        total_points = len(dataset)
                    else:
                        # External layers must match a validated frozen input.
                        matching = next(
                            (
                                item
                                for item in inputs
                                if (
                                    isinstance(item, PublishedAnalysisOutputInput)
                                    and item.kind == "analysis_dataset"
                                    and item.source == source.source
                                    and item.target == source.dataset.dataset_id
                                    and item.content_hash == source.dataset.content_hash
                                    and item.codec == source.dataset.codec
                                )
                            ),
                            None,
                        )
                        if matching is None:
                            raise ValueError(
                                "published figure source must match "
                                "a frozen analysis dataset input"
                            )
                        dataset, total_points = _read_figure_dataset(
                            services,
                            repository,
                            source,
                            layer.projection,
                            figure_layer_budget(
                                len(output.content.layers), layer_index
                            ),
                        )
                    layers.append((layer, dataset, total_points))
                selected[output.id] = project_figure_layers(layers)
        except (KeyError, TypeError, ValueError) as error:
            _raise_analysis_problem(
                "analysis_view_projection_unknown",
                f"analysis view projection is invalid: {error}",
                "outputs",
                index,
                "content",
            )
    try:
        validate_analysis_output_content_budget(selected.values())
    except ValueError as error:
        _raise_analysis_problem(
            "analysis_view_budget_exceeded",
            str(error),
            "outputs",
        )
    return selected


def _validate_analysis_execution_outputs(
    executions: Sequence[AnalysisExecution],
    outputs: Sequence[AnalysisOutput],
) -> None:
    execution_by_id = {execution.id: execution for execution in executions}
    if len(execution_by_id) != len(executions):
        _raise_analysis_problem(
            "analysis_execution_id_duplicated",
            "analysis execution ids must be unique",
            "executions",
        )
    for output in outputs:
        if not isinstance(
            output,
            AnalysisDatasetOutput | AnalysisFactOutput | AnalysisArtifactOutput,
        ):
            continue
        if (
            isinstance(output, AnalysisDatasetOutput)
            and output.produced_by is not None
            and output.derived_from is not None
        ):
            _raise_analysis_problem(
                "analysis_output_source_invalid",
                "analysis dataset output cannot be both produced and derived",
                "outputs",
            )
        source = (
            output.derived_from.source
            if isinstance(output, AnalysisDatasetOutput)
            and output.derived_from is not None
            else output.produced_by
        )
        if source is None:
            continue
        execution = execution_by_id.get(source.execution_id)
        if execution is None:
            _raise_analysis_problem(
                "analysis_output_producer_unknown",
                "analysis output producer must identify an execution",
                "outputs",
            )
        if isinstance(output, AnalysisDatasetOutput):
            content_hash = sha256_content_hash(output.content.to_arrow_ipc())
            expected_kind = "derived_dataset"
            expected_codec = DERIVED_DATASET_CODEC
        elif isinstance(output, AnalysisArtifactOutput):
            content_hash = sha256_content_hash(output.content)
            expected_kind = "artifact"
            expected_codec = ANALYSIS_ARTIFACT_CODEC
        else:
            content_hash = f"sha256:{stable_content_hash(output.content.value)}"
            expected_kind = "value"
            expected_codec = output.content.codec
        execution_output = next(
            (item for item in execution.outputs if item.name == source.output_name),
            None,
        )
        if execution_output is None:
            _raise_analysis_problem(
                "analysis_output_producer_unknown",
                "analysis output producer must identify an execution output",
                "outputs",
            )
        if (
            isinstance(output, AnalysisDatasetOutput)
            and output.derived_from is not None
        ):
            if (
                execution_output.kind != "derived_dataset"
                or execution_output.codec != DERIVED_DATASET_CODEC
            ):
                _raise_analysis_problem(
                    "analysis_output_execution_mismatch",
                    "analysis dataset derivation source must be a dataset result",
                    "outputs",
                )
            continue
        if (
            execution_output.kind != expected_kind
            or execution_output.content_hash != content_hash
            or execution_output.codec != expected_codec
        ):
            _raise_analysis_problem(
                "analysis_output_execution_mismatch",
                "analysis output content does not match its producing execution",
                "outputs",
            )


def _validate_analysis_proposal_evidence(
    outputs: Sequence[AnalysisOutput],
    proposals: Sequence[ParameterChangeProposal],
) -> None:
    authoritative_output_ids = {
        output.id
        for output in outputs
        if isinstance(
            output,
            AnalysisFactOutput | AnalysisDatasetOutput | AnalysisArtifactOutput,
        )
    }
    for proposal in proposals:
        unknown = set(proposal.evidence_output_ids) - authoritative_output_ids
        if unknown:
            _raise_analysis_problem(
                "analysis_parameter_proposal_evidence_unknown",
                "analysis parameter proposal evidence must identify fact, dataset, "
                "or artifact outputs: " + ", ".join(sorted(unknown)),
                "parameter_proposals",
            )


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
