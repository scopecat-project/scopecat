"""Follow typed scientific references from selected runs in one read snapshot."""

import sqlite3
from collections.abc import Iterable, Iterator, Mapping
from pathlib import Path
from typing import cast

from pydantic import BaseModel
from scopecat.automation.models import AnalysisPublicationOutputRef, RunOutputRef
from scopecat.config.registry.records import CandidateConfigRegistrySource
from scopecat.data_exchange.models import (
    AnalysisEvidence,
    InputRevisionEvidence,
    InterpretationEvidence,
    RunEvidence,
    ScientificEvidence,
)
from scopecat.measurements.datasets import MEASUREMENT_DATASET_CODEC
from scopecat.records.analysis import (
    CONFIGURATION_ANALYSIS_INPUT_CODEC,
    AnalysisInterpretationReference,
    AnalysisPublishedOutputReference,
    AnalysisSubject,
    ConfigurationAnalysisRecordInput,
    InterpretationAnalysisRecordInput,
    MeasurementAnalysisRecordInput,
    ProjectAnalysisOutputReference,
    ProjectAnalysisSubject,
    RunAnalysisSubject,
)
from scopecat.records.candidate_input import AnalysisCandidateRunConfigSource
from scopecat.records.parameter_branch import ParameterBranchPublication
from scopecat.records.parameter_change import (
    ParameterChangeProposal,
    ParameterProposalRef,
)
from scopecat.records.plan_ref import PlanAnalysisSource
from scopecat.runs.refs import CONFIG_PROFILE_SNAPSHOT_REF, record_content_ref

from scopecat_server.storage.sqlite.evidence_analysis import (
    capture_analysis_input_graph,
)
from scopecat_server.storage.sqlite.evidence_export import capture_run_evidence
from scopecat_server.storage.sqlite.evidence_inputs import capture_input_revisions
from scopecat_server.storage.sqlite.evidence_interpretation import (
    capture_interpretation_evidence,
)
from scopecat_server.storage.sqlite.exchange_writer import write_captured_exchange
from scopecat_server.storage.sqlite.project_store import SQLiteProjectStore
from scopecat_server.storage.sqlite.run_repository import SQLiteRunRepository


def _models(value: object) -> Iterator[BaseModel]:
    if isinstance(value, BaseModel):
        yield value
        for name in type(value).model_fields:
            yield from _models(cast("object", getattr(value, name)))
    elif isinstance(value, Mapping):
        for item in cast("Mapping[object, object]", value).values():
            yield from _models(item)
    elif isinstance(value, tuple | list):
        for item in cast("Iterable[object]", value):
            yield from _models(item)


