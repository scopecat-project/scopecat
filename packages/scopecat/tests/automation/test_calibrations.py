from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError
from scopecat_testkit.records import assert_model_round_trip

from scopecat.automation.calibrations import (
    CalibrationAttemptRef,
    CalibrationAttemptStatus,
    CalibrationCohort,
    CalibrationCohortFinalization,
    CalibrationCohortFinalizationState,
    CalibrationCohortMember,
    CalibrationCohortMemberSpec,
    CalibrationCohortSpec,
    CalibrationConfigSourceRef,
    CalibrationDefinitionChangedDueReason,
    CalibrationDefinitionRef,
    CalibrationDependencyChangedDueReason,
    CalibrationDependencyEvidence,
    CalibrationDueReason,
    CalibrationForcedDueReason,
    CalibrationInputsChangedDueReason,
    CalibrationMissingSuccessDueReason,
    CalibrationPublicationAttention,
    CalibrationPublicationBaseChangedDueReason,
    CalibrationPublicationCompletion,
    CalibrationPublicationFailure,
    CalibrationPublicationPolicyRef,
    CalibrationPublicationSupersession,
    CalibrationStatus,
    CalibrationStatusSnapshot,
    CalibrationSuccessPolicy,
    CalibrationSuccessPublication,
    CalibrationSuccessRef,
    CalibrationTargetRef,
    CalibrationWorkingPointSupersession,
    calibration_cohort_member_request_key,
    calibration_cohort_spec_hash,
    calibration_freshness_fingerprint,
    calibration_key,
    scoped_calibration_target,
)
from scopecat.automation.models import ProcedureClosure, ProcedureDefinitionRef
from scopecat.config.registry.records import ConfigCompositionPolicyRef
from scopecat.records.calibration_scope import (
    CatalogCalibrationScope,
    WorkingPointCalibrationScope,
)
from scopecat.records.run import ConfigRegistryRunConfigSource
from scopecat.records.sample import SampleBinding

_HASH_1 = "sha256:" + "1" * 64
_HASH_2 = "sha256:" + "2" * 64
_HASH_3 = "sha256:" + "3" * 64
_EVALUATED = datetime(2026, 8, 18, 8, tzinfo=UTC)
_CREATED = _EVALUATED + timedelta(seconds=1)


def _definition(
    *,
    definition_id: str = "drag",
    version: str = "2",
    success_policy: CalibrationSuccessPolicy = "procedure_success",
) -> CalibrationDefinitionRef:
    return CalibrationDefinitionRef(
        id=definition_id,
        version=version,
        fingerprint=_HASH_1,
        success_policy=success_policy,
    )


def _publication_policy(
    calibration: CalibrationDefinitionRef | None = None,
) -> CalibrationPublicationPolicyRef:
    selected_calibration = calibration or _definition(success_policy="published_result")
    return CalibrationPublicationPolicyRef(
        id="automatic-calibration-merge",
        version="3",
        fingerprint=_HASH_2,
        calibration=selected_calibration,
        composition_policy=ConfigCompositionPolicyRef(
            id="merge-calibration-results",
            version="5",
            fingerprint=_HASH_3,
        ),
    )


def _target(*, target_id: str = "q0") -> CalibrationTargetRef:
    return scoped_calibration_target(
        CalibrationTargetRef(kind="qubit", id=target_id), _frozen_config_source()
    )


def _procedure(*, version: str = "4") -> ProcedureDefinitionRef:
    return ProcedureDefinitionRef(
        id="calibrate-drag",
        version=version,
        fingerprint=_HASH_2,
    )


def _config_source() -> ConfigRegistryRunConfigSource:
    return ConfigRegistryRunConfigSource(
        selector="active",
        entry_id="config-17",
        config_ref="configs/17",
        content_hash=_HASH_3,
        registry_generation=23,
    )


def _frozen_config_source() -> CalibrationConfigSourceRef:
    return CalibrationConfigSourceRef.from_run_config_source(
        _config_source()
    ).model_copy(
        update={
            "scope": WorkingPointCalibrationScope(
                workspace_id="parked",
                sample=SampleBinding(
                    role="subject",
                    sample_id="chip",
                    revision=1,
                    content_hash=_HASH_1,
                    kind="synthetic",
                    display_name="Chip",
                    context_id="parked",
                ),
            ),
        }
    )


