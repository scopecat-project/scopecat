from __future__ import annotations

import sqlite3
from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from threading import Barrier
from typing import Literal, cast

import pytest
from scopecat.adaptive_domains import DomainProposalAttempt, ResolvedDomainFragment
from scopecat.daemon.points import (
    AcceptedRunPointView,
    RunDomainDecisionCommand,
    RunDomainEnqueueCommand,
    RunDomainFragmentInput,
    RunDomainProposalAttemptView,
    RunPointCoordinateValue,
    RunPointPlanCloseCommand,
)
from scopecat.daemon.wire import RunDomainJobTransitionItem
from scopecat.kernel.points import PointProposalAttempt
from scopecat.kernel.quantity import Quantity
from scopecat.measurements import recording_arrow
from scopecat.records.execution import (
    DomainExecutionReceipt,
    DomainJobCheckpoint,
    DomainJobCheckpointTransition,
    DomainJobInvocationTransition,
    DomainJobTerminalTransition,
    RecoveryGroupCompletion,
)
from scopecat.records.measurement import (
    MeasurementDatasetSchema,
    MeasurementDimension,
    MeasurementPointCloudPointDomain,
    MeasurementRecord,
    MeasurementScalar,
    MeasurementUnavailable,
    MeasurementVariable,
)
from scopecat.records.measurement_recording import (
    MeasurementDatasetAppend,
    MeasurementDatasetHeader,
    MeasurementDatasetSeal,
    measurement_fragment_content_hash,
)
from scopecat_testkit.domain import domain_execution_identity

from scopecat_server.storage.sqlite.connection import SQLiteDatabase
from scopecat_server.storage.sqlite.execution import (
    ExecutionStateConflict,
    SQLiteDomainJobTransitions,
    SQLiteMeasurementDatasetRepository,
    SQLiteRecoveryGroups,
    SQLiteRunCoverage,
    SQLiteRunPointLedger,
)
from scopecat_server.storage.sqlite.project_store import SQLiteProjectStore
from scopecat_server.storage.sqlite.run_repository import SQLiteRunRepository


def _runs(tmp_path: Path) -> SQLiteRunRepository:
    sqlite = SQLiteDatabase(tmp_path / "control.sqlite3")
    SQLiteProjectStore(
        sqlite,
        tmp_path / "objects",
    ).bootstrap()
    runs = SQLiteRunRepository(
        sqlite,
        tmp_path / "objects",
    )
    return runs


