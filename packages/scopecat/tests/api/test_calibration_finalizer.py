from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from threading import Event
from typing import cast

import httpx2
import pytest

from scopecat.api.calibration_finalizer import (
    CalibrationPublicationCandidate,
    CalibrationPublicationDeferred,
    CalibrationPublicationPlanningContext,
    CalibrationPublicationPolicy,
    CalibrationPublicationPrepare,
    CalibrationPublicationProcedureView,
    CalibrationPublicationRunView,
    ProjectCalibrationPublicationFinalizer,
)
from scopecat.api.calibration_policy import CalibrationPublicationPolicyRegistry
from scopecat.api.calibration_publication import (
    CalibrationCohortPublicationPlan,
    CalibrationPublicationDriftError,
    CalibrationPublicationOutcomeUnknown,
    calibration_cohort_merge_revision_source,
)
from scopecat.automation.calibration_wire import (
    CalibrationCohortMemberPage,
    CalibrationPublicationAttentionCommand,
    CalibrationPublicationDeferCommand,
    CalibrationPublicationReadyItem,
    CalibrationPublicationReadyPage,
    CalibrationPublicationReadyQuery,
)
from scopecat.automation.calibrations import (
    CalibrationCohort,
    CalibrationCohortFinalization,
    CalibrationCohortMember,
    CalibrationCohortMemberSpec,
    CalibrationCohortSpec,
    CalibrationCohortSummary,
    CalibrationConfigSourceRef,
    CalibrationDefinitionRef,
    CalibrationMissingSuccessDueReason,
    CalibrationPublicationAttention,
    CalibrationPublicationCompletion,
    CalibrationPublicationPolicyRef,
    CalibrationPublicationSupersession,
    CalibrationStatus,
    CalibrationTargetRef,
    CalibrationWorkingPointSupersession,
    calibration_cohort_member_request_key,
    calibration_cohort_spec_hash,
    calibration_freshness_fingerprint,
    calibration_key,
    scoped_calibration_target,
)
from scopecat.automation.models import ProcedureDefinitionRef
from scopecat.config.registry.records import (
    CalibrationCohortMergeContribution,
    ConfigCompositionEvidenceStepRef,
    ConfigCompositionPolicyRef,
    VerifiedParameterProposalProofV1,
)
from scopecat.daemon.client import DaemonConflictError
from scopecat.daemon.wire import CalibrationPublicationReceipt
from scopecat.records.analysis import ProjectAnalysisDecisionReference
from scopecat.records.calibration_scope import WorkingPointCalibrationScope
from scopecat.records.sample import SampleBinding

_SCOPE = WorkingPointCalibrationScope(
    workspace_id="test-working-point",
    sample=SampleBinding(
        role="subject",
        sample_id="chip",
        revision=1,
        content_hash="sha256:" + "a" * 64,
        kind="synthetic",
        display_name="Chip",
        context_id="parked",
    ),
)

_NOW = datetime(2026, 8, 19, 9, tzinfo=UTC)
_HASH_A = f"sha256:{'a' * 64}"
_HASH_B = f"sha256:{'b' * 64}"
_HASH_C = f"sha256:{'c' * 64}"
_HASH_D = f"sha256:{'d' * 64}"
_HASH_E = f"sha256:{'e' * 64}"

_PROCEDURE = ProcedureDefinitionRef(
    id="tests.publication-finalizer.procedure",
    version="1",
    fingerprint=_HASH_A,
)
_CALIBRATION = CalibrationDefinitionRef(
    id="tests.publication-finalizer.calibration",
    version="1",
    fingerprint=_HASH_B,
    success_policy="published_result",
)
_CALIBRATION_V2 = _CALIBRATION.model_copy(update={"version": "2"})
_COMPOSITION = ConfigCompositionPolicyRef(
    id="tests.publication-finalizer.composition",
    version="1",
    fingerprint=_HASH_C,
)
_BASE = CalibrationConfigSourceRef(
    entry_id="base-entry",
    config_ref="config-registry/entries/base-entry/config.json",
    content_hash=_HASH_D,
    scope=_SCOPE,
)

_PLANS: dict[str, CalibrationCohortPublicationPlan] = {}
_prepare_stop_event: Event | None = None


def _prepare_success(
    _context: CalibrationPublicationPlanningContext,
    candidate: CalibrationPublicationCandidate,
) -> CalibrationCohortPublicationPlan:
    return _PLANS[candidate.cohort.cohort_id]


def _prepare_alternative(
    _context: CalibrationPublicationPlanningContext,
    candidate: CalibrationPublicationCandidate,
) -> CalibrationCohortPublicationPlan:
    return _PLANS[candidate.cohort.cohort_id]


def _captured_prepare(marker: str) -> CalibrationPublicationPrepare:
    def prepare(
        _context: CalibrationPublicationPlanningContext,
        _candidate: CalibrationPublicationCandidate,
    ) -> CalibrationCohortPublicationPlan:
        raise AssertionError(f"captured publication {marker} must not run")

    return prepare