def test_catalog_config_source_retains_exact_entry_without_activation_ownership() -> (
    None
):
    source = _config_source()
    frozen = CalibrationConfigSourceRef.from_run_config_source(source)
    assert isinstance(frozen.scope, CatalogCalibrationScope)
    assert frozen.entry_id == source.entry_id
    assert frozen == CalibrationConfigSourceRef.from_run_config_source(
        source.model_copy(update={"registry_generation": None, "selector": "entry"})
    )


def _success(
    *,
    definition: CalibrationDefinitionRef | None = None,
    target: CalibrationTargetRef | None = None,
    cohort_id: str = "cohort-prior",
    member_id: str = "member-prior",
    procedure_run_id: str = "procedure-prior",
    dependencies: tuple[CalibrationDependencyEvidence, ...] = (),
    base_config_source: CalibrationConfigSourceRef | None = None,
) -> tuple[CalibrationSuccessRef, CalibrationStatus]:
    selected_definition = definition or _definition()
    selected_target = target or _target()
    procedure = _procedure()
    input_fingerprint = _HASH_3
    freshness = calibration_freshness_fingerprint(
        definition=selected_definition,
        target=selected_target,
        procedure=procedure,
        input_fingerprint=input_fingerprint,
        dependencies=dependencies,
    )
    admitted_at = _EVALUATED - timedelta(hours=2)
    succeeded_at = _EVALUATED - timedelta(hours=1)
    attempt = CalibrationAttemptRef(
        calibration_key=calibration_key(selected_definition.id, selected_target),
        cohort_id=cohort_id,
        member_id=member_id,
        procedure_run_id=procedure_run_id,
        definition=selected_definition,
        target=selected_target,
        procedure=procedure,
        input_fingerprint=input_fingerprint,
        dependencies=dependencies,
        freshness_fingerprint=freshness,
        admitted_at=admitted_at,
    )
    success = CalibrationSuccessRef(
        attempt=attempt,
        base_config_source=base_config_source or _frozen_config_source(),
        succeeded_at=succeeded_at,
    )
    status = CalibrationStatus(
        calibration_key=attempt.calibration_key,
        latest_attempt=CalibrationAttemptStatus(
            attempt=attempt,
            procedure_state="closed",
            procedure_revision=7,
            updated_at=succeeded_at,
            closure=ProcedureClosure(
                status="succeeded",
                closed_at=succeeded_at,
            ),
        ),
        latest_success=success,
    )
    return success, status


def _published_success(
    pending: CalibrationSuccessRef,
    *,
    result_input_fingerprint: str = _HASH_2,
    published_at: datetime | None = None,
) -> CalibrationSuccessRef:
    result_source = pending.base_config_source.model_copy(
        update={
            "entry_id": "config-18",
            "config_ref": "configs/18",
            "content_hash": _HASH_2,
        }
    )
    publication = CalibrationSuccessPublication(
        operation_id="publish-calibration-result",
        source_intent_hash=_HASH_1,
        result_input_fingerprint=result_input_fingerprint,
        result_freshness_fingerprint=calibration_freshness_fingerprint(
            definition=pending.attempt.definition,
            target=pending.attempt.target,
            procedure=pending.attempt.procedure,
            input_fingerprint=result_input_fingerprint,
            dependencies=pending.attempt.dependencies,
        ),
        result_config_source=result_source,
        published_at=published_at or _EVALUATED - timedelta(minutes=30),
    )
    return CalibrationSuccessRef(
        attempt=pending.attempt,
        base_config_source=pending.base_config_source,
        succeeded_at=pending.succeeded_at,
        publication=publication,
    )


def _missing_status(
    *,
    definition: CalibrationDefinitionRef | None = None,
    target: CalibrationTargetRef | None = None,
) -> CalibrationStatus:
    selected_definition = definition or _definition()
    selected_target = target or _target()
    return CalibrationStatus(
        calibration_key=calibration_key(selected_definition.id, selected_target)
    )


