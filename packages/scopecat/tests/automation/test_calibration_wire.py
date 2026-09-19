from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError
from scopecat_testkit.records import assert_model_round_trip

from scopecat.automation import (
    CalibrationCohort,
    CalibrationCohortCreateCommand,
    CalibrationCohortCreateReceipt,
    CalibrationCohortFinalization,
    CalibrationCohortGetQuery,
    CalibrationCohortGetReceipt,
    CalibrationCohortListQuery,
    CalibrationCohortMember,
    CalibrationCohortMemberListQuery,
    CalibrationCohortMemberPage,
    CalibrationCohortMemberSpec,
    CalibrationCohortPage,
    CalibrationCohortSpec,
    CalibrationCohortSummary,
    CalibrationConfigSourceRef,
    CalibrationDefinitionRef,
    CalibrationMissingSuccessDueReason,
    CalibrationPublicationAttention,
    CalibrationPublicationAttentionCommand,
    CalibrationPublicationAttentionReceipt,
    CalibrationPublicationDeferCommand,
    CalibrationPublicationDeferReceipt,
    CalibrationPublicationGetQuery,
    CalibrationPublicationGetReceipt,
    CalibrationPublicationPolicyRef,
    CalibrationPublicationReadyItem,
    CalibrationPublicationReadyPage,
    CalibrationPublicationReadyQuery,
    CalibrationPublicationRetryCommand,
    CalibrationPublicationRetryReceipt,
    CalibrationStatus,
    CalibrationStatusQuery,
    CalibrationStatusReceipt,
    CalibrationStatusSnapshot,
    CalibrationTargetRef,
    ProcedureDefinitionRef,
    calibration_cohort_member_request_key,
    calibration_cohort_spec_hash,
    calibration_freshness_fingerprint,
    calibration_key,
)
from scopecat.config.registry.records import ConfigCompositionPolicyRef
from scopecat.records.calibration_scope import WorkingPointCalibrationScope
from scopecat.records.run import ConfigRegistryRunConfigSource
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

_HASH_1 = "sha256:" + "1" * 64
_HASH_2 = "sha256:" + "2" * 64
_HASH_3 = "sha256:" + "3" * 64
_EVALUATED = datetime(2026, 8, 18, 8, tzinfo=UTC)
_CREATED = _EVALUATED + timedelta(seconds=1)


def _fixture() -> tuple[
    CalibrationCohort,
    CalibrationCohortMember,
    CalibrationStatusSnapshot,
]:
    definition = CalibrationDefinitionRef(
        id="drag",
        version="2",
        fingerprint=_HASH_1,
        success_policy="published_result",
    )
    publication_policy = CalibrationPublicationPolicyRef(
        id="automatic-calibration-merge",
        version="3",
        fingerprint=_HASH_2,
        calibration=definition,
        composition_policy=ConfigCompositionPolicyRef(
            id="merge-calibration-results",
            version="5",
            fingerprint=_HASH_3,
        ),
    )
    target = CalibrationTargetRef(
        kind="qubit", id="q0", owner=_SCOPE.owner, sample_id="chip", context_id="parked"
    )
    procedure = ProcedureDefinitionRef(
        id="calibrate-drag",
        version="4",
        fingerprint=_HASH_2,
    )
    key = calibration_key(definition.id, target)
    status = CalibrationStatus(calibration_key=key)
    freshness = calibration_freshness_fingerprint(
        definition=definition,
        target=target,
        procedure=procedure,
        input_fingerprint=_HASH_3,
        dependencies=(),
    )
    member_spec = CalibrationCohortMemberSpec(
        member_id="member-q0",
        calibration_key=key,
        definition=definition,
        target=target,
        procedure=procedure,
        intent={"target_id": "q0"},
        input_fingerprint=_HASH_3,
        dependencies=(),
        freshness_fingerprint=freshness,
        due_reasons=(CalibrationMissingSuccessDueReason(),),
    )
    spec = CalibrationCohortSpec(
        definition=definition,
        automatic_publication=publication_policy,
        config_source=CalibrationConfigSourceRef.from_run_config_source(
            ConfigRegistryRunConfigSource(
                selector="active",
                entry_id="config-17",
                config_ref="configs/17",
                content_hash=_HASH_3,
                registry_generation=23,
            )
        ).model_copy(update={"scope": _SCOPE}),
        fanout_scope="chip-alpha",
        max_in_flight=2,
        observed_fanout_active_count=0,
        evaluated_at=_EVALUATED,
        observations=(status,),
        members=(member_spec,),
    )
    cohort = CalibrationCohort(
        cohort_id="cohort-18",
        spec=spec,
        spec_hash=calibration_cohort_spec_hash(spec),
        created_at=_CREATED,
    )
    member = CalibrationCohortMember(
        cohort_id=cohort.cohort_id,
        index=0,
        spec=member_spec,
        procedure_run_id="procedure-18-q0",
        request_key=calibration_cohort_member_request_key(
            cohort.cohort_id,
            member_spec,
        ),
        admitted_at=_CREATED,
    )
    snapshot = CalibrationStatusSnapshot(
        observed_at=_EVALUATED,
        fanout_scope=spec.fanout_scope,
        fanout_active_count=spec.observed_fanout_active_count,
        statuses=spec.observations,
    )
    return cohort, member, snapshot


