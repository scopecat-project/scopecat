"""Check reuse explains exact-context changes without mutating parameters."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from scopecat.api.calibration_report import CalibrationReportView
from scopecat.automation.calibration import (
    CapabilityAvailability,
    CheckEvidence,
    CheckSelection,
    assess_calibration_check,
    assess_capability_dependencies,
    select_calibration_check,
)
from scopecat.daemon.calibration_checks import (
    CalibrationReportQuery,
    CalibrationRequirementStatus,
)
from scopecat.kernel.content_identity import sha256_json_hash
from scopecat.kernel.frozen import freeze_json_mapping
from scopecat.kernel.run_outcome import RunOutcome
from scopecat.records.calibration_check import CalibrationCheckRequest, CalibrationScope
from scopecat.records.calibration_policy import CalibrationRequirement
from scopecat.records.execution_scenario import SoftwareExecutionScenario
from scopecat.records.measurement_context import MeasurementContext
from scopecat.records.parameter_revision import ParameterRevisionRef
from scopecat.records.parameter_update import DeleteParameterRows
from scopecat.records.run import (
    AnalysisCandidateRunConfigSource,
    ParameterRunConfigSource,
    RunSnapshot,
)
from scopecat.records.sample import SampleBinding
from scopecat.records.scientific_binding import (
    EntityProjection,
    InlineSamplesSubject,
    RegisteredTargetSubject,
    ResolvedScientificBinding,
    TargetSetupBinding,
    UnboundSubject,
)
from scopecat.records.scientific_scope import (
    MeasurementTarget,
    TargetEntity,
    TargetMember,
)
from scopecat.records.setup import SetupRevisionRef
from scopecat.records.target_catalog import TargetRevisionRef

START = datetime(2026, 9, 23, tzinfo=UTC)
SCOPE = CalibrationScope("drive", ("q0", "q1"), "idle-v1", "residual-v1")


def test_notebook_report_escapes_lab_text_and_preserves_typed_data(
    observation: tuple[RunSnapshot, MeasurementContext],
) -> None:
    _, context = observation
    item = CalibrationRequirementStatus(
        requirement=CalibrationRequirement(
            id="<script>lab</script>",
            scope=SCOPE,
            max_age=timedelta(hours=1),
            depends_on=("<readout>",),
        ),
        selection=CheckSelection("unknown", "incomplete_history"),
        availability=CapabilityAvailability("blocked", ("<readout>",)),
        scanned=2,
        unresolved_procedures=("<execution>",),
        incomplete_reasons=("unresolved_checks",),
    )
    view = CalibrationReportView(context=context, observed_at=START, items=(item,))
    rendered = view._repr_html_()
    assert "<script>" not in rendered and "&lt;script&gt;" in rendered
    assert "&lt;readout&gt;" in rendered and "&lt;execution&gt;" in rendered
    assert "unknown" in rendered and "blocked" in rendered
    assert START.isoformat() in rendered
    assert view.items[0].selection.status == "unknown"
    assert view.context == context
    assert "blocked" in repr(view)


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
    observation: tuple[RunSnapshot, MeasurementContext],
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
def observation() -> tuple[RunSnapshot, MeasurementContext]:
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
        MeasurementContext(parameters, binding.subject, identity, scenario),
    )


@pytest.mark.parametrize("passed, status", [(True, "usable"), (False, "out_of_spec")])
def test_applicable_negative_check_is_not_execution_failure(
    observation: tuple[RunSnapshot, MeasurementContext], passed: bool, status: str
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


def test_run_context_uses_retained_inputs_and_excludes_unsaved_parameters(
    observation: tuple[RunSnapshot, MeasurementContext],
) -> None:
    measured, current = observation
    assert measured.measurement_context == current
    assert (
        RunSnapshot.model_validate_json(measured.model_dump_json()).measurement_context
        == current
    )
    assert "measurement_context" not in measured.model_dump()
    source = measured.config_source
    assert isinstance(source, ParameterRunConfigSource)
    for unsaved in (
        None,
        source.model_copy(
            update={
                "overrides": (
                    DeleteParameterRows(parameter_id="qubits", key={"id": "q0"}),
                )
            }
        ),
        AnalysisCandidateRunConfigSource(
            source_run_id="prior",
            analysis_record_id="analysis",
            proposal_id="proposal",
            base_config_content_hash=source.content_hash,
            content_hash=source.content_hash,
        ),
    ):
        changed = measured.model_copy(update={"config_source": unsaved})
        assert changed.measurement_context is None


def test_all_recheck_reasons_are_retained_and_target_order_matters(
    observation: tuple[RunSnapshot, MeasurementContext],
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


def test_changed_mapping_is_not_reused_even_when_subject_is_unchanged(
    observation: tuple[RunSnapshot, MeasurementContext],
) -> None:
    measured, current = observation
    mapping = TargetSetupBinding(
        target=TargetRevisionRef(
            catalog_id="lab",
            target_id="chip",
            revision=1,
            content_hash=sha256_json_hash("chip"),
        ),
        setup_content_hash=current.setup_content_hash,
        entities=(
            EntityProjection(
                target_entity=TargetEntity(member_id="device", entity_id="q0"),
                runtime_entity_id="left",
            ),
        ),
        connections=(),
    )
    sample = SampleBinding(
        role="subject",
        sample_id="chip",
        revision=1,
        content_hash=sha256_json_hash("sample"),
        kind="chip",
        display_name="Chip",
    )
    subject = RegisteredTargetSubject(
        ref=mapping.target,
        content=MeasurementTarget(
            members=(
                TargetMember(
                    id="device",
                    sample_id=sample.sample_id,
                    revision=sample.revision,
                    content_hash=sample.content_hash,
                ),
            )
        ),
        sample=sample,
    )
    measured = measured.model_copy(
        update={
            "scientific_binding": measured.scientific_binding.model_copy(
                update={
                    "subject": subject,
                    "target_binding": mapping,
                }
            ),
        }
    )
    changed_mapping = mapping.model_copy(
        update={
            "entities": (
                mapping.entities[0].model_copy(update={"runtime_entity_id": "right"}),
            )
        }
    )
    changed = replace(current, subject=subject, target_binding=changed_mapping)
    assert measured.measurement_context == replace(
        current, subject=subject, target_binding=mapping
    )
    result = assess_calibration_check(
        measured,
        checked_scope=SCOPE,
        requested_scope=SCOPE,
        passed=True,
        current=changed,
        now=START + timedelta(minutes=1),
        max_age=timedelta(hours=1),
    )
    assert result.status == "recheck"
    assert result.reasons == ("target_binding_changed",)
    selection = select_calibration_check(
        (CheckEvidence(measured, SCOPE, "analysis", True),),
        requested_scope=SCOPE,
        current=changed,
        now=START + timedelta(minutes=1),
        max_age=timedelta(hours=1),
        history_complete=True,
    )
    assert selection.reason == "no_matching_evidence"


def test_missing_evidence_does_not_grant_readiness(
    observation: tuple[RunSnapshot, MeasurementContext],
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
    observation: tuple[RunSnapshot, MeasurementContext],
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
    observation: tuple[RunSnapshot, MeasurementContext],
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
    observation: tuple[RunSnapshot, MeasurementContext],
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
    observation: tuple[RunSnapshot, MeasurementContext],
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
    observation: tuple[RunSnapshot, MeasurementContext],
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
    observation: tuple[RunSnapshot, MeasurementContext],
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
    observation: tuple[RunSnapshot, MeasurementContext],
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
