"""Project-owned policy and bounded automatic calibration publication."""

from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta
from threading import Event
from typing import Literal, Protocol, cast, get_type_hints

import httpx2
from pydantic import ValidationError

from scopecat.api._config import LabConfigOperations
from scopecat.api.calibration_policy import (
    MAX_CALIBRATION_PUBLICATION_POLICY_REGISTRY_SIZE,
    CalibrationPublicationPolicyRegistration,
    CalibrationPublicationPolicyRegistry,
)
from scopecat.api.calibration_publication import (
    CalibrationCohortPublicationPlan,
    CalibrationPublicationDriftError,
    CalibrationPublicationOutcomeUnknown,
    CalibrationPublicationReadSession,
    build_calibration_cohort_merge_contribution,
    calibration_cohort_merge_revision_source,
)
from scopecat.api.procedures import LabProcedureOperations, ProcedureHandle
from scopecat.api.published_analysis import PublishedAnalysis
from scopecat.api.run import RunHandle
from scopecat.automation import (
    ProcedureRun,
    ProcedureStepAttempt,
    ProcedureStepAttemptPage,
    ProcedureStepOutputRef,
)
from scopecat.automation.calibration_wire import (
    MAX_CALIBRATION_PUBLICATION_RETRY_AFTER_SECONDS,
    CalibrationCohortMemberListQuery,
    CalibrationCohortMemberPage,
    CalibrationPublicationAttentionCommand,
    CalibrationPublicationDeferCommand,
    CalibrationPublicationReadyItem,
    CalibrationPublicationReadyPage,
    CalibrationPublicationReadyQuery,
)
from scopecat.automation.calibrations import (
    MAX_CALIBRATION_COHORT_MEMBERS,
    CalibrationCohort,
    CalibrationCohortFinalization,
    CalibrationCohortMember,
    CalibrationCohortSummary,
    CalibrationDefinitionRef,
    CalibrationPublicationPolicyRef,
)
from scopecat.config.registry.records import (
    CalibrationCohortMergeContribution,
    ConfigCompositionPolicyRef,
)
from scopecat.daemon.client import (
    DaemonClient,
    DaemonClientError,
    DaemonConflictError,
)
from scopecat.daemon.views import ConfigEntryView
from scopecat.daemon.wire import (
    CalibrationCohortMergeRevisionSource,
    CalibrationPublicationReceipt,
)
from scopecat.kernel.content_identity import stable_content_hash
from scopecat.kernel.python_source import (
    python_source_identity,
    require_no_python_nonlocals,
)
from scopecat.records.config import ConfigContentHash, ConfigProfileSnapshot
from scopecat.records.content import Sha256ContentHash
from scopecat.records.run import RunSnapshot

_CALIBRATION_PUBLICATION_POLICY_FINGERPRINT_CODEC = (
    "scopecat.calibration-publication-policy.v1"
)
_TRANSIENT_CLIENT_STATUSES = frozenset({408, 425, 429})

type CalibrationPublicationPrepare = Callable[
    [CalibrationPublicationPlanningContext, CalibrationPublicationCandidate],
    CalibrationCohortPublicationPlan,
]
type _DispositionOutcome = Literal["applied", "reconciled", "race"]


@dataclass(frozen=True, slots=True)
class CalibrationPublicationProcedureView:
    """Read-only projection of one procedure used to prove publication."""

    _handle: ProcedureHandle = field(repr=False)

    @property
    def id(self) -> str:
        return self._handle.id

    @property
    def snapshot(self) -> ProcedureRun:
        return self._handle.snapshot

    def steps(
        self,
        *,
        limit: int = 50,
        before: int | None = None,
    ) -> ProcedureStepAttemptPage:
        return self._handle.steps(limit=limit, before=before)

    def step(self, step_key: str) -> ProcedureStepAttempt:
        return self._handle.step(step_key)

    def output(self, step_key: str) -> ProcedureStepOutputRef:
        return self._handle.output(step_key)