@contextmanager
def _sqlite_transaction(
    runs: SQLiteRunRepository,
) -> Generator[sqlite3.Connection]:
    connection = sqlite3.connect(
        runs.database,
        isolation_level=None,
        timeout=5,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("BEGIN IMMEDIATE")
    try:
        yield connection
    except BaseException:
        connection.rollback()
        raise
    else:
        connection.commit()
    finally:
        connection.close()


def test_adaptive_domain_ledger_persists_idempotent_decisions_and_closure(
    tmp_path: Path,
) -> None:
    runs = _runs(tmp_path)
    run_id = "adaptive-ledger-run"
    with _sqlite_transaction(runs) as connection:
        connection.execute(
            """
            INSERT INTO scheduler_runs(
                submission_id, run_id, state, updated_at, admission_json
            )
            VALUES (?, ?, 'queued', ?, '{}')
            """,
            ("adaptive-ledger-submission", run_id, datetime.now(UTC).isoformat()),
        )
        ledger = SQLiteRunPointLedger(runs, run_id=run_id)
        initialized = ledger.initialize_in_transaction(
            connection,
            operation_id="initialize",
            initial_point_count=2,
            point_limit=5,
            plan_closed=False,
        )
        first_command = _domain_decision_command(
            operation_id="decision-1",
            point_start=2,
            point_count=2,
        )
        accepted = ledger.append_decision_in_transaction(
            connection,
            first_command,
        )
        retry = ledger.append_decision_in_transaction(
            connection,
            first_command,
        )
        rejected = ledger.append_decision_in_transaction(
            connection,
            _domain_decision_command(
                operation_id="decision-2",
                point_start=4,
                outcome="rejected",
                reason="stale optimizer state",
            ),
        )
        with pytest.raises(ExecutionStateConflict, match="point prefix"):
            ledger.append_decision_in_transaction(
                connection,
                _domain_decision_command(
                    operation_id="noncontiguous",
                    point_start=3,
                ),
            )
        close = RunPointPlanCloseCommand(
            lease_id="lease-1",
            operation_id="close",
            based_on_completed_point_count=4,
            reason="optimizer converged",
        )
        closed = ledger.close_in_transaction(
            connection,
            close,
            completed_point_count=4,
        )
        close_retry = ledger.close_in_transaction(
            connection,
            close,
            completed_point_count=4,
        )

    assert initialized.accepted_point_count == 2
    assert accepted == retry
    assert accepted.accepted_point_start == 2
    assert accepted.accepted_point_count == 2
    assert rejected.outcome == "rejected"
    assert closed == close_retry
    assert closed.accepted_point_count == 4
    assert closed.decision_count == 2
    assert closed.plan_closed
    assert closed.stop_reason == "optimizer converged"
    assert SQLiteRunPointLedger(runs, run_id=run_id).read() == closed
    with runs.sqlite.read_transaction() as connection:
        row = connection.execute(
            """
            SELECT COUNT(*) AS point_count
            FROM execution_run_points
            WHERE run_id = ?
            """,
            (run_id,),
        ).fetchone()
    assert row is not None
    assert row["point_count"] == 2


def test_domain_job_transitions_commit_monotonic_state_through_terminal(
    tmp_path: Path,
) -> None:
    runs = _runs(tmp_path)
    run_id = "domain-job-transition-run"
    ledger = SQLiteDomainJobTransitions(runs, run_id=run_id)
    intent, execution_id = domain_execution_identity(
        run_id=run_id,
        logical_compute_node_id="domain.batch-0",
        invocation_id="invocation-1",
        target_intent={"provider_profile": "fast-readout"},
    )
    first_checkpoint = DomainJobCheckpoint(
        execution_key=execution_id.execution_key,
        job_id="provider-job",
        revision=1,
        resume_token={"cursor": "poll-1"},
        progress={"status": "submitted"},
    )
    first_command = RunDomainJobTransitionItem(
        logical_compute_node_id="domain.batch-0",
        point_ordinals=(0, 1),
        transition=DomainJobCheckpointTransition(checkpoint=first_checkpoint),
    )
    invocation_command = first_command.model_copy(
        update={
            "transition": DomainJobInvocationTransition(
                execution_id=execution_id,
                intent=intent,
            )
        }
    )
    second_checkpoint = first_checkpoint.model_copy(
        update={
            "revision": 2,
            "resume_token": {"cursor": "poll-2"},
            "progress": {"status": "results_ready"},
        }
    )
    second_command = first_command.model_copy(
        update={
            "transition": DomainJobCheckpointTransition(checkpoint=second_checkpoint)
        }
    )
    terminal_command = first_command.model_copy(
        update={
            "transition": DomainJobTerminalTransition(
                receipt=DomainExecutionReceipt(
                    execution_key=first_checkpoint.execution_key,
                    status="completed",
                    result_fingerprint="results-v1",
                    result_count=2,
                )
            )
        }
    )
    with _sqlite_transaction(runs) as connection:
        connection.execute(
            """
            INSERT INTO scheduler_runs(
                submission_id, run_id, state, updated_at, admission_json
            )
            VALUES (?, ?, 'leased', ?, '{}')
            """,
            ("domain-job-submission", run_id, datetime.now(UTC).isoformat()),
        )
        with pytest.raises(ExecutionStateConflict, match="durable invocation"):
            ledger.commit_in_transaction(connection, first_command)
        with pytest.raises(ExecutionStateConflict, match="run or node"):
            ledger.commit_in_transaction(
                connection,
                invocation_command.model_copy(
                    update={
                        "transition": DomainJobInvocationTransition(
                            execution_id=execution_id.model_copy(
                                update={"run_id": "another-run"}
                            ),
                            intent=intent,
                        )
                    }
                ),
            )
        invocation, invocation_inserted = ledger.commit_in_transaction(
            connection,
            invocation_command,
        )
        invocation_retry, invocation_retry_inserted = ledger.commit_in_transaction(
            connection,
            invocation_command,
        )
        [invocation_state] = ledger.read_current_in_transaction(
            connection,
            limit=10,
        ).items
        assert invocation_state.state == "invocation_unknown"
        assert invocation_state.transition_count == 1
        assert isinstance(
            invocation_state.latest_transition,
            DomainJobInvocationTransition,
        )
        first, inserted = ledger.commit_in_transaction(connection, first_command)
        retried, retry_inserted = ledger.commit_in_transaction(
            connection,
            first_command,
        )
        second, second_inserted = ledger.commit_in_transaction(
            connection,
            second_command,
        )
        [pending_state] = ledger.read_current_in_transaction(
            connection,
            limit=10,
        ).items
        assert pending_state.state == "pending"
        assert pending_state.transition_count == 3
        assert pending_state.invocation.intent.target_intent == {
            "provider_profile": "fast-readout"
        }
        assert pending_state.latest_transition == second_command.transition
        with pytest.raises(ExecutionStateConflict, match="conflicts"):
            ledger.commit_in_transaction(
                connection,
                first_command.model_copy(
                    update={
                        "transition": DomainJobCheckpointTransition(
                            checkpoint=first_checkpoint.model_copy(
                                update={"progress": {"status": "different"}}
                            )
                        )
                    }
                ),
            )
        with pytest.raises(ExecutionStateConflict, match="job identity"):
            ledger.commit_in_transaction(
                connection,
                second_command.model_copy(
                    update={
                        "transition": DomainJobCheckpointTransition(
                            checkpoint=second_checkpoint.model_copy(
                                update={"revision": 3, "job_id": "another-job"}
                            )
                        )
                    }
                ),
            )
        terminal, terminal_inserted = ledger.commit_in_transaction(
            connection,
            terminal_command,
        )
        terminal_retry, terminal_retry_inserted = ledger.commit_in_transaction(
            connection,
            terminal_command,
        )
        [terminal_state] = ledger.read_current_in_transaction(
            connection,
            limit=10,
        ).items
        assert terminal_state.state == "terminal"
        assert terminal_state.transition_count == 4
        assert terminal_state.latest_transition == terminal_command.transition
        with pytest.raises(ExecutionStateConflict, match="follows its terminal"):
            ledger.commit_in_transaction(
                connection,
                second_command.model_copy(
                    update={
                        "transition": DomainJobCheckpointTransition(
                            checkpoint=second_checkpoint.model_copy(
                                update={"revision": 3}
                            )
                        )
                    }
                ),
            )

    assert invocation_inserted and inserted and second_inserted and terminal_inserted
    assert not invocation_retry_inserted
    assert not retry_inserted and not terminal_retry_inserted
    assert invocation_retry == invocation
    assert retried == first
    assert terminal_retry == terminal
    assert first.sequence > invocation.sequence
    assert second.sequence > first.sequence
    assert terminal.sequence > second.sequence
    latest = ledger.read(limit=1)
    assert [item.transition.kind for item in latest.items] == ["terminal"]
    assert latest.next_cursor == terminal.sequence
    previous = SQLiteDomainJobTransitions(runs, run_id=run_id).read(
        limit=1,
        before=latest.next_cursor,
    )
    [previous_item] = previous.items
    assert isinstance(previous_item.transition, DomainJobCheckpointTransition)
    assert previous_item.transition.checkpoint.revision == 2
    assert previous.next_cursor == second.sequence


def test_domain_job_current_states_page_by_latest_transition(
    tmp_path: Path,
) -> None:
    runs = _runs(tmp_path)
    run_id = "domain-job-state-page-run"
    ledger = SQLiteDomainJobTransitions(runs, run_id=run_id)
    with _sqlite_transaction(runs) as connection:
        connection.execute(
            """
            INSERT INTO scheduler_runs(
                submission_id, run_id, state, updated_at, admission_json
            )
            VALUES (?, ?, 'leased', ?, '{}')
            """,
            ("domain-job-state-page", run_id, datetime.now(UTC).isoformat()),
        )
        for index in range(3):
            intent, execution_id = domain_execution_identity(
                run_id=run_id,
                logical_compute_node_id=f"domain.batch-{index}",
                invocation_id=f"invocation-{index}",
            )
            ledger.commit_in_transaction(
                connection,
                RunDomainJobTransitionItem(
                    logical_compute_node_id=execution_id.logical_compute_node_id,
                    point_ordinals=(index,),
                    transition=DomainJobInvocationTransition(
                        execution_id=execution_id,
                        intent=intent,
                    ),
                ),
            )

    latest = ledger.read_current(limit=2)
    assert [item.invocation.execution_id.invocation_id for item in latest.items] == [
        "invocation-1",
        "invocation-2",
    ]
    assert latest.next_cursor == latest.items[0].invocation_sequence
    previous = ledger.read_current(limit=2, before=latest.next_cursor)
    assert [item.invocation.execution_id.invocation_id for item in previous.items] == [
        "invocation-0"
    ]
    assert previous.next_cursor is None


def _domain_decision_command(
    *,
    operation_id: str,
    point_start: int,
    point_count: int = 1,
    outcome: Literal["accepted", "rejected"] = "accepted",
    reason: str | None = None,
) -> RunDomainDecisionCommand:
    fragment = ResolvedDomainFragment.points(
        tuple(
            {"frequency": Quantity(5.2 + index / 10, "GHz")}
            for index in range(point_count)
        )
    )
    proposal = DomainProposalAttempt(
        fragment,
        region_ids=("region-0",),
        based_on_region_revisions={"region-0": 2},
    )
    accepted_points = tuple(
        AcceptedRunPointView(
            point_index=point_start + index,
            coordinates=cast("dict[str, RunPointCoordinateValue]", row),
            proposal_fingerprint=PointProposalAttempt(
                row,
                source="optimizer",
                region_id="region-0",
                domain_proposal_fingerprint=proposal.proposal_fingerprint,
                based_on_region_revision=2,
            ).proposal_fingerprint,
            source="optimizer",
            region_id="region-0",
            domain_proposal_fingerprint=proposal.proposal_fingerprint,
        )
        for index, row in enumerate(fragment.rows())
    )
    return RunDomainDecisionCommand(
        lease_id="lease-1",
        operation_id=operation_id,
        proposal=RunDomainProposalAttemptView.from_proposal(proposal),
        outcome=outcome,
        accepted_points=accepted_points if outcome == "accepted" else (),
        reason=reason,
    )


def test_operator_domain_queue_is_fifo_bounded_and_resolved_by_decisions(
    tmp_path: Path,
) -> None:
    runs = _runs(tmp_path)
    run_id = "operator-queue-run"
    fragment = ResolvedDomainFragment.points(({"frequency": Quantity(5.15, "GHz")},))
    enqueue = RunDomainEnqueueCommand(
        request_id="queue-1",
        coordinate_mode="free",
        region_scope="current",
        fragment=RunDomainFragmentInput.from_fragment(fragment),
    )
    second_enqueue = enqueue.model_copy(update={"request_id": "queue-2"})
    with _sqlite_transaction(runs) as connection:
        connection.execute(
            """
            INSERT INTO scheduler_runs(
                submission_id, run_id, state, updated_at, admission_json
            )
            VALUES (?, ?, 'leased', ?, '{}')
            """,
            ("operator-queue-submission", run_id, datetime.now(UTC).isoformat()),
        )
        ledger = SQLiteRunPointLedger(runs, run_id=run_id)
        ledger.initialize_in_transaction(
            connection,
            operation_id="initialize",
            initial_point_count=1,
            point_limit=3,
            plan_closed=False,
        )
        first, first_created = ledger.enqueue_in_transaction(
            connection,
            enqueue,
            resolved_fragment=fragment,
            region_count=1,
        )
        retry, retry_created = ledger.enqueue_in_transaction(
            connection,
            enqueue,
            resolved_fragment=fragment,
            region_count=1,
        )
        second, _ = ledger.enqueue_in_transaction(
            connection,
            second_enqueue,
            resolved_fragment=fragment,
            region_count=1,
        )
        with pytest.raises(ExecutionStateConflict, match="remaining budget"):
            ledger.enqueue_in_transaction(
                connection,
                enqueue.model_copy(update={"request_id": "queue-3"}),
                resolved_fragment=fragment,
                region_count=1,
            )

    assert first == retry
    assert first_created
    assert not retry_created
    assert ledger.next_pending() == first
    proposal = DomainProposalAttempt(
        fragment,
        region_ids=("region-0",),
        source="operator",
    )
    [row] = fragment.rows()
    normalized = PointProposalAttempt(
        row,
        source="operator",
        region_id="region-0",
        domain_proposal_fingerprint=proposal.proposal_fingerprint,
    )
    with _sqlite_transaction(runs) as connection:
        decision = ledger.append_decision_in_transaction(
            connection,
            RunDomainDecisionCommand(
                lease_id="lease-1",
                operation_id="decision-1",
                operator_request_id=first.request.request_id,
                proposal=RunDomainProposalAttemptView.from_proposal(proposal),
                outcome="accepted",
                accepted_points=(
                    AcceptedRunPointView(
                        point_index=1,
                        coordinates=cast("dict[str, RunPointCoordinateValue]", row),
                        proposal_fingerprint=normalized.proposal_fingerprint,
                        source="operator",
                        region_id="region-0",
                        domain_proposal_fingerprint=proposal.proposal_fingerprint,
                    ),
                ),
            ),
        )
        closed = ledger.close_in_transaction(
            connection,
            RunPointPlanCloseCommand(
                lease_id="lease-1",
                operation_id="close",
                based_on_completed_point_count=2,
                reason="operator sweep complete",
            ),
            completed_point_count=2,
        )

    queue = ledger.queue()
    assert decision.accepted_point_start == 1
    assert decision.accepted_point_count == 1
    assert decision.operator_request_id == first.request.request_id
    assert closed.plan_closed
    assert queue.items[0].status == "accepted"
    assert queue.items[0].accepted_point_start == 1
    assert queue.items[0].accepted_point_count == 1
    assert queue.items[1].request.request_id == second.request.request_id
    assert queue.items[1].status == "cancelled"
    assert queue.items[1].reason == "point plan closed: operator sweep complete"


def _header(
    run_id: str,
    *,
    point_count: int = 1,
) -> MeasurementDatasetHeader:
    return MeasurementDatasetHeader(
        run_id=run_id,
        recording_contract_fingerprint="recording.v1",
        dataset_schema=MeasurementDatasetSchema(
            dataset_id="raw-measurements",
            point_domain=MeasurementPointCloudPointDomain(columns=()),
            dimensions=[
                MeasurementDimension(id="point", kind="point", size=point_count)
            ],
            variables=(
                MeasurementVariable(
                    id="signal",
                    role="observable",
                    dtype="float64",
                    unit="ratio",
                    dims=("point",),
                ),
            ),
            primary_observables=("signal",),
        ),
        expected_record_count=point_count,
        record_count_limit=point_count,
    )


def _append(
    header: MeasurementDatasetHeader,
    *,
    point_index: int = 0,
    acquisition_start: int | None = None,
    value: float = 1,
) -> MeasurementDatasetAppend:
    return MeasurementDatasetAppend(
        run_id=header.run_id,
        header_content_hash=header.content_hash,
        acquisition_start=(
            point_index if acquisition_start is None else acquisition_start
        ),
        records=(
            MeasurementRecord(
                run_id=header.run_id,
                logical_point_id=f"point-{point_index}",
                point_index=point_index,
                coordinates={},
                observables={
                    "signal": MeasurementScalar.create(
                        dtype="float64",
                        value=value,
                        unit="ratio",
                    )
                },
            ),
        ),
    )


def _seal(
    header: MeasurementDatasetHeader,
    append: MeasurementDatasetAppend,
) -> MeasurementDatasetSeal:
    return MeasurementDatasetSeal(
        run_id=append.run_id,
        header_content_hash=header.content_hash,
        record_count=append.acquisition_start + len(append.records),
        fragment_record_count=len(append.records),
        fragment_content_hash=measurement_fragment_content_hash(
            header_content_hash=header.content_hash,
            record_content_hashes=append.record_content_hashes,
        ),
    )


def _commit_append(
    runs: SQLiteRunRepository,
    repository: SQLiteMeasurementDatasetRepository,
    append: MeasurementDatasetAppend,
) -> None:
    prepared = repository.prepare_append(append)
    with _sqlite_transaction(runs) as connection:
        repository.append_prepared_in_transaction(
            connection,
            prepared,
            segment_id=_segment_id(append.run_id),
        )
        SQLiteRecoveryGroups(runs, run_id=append.run_id).append_in_transaction(
            connection,
            (
                RecoveryGroupCompletion(
                    schedule_fingerprint="test-measurement-schedule",
                    group_id=f"test:{append.acquisition_start}",
                    point_indices=tuple(
                        record.point_index for record in append.records
                    ),
                    output_kind="measurement",
                    record_content_hashes=append.record_content_hashes,
                ),
            ),
            segment_id=_segment_id(append.run_id),
        )


def _commit_acquisition(
    runs: SQLiteRunRepository,
    repository: SQLiteMeasurementDatasetRepository,
    append: MeasurementDatasetAppend,
) -> None:
    prepared = repository.prepare_append(append)
    with _sqlite_transaction(runs) as connection:
        repository.append_prepared_in_transaction(
            connection,
            prepared,
            segment_id=_segment_id(append.run_id),
        )


def _commit_header(
    runs: SQLiteRunRepository,
    repository: SQLiteMeasurementDatasetRepository,
    header: MeasurementDatasetHeader,
) -> None:
    prepared = repository.prepare_header(header)
    with _sqlite_transaction(runs) as connection:
        _ensure_run_owner(connection, header.run_id)
        repository.header_prepared_in_transaction(
            connection,
            prepared,
            segment_id=_segment_id(header.run_id),
        )


def _segment_id(run_id: str, ordinal: int = 0) -> str:
    return f"segment:{run_id}:{ordinal}"


def _ensure_run_owner(connection: sqlite3.Connection, run_id: str) -> None:
    connection.execute(
        """
        INSERT OR IGNORE INTO runs(run_id, created_at, config_content_hash)
        VALUES (?, ?, ?)
        """,
        (run_id, datetime.now(UTC).isoformat(), f"sha256:{'0' * 64}"),
    )
    connection.execute(
        """
        INSERT OR IGNORE INTO scheduler_runs(
            submission_id, run_id, state, updated_at, admission_json
        )
        VALUES (?, ?, 'leased', ?, '{}')
        """,
        (f"submission:{run_id}", run_id, datetime.now(UTC).isoformat()),
    )
    connection.execute(
        """
        INSERT OR IGNORE INTO run_execution_segments(
            segment_id, run_id, ordinal, executor_id,
            run_contract_fingerprint, started_at, start_point_count
        )
        VALUES (?, ?, 0, 'test-executor', ?, ?, 0)
        """,
        (
            _segment_id(run_id),
            run_id,
            "0" * 64,
            datetime.now(UTC).isoformat(),
        ),
    )


def _commit_seal(
    runs: SQLiteRunRepository,
    repository: SQLiteMeasurementDatasetRepository,
    seal: MeasurementDatasetSeal,
) -> None:
    prepared = repository.prepare_seal(seal)
    with _sqlite_transaction(runs) as connection:
        repository.seal_prepared_in_transaction(
            connection,
            prepared,
            segment_id=_segment_id(seal.run_id),
        )


def test_measurement_repository_reuses_schema_hash_for_appends(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runs = _runs(tmp_path)
    repository = SQLiteMeasurementDatasetRepository(runs, run_id="run-cached-schema")
    header = _header("run-cached-schema", point_count=2)
    original = recording_arrow.measurement_dataset_schema_hash
    hash_calls = 0

    def counted_hash(dataset_schema: MeasurementDatasetSchema) -> str:
        nonlocal hash_calls
        hash_calls += 1
        return original(dataset_schema)

    monkeypatch.setattr(
        recording_arrow,
        "measurement_dataset_schema_hash",
        counted_hash,
    )

    _commit_header(runs, repository, header)
    _commit_append(runs, repository, _append(header, point_index=0))
    _commit_append(runs, repository, _append(header, point_index=1))

    assert hash_calls == 1


def test_in_transaction_primitives_report_created_and_replay_durable_values(
    tmp_path: Path,
) -> None:
    runs = _runs(tmp_path)
    run_id = "run-in-transaction"
    measurements = SQLiteMeasurementDatasetRepository(runs, run_id=run_id)
    header = _header(run_id)
    append = _append(header)
    seal = _seal(header, append)
    prepared_header = measurements.prepare_header(header)
    prepared_append = measurements.prepare_append(
        append,
        dataset_schema=header.dataset_schema,
    )
    prepared_seal = measurements.prepare_seal(seal)

    with _sqlite_transaction(runs) as connection:
        _ensure_run_owner(connection, run_id)
        header_receipt, header_created, fragment_created = (
            measurements.header_prepared_in_transaction(
                connection,
                prepared_header,
                segment_id=_segment_id(run_id),
            )
        )
        header_replay, header_replay_created, fragment_replay_created = (
            measurements.header_prepared_in_transaction(
                connection,
                prepared_header,
                segment_id=_segment_id(run_id),
            )
        )
        append_receipt, append_created = measurements.append_prepared_in_transaction(
            connection,
            prepared_append,
            segment_id=_segment_id(run_id),
        )
        append_replay, append_replay_created = (
            measurements.append_prepared_in_transaction(
                connection,
                prepared_append,
                segment_id=_segment_id(run_id),
            )
        )
        SQLiteRecoveryGroups(runs, run_id=run_id).append_in_transaction(
            connection,
            (
                RecoveryGroupCompletion(
                    schedule_fingerprint="test-measurement-schedule",
                    group_id="test:0",
                    point_indices=(0,),
                    output_kind="measurement",
                    record_content_hashes=append.record_content_hashes,
                ),
            ),
            segment_id=_segment_id(run_id),
        )
        seal_receipt, seal_created = measurements.seal_prepared_in_transaction(
            connection,
            prepared_seal,
            segment_id=_segment_id(run_id),
        )
        seal_replay, seal_replay_created = measurements.seal_prepared_in_transaction(
            connection,
            prepared_seal,
            segment_id=_segment_id(run_id),
        )
        sealed_append_replay, sealed_append_created = (
            measurements.append_prepared_in_transaction(
                connection,
                prepared_append,
                segment_id=_segment_id(run_id),
            )
        )
        assert header_created
        assert fragment_created
        assert not header_replay_created
        assert not fragment_replay_created
        assert header_replay == header_receipt
        assert append_created
        assert not append_replay_created
        assert append_replay == append_receipt
        assert seal_created
        assert not seal_replay_created
        assert seal_replay == seal_receipt
        assert not sealed_append_created
        assert sealed_append_replay == append_receipt


def test_two_measurement_connections_replay_and_conflict_by_canonical_slot(
    tmp_path: Path,
) -> None:
    runs = _runs(tmp_path)
    first = SQLiteMeasurementDatasetRepository(runs, run_id="run-measurement")
    second = SQLiteMeasurementDatasetRepository(runs, run_id="run-measurement")
    header = _header("run-measurement")
    _commit_header(runs, first, header)
    append = _append(header)
    barrier = Barrier(2)

    def commit(
        repository: SQLiteMeasurementDatasetRepository,
    ) -> tuple[str, bool]:
        barrier.wait()
        prepared = repository.prepare_append(append)
        with _sqlite_transaction(runs) as connection:
            receipt, created = repository.append_prepared_in_transaction(
                connection,
                prepared,
                segment_id=_segment_id(header.run_id),
            )
            return receipt.model_dump_json(), created

    with ThreadPoolExecutor(max_workers=2) as pool:
        receipts = tuple(pool.map(commit, (first, second)))

    assert len({receipt for receipt, _created in receipts}) == 1
    assert sorted(created for _receipt, created in receipts) == [False, True]
    _commit_append(runs, first, append)
    assert len(first.measurements()) == 1
    with pytest.raises(ExecutionStateConflict):
        _commit_append(
            runs,
            second,
            _append(header, value=2),
        )


def test_measurement_page_reads_intersecting_chunks_and_live_schema(
    tmp_path: Path,
) -> None:
    runs = _runs(tmp_path)
    repository = SQLiteMeasurementDatasetRepository(runs, run_id="run-page")
    header = _header("run-page", point_count=3)
    _commit_header(runs, repository, header)
    first = _append(header, point_index=0, value=1)
    second = _append(header, point_index=1, value=2)
    _commit_append(runs, repository, first)
    _commit_append(runs, repository, second)

    items, next_offset, schema, snapshot_size = repository.measurement_page(
        limit=1, offset=0
    )
    _commit_append(
        runs,
        repository,
        _append(header, point_index=2, value=3),
    )
    later, later_offset, later_schema, later_snapshot_size = (
        repository.measurement_page(
            limit=1,
            offset=1,
            snapshot_size=snapshot_size,
            include_schema=False,
        )
    )
    current, current_offset, _current_schema, current_snapshot_size = (
        repository.measurement_page(limit=10, offset=0)
    )

    assert [record.point_index for record in items] == [0]
    assert next_offset == 1
    assert [record.point_index for record in later] == [1]
    assert later_offset is None
    assert snapshot_size == later_snapshot_size == 2
    assert [record.point_index for record in current] == [0, 1, 2]
    assert current_offset is None
    assert current_snapshot_size == 3
    assert schema == header.dataset_schema
    assert later_schema is None


def test_measurements_preserve_acquisition_order_and_project_logical_order(
    tmp_path: Path,
) -> None:
    runs = _runs(tmp_path)
    run_id = "run-acquisition-order"
    repository = SQLiteMeasurementDatasetRepository(runs, run_id=run_id)
    header = _header(run_id, point_count=3)
    records = tuple(
        _append(header, point_index=point_index, value=point_index).records[0]
        for point_index in (2, 0, 1)
    )
    append = MeasurementDatasetAppend(
        run_id=run_id,
        header_content_hash=header.content_hash,
        acquisition_start=0,
        records=records,
    )

    _commit_header(runs, repository, header)
    _commit_append(runs, repository, append)

    with runs.sqlite.read_connection() as connection:
        acquired = connection.execute(
            """
            SELECT point_index
            FROM execution_measurement_records
            WHERE run_id = ?
            ORDER BY acquisition_index
            """,
            (run_id,),
        ).fetchall()

    assert [int(row["point_index"]) for row in acquired] == [2, 0, 1]
    assert [record.point_index for record in repository.measurements()] == [0, 1, 2]
    assert repository.acquisition_record_count() == 3
    assert repository.measurement_record_count() == 3


def test_seal_selects_completed_retry_without_rewriting_acquisition_log(
    tmp_path: Path,
) -> None:
    runs = _runs(tmp_path)
    run_id = "run-acquisition-retry"
    repository = SQLiteMeasurementDatasetRepository(runs, run_id=run_id)
    header = _header(run_id, point_count=2)
    orphan = _append(
        header,
        point_index=1,
        acquisition_start=0,
        value=10,
    )
    retried_records = tuple(
        _append(header, point_index=point_index, value=point_index + 1).records[0]
        for point_index in (0, 1)
    )
    retry = MeasurementDatasetAppend(
        run_id=run_id,
        header_content_hash=header.content_hash,
        acquisition_start=1,
        records=retried_records,
    )

    _commit_header(runs, repository, header)
    _commit_acquisition(runs, repository, orphan)
    _commit_append(runs, repository, retry)
    _commit_seal(
        runs,
        repository,
        MeasurementDatasetSeal(
            run_id=run_id,
            header_content_hash=header.content_hash,
            record_count=2,
            fragment_record_count=3,
            fragment_content_hash=measurement_fragment_content_hash(
                header_content_hash=header.content_hash,
                record_content_hashes=(
                    *orphan.record_content_hashes,
                    *retry.record_content_hashes,
                ),
            ),
        ),
    )

    records = repository.measurements()
    assert [record.point_index for record in records] == [0, 1]
    assert [record.observables["signal"] for record in records] == [
        MeasurementScalar.create(dtype="float64", value=1, unit="ratio"),
        MeasurementScalar.create(dtype="float64", value=2, unit="ratio"),
    ]
    assert repository.acquisition_record_count() == 3
    assert repository.measurement_record_count() == 2
    with runs.sqlite.read_connection() as connection:
        acquired = connection.execute(
            """
            SELECT point_index
            FROM execution_measurement_records
            WHERE run_id = ?
            ORDER BY acquisition_index
            """,
            (run_id,),
        ).fetchall()
    assert [int(row["point_index"]) for row in acquired] == [1, 0, 1]


def test_measurement_page_pushes_variable_selection_into_arrow_chunks(
    tmp_path: Path,
) -> None:
    runs = _runs(tmp_path)
    repository = SQLiteMeasurementDatasetRepository(runs, run_id="run-columns")
    base = _header("run-columns")
    header = base.model_copy(
        update={
            "dataset_schema": base.dataset_schema.model_copy(
                update={
                    "variables": (
                        MeasurementVariable(
                            id="frequency",
                            role="coordinate",
                            dtype="float64",
                            unit="Hz",
                            dims=("point",),
                        ),
                        *base.dataset_schema.variables,
                    ),
                    "primary_coordinates": ("frequency",),
                }
            )
        }
    )
    _commit_header(runs, repository, header)
    append = _append(header).model_copy(
        update={
            "records": (
                _append(header)
                .records[0]
                .model_copy(
                    update={
                        "coordinates": {
                            "frequency": MeasurementScalar.create(
                                dtype="float64",
                                value=5e9,
                                unit="Hz",
                            )
                        }
                    }
                ),
            )
        }
    )
    _commit_append(runs, repository, append)

    items, next_offset, schema, snapshot_size = repository.measurement_page(
        limit=1,
        offset=0,
        variable_ids=("signal",),
    )

    assert next_offset is None
    assert snapshot_size == 1
    assert schema == header.dataset_schema
    assert items[0].coordinates == {}
    assert tuple(items[0].observables) == ("signal",)


def test_measurement_records_at_reads_only_selected_durable_points(
    tmp_path: Path,
) -> None:
    runs = _runs(tmp_path)
    repository = SQLiteMeasurementDatasetRepository(runs, run_id="run-selection")
    header = _header("run-selection", point_count=5)
    _commit_header(runs, repository, header)
    for acquisition_start in (0, 2):
        append = MeasurementDatasetAppend(
            run_id=header.run_id,
            header_content_hash=header.content_hash,
            acquisition_start=acquisition_start,
            records=tuple(
                _append(
                    header,
                    point_index=point_index,
                    value=point_index,
                ).records[0]
                for point_index in range(
                    acquisition_start,
                    acquisition_start + 2,
                )
            ),
        )
        _commit_append(runs, repository, append)

    records = repository.measurement_records_at((3, 1, 4))

    assert [record.point_index for record in records] == [3, 1]
    assert [record.observables["signal"] for record in records] == [
        MeasurementScalar.create(dtype="float64", value=3, unit="ratio"),
        MeasurementScalar.create(dtype="float64", value=1, unit="ratio"),
    ]


def test_measurement_header_makes_an_empty_dataset_readable(tmp_path: Path) -> None:
    runs = _runs(tmp_path)
    repository = SQLiteMeasurementDatasetRepository(runs, run_id="run-empty")
    header = _header("run-empty", point_count=0)
    _commit_header(runs, repository, header)
    seal = MeasurementDatasetSeal(
        run_id=header.run_id,
        header_content_hash=header.content_hash,
        record_count=0,
        fragment_record_count=0,
        fragment_content_hash=measurement_fragment_content_hash(
            header_content_hash=header.content_hash,
            record_content_hashes=(),
        ),
    )
    _commit_seal(runs, repository, seal)

    items, next_offset, schema, snapshot_size = repository.measurement_page(
        limit=10, offset=0
    )

    assert items == ()
    assert next_offset is None
    assert snapshot_size == 0
    assert schema == header.dataset_schema
    assert repository.measurements() == ()


def test_measurement_header_rejects_a_changed_live_schema(tmp_path: Path) -> None:
    runs = _runs(tmp_path)
    repository = SQLiteMeasurementDatasetRepository(runs, run_id="run-schema")
    header = _header("run-schema")
    _commit_header(runs, repository, header)
    changed_schema = header.dataset_schema.model_copy(
        update={"metadata": {"revision": 2}}
    )

    with pytest.raises(ExecutionStateConflict, match="different content"):
        _commit_header(
            runs,
            repository,
            header.model_copy(update={"dataset_schema": changed_schema}),
        )


def test_unavailable_measurement_round_trips_through_dataset_storage(
    tmp_path: Path,
) -> None:
    runs = _runs(tmp_path)
    repository = SQLiteMeasurementDatasetRepository(
        runs,
        run_id="run-unavailable",
    )
    unavailable = MeasurementUnavailable.create(
        reason="overload",
        dtype="float64",
        unit="ratio",
        shape=(),
        metadata={"status_register": 4},
    )
    header = _header("run-unavailable")
    append = MeasurementDatasetAppend(
        run_id="run-unavailable",
        header_content_hash=header.content_hash,
        acquisition_start=0,
        records=(
            MeasurementRecord(
                run_id="run-unavailable",
                logical_point_id="point-0",
                point_index=0,
                coordinates={},
                observables={"signal": unavailable},
            ),
        ),
    )

    _commit_header(runs, repository, header)
    _commit_append(runs, repository, append)

    [record] = repository.measurements()
    restored = record.observables["signal"]
    assert isinstance(restored, MeasurementUnavailable)
    assert restored == unavailable


def test_measurement_replay_rejects_mismatched_durable_operation_identity(
    tmp_path: Path,
) -> None:
    runs = _runs(tmp_path)
    append_repository = SQLiteMeasurementDatasetRepository(
        runs,
        run_id="run-append-identity",
    )
    append_header = _header("run-append-identity")
    _commit_header(runs, append_repository, append_header)
    append = _append(append_header)
    _commit_append(runs, append_repository, append)
    seal_repository = SQLiteMeasurementDatasetRepository(
        runs,
        run_id="run-seal-identity",
    )
    seal_header = _header("run-seal-identity")
    _commit_header(runs, seal_repository, seal_header)
    seal_append = _append(seal_header)
    seal = _seal(seal_header, seal_append)
    _commit_append(runs, seal_repository, seal_append)
    _commit_seal(runs, seal_repository, seal)
    with sqlite3.connect(runs.database) as connection:
        connection.execute(
            """
            UPDATE execution_measurement_appends
            SET operation_id = 'different-append'
            WHERE run_id = ?
            """,
            (append.run_id,),
        )
        connection.execute(
            """
            UPDATE execution_measurement_seals
            SET operation_id = 'different-seal'
            WHERE run_id = ?
            """,
            (seal.run_id,),
        )

    with pytest.raises(ExecutionStateConflict, match="different content"):
        _commit_append(runs, append_repository, append)
    with pytest.raises(ExecutionStateConflict, match="different content"):
        _commit_seal(runs, seal_repository, seal)


def test_coverage_requires_actual_durable_ordinals_not_acquisition_count(
    tmp_path: Path,
) -> None:
    runs = _runs(tmp_path)
    header = _header("durable-coverage", point_count=3)
    repository = SQLiteMeasurementDatasetRepository(runs, run_id=header.run_id)
    _commit_header(runs, repository, header)
    coverage = SQLiteRunCoverage(runs, run_id=header.run_id)
    # Receiving/preparing output is not a durable append; an unrelated ordinal
    # is not evidence of a contiguous prefix either.
    repository.prepare_append(_append(header))
    with (
        pytest.raises(ExecutionStateConflict, match="durably acquired"),
        _sqlite_transaction(runs) as connection,
    ):
        coverage.advance_in_transaction(connection, start_index=0, point_count=1)
    _commit_acquisition(
        runs, repository, _append(header, point_index=2, acquisition_start=0)
    )
    with (
        pytest.raises(ExecutionStateConflict, match="durably acquired"),
        _sqlite_transaction(runs) as connection,
    ):
        coverage.advance_in_transaction(connection, start_index=0, point_count=1)
    assert coverage.read() == 0
    _commit_append(
        runs, repository, _append(header, point_index=0, acquisition_start=1)
    )
    with _sqlite_transaction(runs) as connection:
        assert coverage.advance_in_transaction(
            connection, start_index=0, point_count=1
        ) == (1, True)
    with (
        pytest.raises(ExecutionStateConflict, match="durably acquired"),
        _sqlite_transaction(runs) as connection,
    ):
        coverage.advance_in_transaction(connection, start_index=1, point_count=2)
    assert coverage.read() == 1


def test_durable_preview_keeps_fixed_output_and_selects_latest_unfixed_tail(
    tmp_path: Path,
) -> None:
    runs = _runs(tmp_path)
    header = _header("preview-tail", point_count=3)
    repository = SQLiteMeasurementDatasetRepository(runs, run_id=header.run_id)
    _commit_header(runs, repository, header)
    fixed = _append(header, value=1)
    _commit_append(runs, repository, fixed)
    _commit_acquisition(
        runs, repository, _append(header, point_index=0, acquisition_start=1, value=99)
    )
    _commit_acquisition(
        runs, repository, _append(header, point_index=2, acquisition_start=2, value=2)
    )
    latest = _append(header, point_index=2, acquisition_start=3, value=3)
    _commit_acquisition(runs, repository, latest)
    items, next_offset, schema = repository.measurement_preview(limit=1)
    assert items == fixed.records
    assert next_offset == 1
    assert schema == header.dataset_schema
    items, next_offset, _schema = repository.measurement_preview(limit=2)
    assert items == (*fixed.records, *latest.records)
    assert next_offset is None  # Two actual rows, even though the last ordinal is 2.
    assert repository.measurement_page(limit=3, offset=0)[0] == fixed.records
    # A resumed exact group may select a newer acquisition for the unproved tail.
    resumed = _append(header, point_index=2, acquisition_start=4, value=4)
    _commit_append(runs, repository, resumed)
    assert repository.measurement_preview(limit=3)[0] == (
        *fixed.records,
        *resumed.records,
    )
    assert repository.measurement_page(limit=3, offset=0)[0] == (
        *fixed.records,
        *resumed.records,
    )