def _prepare_failure(
    _context: CalibrationPublicationPlanningContext,
    _candidate: CalibrationPublicationCandidate,
) -> CalibrationCohortPublicationPlan:
    raise ValueError("deterministic proof failed")


def _prepare_deferred(
    _context: CalibrationPublicationPlanningContext,
    _candidate: CalibrationPublicationCandidate,
) -> CalibrationCohortPublicationPlan:
    raise CalibrationPublicationDeferred(
        "analysis replica is not ready",
        retry_after_seconds=30,
    )


def _prepare_and_stop(
    _context: CalibrationPublicationPlanningContext,
    candidate: CalibrationPublicationCandidate,
) -> CalibrationCohortPublicationPlan:
    assert _prepare_stop_event is not None
    _prepare_stop_event.set()
    return _PLANS[candidate.cohort.cohort_id]


def _prepare_failure_and_stop(
    _context: CalibrationPublicationPlanningContext,
    _candidate: CalibrationPublicationCandidate,
) -> CalibrationCohortPublicationPlan:
    assert _prepare_stop_event is not None
    _prepare_stop_event.set()
    raise ValueError("stopped deterministic proof")


def _prepare_deferred_and_stop(
    _context: CalibrationPublicationPlanningContext,
    _candidate: CalibrationPublicationCandidate,
) -> CalibrationCohortPublicationPlan:
    assert _prepare_stop_event is not None
    _prepare_stop_event.set()
    raise CalibrationPublicationDeferred("stopped defer", retry_after_seconds=30)


@dataclass(frozen=True, slots=True)
class _RegisteredPolicy:
    policy: CalibrationPublicationPolicy
    prepared_plan: CalibrationCohortPublicationPlan
    exact_ref: CalibrationPublicationPolicyRef | None = None

    @property
    def id(self) -> str:
        return self.policy.id

    @property
    def version(self) -> str:
        return self.policy.version

    @property
    def fingerprint(self) -> str:
        return self.policy.fingerprint

    @property
    def calibration(self) -> CalibrationDefinitionRef:
        return self.policy.calibration

    @property
    def composition_policy(self) -> ConfigCompositionPolicyRef:
        return self.policy.composition_policy

    @property
    def actor(self) -> str:
        return self.policy.actor

    @property
    def note(self) -> str:
        return self.policy.note

    @property
    def ref(self) -> CalibrationPublicationPolicyRef:
        return self.policy.ref if self.exact_ref is None else self.exact_ref

    def prepare(
        self,
        _context: CalibrationPublicationPlanningContext,
        _candidate: CalibrationPublicationCandidate,
    ) -> CalibrationCohortPublicationPlan:
        return self.prepared_plan


def test_policy_fingerprint_covers_exact_contract_and_registry_history() -> None:
    first = _policy(_prepare_success)
    actor_changed = _policy(_prepare_success, actor="another-actor")
    note_changed = _policy(_prepare_success, note="another note")
    callback_changed = _policy(_prepare_alternative)
    composition_changed = _policy(
        _prepare_success,
        composition=_COMPOSITION.model_copy(update={"version": "2"}),
    )
    calibration_changed = _policy(
        _prepare_success,
        calibration=_CALIBRATION_V2,
    )

    assert (
        len(
            {
                first.fingerprint,
                actor_changed.fingerprint,
                note_changed.fingerprint,
                callback_changed.fingerprint,
                composition_changed.fingerprint,
                calibration_changed.fingerprint,
            }
        )
        == 6
    )

    historical = _policy(
        _prepare_alternative,
        version="2",
        calibration=_CALIBRATION_V2,
    )
    registry = CalibrationPublicationPolicyRegistry((historical, first))
    assert registry.capabilities == (first.ref, historical.ref)
    assert registry.active_bindings == (first.ref, historical.ref)
    assert registry.resolve(first.ref) is first
    assert registry.resolve(historical.ref) is historical
    assert registry.for_calibration(_CALIBRATION) is first
    assert registry.for_calibration(_CALIBRATION_V2) is historical

    with pytest.raises(ValueError, match="registered more than once"):
        CalibrationPublicationPolicyRegistry((first, first))
    separately_named = _policy(
        _prepare_success,
        id="tests.publication-finalizer.another-policy",
    )
    with pytest.raises(ValueError, match="must be selected explicitly"):
        CalibrationPublicationPolicyRegistry((first, separately_named))
    selected = CalibrationPublicationPolicyRegistry(
        (separately_named, first),
        active=(separately_named.ref,),
    )
    assert selected.capabilities == (separately_named.ref, first.ref)
    assert selected.active_bindings == (separately_named.ref,)
    assert selected.for_calibration(_CALIBRATION) is separately_named
    assert selected.resolve(first.ref) is first

    inactive = CalibrationPublicationPolicyRegistry(
        (first, separately_named),
        active=(),
    )
    assert inactive.capabilities == (separately_named.ref, first.ref)
    assert inactive.active_bindings == ()
    assert inactive.for_calibration(_CALIBRATION) is None

    with pytest.raises(ValueError, match="more than one active"):
        CalibrationPublicationPolicyRegistry(
            (first, separately_named),
            active=(first.ref, separately_named.ref),
        )
    next_version = _policy(_prepare_alternative, version="2")
    versioned = CalibrationPublicationPolicyRegistry(
        (next_version, first),
        active=(next_version.ref,),
    )
    assert versioned.capabilities == (first.ref, next_version.ref)
    assert versioned.for_calibration(_CALIBRATION) is next_version
    assert versioned.resolve(first.ref) is first
    retargeted = _policy(
        _prepare_alternative,
        version="3",
        calibration=_CALIBRATION.model_copy(update={"id": "tests.other-calibration"}),
    )
    with pytest.raises(ValueError, match="same logical calibration"):
        CalibrationPublicationPolicyRegistry((first, retargeted))
    with pytest.raises(ValueError, match="exact fingerprint"):
        registry.resolve(first.ref.model_copy(update={"fingerprint": _HASH_E}))
    fixture = _fixture(first, cohort_id="cohort-malformed-policy-ref")
    malformed = _RegisteredPolicy(
        first,
        fixture.plan,
        exact_ref=first.ref.model_copy(update={"fingerprint": _HASH_E}),
    )
    with pytest.raises(ValueError, match="declared exact identity"):
        CalibrationPublicationPolicyRegistry((malformed,))