def _member_spec(
    *,
    definition: CalibrationDefinitionRef | None = None,
    target: CalibrationTargetRef | None = None,
    procedure: ProcedureDefinitionRef | None = None,
    input_fingerprint: str = _HASH_3,
    dependencies: tuple[CalibrationDependencyEvidence, ...] = (),
    due_reasons: tuple[CalibrationDueReason, ...] | None = None,
) -> CalibrationCohortMemberSpec:
    selected_definition = definition or _definition()
    selected_target = target or _target()
    selected_procedure = procedure or _procedure()
    freshness = calibration_freshness_fingerprint(
        definition=selected_definition,
        target=selected_target,
        procedure=selected_procedure,
        input_fingerprint=input_fingerprint,
        dependencies=dependencies,
    )
    return CalibrationCohortMemberSpec(
        member_id=f"member-{selected_target.id}",
        calibration_key=calibration_key(selected_definition.id, selected_target),
        definition=selected_definition,
        target=selected_target,
        procedure=selected_procedure,
        intent={"target_id": selected_target.id},
        input_fingerprint=input_fingerprint,
        dependencies=dependencies,
        freshness_fingerprint=freshness,
        due_reasons=due_reasons or (CalibrationMissingSuccessDueReason(),),
    )


def _cohort_spec(
    *,
    member: CalibrationCohortMemberSpec | None = None,
    observations: tuple[CalibrationStatus, ...] | None = None,
    max_in_flight: int = 2,
    observed_active: int = 0,
    automatic_publication: CalibrationPublicationPolicyRef | None = None,
) -> CalibrationCohortSpec:
    selected_member = member or _member_spec()
    selected_observations = (
        observations
        if observations is not None
        else (
            _missing_status(
                definition=selected_member.definition,
                target=selected_member.target,
            ),
        )
    )
    return CalibrationCohortSpec(
        definition=selected_member.definition,
        automatic_publication=automatic_publication,
        config_source=_frozen_config_source(),
        fanout_scope="chip-alpha",
        max_in_flight=max_in_flight,
        observed_fanout_active_count=observed_active,
        evaluated_at=_EVALUATED,
        observations=selected_observations,
        members=(selected_member,),
    )


def _cohort() -> CalibrationCohort:
    spec = _cohort_spec()
    return CalibrationCohort(
        cohort_id="cohort-18",
        spec=spec,
        spec_hash=calibration_cohort_spec_hash(spec),
        created_at=_CREATED,
    )


def _finalization(
    *,
    policy: CalibrationPublicationPolicyRef,
    base_config_source: CalibrationConfigSourceRef,
    revision: int,
    state: CalibrationCohortFinalizationState,
    attempt_count: int,
    created_at: datetime,
    updated_at: datetime,
    ready_at: datetime | None = None,
    available_at: datetime | None = None,
    attention: CalibrationPublicationAttention | None = None,
    failure: CalibrationPublicationFailure | None = None,
    supersession: CalibrationPublicationSupersession | None = None,
    publication: CalibrationPublicationCompletion | None = None,
) -> CalibrationCohortFinalization:
    return CalibrationCohortFinalization(
        cohort_id="cohort-automatic",
        spec_hash=_HASH_1,
        policy=policy,
        base_config_source=base_config_source,
        revision=revision,
        state=state,
        attempt_count=attempt_count,
        created_at=created_at,
        updated_at=updated_at,
        ready_at=ready_at,
        available_at=available_at,
        attention=attention,
        failure=failure,
        supersession=supersession,
        publication=publication,
    )


def test_calibration_key_is_logical_across_version_and_procedure_changes() -> None:
    target = _target()

    assert calibration_key(_definition(version="1").id, target) == calibration_key(
        _definition(version="9").id,
        target,
    )
    assert calibration_key("drag", target) != calibration_key(
        "drag",
        _target(target_id="q1"),
    )


def test_calibration_key_isolated_by_sample_and_context() -> None:
    first = CalibrationTargetRef(
        kind="qubit", id="q0", sample_id="die-1", context_id="cooldown-1"
    )
    second = CalibrationTargetRef(
        kind="qubit", id="q0", sample_id="die-2", context_id="cooldown-1"
    )
    remounted = CalibrationTargetRef(
        kind="qubit",
        id="q0",
        sample_id="die-1",
        context_id="cooldown-2",
    )

    keys = {
        calibration_key("drag", first),
        calibration_key("drag", second),
        calibration_key("drag", remounted),
    }

    assert len(keys) == 3
    with pytest.raises(ValidationError, match="context requires a sample"):
        CalibrationTargetRef(kind="qubit", id="q0", context_id="cooldown-1")


