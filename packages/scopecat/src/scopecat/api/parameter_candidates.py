"""Receipt-backed parameter proposals and explicit independent verification."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, Protocol, cast
from uuid import uuid4

from scopecat.analysis.facts import ordinary_result_schema
from scopecat.api._remote import RemoteRunOperations
from scopecat.api.analysis import AnalysisContext
from scopecat.api.parameters import ParameterVersion, RowKey
from scopecat.api.project_analysis import RemoteProjectAnalysisOperations
from scopecat.api.published_analysis import AnalysisResult, PublishedAnalysis
from scopecat.api.run import RunHandle
from scopecat.authoring.parameter_fields import (
    ResolvedParameterField,
    require_parameter_field,
    stored_parameter_value,
)
from scopecat.authoring.parameter_models import (
    ParameterFieldIdentity,
    ParameterModel,
    parameter_fields,
    parameter_key,
    parameter_table_name,
)
from scopecat.config.candidates import CandidateConfig
from scopecat.config.parameter_updates import update_parameter_rows
from scopecat.daemon.client import DaemonClient, DaemonConflictError
from scopecat.daemon.wire import (
    ConfigContextPublishCommand,
    ConfigContextPublishReceipt,
    ConfigPublishReceipt,
    ParameterBranchPublishCommand,
    ParameterCandidateComposeCommand,
)
from scopecat.kernel.content_identity import model_wire_content_hash
from scopecat.kernel.entity import EntityRef
from scopecat.kernel.value_types import Table
from scopecat.kernel.value_validation import coerce_literal
from scopecat.records.analysis import ProjectAnalysisDecisionReference
from scopecat.records.author_revision import AuthorRevisionRef
from scopecat.records.config import ConfigProfileSnapshot
from scopecat.records.config_context import ConfigContextRef
from scopecat.records.parameter import ParameterAtomValue
from scopecat.records.parameter_branch import ParameterBranch
from scopecat.records.parameter_change import ParameterProposalRef
from scopecat.records.run import RunConfigSource


class CandidateOperations(Protocol):
    @property
    def client(self) -> DaemonClient: ...

    @property
    def run_operations(self) -> RemoteRunOperations: ...

    def resolve_with_source(
        self, config: CandidateConfig
    ) -> tuple[ConfigProfileSnapshot, RunConfigSource | None]: ...

    @property
    def operator(self) -> str: ...

    def publish_context(
        self, command: ConfigContextPublishCommand
    ) -> ConfigContextPublishReceipt: ...

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

    def combine(
        self, *others: ParameterCandidate, name: str, note: str = ""
    ) -> ParameterCandidate:
        """Save a joint candidate; individual verification is not inherited.

        Sources must share exact independent parameters, setup and scientific
        scope. Prepare and verify the returned candidate before publishing it.
        """
        return self._compose(others, name=name, note=note, mode="parallel")

    def then(
        self, *later: ParameterCandidate, name: str, note: str = ""
    ) -> ParameterCandidate:
        """Retain a sequential chain as one candidate against the original base.

        Each later proposal must come from a successful measurement using the
        immediately preceding exact candidate. Later stages may refine the same
        cells. This records completed work, not scheduling; independently verify
        the returned candidate before publishing it to the original branch.
        """
        return self._compose(later, name=name, note=note, mode="sequential")

    def _compose(
        self,
        others: tuple[ParameterCandidate, ...],
        *,
        name: str,
        note: str,
        mode: Literal["parallel", "sequential"],
    ) -> ParameterCandidate:
        proposals = (
            self.config.parameter_proposal,
            *(item.config.parameter_proposal for item in others),
        )
        saved = self.operations.client.compose_parameter_candidate(
            self.config.source_run_id,
            ParameterCandidateComposeCommand(
                name=name,
                mode=mode,
                note=note,
                sources=tuple(
                    ParameterProposalRef(
                        run_id=proposal.source_run_id,
                        proposal_id=proposal.id,
                        analysis_record_id=proposal.analysis_record_id,
                        content_hash=f"sha256:{model_wire_content_hash(proposal)}",
                    )
                    for proposal in proposals
                ),
            ),
        )
        [proposal] = saved.parameter_proposals
        return ParameterCandidate(self.operations, CandidateConfig(proposal))

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
        composition = self.config.parameter_proposal.composition
        if composition is not None:
            for index, run_id in enumerate(
                sorted(
                    {source.run_id for source in composition.sources} - {baseline.id}
                )
            ):
                context.measurements(
                    RunHandle(self.operations, run_id),
                    id=f"baseline-{index + 1}",
                    role="baseline",
                )
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

    def publish_to_branch(
        self,
        branch: ParameterBranch,
        *,
        name: str,
        note: str = "",
    ) -> ParameterBranch:
        """Accept into an exact branch head; repeat identical arguments to retry.

        This does not move a session checkout or change setup/default selection.
        The receipt records acceptance of these cells, not branch-wide validity.
        """
        decision = self.verification.fact("decision")
        return self.candidate.operations.client.publish_parameter_branch(
            ParameterBranchPublishCommand(
                name=branch.name,
                expected_generation=branch.generation,
                base=branch.revision,
                run_id=self.candidate.config.source_run_id,
                proposal_id=self.candidate.config.proposal_id,
                verification=ProjectAnalysisDecisionReference(
                    analysis_record_id=self.verification.id,
                    output_id="decision",
                    schema_id=decision.schema_id,
                    schema_hash=decision.schema_hash,
                ),
                revision_id=name,
                actor=self.candidate.operations.operator,
                note=note,
            )
        )

    def select(self) -> ParameterCandidate:
        """Return this exact candidate for one explicit prepare(candidate=...) call."""
        return self.candidate

    def publish_to(
        self,
        *,
        working_point: ParameterVersion | ConfigContextRef,
        name: str,
        note: str = "",
        operation_id: str | None = None,
    ) -> ParameterVersion:
        """Publish to this exact working point; unrelated heads stay unchanged."""
        base = (
            working_point.context
            if isinstance(working_point, ParameterVersion)
            else working_point
        )
        decision = self.verification.fact("decision")
        receipt = self.candidate.operations.publish_context(
            ConfigContextPublishCommand(
                operation_id=operation_id or f"context-publish-{uuid4().hex}",
                base=base,
                run_id=self.candidate.config.source_run_id,
                proposal_id=self.candidate.config.proposal_id,
                verification=ProjectAnalysisDecisionReference(
                    analysis_record_id=self.verification.id,
                    output_id="decision",
                    schema_id=decision.schema_id,
                    schema_hash=decision.schema_hash,
                ),
                entry_id=name,
                actor=self.candidate.operations.operator,
                note=note,
            )
        )
        return ParameterVersion(
            ConfigContextRef(
                entry_id=receipt.entry.id,
                content_hash=receipt.entry.content_hash,
            )
        )

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
    table: str | type[ParameterModel],
    key: RowKey,
    fields: Mapping[str | ParameterFieldIdentity, str],
    note: str = "",
) -> ParameterCandidate:
    """Map exact receipt fields into existing keyed cell proposals."""
    table = table if isinstance(table, str) else parameter_table_name(table)
    targets: dict[str, str] = {}
    declarations: dict[str, ResolvedParameterField] = {}
    declared_keys: set[tuple[str, ...]] = set()
    for target, source in fields.items():
        if not isinstance(target, str):
            if parameter_table_name(target.owner) != table:
                raise ValueError(
                    f"{target.name}: field belongs to a different table than {table}"
                )
            declared_keys.add(parameter_key(target.owner))
            declarations[target.name] = next(
                field
                for field in parameter_fields(target.owner)
                if field.name == target.name
            )
            target = target.name
        if target in targets:
            raise ValueError(f"{table}.{target}: duplicate candidate target")
        targets[target] = source
    run, publication, value = _managed_result(operations, result)
    definition = run.config.parameter_catalog.get(table)
    if definition is None or not isinstance(definition.value_type, Table):
        raise ValueError(f"{table}: select an existing parameter table")
    schema = definition.value_type
    if any(declared != schema.primary_key for declared in declared_keys):
        raise ValueError(f"{table}: declared primary key differs from the frozen table")
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
    for target, source in targets.items():
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
        if target in declarations:
            declared = declarations[target]
            require_parameter_field(declared.value_type, columns[target], label=label)
            selected = stored_parameter_value(selected, declared, label=label)
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
        .artifact("cell-mapping", text=str(targets))
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