@pytest.mark.parametrize("marker", ["first", "second"])
def test_policy_rejects_nonlocal_callback_state(marker: str) -> None:
    with pytest.raises(
        TypeError,
        match=(
            "calibration publication prepare callback must not capture "
            "nonlocal values: marker"
        ),
    ):
        _policy(_captured_prepare(marker))


def test_policy_requires_exact_callback_signature() -> None:
    def missing_annotations(context: object, candidate: object) -> None:
        del context, candidate

    with pytest.raises(TypeError, match="context must be"):
        _policy(cast("CalibrationPublicationPrepare", missing_annotations))

    async def asynchronous(
        _context: CalibrationPublicationPlanningContext,
        candidate: CalibrationPublicationCandidate,
    ) -> CalibrationCohortPublicationPlan:
        return _PLANS[candidate.cohort.cohort_id]

    with pytest.raises(TypeError, match="must be synchronous"):
        _policy(cast("CalibrationPublicationPrepare", asynchronous))


def test_publication_planning_views_do_not_expose_mutation_methods() -> None:
    assert not hasattr(CalibrationPublicationPlanningContext, "publish")
    assert not hasattr(CalibrationPublicationPlanningContext, "defer_publication")
    assert not hasattr(CalibrationPublicationProcedureView, "resume")
    assert not hasattr(CalibrationPublicationRunView, "save_analysis")
    assert not hasattr(CalibrationPublicationRunView, "analyze")
    assert not hasattr(CalibrationPublicationRunView, "attach")


def test_candidate_and_policy_reject_cross_response_or_plan_drift() -> None:
    policy = _policy(_prepare_success)
    fixture = _fixture(policy)
    context = cast(
        "CalibrationPublicationPlanningContext",
        cast("object", fixture.context),
    )

    assert policy.prepare(context, fixture.candidate) == fixture.plan

    drifted_item = fixture.item.model_copy(
        update={
            "cohort": fixture.item.cohort.model_copy(
                update={"cohort_id": "another-cohort"}
            )
        }
    )
    with pytest.raises(ValueError, match="summary drifted"):
        CalibrationPublicationCandidate(
            drifted_item,
            fixture.cohort,
            fixture.member_page,
        )

    _PLANS[fixture.cohort.cohort_id] = CalibrationCohortPublicationPlan.create(
        fixture.plan.source,
        actor="wrong-actor",
        note=policy.note,
        expected_finalization_revision=fixture.finalization.revision,
    )
    try:
        with pytest.raises(ValueError, match="exact policy/candidate"):
            policy.prepare(context, fixture.candidate)
    finally:
        _PLANS[fixture.cohort.cohort_id] = fixture.plan


def test_finalizer_validates_plan_from_any_registered_policy_protocol() -> None:
    policy = _policy(_prepare_success)
    fixture = _fixture(policy)
    unsafe = _RegisteredPolicy(
        policy,
        CalibrationCohortPublicationPlan.create(
            fixture.plan.source,
            actor="wrong-actor",
            note=policy.note,
            expected_finalization_revision=fixture.finalization.revision,
        ),
    )
    operations = _operations_for(fixture)

    result = ProjectCalibrationPublicationFinalizer(
        operations,
        CalibrationPublicationPolicyRegistry((unsafe,)),
    ).cycle()

    assert result.failures == 1
    assert result.attention_items == 1
    assert operations.plans == []