def test_attempt_carries_flat_dependency_evidence_and_exact_freshness() -> None:
    dependency_success, _ = _success(
        definition=_definition(definition_id="readout"),
        target=_target(),
    )
    dependency = dependency_success.dependency_evidence
    success, _ = _success(dependencies=(dependency,))

    assert success.attempt.dependencies == (dependency,)
    assert "attempt" not in dependency.model_dump(mode="json")
    assert assert_model_round_trip(success) == success

    invalid = success.attempt.model_dump()
    invalid["input_fingerprint"] = _HASH_2
    with pytest.raises(ValidationError, match="freshness"):
        CalibrationAttemptRef.model_validate(invalid)


def test_published_result_is_pending_until_exact_publication_is_attached() -> None:
    pending, status = _success(
        definition=_definition(success_policy="published_result"),
    )

    assert pending.is_effective is False
    with pytest.raises(ValueError, match="pending calibration publication"):
        _ = pending.dependency_evidence
    with pytest.raises(ValueError, match="no effective freshness"):
        _ = pending.effective_freshness_fingerprint
    with pytest.raises(ValueError, match="no effective inputs"):
        _ = pending.effective_input_fingerprint
    with pytest.raises(ValueError, match="no effective config source"):
        _ = pending.effective_config_source

    published = _published_success(pending)
    anchored_status = CalibrationStatus(
        calibration_key=status.calibration_key,
        latest_attempt=status.latest_attempt,
        latest_success=published,
    )

    assert published.is_effective is True
    assert published.publication is not None
    assert (
        published.effective_config_source == published.publication.result_config_source
    )
    assert published.effective_input_fingerprint == _HASH_2
    assert published.dependency_evidence.freshness_fingerprint == (
        published.effective_freshness_fingerprint
    )
    assert (
        published.dependency_evidence.publication_operation_id
        == "publish-calibration-result"
    )
    assert assert_model_round_trip(anchored_status) == anchored_status


def test_success_publication_validates_policy_generation_and_freshness() -> None:
    procedure_success, _ = _success()
    published_result, _ = _success(
        definition=_definition(success_policy="published_result"),
    )
    valid_publication = _published_success(published_result).publication
    assert valid_publication is not None

    with pytest.raises(ValidationError, match="procedure-success"):
        CalibrationSuccessRef(
            attempt=procedure_success.attempt,
            base_config_source=procedure_success.base_config_source,
            succeeded_at=procedure_success.succeeded_at,
            publication=valid_publication,
        )

    with pytest.raises(ValidationError, match="same working point"):
        CalibrationSuccessRef(
            attempt=published_result.attempt,
            base_config_source=published_result.base_config_source,
            succeeded_at=published_result.succeeded_at,
            publication=valid_publication.model_copy(
                update={
                    "result_config_source": (
                        valid_publication.result_config_source.model_copy(
                            update={"scope": CatalogCalibrationScope()}
                        )
                    )
                }
            ),
        )

    with pytest.raises(ValidationError, match="result inputs"):
        CalibrationSuccessRef(
            attempt=published_result.attempt,
            base_config_source=published_result.base_config_source,
            succeeded_at=published_result.succeeded_at,
            publication=valid_publication.model_copy(
                update={"result_freshness_fingerprint": _HASH_3}
            ),
        )


def test_dependency_evidence_is_a_canonical_order_independent_set() -> None:
    first, _ = _success(
        definition=_definition(definition_id="readout"),
        target=_target(target_id="q1"),
    )
    second, _ = _success(
        definition=_definition(definition_id="resonator"),
        target=_target(target_id="q2"),
    )
    unordered = (second.dependency_evidence, first.dependency_evidence)
    canonical = tuple(
        sorted(unordered, key=lambda dependency: dependency.calibration_key)
    )

    success, _ = _success(dependencies=unordered)
    member = _member_spec(dependencies=unordered)

    assert success.attempt.dependencies == canonical
    assert member.dependencies == canonical
    assert calibration_freshness_fingerprint(
        definition=_definition(),
        target=_target(),
        procedure=_procedure(),
        input_fingerprint=_HASH_3,
        dependencies=unordered,
    ) == calibration_freshness_fingerprint(
        definition=_definition(),
        target=_target(),
        procedure=_procedure(),
        input_fingerprint=_HASH_3,
        dependencies=tuple(reversed(unordered)),
    )


