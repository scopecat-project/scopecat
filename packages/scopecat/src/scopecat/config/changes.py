"""Parameter change proposals and their optional operator approval."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from scopecat.config.parameter_resolution import validate_parameter_snapshot
from scopecat.config.parameter_updates import (
    ParameterUpdate,
    materialize_parameter_updates,
)
from scopecat.kernel.content_identity import model_wire_content_hash
from scopecat.kernel.errors import CheckFailed, Conflict, DataIntegrityError, NotFound
from scopecat.kernel.ids import artifact_slug
from scopecat.kernel.problems import (
    Problem,
    ProblemLocation,
    ProblemPhase,
    StorageLocation,
    model_location,
    problem,
)
from scopecat.project_state import ProjectStateServices
from scopecat.records.config import ConfigProfileSnapshot, config_content_hash
from scopecat.records.content import ContentEntry, ModelWrite
from scopecat.records.parameter_change import (
    ParameterChangeApprovalRecord,
    ParameterChangeProposal,
)
from scopecat.runs.refs import record_content_ref
from scopecat.runs.repository import (
    RunContentPublication,
    RunRepository,
)

SAFE_PARAMETER_CHANGE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


@dataclass(frozen=True, slots=True)
class PreparedParameterChangeProposals:
    entries: tuple[ContentEntry, ...]
    writes: tuple[ModelWrite, ...]


@dataclass(frozen=True, slots=True)
class PreparedParameterChangeApproval:
    approval: ParameterChangeApprovalRecord
    publication: RunContentPublication | None


@dataclass(frozen=True, slots=True)
class ParameterChangeProposalPage:
    """Newest-first bounded page of one run's parameter proposals."""

    items: tuple[ParameterChangeProposal, ...] = ()
    next_cursor: int | None = None


def is_safe_parameter_change_id(value: str) -> bool:
    return SAFE_PARAMETER_CHANGE_ID_RE.fullmatch(value) is not None


def parameter_change_proposal_from_updates(
    *,
    source_run_id: str,
    source_config: ConfigProfileSnapshot,
    analysis_title: str,
    analysis_record_id: str,
    proposal_id: str,
    updates: Sequence[ParameterUpdate],
    reason: str,
    confidence: float | None,
    evidence_output_ids: Sequence[str] = (),
) -> ParameterChangeProposal:
    selected_id = artifact_slug(proposal_id, fallback="analysis")
    if not is_safe_parameter_change_id(selected_id):
        raise CheckFailed(
            [
                _parameter_problem(
                    "parameter_change_invalid_id",
                    "parameter change proposal id is not safe for record paths",
                    phase=ProblemPhase.ANALYSIS,
                    location=model_location("parameter_change_proposal", "id"),
                    details={"proposal_id": selected_id},
                )
            ]
        )
    selected_reason = reason or (
        f"Parameter change {selected_id!r} from analysis {analysis_title!r}."
    )
    candidate, deltas = materialize_parameter_updates(
        catalog=source_config.parameter_catalog,
        base=source_config.parameter_snapshot,
        updates=updates,
        candidate_id=f"{source_config.parameter_snapshot.id}.candidate.{selected_id}",
    )
    problems = validate_parameter_snapshot(
        source_config.parameter_catalog,
        candidate,
        allow_missing=True,
    )
    if problems:
        raise CheckFailed(problems)
    return ParameterChangeProposal(
        id=selected_id,
        source_run_id=source_run_id,
        analysis_record_id=analysis_record_id,
        base_config_id=source_config.id,
        base_config_content_hash=config_content_hash(source_config),
        reason=selected_reason,
        confidence=confidence,
        evidence_output_ids=tuple(evidence_output_ids),
        deltas=deltas,
    )


def load_parameter_change_proposal(
    *, run_id: str, selector: str, services: ProjectStateServices
) -> ParameterChangeProposal:
    storage = services.runs
    proposal, _record = _resolve_proposal_ref(
        storage=storage,
        run_id=run_id,
        selector=selector,
    )
    return proposal


