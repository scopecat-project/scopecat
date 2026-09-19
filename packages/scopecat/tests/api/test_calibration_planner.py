from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from threading import Event
from typing import Literal, cast, override

import httpx2
import pytest
from pydantic import BaseModel, ConfigDict

from scopecat.api.calibration_finalizer import (
    CalibrationPublicationCandidate,
    CalibrationPublicationPlanningContext,
    CalibrationPublicationPolicy,
)
from scopecat.api.calibration_planner import (
    CalibrationPlanningContext,
    ProjectCalibrationEvaluator,
    calibration_cohort_id,
)
from scopecat.api.calibration_policy import CalibrationPublicationPolicyRegistry
from scopecat.api.calibration_publication import CalibrationCohortPublicationPlan
from scopecat.automation import (
    CalibrationAttemptRef,
    CalibrationAttemptStatus,
    CalibrationCohort,
    CalibrationCohortCreateReceipt,
    CalibrationCohortMember,
    CalibrationCohortSpec,
    CalibrationConfigSourceRef,
    CalibrationDependencyChangedDueReason,
    CalibrationDependencyEvidence,
    CalibrationInputsChangedDueReason,
    CalibrationPublicationBaseChangedDueReason,
    CalibrationStatus,
    CalibrationStatusSnapshot,
    CalibrationSuccessPolicy,
    CalibrationSuccessPublication,
    CalibrationSuccessRef,
    CalibrationTargetRef,
    ProcedureCloseStatus,
    ProcedureClosure,
    ProcedureRunState,
    calibration_cohort_member_request_key,
    calibration_cohort_spec_hash,
    calibration_freshness_fingerprint,
    calibration_key,
    procedure,
)
from scopecat.automation.calibration_definition import (
    CalibrationDefinition,
    CalibrationDependencyRequirement,
    CalibrationObservation,
    CalibrationRegistry,
)
from scopecat.config.registry.records import ConfigCompositionPolicyRef
from scopecat.daemon.client import DaemonConflictError, DaemonNotFoundError
from scopecat.records.calibration_scope import WorkingPointCalibrationScope
from scopecat.records.config import ConfigProfileSnapshot
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

_NOW = datetime(2026, 8, 18, 10, tzinfo=UTC)
_EVENTS: list[str] = []
_targets: tuple[CalibrationTargetRef, ...] = ()
_values: dict[str, int] = {}
_dependencies: dict[str, tuple[CalibrationDependencyRequirement, ...]] = {}
_valid_for: dict[str, timedelta] = {}


class _Inputs(BaseModel):
    model_config = ConfigDict(frozen=True)

    value: int


class _Intent(BaseModel):
    model_config = ConfigDict(frozen=True)

    target_id: str
    value: int
    dependency_runs: tuple[str, ...]


@procedure(id="tests.calibration.procedure", version="1", intent=_Intent)
def _procedure(_context: object, _intent: _Intent) -> None:
    pass


def _select(_context: CalibrationPlanningContext) -> tuple[CalibrationTargetRef, ...]:
    return _targets


def _observe(
    _context: CalibrationPlanningContext,
    target: CalibrationTargetRef,
) -> CalibrationObservation[_Inputs]:
    return CalibrationObservation(
        inputs=_Inputs(value=_values[target.id]),
        dependencies=_dependencies.get(target.id, ()),
        valid_for=_valid_for.get(target.id),
    )


def _build(
    _context: CalibrationPlanningContext,
    target: CalibrationTargetRef,
    inputs: _Inputs,
    dependencies: tuple[CalibrationDependencyEvidence, ...],
) -> _Intent:
    _EVENTS.append(f"build:{target.id}")
    return _Intent(
        target_id=target.id,
        value=inputs.value,
        dependency_runs=tuple(item.procedure_run_id for item in dependencies),
    )


def _unused_publication_prepare(
    _context: CalibrationPublicationPlanningContext,
    _candidate: CalibrationPublicationCandidate,
) -> CalibrationCohortPublicationPlan:
    raise AssertionError("evaluator must only pin the policy ref")


def _definition(
    id: str = "tests.calibration",
    *,
    max_in_flight: int = 10,
    success_policy: CalibrationSuccessPolicy = "procedure_success",
) -> CalibrationDefinition[CalibrationPlanningContext, _Inputs, _Intent]:
    return CalibrationDefinition(
        id=id,
        version="1",
        input_type=_Inputs,
        procedure=_procedure,
        fanout_scope="tests.device",
        max_in_flight=max_in_flight,
        _select_targets=_select,
        _observe=_observe,
        _build_intent=_build,
        success_policy=success_policy,
    )