def test_finalizer_preserves_finite_traversal_and_publishes_exact_plans() -> None:
    policy = _policy(_prepare_success)
    first = _fixture(policy, cohort_id="cohort-first", sequence=1)
    second = _fixture(policy, cohort_id="cohort-second", sequence=2)
    context = _Context(
        cohorts={
            first.cohort.cohort_id: first.cohort,
            second.cohort.cohort_id: second.cohort,
        },
        member_pages={
            first.cohort.cohort_id: first.member_page,
            second.cohort.cohort_id: second.member_page,
        },
    )
    operations = _Operations(
        context=context,
        pages=[
            CalibrationPublicationReadyPage(
                items=(first.item,),
                next_cursor=1,
                through_sequence=2,
            ),
            CalibrationPublicationReadyPage(items=(second.item,)),
        ],
        finalizations={
            first.cohort.cohort_id: first.finalization,
            second.cohort.cohort_id: second.finalization,
        },
    )
    finalizer = ProjectCalibrationPublicationFinalizer(
        operations,
        CalibrationPublicationPolicyRegistry((policy,)),
        page_limit=1,
    )

    first_result = finalizer.cycle()
    second_result = finalizer.cycle()

    assert first_result.published_items == 1
    assert first_result.has_more is True
    assert second_result.published_items == 1
    assert second_result.has_more is False
    assert tuple(plan.operation_id for plan in operations.plans) == (
        first.plan.operation_id,
        second.plan.operation_id,
    )
    assert operations.queries == [
        CalibrationPublicationReadyQuery(
            capabilities=(policy.ref,),
            limit=1,
        ),
        CalibrationPublicationReadyQuery(
            capabilities=(policy.ref,),
            cursor=1,
            through_sequence=2,
            limit=1,
        ),
    ]


def test_continuation_terminal_page_cannot_exceed_frozen_high_water() -> None:
    policy = _policy(_prepare_success)
    first = _fixture(policy, cohort_id="cohort-high-water-first", sequence=1)
    injected = _fixture(policy, cohort_id="cohort-late-injected", sequence=3)
    operations = _Operations(
        context=_Context(
            cohorts={
                first.cohort.cohort_id: first.cohort,
                injected.cohort.cohort_id: injected.cohort,
            },
            member_pages={
                first.cohort.cohort_id: first.member_page,
                injected.cohort.cohort_id: injected.member_page,
            },
        ),
        pages=[
            CalibrationPublicationReadyPage(
                items=(first.item,),
                next_cursor=1,
                through_sequence=2,
            ),
            CalibrationPublicationReadyPage(items=(injected.item,)),
        ],
        finalizations={
            first.cohort.cohort_id: first.finalization,
            injected.cohort.cohort_id: injected.finalization,
        },
    )
    finalizer = ProjectCalibrationPublicationFinalizer(
        operations,
        CalibrationPublicationPolicyRegistry((policy,)),
        page_limit=1,
    )
    assert finalizer.cycle().has_more is True

    with pytest.raises(CalibrationPublicationDriftError, match="finite high-water"):
        finalizer.cycle()

    assert operations.plans == [first.plan]


def test_finalizer_stop_after_prepare_never_publishes_or_advances() -> None:
    global _prepare_stop_event
    stop = Event()
    _prepare_stop_event = stop
    policy = _policy(_prepare_and_stop)
    fixture = _fixture(policy)
    operations = _operations_for(fixture)
    operations.pages = [
        CalibrationPublicationReadyPage(
            items=(fixture.item,),
            next_cursor=fixture.item.sequence,
            through_sequence=fixture.item.sequence + 1,
        ),
        CalibrationPublicationReadyPage(items=()),
    ]
    finalizer = ProjectCalibrationPublicationFinalizer(
        operations,
        CalibrationPublicationPolicyRegistry((policy,)),
    )

    result = finalizer.cycle(stop)

    assert result.prepared_items == 1
    assert result.published_items == 0
    assert result.has_more is True
    assert operations.plans == []

    stop.clear()
    _prepare_stop_event = None
    finalizer.cycle(Event())
    assert operations.queries[1].cursor is None


@pytest.mark.parametrize(
    "prepare",
    [_prepare_failure_and_stop, _prepare_deferred_and_stop],
)
def test_stop_during_prepare_never_records_a_disposition(
    prepare: CalibrationPublicationPrepare,
) -> None:
    global _prepare_stop_event
    stop = Event()
    _prepare_stop_event = stop
    policy = _policy(prepare)
    fixture = _fixture(policy)
    operations = _operations_for(fixture)

    try:
        result = ProjectCalibrationPublicationFinalizer(
            operations,
            CalibrationPublicationPolicyRegistry((policy,)),
        ).cycle(stop)
    finally:
        _prepare_stop_event = None

    assert result.has_more is True
    assert result.failures == 0
    assert operations.attention_commands == []
    assert operations.defer_commands == []