def list_parameter_change_proposals(
    *,
    run_id: str,
    services: ProjectStateServices,
    limit: int = 100,
    before: int | None = None,
) -> ParameterChangeProposalPage:
    """Load one bounded newest-first proposal page without repeated scans."""

    storage = services.runs
    page = storage.list_contents(
        run_id,
        limit=limit,
        before=before,
        role="record",
        kind="parameter_change_proposal",
    )
    return ParameterChangeProposalPage(
        items=tuple(
            _load_proposal_record(
                storage=storage,
                run_id=run_id,
                proposal_record=entry,
            )
            for entry in page.items
        ),
        next_cursor=page.next_cursor,
    )


def prepare_parameter_change_approval(
    *,
    run_id: str,
    selector: str,
    services: ProjectStateServices,
    actor: str,
    note: str = "",
) -> PreparedParameterChangeApproval:
    """Prepare the proposal's one immutable approval."""

    storage = services.runs
    proposal, _proposal_record = _resolve_proposal_ref(
        storage=storage,
        run_id=run_id,
        selector=selector,
    )
    approval = ParameterChangeApprovalRecord(
        run_id=run_id,
        proposal_id=proposal.id,
        actor=actor,
        note=note,
    )
    existing = load_parameter_change_approval(
        run_id=run_id,
        selector=proposal.id,
        storage=storage,
    )
    if existing is not None:
        if existing.actor != approval.actor or existing.note != approval.note:
            raise Conflict(
                [
                    _parameter_problem(
                        "parameter_change_approval_conflict",
                        "parameter change proposal already has a different approval",
                        phase=ProblemPhase.PERSISTENCE,
                        location=StorageLocation(
                            run_id=run_id,
                            ref=record_content_ref(
                                record_id=f"{proposal.id}-approval",
                                kind="parameter_change_approval_record",
                            ),
                        ),
                        details={"proposal_id": proposal.id},
                    )
                ]
            )
        return PreparedParameterChangeApproval(
            approval=existing,
            publication=None,
        )
    approval_entry = _parameter_change_approval_record_entry(approval)
    approval_ref = record_content_ref(
        record_id=approval_entry.id,
        kind=approval_entry.kind,
    )
    return PreparedParameterChangeApproval(
        approval=approval,
        publication=RunContentPublication(
            run_id=run_id,
            entries=(approval_entry,),
            models=(
                ModelWrite(
                    ref=approval_ref,
                    value=approval,
                    replace=False,
                ),
            ),
        ),
    )


def load_parameter_change_approval(
    *,
    run_id: str,
    selector: str,
    storage: RunRepository,
) -> ParameterChangeApprovalRecord | None:
    proposal, _record = _resolve_proposal_ref(
        storage=storage,
        run_id=run_id,
        selector=selector,
    )
    return load_parameter_change_approval_for_proposal(
        run_id=run_id,
        proposal=proposal,
        storage=storage,
    )


