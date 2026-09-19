"""Project-side calibration freshness evaluation and bounded cohort admission."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from threading import Event
from typing import Literal, Protocol

import httpx2
from pydantic import BaseModel

from scopecat.api.calibration_policy import CalibrationPublicationPolicyRegistry
from scopecat.automation.calibration_definition import (
    CalibrationObservation,
    CalibrationRegistry,
    RegisteredCalibration,
)
from scopecat.automation.calibration_wire import CalibrationCohortCreateReceipt
from scopecat.automation.calibrations import (
    MAX_CALIBRATION_STATUS_KEYS,
    CalibrationCohort,
    CalibrationCohortMemberSpec,
    CalibrationCohortSpec,
    CalibrationConfigSourceRef,
    CalibrationDefinitionChangedDueReason,
    CalibrationDependencyChangedDueReason,
    CalibrationDependencyEvidence,
    CalibrationDueReason,
    CalibrationExpiredDueReason,
    CalibrationInputsChangedDueReason,
    CalibrationMissingSuccessDueReason,
    CalibrationPublicationBaseChangedDueReason,
    CalibrationStatus,
    CalibrationStatusSnapshot,
    CalibrationSuccessRef,
    CalibrationTargetRef,
    calibration_cohort_spec_hash,
    calibration_freshness_fingerprint,
    calibration_key,
    scoped_calibration_target,
)
from scopecat.daemon.client import (
    DaemonClientError,
    DaemonConflictError,
    DaemonNotFoundError,
)
from scopecat.records.calibration_scope import WorkingPointCalibrationScope
from scopecat.records.config import ConfigProfileSnapshot

_TRANSIENT_CLIENT_STATUSES = frozenset({408, 425, 429})
_LOG = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CalibrationPlanningContext:
    """One exact active configuration snapshot shared by a planning cycle."""

    config: ConfigProfileSnapshot
    config_source: CalibrationConfigSourceRef

    def target(self, target: CalibrationTargetRef) -> CalibrationTargetRef:
        return scoped_calibration_target(target, self.config_source)

    def key(self, definition_id: str, target: CalibrationTargetRef) -> str:
        return calibration_key(definition_id, self.target(target))


class CalibrationPlanningOperations(Protocol):
    """Narrow durable operations required by the project evaluator."""

    def status(
        self,
        calibration_keys: tuple[str, ...],
        *,
        fanout_scope: str,
    ) -> CalibrationStatusSnapshot: ...

    def create(
        self,
        cohort_id: str,
        spec: CalibrationCohortSpec,
    ) -> CalibrationCohortCreateReceipt: ...

    def get(self, cohort_id: str) -> CalibrationCohort: ...


@dataclass(frozen=True, slots=True)
class CalibrationEvaluatorCycleResult:
    """Bounded calibration decisions made from exact server observations."""

    definitions: int
    selected_targets: int
    fresh_members: int
    pending_publication_members: int
    blocked_members: int
    suppressed_active_members: int
    suppressed_failed_members: int
    suppressed_attention_members: int
    ready_members: int
    admitted_members: int
    created_cohorts: int
    reconciled_cohorts: int
    admission_conflicts: int
    cohort_drifts: int
    failures: int
    has_more: bool


@dataclass(frozen=True, slots=True)
class _ObservedMember:
    target: CalibrationTargetRef
    observation: CalibrationObservation[BaseModel]


@dataclass(frozen=True, slots=True)
class _ReadyMember:
    observed: _ObservedMember
    status: CalibrationStatus
    input_fingerprint: str
    dependencies: tuple[CalibrationDependencyEvidence, ...]
    freshness_fingerprint: str
    due_reasons: tuple[CalibrationDueReason, ...]


type _Suppression = Literal["active", "failed", "attention"]


class ProjectCalibrationEvaluator:
    """Evaluate flat freshness evidence and atomically admit one ready frontier."""

    __slots__ = (
        "_context",
        "_operations",
        "_publication_policies",
        "_registry",
    )

    def __init__(
        self,
        operations: CalibrationPlanningOperations,
        registry: CalibrationRegistry[CalibrationPlanningContext],
        context: Callable[[], CalibrationPlanningContext],
        *,
        publication_policies: CalibrationPublicationPolicyRegistry | None = None,
    ) -> None:
        self._operations = operations
        self._registry = registry
        self._context = context
        self._publication_policies = (
            publication_policies
            if publication_policies is not None
            else CalibrationPublicationPolicyRegistry()
        )

    def cycle(  # noqa: C901 - explicit bounded planning phase boundaries
        self,
        stop: Event | None = None,
    ) -> CalibrationEvaluatorCycleResult:
        """Evaluate every bounded definition against one exact config snapshot."""

        if stop is not None and stop.is_set():
            return _empty_cycle(has_more=bool(self._registry))
        if not self._registry:
            return _empty_cycle()

        context = self._context()
        totals = _MutableCycle()
        for definition in self._registry.values():
            if _stopped(stop):
                totals.has_more = True
                break
            if definition.ref.success_policy == "published_result" and not isinstance(
                context.config_source.scope, WorkingPointCalibrationScope
            ):
                continue
            totals.definitions += 1
            publication_policy = self._publication_policies.for_calibration(
                definition.ref
            )
            try:
                selected = definition.select_targets(context)
            except Exception as error:
                if _is_transient_control_error(error):
                    raise
                totals.failures += 1
                _LOG.exception("calibration %s selector failed", definition.id)
                continue
            totals.selected_targets += len(selected)
            if not selected:
                continue

            observed: list[_ObservedMember] = []
            for target in selected:
                if _stopped(stop):
                    totals.has_more = True
                    break
                try:
                    target = context.target(target)
                    observation = definition.observe(context, target)
                    observation = observation.model_copy(
                        update={
                            "dependencies": tuple(
                                dependency.model_copy(
                                    update={"target": context.target(dependency.target)}
                                )
                                for dependency in observation.dependencies
                            ),
                        }
                    )
                except Exception as error:
                    if _is_transient_control_error(error):
                        raise
                    totals.failures += 1
                    _LOG.exception(
                        "calibration %s input observation failed for %s/%s",
                        definition.id,
                        target.kind,
                        target.id,
                    )
                    continue
                member_key = calibration_key(definition.id, target)
                if any(
                    dependency.calibration_key == member_key
                    for dependency in observation.dependencies
                ):
                    totals.failures += 1
                    _LOG.error(
                        "calibration %s target %s/%s depends on itself",
                        definition.id,
                        target.kind,
                        target.id,
                    )
                    continue
                observed.append(_ObservedMember(target, observation))
            if _stopped(stop):
                break
            if not observed:
                continue

            keys = _status_keys(definition, observed)
            if len(keys) > MAX_CALIBRATION_STATUS_KEYS:
                totals.failures += 1
                _LOG.error(
                    "calibration %s requires %d status keys; maximum is %d",
                    definition.id,
                    len(keys),
                    MAX_CALIBRATION_STATUS_KEYS,
                )
                continue
            if _stopped(stop):
                totals.has_more = True
                break
            try:
                snapshot = self._operations.status(
                    keys,
                    fanout_scope=definition.fanout_scope,
                )
                statuses = _validate_status_snapshot(snapshot, keys, definition)
            except Exception as error:
                if _is_transient_control_error(error):
                    raise
                totals.failures += 1
                _LOG.exception("calibration %s status query failed", definition.id)
                continue

            ready: list[_ReadyMember] = []
            for member in observed:
                status = statuses[calibration_key(definition.id, member.target)]
                suppression = _suppression(status)
                if suppression == "active":
                    totals.suppressed_active += 1
                    continue
                if suppression == "attention":
                    totals.suppressed_attention += 1
                    continue

                dependencies: list[CalibrationDependencyEvidence] = []
                for requirement in member.observation.dependencies:
                    success = statuses[requirement.calibration_key].latest_success
                    if success is None or not success.is_effective:
                        break
                    dependencies.append(success.dependency_evidence)
                else:
                    input_fingerprint = definition.input_fingerprint(
                        member.observation.inputs
                    )
                    exact_dependencies = tuple(
                        sorted(
                            dependencies,
                            key=lambda dependency: dependency.calibration_key,
                        )
                    )
                    freshness = calibration_freshness_fingerprint(
                        definition=definition.ref,
                        target=member.target,
                        procedure=definition.procedure.ref,
                        input_fingerprint=input_fingerprint,
                        dependencies=exact_dependencies,
                    )
                    if (
                        suppression == "failed"
                        and status.latest_attempt is not None
                        and status.latest_attempt.attempt.freshness_fingerprint
                        == freshness
                    ):
                        totals.suppressed_failed += 1
                        continue
                    success = status.latest_success
                    if success is not None and _pending_publication_matches(
                        success,
                        definition=definition,
                        freshness_fingerprint=freshness,
                        config_source=context.config_source,
                    ):
                        totals.pending_publication += 1
                        continue
                    reasons = _due_reasons(
                        definition,
                        member,
                        status,
                        input_fingerprint=input_fingerprint,
                        dependencies=exact_dependencies,
                        config_source=context.config_source,
                        evaluated_at=snapshot.observed_at,
                    )
                    if reasons:
                        ready.append(
                            _ReadyMember(
                                observed=member,
                                status=status,
                                input_fingerprint=input_fingerprint,
                                dependencies=exact_dependencies,
                                freshness_fingerprint=freshness,
                                due_reasons=reasons,
                            )
                        )
                    else:
                        totals.fresh += 1
                    continue
                totals.blocked += 1

            totals.ready += len(ready)
            available = max(
                0,
                definition.max_in_flight - snapshot.fanout_active_count,
            )
            frontier = ready[:available]
            frontier_has_more = bool(frontier) and len(frontier) < len(ready)
            if not frontier:
                continue

            member_specs: list[CalibrationCohortMemberSpec] = []
            for member in frontier:
                if _stopped(stop):
                    totals.has_more = True
                    break
                try:
                    intent = definition.build_intent(
                        context,
                        member.observed.target,
                        member.observed.observation.inputs,
                        member.dependencies,
                    )
                    encoded_intent = definition.procedure.encode_intent(intent)
                    member_key = calibration_key(
                        definition.id,
                        member.observed.target,
                    )
                    member_specs.append(
                        CalibrationCohortMemberSpec(
                            member_id=member_key,
                            calibration_key=member_key,
                            definition=definition.ref,
                            target=member.observed.target,
                            procedure=definition.procedure.ref,
                            intent=encoded_intent,
                            input_fingerprint=member.input_fingerprint,
                            dependencies=member.dependencies,
                            freshness_fingerprint=member.freshness_fingerprint,
                            due_reasons=member.due_reasons,
                        )
                    )
                except Exception as error:
                    if _is_transient_control_error(error):
                        raise
                    totals.failures += 1
                    _LOG.exception(
                        "calibration %s intent builder failed for %s/%s",
                        definition.id,
                        member.observed.target.kind,
                        member.observed.target.id,
                    )
            if _stopped(stop):
                break
            if not member_specs:
                continue

            spec = CalibrationCohortSpec(
                definition=definition.ref,
                automatic_publication=(
                    None if publication_policy is None else publication_policy.ref
                ),
                config_source=context.config_source,
                fanout_scope=definition.fanout_scope,
                max_in_flight=definition.max_in_flight,
                observed_fanout_active_count=snapshot.fanout_active_count,
                evaluated_at=snapshot.observed_at,
                observations=snapshot.statuses,
                members=tuple(member_specs),
            )
            cohort_id = calibration_cohort_id(spec)
            if _stopped(stop):
                totals.has_more = True
                break
            try:
                receipt = self._operations.create(cohort_id, spec)
            except DaemonConflictError:
                try:
                    existing = self._operations.get(cohort_id)
                except DaemonNotFoundError:
                    totals.admission_conflicts += 1
                    continue
                except (DaemonClientError, httpx2.HTTPError) as error:
                    if _is_transient_control_error(error):
                        raise
                    totals.failures += 1
                    _LOG.exception(
                        "calibration cohort %s conflict could not be reconciled",
                        cohort_id,
                    )
                    continue
                totals.reconciled_cohorts += 1
                if not _matches_cohort(existing, cohort_id, spec):
                    totals.cohort_drifts += 1
                else:
                    totals.admitted += len(spec.members)
                    totals.has_more |= frontier_has_more
                continue
            except httpx2.TransportError as error:
                try:
                    existing = self._operations.get(cohort_id)
                except DaemonNotFoundError:
                    raise error from None
                totals.reconciled_cohorts += 1
                if not _matches_cohort(existing, cohort_id, spec):
                    totals.cohort_drifts += 1
                else:
                    totals.admitted += len(spec.members)
                    totals.has_more |= frontier_has_more
                continue
            except (DaemonClientError, httpx2.HTTPError) as error:
                if _is_transient_control_error(error):
                    raise
                totals.failures += 1
                _LOG.exception("calibration cohort %s admission failed", cohort_id)
                continue

            totals.created_cohorts += 1
            if not _matches_cohort(receipt.cohort, cohort_id, spec):
                totals.cohort_drifts += 1
            else:
                totals.admitted += len(receipt.members)
                totals.has_more |= frontier_has_more

        return totals.freeze()


@dataclass(slots=True)
class _MutableCycle:
    definitions: int = 0
    selected_targets: int = 0
    fresh: int = 0
    pending_publication: int = 0
    blocked: int = 0
    suppressed_active: int = 0
    suppressed_failed: int = 0
    suppressed_attention: int = 0
    ready: int = 0
    admitted: int = 0
    created_cohorts: int = 0
    reconciled_cohorts: int = 0
    admission_conflicts: int = 0
    cohort_drifts: int = 0
    failures: int = 0
    has_more: bool = False

    def freeze(self) -> CalibrationEvaluatorCycleResult:
        return CalibrationEvaluatorCycleResult(
            definitions=self.definitions,
            selected_targets=self.selected_targets,
            fresh_members=self.fresh,
            pending_publication_members=self.pending_publication,
            blocked_members=self.blocked,
            suppressed_active_members=self.suppressed_active,
            suppressed_failed_members=self.suppressed_failed,
            suppressed_attention_members=self.suppressed_attention,
            ready_members=self.ready,
            admitted_members=self.admitted,
            created_cohorts=self.created_cohorts,
            reconciled_cohorts=self.reconciled_cohorts,
            admission_conflicts=self.admission_conflicts,
            cohort_drifts=self.cohort_drifts,
            failures=self.failures,
            has_more=self.has_more,
        )


def calibration_cohort_id(spec: CalibrationCohortSpec) -> str:
    """Derive a deterministic caller identity from the complete cohort decision."""

    digest = calibration_cohort_spec_hash(spec).removeprefix("sha256:")
    return f"calibration-cohort:{digest}"


def _status_keys(
    definition: RegisteredCalibration[CalibrationPlanningContext],
    observed: list[_ObservedMember],
) -> tuple[str, ...]:
    keys = {calibration_key(definition.id, member.target) for member in observed}
    keys.update(
        dependency.calibration_key
        for member in observed
        for dependency in member.observation.dependencies
    )
    return tuple(sorted(keys))


def _validate_status_snapshot(
    snapshot: CalibrationStatusSnapshot,
    keys: tuple[str, ...],
    definition: RegisteredCalibration[CalibrationPlanningContext],
) -> dict[str, CalibrationStatus]:
    if snapshot.fanout_scope != definition.fanout_scope:
        raise ValueError("calibration status snapshot fanout scope drifted")
    if tuple(status.calibration_key for status in snapshot.statuses) != keys:
        raise ValueError(
            "calibration status snapshot must preserve requested key order"
        )
    statuses = {status.calibration_key: status for status in snapshot.statuses}
    if set(statuses) != set(keys):
        raise ValueError("calibration status snapshot must cover every requested key")
    return statuses


def _suppression(status: CalibrationStatus) -> _Suppression | None:
    attempt = status.latest_attempt
    if attempt is None:
        return None
    if attempt.procedure_state == "attention_required":
        return "attention"
    if attempt.procedure_state != "closed":
        return "active"
    if attempt.closure is None or attempt.closure.status != "succeeded":
        return "failed"
    return None


def _due_reasons(
    definition: RegisteredCalibration[CalibrationPlanningContext],
    member: _ObservedMember,
    status: CalibrationStatus,
    *,
    input_fingerprint: str,
    dependencies: tuple[CalibrationDependencyEvidence, ...],
    config_source: CalibrationConfigSourceRef,
    evaluated_at: datetime,
) -> tuple[CalibrationDueReason, ...]:
    success = status.latest_success
    if success is None:
        reasons: list[CalibrationDueReason] = [CalibrationMissingSuccessDueReason()]
    else:
        previous = success.attempt
        reasons = []
        if (
            previous.definition != definition.ref
            or previous.procedure != definition.procedure.ref
        ):
            reasons.append(
                CalibrationDefinitionChangedDueReason(previous_success=success)
            )
        previous_input = (
            success.effective_input_fingerprint
            if success.is_effective
            else previous.input_fingerprint
        )
        if previous_input != input_fingerprint:
            reasons.append(CalibrationInputsChangedDueReason(previous_success=success))
        if (
            previous.definition.success_policy == "published_result"
            and success.publication is None
            and success.base_config_source != config_source
        ):
            reasons.append(
                CalibrationPublicationBaseChangedDueReason(
                    previous_success=success,
                    current_config_source=config_source,
                )
            )
        previous_dependencies = {
            dependency.calibration_key: dependency
            for dependency in previous.dependencies
        }
        current_dependencies = {
            dependency.calibration_key: dependency for dependency in dependencies
        }
        for dependency_key in sorted(
            previous_dependencies.keys() | current_dependencies.keys()
        ):
            prior = previous_dependencies.get(dependency_key)
            current = current_dependencies.get(dependency_key)
            if prior != current:
                reasons.append(
                    CalibrationDependencyChangedDueReason(
                        dependency_key=dependency_key,
                        previous_success=prior,
                        current_success=current,
                    )
                )
        valid_for = member.observation.valid_for
        if valid_for is not None:
            expired_at = success.succeeded_at + valid_for
            if evaluated_at >= expired_at:
                reasons.append(
                    CalibrationExpiredDueReason(
                        previous_success=success,
                        expired_at=expired_at,
                    )
                )
    return tuple(reasons)


def _pending_publication_matches(
    success: CalibrationSuccessRef,
    *,
    definition: RegisteredCalibration[CalibrationPlanningContext],
    freshness_fingerprint: str,
    config_source: CalibrationConfigSourceRef,
) -> bool:
    attempt = success.attempt
    return (
        attempt.definition.success_policy == "published_result"
        and success.publication is None
        and attempt.definition == definition.ref
        and attempt.procedure == definition.procedure.ref
        and attempt.freshness_fingerprint == freshness_fingerprint
        and success.base_config_source == config_source
    )


def _matches_cohort(
    cohort: CalibrationCohort,
    cohort_id: str,
    spec: CalibrationCohortSpec,
) -> bool:
    return (
        cohort.cohort_id == cohort_id
        and cohort.spec == spec
        and cohort.spec_hash == calibration_cohort_spec_hash(spec)
    )


def _is_transient_control_error(error: Exception) -> bool:
    if isinstance(error, httpx2.TransportError):
        return True
    if isinstance(error, (DaemonClientError, httpx2.HTTPStatusError)):
        status = error.response.status_code
        return status == 503 or status >= 500 or status in _TRANSIENT_CLIENT_STATUSES
    return False


def _stopped(stop: Event | None) -> bool:
    return stop is not None and stop.is_set()


def _empty_cycle(*, has_more: bool = False) -> CalibrationEvaluatorCycleResult:
    return CalibrationEvaluatorCycleResult(
        definitions=0,
        selected_targets=0,
        fresh_members=0,
        pending_publication_members=0,
        blocked_members=0,
        suppressed_active_members=0,
        suppressed_failed_members=0,
        suppressed_attention_members=0,
        ready_members=0,
        admitted_members=0,
        created_cohorts=0,
        reconciled_cohorts=0,
        admission_conflicts=0,
        cohort_drifts=0,
        failures=0,
        has_more=has_more,
    )


__all__ = [
    "CalibrationEvaluatorCycleResult",
    "CalibrationPlanningContext",
    "CalibrationPlanningOperations",
    "ProjectCalibrationEvaluator",
    "calibration_cohort_id",
]
