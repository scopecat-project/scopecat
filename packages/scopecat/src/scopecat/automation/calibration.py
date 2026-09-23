"""Advisory applicability of check-only evidence within one laboratory catalog.

This is a pure planning input, not publication authorization. Callers read the
scope and scientific result from the retained analysis of the supplied run.
No row-level independence or automatic physical dependency inference is claimed.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from scopecat.records.content import Sha256ContentHash
from scopecat.records.execution_scenario import SoftwareExecutionScenario
from scopecat.records.parameter_revision import ParameterRevisionRef
from scopecat.records.run import ParameterRunConfigSource, RunSnapshot
from scopecat.records.scientific_binding import ResolvedSubject, UnboundSubject


@dataclass(frozen=True)
class CalibrationScope:
    """Laboratory-defined capability, ordered target addresses and check contract.

    Targets are addresses within the resolved subject, not global qubit names.
    Order matters for directional operations. Conditions name a laboratory-owned
    operating condition contract; change it when relevant unmodeled conditions
    change. Policy versions cover thresholds, analysis and measurement semantics.
    """

    capability: str
    targets: tuple[str, ...]
    conditions: str
    policy_version: str


@dataclass(frozen=True)
class CalibrationContext:
    """Exact saved inputs for a requested check, independent of branch names.

    The caller supplies the requested subject/scenario, not those of whichever
    historical check happens to be available. This initial contract deliberately
    requires the same parameter revision until selective dependencies exist.
    """

    parameters: ParameterRevisionRef
    subject: ResolvedSubject
    setup_content_hash: Sha256ContentHash
    scenario: SoftwareExecutionScenario | None


type CheckReason = Literal[
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


@dataclass(frozen=True)
class CheckAssessment:
    run_id: str
    status: Literal["usable", "out_of_spec", "recheck", "unknown"]
    reasons: tuple[CheckReason, ...]


def assess_calibration_check(
    measurement: RunSnapshot,
    *,
    checked_scope: CalibrationScope,
    requested_scope: CalibrationScope,
    passed: bool,
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
