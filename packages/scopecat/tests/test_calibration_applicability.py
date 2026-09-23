"""Check reuse explains exact-context changes without mutating parameters."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from scopecat.automation.calibration import (
    CheckEvidence,
    assess_calibration_check,
    assess_capability_dependencies,
    select_calibration_check,
)
from scopecat.daemon.calibration_checks import (
    CalibrationReportQuery,
    CalibrationRequirement,
)
from scopecat.kernel.content_identity import sha256_json_hash
from scopecat.kernel.frozen import freeze_json_mapping
from scopecat.kernel.run_outcome import RunOutcome
from scopecat.records.calibration_check import (
    CalibrationCheckRequest,
    CalibrationContext,
    CalibrationScope,
)
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


def test_capability_dependencies_propagate_without_changing_own_checks() -> None:
    result = assess_capability_dependencies(
        {"joint": ("gate",), "gate": ("readout",), "readout": (), "other": ()},
        {
            "joint": "usable",
            "gate": "out_of_spec",
            "readout": "recheck",
            "other": "usable",
        },
    )
    assert result["joint"].status == "blocked"
    assert result["joint"].blocked_by == ("gate",)
    assert result["gate"].blocked_by == ("readout",)
    assert result["readout"].status == "recheck"
    assert result["other"].status == "usable"


@pytest.mark.parametrize("dependencies", [("missing",), ("a",), ("b",), ("b", "b")])
def test_report_rejects_invalid_dependency_graph(
    observation: tuple[RunSnapshot, CalibrationContext],
    dependencies: tuple[str, ...],
) -> None:
    _, context = observation
    with pytest.raises(ValidationError):
        CalibrationReportQuery(
            context=context,
            requirements=(
                CalibrationRequirement(
                    id="a",
                    scope=SCOPE,
                    max_age=timedelta(hours=1),
                    depends_on=dependencies,
                ),
                CalibrationRequirement(
                    id="b", scope=SCOPE, max_age=timedelta(hours=1), depends_on=("a",)
                ),
            ),
        )


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


def later_run(measured: RunSnapshot, *, complete: bool = True) -> RunSnapshot:
    return measured.model_copy(
        update={
            "run_id": "check-2",
            "created_at": START + timedelta(minutes=1),
            "outcome": RunOutcome(
                run_id="check-2",
                result="succeeded",
                certainty="known",
                finished_at=START + timedelta(minutes=1),
            )
            if complete
            else None,
        }
    )


def test_check_request_decodes_without_author_code_after_intent_freezing(
    observation: tuple[RunSnapshot, CalibrationContext],
) -> None:
    _, context = observation
    request = CalibrationCheckRequest(
        scope=SCOPE,
        context=context,
        measurement_step="probe",
        analysis_step="judge",
    )
    frozen = freeze_json_mapping(request.model_dump(mode="json"), path="check")
    assert CalibrationCheckRequest.model_validate(frozen) == request


@pytest.mark.parametrize("reverse", [False, True])
def test_selection_never_falls_back_from_newer_negative_evidence(
    observation: tuple[RunSnapshot, CalibrationContext],
    reverse: bool,
) -> None:
    measured, current = observation
    old = CheckEvidence(measured, SCOPE, "old-pass", True)
    newer = CheckEvidence(
        later_run(measured),
        SCOPE,
        "new-fail",
        False,
    )
    evidence = (newer, old) if reverse else (old, newer)
    selected = select_calibration_check(
        evidence,
        requested_scope=SCOPE,
        current=current,
        now=START + timedelta(minutes=2),
        max_age=timedelta(hours=1),
        history_complete=True,
    )
    assert selected.status == "out_of_spec"
    assert selected.evidence == newer
    # Expiry cannot resurrect an even older positive observation.
    expired = select_calibration_check(
        evidence,
        requested_scope=SCOPE,
        current=current,
        now=START + timedelta(hours=2),
        max_age=timedelta(hours=1),
        history_complete=True,
    )
    assert expired.status == "recheck"
    assert expired.evidence == newer


def test_selection_blocks_incomplete_attempt_and_incomplete_query(
    observation: tuple[RunSnapshot, CalibrationContext],
) -> None:
    measured, current = observation
    old = CheckEvidence(measured, SCOPE, "old-pass", True)
    pending = CheckEvidence(
        later_run(measured, complete=False),
        SCOPE,
        None,
        None,
    )
    selected = select_calibration_check(
        (old, pending),
        requested_scope=SCOPE,
        current=current,
        now=START + timedelta(minutes=2),
        max_age=timedelta(hours=1),
        history_complete=True,
    )
    assert selected.status == "unknown"
    assert selected.evidence == pending
    assert selected.assessment is not None
    assert set(selected.assessment.reasons) == {
        "analysis_missing",
        "measurement_incomplete",
    }
    truncated = select_calibration_check(
        (old,),
        requested_scope=SCOPE,
        current=current,
        now=START,
        max_age=timedelta(hours=1),
        history_complete=False,
    )
    assert truncated.reason == "incomplete_history"
    assert truncated.status == "unknown"


def test_selection_filters_context_but_does_not_choose_between_reanalyses(
    observation: tuple[RunSnapshot, CalibrationContext],
) -> None:
    measured, current = observation
    old = CheckEvidence(measured, SCOPE, "first-analysis", True)
    other = CheckEvidence(
        later_run(measured),
        replace(SCOPE, targets=("q2",)),
        "other-target",
        False,
    )
    selected = select_calibration_check(
        (old, other),
        requested_scope=SCOPE,
        current=current,
        now=START + timedelta(minutes=2),
        max_age=timedelta(hours=1),
        history_complete=True,
    )
    assert selected.evidence == old
    assert selected.status == "usable"
    ambiguous = select_calibration_check(
        (old, replace(old, analysis_record_id="reanalysis", passed=False)),
        requested_scope=SCOPE,
        current=current,
        now=START + timedelta(minutes=2),
        max_age=timedelta(hours=1),
        history_complete=True,
    )
    assert ambiguous.status == "unknown"
    assert ambiguous.reason == "ambiguous_latest"
    empty = select_calibration_check(
        (other,),
        requested_scope=SCOPE,
        current=current,
        now=START + timedelta(minutes=2),
        max_age=timedelta(hours=1),
        history_complete=True,
    )
    assert empty.reason == "no_matching_evidence"
