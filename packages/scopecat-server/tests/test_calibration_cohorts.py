from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient
from scopecat.automation import (
    CalibrationCohortCreateCommand,
    CalibrationCohortCreateReceipt,
    CalibrationCohortGetQuery,
    CalibrationCohortGetReceipt,
    CalibrationCohortListQuery,
    CalibrationCohortMember,
    CalibrationCohortMemberListQuery,
    CalibrationCohortMemberSpec,
    CalibrationCohortSpec,
    CalibrationCohortSummary,
    CalibrationConfigSourceRef,
    CalibrationDefinitionRef,
    CalibrationForcedDueReason,
    CalibrationPublicationAttentionCommand,
    CalibrationPublicationAttentionReceipt,
    CalibrationPublicationDeferCommand,
    CalibrationPublicationDeferReceipt,
    CalibrationPublicationGetQuery,
    CalibrationPublicationGetReceipt,
    CalibrationPublicationPolicyRef,
    CalibrationPublicationReadyPage,
    CalibrationPublicationReadyQuery,
    CalibrationPublicationRetryCommand,
    CalibrationPublicationRetryReceipt,
    CalibrationStatusQuery,
    CalibrationStatusReceipt,
    CalibrationSuccessPolicy,
    CalibrationSuccessPublication,
    CalibrationSuccessRef,
    CalibrationTargetRef,
    ProcedureCloseCommand,
    ProcedureCloseStatus,
    ProcedureDefinitionRef,
    ProcedureRun,
    ProcedureRunAttentionCommand,
    ProcedureRunListQuery,
    ProcedureRunPage,
    ProcedureRunState,
    ProcedureSubmitCommand,
    ProcedureWorkerLeaseAcquireCommand,
    calibration_cohort_member_request_key,
    calibration_freshness_fingerprint,
    calibration_key,
)
from scopecat.config.registry.records import (
    ConfigCompositionPolicyRef,
    ConfigPublishOperation,
)
from scopecat.config.registry.service import (
    ConfigRevision,
    DirectConfigRevisionSource,
    publish_config_revision,
    resolve_config_registry_config_source,
)
from scopecat.daemon.wire import (
    ConfigPublishCommand,
    ConfigPublishReceipt,
)
from scopecat.daemon.wire import (
    DirectConfigRevisionSource as WireDirectConfigRevisionSource,
)
from scopecat.records.run import ConfigRegistryRunConfigSource
from scopecat.records.sample import SampleSelector
from scopecat_testkit.workflow_fixtures import load_config

from scopecat_server import BackendConflict, BackendNotFound, LocalDaemonRuntime
from scopecat_server.services.automation import AutomationService
from scopecat_server.services.calibration_cohorts import CalibrationCohortService
from scopecat_server.storage.sqlite import calibration_cohorts as calibration_storage
from scopecat_server.storage.sqlite.automation import (
    AutomationConflict,
    SQLiteAutomationStore,
)
from scopecat_server.storage.sqlite.calibration_cohorts import (
    CalibrationCohortConflict,
    CalibrationCohortStoreError,
    SQLiteCalibrationCohortStore,
)
from scopecat_server.storage.sqlite.config_operations import SQLiteConfigOperationStore
from scopecat_server.storage.sqlite.config_registry import SQLiteConfigRegistryStore
from scopecat_server.storage.sqlite.connection import SQLiteDatabase
from scopecat_server.storage.sqlite.project_store import SQLiteProjectStore
from scopecat_server.storage.sqlite.run_repository import SQLiteRunRepository

_START = datetime(2026, 8, 18, 9, tzinfo=UTC)
_DEFINITION_HASH = "sha256:" + "1" * 64
_PROCEDURE_HASH = "sha256:" + "2" * 64
_INPUT_HASH = "sha256:" + "3" * 64
_RESULT_INPUT_HASH = "sha256:" + "4" * 64
_PUBLICATION_POLICY_HASH = "sha256:" + "5" * 64
_COMPOSITION_POLICY_HASH = "sha256:" + "6" * 64


@dataclass(frozen=True, slots=True)
class _Harness:
    service: CalibrationCohortService
    automation: AutomationService
    store: SQLiteCalibrationCohortStore
    automation_store: SQLiteAutomationStore
    config_registry: SQLiteConfigRegistryStore
    source: CalibrationConfigSourceRef
    now: list[datetime]


def _harness(tmp_path: Path) -> _Harness:
    sqlite = SQLiteDatabase(tmp_path / "control.sqlite3")
    objects = tmp_path / "objects"
    SQLiteProjectStore(sqlite, objects).bootstrap()
    runs = SQLiteRunRepository(sqlite, objects)
    config_registry = SQLiteConfigRegistryStore(sqlite, runs=runs)
    publish_config_revision(
        revision=ConfigRevision(
            source=DirectConfigRevisionSource(load_config()),
            entry_id="calibration-baseline",
            actor="test",
        ),
        unit_of_work=config_registry.write_unit_of_work,
        expected_generation=0,
    )
    _, run_source = resolve_config_registry_config_source(
        selector="active",
        unit_of_work=config_registry.read_unit_of_work,
    )
    assert isinstance(run_source, ConfigRegistryRunConfigSource)
    source = CalibrationConfigSourceRef.from_run_config_source(run_source)
    now = [_START]
    automation_store = SQLiteAutomationStore(sqlite)
    automation = AutomationService(
        automation_store,
        runs=SQLiteRunRepository(sqlite, tmp_path / "objects"),
        clock=lambda: now[0],
    )
    store = SQLiteCalibrationCohortStore(sqlite)
    return _Harness(
        service=CalibrationCohortService(
            store,
            automation,
            config_registry,
            clock=lambda: now[0],
        ),
        automation=automation,
        store=store,
        automation_store=automation_store,
        config_registry=config_registry,
        source=source,
        now=now,
    )