@dataclass(slots=True)
class _Operations:
    statuses: dict[str, CalibrationStatus] = field(default_factory=dict)
    active_count: int = 0
    events: list[str] = field(default_factory=lambda: _EVENTS)
    cohorts: dict[str, CalibrationCohortCreateReceipt] = field(default_factory=dict)

    def status(
        self,
        calibration_keys: tuple[str, ...],
        *,
        fanout_scope: str,
    ) -> CalibrationStatusSnapshot:
        self.events.append("status")
        return CalibrationStatusSnapshot(
            observed_at=_NOW,
            fanout_scope=fanout_scope,
            fanout_active_count=self.active_count,
            statuses=tuple(
                self.statuses.get(key, CalibrationStatus(calibration_key=key))
                for key in calibration_keys
            ),
        )

    def create(
        self,
        cohort_id: str,
        spec: CalibrationCohortSpec,
    ) -> CalibrationCohortCreateReceipt:
        self.events.append("create")
        cohort = CalibrationCohort(
            cohort_id=cohort_id,
            spec=spec,
            spec_hash=calibration_cohort_spec_hash(spec),
            created_at=spec.evaluated_at,
        )
        members = tuple(
            CalibrationCohortMember(
                cohort_id=cohort_id,
                index=index,
                spec=member,
                procedure_run_id=f"run-{member.target.id}",
                request_key=calibration_cohort_member_request_key(
                    cohort_id,
                    member,
                ),
                admitted_at=spec.evaluated_at,
            )
            for index, member in enumerate(spec.members)
        )
        receipt = CalibrationCohortCreateReceipt(cohort=cohort, members=members)
        self.cohorts[cohort_id] = receipt
        return receipt

    def get(self, cohort_id: str) -> CalibrationCohort:
        return self.cohorts[cohort_id].cohort


class _UnknownCreateOperations(_Operations):
    outcome: Literal["match", "missing", "drift"]
    error: httpx2.TransportError
    attempted: tuple[str, CalibrationCohortSpec] | None

    def __init__(self, outcome: Literal["match", "missing", "drift"]) -> None:
        super().__init__()
        self.outcome = outcome
        self.error = httpx2.ConnectError(
            "calibration cohort create outcome is unknown",
            request=httpx2.Request(
                "POST",
                "http://daemon.test/calibration-cohorts",
            ),
        )
        self.attempted = None

    @override
    def create(
        self,
        cohort_id: str,
        spec: CalibrationCohortSpec,
    ) -> CalibrationCohortCreateReceipt:
        self.events.append("create")
        self.attempted = (cohort_id, spec)
        raise self.error

    @override
    def get(self, cohort_id: str) -> CalibrationCohort:
        self.events.append("get")
        if self.outcome == "missing":
            raise DaemonNotFoundError(
                "unknown create did not persist a cohort",
                response=_response(404),
            )

        assert self.attempted is not None
        attempted_id, attempted_spec = self.attempted
        assert cohort_id == attempted_id
        existing_spec = attempted_spec
        if self.outcome == "drift":
            source = attempted_spec.config_source
            existing_spec = CalibrationCohortSpec(
                definition=attempted_spec.definition,
                config_source=CalibrationConfigSourceRef(
                    entry_id="drifted-config-entry",
                    config_ref=source.config_ref,
                    content_hash=source.content_hash,
                    scope=source.scope,
                ),
                fanout_scope=attempted_spec.fanout_scope,
                max_in_flight=attempted_spec.max_in_flight,
                observed_fanout_active_count=(
                    attempted_spec.observed_fanout_active_count
                ),
                evaluated_at=attempted_spec.evaluated_at,
                observations=attempted_spec.observations,
                members=attempted_spec.members,
            )
        return CalibrationCohort(
            cohort_id=cohort_id,
            spec=existing_spec,
            spec_hash=calibration_cohort_spec_hash(existing_spec),
            created_at=existing_spec.evaluated_at,
        )


@pytest.fixture(autouse=True)
def reset_authoring_state() -> None:
    global _targets
    _targets = ()
    _values.clear()
    _dependencies.clear()
    _valid_for.clear()
    _EVENTS.clear()