@dataclass(frozen=True, slots=True)
class CalibrationPublicationRunView:
    """Read-only projection of one run used to prove publication."""

    _handle: RunHandle = field(repr=False)

    @property
    def id(self) -> str:
        return self._handle.id

    @property
    def snapshot(self) -> RunSnapshot:
        return self._handle.snapshot

    @property
    def config(self) -> ConfigProfileSnapshot:
        return self._handle.config

    def published_analysis(self, selector: str) -> PublishedAnalysis:
        return self._handle.published_analysis(selector)


@dataclass(frozen=True, slots=True, repr=False)
class CalibrationPublicationPlanningContext:
    """Read-only project facade exposed to one publication policy callback."""

    _client: DaemonClient = field(repr=False, compare=False)
    _config: LabConfigOperations = field(repr=False, compare=False)
    _procedures: LabProcedureOperations = field(repr=False, compare=False)
    _session: CalibrationPublicationReadSession = field(repr=False, compare=False)

    def cohort(self, cohort_id: str) -> CalibrationCohort:
        """Read one exact immutable cohort."""

        return self._client.get_calibration_cohort(cohort_id).cohort

    def cohort_members(self, cohort_id: str) -> CalibrationCohortMemberPage:
        """Read the complete bounded member page of one cohort."""

        return self._client.list_calibration_cohort_members(
            CalibrationCohortMemberListQuery(
                cohort_id=cohort_id,
                limit=MAX_CALIBRATION_COHORT_MEMBERS,
            )
        )

    def config_entry(self, entry_id: str) -> ConfigEntryView:
        """Read one exact immutable config registry entry."""

        return self._config.entry(entry_id)

    def procedure(self, procedure_run_id: str) -> CalibrationPublicationProcedureView:
        """Read one exact procedure through a mutation-free projection."""

        return CalibrationPublicationProcedureView(
            self._procedures.get(procedure_run_id)
        )

    def run(self, run_id: str) -> CalibrationPublicationRunView:
        """Read one exact run through a mutation-free projection."""

        return CalibrationPublicationRunView(self._session.get_run(run_id))

    def published_analysis(self, selector: str) -> PublishedAnalysis:
        """Read one exact project analysis publication."""

        return self._session.published_analysis(selector)

    def build_merge_contribution(
        self,
        *,
        cohort: CalibrationCohort,
        member: CalibrationCohortMember,
        procedure: CalibrationPublicationProcedureView,
        evidence_step_key: str,
        decision_output_id: str,
        result_input_fingerprint: Sha256ContentHash,
    ) -> CalibrationCohortMergeContribution:
        """Freeze one member's exact successful proof without publishing."""

        return build_calibration_cohort_merge_contribution(
            cohort=cohort,
            member=member,
            procedure=self._procedures.get(procedure.id),
            evidence_step_key=evidence_step_key,
            decision_output_id=decision_output_id,
            result_input_fingerprint=result_input_fingerprint,
            session=self._session,
        )

    def merge_source(
        self,
        *,
        cohort: CalibrationCohort,
        member_page: CalibrationCohortMemberPage,
        composition_policy_ref: ConfigCompositionPolicyRef,
        candidate_id: str,
        contributions: tuple[CalibrationCohortMergeContribution, ...],
        expected_result_content_hash: ConfigContentHash,
    ) -> CalibrationCohortMergeRevisionSource:
        """Build one exact cohort merge source without publishing it."""

        return calibration_cohort_merge_revision_source(
            cohort=cohort,
            member_page=member_page,
            composition_policy_ref=composition_policy_ref,
            candidate_id=candidate_id,
            contributions=contributions,
            expected_result_content_hash=expected_result_content_hash,
        )

    def publication_plan(
        self,
        source: CalibrationCohortMergeRevisionSource,
        *,
        actor: str,
        note: str = "",
        expected_finalization_revision: int | None = None,
    ) -> CalibrationCohortPublicationPlan:
        """Freeze deterministic publication identities without mutating config."""

        if (
            source.automatic_publication is not None
            and expected_finalization_revision is None
        ):
            finalization = self._client.get_calibration_publication(
                source.cohort_id
            ).finalization
            if (
                finalization.cohort_id != source.cohort_id
                or finalization.spec_hash != source.spec_hash
                or finalization.policy != source.automatic_publication
                or finalization.base_config_source != source.base
            ):
                raise ValueError(
                    "automatic publication finalization does not match merge source"
                )
            expected_finalization_revision = finalization.revision
        return CalibrationCohortPublicationPlan.create(
            source,
            actor=actor,
            note=note,
            expected_finalization_revision=expected_finalization_revision,
        )