def _member(
    target_id: str,
    *,
    success_policy: CalibrationSuccessPolicy = "procedure_success",
    sample_id: str | None = None,
    context_id: str | None = None,
) -> CalibrationCohortMemberSpec:
    definition = CalibrationDefinitionRef(
        id="drag-calibration",
        version="1",
        fingerprint=_DEFINITION_HASH,
        success_policy=success_policy,
    )
    target = CalibrationTargetRef(
        kind="qubit",
        id=target_id,
        sample_id=sample_id,
        context_id=context_id,
    )
    procedure = ProcedureDefinitionRef(
        id="drag-calibration-procedure",
        version="1",
        fingerprint=_PROCEDURE_HASH,
    )
    return CalibrationCohortMemberSpec(
        member_id=f"drag-{target_id}",
        calibration_key=calibration_key(definition.id, target),
        definition=definition,
        target=target,
        procedure=procedure,
        intent={"target": target_id},
        input_fingerprint=_INPUT_HASH,
        freshness_fingerprint=calibration_freshness_fingerprint(
            definition=definition,
            target=target,
            procedure=procedure,
            input_fingerprint=_INPUT_HASH,
            dependencies=(),
        ),
        due_reasons=(CalibrationForcedDueReason(reason="test admission"),),
    )


def _command(
    cohort_id: str,
    *,
    source: CalibrationConfigSourceRef,
    snapshot: CalibrationStatusReceipt,
    members: tuple[CalibrationCohortMemberSpec, ...],
    automatic_publication: CalibrationPublicationPolicyRef | None = None,
) -> CalibrationCohortCreateCommand:
    status = snapshot.snapshot
    return CalibrationCohortCreateCommand(
        cohort_id=cohort_id,
        spec=CalibrationCohortSpec(
            definition=members[0].definition,
            automatic_publication=automatic_publication,
            config_source=source,
            fanout_scope=status.fanout_scope,
            max_in_flight=4,
            observed_fanout_active_count=status.fanout_active_count,
            evaluated_at=status.observed_at,
            observations=status.statuses,
            members=members,
        ),
    )


def _publication_policy(
    definition: CalibrationDefinitionRef,
) -> CalibrationPublicationPolicyRef:
    return CalibrationPublicationPolicyRef(
        id="test.calibration-publication",
        version="1",
        fingerprint=_PUBLICATION_POLICY_HASH,
        calibration=definition,
        composition_policy=ConfigCompositionPolicyRef(
            id="test.config-composition",
            version="1",
            fingerprint=_COMPOSITION_POLICY_HASH,
        ),
    )


def _status(
    harness: _Harness,
    members: tuple[CalibrationCohortMemberSpec, ...],
    *,
    scope: str = "calibration-workers",
) -> CalibrationStatusReceipt:
    return harness.service.status(
        CalibrationStatusQuery(
            calibration_keys=tuple(member.calibration_key for member in members),
            fanout_scope=scope,
        )
    )


def _close(
    harness: _Harness,
    member: CalibrationCohortMember,
    *,
    status: ProcedureCloseStatus,
) -> None:
    acquired = harness.automation.acquire_lease(
        ProcedureWorkerLeaseAcquireCommand(
            procedure_run_id=member.procedure_run_id,
            worker_id="test-worker",
            expected_run_revision=1,
        )
    )
    harness.now[0] += timedelta(seconds=1)
    harness.automation.close(
        ProcedureCloseCommand(
            procedure_run_id=member.procedure_run_id,
            lease_token=acquired.lease.lease_token,
            expected_run_revision=acquired.run.revision,
            status=status,
            reason=None if status == "succeeded" else "test failure",
        )
    )


def _attach_success_publication(
    harness: _Harness,
    pending: CalibrationSuccessRef,
) -> CalibrationSuccessRef:
    result_config = load_config().model_copy(update={"id": "calibration-result"})
    result = publish_config_revision(
        revision=ConfigRevision(
            source=DirectConfigRevisionSource(result_config),
            entry_id="calibration-result-entry",
            actor="test",
        ),
        unit_of_work=harness.config_registry.write_unit_of_work,
        expected_generation=pending.base_config_source.registry_generation,
    )
    activation = result.activation
    assert activation is not None
    command = ConfigPublishCommand(
        operation_id="publish:calibration-result",
        source=WireDirectConfigRevisionSource(config=result_config),
        actor="test",
        expected_generation=pending.base_config_source.registry_generation,
        entry_id=result.entry.id,
    )
    operation = ConfigPublishOperation(
        operation_id=command.operation_id,
        intent_hash=command.intent_hash,
        source_intent_hash=command.source_intent_hash,
        entry_id=command.entry_id,
        expected_generation=command.expected_generation,
        actor=command.actor,
        note=command.note,
        activation_generation=activation.generation,
        recorded_at=activation.recorded_at,
    )
    receipt = ConfigPublishReceipt(
        operation=operation,
        entry=result.entry,
        deltas=result.deltas,
        activation=activation,
    )
    result_source = CalibrationConfigSourceRef(
        entry_id=result.entry.id,
        config_ref=result.entry.config_ref,
        content_hash=result.entry.content_hash,
        registry_generation=activation.generation,
    )
    publication = CalibrationSuccessPublication(
        operation_id=operation.operation_id,
        source_intent_hash=operation.source_intent_hash,
        result_input_fingerprint=_RESULT_INPUT_HASH,
        result_freshness_fingerprint=calibration_freshness_fingerprint(
            definition=pending.attempt.definition,
            target=pending.attempt.target,
            procedure=pending.attempt.procedure,
            input_fingerprint=_RESULT_INPUT_HASH,
            dependencies=pending.attempt.dependencies,
        ),
        result_config_source=result_source,
        published_at=activation.recorded_at,
    )
    anchored = CalibrationSuccessRef(
        attempt=pending.attempt,
        base_config_source=pending.base_config_source,
        succeeded_at=pending.succeeded_at,
        publication=publication,
    )
    with harness.store.write_transaction() as connection:
        SQLiteConfigOperationStore(harness.store.sqlite).commit_in_transaction(
            connection,
            receipt,
        )
        harness.store.insert_success_publication_in_transaction(
            connection,
            anchored,
        )
    harness.now[0] = max(harness.now[0], activation.recorded_at)
    return anchored