def load_parameter_change_approval_for_proposal(
    *,
    run_id: str,
    proposal: ParameterChangeProposal,
    storage: RunRepository,
) -> ParameterChangeApprovalRecord | None:
    """Load the deterministic approval record for an already loaded proposal."""

    approval_id = f"{proposal.id}-approval"
    try:
        entry = storage.read_content(
            run_id,
            role="record",
            content_id=approval_id,
        )
    except NotFound:
        return None
    if entry.kind != "parameter_change_approval_record":
        raise DataIntegrityError(
            [
                _parameter_problem(
                    "invalid_parameter_change_approval_kind",
                    "parameter change approval identity names a different record kind",
                    phase=ProblemPhase.PERSISTENCE,
                    location=StorageLocation(run_id=run_id),
                    details={"record_id": entry.id, "kind": entry.kind},
                )
            ]
        )
    approval_ref = record_content_ref(record_id=entry.id, kind=entry.kind)
    try:
        approval = storage.read_model(
            run_id,
            approval_ref,
            ParameterChangeApprovalRecord,
        )
    except DataIntegrityError as error:
        raise DataIntegrityError(
            [
                _parameter_problem(
                    "invalid_parameter_change_approval",
                    "parameter change approval record is invalid",
                    phase=ProblemPhase.PERSISTENCE,
                    location=StorageLocation(run_id=run_id, ref=approval_ref),
                    details={"record_id": entry.id},
                )
            ]
        ) from error
    if (
        approval.run_id != run_id
        or approval.proposal_id != proposal.id
        or entry.id != approval_id
    ):
        raise DataIntegrityError(
            [
                _parameter_problem(
                    "invalid_parameter_change_approval_identity",
                    "parameter change approval identity does not match its run record",
                    phase=ProblemPhase.PERSISTENCE,
                    location=StorageLocation(run_id=run_id, ref=approval_ref),
                    details={
                        "record_id": entry.id,
                        "approval_run_id": approval.run_id,
                        "expected_record_id": approval_id,
                    },
                )
            ]
        )
    return approval


def parameter_change_proposal_record_ref(proposal_id: str) -> str:
    return record_content_ref(
        record_id=proposal_id,
        kind="parameter_change_proposal",
    )


def _parameter_change_proposal_record(
    *, proposal: ParameterChangeProposal
) -> ContentEntry:
    return ContentEntry(
        role="record",
        id=proposal.id,
        kind="parameter_change_proposal",
        media_type="application/json",
        content_hash=model_wire_content_hash(proposal),
    )


def _parameter_change_approval_record_entry(
    record: ParameterChangeApprovalRecord,
) -> ContentEntry:
    return ContentEntry(
        role="record",
        id=f"{record.proposal_id}-approval",
        kind="parameter_change_approval_record",
        media_type="application/json",
        content_hash=model_wire_content_hash(record),
    )


def prepare_parameter_change_proposal_contents(
    *,
    storage: RunRepository,
    run_id: str,
    proposals: Sequence[ParameterChangeProposal],
) -> PreparedParameterChangeProposals:
    """Prepare immutable proposals, reusing durable entries on retries."""

    entries: list[ContentEntry] = []
    writes: list[ModelWrite] = []
    for proposal in proposals:
        candidate_entry = _parameter_change_proposal_record(proposal=proposal)
        if proposal.source_run_id != run_id:
            raise CheckFailed(
                [
                    _parameter_problem(
                        "parameter_change_proposal_source_run_mismatch",
                        "parameter change proposal belongs to a different source run",
                        phase=ProblemPhase.PERSISTENCE,
                        location=model_location(
                            "parameter_change_proposal", "source_run_id"
                        ),
                        details={
                            "proposal_id": proposal.id,
                            "proposal_run_id": proposal.source_run_id,
                            "target_run_id": run_id,
                        },
                    )
                ]
            )
        proposal_ref = record_content_ref(
            record_id=candidate_entry.id,
            kind=candidate_entry.kind,
        )
        if storage.exists(run_id, proposal_ref):
            existing = _load_proposal_record(
                storage=storage,
                run_id=run_id,
                proposal_record=candidate_entry,
            )
            if not _same_parameter_change_proposal(existing, proposal):
                raise Conflict(
                    [
                        _parameter_problem(
                            "parameter_change_proposal_conflict",
                            "parameter change proposal record is immutable and "
                            "already contains different content",
                            phase=ProblemPhase.PERSISTENCE,
                            location=StorageLocation(run_id=run_id, ref=proposal_ref),
                            details={"proposal_id": proposal.id},
                        )
                    ]
                )
            entries.append(_parameter_change_proposal_record(proposal=existing))
            continue
        entries.append(candidate_entry)
        writes.append(
            ModelWrite(
                ref=proposal_ref,
                value=proposal,
                replace=False,
            )
        )
    return PreparedParameterChangeProposals(
        entries=tuple(entries),
        writes=tuple(writes),
    )