def test_ready_frontier_is_capacity_bounded_and_builds_after_status() -> None:
    global _targets
    _targets = tuple(_target(id) for id in ("c", "a", "b"))
    _values.update({"a": 1, "b": 2, "c": 3})
    definition = _definition(max_in_flight=3)
    operations = _Operations(active_count=1)

    result = _evaluator(definition, operations).cycle()

    assert result.selected_targets == 3
    assert result.ready_members == 3
    assert result.admitted_members == 2
    assert result.created_cohorts == 1
    assert result.has_more is True
    assert _EVENTS == ["status", "build:a", "build:b", "create"]
    receipt = next(iter(operations.cohorts.values()))
    assert tuple(member.spec.target.id for member in receipt.members) == ("a", "b")
    assert receipt.cohort.cohort_id == calibration_cohort_id(receipt.cohort.spec)
    assert tuple(member.spec.member_id for member in receipt.members) == tuple(
        calibration_key(definition.id, _target(id)) for id in ("a", "b")
    )


def test_full_fanout_capacity_waits_for_normal_poll_instead_of_hot_loop() -> None:
    global _targets
    _targets = (_target("a"),)
    _values["a"] = 1
    definition = _definition(max_in_flight=1)
    operations = _Operations(active_count=1)

    result = _evaluator(definition, operations).cycle()

    assert result.ready_members == 1
    assert result.admitted_members == 0
    assert result.has_more is False
    assert _EVENTS == ["status"]


def test_stale_admission_conflict_waits_for_normal_poll() -> None:
    global _targets
    _targets = (_target("a"),)
    _values["a"] = 1
    definition = _definition()

    class ConflictingOperations(_Operations):
        @override
        def create(
            self,
            cohort_id: str,
            spec: CalibrationCohortSpec,
        ) -> CalibrationCohortCreateReceipt:
            del cohort_id, spec
            raise DaemonConflictError(
                "stale status",
                response=_response(409),
            )

        @override
        def get(self, cohort_id: str) -> CalibrationCohort:
            del cohort_id
            raise DaemonNotFoundError(
                "no cohort for stale decision",
                response=_response(404),
            )

    result = _evaluator(definition, ConflictingOperations()).cycle()

    assert result.admission_conflicts == 1
    assert result.admitted_members == 0
    assert result.has_more is False


def test_unknown_create_outcome_reconciles_matching_exact_cohort() -> None:
    global _targets
    _targets = (_target("a"),)
    _values["a"] = 1
    operations = _UnknownCreateOperations("match")

    result = _evaluator(_definition(), operations).cycle()

    assert result.created_cohorts == 0
    assert result.reconciled_cohorts == 1
    assert result.cohort_drifts == 0
    assert result.admitted_members == 1
    assert _EVENTS == ["status", "build:a", "create", "get"]


def test_unknown_create_outcome_without_exact_cohort_rethrows_transport() -> None:
    global _targets
    _targets = (_target("a"),)
    _values["a"] = 1
    operations = _UnknownCreateOperations("missing")

    with pytest.raises(httpx2.TransportError) as captured:
        _evaluator(_definition(), operations).cycle()

    assert captured.value is operations.error
    assert _EVENTS == ["status", "build:a", "create", "get"]


def test_unknown_create_outcome_records_drift_without_admission() -> None:
    global _targets
    _targets = (_target("a"),)
    _values["a"] = 1
    operations = _UnknownCreateOperations("drift")

    result = _evaluator(_definition(), operations).cycle()

    assert result.created_cohorts == 0
    assert result.reconciled_cohorts == 1
    assert result.cohort_drifts == 1
    assert result.admitted_members == 0
    assert _EVENTS == ["status", "build:a", "create", "get"]