@dataclass(frozen=True, slots=True)
class CalibrationPublicationCandidate:
    """Exact ready occurrence and complete immutable cohort evidence."""

    item: CalibrationPublicationReadyItem
    cohort: CalibrationCohort
    member_page: CalibrationCohortMemberPage

    def __post_init__(self) -> None:
        summary = CalibrationCohortSummary.from_cohort(self.cohort)
        if self.item.cohort != summary:
            raise ValueError(
                "calibration publication candidate summary drifted from its cohort"
            )
        finalization = self.item.finalization
        policy = self.cohort.spec.automatic_publication
        if (
            finalization.cohort_id != self.cohort.cohort_id
            or finalization.spec_hash != self.cohort.spec_hash
            or finalization.base_config_source != self.cohort.spec.config_source
            or policy is None
            or finalization.policy != policy
        ):
            raise ValueError(
                "calibration publication candidate finalization drifted from its cohort"
            )
        _complete_cohort_members(self.cohort, self.member_page)

    @property
    def finalization(self) -> CalibrationCohortFinalization:
        return self.item.finalization

    @property
    def members(self) -> tuple[CalibrationCohortMember, ...]:
        return self.member_page.items


class RegisteredCalibrationPublicationPolicy(
    CalibrationPublicationPolicyRegistration,
    Protocol,
):
    """Exact registered identity plus its read-only deterministic planner."""

    def prepare(
        self,
        context: CalibrationPublicationPlanningContext,
        candidate: CalibrationPublicationCandidate,
    ) -> CalibrationCohortPublicationPlan: ...


@dataclass(frozen=True, slots=True, repr=False)
class CalibrationPublicationPolicy:
    """One frozen callback that prepares an exact cohort publication plan."""

    id: str
    version: str
    calibration: CalibrationDefinitionRef
    composition_policy: ConfigCompositionPolicyRef
    actor: str
    note: str
    _prepare: CalibrationPublicationPrepare = field(repr=False, compare=False)
    fingerprint: Sha256ContentHash = field(init=False)

    def __post_init__(self) -> None:
        _require_non_blank(self.id, field_name="calibration publication policy id")
        _require_non_blank(
            self.version,
            field_name="calibration publication policy version",
        )
        _require_non_blank(self.actor, field_name="calibration publication actor")
        if self.calibration.success_policy != "published_result":
            raise ValueError(
                "automatic publication policy requires published-result calibration"
            )
        _validate_prepare(self._prepare)
        object.__setattr__(
            self,
            "fingerprint",
            _publication_policy_fingerprint(
                id=self.id,
                version=self.version,
                calibration=self.calibration,
                composition_policy=self.composition_policy,
                actor=self.actor,
                note=self.note,
                prepare=self._prepare,
            ),
        )

    @property
    def ref(self) -> CalibrationPublicationPolicyRef:
        return CalibrationPublicationPolicyRef(
            id=self.id,
            version=self.version,
            fingerprint=self.fingerprint,
            calibration=self.calibration,
            composition_policy=self.composition_policy,
        )

    @property
    def __wrapped__(self) -> CalibrationPublicationPrepare:
        return self._prepare

    @property
    def __name__(self) -> str:
        return self._prepare.__name__

    @property
    def __signature__(self) -> inspect.Signature:
        return inspect.signature(self._prepare)

    def prepare(
        self,
        context: CalibrationPublicationPlanningContext,
        candidate: CalibrationPublicationCandidate,
    ) -> CalibrationCohortPublicationPlan:
        if candidate.finalization.policy != self.ref:
            raise ValueError(
                "calibration publication candidate does not match its exact policy"
            )
        plan = self._prepare(context, candidate)
        _validate_policy_plan(self, candidate, plan)
        return plan