def test_automatic_publication_becomes_ready_and_supports_control_transitions(
    tmp_path: Path,
) -> None:
    harness = _harness(tmp_path)
    members = (
        _member("q0", success_policy="published_result"),
        _member("q1", success_policy="published_result"),
    )
    policy = _publication_policy(members[0].definition)
    command = _command(
        "automatic-cohort",
        source=harness.source,
        snapshot=_status(harness, members),
        members=members,
        automatic_publication=policy,
    )
    receipt = harness.service.create(command)

    initial = harness.service.get_publication(
        CalibrationPublicationGetQuery(cohort_id=command.cohort_id)
    ).finalization
    assert initial.state == "waiting"
    assert initial.revision == 1

    _close(harness, receipt.members[0], status="succeeded")
    assert harness.store.read_finalization(command.cohort_id).state == "waiting"
    _close(harness, receipt.members[1], status="succeeded")

    ready = harness.store.read_finalization(command.cohort_id)
    assert ready.state == "ready"
    assert ready.revision == 2
    assert ready.ready_at == harness.now[0]
    page = harness.service.ready_publications(
        CalibrationPublicationReadyQuery(capabilities=(policy,), limit=1)
    )
    assert len(page.items) == 1
    first_occurrence = page.items[0]
    assert first_occurrence.finalization == ready

    changed_policy = policy.model_copy(update={"fingerprint": "sha256:" + "9" * 64})
    assert not harness.service.ready_publications(
        CalibrationPublicationReadyQuery(capabilities=(changed_policy,))
    ).items
    changed_binding = policy.model_copy(
        update={
            "calibration": policy.calibration.model_copy(
                update={"fingerprint": "sha256:" + "8" * 64}
            )
        }
    )
    assert not harness.service.ready_publications(
        CalibrationPublicationReadyQuery(capabilities=(changed_binding,))
    ).items

    deferred = harness.service.defer_publication(
        CalibrationPublicationDeferCommand(
            cohort_id=command.cohort_id,
            policy=policy,
            expected_finalization_revision=ready.revision,
            retry_after_seconds=30,
            reason="temporary control-plane outage",
        )
    ).finalization
    assert deferred.state == "ready"
    assert deferred.attempt_count == 1
    assert not harness.service.ready_publications(
        CalibrationPublicationReadyQuery(capabilities=(policy,))
    ).items

    harness.now[0] += timedelta(seconds=30)
    resumed_page = harness.service.ready_publications(
        CalibrationPublicationReadyQuery(capabilities=(policy,))
    )
    assert resumed_page.items[0].sequence == first_occurrence.sequence
    assert resumed_page.items[0].enqueued_at == first_occurrence.enqueued_at

    attention = harness.service.require_publication_attention(
        CalibrationPublicationAttentionCommand(
            cohort_id=command.cohort_id,
            policy=policy,
            expected_finalization_revision=deferred.revision,
            actor="automatic-finalizer",
            reason="deterministic proof drift",
        )
    ).finalization
    assert attention.state == "attention_required"
    assert not harness.service.ready_publications(
        CalibrationPublicationReadyQuery(capabilities=(policy,))
    ).items

    harness.now[0] += timedelta(seconds=1)
    retried = harness.service.retry_publication(
        CalibrationPublicationRetryCommand(
            cohort_id=command.cohort_id,
            policy=policy,
            expected_finalization_revision=attention.revision,
            actor="operator",
            reason="policy restored",
        )
    ).finalization
    retry_page = harness.service.ready_publications(
        CalibrationPublicationReadyQuery(capabilities=(policy,))
    )
    assert retried.state == "ready"
    assert retry_page.items[0].sequence > first_occurrence.sequence
    assert retry_page.items[0].enqueued_at == retried.ready_at


def test_ready_publication_routes_on_canonical_finalization_columns(
    tmp_path: Path,
) -> None:
    harness = _harness(tmp_path)
    member = _member("q0", success_policy="published_result")
    policy = _publication_policy(member.definition)
    command = _command(
        "canonical-ready-routing",
        source=harness.source,
        snapshot=_status(harness, (member,)),
        members=(member,),
        automatic_publication=policy,
    )
    created = harness.service.create(command)
    _close(harness, created.members[0], status="succeeded")

    query, capability_parameters = calibration_storage._ready_publication_rows_query(
        (policy,)
    )
    assert "policy_json" not in query
    assert "finalizations.available_at <= ?" in query
    with harness.store.read_transaction() as connection:
        through_sequence = connection.execute(
            "SELECT MAX(sequence) FROM calibration_publication_ready_queue"
        ).fetchone()[0]
        assert through_sequence is not None
        plan = tuple(
            row["detail"]
            for row in connection.execute(
                f"EXPLAIN QUERY PLAN {query}",
                (
                    harness.now[0].isoformat(),
                    None,
                    None,
                    through_sequence,
                    *capability_parameters,
                    51,
                ),
            )
        )
    assert any(
        "calibration_cohort_finalizations_ready_capability" in detail for detail in plan
    )