def test_status_snapshot_uses_one_server_clock_and_unique_keys() -> None:
    success, status = _success()
    offset = timezone(timedelta(hours=8))
    snapshot = CalibrationStatusSnapshot(
        observed_at=_EVALUATED.astimezone(offset),
        fanout_scope="chip-alpha",
        fanout_active_count=3,
        statuses=(status,),
    )

    assert snapshot.observed_at == _EVALUATED
    assert snapshot.statuses[0].latest_success == success
    assert assert_model_round_trip(snapshot) == snapshot
    with pytest.raises(ValidationError, match="unique"):
        CalibrationStatusSnapshot(
            observed_at=_EVALUATED,
            fanout_scope="chip-alpha",
            fanout_active_count=0,
            statuses=(status, status),
        )

    with pytest.raises(ValidationError, match="success cannot follow"):
        CalibrationStatusSnapshot(
            observed_at=success.succeeded_at - timedelta(seconds=1),
            fanout_scope="chip-alpha",
            fanout_active_count=0,
            statuses=(status,),
        )

    future_active = CalibrationStatus(
        calibration_key=status.calibration_key,
        latest_attempt=CalibrationAttemptStatus(
            attempt=success.attempt,
            procedure_state="ready",
            procedure_revision=8,
            updated_at=_EVALUATED + timedelta(seconds=1),
        ),
    )
    with pytest.raises(ValidationError, match="attempt update cannot follow"):
        CalibrationStatusSnapshot(
            observed_at=_EVALUATED,
            fanout_scope="chip-alpha",
            fanout_active_count=1,
            statuses=(future_active,),
        )


def test_cohort_spec_binds_observations_dependencies_and_capacity() -> None:
    dependency_success, dependency_status = _success(
        definition=_definition(definition_id="readout"),
    )
    member = _member_spec(dependencies=(dependency_success.dependency_evidence,))
    spec = _cohort_spec(
        member=member,
        observations=(_missing_status(), dependency_status),
        max_in_flight=4,
        observed_active=3,
    )

    assert assert_model_round_trip(spec) == spec

    wrong_success, wrong_status = _success(
        definition=_definition(definition_id="readout"),
        cohort_id="different-cohort",
        procedure_run_id="different-run",
    )
    assert wrong_success.dependency_evidence != dependency_success.dependency_evidence
    with pytest.raises(ValidationError, match="observed latest success"):
        _cohort_spec(
            member=member,
            observations=(_missing_status(), wrong_status),
        )

    with pytest.raises(ValidationError, match="fanout capacity"):
        _cohort_spec(max_in_flight=3, observed_active=3)


def test_cohort_spec_rejects_missing_observation_and_mixed_definition() -> None:
    member = _member_spec()
    with pytest.raises(ValidationError, match="cover every member"):
        _cohort_spec(member=member, observations=())

    other_definition = _definition(definition_id="readout")
    data = _cohort_spec().model_dump()
    data["definition"] = other_definition
    with pytest.raises(ValidationError, match="exact definition"):
        CalibrationCohortSpec.model_validate(data)


def test_automatic_publication_policy_pins_exact_published_result_meaning() -> None:
    definition = _definition(success_policy="published_result")
    policy = _publication_policy(definition)
    member = _member_spec(definition=definition)
    manual = _cohort_spec(member=member)
    automatic = _cohort_spec(
        member=member,
        automatic_publication=policy,
    )

    assert automatic.automatic_publication == policy
    assert assert_model_round_trip(policy) == policy
    assert calibration_cohort_spec_hash(automatic) != calibration_cohort_spec_hash(
        manual
    )

    with pytest.raises(ValidationError, match="published-result"):
        _publication_policy(_definition(success_policy="procedure_success"))

    mismatched = policy.model_copy(
        update={"calibration": definition.model_copy(update={"version": "other"})}
    )
    with pytest.raises(ValidationError, match="exact calibration definition"):
        _cohort_spec(member=member, automatic_publication=mismatched)