def test_stop_during_candidate_read_never_records_attention() -> None:
    stop = Event()
    policy = _policy(_prepare_success)
    fixture = _fixture(policy)
    fixture.context.stop_after_members = stop
    fixture.context.member_pages[fixture.cohort.cohort_id] = (
        fixture.member_page.model_copy(update={"cohort_id": "drifted-cohort"})
    )
    operations = _operations_for(fixture)

    result = ProjectCalibrationPublicationFinalizer(
        operations,
        CalibrationPublicationPolicyRegistry((policy,)),
    ).cycle(stop)

    assert result.has_more is True
    assert result.failures == 0
    assert operations.attention_commands == []


@pytest.mark.parametrize(
    ("prepare", "expected_attention", "expected_deferred"),
    [
        (_prepare_failure, 1, 0),
        (_prepare_deferred, 0, 1),
    ],
)
def test_finalizer_durably_disposes_deterministic_and_temporary_failures(
    prepare: CalibrationPublicationPrepare,
    expected_attention: int,
    expected_deferred: int,
) -> None:
    policy = _policy(prepare)
    fixture = _fixture(policy)
    operations = _operations_for(fixture)
    finalizer = ProjectCalibrationPublicationFinalizer(
        operations,
        CalibrationPublicationPolicyRegistry((policy,)),
    )

    result = finalizer.cycle()

    assert result.attention_items == expected_attention
    assert result.deferred_items == expected_deferred
    assert len(operations.attention_commands) == expected_attention
    assert len(operations.defer_commands) == expected_deferred
    if expected_attention:
        assert result.failures == 1
        assert "deterministic proof failed" in operations.attention_commands[0].reason
    else:
        assert result.failures == 0
        assert operations.defer_commands[0].retry_after_seconds == 30


@pytest.mark.parametrize(
    ("prepare", "disposition"),
    [
        (_prepare_failure, "attention"),
        (_prepare_deferred, "defer"),
    ],
)
def test_disposition_response_loss_reconciles_exact_committed_state(
    prepare: CalibrationPublicationPrepare,
    disposition: str,
) -> None:
    policy = _policy(prepare)
    fixture = _fixture(policy)
    operations = _operations_for(fixture)
    response_lost = httpx2.ConnectError(
        "disposition response lost",
        request=httpx2.Request(
            "POST",
            "http://daemon.test/calibration-publications/disposition",
        ),
    )
    if disposition == "attention":
        operations.attention_error = response_lost
        operations.attention_commits_before_error = True
    else:
        operations.defer_error = response_lost
        operations.defer_commits_before_error = True

    result = ProjectCalibrationPublicationFinalizer(
        operations,
        CalibrationPublicationPolicyRegistry((policy,)),
    ).cycle()

    assert result.reconciled_items == 1
    if disposition == "attention":
        assert result.attention_items == 1
    else:
        assert result.deferred_items == 1


def test_defer_response_loss_does_not_claim_another_workers_delay() -> None:
    policy = _policy(_prepare_deferred)
    fixture = _fixture(policy)
    operations = _operations_for(fixture)
    operations.defer_error = httpx2.ConnectError(
        "defer response lost",
        request=httpx2.Request(
            "POST",
            "http://daemon.test/calibration-publications/defer",
        ),
    )
    operations.defer_commits_before_error = True
    operations.defer_commit_retry_after_seconds = 60

    result = ProjectCalibrationPublicationFinalizer(
        operations,
        CalibrationPublicationPolicyRegistry((policy,)),
    ).cycle()

    assert result.deferred_items == 0
    assert result.reconciled_items == 0
    assert result.benign_races == 1


def test_publish_conflict_reconciles_exact_completion_or_supersession() -> None:
    policy = _policy(_prepare_success)
    fixture = _fixture(policy)
    operations = _operations_for(fixture)
    operations.publish_results.append(_conflict())
    operations.finalizations[fixture.cohort.cohort_id] = _published(
        fixture.finalization,
        fixture.plan.operation_id,
    )
    finalizer = ProjectCalibrationPublicationFinalizer(
        operations,
        CalibrationPublicationPolicyRegistry((policy,)),
    )

    result = finalizer.cycle()

    assert result.published_items == 1
    assert result.reconciled_items == 1
    assert result.failures == 0

    superseded_fixture = _fixture(policy, cohort_id="cohort-superseded")
    superseded_operations = _operations_for(superseded_fixture)
    superseded_operations.publish_results.append(_conflict())
    superseded_operations.finalizations[superseded_fixture.cohort.cohort_id] = (
        _superseded(superseded_fixture.finalization)
    )
    superseded_result = ProjectCalibrationPublicationFinalizer(
        superseded_operations,
        CalibrationPublicationPolicyRegistry((policy,)),
    ).cycle()

    assert superseded_result.superseded_items == 1
    assert superseded_result.benign_races == 1