def test_active_failed_attention_and_fresh_members_suppress_builder() -> None:
    global _targets
    _targets = tuple(_target(id) for id in ("active", "failed", "attention", "fresh"))
    _values.update(dict.fromkeys((target.id for target in _targets), 1))
    definition = _definition()
    operations = _Operations(
        statuses={
            calibration_key(definition.id, _target("active")): _attempt_status(
                definition,
                _target("active"),
                procedure_state="ready",
            ),
            calibration_key(definition.id, _target("failed")): _attempt_status(
                definition,
                _target("failed"),
                procedure_state="closed",
                closure_status="failed",
            ),
            calibration_key(definition.id, _target("attention")): _attempt_status(
                definition,
                _target("attention"),
                procedure_state="attention_required",
            ),
            calibration_key(definition.id, _target("fresh")): _attempt_status(
                definition,
                _target("fresh"),
                procedure_state="closed",
                closure_status="succeeded",
            ),
        }
    )

    result = _evaluator(definition, operations).cycle()

    assert result.suppressed_active_members == 1
    assert result.suppressed_failed_members == 1
    assert result.suppressed_attention_members == 1
    assert result.fresh_members == 1
    assert result.ready_members == 0
    assert result.admitted_members == 0
    assert _EVENTS == ["status"]


def test_same_cycle_member_is_not_used_as_dependency() -> None:
    global _targets
    target_a = _target("a")
    target_b = _target("b")
    _targets = (target_a, target_b)
    _values.update({"a": 1, "b": 2})
    definition = _definition()
    _dependencies["a"] = (
        CalibrationDependencyRequirement(
            definition_id=definition.id,
            target=target_b,
        ),
    )
    operations = _Operations()

    result = _evaluator(definition, operations).cycle()

    assert result.blocked_members == 1
    assert result.ready_members == 1
    assert result.admitted_members == 1
    assert _EVENTS == ["status", "build:b", "create"]
    member = next(iter(operations.cohorts.values())).cohort.spec.members[0]
    assert member.target == target_b
    assert member.dependencies == ()


def test_added_dependency_records_explicit_due_evidence() -> None:
    global _targets
    target = _target("a")
    _targets = (target,)
    _values["a"] = 1
    definition = _definition()
    dependency_definition = _definition(id="tests.dependency")
    dependency_target = _target("dependency")
    dependency_status = _attempt_status(
        dependency_definition,
        dependency_target,
        procedure_state="closed",
        closure_status="succeeded",
    )
    assert dependency_status.latest_success is not None
    current_dependency = dependency_status.latest_success.dependency_evidence
    _dependencies["a"] = (
        CalibrationDependencyRequirement(
            definition_id=dependency_definition.id,
            target=dependency_target,
        ),
    )
    operations = _Operations(
        statuses={
            calibration_key(definition.id, target): _attempt_status(
                definition,
                target,
                procedure_state="closed",
                closure_status="succeeded",
            ),
            dependency_status.calibration_key: dependency_status,
        }
    )

    result = _evaluator(definition, operations).cycle()

    assert result.admitted_members == 1
    member = next(iter(operations.cohorts.values())).cohort.spec.members[0]
    assert member.dependencies == (current_dependency,)
    dependency_reasons = tuple(
        reason
        for reason in member.due_reasons
        if isinstance(reason, CalibrationDependencyChangedDueReason)
    )
    assert len(dependency_reasons) == 1
    assert dependency_reasons[0].previous_success is None
    assert dependency_reasons[0].current_success == current_dependency


def test_removed_dependency_records_explicit_due_evidence() -> None:
    global _targets
    target = _target("a")
    _targets = (target,)
    _values["a"] = 1
    definition = _definition()
    dependency_definition = _definition(id="tests.dependency")
    dependency_target = _target("dependency")
    dependency_status = _attempt_status(
        dependency_definition,
        dependency_target,
        procedure_state="closed",
        closure_status="succeeded",
    )
    assert dependency_status.latest_success is not None
    previous_dependency = dependency_status.latest_success.dependency_evidence
    operations = _Operations(
        statuses={
            calibration_key(definition.id, target): _attempt_status(
                definition,
                target,
                dependencies=(previous_dependency,),
                procedure_state="closed",
                closure_status="succeeded",
            )
        }
    )

    result = _evaluator(definition, operations).cycle()

    assert result.admitted_members == 1
    member = next(iter(operations.cohorts.values())).cohort.spec.members[0]
    assert member.dependencies == ()
    dependency_reasons = tuple(
        reason
        for reason in member.due_reasons
        if isinstance(reason, CalibrationDependencyChangedDueReason)
    )
    assert len(dependency_reasons) == 1
    assert dependency_reasons[0].previous_success == previous_dependency
    assert dependency_reasons[0].current_success is None