def test_retry_rolls_back_when_ready_occurrence_conflicts(tmp_path: Path) -> None:
    harness = _harness(tmp_path)
    member = _member("q0", success_policy="published_result")
    policy = _publication_policy(member.definition)
    command = _command(
        "retry-ready-conflict",
        source=harness.source,
        snapshot=_status(harness, (member,)),
        members=(member,),
        automatic_publication=policy,
    )
    created = harness.service.create(command)
    _close(harness, created.members[0], status="succeeded")
    ready = harness.store.read_finalization(command.cohort_id)
    attention = harness.service.require_publication_attention(
        CalibrationPublicationAttentionCommand(
            cohort_id=command.cohort_id,
            policy=policy,
            expected_finalization_revision=ready.revision,
            actor="test-finalizer",
            reason="prepare conflicting occurrence",
        )
    ).finalization
    with harness.store.write_transaction() as connection:
        connection.execute(
            """
            INSERT INTO calibration_publication_ready_queue(cohort_id, enqueued_at)
            VALUES (?, ?)
            """,
            (command.cohort_id, harness.now[0].isoformat()),
        )

    with pytest.raises(BackendConflict, match="retry conflicts"):
        harness.service.retry_publication(
            CalibrationPublicationRetryCommand(
                cohort_id=command.cohort_id,
                policy=policy,
                expected_finalization_revision=attention.revision,
                actor="operator",
                reason="exercise rollback",
            )
        )

    assert harness.store.read_finalization(command.cohort_id) == attention


def test_publication_completion_store_requires_exact_available_ready_revision(
    tmp_path: Path,
) -> None:
    harness = _harness(tmp_path)
    members = (_member("q0", success_policy="published_result"),)
    policy = _publication_policy(members[0].definition)
    command = _command(
        "completion-fence-cohort",
        source=harness.source,
        snapshot=_status(harness, members),
        members=members,
        automatic_publication=policy,
    )
    receipt = harness.service.create(command)
    _close(harness, receipt.members[0], status="succeeded")
    ready = harness.store.read_finalization(command.cohort_id)
    assert ready.state == "ready"

    with (
        harness.store.write_transaction() as connection,
        pytest.raises(CalibrationCohortConflict, match="not eligible"),
    ):
        harness.store.complete_publication_in_transaction(
            connection,
            cohort_id=command.cohort_id,
            policy=policy,
            expected_revision=ready.revision - 1,
            operation_id="uncommitted-publication",
            at=harness.now[0],
        )

    deferred = harness.service.defer_publication(
        CalibrationPublicationDeferCommand(
            cohort_id=command.cohort_id,
            policy=policy,
            expected_finalization_revision=ready.revision,
            retry_after_seconds=30,
            reason="transient dependency outage",
        )
    ).finalization
    with (
        harness.store.write_transaction() as connection,
        pytest.raises(CalibrationCohortConflict, match="not yet available"),
    ):
        harness.store.complete_publication_in_transaction(
            connection,
            cohort_id=command.cohort_id,
            policy=policy,
            expected_revision=deferred.revision,
            operation_id="uncommitted-publication",
            at=harness.now[0],
        )

    harness.now[0] += timedelta(seconds=30)
    attention = harness.service.require_publication_attention(
        CalibrationPublicationAttentionCommand(
            cohort_id=command.cohort_id,
            policy=policy,
            expected_finalization_revision=deferred.revision,
            actor="automatic-finalizer",
            reason="deterministic proof drift",
        )
    ).finalization
    with (
        harness.store.write_transaction() as connection,
        pytest.raises(CalibrationCohortConflict, match="not eligible"),
    ):
        harness.store.complete_publication_in_transaction(
            connection,
            cohort_id=command.cohort_id,
            policy=policy,
            expected_revision=attention.revision,
            operation_id="uncommitted-publication",
            at=harness.now[0],
        )
    assert harness.store.read_finalization(command.cohort_id) == attention


def test_automatic_publication_fails_terminally_with_any_failed_member(
    tmp_path: Path,
) -> None:
    harness = _harness(tmp_path)
    members = (
        _member("q0", success_policy="published_result"),
        _member("q1", success_policy="published_result"),
    )
    policy = _publication_policy(members[0].definition)
    receipt = harness.service.create(
        _command(
            "failed-automatic-cohort",
            source=harness.source,
            snapshot=_status(harness, members),
            members=members,
            automatic_publication=policy,
        )
    )

    _close(harness, receipt.members[0], status="failed")

    finalization = harness.store.read_finalization(receipt.cohort.cohort_id)
    assert finalization.state == "failed"
    assert finalization.failure is not None
    assert not harness.service.ready_publications(
        CalibrationPublicationReadyQuery(capabilities=(policy,))
    ).items


def test_automatic_publication_ready_traversal_uses_a_finite_high_water(
    tmp_path: Path,
) -> None:
    harness = _harness(tmp_path)
    policy = _publication_policy(
        _member("q0", success_policy="published_result").definition
    )

    def create_ready(cohort_id: str, target_id: str) -> None:
        members = (_member(target_id, success_policy="published_result"),)
        receipt = harness.service.create(
            _command(
                cohort_id,
                source=harness.source,
                snapshot=_status(harness, members),
                members=members,
                automatic_publication=policy,
            )
        )
        _close(harness, receipt.members[0], status="succeeded")

    create_ready("ready-a", "q0")
    create_ready("ready-b", "q1")
    first = harness.service.ready_publications(
        CalibrationPublicationReadyQuery(capabilities=(policy,), limit=1)
    )
    assert tuple(item.cohort.cohort_id for item in first.items) == ("ready-a",)
    assert first.next_cursor is not None
    assert first.through_sequence is not None
    first_item = first.items[0]
    harness.service.require_publication_attention(
        CalibrationPublicationAttentionCommand(
            cohort_id=first_item.cohort.cohort_id,
            policy=policy,
            expected_finalization_revision=first_item.finalization.revision,
            actor="test-finalizer",
            reason="finish first occurrence",
        )
    )

    create_ready("ready-c", "q2")
    second = harness.service.ready_publications(
        CalibrationPublicationReadyQuery(
            capabilities=(policy,),
            cursor=first.next_cursor,
            through_sequence=first.through_sequence,
            limit=1,
        )
    )
    assert tuple(item.cohort.cohort_id for item in second.items) == ("ready-b",)
    assert second.next_cursor is None
    assert second.through_sequence is None
    second_item = second.items[0]
    harness.service.require_publication_attention(
        CalibrationPublicationAttentionCommand(
            cohort_id=second_item.cohort.cohort_id,
            policy=policy,
            expected_finalization_revision=second_item.finalization.revision,
            actor="test-finalizer",
            reason="finish frozen traversal",
        )
    )

    wrapped = harness.service.ready_publications(
        CalibrationPublicationReadyQuery(capabilities=(policy,), limit=1)
    )
    assert tuple(item.cohort.cohort_id for item in wrapped.items) == ("ready-c",)