def calibration_publication_policy(
    *,
    id: str,
    version: str,
    calibration: CalibrationDefinitionRef,
    composition_policy: ConfigCompositionPolicyRef,
    actor: str,
    note: str = "",
) -> Callable[[CalibrationPublicationPrepare], CalibrationPublicationPolicy]:
    """Decorate one read-only exact publication planning callback."""

    def decorate(
        prepare: CalibrationPublicationPrepare,
    ) -> CalibrationPublicationPolicy:
        return CalibrationPublicationPolicy(
            id=id,
            version=version,
            calibration=calibration,
            composition_policy=composition_policy,
            actor=actor,
            note=note,
            _prepare=prepare,
        )

    return decorate


class CalibrationPublicationDeferred(RuntimeError):
    """A policy-detected temporary block with a durable retry delay."""

    def __init__(self, reason: str, *, retry_after_seconds: int) -> None:
        selected = _require_non_blank(
            reason,
            field_name="calibration publication defer reason",
        )
        if (
            not 1
            <= retry_after_seconds
            <= (MAX_CALIBRATION_PUBLICATION_RETRY_AFTER_SECONDS)
        ):
            raise ValueError(
                "calibration publication retry delay must be between 1 and "
                f"{MAX_CALIBRATION_PUBLICATION_RETRY_AFTER_SECONDS} seconds"
            )
        super().__init__(selected)
        self.reason = selected
        self.retry_after_seconds = retry_after_seconds


class CalibrationPublicationFinalizerOperations(Protocol):
    """Narrow durable mutation surface used by the resident finalizer."""

    def publication_planning_context(
        self,
    ) -> CalibrationPublicationPlanningContext: ...

    def ready_publications(
        self,
        query: CalibrationPublicationReadyQuery,
    ) -> CalibrationPublicationReadyPage: ...

    def publication_finalization(
        self,
        cohort_id: str,
    ) -> CalibrationCohortFinalization: ...

    def publish(
        self,
        plan: CalibrationCohortPublicationPlan,
    ) -> CalibrationPublicationReceipt: ...

    def require_publication_attention(
        self,
        command: CalibrationPublicationAttentionCommand,
    ) -> CalibrationCohortFinalization: ...

    def defer_publication(
        self,
        command: CalibrationPublicationDeferCommand,
    ) -> CalibrationCohortFinalization: ...


@dataclass(frozen=True, slots=True)
class CalibrationPublicationFinalizerCycleResult:
    """Bounded automatic-publication work completed in one resident cycle."""

    ready_items: int
    prepared_items: int
    published_items: int
    deferred_items: int
    attention_items: int
    reconciled_items: int
    superseded_items: int
    benign_races: int
    failures: int
    has_more: bool