def test_publication_finalization_states_require_exact_conditional_audit() -> None:
    policy = _publication_policy()
    base = _frozen_config_source()
    created_at = _CREATED
    ready_at = created_at + timedelta(minutes=1)
    terminal_at = ready_at + timedelta(minutes=1)
    finalizations = (
        _finalization(
            policy=policy,
            base_config_source=base,
            revision=1,
            state="waiting",
            attempt_count=0,
            created_at=created_at,
            updated_at=created_at,
        ),
        _finalization(
            policy=policy,
            base_config_source=base,
            revision=2,
            state="ready",
            attempt_count=0,
            created_at=created_at,
            updated_at=ready_at,
            ready_at=ready_at,
            available_at=ready_at,
        ),
        _finalization(
            policy=policy,
            base_config_source=base,
            revision=3,
            state="attention_required",
            attempt_count=1,
            created_at=created_at,
            updated_at=terminal_at,
            ready_at=ready_at,
            attention=CalibrationPublicationAttention(
                actor="resident-worker",
                reason="policy proof is invalid",
                required_at=terminal_at,
            ),
        ),
        _finalization(
            policy=policy,
            base_config_source=base,
            revision=2,
            state="failed",
            attempt_count=0,
            created_at=created_at,
            updated_at=terminal_at,
            failure=CalibrationPublicationFailure(failed_at=terminal_at),
        ),
        _finalization(
            policy=policy,
            base_config_source=base,
            revision=3,
            state="superseded",
            attempt_count=1,
            created_at=created_at,
            updated_at=terminal_at,
            ready_at=ready_at,
            supersession=CalibrationPublicationSupersession(
                evidence=CalibrationWorkingPointSupersession(
                    superseded_by=base.model_copy(update={"entry_id": "next"})
                ),
                superseded_at=terminal_at,
            ),
        ),
        _finalization(
            policy=policy,
            base_config_source=base,
            revision=3,
            state="published",
            attempt_count=1,
            created_at=created_at,
            updated_at=terminal_at,
            ready_at=ready_at,
            publication=CalibrationPublicationCompletion(
                operation_id="publish-cohort-automatic",
                published_at=terminal_at,
            ),
        ),
    )

    assert {item.state for item in finalizations} == {
        "waiting",
        "ready",
        "attention_required",
        "failed",
        "superseded",
        "published",
    }
    for finalization in finalizations:
        assert assert_model_round_trip(finalization) == finalization

    ready = finalizations[1]
    with pytest.raises(ValidationError, match="availability"):
        CalibrationCohortFinalization.model_validate(
            {
                **ready.model_dump(),
                "available_at": ready.updated_at - timedelta(seconds=1),
            }
        )
    with pytest.raises(ValidationError, match="another head"):
        _finalization(
            policy=policy,
            base_config_source=base,
            revision=2,
            state="superseded",
            attempt_count=0,
            created_at=created_at,
            updated_at=terminal_at,
            supersession=CalibrationPublicationSupersession(
                evidence=CalibrationWorkingPointSupersession(superseded_by=base),
                superseded_at=terminal_at,
            ),
        )
    with pytest.raises(ValidationError, match="incompatible audit detail"):
        CalibrationCohortFinalization.model_validate(
            {
                **ready.model_dump(),
                "attention": CalibrationPublicationAttention(
                    actor="resident-worker",
                    reason="invalid",
                    required_at=ready.updated_at,
                ),
            }
        )


def test_due_reasons_are_typed_and_match_observed_evidence() -> None:
    previous, previous_status = _success(
        definition=_definition(version="1"),
    )
    definition = _definition(version="2")
    target = _target()
    procedure = _procedure()
    freshness = calibration_freshness_fingerprint(
        definition=definition,
        target=target,
        procedure=procedure,
        input_fingerprint=_HASH_3,
        dependencies=(),
    )
    member = CalibrationCohortMemberSpec(
        member_id="member-q0",
        calibration_key=calibration_key(definition.id, target),
        definition=definition,
        target=target,
        procedure=procedure,
        intent={"target_id": "q0"},
        input_fingerprint=_HASH_3,
        dependencies=(),
        freshness_fingerprint=freshness,
        due_reasons=(CalibrationDefinitionChangedDueReason(previous_success=previous),),
    )
    spec = _cohort_spec(member=member, observations=(previous_status,))

    assert spec.members[0].due_reasons[0].kind == "definition_changed"


def test_cohort_rejects_false_definition_and_input_change_claims() -> None:
    previous, previous_status = _success()

    false_definition = _member_spec(
        due_reasons=(CalibrationDefinitionChangedDueReason(previous_success=previous),)
    )
    with pytest.raises(ValidationError, match="prior definition or procedure"):
        _cohort_spec(member=false_definition, observations=(previous_status,))

    false_inputs = _member_spec(
        due_reasons=(CalibrationInputsChangedDueReason(previous_success=previous),)
    )
    with pytest.raises(ValidationError, match="prior input change"):
        _cohort_spec(member=false_inputs, observations=(previous_status,))


