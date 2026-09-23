"""Advisory applicability of check-only evidence within one laboratory catalog.

This is a pure planning input, not publication authorization. Callers read the
scope and scientific result from the retained analysis of the supplied run.
No row-level independence or automatic physical dependency inference is claimed.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from scopecat.records.calibration_check import CalibrationContext, CalibrationScope
from scopecat.records.run import ParameterRunConfigSource, RunSnapshot
from scopecat.records.scientific_binding import UnboundSubject

type CheckReason = Literal[
    "analysis_missing",
    "measurement_incomplete",
    "parameters_unsaved",
    "subject_unbound",
    "capability_changed",
    "targets_changed",
    "conditions_changed",
    "policy_changed",
    "parameters_changed",
    "subject_changed",
    "setup_changed",
    "scenario_changed",
    "evidence_from_future",
    "check_expired",
    "within_spec",
    "out_of_spec",
]

type CheckStatus = Literal["usable", "out_of_spec", "recheck", "unknown"]


@dataclass(frozen=True)
class CheckAssessment:
    run_id: str
    status: CheckStatus
    reasons: tuple[CheckReason, ...]


def assess_calibration_check(
    measurement: RunSnapshot,
    *,
    checked_scope: CalibrationScope,
    requested_scope: CalibrationScope,
    passed: bool | None,
    current: CalibrationContext,
    now: datetime,
    max_age: timedelta,
) -> CheckAssessment:
    """Explain reuse under an explicit exact-input and maximum-age policy.

    Use the run's creation time conservatively, never analysis publication
    time: reanalysis does not refresh observations. Negative but applicable checks
    are out-of-spec; stale negative checks require rechecking. Unknown/incomplete
    evidence never grants readiness. No state is changed by this function.
    """
    if now.utcoffset() is None or measurement.created_at.utcoffset() is None:
        raise ValueError("check assessment requires timezone-aware times")
    if max_age <= timedelta(0):
        raise ValueError("check maximum age must be positive")

    unknown: list[CheckReason] = []
    changed: list[CheckReason] = []
    if passed is None:
        unknown.append("analysis_missing")
    outcome = measurement.outcome
    if outcome is None or outcome.result != "succeeded":
        unknown.append("measurement_incomplete")
    source = measurement.config_source
    if not isinstance(source, ParameterRunConfigSource) or source.overrides:
        unknown.append("parameters_unsaved")
    elif source.parameters != current.parameters:
        changed.append("parameters_changed")
    binding = measurement.scientific_binding
    if (isinstance(binding.subject, UnboundSubject) and binding.scenario is None) or (
        isinstance(current.subject, UnboundSubject) and current.scenario is None
    ):
        unknown.append("subject_unbound")
    comparisons: tuple[tuple[object, object, CheckReason], ...] = (
        (checked_scope.capability, requested_scope.capability, "capability_changed"),
        (checked_scope.targets, requested_scope.targets, "targets_changed"),
        (checked_scope.conditions, requested_scope.conditions, "conditions_changed"),
        (
            checked_scope.policy_version,
            requested_scope.policy_version,
            "policy_changed",
        ),
        (binding.subject, current.subject, "subject_changed"),
        (binding.setup_content_hash, current.setup_content_hash, "setup_changed"),
        (binding.scenario, current.scenario, "scenario_changed"),
    )
    for before, after, reason in comparisons:
        if before != after:
            changed.append(reason)
    age = now - measurement.created_at
    if age < timedelta(0):
        unknown.append("evidence_from_future")
    elif age >= max_age:
        changed.append("check_expired")
    if unknown:
        return CheckAssessment(measurement.run_id, "unknown", tuple(unknown + changed))
    if changed:
        return CheckAssessment(measurement.run_id, "recheck", tuple(changed))
    return CheckAssessment(
        measurement.run_id,
        "usable" if passed else "out_of_spec",
        ("within_spec" if passed else "out_of_spec",),
    )


@dataclass(frozen=True)
class CheckEvidence:
    """Read projection of a retained check, including attempts without a result.

    Scope comes from the analysis (or the original check request if unfinished).
    The caller resolves the analysis belonging to this measurement. References
    are retained for inspection, not authenticated by this pure selector.
    """

    measurement: RunSnapshot
    scope: CalibrationScope
    analysis_record_id: str | None
    passed: bool | None


@dataclass(frozen=True)
class CheckSelection:
    status: CheckStatus
    reason: Literal[
        "latest_matching",
        "no_matching_evidence",
        "ambiguous_latest",
        "incomplete_history",
    ]
    evidence: CheckEvidence | None = None
    assessment: CheckAssessment | None = None


_CONTEXT_CHANGES: frozenset[CheckReason] = frozenset(
    {
        "capability_changed",
        "targets_changed",
        "conditions_changed",
        "policy_changed",
        "parameters_changed",
        "subject_changed",
        "setup_changed",
        "scenario_changed",
    }
)


def select_calibration_check(
    evidence: tuple[CheckEvidence, ...],
    *,
    requested_scope: CalibrationScope,
    current: CalibrationContext,
    now: datetime,
    max_age: timedelta,
    history_complete: bool,
) -> CheckSelection:
    """Select by run creation time, never by success or analysis publication time.

    The supplied history must include unsuccessful/unfinished relevant attempts,
    not just published positive results. A truncated or otherwise incomplete query
    cannot establish readiness. Distinct equally recent records are ambiguous;
    callers must resolve their authority rather than using incidental ID order.
    """
    if now.utcoffset() is None or max_age <= timedelta(0):
        raise ValueError("selection requires an aware time and positive maximum age")
    if not history_complete:
        return CheckSelection("unknown", "incomplete_history")
    matching: list[tuple[CheckEvidence, CheckAssessment]] = []
    for item in evidence:
        assessment = assess_calibration_check(
            item.measurement,
            checked_scope=item.scope,
            requested_scope=requested_scope,
            passed=item.passed if item.analysis_record_id is not None else None,
            current=current,
            now=now,
            max_age=max_age,
        )
        if not _CONTEXT_CHANGES.intersection(assessment.reasons):
            matching.append((item, assessment))
    if not matching:
        return CheckSelection("unknown", "no_matching_evidence")
    latest_time = max(item.measurement.created_at for item, _ in matching)
    latest = [
        pair for pair in matching if pair[0].measurement.created_at == latest_time
    ]
    selected, assessment = latest[0]
    if any(item != selected for item, _ in latest[1:]):
        return CheckSelection("unknown", "ambiguous_latest")
    return CheckSelection(assessment.status, "latest_matching", selected, assessment)