def test_publish_conflict_with_new_ready_revision_is_a_benign_race() -> None:
    policy = _policy(_prepare_success)
    fixture = _fixture(policy)
    operations = _operations_for(fixture)
    operations.publish_results.append(_conflict())
    operations.finalizations[fixture.cohort.cohort_id] = _deferred(
        fixture.finalization,
        30,
    )

    result = ProjectCalibrationPublicationFinalizer(
        operations,
        CalibrationPublicationPolicyRegistry((policy,)),
    ).cycle()

    assert result.benign_races == 1
    assert result.failures == 0
    assert operations.attention_commands == []


def test_unknown_publish_outcome_only_accepts_exact_durable_completion() -> None:
    policy = _policy(_prepare_success)
    fixture = _fixture(policy)
    operations = _operations_for(fixture)
    unknown = CalibrationPublicationOutcomeUnknown(
        fixture.plan,
        cause=RuntimeError("response lost"),
    )
    operations.publish_results.append(unknown)
    finalizer = ProjectCalibrationPublicationFinalizer(
        operations,
        CalibrationPublicationPolicyRegistry((policy,)),
    )

    with pytest.raises(CalibrationPublicationOutcomeUnknown):
        finalizer.cycle()

    operations.pages.append(CalibrationPublicationReadyPage(items=(fixture.item,)))
    operations.publish_results.append(unknown)
    operations.finalizations[fixture.cohort.cohort_id] = _published(
        fixture.finalization,
        "another-operation",
    )
    with pytest.raises(CalibrationPublicationDriftError, match="deterministic plan"):
        finalizer.cycle()


def test_unknown_publish_outcome_survives_failed_reconciliation_lookup() -> None:
    policy = _policy(_prepare_success)
    fixture = _fixture(policy)
    operations = _operations_for(fixture)
    unknown = CalibrationPublicationOutcomeUnknown(
        fixture.plan,
        cause=RuntimeError("publish response lost"),
    )
    lookup_error = httpx2.ReadError("finalization lookup unavailable")
    operations.publish_results.append(unknown)
    operations.finalization_error = lookup_error

    with pytest.raises(CalibrationPublicationOutcomeUnknown) as raised:
        ProjectCalibrationPublicationFinalizer(
            operations,
            CalibrationPublicationPolicyRegistry((policy,)),
        ).cycle()

    assert raised.value is unknown
    assert raised.value.__cause__ is lookup_error


def test_empty_and_pre_stopped_finalizers_do_not_discover_work() -> None:
    operations = _Operations(context=_Context(), pages=[], finalizations={})
    empty = ProjectCalibrationPublicationFinalizer(
        operations,
        CalibrationPublicationPolicyRegistry(),
    )
    assert empty.cycle().has_more is False
    assert operations.queries == []

    policy = _policy(_prepare_success)
    stop = Event()
    stop.set()
    stopped = ProjectCalibrationPublicationFinalizer(
        operations,
        CalibrationPublicationPolicyRegistry((policy,)),
    )
    assert stopped.cycle(stop).has_more is True
    assert operations.queries == []


@dataclass(frozen=True, slots=True)
class _Fixture:
    context: _Context
    cohort: CalibrationCohort
    member_page: CalibrationCohortMemberPage
    finalization: CalibrationCohortFinalization
    item: CalibrationPublicationReadyItem
    candidate: CalibrationPublicationCandidate
    plan: CalibrationCohortPublicationPlan


@dataclass(slots=True)
class _Context:
    cohorts: dict[str, CalibrationCohort] = field(default_factory=dict)
    member_pages: dict[str, CalibrationCohortMemberPage] = field(default_factory=dict)
    stop_after_members: Event | None = None

    def cohort(self, cohort_id: str) -> CalibrationCohort:
        return self.cohorts[cohort_id]

    def cohort_members(self, cohort_id: str) -> CalibrationCohortMemberPage:
        if self.stop_after_members is not None:
            self.stop_after_members.set()
        return self.member_pages[cohort_id]