def test_publication_base_change_reason_binds_pending_success_and_cohort_source() -> (
    None
):
    pending, pending_status = _success(
        definition=_definition(success_policy="published_result"),
    )
    current_source = pending.base_config_source.model_copy(
        update={
            "entry_id": "config-current",
            "config_ref": "configs/current",
        }
    )
    reason = CalibrationPublicationBaseChangedDueReason(
        previous_success=pending,
        current_config_source=current_source,
    )
    member = _member_spec(
        definition=pending.attempt.definition,
        due_reasons=(reason,),
    )
    spec = CalibrationCohortSpec(
        definition=member.definition,
        config_source=current_source,
        fanout_scope="chip-alpha",
        max_in_flight=2,
        observed_fanout_active_count=0,
        evaluated_at=_EVALUATED,
        observations=(pending_status,),
        members=(member,),
    )

    assert spec.members[0].due_reasons == (reason,)

    with pytest.raises(ValidationError, match="cohort config source"):
        CalibrationCohortSpec(
            definition=member.definition,
            config_source=current_source.model_copy(
                update={"entry_id": "other-current-config"}
            ),
            fanout_scope="chip-alpha",
            max_in_flight=2,
            observed_fanout_active_count=0,
            evaluated_at=_EVALUATED,
            observations=(pending_status,),
            members=(member,),
        )


def test_status_snapshot_rejects_publication_after_observation() -> None:
    pending, status = _success(
        definition=_definition(success_policy="published_result"),
    )
    published = _published_success(pending, published_at=_EVALUATED)
    anchored_status = CalibrationStatus(
        calibration_key=status.calibration_key,
        latest_attempt=status.latest_attempt,
        latest_success=published,
    )

    with pytest.raises(ValidationError, match="publication cannot follow"):
        CalibrationStatusSnapshot(
            observed_at=_EVALUATED - timedelta(seconds=1),
            fanout_scope="chip-alpha",
            fanout_active_count=0,
            statuses=(anchored_status,),
        )


def test_same_freshness_failed_retry_requires_explicit_force() -> None:
    member = _member_spec()
    _, successful_status = _success()
    assert successful_status.latest_attempt is not None
    previous_attempt = successful_status.latest_attempt.attempt
    failed_at = _EVALUATED - timedelta(minutes=30)
    failed_status = CalibrationStatus(
        calibration_key=previous_attempt.calibration_key,
        latest_attempt=CalibrationAttemptStatus(
            attempt=previous_attempt,
            procedure_state="closed",
            procedure_revision=8,
            updated_at=failed_at,
            closure=ProcedureClosure(
                status="failed",
                closed_at=failed_at,
                reason="calibration failed",
            ),
        ),
    )

    with pytest.raises(ValidationError, match="explicit forced"):
        _cohort_spec(member=member, observations=(failed_status,))

    forced_member = member.model_copy(
        update={
            "due_reasons": (
                CalibrationMissingSuccessDueReason(),
                CalibrationForcedDueReason(reason="operator retry"),
            )
        }
    )
    spec = _cohort_spec(member=forced_member, observations=(failed_status,))

    assert any(
        isinstance(reason, CalibrationForcedDueReason)
        for reason in spec.members[0].due_reasons
    )


def test_cohort_observations_cannot_follow_their_evaluation_time() -> None:
    _, previous_status = _success()
    forced_member = _member_spec(
        due_reasons=(CalibrationForcedDueReason(reason="operator retry"),)
    )
    data = _cohort_spec(
        member=forced_member,
        observations=(previous_status,),
    ).model_dump()
    data["evaluated_at"] = _EVALUATED - timedelta(minutes=90)

    with pytest.raises(ValidationError, match="cannot follow its status observation"):
        CalibrationCohortSpec.model_validate(data)


def test_dependency_changed_reason_uses_current_flat_evidence() -> None:
    old, _ = _success(
        definition=_definition(definition_id="readout"),
        cohort_id="old",
        procedure_run_id="old-run",
    )
    current, _ = _success(
        definition=_definition(definition_id="readout"),
        cohort_id="new",
        procedure_run_id="new-run",
    )
    reason = CalibrationDependencyChangedDueReason(
        dependency_key=current.attempt.calibration_key,
        previous_success=old.dependency_evidence,
        current_success=current.dependency_evidence,
    )

    assert reason.previous_success != reason.current_success

    removed = CalibrationDependencyChangedDueReason(
        dependency_key=old.attempt.calibration_key,
        previous_success=old.dependency_evidence,
        current_success=None,
    )
    assert removed.current_success is None

    with pytest.raises(ValidationError, match="previous or current"):
        CalibrationDependencyChangedDueReason(
            dependency_key=old.attempt.calibration_key,
            previous_success=None,
            current_success=None,
        )