def test_dependency_reordering_does_not_change_freshness() -> None:
    global _targets
    target = _target("a")
    _targets = (target,)
    _values["a"] = 1
    definition = _definition()
    first_definition = _definition(id="tests.dependency.first")
    second_definition = _definition(id="tests.dependency.second")
    first_target = _target("first")
    second_target = _target("second")
    first_status = _attempt_status(
        first_definition,
        first_target,
        procedure_state="closed",
        closure_status="succeeded",
    )
    second_status = _attempt_status(
        second_definition,
        second_target,
        procedure_state="closed",
        closure_status="succeeded",
    )
    assert first_status.latest_success is not None
    assert second_status.latest_success is not None
    first_evidence = first_status.latest_success.dependency_evidence
    second_evidence = second_status.latest_success.dependency_evidence
    _dependencies["a"] = (
        CalibrationDependencyRequirement(
            definition_id=second_definition.id,
            target=second_target,
        ),
        CalibrationDependencyRequirement(
            definition_id=first_definition.id,
            target=first_target,
        ),
    )
    operations = _Operations(
        statuses={
            calibration_key(definition.id, target): _attempt_status(
                definition,
                target,
                dependencies=(first_evidence, second_evidence),
                procedure_state="closed",
                closure_status="succeeded",
            ),
            first_status.calibration_key: first_status,
            second_status.calibration_key: second_status,
        }
    )

    result = _evaluator(definition, operations).cycle()

    assert result.fresh_members == 1
    assert result.admitted_members == 0
    assert _EVENTS == ["status"]


def test_changed_inputs_record_typed_due_reason() -> None:
    global _targets
    target = _target("a")
    _targets = (target,)
    _values["a"] = 2
    definition = _definition()
    key = calibration_key(definition.id, target)
    operations = _Operations(
        statuses={
            key: _attempt_status(
                definition,
                target,
                input_value=1,
                procedure_state="closed",
                closure_status="succeeded",
            )
        }
    )

    result = _evaluator(definition, operations).cycle()

    assert result.admitted_members == 1
    member = next(iter(operations.cohorts.values())).cohort.spec.members[0]
    assert any(
        isinstance(reason, CalibrationInputsChangedDueReason)
        for reason in member.due_reasons
    )


def test_changed_inputs_after_failed_need_admit_new_work() -> None:
    global _targets
    target = _target("a")
    _targets = (target,)
    _values["a"] = 2
    definition = _definition()
    operations = _Operations(
        statuses={
            calibration_key(definition.id, target): _attempt_status(
                definition,
                target,
                input_value=1,
                procedure_state="closed",
                closure_status="failed",
            )
        }
    )

    result = _evaluator(definition, operations).cycle()

    assert result.suppressed_failed_members == 0
    assert result.admitted_members == 1


def test_published_result_success_waits_for_publication_without_rerunning() -> None:
    global _targets
    target = _target("a")
    _targets = (target,)
    _values["a"] = 1
    _valid_for["a"] = timedelta(minutes=30)
    definition = _definition(success_policy="published_result")
    operations = _Operations(
        statuses={
            calibration_key(definition.id, target): _attempt_status(
                definition,
                target,
                procedure_state="closed",
                closure_status="succeeded",
            )
        }
    )

    result = _evaluator(definition, operations).cycle()

    assert result.pending_publication_members == 1
    assert result.fresh_members == 0
    assert result.ready_members == 0
    assert result.admitted_members == 0
    assert _EVENTS == ["status"]


def test_pending_publication_is_not_usable_as_dependency() -> None:
    global _targets
    target = _target("a")
    dependency_target = _target("dependency")
    _targets = (target,)
    _values["a"] = 1
    definition = _definition()
    dependency_definition = _definition(
        id="tests.dependency",
        success_policy="published_result",
    )
    _dependencies["a"] = (
        CalibrationDependencyRequirement(
            definition_id=dependency_definition.id,
            target=dependency_target,
        ),
    )
    dependency_status = _attempt_status(
        dependency_definition,
        dependency_target,
        procedure_state="closed",
        closure_status="succeeded",
    )
    operations = _Operations(
        statuses={dependency_status.calibration_key: dependency_status}
    )

    result = _evaluator(definition, operations).cycle()

    assert result.blocked_members == 1
    assert result.ready_members == 0
    assert result.admitted_members == 0