def _move_to_active_state(
    harness: _Harness,
    member: CalibrationCohortMember,
    state: ProcedureRunState,
) -> None:
    if state == "ready":
        return
    acquired = harness.automation.acquire_lease(
        ProcedureWorkerLeaseAcquireCommand(
            procedure_run_id=member.procedure_run_id,
            worker_id="state-worker",
            expected_run_revision=1,
        )
    )
    if state == "leased":
        return
    assert state == "attention_required"
    harness.automation.require_run_attention(
        ProcedureRunAttentionCommand(
            procedure_run_id=member.procedure_run_id,
            lease_token=acquired.lease.lease_token,
            expected_run_revision=acquired.run.revision,
            reason="test attention",
        )
    )


def test_sample_scoped_calibration_propagates_to_procedure_child_runs(
    tmp_path: Path,
) -> None:
    harness = _harness(tmp_path)
    member = _member(
        "q0",
        sample_id="die-1",
        context_id="cooldown-2",
    )
    command = _command(
        "sample-scoped-calibration",
        source=harness.source,
        snapshot=_status(harness, (member,)),
        members=(member,),
    )

    created = harness.service.create(command)
    procedure = harness.automation.get(created.members[0].procedure_run_id)

    assert procedure.samples == (
        SampleSelector(sample_id="die-1", context_id="cooldown-2"),
    )


def test_cohort_status_tracks_latest_attempt_and_prior_success(
    tmp_path: Path,
) -> None:
    harness = _harness(tmp_path)
    first_members = (_member("q0"), _member("q1"))
    command = _command(
        "drag-batch-1",
        source=harness.source,
        snapshot=_status(harness, first_members),
        members=first_members,
    )

    created = harness.service.create(command)
    assert tuple(member.index for member in created.members) == (0, 1)
    assert all(
        harness.automation.get(member.procedure_run_id).state == "ready"
        for member in created.members
    )
    assert harness.service.create(command) == created
    assert harness.service.list(CalibrationCohortListQuery()).items == (
        CalibrationCohortSummary.from_cohort(created.cohort),
    )

    first_member_page = harness.service.list_members(
        CalibrationCohortMemberListQuery(
            cohort_id=created.cohort.cohort_id,
            limit=1,
        )
    )
    assert first_member_page.items == (created.members[0],)
    assert first_member_page.next_cursor == 0
    assert harness.service.list_members(
        CalibrationCohortMemberListQuery(
            cohort_id=created.cohort.cohort_id,
            cursor=first_member_page.next_cursor,
            limit=1,
        )
    ).items == (created.members[1],)

    active = _status(harness, first_members)
    assert active.snapshot.fanout_active_count == 2
    assert all(
        item.latest_attempt is not None
        and item.latest_attempt.procedure_state == "ready"
        for item in active.snapshot.statuses
    )

    duplicate = _command(
        "drag-q0-concurrent",
        source=harness.source,
        snapshot=harness.service.status(
            CalibrationStatusQuery(
                calibration_keys=(first_members[0].calibration_key,),
                fanout_scope="calibration-workers",
            )
        ),
        members=(first_members[0],),
    )
    with pytest.raises(BackendConflict, match="active attempt"):
        harness.service.create(duplicate)

    _close(harness, created.members[0], status="succeeded")
    successful = harness.service.status(
        CalibrationStatusQuery(
            calibration_keys=(first_members[0].calibration_key,),
            fanout_scope="calibration-workers",
        )
    )
    [successful_status] = successful.snapshot.statuses
    assert successful_status.latest_success is not None
    assert (
        successful_status.latest_success.attempt.procedure_run_id
        == created.members[0].procedure_run_id
    )

    second = harness.service.create(
        _command(
            "drag-q0-retry",
            source=harness.source,
            snapshot=successful,
            members=(first_members[0],),
        )
    )
    _close(harness, second.members[0], status="failed")
    failed_latest = harness.service.status(
        CalibrationStatusQuery(
            calibration_keys=(first_members[0].calibration_key,),
            fanout_scope="calibration-workers",
        )
    ).snapshot.statuses[0]
    assert failed_latest.latest_attempt is not None
    assert failed_latest.latest_attempt.closure is not None
    assert failed_latest.latest_attempt.closure.status == "failed"
    assert failed_latest.latest_success == successful_status.latest_success

    later_config = load_config().model_copy(update={"id": "later-config"})
    publish_config_revision(
        revision=ConfigRevision(
            source=DirectConfigRevisionSource(later_config),
            entry_id="later-calibration-config",
            actor="test",
        ),
        unit_of_work=harness.config_registry.write_unit_of_work,
        expected_generation=1,
    )
    # Exact replay is resolved before changed config, status, and fanout checks.
    assert harness.service.create(command) == created


def test_success_publication_requires_an_exact_anchored_merge_receipt(
    tmp_path: Path,
) -> None:
    harness = _harness(tmp_path)
    member = _member("q0", success_policy="published_result")
    created = harness.service.create(
        _command(
            "published-result",
            source=harness.source,
            snapshot=_status(harness, (member,)),
            members=(member,),
        )
    )
    _close(harness, created.members[0], status="succeeded")
    pending_status = _status(harness, (member,)).snapshot.statuses[0]
    pending = pending_status.latest_success
    assert pending is not None
    assert pending.base_config_source == harness.source
    assert pending.publication is None
    assert pending.is_effective is False

    with pytest.raises(
        CalibrationCohortConflict,
        match="calibration publication config operation",
    ):
        _attach_success_publication(harness, pending)
    projected = _status(harness, (member,)).snapshot.statuses[0]

    assert projected.latest_success == pending
    assert projected.latest_success is not None
    assert projected.latest_success.publication is None
    with harness.store.read_transaction() as connection:
        query, parameters = calibration_storage._status_rows_query(
            (member.calibration_key,)
        )
        plan = tuple(
            row["detail"]
            for row in connection.execute(
                f"EXPLAIN QUERY PLAN {query}",
                parameters,
            )
        )
    assert any(
        "SEARCH publications" in detail
        and "procedure_run_id=?" in detail
        and "LEFT-JOIN" in detail
        for detail in plan
    )
    assert not any("SCAN publications" in detail for detail in plan)