class ProjectCalibrationPublicationFinalizer:
    """Discover, prepare, and publish one bounded finite ready-work page."""

    __slots__ = (
        "_actor",
        "_cursor",
        "_operations",
        "_page_limit",
        "_policies",
        "_through_sequence",
    )

    def __init__(
        self,
        operations: CalibrationPublicationFinalizerOperations,
        policies: CalibrationPublicationPolicyRegistry,
        *,
        page_limit: int = 50,
        actor: str = "calibration-publication-finalizer",
    ) -> None:
        if not 1 <= page_limit <= MAX_CALIBRATION_PUBLICATION_POLICY_REGISTRY_SIZE:
            raise ValueError("calibration publication page limit must be 1..200")
        self._actor = _require_non_blank(
            actor,
            field_name="calibration publication finalizer actor",
        )
        self._operations = operations
        self._policies = policies
        self._page_limit = page_limit
        self._cursor: int | None = None
        self._through_sequence: int | None = None

    def cycle(
        self,
        stop: Event | None = None,
    ) -> CalibrationPublicationFinalizerCycleResult:
        """Process one page without advancing traversal past unfinished work."""

        if _stopped(stop):
            return _empty_cycle(has_more=bool(self._policies))
        if not self._policies:
            return _empty_cycle()

        page = self._operations.ready_publications(
            CalibrationPublicationReadyQuery(
                capabilities=self._policies.capabilities,
                cursor=self._cursor,
                through_sequence=self._through_sequence,
                limit=self._page_limit,
            )
        )
        self._validate_page(page)
        totals = _MutableCycle()
        context = self._operations.publication_planning_context()
        for item in page.items:
            if _stopped(stop):
                totals.has_more = True
                break
            totals.ready_items += 1
            if not self._process_item(context, item, totals, stop):
                totals.has_more = True
                break
        else:
            self._cursor = page.next_cursor
            self._through_sequence = page.through_sequence
            totals.has_more = page.next_cursor is not None
        return totals.freeze()

    def _process_item(  # noqa: C901 - explicit prepare/publish error boundaries
        self,
        context: CalibrationPublicationPlanningContext,
        item: CalibrationPublicationReadyItem,
        totals: _MutableCycle,
        stop: Event | None,
    ) -> bool:
        try:
            policy = cast(
                "RegisteredCalibrationPublicationPolicy",
                self._policies.resolve(item.finalization.policy),
            )
            cohort = context.cohort(item.cohort.cohort_id)
            member_page = context.cohort_members(item.cohort.cohort_id)
            candidate = CalibrationPublicationCandidate(
                item=item,
                cohort=cohort,
                member_page=member_page,
            )
        except Exception as error:
            if _stopped(stop):
                return False
            if _is_transient_control_error(error):
                raise
            totals.failures += 1
            self._record_attention(item.finalization, error, totals)
            return True

        if _stopped(stop):
            return False
        try:
            plan = policy.prepare(context, candidate)
            _validate_policy_plan(policy, candidate, plan)
        except CalibrationPublicationDeferred as deferred:
            if _stopped(stop):
                return False
            outcome = self._record_defer(candidate.finalization, deferred)
            if outcome == "applied":
                totals.deferred_items += 1
            elif outcome == "reconciled":
                totals.deferred_items += 1
                totals.reconciled_items += 1
            else:
                totals.benign_races += 1
            return True
        except Exception as error:
            if _stopped(stop):
                return False
            if _is_transient_control_error(error):
                raise
            totals.failures += 1
            self._record_attention(candidate.finalization, error, totals)
            return True

        totals.prepared_items += 1
        if _stopped(stop):
            return False
        try:
            self._operations.publish(plan)
        except CalibrationPublicationOutcomeUnknown as error:
            self._reconcile_publication(
                candidate.finalization,
                plan,
                totals,
                unknown=error,
            )
        except DaemonConflictError as error:
            self._reconcile_publication(
                candidate.finalization,
                plan,
                totals,
                conflict=error,
            )
        except httpx2.HTTPStatusError as error:
            if error.response.status_code == 409:
                self._reconcile_publication(
                    candidate.finalization,
                    plan,
                    totals,
                    conflict=error,
                )
            elif _is_transient_control_error(error):
                raise
            else:
                totals.failures += 1
                self._record_attention(candidate.finalization, error, totals)
        except Exception as error:
            if _is_transient_control_error(error):
                raise
            totals.failures += 1
            self._record_attention(candidate.finalization, error, totals)
        else:
            totals.published_items += 1
        return True

    def _reconcile_publication(
        self,
        expected: CalibrationCohortFinalization,
        plan: CalibrationCohortPublicationPlan,
        totals: _MutableCycle,
        *,
        conflict: BaseException | None = None,
        unknown: CalibrationPublicationOutcomeUnknown | None = None,
    ) -> None:
        try:
            current = self._operations.publication_finalization(expected.cohort_id)
        except Exception as lookup_error:
            if unknown is not None:
                raise unknown from lookup_error
            raise
        _validate_finalization_identity(current, expected)
        if current.state == "published":
            publication = current.publication
            if publication is None or publication.operation_id != plan.operation_id:
                raise CalibrationPublicationDriftError(
                    "published finalization does not match the deterministic plan"
                )
            totals.published_items += 1
            totals.reconciled_items += 1
            return
        if current.state == "superseded":
            totals.superseded_items += 1
            totals.benign_races += 1
            return
        if unknown is not None:
            raise unknown
        if current.state in {"attention_required", "failed"}:
            totals.benign_races += 1
            return
        if current.state == "ready":
            if current.revision != expected.revision:
                totals.benign_races += 1
                return
            totals.failures += 1
            self._record_attention(
                current,
                conflict or RuntimeError("publish conflict"),
                totals,
            )
            return
        raise CalibrationPublicationDriftError(
            "publication conflict reconciled to an impossible finalization state"
        )

    def _record_attention(
        self,
        finalization: CalibrationCohortFinalization,
        error: BaseException,
        totals: _MutableCycle,
    ) -> None:
        outcome = self._require_attention(finalization, _error_reason(error))
        if outcome == "applied":
            totals.attention_items += 1
        elif outcome == "reconciled":
            totals.attention_items += 1
            totals.reconciled_items += 1
        else:
            totals.benign_races += 1

    def _require_attention(
        self,
        expected: CalibrationCohortFinalization,
        reason: str,
    ) -> _DispositionOutcome:
        command = CalibrationPublicationAttentionCommand(
            cohort_id=expected.cohort_id,
            policy=expected.policy,
            expected_finalization_revision=expected.revision,
            actor=self._actor,
            reason=reason,
        )
        try:
            current = self._operations.require_publication_attention(command)
        except Exception as error:
            if not _is_uncertain_disposition_error(error):
                raise
            return self._reconcile_attention(command, expected, cause=error)
        _validate_finalization_identity(current, expected)
        attention = current.attention
        if (
            current.state != "attention_required"
            or current.revision != expected.revision + 1
            or attention is None
            or attention.actor != command.actor
            or attention.reason != command.reason
        ):
            raise CalibrationPublicationDriftError(
                "publication attention receipt did not match its exact command"
            )
        return "applied"

    def _reconcile_attention(
        self,
        command: CalibrationPublicationAttentionCommand,
        expected: CalibrationCohortFinalization,
        *,
        cause: BaseException,
    ) -> _DispositionOutcome:
        current = self._operations.publication_finalization(expected.cohort_id)
        _validate_finalization_identity(current, expected)
        if current.state == "attention_required":
            attention = current.attention
            if (
                current.revision == command.expected_finalization_revision + 1
                and attention is not None
                and attention.actor == command.actor
                and attention.reason == command.reason
            ):
                return "reconciled"
            return "race"
        if current.state in {"published", "superseded", "failed"}:
            return "race"
        if current.state == "ready" and current.revision != expected.revision:
            return "race"
        raise cause

    def _record_defer(
        self,
        expected: CalibrationCohortFinalization,
        deferred: CalibrationPublicationDeferred,
    ) -> _DispositionOutcome:
        command = CalibrationPublicationDeferCommand(
            cohort_id=expected.cohort_id,
            policy=expected.policy,
            expected_finalization_revision=expected.revision,
            retry_after_seconds=deferred.retry_after_seconds,
            reason=deferred.reason,
        )
        try:
            current = self._operations.defer_publication(command)
        except Exception as error:
            if not _is_uncertain_disposition_error(error):
                raise
            return self._reconcile_defer(command, expected, cause=error)
        _validate_finalization_identity(current, expected)
        if not _matches_defer(current, expected, command):
            raise CalibrationPublicationDriftError(
                "publication defer receipt did not match its exact command"
            )
        return "applied"

    def _reconcile_defer(
        self,
        command: CalibrationPublicationDeferCommand,
        expected: CalibrationCohortFinalization,
        *,
        cause: BaseException,
    ) -> _DispositionOutcome:
        current = self._operations.publication_finalization(expected.cohort_id)
        _validate_finalization_identity(current, expected)
        if current.state == "ready":
            if _matches_defer(current, expected, command):
                return "reconciled"
            if current.revision != expected.revision:
                return "race"
            raise cause
        if current.state in {
            "attention_required",
            "published",
            "superseded",
            "failed",
        }:
            return "race"
        raise cause

    def _validate_page(self, page: CalibrationPublicationReadyPage) -> None:
        if self._cursor is not None:
            through_sequence = self._through_sequence
            if through_sequence is None:
                raise CalibrationPublicationDriftError(
                    "publication continuation is missing its finite high-water"
                )
            if any(item.sequence <= self._cursor for item in page.items):
                raise CalibrationPublicationDriftError(
                    "publication ready traversal did not advance beyond its cursor"
                )
            if any(item.sequence > through_sequence for item in page.items):
                raise CalibrationPublicationDriftError(
                    "publication ready traversal exceeded its finite high-water"
                )
            if (
                page.through_sequence is not None
                and page.through_sequence != through_sequence
            ):
                raise CalibrationPublicationDriftError(
                    "publication ready traversal changed its finite high-water"
                )