def test_pending_publication_base_drift_admits_a_new_truthful_need() -> None:
    global _targets
    target = _target("a")
    _targets = (target,)
    _values["a"] = 1
    definition = _definition(success_policy="published_result")
    pending_status = _attempt_status(
        definition,
        target,
        procedure_state="closed",
        closure_status="succeeded",
    )
    operations = _Operations(statuses={pending_status.calibration_key: pending_status})
    evaluator = ProjectCalibrationEvaluator(
        operations,
        CalibrationRegistry((definition,)),
        lambda: _context(head_version=2),
    )

    result = evaluator.cycle()

    assert result.pending_publication_members == 0
    assert result.admitted_members == 1
    member = next(iter(operations.cohorts.values())).cohort.spec.members[0]
    reasons = tuple(
        reason
        for reason in member.due_reasons
        if isinstance(reason, CalibrationPublicationBaseChangedDueReason)
    )
    assert len(reasons) == 1
    assert reasons[0].previous_success == pending_status.latest_success
    assert reasons[0].current_config_source == _context(head_version=2).config_source


def test_evaluator_pins_exact_automatic_publication_policy_into_cohort() -> None:
    global _targets
    target = _target("a")
    _targets = (target,)
    _values["a"] = 1
    definition = _definition(success_policy="published_result")
    policy = CalibrationPublicationPolicy(
        id="tests.calibration.automatic-publication",
        version="1",
        calibration=definition.ref,
        composition_policy=ConfigCompositionPolicyRef(
            id="tests.calibration.composition",
            version="1",
            fingerprint=f"sha256:{'8' * 64}",
        ),
        actor="calibration-finalizer",
        note="publish exact cohort",
        _prepare=_unused_publication_prepare,
    )
    operations = _Operations()
    evaluator = ProjectCalibrationEvaluator(
        operations,
        CalibrationRegistry((definition,)),
        _context,
        publication_policies=CalibrationPublicationPolicyRegistry((policy,)),
    )

    result = evaluator.cycle()

    assert result.created_cohorts == 1
    [receipt] = operations.cohorts.values()
    assert receipt.cohort.spec.automatic_publication == policy.ref


def test_published_result_uses_result_inputs_as_effective_freshness() -> None:
    global _targets
    target = _target("a")
    _targets = (target,)
    _values["a"] = 2
    definition = _definition(success_policy="published_result")
    pending_status = _attempt_status(
        definition,
        target,
        input_value=1,
        procedure_state="closed",
        closure_status="succeeded",
    )
    published_status = _published_status(
        pending_status,
        definition,
        result_input_value=2,
    )
    operations = _Operations(
        statuses={published_status.calibration_key: published_status}
    )
    evaluator = ProjectCalibrationEvaluator(
        operations,
        CalibrationRegistry((definition,)),
        lambda: _context(head_version=2),
    )

    result = evaluator.cycle()

    assert result.fresh_members == 1
    assert result.pending_publication_members == 0
    assert result.admitted_members == 0
    assert _EVENTS == ["status"]


def test_published_result_ttl_still_starts_at_procedure_closure() -> None:
    global _targets
    target = _target("a")
    _targets = (target,)
    _values["a"] = 2
    _valid_for["a"] = timedelta(minutes=45)
    definition = _definition(success_policy="published_result")
    pending_status = _attempt_status(
        definition,
        target,
        input_value=1,
        procedure_state="closed",
        closure_status="succeeded",
    )
    published_status = _published_status(
        pending_status,
        definition,
        result_input_value=2,
    )
    operations = _Operations(
        statuses={published_status.calibration_key: published_status}
    )
    evaluator = ProjectCalibrationEvaluator(
        operations,
        CalibrationRegistry((definition,)),
        lambda: _context(head_version=2),
    )

    result = evaluator.cycle()

    assert result.fresh_members == 0
    assert result.admitted_members == 1
    member = next(iter(operations.cohorts.values())).cohort.spec.members[0]
    expired = tuple(reason for reason in member.due_reasons if reason.kind == "expired")
    assert len(expired) == 1
    assert expired[0].expired_at == _NOW - timedelta(minutes=15)


def test_generation_only_context_change_does_not_stale_semantic_inputs() -> None:
    global _targets
    target = _target("a")
    _targets = (target,)
    _values["a"] = 1
    definition = _definition()
    operations = _Operations(
        statuses={
            calibration_key(definition.id, target): _attempt_status(
                definition,
                target,
                procedure_state="closed",
                closure_status="succeeded",
            )
        }
    )
    evaluator = ProjectCalibrationEvaluator(
        operations,
        CalibrationRegistry((definition,)),
        lambda: _context(head_version=2),
    )

    result = evaluator.cycle()

    assert result.fresh_members == 1
    assert result.admitted_members == 0
    assert _EVENTS == ["status"]