def test_status_query_bounds_rows_to_latest_attempt_and_success(
    tmp_path: Path,
) -> None:
    harness = _harness(tmp_path)
    member = _member("q0")
    first = harness.service.create(
        _command(
            "history-success",
            source=harness.source,
            snapshot=_status(harness, (member,)),
            members=(member,),
        )
    )
    _close(harness, first.members[0], status="succeeded")
    first_success = _status(harness, (member,)).snapshot.statuses[0].latest_success
    assert first_success is not None

    latest_member = first.members[0]
    for index in range(64):
        admitted = harness.service.create(
            _command(
                f"history-failure-{index}",
                source=harness.source,
                snapshot=_status(harness, (member,)),
                members=(member,),
            )
        )
        latest_member = admitted.members[0]
        _close(harness, latest_member, status="failed")

    with harness.store.read_transaction() as connection:
        rows = harness.store._status_rows_in_transaction(
            connection,
            (member.calibration_key,),
        )
        query, parameters = calibration_storage._status_rows_query(
            (member.calibration_key,)
        )
        status_plan = tuple(
            row["detail"]
            for row in connection.execute(
                f"EXPLAIN QUERY PLAN {query}",
                parameters,
            )
        )
        active_plan = tuple(
            row["detail"]
            for row in connection.execute(
                "EXPLAIN QUERY PLAN "
                + calibration_storage._ACTIVE_CALIBRATION_COUNT_SQL,
                ("calibration-workers",),
            )
        )
        projections = connection.execute(
            """
            SELECT members.sequence,
                   members.closure_status AS member_closure_status,
                   members.closed_at AS member_closed_at,
                   runs.closure_status AS run_closure_status,
                   runs.closed_at AS run_closed_at
            FROM calibration_cohort_members AS members
            JOIN procedure_runs AS runs
              ON runs.procedure_run_id = members.procedure_run_id
            WHERE members.calibration_key = ?
            ORDER BY members.sequence
            """,
            (member.calibration_key,),
        ).fetchall()

    assert len(rows) == 2
    assert tuple(row["closure_status"] for row in rows) == ("failed", "succeeded")
    assert any(
        "calibration_cohort_members_key_sequence" in detail for detail in status_plan
    )
    assert any(
        "calibration_cohort_members_success_key_sequence" in detail
        for detail in status_plan
    )
    assert any(
        "SEARCH members USING INTEGER PRIMARY KEY (rowid=?)" in detail
        for detail in status_plan
    )
    assert not any("SCAN members" in detail for detail in status_plan)
    assert any("procedure_runs_state_sequence" in detail for detail in active_plan)
    assert len(projections) == 65
    assert all(
        row["member_closure_status"] == row["run_closure_status"]
        and row["member_closed_at"] == row["run_closed_at"]
        for row in projections
    )
    sequences = tuple(row["sequence"] for row in projections)
    assert sequences == tuple(sorted(set(sequences)))
    status = _status(harness, (member,)).snapshot.statuses[0]
    assert status.latest_attempt is not None
    assert status.latest_attempt.attempt.procedure_run_id == (
        latest_member.procedure_run_id
    )
    assert status.latest_attempt.closure is not None
    assert status.latest_attempt.closure.status == "failed"
    assert status.latest_success == first_success


def test_status_clock_is_sampled_after_the_database_snapshot(
    tmp_path: Path,
) -> None:
    harness = _harness(tmp_path)
    member = _member("q0")
    created = harness.service.create(
        _command(
            "clock-order",
            source=harness.source,
            snapshot=_status(harness, (member,)),
            members=(member,),
        )
    )
    sampled_at = harness.now[0]

    def sample_while_run_closes() -> datetime:
        observed_at = harness.now[0]
        _close(harness, created.members[0], status="succeeded")
        return observed_at

    concurrent = CalibrationCohortService(
        harness.store,
        harness.automation,
        harness.config_registry,
        clock=sample_while_run_closes,
    )
    snapshot = concurrent.status(
        CalibrationStatusQuery(
            calibration_keys=(member.calibration_key,),
            fanout_scope="calibration-workers",
        )
    ).snapshot

    assert snapshot.observed_at == sampled_at
    assert snapshot.fanout_active_count == 1
    assert snapshot.statuses[0].latest_attempt is not None
    assert snapshot.statuses[0].latest_attempt.procedure_state == "ready"
    assert snapshot.statuses[0].latest_success is None
    closed = _status(harness, (member,)).snapshot.statuses[0]
    assert closed.latest_success is not None
    assert closed.latest_success.succeeded_at > snapshot.observed_at


@pytest.mark.parametrize(
    "state",
    ("ready", "leased", "attention_required"),
)
def test_cohort_rejects_every_nonclosed_latest_attempt(
    tmp_path: Path,
    state: ProcedureRunState,
) -> None:
    harness = _harness(tmp_path)
    member = _member("q0")
    created = harness.service.create(
        _command(
            "active-attempt",
            source=harness.source,
            snapshot=_status(harness, (member,)),
            members=(member,),
        )
    )
    _move_to_active_state(harness, created.members[0], state)
    current = _status(harness, (member,))
    assert current.snapshot.statuses[0].latest_attempt is not None
    assert current.snapshot.statuses[0].latest_attempt.procedure_state == state

    with pytest.raises(BackendConflict, match="active attempt"):
        harness.service.create(
            _command(
                f"duplicate-{state}",
                source=harness.source,
                snapshot=current,
                members=(member,),
            )
        )

    assert len(harness.service.list(CalibrationCohortListQuery()).items) == 1
    assert len(harness.automation.list(ProcedureRunListQuery()).items) == 1