def _same_parameter_change_proposal(
    existing: ParameterChangeProposal,
    candidate: ParameterChangeProposal,
) -> bool:
    """Compare one idempotent proposal intent, preserving its first timestamp.

    A stable proposal ID is the idempotency key for repeatable analysis cells.
    ``proposed_at`` records the first successful publication and therefore does
    not make an otherwise identical retry new content. Every scientific and
    provenance-bearing field remains part of the immutable comparison.
    """

    return existing.model_dump(
        mode="python",
        exclude={"proposed_at"},
    ) == candidate.model_dump(
        mode="python",
        exclude={"proposed_at"},
    )


def _resolve_proposal_ref(
    *, storage: RunRepository, run_id: str, selector: str
) -> tuple[ParameterChangeProposal, ContentEntry]:
    try:
        proposal_record = storage.read_content(
            run_id,
            role="record",
            content_id=selector,
        )
    except NotFound:
        raise _proposal_not_found(run_id=run_id, selector=selector) from None
    if proposal_record.kind != "parameter_change_proposal":
        raise _proposal_not_found(run_id=run_id, selector=selector)
    return (
        _load_proposal_record(
            storage=storage,
            run_id=run_id,
            proposal_record=proposal_record,
        ),
        proposal_record,
    )


def _load_proposal_record(
    *, storage: RunRepository, run_id: str, proposal_record: ContentEntry
) -> ParameterChangeProposal:
    proposal_ref = record_content_ref(
        record_id=proposal_record.id,
        kind=proposal_record.kind,
    )
    if not storage.exists(run_id, proposal_ref):
        raise DataIntegrityError(
            [
                _parameter_problem(
                    "parameter_change_proposal_record_missing",
                    (
                        "run content catalog references a missing parameter "
                        "change proposal"
                    ),
                    phase=ProblemPhase.PERSISTENCE,
                    location=StorageLocation(run_id=run_id, ref=proposal_ref),
                    details={"record_id": proposal_record.id},
                )
            ]
        )
    try:
        proposal = storage.read_model(
            run_id,
            proposal_ref,
            ParameterChangeProposal,
        )
    except DataIntegrityError as error:
        raise DataIntegrityError(
            [
                _parameter_problem(
                    "invalid_parameter_change_proposal",
                    "parameter change proposal record is invalid",
                    phase=ProblemPhase.PERSISTENCE,
                    location=StorageLocation(run_id=run_id, ref=proposal_ref),
                    details={"record_id": proposal_record.id},
                )
            ]
        ) from error
    if proposal.id != proposal_record.id or proposal.source_run_id != run_id:
        raise DataIntegrityError(
            [
                _parameter_problem(
                    "invalid_parameter_change_proposal_identity",
                    "parameter change proposal identity does not match its run record",
                    phase=ProblemPhase.PERSISTENCE,
                    location=StorageLocation(run_id=run_id, ref=proposal_ref),
                    details={
                        "record_id": proposal_record.id,
                        "proposal_id": proposal.id,
                        "proposal_run_id": proposal.source_run_id,
                    },
                )
            ]
        )
    return proposal


def _proposal_not_found(*, run_id: str, selector: str) -> NotFound:
    return NotFound(
        [
            _parameter_problem(
                "parameter_change_proposal_not_found",
                "parameter change proposal was not found",
                phase=ProblemPhase.ANALYSIS,
                location=model_location("parameter_change_selector"),
                details={"selector": selector, "run_id": run_id},
            )
        ]
    )


def _parameter_problem(
    code: str,
    message: str,
    *,
    phase: ProblemPhase,
    location: ProblemLocation,
    details: dict[str, object] | None = None,
) -> Problem:
    return problem(
        code,
        message,
        phase=phase,
        location=location,
        details=details,
    )