@dataclass(slots=True)
class _MutableCycle:
    ready_items: int = 0
    prepared_items: int = 0
    published_items: int = 0
    deferred_items: int = 0
    attention_items: int = 0
    reconciled_items: int = 0
    superseded_items: int = 0
    benign_races: int = 0
    failures: int = 0
    has_more: bool = False

    def freeze(self) -> CalibrationPublicationFinalizerCycleResult:
        return CalibrationPublicationFinalizerCycleResult(
            ready_items=self.ready_items,
            prepared_items=self.prepared_items,
            published_items=self.published_items,
            deferred_items=self.deferred_items,
            attention_items=self.attention_items,
            reconciled_items=self.reconciled_items,
            superseded_items=self.superseded_items,
            benign_races=self.benign_races,
            failures=self.failures,
            has_more=self.has_more,
        )


def _validate_policy_plan(
    policy: RegisteredCalibrationPublicationPolicy,
    candidate: CalibrationPublicationCandidate,
    plan: CalibrationCohortPublicationPlan,
) -> None:
    cohort = candidate.cohort
    base = cohort.spec.config_source
    source = plan.source
    members = {member.spec.member_id: member for member in candidate.member_page.items}
    contributions = {
        contribution.member_id: contribution for contribution in source.contributions
    }
    if (
        plan.actor != policy.actor
        or plan.note != policy.note
        or plan.expected_finalization_revision != candidate.finalization.revision
        or source.cohort_id != cohort.cohort_id
        or source.spec_hash != cohort.spec_hash
        or source.automatic_publication != policy.ref
        or source.composition_policy_ref != policy.composition_policy
        or source.base != base
        or contributions.keys() != members.keys()
        or any(
            contributions[member_id].proof.evidence_step.procedure_run_id
            != member.procedure_run_id
            for member_id, member in members.items()
        )
    ):
        raise ValueError(
            "calibration publication plan does not match its exact policy/candidate"
        )