class _Capture:
    def __init__(self, connection: sqlite3.Connection, store: SQLiteProjectStore):
        self.connection = connection
        self.store = store
        self.repository = SQLiteRunRepository(store.sqlite, store.objects.root)
        self.runs: dict[str, RunEvidence] = {}
        self.analyses: dict[tuple[AnalysisSubject, str], AnalysisEvidence] = {}
        self.interpretations: dict[
            AnalysisInterpretationReference, InterpretationEvidence
        ] = {}
        self.pending_runs: set[str] = set()
        self.pending_analyses: set[tuple[AnalysisSubject, str]] = set()
        self.pending_interpretations: set[AnalysisInterpretationReference] = set()
        self.documents: list[BaseModel] = []

    def scan(self, document: BaseModel) -> None:
        self.documents.append(document)
        for item in _models(document):
            match item:
                case (
                    RunAnalysisSubject()
                    | RunOutputRef()
                    | MeasurementAnalysisRecordInput()
                    | ConfigurationAnalysisRecordInput()
                ):
                    self.pending_runs.add(item.run_id)
                case AnalysisCandidateRunConfigSource() | ParameterChangeProposal():
                    self.pending_runs.add(item.source_run_id)
                    self.pending_analyses.add(
                        (
                            RunAnalysisSubject(run_id=item.source_run_id),
                            item.analysis_record_id,
                        )
                    )
                case PlanAnalysisSource():
                    self.pending_runs.add(item.run_id)
                    self.pending_analyses.add(
                        (RunAnalysisSubject(run_id=item.run_id), item.analysis_id)
                    )
                case ParameterProposalRef():
                    self.pending_runs.add(item.run_id)
                    self.pending_analyses.add(
                        (
                            RunAnalysisSubject(run_id=item.run_id),
                            item.analysis_record_id,
                        )
                    )
                case CandidateConfigRegistrySource() | ParameterBranchPublication():
                    self.pending_runs.add(item.run_id)
                case ProjectAnalysisOutputReference():
                    self.pending_analyses.add(
                        (ProjectAnalysisSubject(), item.analysis_record_id)
                    )
                case (
                    AnalysisPublicationOutputRef() | AnalysisPublishedOutputReference()
                ):
                    self.pending_analyses.add((item.subject, item.analysis_record_id))
                case AnalysisInterpretationReference():
                    self.pending_interpretations.add(item)
                case _:
                    pass  # Containers and inert metadata are traversed, not references.

    def drain(self) -> None:
        while (
            self.pending_runs or self.pending_analyses or self.pending_interpretations
        ):
            while self.pending_runs:
                run_id = self.pending_runs.pop()
                if run_id in self.runs:
                    continue
                run = capture_run_evidence(self.connection, self.repository, run_id)
                self.runs[run_id] = run
                self.scan(run)
                for entry in run.contents:
                    if entry.role == "record" and entry.kind == "analysis":
                        self.pending_analyses.add(
                            (RunAnalysisSubject(run_id=run_id), entry.id)
                        )
                    elif (
                        entry.role == "record"
                        and entry.kind == "parameter_change_proposal"
                    ):
                        self.scan(
                            ParameterChangeProposal.model_validate_json(
                                self.repository.read_bytes_in_transaction(
                                    self.connection,
                                    run_id,
                                    record_content_ref(
                                        record_id=entry.id, kind=entry.kind
                                    ),
                                )
                            )
                        )
            roots = self.pending_analyses - self.analyses.keys()
            self.pending_analyses.clear()
            for captured in capture_analysis_input_graph(
                self.connection, self.repository, roots
            ):
                analysis = captured.evidence
                identity = (analysis.record.subject, analysis.entry.id)
                if identity not in self.analyses:
                    self.analyses[identity] = analysis
                    self.scan(analysis)
            while self.pending_interpretations:
                reference = self.pending_interpretations.pop()
                if reference in self.interpretations:
                    continue
                evidence = capture_interpretation_evidence(self.connection, reference)
                self.interpretations[reference] = evidence
                self.scan(evidence)

    def validate_edges(self) -> None:
        for document in self.documents:
            for item in _models(document):
                match item:
                    case PlanAnalysisSource():
                        source = self.analyses[
                            (RunAnalysisSubject(run_id=item.run_id), item.analysis_id)
                        ]
                        if source.record.publication_hash != item.publication_hash:
                            raise ValueError(
                                "plan analysis source differs from retained publication"
                            )
                    case ConfigurationAnalysisRecordInput():
                        if (
                            item.target != CONFIG_PROFILE_SNAPSHOT_REF
                            or item.codec != CONFIGURATION_ANALYSIS_INPUT_CODEC
                            or item.content_hash
                            != self.runs[item.run_id].snapshot.config_content_hash
                        ):
                            raise ValueError(
                                "configuration input differs from retained run"
                            )
                    case MeasurementAnalysisRecordInput():
                        entry = next(
                            (
                                entry
                                for entry in self.runs[item.run_id].contents
                                if entry.role == "dataset" and entry.id == item.target
                            ),
                            None,
                        )
                        if (
                            entry is None
                            or entry.kind != "measurement_dataset"
                            or entry.content_hash != item.content_hash
                            or item.codec != MEASUREMENT_DATASET_CODEC
                        ):
                            raise ValueError(
                                "measurement input differs from retained dataset"
                            )
                    case InterpretationAnalysisRecordInput():
                        if (
                            item.target != item.source.step_key
                            or item.content_hash != item.source.response_hash
                            or item.codec != "scopecat.interpretation-response.v1"
                        ):
                            raise ValueError(
                                "interpretation input differs from retained judgment"
                            )
                    case _:
                        pass


def capture_scientific_evidence(
    connection: sqlite3.Connection, store: SQLiteProjectStore, run_ids: Iterable[str]
) -> ScientificEvidence:
    """Resolve retained dependencies without loading author modules or devices."""
    roots = tuple(sorted(set(run_ids)))
    if not roots:
        raise ValueError("select at least one run to export")
    capture = _Capture(connection, store)
    capture.pending_runs.update(roots)
    inputs = InputRevisionEvidence()
    while True:
        capture.drain()
        updated = capture_input_revisions(connection, store, capture.documents)
        if updated == inputs:
            break
        inputs = updated
        capture.scan(inputs)
    capture.validate_edges()
    return ScientificEvidence(
        source_project_id=capture.runs[roots[0]].source_project_id,
        roots=roots,
        runs=tuple(capture.runs[key] for key in sorted(capture.runs)),
        inputs=inputs,
        analyses=tuple(
            sorted(
                capture.analyses.values(),
                key=lambda item: (item.record.subject.model_dump_json(), item.entry.id),
            )
        ),
        interpretations=tuple(
            sorted(
                capture.interpretations.values(),
                key=lambda item: item.reference.model_dump_json(),
            )
        ),
    )


def export_scientific_capture(
    store: SQLiteProjectStore, run_ids: Iterable[str], destination: Path
) -> None:
    """Capture and publish a selected-run package through one read transaction."""
    with store.sqlite.read_transaction() as connection:
        evidence = capture_scientific_evidence(connection, store, run_ids)
        write_captured_exchange(connection, store, evidence, destination)