def test_cohort_query_projections_are_exactly_validated(tmp_path: Path) -> None:
    harness = _harness(tmp_path)
    member = _member("q0")
    created = harness.service.create(
        _command(
            "projection-validation",
            source=harness.source,
            snapshot=_status(harness, (member,)),
            members=(member,),
        )
    )
    with harness.store.write_transaction() as connection:
        connection.execute(
            "UPDATE calibration_cohorts SET fanout_scope = ? WHERE cohort_id = ?",
            ("drifted-scope", created.cohort.cohort_id),
        )
    with pytest.raises(CalibrationCohortStoreError, match="projection drifted"):
        harness.store.read(created.cohort.cohort_id)

    with harness.store.write_transaction() as connection:
        connection.execute(
            "UPDATE calibration_cohorts SET fanout_scope = ? WHERE cohort_id = ?",
            (created.cohort.spec.fanout_scope, created.cohort.cohort_id),
        )
        connection.execute(
            """
            UPDATE calibration_cohort_members
            SET member_id = ?
            WHERE cohort_id = ? AND member_index = 0
            """,
            ("drifted-member", created.cohort.cohort_id),
        )
    with (
        harness.store.read_transaction() as connection,
        pytest.raises(
            CalibrationCohortStoreError,
            match=r"member.*projection drifted",
        ),
    ):
        harness.store.list_members_in_transaction(
            connection,
            created.cohort.cohort_id,
        )


def test_cohort_create_rejects_stale_config_status_and_fanout(
    tmp_path: Path,
) -> None:
    harness = _harness(tmp_path)
    q0 = _member("q0")
    q1 = _member("q1")
    stale_q0_scope_a = _status(harness, (q0,), scope="scope-a")
    stale_q1_scope_b = _status(harness, (q1,), scope="scope-b")
    q1_scope_a = _status(harness, (q1,), scope="scope-a")
    admitted = harness.service.create(
        _command(
            "scope-a-q1",
            source=harness.source,
            snapshot=q1_scope_a,
            members=(q1,),
        )
    )

    with pytest.raises(BackendConflict, match="fanout activity changed"):
        harness.service.create(
            _command(
                "stale-fanout",
                source=harness.source,
                snapshot=stale_q0_scope_a,
                members=(q0,),
            )
        )
    with pytest.raises(BackendConflict, match="status observations changed"):
        harness.service.create(
            _command(
                "stale-status",
                source=harness.source,
                snapshot=stale_q1_scope_b,
                members=(q1,),
            )
        )

    forged_fields = (
        ("entry", {"entry_id": "forged-entry"}),
        ("ref", {"config_ref": "config-registry/configs/forged.config.json"}),
        ("hash", {"content_hash": "sha256:" + "9" * 64}),
    )
    for label, updates in forged_fields:
        forged_source = harness.source.model_copy(update=updates)
        with pytest.raises(BackendConflict, match="config registry source changed"):
            harness.service.create(
                _command(
                    f"forged-config-{label}",
                    source=forged_source,
                    snapshot=_status(harness, (q0,), scope="scope-c"),
                    members=(q0,),
                )
            )

    assert harness.service.list(CalibrationCohortListQuery()).items == (
        CalibrationCohortSummary.from_cohort(admitted.cohort),
    )
    assert len(harness.automation.list(ProcedureRunListQuery()).items) == 1


def test_cohort_create_rolls_back_every_member_run_on_member_write_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _harness(tmp_path)
    members = (_member("q0"), _member("q1"))
    command = _command(
        "atomic-write",
        source=harness.source,
        snapshot=_status(harness, members),
        members=members,
    )
    insert = harness.store.insert_member_in_transaction

    def fail_second_member(
        connection: sqlite3.Connection,
        member: CalibrationCohortMember,
    ) -> None:
        if member.index == 1:
            raise CalibrationCohortConflict("injected member write failure")
        insert(connection, member)

    monkeypatch.setattr(
        harness.store,
        "insert_member_in_transaction",
        fail_second_member,
    )
    with pytest.raises(BackendConflict, match="injected member write failure"):
        harness.service.create(command)

    assert harness.automation.list(ProcedureRunListQuery()).items == ()
    assert harness.service.list(CalibrationCohortListQuery()).items == ()
    with pytest.raises(BackendNotFound):
        harness.service.get(CalibrationCohortGetQuery(cohort_id=command.cohort_id))


def test_cohort_refuses_to_adopt_a_preexisting_member_run(tmp_path: Path) -> None:
    harness = _harness(tmp_path)
    member = _member("q0")
    command = _command(
        "preexisting-run",
        source=harness.source,
        snapshot=_status(harness, (member,)),
        members=(member,),
    )
    request_key = calibration_cohort_member_request_key(command.cohort_id, member)
    existing = harness.automation.submit(
        ProcedureSubmitCommand(
            definition=member.procedure,
            request_key=request_key,
            intent=member.intent,
        )
    ).run

    with pytest.raises(BackendConflict, match="already has a durable run"):
        harness.service.create(command)

    assert harness.automation.list(ProcedureRunListQuery()).items == (existing,)
    assert harness.service.list(CalibrationCohortListQuery()).items == ()


def test_cohort_rolls_back_after_the_last_procedure_run_insert_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _harness(tmp_path)
    members = (_member("q0"), _member("q1"))
    command = _command(
        "procedure-insert-failure",
        source=harness.source,
        snapshot=_status(harness, members),
        members=members,
    )
    insert = harness.automation_store.insert_run_in_transaction
    calls = 0

    def fail_last_run(
        connection: sqlite3.Connection,
        run: ProcedureRun,
    ) -> None:
        nonlocal calls
        calls += 1
        if calls == len(members):
            raise AutomationConflict("injected procedure run write failure")
        insert(connection, run)

    monkeypatch.setattr(
        harness.automation_store,
        "insert_run_in_transaction",
        fail_last_run,
    )
    with pytest.raises(BackendConflict, match="injected procedure run write failure"):
        harness.service.create(command)

    assert harness.automation.list(ProcedureRunListQuery()).items == ()
    assert harness.service.list(CalibrationCohortListQuery()).items == ()