def _matches_defer(
    current: CalibrationCohortFinalization,
    expected: CalibrationCohortFinalization,
    command: CalibrationPublicationDeferCommand,
) -> bool:
    return (
        current.state == "ready"
        and current.revision == command.expected_finalization_revision + 1
        and current.attempt_count == expected.attempt_count + 1
        and current.created_at == expected.created_at
        and current.ready_at == expected.ready_at
        and current.available_at is not None
        and current.available_at
        == current.updated_at + timedelta(seconds=command.retry_after_seconds)
    )


def _validate_prepare(prepare: Callable[..., object]) -> None:
    if not inspect.isfunction(prepare):
        raise TypeError("calibration publication prepare callback must be a function")
    if inspect.iscoroutinefunction(prepare):
        raise TypeError("calibration publication prepare callback must be synchronous")
    require_no_python_nonlocals(
        prepare,
        label="calibration publication prepare callback",
    )
    parameters = tuple(inspect.signature(prepare).parameters.values())
    if len(parameters) != 2 or any(
        parameter.kind
        not in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        )
        or cast("object", parameter.default) is not inspect.Parameter.empty
        for parameter in parameters
    ):
        raise TypeError(
            "calibration publication prepare callback requires exactly "
            "(context, candidate)"
        )
    try:
        hints = get_type_hints(prepare)
    except (NameError, TypeError) as error:
        raise TypeError(
            "calibration publication prepare annotations must be resolvable"
        ) from error
    context, candidate = parameters
    if hints.get(context.name) is not CalibrationPublicationPlanningContext:
        raise TypeError(
            "calibration publication context must be "
            "CalibrationPublicationPlanningContext"
        )
    if hints.get(candidate.name) is not CalibrationPublicationCandidate:
        raise TypeError(
            "calibration publication candidate must be CalibrationPublicationCandidate"
        )
    if hints.get("return") is not CalibrationCohortPublicationPlan:
        raise TypeError(
            "calibration publication prepare return must be "
            "CalibrationCohortPublicationPlan"
        )