def test_cohort_rejects_forged_dependency_change_evidence() -> None:
    dependency_definition = _definition(definition_id="readout")
    dependency_target = _target(target_id="q1")
    old, _ = _success(
        definition=dependency_definition,
        target=dependency_target,
        cohort_id="old",
        procedure_run_id="old-run",
    )
    current, current_status = _success(
        definition=dependency_definition,
        target=dependency_target,
        cohort_id="current",
        procedure_run_id="current-run",
    )
    forged, _ = _success(
        definition=dependency_definition,
        target=dependency_target,
        cohort_id="forged",
        procedure_run_id="forged-run",
    )
    _, previous_status = _success(dependencies=(old.dependency_evidence,))

    forged_previous = CalibrationDependencyChangedDueReason(
        dependency_key=current.attempt.calibration_key,
        previous_success=forged.dependency_evidence,
        current_success=current.dependency_evidence,
    )
    member = _member_spec(
        dependencies=(current.dependency_evidence,),
        due_reasons=(forged_previous,),
    )
    with pytest.raises(ValidationError, match="observed prior dependency"):
        _cohort_spec(
            member=member,
            observations=(previous_status, current_status),
        )

    wrong_current = CalibrationDependencyChangedDueReason(
        dependency_key=current.attempt.calibration_key,
        previous_success=old.dependency_evidence,
        current_success=forged.dependency_evidence,
    )
    with pytest.raises(ValidationError, match="current dependency evidence"):
        _member_spec(
            dependencies=(current.dependency_evidence,),
            due_reasons=(wrong_current,),
        )

    false_change_without_prior_success = CalibrationDependencyChangedDueReason(
        dependency_key=current.attempt.calibration_key,
        current_success=current.dependency_evidence,
    )
    member_without_prior_success = _member_spec(
        dependencies=(current.dependency_evidence,),
        due_reasons=(false_change_without_prior_success,),
    )
    with pytest.raises(ValidationError, match="observed latest success"):
        _cohort_spec(
            member=member_without_prior_success,
            observations=(_missing_status(), current_status),
        )


def test_cohort_and_member_validate_hash_request_key_and_utc() -> None:
    cohort = _cohort()
    member_spec = cohort.spec.members[0]
    offset_created = _CREATED.astimezone(timezone(timedelta(hours=8)))
    member = CalibrationCohortMember(
        cohort_id=cohort.cohort_id,
        index=0,
        spec=member_spec,
        procedure_run_id="procedure-18-q0",
        request_key=calibration_cohort_member_request_key(
            cohort.cohort_id,
            member_spec,
        ),
        admitted_at=offset_created,
    )

    assert member.admitted_at == _CREATED
    assert member.attempt_ref.procedure == member.spec.procedure
    assert assert_model_round_trip(cohort) == cohort
    assert assert_model_round_trip(member) == member
    with pytest.raises(ValidationError):
        cohort.spec.config_source.entry_id = "other"

    bad_cohort = cohort.model_dump()
    bad_cohort["spec_hash"] = _HASH_1
    with pytest.raises(ValidationError, match="spec hash"):
        CalibrationCohort.model_validate(bad_cohort)

    bad_member = member.model_dump()
    bad_member["request_key"] = "wrong"
    with pytest.raises(ValidationError, match="request key"):
        CalibrationCohortMember.model_validate(bad_member)


def test_cohort_rejects_forged_cross_owner_dependency_even_with_matching_batch() -> (
    None
):
    from scopecat.records.calibration_scope import WorkingPointCalibrationOwner

    dependency, status = _success(
        target=_target(target_id="other").model_copy(
            update={
                "owner": WorkingPointCalibrationOwner(workspace_id="other-branch"),
            }
        )
    )
    member = _member_spec(dependencies=(dependency.dependency_evidence,))
    with pytest.raises(ValidationError, match="another owner"):
        _cohort_spec(
            member=member,
            observations=(
                _missing_status(target=member.target),
                status,
            ),
        )