@dataclass(slots=True)
class _Operations:
    context: _Context
    pages: list[CalibrationPublicationReadyPage]
    finalizations: dict[str, CalibrationCohortFinalization]
    publish_results: list[BaseException | None] = field(default_factory=list)
    attention_error: BaseException | None = None
    attention_commits_before_error: bool = False
    defer_error: BaseException | None = None
    defer_commits_before_error: bool = False
    defer_commit_retry_after_seconds: int | None = None
    finalization_error: BaseException | None = None
    queries: list[CalibrationPublicationReadyQuery] = field(default_factory=list)
    plans: list[CalibrationCohortPublicationPlan] = field(default_factory=list)
    attention_commands: list[CalibrationPublicationAttentionCommand] = field(
        default_factory=list
    )
    defer_commands: list[CalibrationPublicationDeferCommand] = field(
        default_factory=list
    )

    def publication_planning_context(self) -> CalibrationPublicationPlanningContext:
        return cast(
            "CalibrationPublicationPlanningContext",
            cast("object", self.context),
        )

    def ready_publications(
        self,
        query: CalibrationPublicationReadyQuery,
    ) -> CalibrationPublicationReadyPage:
        self.queries.append(query)
        return self.pages.pop(0)

    def publication_finalization(
        self,
        cohort_id: str,
    ) -> CalibrationCohortFinalization:
        if self.finalization_error is not None:
            raise self.finalization_error
        return self.finalizations[cohort_id]

    def publish(
        self, plan: CalibrationCohortPublicationPlan
    ) -> CalibrationPublicationReceipt:
        self.plans.append(plan)
        result = self.publish_results.pop(0) if self.publish_results else None
        if isinstance(result, BaseException):
            raise result
        return cast("CalibrationPublicationReceipt", object())

    def require_publication_attention(
        self,
        command: CalibrationPublicationAttentionCommand,
    ) -> CalibrationCohortFinalization:
        self.attention_commands.append(command)
        current = self.finalizations[command.cohort_id]
        changed = _attention(current, command.actor, command.reason)
        if self.attention_error is not None:
            if self.attention_commits_before_error:
                self.finalizations[command.cohort_id] = changed
            raise self.attention_error
        self.finalizations[command.cohort_id] = changed
        return changed

    def defer_publication(
        self,
        command: CalibrationPublicationDeferCommand,
    ) -> CalibrationCohortFinalization:
        self.defer_commands.append(command)
        current = self.finalizations[command.cohort_id]
        changed = _deferred(
            current,
            self.defer_commit_retry_after_seconds or command.retry_after_seconds,
        )
        if self.defer_error is not None:
            if self.defer_commits_before_error:
                self.finalizations[command.cohort_id] = changed
            raise self.defer_error
        self.finalizations[command.cohort_id] = changed
        return changed


def _policy(
    prepare: CalibrationPublicationPrepare,
    *,
    id: str = "tests.publication-finalizer.policy",
    version: str = "1",
    calibration: CalibrationDefinitionRef = _CALIBRATION,
    composition: ConfigCompositionPolicyRef = _COMPOSITION,
    actor: str = "calibration-finalizer",
    note: str = "publish exact cohort",
) -> CalibrationPublicationPolicy:
    return CalibrationPublicationPolicy(
        id=id,
        version=version,
        calibration=calibration,
        composition_policy=composition,
        actor=actor,
        note=note,
        _prepare=prepare,
    )


def _fixture(
    policy: CalibrationPublicationPolicy,
    *,
    cohort_id: str = "cohort-ready",
    sequence: int = 1,
) -> _Fixture:
    target = scoped_calibration_target(
        CalibrationTargetRef(kind="qubit", id=cohort_id), _BASE
    )
    input_fingerprint = _HASH_E
    member_spec = CalibrationCohortMemberSpec(
        member_id=f"member-{cohort_id}",
        calibration_key=calibration_key(policy.calibration.id, target),
        definition=policy.calibration,
        target=target,
        procedure=_PROCEDURE,
        intent={"target": cohort_id},
        input_fingerprint=input_fingerprint,
        freshness_fingerprint=calibration_freshness_fingerprint(
            definition=policy.calibration,
            target=target,
            procedure=_PROCEDURE,
            input_fingerprint=input_fingerprint,
            dependencies=(),
        ),
        due_reasons=(CalibrationMissingSuccessDueReason(),),
    )
    spec = CalibrationCohortSpec(
        definition=policy.calibration,
        automatic_publication=policy.ref,
        config_source=_BASE,
        fanout_scope="tests.publication-finalizer",
        max_in_flight=1,
        observed_fanout_active_count=0,
        evaluated_at=_NOW,
        observations=(CalibrationStatus(calibration_key=member_spec.calibration_key),),
        members=(member_spec,),
    )
    created_at = _NOW + timedelta(minutes=1)
    cohort = CalibrationCohort(
        cohort_id=cohort_id,
        spec=spec,
        spec_hash=calibration_cohort_spec_hash(spec),
        created_at=created_at,
    )
    member = CalibrationCohortMember(
        cohort_id=cohort_id,
        index=0,
        spec=member_spec,
        procedure_run_id=f"procedure-{cohort_id}",
        request_key=calibration_cohort_member_request_key(cohort_id, member_spec),
        admitted_at=created_at,
    )
    member_page = CalibrationCohortMemberPage(
        cohort_id=cohort_id,
        items=(member,),
    )
    ready_at = created_at + timedelta(minutes=1)
    finalization = CalibrationCohortFinalization(
        cohort_id=cohort_id,
        spec_hash=cohort.spec_hash,
        policy=policy.ref,
        base_config_source=_BASE,
        revision=2,
        state="ready",
        attempt_count=0,
        created_at=created_at,
        updated_at=ready_at,
        ready_at=ready_at,
        available_at=ready_at,
    )
    item = CalibrationPublicationReadyItem(
        sequence=sequence,
        cohort=CalibrationCohortSummary.from_cohort(cohort),
        finalization=finalization,
        enqueued_at=ready_at,
    )
    contribution = CalibrationCohortMergeContribution(
        member_id=member_spec.member_id,
        proof=VerifiedParameterProposalProofV1(
            evidence_step=ConfigCompositionEvidenceStepRef(
                procedure_run_id=member.procedure_run_id,
                step_key="verification",
                attempt=1,
            ),
            decision=ProjectAnalysisDecisionReference(
                analysis_record_id=f"analysis-{cohort_id}",
                output_id="decision",
                schema_id="tests.publication-finalizer.acceptance.v1",
                schema_hash=_HASH_A,
            ),
        ),
        result_input_fingerprint=_HASH_A,
    )
    source = calibration_cohort_merge_revision_source(
        cohort=cohort,
        member_page=member_page,
        composition_policy_ref=policy.composition_policy,
        candidate_id=f"merged-{cohort_id}",
        contributions=(contribution,),
        expected_result_content_hash=_HASH_B,
    )
    plan = CalibrationCohortPublicationPlan.create(
        source,
        actor=policy.actor,
        note=policy.note,
        expected_finalization_revision=finalization.revision,
    )
    _PLANS[cohort_id] = plan
    context = _Context(
        cohorts={cohort_id: cohort},
        member_pages={cohort_id: member_page},
    )
    candidate = CalibrationPublicationCandidate(item, cohort, member_page)
    return _Fixture(
        context=context,
        cohort=cohort,
        member_page=member_page,
        finalization=finalization,
        item=item,
        candidate=candidate,
        plan=plan,
    )