def test_calibration_cohort_http_sqlite_vertical_replays_after_restart(
    tmp_path: Path,
) -> None:
    member = _member("q0", success_policy="published_result")
    policy = _publication_policy(member.definition)
    cohort_id = "foo/members"
    encoded_id = quote(cohort_id, safe="")
    with (
        LocalDaemonRuntime(tmp_path, bootstrap_config=load_config()) as runtime,
        TestClient(runtime.app()) as client,
    ):
        active = runtime.application.config.get_active_config()
        source = CalibrationConfigSourceRef(
            selector="active",
            entry_id=active.entry.id,
            config_ref=active.entry.config_ref,
            content_hash=active.entry.content_hash,
            registry_generation=active.activation.generation,
        )
        status_response = client.post(
            "/api/v1/calibration-status/query",
            json=CalibrationStatusQuery(
                calibration_keys=(member.calibration_key,),
                fanout_scope="http-workers",
            ).model_dump(mode="json"),
        )
        assert status_response.status_code == 200
        status = CalibrationStatusReceipt.model_validate(status_response.json())
        command = _command(
            cohort_id,
            source=source,
            snapshot=status,
            members=(member,),
            automatic_publication=policy,
        )
        response = client.post(
            "/api/v1/calibration-cohorts",
            json=command.model_dump(mode="json"),
        )
        assert response.status_code == 201
        created = CalibrationCohortCreateReceipt.model_validate(response.json())

        get_response = client.get(f"/api/v1/calibration-cohorts/by-id/{encoded_id}")
        assert get_response.status_code == 200
        assert (
            CalibrationCohortGetReceipt.model_validate(get_response.json()).cohort
            == created.cohort
        )
        member_response = client.get(
            f"/api/v1/calibration-cohort-members/by-cohort/{encoded_id}"
        )
        assert member_response.status_code == 200
        assert member_response.json()["items"][0]["procedure_run_id"] == (
            created.members[0].procedure_run_id
        )
        acquired = runtime.application.automation.acquire_lease(
            ProcedureWorkerLeaseAcquireCommand(
                procedure_run_id=created.members[0].procedure_run_id,
                worker_id="http-publication-worker",
                expected_run_revision=1,
            )
        )
        runtime.application.automation.close(
            ProcedureCloseCommand(
                procedure_run_id=created.members[0].procedure_run_id,
                lease_token=acquired.lease.lease_token,
                expected_run_revision=acquired.run.revision,
                status="succeeded",
            )
        )
        ready_response = client.post(
            "/api/v1/calibration-publications/ready/query",
            json=CalibrationPublicationReadyQuery(
                capabilities=(policy,),
            ).model_dump(mode="json"),
        )
        assert ready_response.status_code == 200
        ready = CalibrationPublicationReadyPage.model_validate(ready_response.json())
        assert ready.items[0].cohort.cohort_id == cohort_id
        publication_response = client.get(
            f"/api/v1/calibration-publications/by-cohort/{encoded_id}"
        )
        assert publication_response.status_code == 200
        finalization = CalibrationPublicationGetReceipt.model_validate(
            publication_response.json()
        ).finalization
        assert finalization.state == "ready"
        attention_command = CalibrationPublicationAttentionCommand(
            cohort_id=cohort_id,
            policy=policy,
            expected_finalization_revision=finalization.revision,
            actor="http-finalizer",
            reason="test deterministic failure",
        )
        attention_response = client.post(
            f"/api/v1/calibration-publication-attentions/{encoded_id}",
            json=attention_command.model_dump(mode="json"),
        )
        attention = CalibrationPublicationAttentionReceipt.model_validate(
            attention_response.json()
        ).finalization
        retry_response = client.post(
            f"/api/v1/calibration-publication-retries/{encoded_id}",
            json=CalibrationPublicationRetryCommand(
                cohort_id=cohort_id,
                policy=policy,
                expected_finalization_revision=attention.revision,
                actor="operator",
                reason="test retry",
            ).model_dump(mode="json"),
        )
        retried = CalibrationPublicationRetryReceipt.model_validate(
            retry_response.json()
        ).finalization
        defer_response = client.post(
            f"/api/v1/calibration-publication-deferrals/{encoded_id}",
            json=CalibrationPublicationDeferCommand(
                cohort_id=cohort_id,
                policy=policy,
                expected_finalization_revision=retried.revision,
                retry_after_seconds=30,
                reason="test outage",
            ).model_dump(mode="json"),
        )
        deferred = CalibrationPublicationDeferReceipt.model_validate(
            defer_response.json()
        ).finalization
        assert deferred.state == "ready"
        assert deferred.attempt_count == 1

    with (
        LocalDaemonRuntime(tmp_path, bootstrap_config=load_config()) as restarted,
        TestClient(restarted.app()) as client,
    ):
        replay = client.post(
            "/api/v1/calibration-cohorts",
            json=command.model_dump(mode="json"),
        )
        assert replay.status_code == 201
        assert CalibrationCohortCreateReceipt.model_validate(replay.json()) == created
        procedures = ProcedureRunPage.model_validate(
            client.get("/api/v1/procedures").json()
        )
        assert len(procedures.items) == 1
        assert procedures.items[0].procedure_run_id == (
            created.members[0].procedure_run_id
        )
        publication = CalibrationPublicationGetReceipt.model_validate(
            client.get(
                f"/api/v1/calibration-publications/by-cohort/{encoded_id}"
            ).json()
        ).finalization
        assert publication == deferred
