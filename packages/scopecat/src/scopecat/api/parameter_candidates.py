"""Receipt-backed parameter proposals and explicit independent verification."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol, cast

from scopecat.analysis.facts import ordinary_result_schema
from scopecat.api._remote import RemoteRunOperations
from scopecat.api.analysis import AnalysisContext
from scopecat.api.parameters import RowKey
from scopecat.api.project_analysis import RemoteProjectAnalysisOperations
from scopecat.api.published_analysis import AnalysisResult, PublishedAnalysis
from scopecat.api.run import RunHandle
from scopecat.config.candidates import CandidateConfig
from scopecat.config.parameter_updates import update_parameter_rows
from scopecat.daemon.client import DaemonClient, DaemonConflictError
from scopecat.daemon.wire import ConfigPublishReceipt
from scopecat.kernel.entity import EntityRef
from scopecat.kernel.value_types import Table
from scopecat.kernel.value_validation import coerce_literal
from scopecat.records.author_revision import AuthorRevisionRef
from scopecat.records.config import ConfigProfileSnapshot
from scopecat.records.parameter import ParameterAtomValue
from scopecat.records.run import AnalysisCandidateRunConfigSource, RunConfigSource


class CandidateOperations(Protocol):
    @property
    def client(self) -> DaemonClient: ...

    @property
    def run_operations(self) -> RemoteRunOperations: ...

    def resolve_with_source(
        self, config: CandidateConfig
    ) -> tuple[ConfigProfileSnapshot, RunConfigSource | None]: ...

    def accept_verified(
        self,
        candidate: CandidateConfig,
        *,
        verified_by: tuple[PublishedAnalysis, str],
        entry_id: str | None = None,
        note: str = "",
    ) -> ConfigPublishReceipt: ...


@dataclass(frozen=True, slots=True)
class ParameterCandidate:
    """One saved proposal, not a validated or activated parameter version."""

    operations: CandidateOperations
    config: CandidateConfig

    @property
    def name(self) -> str:
        return self.config.proposal_id

    @property
    def cells(self) -> tuple[str, ...]:
        return tuple(
            f"{delta.parameter_id}[{_key_label(cell.key)}].{cell.field}"
            for delta in self.config.parameter_proposal.deltas
            for cell in (delta.cells or ())
        )

    def verify[ResultT](
        self, result: AnalysisResult[ResultT]
    ) -> VerifiedParameterCandidate:
        """Bind an explicit retained policy decision to independent candidate data.

        The policy belongs to the laboratory's registered analysis and must
        return an ``accepted: bool`` field. No numeric threshold is inferred here.
        """
        run, publication, value = _managed_result(self.operations, result)
        baseline = RunHandle(self.operations, self.config.source_run_id)
        snapshot = run.snapshot
        _, expected_source = self.operations.resolve_with_source(self.config)
        actual_source = snapshot.config_source
        if isinstance(actual_source, AnalysisCandidateRunConfigSource):
            actual_source = actual_source.model_copy(
                update={"registry_generation": None}
            )
        if run.id == baseline.id or actual_source != expected_source:
            raise ValueError(
                f"{', '.join(self.cells)}: verification needs an independent retained "
                "run using this exact candidate; prepare(..., candidate=candidate)"
            )
        if snapshot.samples != baseline.samples:
            raise ValueError(
                "candidate verification requires the same sample revision/workpoint"
            )
        accepted = getattr(value, "accepted", None)
        if not isinstance(accepted, bool):
            raise ValueError(
                "verification analysis must declare accepted: bool; "
                "a numeric fit is not verification"
            )
        context = AnalysisContext(
            owner=RemoteProjectAnalysisOperations(self.operations.client),
            default_title=f"Verify {self.name}",
            default_key=f"verify-{self.name}-{publication.id}",
        )
        context.measurements(baseline, id="baseline", role="baseline")
        context.measurements(run, id="candidate", role="candidate")
        decision = context.analysis_fact(
            publication,
            "result",
            schema=ordinary_result_schema(type(value)),
            id="policy",
            role="decision",
        )
        saved = (
            context.result()
            .fact("decision", decision, schema=ordinary_result_schema(type(value)))
            .save()
        )
        if not accepted:
            raise ValueError(
                f"{', '.join(self.cells)}: verification rejected this candidate; "
                f"decision retained as {saved.id}. "
                "Inspect the policy result and collect new data."
            )
        return VerifiedParameterCandidate(self, saved)


@dataclass(frozen=True, slots=True)
class VerifiedParameterCandidate:
    """A positive independent decision; publication remains an explicit action."""

    candidate: ParameterCandidate
    verification: PublishedAnalysis

    def select(self) -> ParameterCandidate:
        """Return this exact candidate for one explicit prepare(candidate=...) call."""
        return self.candidate

    def publish_default(self, *, name: str, note: str = "") -> ConfigPublishReceipt:
        """Change the shared default through existing verified acceptance fences."""
        try:
            return self.candidate.operations.accept_verified(
                self.candidate.config,
                verified_by=(self.verification, "decision"),
                entry_id=name,
                note=note,
            )
        except DaemonConflictError as error:
            raise ValueError(
                f"{', '.join(self.candidate.cells)}: "
                "cannot publish this stale candidate: "
                f"{error}. Revisit the current context and explicitly "
                "rebase its workspace; "
                "a new proposal requires independent verification again."
            ) from error


def stage_candidate[ResultT](
    operations: CandidateOperations,
    result: AnalysisResult[ResultT],
    *,
    name: str,
    table: str,
    key: RowKey,
    fields: Mapping[str, str],
    note: str = "",
) -> ParameterCandidate:
    """Map exact receipt fields into existing keyed cell proposals."""
    run, publication, value = _managed_result(operations, result)
    definition = run.config.parameter_catalog.get(table)
    if definition is None or not isinstance(definition.value_type, Table):
        raise ValueError(f"{table}: select an existing parameter table")
    schema = definition.value_type
    keys = key if isinstance(key, tuple) else (key,)
    if not schema.primary_key or len(keys) != len(schema.primary_key):
        raise ValueError(f"{table}[{key!r}]: expected key fields {schema.primary_key}")
    columns = {column.id: column.value_type for column in schema.columns}
    selected_key = {
        field: cast(
            "ParameterAtomValue",
            coerce_literal(columns[field], item, path=(table, str(key), field)),
        )
        for field, item in zip(schema.primary_key, keys, strict=True)
    }
    values: dict[str, ParameterAtomValue] = {}
    for target, source in fields.items():
        label = f"{table}[{key!r}].{target}"
        if target not in columns or target in schema.primary_key:
            raise ValueError(f"{label}: select an existing non-key field")
        if not hasattr(value, source):
            raise ValueError(f"{label}: receipt has no result field {source!r}")
        selected = cast("object", getattr(value, source))
        if selected is None:
            raise ValueError(
                f"{label}: result.{source} is unknown; no candidate was staged"
            )
        values[target] = cast(
            "ParameterAtomValue",
            coerce_literal(columns[target], selected, path=(table, str(key), target)),
        )
    context = run.analysis(f"Candidate {name}", key=f"candidate-{name}")
    evidence = context.analysis_fact(
        publication,
        "result",
        schema=ordinary_result_schema(type(value)),
        id="fit",
    )
    saved = (
        context.result()
        .fact("fit", evidence, schema=ordinary_result_schema(type(value)))
        .artifact("cell-mapping", text=str(dict(fields)))
        .propose(
            name,
            update_parameter_rows(table, key=selected_key, values=values),
            reason=note or f"Stage named fields from {publication.id}",
            evidence=("fit",),
        )
        .save()
    )
    return ParameterCandidate(operations, saved.candidate_config())


def _managed_result[ResultT](
    operations: CandidateOperations,
    result: AnalysisResult[ResultT],
) -> tuple[RunHandle, PublishedAnalysis, ResultT]:
    subject = result.publication.view.analysis.subject
    if subject.kind != "run":
        raise ValueError("managed result must belong to one retained run")
    run = RunHandle(operations, subject.run_id)
    snapshot = run.snapshot
    if snapshot.outcome is None or snapshot.outcome.result != "succeeded":
        raise ValueError(
            "candidate evidence requires a completed successful retained run"
        )
    publication = run.published_analysis(result.publication.id)
    try:
        revision = publication.fact("author_code_revision").value
    except KeyError:
        raise ValueError(
            "result has no managed author source receipt; "
            "manual values belong in a workspace"
        ) from None
    if not isinstance(revision, str):
        raise ValueError("result has no managed author source revision")
    operations.client.author_revision(AuthorRevisionRef(content_hash=revision))
    value = publication.fact_as("result", ordinary_result_schema(type(result.value)))
    return run, publication, value


def _key_label(key: Mapping[str, ParameterAtomValue]) -> str:
    return ", ".join(
        f"{name}={value.id!r}" if isinstance(value, EntityRef) else f"{name}={value!r}"
        for name, value in key.items()
    )