def _publication_policy_fingerprint(
    *,
    id: str,
    version: str,
    calibration: CalibrationDefinitionRef,
    composition_policy: ConfigCompositionPolicyRef,
    actor: str,
    note: str,
    prepare: CalibrationPublicationPrepare,
) -> Sha256ContentHash:
    identity = {
        "codec": _CALIBRATION_PUBLICATION_POLICY_FINGERPRINT_CODEC,
        "id": id,
        "version": version,
        "calibration": calibration.model_dump(mode="json"),
        "composition_policy": composition_policy.model_dump(mode="json"),
        "actor": actor,
        "note": note,
        "prepare": {
            **python_source_identity(
                prepare,
                label="calibration publication prepare",
            ),
            "signature": str(inspect.signature(prepare)),
        },
    }
    return f"sha256:{stable_content_hash(identity)}"


def _complete_cohort_members(
    cohort: CalibrationCohort,
    page: CalibrationCohortMemberPage,
) -> tuple[CalibrationCohortMember, ...]:
    if page.cohort_id != cohort.cohort_id or page.next_cursor is not None:
        raise ValueError(
            "calibration publication candidate requires one complete member page"
        )
    if len(page.items) != len(cohort.spec.members):
        raise ValueError(
            "calibration publication candidate members must cover the whole cohort"
        )
    for index, (member, spec) in enumerate(
        zip(page.items, cohort.spec.members, strict=True)
    ):
        if (
            member.cohort_id != cohort.cohort_id
            or member.index != index
            or member.spec != spec
            or member.admitted_at != cohort.created_at
        ):
            raise ValueError(
                "calibration publication candidate member drifted from admission"
            )
    return page.items


def _validate_finalization_identity(
    current: CalibrationCohortFinalization,
    expected: CalibrationCohortFinalization,
) -> None:
    if (
        current.cohort_id != expected.cohort_id
        or current.spec_hash != expected.spec_hash
        or current.policy != expected.policy
        or current.base_config_source != expected.base_config_source
    ):
        raise CalibrationPublicationDriftError(
            "reconciled publication finalization changed immutable identity"
        )


def _is_uncertain_disposition_error(error: BaseException) -> bool:
    return isinstance(error, ValidationError | DaemonConflictError) or (
        isinstance(error, Exception) and _is_transient_control_error(error)
    )


def _is_transient_control_error(error: BaseException) -> bool:
    if isinstance(error, httpx2.TransportError):
        return True
    if isinstance(error, (DaemonClientError, httpx2.HTTPStatusError)):
        status = error.response.status_code
        return status == 503 or status >= 500 or status in _TRANSIENT_CLIENT_STATUSES
    return False


def _error_reason(error: BaseException) -> str:
    detail = str(error).strip()
    if detail:
        return f"{type(error).__name__}: {detail}"
    return type(error).__name__


def _require_non_blank(value: str, *, field_name: str) -> str:
    if not value.strip():
        raise ValueError(f"{field_name} must be non-empty")
    return value


def _stopped(stop: Event | None) -> bool:
    return stop is not None and stop.is_set()


def _empty_cycle(
    *,
    has_more: bool = False,
) -> CalibrationPublicationFinalizerCycleResult:
    return CalibrationPublicationFinalizerCycleResult(
        ready_items=0,
        prepared_items=0,
        published_items=0,
        deferred_items=0,
        attention_items=0,
        reconciled_items=0,
        superseded_items=0,
        benign_races=0,
        failures=0,
        has_more=has_more,
    )


__all__ = [
    "CalibrationPublicationCandidate",
    "CalibrationPublicationDeferred",
    "CalibrationPublicationFinalizerCycleResult",
    "CalibrationPublicationFinalizerOperations",
    "CalibrationPublicationPlanningContext",
    "CalibrationPublicationPolicy",
    "CalibrationPublicationPrepare",
    "CalibrationPublicationProcedureView",
    "CalibrationPublicationRunView",
    "ProjectCalibrationPublicationFinalizer",
    "RegisteredCalibrationPublicationPolicy",
    "calibration_publication_policy",
]