def _ready_finalization(
    cohort: CalibrationCohort,
    *,
    revision: int = 2,
    attempt_count: int = 0,
    updated_at: datetime | None = None,
    available_at: datetime | None = None,
) -> CalibrationCohortFinalization:
    policy = cohort.spec.automatic_publication
    assert policy is not None
    ready_at = _CREATED + timedelta(minutes=1)
    selected_updated_at = updated_at or ready_at
    return CalibrationCohortFinalization(
        cohort_id=cohort.cohort_id,
        spec_hash=cohort.spec_hash,
        policy=policy,
        base_config_source=cohort.spec.config_source,
        revision=revision,
        state="ready",
        attempt_count=attempt_count,
        created_at=cohort.created_at,
        updated_at=selected_updated_at,
        ready_at=ready_at,
        available_at=available_at or selected_updated_at,
    )


def test_status_query_and_receipt_are_bounded_and_round_trip() -> None:
    cohort, _, snapshot = _fixture()
    key = cohort.spec.members[0].calibration_key
    query = CalibrationStatusQuery(
        calibration_keys=(key,),
        fanout_scope="chip-alpha",
    )
    receipt = CalibrationStatusReceipt(snapshot=snapshot)

    assert assert_model_round_trip(query) == query
    assert assert_model_round_trip(receipt) == receipt
    with pytest.raises(ValidationError, match="unique"):
        CalibrationStatusQuery(
            calibration_keys=(key, key),
            fanout_scope="chip-alpha",
        )
    with pytest.raises(ValidationError):
        CalibrationStatusQuery(
            calibration_keys=tuple(str(index) for index in range(201)),
            fanout_scope="chip-alpha",
        )


def test_create_command_exposes_hash_and_receipt_requires_exact_members() -> None:
    cohort, member, _ = _fixture()
    command = CalibrationCohortCreateCommand(
        cohort_id=cohort.cohort_id,
        spec=cohort.spec,
    )
    receipt = CalibrationCohortCreateReceipt(
        cohort=cohort,
        members=(member,),
    )

    assert command.spec_hash == cohort.spec_hash
    assert assert_model_round_trip(command) == command
    assert assert_model_round_trip(receipt) == receipt

    wrong_member = CalibrationCohortMember(
        cohort_id="other",
        index=member.index,
        spec=member.spec,
        procedure_run_id=member.procedure_run_id,
        request_key=calibration_cohort_member_request_key("other", member.spec),
        admitted_at=member.admitted_at,
    )
    with pytest.raises(ValidationError, match="match its cohort"):
        CalibrationCohortCreateReceipt(
            cohort=cohort,
            members=(wrong_member,),
        )