def test_definition_change_after_failed_need_admits_new_work() -> None:
    global _targets
    target = _target("a")
    _targets = (target,)
    _values["a"] = 1
    previous = _definition()
    current = replace(previous, version="2")
    operations = _Operations(
        statuses={
            calibration_key(previous.id, target): _attempt_status(
                previous,
                target,
                procedure_state="closed",
                closure_status="failed",
            )
        }
    )

    result = _evaluator(current, operations).cycle()

    assert result.suppressed_failed_members == 0
    assert result.admitted_members == 1


def test_pre_stopped_evaluator_does_not_capture_context() -> None:
    calls = 0

    def context() -> CalibrationPlanningContext:
        nonlocal calls
        calls += 1
        return _context()

    stop = Event()
    stop.set()
    evaluator = ProjectCalibrationEvaluator(
        _Operations(),
        CalibrationRegistry((_definition(),)),
        context,
    )

    result = evaluator.cycle(stop)

    assert calls == 0
    assert result.has_more is True


def _evaluator(
    definition: CalibrationDefinition[CalibrationPlanningContext, _Inputs, _Intent],
    operations: _Operations,
) -> ProjectCalibrationEvaluator:
    return ProjectCalibrationEvaluator(
        operations,
        CalibrationRegistry((definition,)),
        _context,
    )


def _context(*, head_version: int = 1) -> CalibrationPlanningContext:
    return CalibrationPlanningContext(
        config=cast("ConfigProfileSnapshot", object()),
        config_source=CalibrationConfigSourceRef(
            entry_id=f"config-entry-{head_version}",
            config_ref=f"config-entry-{head_version}@r1",
            content_hash=f"sha256:{str(head_version % 10) * 64}",
            scope=_SCOPE,
        ),
    )


def _target(id: str) -> CalibrationTargetRef:
    return _context().target(CalibrationTargetRef(kind="qubit", id=id))


def _response(status: int) -> httpx2.Response:
    return httpx2.Response(
        status,
        request=httpx2.Request("POST", "http://daemon.test/calibration-cohorts"),
    )


def _attempt_status(
    definition: CalibrationDefinition[CalibrationPlanningContext, _Inputs, _Intent],
    target: CalibrationTargetRef,
    *,
    input_value: int = 1,
    dependencies: tuple[CalibrationDependencyEvidence, ...] = (),
    procedure_state: ProcedureRunState,
    closure_status: ProcedureCloseStatus | None = None,
) -> CalibrationStatus:
    inputs = _Inputs(value=input_value)
    input_fingerprint = definition.input_fingerprint(inputs)
    freshness = calibration_freshness_fingerprint(
        definition=definition.ref,
        target=target,
        procedure=definition.procedure.ref,
        input_fingerprint=input_fingerprint,
        dependencies=dependencies,
    )
    key = calibration_key(definition.id, target)
    attempt = CalibrationAttemptRef(
        calibration_key=key,
        cohort_id=f"cohort-{target.id}",
        member_id=key,
        procedure_run_id=f"previous-{target.id}",
        definition=definition.ref,
        target=target,
        procedure=definition.procedure.ref,
        input_fingerprint=input_fingerprint,
        dependencies=dependencies,
        freshness_fingerprint=freshness,
        admitted_at=_NOW - timedelta(hours=2),
    )
    closure = (
        None
        if closure_status is None
        else ProcedureClosure(
            status=closure_status,
            closed_at=_NOW - timedelta(hours=1),
            reason=None if closure_status == "succeeded" else "failed",
        )
    )
    attempt_status = CalibrationAttemptStatus(
        attempt=attempt,
        procedure_state=procedure_state,
        procedure_revision=1,
        updated_at=_NOW - timedelta(hours=1),
        closure=closure,
    )
    success = None
    if closure_status == "succeeded":
        assert closure is not None
        success = CalibrationSuccessRef(
            attempt=attempt,
            base_config_source=_context().config_source,
            succeeded_at=closure.closed_at,
        )
    return CalibrationStatus(
        calibration_key=key,
        latest_attempt=attempt_status,
        latest_success=success,
    )


