"""Check reuse explains exact-context changes without mutating parameters."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from scopecat.automation.calibration import (
    CalibrationContext,
    CalibrationScope,
    assess_calibration_check,
)
from scopecat.kernel.content_identity import sha256_json_hash
from scopecat.kernel.run_outcome import RunOutcome
from scopecat.records.execution_scenario import SoftwareExecutionScenario
from scopecat.records.parameter_revision import ParameterRevisionRef
from scopecat.records.run import ParameterRunConfigSource, RunSnapshot
from scopecat.records.sample import SampleBinding
from scopecat.records.scientific_binding import (
    InlineSamplesSubject,
    ResolvedScientificBinding,
    UnboundSubject,
)
from scopecat.records.setup import SetupRevisionRef

START = datetime(2026, 9, 23, tzinfo=UTC)
SCOPE = CalibrationScope("drive", ("q0", "q1"), "idle-v1", "residual-v1")


@pytest.fixture
def observation() -> tuple[RunSnapshot, CalibrationContext]:
    identity = sha256_json_hash("saved")
    parameters = ParameterRevisionRef(revision_id="daily-1", content_hash=identity)
    scenario = SoftwareExecutionScenario(
        id="synthetic",
        label="Synthetic",
        model_id="residual",
        model_version="1",
        capabilities=("residual",),
    )
    binding = ResolvedScientificBinding(
        subject=UnboundSubject(),
        scenario=scenario,
        config_content_hash=identity,
        setup_content_hash=identity,
    )
    return (
        RunSnapshot(
            run_id="check-1",
            created_at=START,
            scientific_binding=binding,
            config_content_hash=identity,
            config_source=ParameterRunConfigSource(
                parameters=parameters,
                setup=SetupRevisionRef(revision_id="setup-1", content_hash=identity),
                content_hash=identity,
            ),
            outcome=RunOutcome(run_id="check-1", result="succeeded", certainty="known"),
        ),
        CalibrationContext(parameters, binding.subject, identity, scenario),
    )


@pytest.mark.parametrize("passed, status", [(True, "usable"), (False, "out_of_spec")])
def test_applicable_negative_check_is_not_execution_failure(
    observation: tuple[RunSnapshot, CalibrationContext], passed: bool, status: str
) -> None:
    measured, current = observation
    result = assess_calibration_check(
        measured,
        checked_scope=SCOPE,
        requested_scope=SCOPE,
        passed=passed,
        current=current,
        now=START + timedelta(minutes=1),
        max_age=timedelta(hours=1),
    )
    assert result.status == status
    assert result.run_id == measured.run_id


def test_all_recheck_reasons_are_retained_and_target_order_matters(
    observation: tuple[RunSnapshot, CalibrationContext],
) -> None:
    measured, current = observation
    assert current.scenario is not None
    requested = replace(
        SCOPE, targets=("q1", "q0"), conditions="driven-v1", policy_version="2"
    )
    changed = replace(
        current,
        parameters=ParameterRevisionRef(
            revision_id="daily-2", content_hash=sha256_json_hash("new")
        ),
        setup_content_hash=sha256_json_hash("rewired"),
        scenario=current.scenario.model_copy(update={"model_version": "2"}),
    )
    result = assess_calibration_check(
        measured,
        checked_scope=SCOPE,
        requested_scope=requested,
        passed=False,
        current=changed,
        now=START + timedelta(hours=1),
        max_age=timedelta(hours=1),
    )
    assert result.status == "recheck"
    assert set(result.reasons) == {
        "parameters_changed",
        "targets_changed",
        "conditions_changed",
        "policy_changed",
        "setup_changed",
        "scenario_changed",
        "check_expired",
    }


def test_missing_evidence_does_not_grant_readiness(
    observation: tuple[RunSnapshot, CalibrationContext],
) -> None:
    measured, current = observation
    measured = measured.model_copy(update={"config_source": None, "outcome": None})
    result = assess_calibration_check(
        measured,
        checked_scope=SCOPE,
        requested_scope=SCOPE,
        passed=True,
        current=current,
        now=START - timedelta(seconds=1),
        max_age=timedelta(hours=1),
    )
    assert result.status == "unknown"
    assert set(result.reasons) == {
        "parameters_unsaved",
        "measurement_incomplete",
        "evidence_from_future",
    }


def test_unbound_real_execution_does_not_inherit_software_check(
    observation: tuple[RunSnapshot, CalibrationContext],
) -> None:
    measured, current = observation
    result = assess_calibration_check(
        measured,
        checked_scope=SCOPE,
        requested_scope=SCOPE,
        passed=True,
        current=replace(current, scenario=None),
        now=START,
        max_age=timedelta(hours=1),
    )
    assert result.status == "unknown"
    assert set(result.reasons) == {"subject_unbound", "scenario_changed"}


def test_physical_evidence_does_not_cross_sample_contexts(
    observation: tuple[RunSnapshot, CalibrationContext],
) -> None:
    measured, current = observation
    sample = SampleBinding(
        role="subject",
        sample_id="chip",
        revision=1,
        content_hash=sha256_json_hash("chip"),
        kind="chip",
        display_name="Chip",
        context_id="cooldown-a",
    )
    subject = InlineSamplesSubject(catalog_id="lab", samples=(sample,))
    measured = measured.model_copy(
        update={
            "samples": (sample,),
            "scientific_binding": measured.scientific_binding.model_copy(
                update={"subject": subject, "scenario": None}
            ),
        }
    )
    current = replace(current, subject=subject, scenario=None)
    result = assess_calibration_check(
        measured,
        checked_scope=SCOPE,
        requested_scope=SCOPE,
        passed=True,
        current=current,
        now=START,
        max_age=timedelta(hours=1),
    )
    assert result.status == "usable"
    changed_subject = subject.model_copy(
        update={
            "samples": (sample.model_copy(update={"context_id": "cooldown-b"}),),
        }
    )
    result = assess_calibration_check(
        measured,
        checked_scope=SCOPE,
        requested_scope=SCOPE,
        passed=True,
        current=replace(current, subject=changed_subject),
        now=START,
        max_age=timedelta(hours=1),
    )
    assert result.status == "recheck"
    assert result.reasons == ("subject_changed",)


def test_age_policy_is_explicit(
    observation: tuple[RunSnapshot, CalibrationContext],
) -> None:
    measured, current = observation
    with pytest.raises(ValueError, match="positive"):
        assess_calibration_check(
            measured,
            checked_scope=SCOPE,
            requested_scope=SCOPE,
            passed=True,
            current=current,
            now=START,
            max_age=timedelta(0),
        )