def test_get_list_and_member_pages_use_compact_exact_projections() -> None:
    cohort, member, _ = _fixture()
    summary = CalibrationCohortSummary.from_cohort(cohort)
    get_query = CalibrationCohortGetQuery(cohort_id=cohort.cohort_id)
    get_receipt = CalibrationCohortGetReceipt(cohort=cohort)
    list_query = CalibrationCohortListQuery(
        limit=20,
        fanout_scope=cohort.spec.fanout_scope,
    )
    page = CalibrationCohortPage(items=(summary,), next_cursor=7)
    member_query = CalibrationCohortMemberListQuery(
        cohort_id=cohort.cohort_id,
        limit=20,
    )
    member_page = CalibrationCohortMemberPage(
        cohort_id=cohort.cohort_id,
        items=(member,),
    )

    assert summary.member_count == 1
    assert assert_model_round_trip(get_query) == get_query
    assert assert_model_round_trip(get_receipt) == get_receipt
    assert assert_model_round_trip(list_query) == list_query
    assert assert_model_round_trip(page) == page
    assert assert_model_round_trip(member_query) == member_query
    assert assert_model_round_trip(member_page) == member_page

    with pytest.raises(ValidationError, match="match its cohort"):
        CalibrationCohortMemberPage(
            cohort_id="other",
            items=(member,),
        )


def test_wire_models_forbid_unknown_fields_and_are_frozen() -> None:
    cohort, _, _ = _fixture()
    command = CalibrationCohortCreateCommand(
        cohort_id=cohort.cohort_id,
        spec=cohort.spec,
    )

    with pytest.raises(ValidationError):
        CalibrationCohortCreateCommand.model_validate(
            {
                **command.model_dump(),
                "unexpected": True,
            }
        )
    with pytest.raises(ValidationError):
        command.cohort_id = "other"


def test_publication_ready_traversal_is_policy_filtered_and_high_water_bounded() -> (
    None
):
    cohort, _, _ = _fixture()
    policy = cohort.spec.automatic_publication
    assert policy is not None
    finalization = _ready_finalization(cohort)
    assert finalization.ready_at is not None
    item = CalibrationPublicationReadyItem(
        sequence=11,
        cohort=CalibrationCohortSummary.from_cohort(cohort),
        finalization=finalization,
        enqueued_at=finalization.ready_at,
    )
    query = CalibrationPublicationReadyQuery(capabilities=(policy,), limit=20)
    continuation = CalibrationPublicationReadyQuery(
        capabilities=(policy,),
        cursor=11,
        through_sequence=20,
        limit=20,
    )
    page = CalibrationPublicationReadyPage(
        items=(item,),
        next_cursor=11,
        through_sequence=20,
    )

    assert assert_model_round_trip(query) == query
    assert assert_model_round_trip(continuation) == continuation
    assert assert_model_round_trip(page) == page
    with pytest.raises(ValidationError, match="provided together"):
        CalibrationPublicationReadyQuery(cursor=11)
    with pytest.raises(ValidationError, match="unique"):
        CalibrationPublicationReadyQuery(capabilities=(policy, policy))
    with pytest.raises(ValidationError, match="last ready sequence"):
        CalibrationPublicationReadyPage(
            items=(item,),
            next_cursor=12,
            through_sequence=20,
        )