def _published_status(
    status: CalibrationStatus,
    definition: CalibrationDefinition[CalibrationPlanningContext, _Inputs, _Intent],
    *,
    result_input_value: int,
) -> CalibrationStatus:
    pending = status.latest_success
    assert pending is not None
    result_input_fingerprint = definition.input_fingerprint(
        _Inputs(value=result_input_value)
    )
    publication = CalibrationSuccessPublication(
        operation_id=f"publish-{pending.attempt.target.id}",
        source_intent_hash=f"sha256:{'8' * 64}",
        result_input_fingerprint=result_input_fingerprint,
        result_freshness_fingerprint=calibration_freshness_fingerprint(
            definition=pending.attempt.definition,
            target=pending.attempt.target,
            procedure=pending.attempt.procedure,
            input_fingerprint=result_input_fingerprint,
            dependencies=pending.attempt.dependencies,
        ),
        result_config_source=_context(head_version=2).config_source,
        published_at=_NOW - timedelta(minutes=30),
    )
    published = CalibrationSuccessRef(
        attempt=pending.attempt,
        base_config_source=pending.base_config_source,
        succeeded_at=pending.succeeded_at,
        publication=publication,
    )
    return CalibrationStatus(
        calibration_key=status.calibration_key,
        latest_attempt=status.latest_attempt,
        latest_success=published,
    )


def test_planner_normalizes_targets_and_dependency_ownership() -> None:
    global _targets
    definition = _definition()
    _targets = (CalibrationTargetRef(kind="qubit", id="a"),)
    _values["a"] = 1
    descriptor = CalibrationTargetRef(kind="qubit", id="dependency")
    dependency_definition = _definition(id="tests.dependency")
    dependency_status = _attempt_status(
        dependency_definition,
        _context().target(descriptor),
        procedure_state="closed",
        closure_status="succeeded",
    )
    _dependencies["a"] = (
        CalibrationDependencyRequirement(
            definition_id=dependency_definition.id, target=descriptor
        ),
    )
    operations = _Operations(
        statuses={dependency_status.calibration_key: dependency_status}
    )
    result = _evaluator(definition, operations).cycle()
    assert result.admitted_members == 1
    member = next(iter(operations.cohorts.values())).cohort.spec.members[0]
    assert member.target == _context().target(_targets[0])
    assert member.dependencies[0].calibration_key == _context().key(
        dependency_definition.id, descriptor
    )


def test_scoped_keys_follow_owner_not_head_and_reject_cross_owner_dependencies() -> (
    None
):
    global _targets
    descriptor = CalibrationTargetRef(kind="qubit", id="a")
    context = _context()
    other = CalibrationPlanningContext(
        config=context.config,
        config_source=context.config_source.model_copy(
            update={
                "scope": _SCOPE.model_copy(update={"workspace_id": "another-branch"}),
            }
        ),
    )
    definition = _definition()
    assert context.key(definition.id, descriptor) == _context(head_version=2).key(
        definition.id, descriptor
    )
    assert context.key(definition.id, descriptor) != other.key(
        definition.id, descriptor
    )
    assert context.config_source.scope.sample_selectors()[0].revision == 1
    _targets = (descriptor,)
    _values["a"] = 1
    _dependencies["a"] = (
        CalibrationDependencyRequirement(
            definition_id="other", target=other.target(descriptor)
        ),
    )
    operations = _Operations()
    result = _evaluator(definition, operations).cycle()
    assert result.failures == 1
    assert not operations.cohorts
    assert _EVENTS == []


def test_catalog_evaluator_checks_devices_without_choosing_a_writable_owner() -> None:
    from scopecat.records.calibration_scope import CatalogCalibrationScope

    global _targets
    _targets = (CalibrationTargetRef(kind="instrument", id="a"),)
    _values["a"] = 1
    catalog = CalibrationPlanningContext(
        config=_context().config,
        config_source=_context().config_source.model_copy(
            update={"scope": CatalogCalibrationScope()}
        ),
    )
    operations = _Operations()
    result = ProjectCalibrationEvaluator(
        operations,
        CalibrationRegistry(
            (
                _definition("check"),
                _definition("publish", success_policy="published_result"),
            )
        ),
        lambda: catalog,
    ).cycle()
    assert result.admitted_members == 1
    [receipt] = operations.cohorts.values()
    assert receipt.cohort.spec.definition.id == "check"
    assert receipt.cohort.spec.config_source.scope.sample_selectors() == ()