def _operations_for(fixture: _Fixture) -> _Operations:
    return _Operations(
        context=fixture.context,
        pages=[CalibrationPublicationReadyPage(items=(fixture.item,))],
        finalizations={fixture.cohort.cohort_id: fixture.finalization},
    )


def _attention(
    finalization: CalibrationCohortFinalization,
    actor: str,
    reason: str,
) -> CalibrationCohortFinalization:
    updated_at = finalization.updated_at + timedelta(seconds=1)
    return CalibrationCohortFinalization(
        cohort_id=finalization.cohort_id,
        spec_hash=finalization.spec_hash,
        policy=finalization.policy,
        base_config_source=finalization.base_config_source,
        revision=finalization.revision + 1,
        state="attention_required",
        attempt_count=finalization.attempt_count,
        created_at=finalization.created_at,
        updated_at=updated_at,
        ready_at=finalization.ready_at,
        attention=CalibrationPublicationAttention(
            actor=actor,
            reason=reason,
            required_at=updated_at,
        ),
    )


def _deferred(
    finalization: CalibrationCohortFinalization,
    retry_after_seconds: int,
) -> CalibrationCohortFinalization:
    updated_at = finalization.updated_at + timedelta(seconds=1)
    return CalibrationCohortFinalization(
        cohort_id=finalization.cohort_id,
        spec_hash=finalization.spec_hash,
        policy=finalization.policy,
        base_config_source=finalization.base_config_source,
        revision=finalization.revision + 1,
        state="ready",
        attempt_count=finalization.attempt_count + 1,
        created_at=finalization.created_at,
        updated_at=updated_at,
        ready_at=finalization.ready_at,
        available_at=updated_at + timedelta(seconds=retry_after_seconds),
    )


def _published(
    finalization: CalibrationCohortFinalization,
    operation_id: str,
) -> CalibrationCohortFinalization:
    updated_at = finalization.updated_at + timedelta(seconds=1)
    return CalibrationCohortFinalization(
        cohort_id=finalization.cohort_id,
        spec_hash=finalization.spec_hash,
        policy=finalization.policy,
        base_config_source=finalization.base_config_source,
        revision=finalization.revision + 1,
        state="published",
        attempt_count=finalization.attempt_count + 1,
        created_at=finalization.created_at,
        updated_at=updated_at,
        ready_at=finalization.ready_at,
        publication=CalibrationPublicationCompletion(
            operation_id=operation_id,
            published_at=updated_at,
        ),
    )


def _superseded(
    finalization: CalibrationCohortFinalization,
) -> CalibrationCohortFinalization:
    updated_at = finalization.updated_at + timedelta(seconds=1)
    return CalibrationCohortFinalization(
        cohort_id=finalization.cohort_id,
        spec_hash=finalization.spec_hash,
        policy=finalization.policy,
        base_config_source=finalization.base_config_source,
        revision=finalization.revision + 1,
        state="superseded",
        attempt_count=finalization.attempt_count,
        created_at=finalization.created_at,
        updated_at=updated_at,
        ready_at=finalization.ready_at,
        supersession=CalibrationPublicationSupersession(
            evidence=CalibrationWorkingPointSupersession(
                superseded_by=finalization.base_config_source.model_copy(
                    update={"entry_id": "later-entry"}
                ),
            ),
            superseded_at=updated_at,
        ),
    )


def _conflict() -> DaemonConflictError:
    return DaemonConflictError(
        "stale publication base",
        response=httpx2.Response(
            409,
            request=httpx2.Request(
                "POST",
                "http://daemon.test/config/publish",
            ),
        ),
    )