def test_publication_defer_preserves_ready_occurrence_sequence() -> None:
    cohort, _, _ = _fixture()
    ready = _ready_finalization(cohort)
    assert ready.ready_at is not None
    deferred_at = ready.updated_at + timedelta(seconds=5)
    deferred = _ready_finalization(
        cohort,
        revision=ready.revision + 1,
        attempt_count=ready.attempt_count + 1,
        updated_at=deferred_at,
        available_at=deferred_at + timedelta(seconds=30),
    )
    assert deferred.ready_at is not None

    original = CalibrationPublicationReadyItem(
        sequence=11,
        cohort=CalibrationCohortSummary.from_cohort(cohort),
        finalization=ready,
        enqueued_at=ready.ready_at,
    )
    after_defer = CalibrationPublicationReadyItem(
        sequence=original.sequence,
        cohort=original.cohort,
        finalization=deferred,
        enqueued_at=original.enqueued_at,
    )

    assert after_defer.sequence == original.sequence
    assert after_defer.enqueued_at == deferred.ready_at
    assert deferred.updated_at > deferred.ready_at
    assert assert_model_round_trip(after_defer) == after_defer


def test_publication_reconciliation_and_cas_commands_return_exact_state() -> None:
    cohort, _, _ = _fixture()
    policy = cohort.spec.automatic_publication
    assert policy is not None
    ready = _ready_finalization(cohort)
    attention_at = ready.updated_at + timedelta(minutes=1)
    attention = CalibrationCohortFinalization(
        cohort_id=cohort.cohort_id,
        spec_hash=cohort.spec_hash,
        policy=policy,
        base_config_source=cohort.spec.config_source,
        revision=ready.revision + 1,
        state="attention_required",
        attempt_count=ready.attempt_count + 1,
        created_at=cohort.created_at,
        updated_at=attention_at,
        ready_at=ready.ready_at,
        attention=CalibrationPublicationAttention(
            actor="resident-worker",
            reason="invalid composition proof",
            required_at=attention_at,
        ),
    )
    retry_at = attention_at + timedelta(minutes=1)
    retried = CalibrationCohortFinalization(
        cohort_id=cohort.cohort_id,
        spec_hash=cohort.spec_hash,
        policy=policy,
        base_config_source=cohort.spec.config_source,
        revision=attention.revision + 1,
        state="ready",
        attempt_count=attention.attempt_count,
        created_at=cohort.created_at,
        updated_at=retry_at,
        ready_at=retry_at,
        available_at=retry_at,
    )
    deferred_at = ready.updated_at + timedelta(seconds=5)
    deferred = _ready_finalization(
        cohort,
        revision=ready.revision + 1,
        attempt_count=ready.attempt_count + 1,
        updated_at=deferred_at,
        available_at=deferred_at + timedelta(seconds=30),
    )

    models = (
        CalibrationPublicationGetQuery(cohort_id=cohort.cohort_id),
        CalibrationPublicationGetReceipt(finalization=ready),
        CalibrationPublicationAttentionCommand(
            cohort_id=cohort.cohort_id,
            policy=policy,
            expected_finalization_revision=ready.revision,
            actor="resident-worker",
            reason="invalid composition proof",
        ),
        CalibrationPublicationAttentionReceipt(finalization=attention),
        CalibrationPublicationRetryCommand(
            cohort_id=cohort.cohort_id,
            policy=policy,
            expected_finalization_revision=attention.revision,
            actor="operator",
            reason="policy implementation repaired",
        ),
        CalibrationPublicationRetryReceipt(finalization=retried),
        CalibrationPublicationDeferCommand(
            cohort_id=cohort.cohort_id,
            policy=policy,
            expected_finalization_revision=ready.revision,
            retry_after_seconds=30,
            reason="config service unavailable",
        ),
        CalibrationPublicationDeferReceipt(finalization=deferred),
    )
    for model in models:
        assert assert_model_round_trip(model) == model

    with pytest.raises(ValidationError, match="ready state"):
        CalibrationPublicationRetryReceipt(finalization=attention)
    with pytest.raises(ValidationError, match="future server availability"):
        CalibrationPublicationDeferReceipt(finalization=ready)
    with pytest.raises(ValidationError):
        CalibrationPublicationDeferCommand(
            cohort_id=cohort.cohort_id,
            policy=policy,
            expected_finalization_revision=ready.revision,
            retry_after_seconds=3601,
            reason="config service unavailable",
        )
