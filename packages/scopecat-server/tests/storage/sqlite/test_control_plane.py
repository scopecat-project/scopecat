from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier
from typing import Never

import pytest
from scopecat.control.models import (
    ControlRun,
    DurableEvent,
    DurableEventInput,
    ExecutorLease,
    InstrumentSession,
    InventoryMigrationBlocker,
    ResourceClaim,
    ResourceKey,
    RunAdmissionRecord,
    RunPlanSummary,
    RunResourceRequirement,
)
from scopecat.records.setup import SetupRevisionRef

from scopecat_server.storage.sqlite.connection import SQLiteDatabase
from scopecat_server.storage.sqlite.control_plane import (
    ControlPlaneConflict,
    ControlPlaneNotFound,
    ExecutorLeaseNotHeld,
    InstrumentSessionNotActive,
    SQLiteControlPlane,
)
from scopecat_server.storage.sqlite.project_store import SQLiteProjectStore

NOW = datetime(2026, 7, 23, 9, tzinfo=UTC)
SUBMISSION_HASH = "1" * 64
SESSION_TTL = timedelta(minutes=5)


def _store(path: Path) -> SQLiteControlPlane:
    sqlite = SQLiteDatabase(path)
    SQLiteProjectStore(sqlite, path.parent / "objects").bootstrap()
    return SQLiteControlPlane(sqlite)


def _open_instrument_session(
    store: SQLiteControlPlane,
    name: str,
    *,
    ttl: timedelta = SESSION_TTL,
    at: datetime = NOW,
) -> InstrumentSession:
    return store.open_instrument_session(
        operation_id=f"open-{name}",
        actor="alice",
        setup=SetupRevisionRef(
            revision_id="baseline", content_hash=f"sha256:{'a' * 64}"
        ),
        instrument_ids=(name,),
        exclusivity_keys=(f"visa:{name}",),
        ttl=ttl,
        expected_setup_generation=0,
        at=at,
    )


def _admission(
    run_id: str,
    *resources: ResourceKey,
    admitted_at: datetime = NOW,
) -> RunAdmissionRecord:
    return RunAdmissionRecord(
        submission_id=f"submission:{run_id}",
        submission_content_hash=SUBMISSION_HASH,
        run_id=run_id,
        plan=RunPlanSummary(
            experiment_id=f"scratch:{run_id}",
            experiment_kind="scratch",
            point_plan_fingerprint="a" * 64,
            measurement_contract_fingerprint="b" * 64,
            point_count=3,
            initial_point_count=3,
            point_limit=3,
            run_resource_requirements=tuple(
                RunResourceRequirement(kind=resource.kind, id=resource.id)
                for resource in resources
            ),
        ),
        resource_claims=resources,
        admitted_at=admitted_at,
    )


def _admit(
    store: SQLiteControlPlane,
    admission: RunAdmissionRecord,
    *,
    expected_setup_generation: int = 0,
) -> ControlRun:
    with store.write_transaction() as connection:
        return store.admit_run_in_transaction(
            connection,
            admission,
            expected_setup_generation=expected_setup_generation,
        )


def _start(
    store: SQLiteControlPlane,
    run_id: str,
    *,
    executor_id: str,
    ttl: timedelta | None = None,
    at: datetime = NOW,
) -> ExecutorLease:
    with store.write_transaction() as connection:
        return store.start_execution_in_transaction(
            connection,
            run_id,
            executor_id=executor_id,
            ttl=ttl or timedelta(seconds=30),
            at=at,
        )


def _close(
    store: SQLiteControlPlane,
    run_id: str,
    *,
    executor_token: str | None = None,
    at: datetime,
) -> ControlRun:
    with store.write_transaction() as connection:
        if executor_token is not None:
            store.finish_execution_segment_in_transaction(
                connection,
                run_id,
                token=executor_token,
                result="succeeded",
                certainty="known",
                at=at,
            )
        return store.close_run_in_transaction(
            connection,
            run_id,
            executor_token=executor_token,
            at=at,
        )


def _append_event(
    store: SQLiteControlPlane,
    event: DurableEventInput,
    *,
    executor_token: str | None = None,
) -> DurableEvent:
    if executor_token is not None:
        assert event.run_id is not None
        with store.fenced_transaction(
            event.run_id,
            token=executor_token,
            at=event.occurred_at,
        ) as connection:
            return store.append_event_in_transaction(connection, event)
    with store.write_transaction() as connection:
        return store.append_event_in_transaction(connection, event)


def _resource_claims(store: SQLiteControlPlane) -> tuple[ResourceClaim, ...]:
    with store.read_transaction() as connection:
        return store.list_resource_claims_in_transaction(connection)


def _inventory_migration_blockers(
    store: SQLiteControlPlane,
    *affected_keys: ResourceKey,
) -> tuple[InventoryMigrationBlocker, ...]:
    with store.read_transaction() as connection:
        return store.inventory_migration_blockers_in_transaction(
            connection,
            affected_keys,
        )


def _release_run_resources(store: SQLiteControlPlane, run_id: str) -> int:
    with store.write_transaction() as connection:
        return store.release_run_resources_in_transaction(connection, run_id)


def _executor_lease(
    store: SQLiteControlPlane,
    run_id: str,
) -> ExecutorLease | None:
    with store.read_transaction() as connection:
        return store.executor_lease_for_run_in_transaction(connection, run_id)


def test_run_admission_state_and_pagination(tmp_path: Path) -> None:
    store = _store(tmp_path / "control.sqlite3")
    for offset in range(3):
        _admit(
            store,
            _admission(
                f"run-{offset}",
                admitted_at=NOW + timedelta(seconds=offset),
            ),
        )

    first = store.list_runs(limit=2)
    assert [run.run_id for run in first.items] == ["run-2", "run-1"]
    assert first.next_cursor == first.items[-1].sequence
    second = store.list_runs(limit=2, before=first.next_cursor)
    assert [run.run_id for run in second.items] == ["run-0"]
    assert second.next_cursor is None

    retry = _admission("retry-run").model_copy(
        update={
            "submission_id": "submission:run-0",
            "plan": RunPlanSummary(
                experiment_id="scratch:run-0",
                experiment_kind="scratch",
                point_plan_fingerprint="a" * 64,
                measurement_contract_fingerprint="b" * 64,
                point_count=3,
                initial_point_count=3,
                point_limit=3,
            ),
            "admitted_at": NOW + timedelta(minutes=1),
        }
    )
    assert _admit(
        store,
        retry,
        expected_setup_generation=999,
    ) == store.get_run("run-0")
    assert second.items[0].admission.submission_id == "submission:run-0"
    assert [event.kind for event in store.list_events(run_id="run-0").items] == [
        "run_admitted"
    ]

    with pytest.raises(ControlPlaneConflict):
        _admit(
            store,
            retry.model_copy(update={"submission_content_hash": "2" * 64}),
        )


def test_executor_resources_and_scheduler_close_commit_together(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path / "control.sqlite3")
    _admit(
        store,
        _admission(
            "run-1",
            ResourceKey(kind="instrument", id="scope"),
            ResourceKey(kind="instrument", id="controller"),
        ),
    )
    executor = _start(
        store,
        "run-1",
        executor_id="kernel-1",
        ttl=timedelta(seconds=30),
        at=NOW,
    )
    assert store.get_run("run-1").state == "leased"
    assert len(_resource_claims(store)) == 2
    [active_segment] = store.list_run_execution_segments("run-1").items
    assert active_segment.segment_id == executor.segment_id
    assert active_segment.ordinal == 0
    assert active_segment.start_point_count == 0
    assert active_segment.result is None

    finished_at = NOW + timedelta(seconds=2)
    closed = _close(
        store,
        "run-1",
        executor_token=executor.token,
        at=finished_at,
    )
    assert closed.state == "closed"
    assert _resource_claims(store) == ()
    [finished_segment] = store.list_run_execution_segments("run-1").items
    assert finished_segment.segment_id == executor.segment_id
    assert finished_segment.result == "succeeded"
    assert finished_segment.certainty == "known"
    assert finished_segment.end_point_count == 0
    with pytest.raises(ExecutorLeaseNotHeld):
        _append_event(
            store,
            DurableEventInput(run_id="run-1", kind="late_executor_event"),
            executor_token=executor.token,
        )


def test_durable_events_have_global_cursor_and_run_filter(tmp_path: Path) -> None:
    store = _store(tmp_path / "control.sqlite3")
    _admit(store, _admission("run-1"))
    for index in range(4):
        _append_event(
            store,
            DurableEventInput(
                run_id="run-1",
                kind="measurement_chunk",
                payload={"index": index},
                occurred_at=NOW + timedelta(seconds=index),
            ),
        )
    later_event = _append_event(
        store, DurableEventInput(kind="config_activated", payload={"generation": 2})
    )

    first = store.list_events(run_id="run-1", limit=2)
    second = store.list_events(
        run_id="run-1",
        limit=10,
        after=first.next_cursor,
    )
    events = (*first.items, *second.items)
    assert [event.event_id for event in events] == sorted(
        event.event_id for event in events
    )
    assert [event.kind for event in events] == [
        "run_admitted",
        "measurement_chunk",
        "measurement_chunk",
        "measurement_chunk",
        "measurement_chunk",
    ]
    assert later_event.event_id > events[-1].event_id
    latest = store.list_events(run_id="run-1", limit=2, latest=True)
    assert [event.payload.get("index") for event in latest.items] == [2, 3]
    assert latest.next_cursor is None
    with pytest.raises(ValueError, match="after cursor"):
        store.list_events(limit=2, after=1, latest=True)


def test_resource_claims_are_all_or_none(tmp_path: Path) -> None:
    store = _store(tmp_path / "control.sqlite3")
    shared = ResourceKey(kind="instrument", id="scope")
    _admit(
        store,
        _admission("run-a", shared, ResourceKey(kind="instrument", id="a")),
    )
    _admit(
        store,
        _admission("run-b", shared, ResourceKey(kind="instrument", id="b")),
    )
    _start(
        store,
        "run-a",
        executor_id="a",
    )

    with pytest.raises(
        ControlPlaneConflict,
        match="resources are busy",
    ) as caught:
        _start(store, "run-b", executor_id="b")
    assert "scope" not in str(caught.value)
    claims = _resource_claims(store)
    assert {claim.resource.id for claim in claims} == {"scope", "a"}
    assert {(claim.owner_kind, claim.owner_id) for claim in claims} == {
        ("run", "run-a")
    }
    assert _executor_lease(store, "run-b") is None
    assert store.get_run("run-b").state == "queued"


def test_inventory_migration_blockers_include_queued_run_reservations(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path / "control.sqlite3")
    key = ResourceKey.instrument("visa:scope")
    _admit(store, _admission("queued", key))

    assert _resource_claims(store) == ()
    assert _inventory_migration_blockers(store, key) == (
        InventoryMigrationBlocker(
            key=key,
            owner_kind="run",
            owner_id="queued",
            state="queued",
        ),
    )


def test_inventory_migration_blockers_deduplicate_leased_run_claims(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path / "control.sqlite3")
    key = ResourceKey.instrument("visa:scope")
    _admit(store, _admission("leased", key))
    _start(store, "leased", executor_id="kernel")

    assert _inventory_migration_blockers(store, key, key) == (
        InventoryMigrationBlocker(
            key=key,
            owner_kind="run",
            owner_id="leased",
            state="leased",
        ),
    )


def test_inventory_migration_blockers_prefer_attention_run_state(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path / "control.sqlite3")
    key = ResourceKey.instrument("visa:scope")
    _admit(store, _admission("attention", key))
    lease = _start(store, "attention", executor_id="kernel")
    store.mark_executor_unknown(
        "attention",
        token=lease.token,
        reason="instrument_apply_unknown",
        at=NOW + timedelta(seconds=1),
    )

    assert _inventory_migration_blockers(store, key) == (
        InventoryMigrationBlocker(
            key=key,
            owner_kind="run",
            owner_id="attention",
            state="attention_required",
        ),
    )


def test_inventory_migration_blockers_include_active_instrument_session(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path / "control.sqlite3")
    key = ResourceKey.instrument("visa:scope")
    _admit(store, _admission("queued", key))
    session = store.open_instrument_session(
        operation_id="open-1",
        actor="alice",
        setup=SetupRevisionRef(
            revision_id="baseline", content_hash=f"sha256:{'a' * 64}"
        ),
        instrument_ids=("scope",),
        exclusivity_keys=(key.id,),
        ttl=SESSION_TTL,
        expected_setup_generation=0,
        at=NOW,
    )

    assert _inventory_migration_blockers(store, key) == (
        InventoryMigrationBlocker(
            key=key,
            owner_kind="instrument_session",
            owner_id=session.session_id,
            state="active",
        ),
        InventoryMigrationBlocker(
            key=key,
            owner_kind="run",
            owner_id="queued",
            state="queued",
        ),
    )


def test_inventory_migration_blockers_include_quarantined_session_claim(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path / "control.sqlite3")
    key = ResourceKey.instrument("visa:scope")
    session = store.open_instrument_session(
        operation_id="open-1",
        actor="alice",
        setup=SetupRevisionRef(
            revision_id="baseline", content_hash=f"sha256:{'a' * 64}"
        ),
        instrument_ids=("scope",),
        exclusivity_keys=(key.id,),
        ttl=SESSION_TTL,
        expected_setup_generation=0,
        at=NOW,
    )
    store.start_instrument_operation(
        session.session_id,
        instrument_id="scope",
        operation_id="collect-1",
        kind="collect",
        at=NOW + timedelta(seconds=1),
    )
    store.reconcile_instrument_sessions_after_restart(at=NOW + timedelta(seconds=2))

    assert _inventory_migration_blockers(store, key) == (
        InventoryMigrationBlocker(
            key=key,
            owner_kind="instrument_session",
            owner_id=session.session_id,
            state="quarantined",
        ),
    )


def test_inventory_migration_blockers_ignore_closed_runs_and_unaffected_keys(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path / "control.sqlite3")
    closed_key = ResourceKey.instrument("visa:closed")
    _admit(store, _admission("closed", closed_key))
    lease = _start(store, "closed", executor_id="kernel")
    _close(
        store,
        "closed",
        executor_token=lease.token,
        at=NOW + timedelta(seconds=1),
    )
    _admit(
        store,
        _admission("unaffected", ResourceKey.instrument("visa:other")),
    )

    assert (
        _inventory_migration_blockers(
            store,
            closed_key,
            ResourceKey.instrument("visa:missing"),
        )
        == ()
    )
    assert _inventory_migration_blockers(store) == ()


def test_concurrent_resource_claim_has_one_winner(tmp_path: Path) -> None:
    path = tmp_path / "control.sqlite3"
    store = _store(path)
    shared = ResourceKey(kind="instrument", id="scope")
    _admit(store, _admission("run-a", shared))
    _admit(store, _admission("run-b", shared))
    barrier = Barrier(2)

    def start(run_id: str) -> bool:
        peer = SQLiteControlPlane(SQLiteDatabase(path))
        barrier.wait()
        try:
            _start(peer, run_id, executor_id=run_id)
        except ControlPlaneConflict:
            return False
        return True

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(start, ("run-a", "run-b")))

    assert sorted(results) == [False, True]
    assert len(_resource_claims(store)) == 1


def test_expired_leased_executor_quarantines_resources(tmp_path: Path) -> None:
    store = _store(tmp_path / "control.sqlite3")
    shared = ResourceKey(kind="instrument", id="scope")
    _admit(store, _admission("lost", shared))
    _admit(store, _admission("waiting", shared))
    _start(
        store,
        "lost",
        executor_id="kernel",
        ttl=timedelta(seconds=10),
        at=NOW,
    )

    expired = store.expire_executor_leases(at=NOW + timedelta(seconds=11))

    assert expired == ("lost",)
    run = store.get_run("lost")
    assert run.state == "attention_required"
    assert run.attention_reason == "executor_lease_expired"
    [segment] = store.list_run_execution_segments("lost").items
    assert segment.result == "interrupted"
    assert segment.certainty == "indeterminate"
    assert segment.reason == "executor_lease_expired"
    event_kinds = {event.kind for event in store.list_events(run_id="lost").items}
    assert {
        "executor_lease_granted",
        "resources_claimed",
        "resources_quarantined",
        "executor_lease_lost",
    } <= event_kinds
    quarantined = _resource_claims(store)
    assert len(quarantined) == 1
    assert quarantined[0].status == "quarantined"
    with pytest.raises(ControlPlaneConflict, match="resources are busy"):
        _start(
            store,
            "waiting",
            executor_id="daemon",
            at=NOW + timedelta(seconds=11),
        )

    assert _release_run_resources(store, "lost") == 1
    waiting = _start(
        store,
        "waiting",
        executor_id="daemon",
        at=NOW + timedelta(seconds=12),
    )
    assert waiting.run_id == "waiting"
    assert store.get_run("lost").state == "attention_required"


def test_reconciled_attention_can_open_a_new_execution_segment(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path / "control.sqlite3")
    shared = ResourceKey(kind="instrument", id="scope")
    _admit(store, _admission("run-1", shared))
    first = _start(store, "run-1", executor_id="kernel-1", at=NOW)
    store.mark_executor_unknown(
        "run-1",
        token=first.token,
        reason="executor_disconnected",
        at=NOW + timedelta(seconds=1),
    )

    with (
        pytest.raises(ControlPlaneConflict, match="contract"),
        store.write_transaction() as connection,
    ):
        store.continue_run_after_attention_in_transaction(
            connection,
            "run-1",
            run_contract_fingerprint="0" * 64,
            at=NOW + timedelta(seconds=2),
        )
    assert store.get_run("run-1").state == "attention_required"
    [quarantined] = _resource_claims(store)
    assert quarantined.status == "quarantined"

    with store.write_transaction() as connection:
        continued, released = store.continue_run_after_attention_in_transaction(
            connection,
            "run-1",
            run_contract_fingerprint=SUBMISSION_HASH,
            at=NOW + timedelta(seconds=2),
        )

    assert continued.state == "queued"
    assert continued.attention_reason is None
    assert released == 1
    assert _resource_claims(store) == ()
    second = _start(
        store,
        "run-1",
        executor_id="kernel-2",
        at=NOW + timedelta(seconds=3),
    )
    segments = store.list_run_execution_segments("run-1").items
    assert [segment.ordinal for segment in segments] == [1, 0]
    assert segments[0].segment_id == second.segment_id
    assert segments[0].result is None
    assert segments[1].segment_id == first.segment_id
    assert segments[1].result == "interrupted"
    assert segments[1].reason == "executor_disconnected"
    event_kinds = {event.kind for event in store.list_events(run_id="run-1").items}
    assert "run_continuation_authorized" in event_kinds


def test_expire_executor_leases_skips_writer_without_candidates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store(tmp_path / "control.sqlite3")

    def unexpected_write_transaction() -> Never:
        raise AssertionError("empty lease scan opened a write transaction")

    monkeypatch.setattr(
        store.sqlite,
        "write_transaction",
        unexpected_write_transaction,
    )

    assert store.expire_executor_leases(at=NOW) == ()


def test_executor_renewal_preserves_claim_and_close_fences_token(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path / "control.sqlite3")
    _admit(
        store,
        _admission("run-1", ResourceKey(kind="instrument", id="scope")),
    )
    first = _start(
        store,
        "run-1",
        executor_id="kernel",
        ttl=timedelta(seconds=10),
        at=NOW,
    )
    events_before_renewal = store.list_events(run_id="run-1").items

    with pytest.raises(ExecutorLeaseNotHeld, match="belongs to another run"):
        store.renew_executor_lease(
            "run-2",
            first.token,
            ttl=timedelta(seconds=20),
            at=NOW + timedelta(seconds=5),
        )
    renewed = store.renew_executor_lease(
        "run-1",
        first.token,
        ttl=timedelta(seconds=20),
        at=NOW + timedelta(seconds=5),
    )
    [claim] = _resource_claims(store)
    assert claim.owner_id == "run-1"
    assert claim.acquired_at == NOW
    assert renewed.renewed_at == NOW + timedelta(seconds=5)
    assert store.list_events(run_id="run-1").items == events_before_renewal
    _close(
        store,
        "run-1",
        executor_token=first.token,
        at=NOW + timedelta(seconds=6),
    )
    with pytest.raises(ExecutorLeaseNotHeld):
        _append_event(
            store,
            DurableEventInput(
                run_id="run-1",
                kind="stale",
                occurred_at=NOW + timedelta(seconds=6),
            ),
            executor_token=first.token,
        )


def test_uncertain_executor_io_fences_exact_token_and_quarantines_resources(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path / "control.sqlite3")
    _admit(
        store,
        _admission("run-1", ResourceKey(kind="instrument", id="scope")),
    )
    lease = _start(
        store,
        "run-1",
        executor_id="kernel",
        ttl=timedelta(seconds=10),
        at=NOW,
    )

    with pytest.raises(ExecutorLeaseNotHeld):
        store.mark_executor_unknown(
            "run-1",
            token=f"{lease.token}-stale",
            reason="instrument_apply_unknown",
            at=NOW + timedelta(seconds=1),
        )
    lost = store.mark_executor_unknown(
        "run-1",
        token=lease.token,
        reason="instrument_apply_unknown",
        at=NOW + timedelta(seconds=1),
    )

    assert lost.state == "attention_required"
    assert lost.attention_reason == "instrument_apply_unknown"
    assert _executor_lease(store, "run-1") is None
    [resource] = _resource_claims(store)
    assert resource.status == "quarantined"


def test_instrument_session_retry_operation_recovery_and_explicit_close(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path / "control.sqlite3")
    first = store.open_instrument_session(
        operation_id="open-1",
        actor="alice",
        setup=SetupRevisionRef(
            revision_id="baseline", content_hash=f"sha256:{'a' * 64}"
        ),
        instrument_ids=("scope",),
        exclusivity_keys=("visa:scope",),
        ttl=SESSION_TTL,
        expected_setup_generation=0,
        at=NOW,
    )
    retry = store.open_instrument_session(
        operation_id="open-1",
        actor="alice",
        setup=SetupRevisionRef(
            revision_id="replacement", content_hash=f"sha256:{'b' * 64}"
        ),
        instrument_ids=("scope",),
        exclusivity_keys=("visa:replacement",),
        ttl=SESSION_TTL,
        expected_setup_generation=None,
        at=NOW + timedelta(seconds=1),
    )

    assert retry == first
    assert first.exclusivity_keys == ("visa:scope",)
    [claim] = _resource_claims(store)
    assert claim.resource == ResourceKey(kind="instrument", id="visa:scope")
    opened = next(
        event
        for event in store.list_events().items
        if event.kind == "instrument_session_opened"
    )
    assert opened.payload == {
        "session_id": first.session_id,
        "operation_id": "open-1",
        "actor": "alice",
        "instrument_ids": ["scope"],
        "setup_revision_id": "baseline",
    }
    assert store.get_instrument_session_by_open_operation_id("open-1") == first
    with pytest.raises(ControlPlaneNotFound):
        store.get_instrument_session_by_open_operation_id("missing")
    with pytest.raises(ControlPlaneConflict, match="different content"):
        store.open_instrument_session(
            operation_id="open-1",
            actor="bob",
            setup=SetupRevisionRef(
                revision_id="baseline", content_hash=f"sha256:{'a' * 64}"
            ),
            instrument_ids=("scope",),
            exclusivity_keys=("visa:scope",),
            ttl=SESSION_TTL,
            expected_setup_generation=None,
            at=NOW + timedelta(seconds=1),
        )

    started = store.start_instrument_operation(
        first.session_id,
        instrument_id="scope",
        operation_id="apply-1",
        kind="apply",
        at=NOW + timedelta(seconds=5),
    )
    assert started.active_operation_id == "apply-1"
    finished = store.finish_instrument_operation(
        first.session_id,
        instrument_id="scope",
        operation_id="apply-1",
        kind="apply",
        status="applied",
        at=NOW + timedelta(seconds=6),
    )
    assert finished.active_operation_id is None
    closed = store.close_instrument_session(
        first.session_id,
        status="closed",
        at=NOW + timedelta(seconds=7),
    )
    assert closed.state == "closed"
    assert closed.end_status == "closed"
    assert closed.exclusivity_keys == ("visa:scope",)
    assert _resource_claims(store) == ()
    with pytest.raises(InstrumentSessionNotActive):
        store.validate_instrument_session(first.session_id)


def test_instrument_session_lease_renews_silently_and_expires_idle_session(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path / "control.sqlite3")
    opened = _open_instrument_session(
        store,
        "scope",
        ttl=timedelta(seconds=10),
    )

    assert opened.acquired_at == NOW
    assert opened.renewed_at == NOW
    assert opened.expires_at == NOW + timedelta(seconds=10)
    with pytest.raises(ValueError, match="expire after its renewal time"):
        InstrumentSession.model_validate(
            {
                **opened.model_dump(),
                "expires_at": opened.renewed_at,
            }
        )

    events_before_renewal = store.list_events().items
    renewed = store.renew_instrument_session(
        opened.session_id,
        ttl=timedelta(seconds=20),
        at=NOW + timedelta(seconds=5),
    )

    assert renewed.acquired_at == NOW
    assert renewed.renewed_at == NOW + timedelta(seconds=5)
    assert renewed.expires_at == NOW + timedelta(seconds=25)
    assert store.list_events().items == events_before_renewal
    [claim] = _resource_claims(store)
    assert claim.acquired_at == NOW
    assert store.expired_instrument_sessions(at=NOW + timedelta(seconds=24)) == ()
    assert store.expired_instrument_sessions(at=NOW + timedelta(seconds=25)) == (
        renewed,
    )

    with pytest.raises(ControlPlaneConflict, match="has not expired"):
        store.expire_instrument_session(
            opened.session_id,
            at=NOW + timedelta(seconds=24),
        )
    with pytest.raises(InstrumentSessionNotActive, match="expired"):
        store.renew_instrument_session(
            opened.session_id,
            ttl=timedelta(minutes=1),
            at=NOW + timedelta(seconds=25),
        )
    with pytest.raises(ControlPlaneConflict, match="open retry has expired"):
        store.open_instrument_session(
            operation_id="open-scope",
            actor="alice",
            setup=SetupRevisionRef(
                revision_id="replacement", content_hash=f"sha256:{'b' * 64}"
            ),
            instrument_ids=("scope",),
            exclusivity_keys=("visa:replacement",),
            ttl=timedelta(minutes=1),
            expected_setup_generation=None,
            at=NOW + timedelta(seconds=25),
        )
    assert store.get_instrument_session(opened.session_id) == renewed

    closed = store.expire_instrument_session(
        opened.session_id,
        at=NOW + timedelta(seconds=25),
    )

    assert closed.state == "closed"
    assert closed.end_status == "aborted"
    assert _resource_claims(store) == ()
    expired_event = store.list_events().items[-1]
    assert expired_event.kind == "instrument_session_lease_expired"
    assert expired_event.occurred_at == NOW + timedelta(seconds=25)
    assert (
        store.expire_instrument_session(
            opened.session_id,
            at=NOW + timedelta(seconds=26),
        )
        == closed
    )


def test_expired_instrument_session_fences_active_operations(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path / "control.sqlite3")
    ttl = timedelta(seconds=10)
    validated = _open_instrument_session(store, "validated", ttl=ttl)
    starting = _open_instrument_session(store, "starting", ttl=ttl)
    finishing = _open_instrument_session(store, "finishing", ttl=ttl)
    store.start_instrument_operation(
        finishing.session_id,
        instrument_id="finishing",
        operation_id="collect-1",
        kind="collect",
        at=NOW + timedelta(seconds=9),
    )

    assert (
        store.validate_instrument_session(
            validated.session_id,
            at=NOW + timedelta(seconds=9),
        )
        == validated
    )
    with pytest.raises(InstrumentSessionNotActive, match="expired"):
        store.validate_instrument_session(
            validated.session_id,
            at=NOW + ttl,
        )
    with pytest.raises(InstrumentSessionNotActive, match="expired"):
        store.start_instrument_operation(
            starting.session_id,
            instrument_id="starting",
            operation_id="apply-1",
            kind="apply",
            at=NOW + ttl,
        )
    with pytest.raises(InstrumentSessionNotActive, match="expired"):
        store.finish_instrument_operation(
            finishing.session_id,
            instrument_id="finishing",
            operation_id="collect-1",
            kind="collect",
            status="collected",
            at=NOW + ttl,
        )
    assert (
        store.get_instrument_session(finishing.session_id).active_operation_id
        == "collect-1"
    )
    with pytest.raises(ControlPlaneConflict, match="active operation"):
        store.expire_instrument_session(finishing.session_id, at=NOW + ttl)


def test_restart_releases_idle_session_and_quarantines_unfinished_operation(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path / "control.sqlite3")
    idle = store.open_instrument_session(
        operation_id="open-idle",
        actor="alice",
        setup=SetupRevisionRef(
            revision_id="baseline", content_hash=f"sha256:{'a' * 64}"
        ),
        instrument_ids=("idle-scope",),
        exclusivity_keys=("visa:idle-scope",),
        ttl=timedelta(seconds=2),
        expected_setup_generation=0,
        at=NOW,
    )
    active = store.open_instrument_session(
        operation_id="open-active",
        actor="alice",
        setup=SetupRevisionRef(
            revision_id="baseline", content_hash=f"sha256:{'a' * 64}"
        ),
        instrument_ids=("active-scope",),
        exclusivity_keys=("visa:active-scope",),
        ttl=timedelta(seconds=2),
        expected_setup_generation=0,
        at=NOW,
    )
    store.start_instrument_operation(
        active.session_id,
        instrument_id="active-scope",
        operation_id="collect-1",
        kind="collect",
        at=NOW + timedelta(seconds=1),
    )

    assert store.reconcile_instrument_sessions_after_restart(
        at=NOW + timedelta(seconds=2)
    ) == (active.session_id,)

    assert store.get_instrument_session(idle.session_id).end_status == "aborted"
    lost = store.get_instrument_session(active.session_id)
    assert lost.state == "attention_required"
    assert lost.attention_reason == "daemon_restarted_during_instrument_operation"
    [claim] = _resource_claims(store)
    assert claim.owner_id == active.session_id
    assert claim.status == "quarantined"

    closed = store.resolve_instrument_session_attention(
        active.session_id,
        at=NOW + timedelta(seconds=3),
    )
    assert closed.end_status == "aborted"
    assert _resource_claims(store) == ()


def test_instrument_session_cannot_claim_a_run_owned_resource(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path / "control.sqlite3")
    resource = ResourceKey(kind="instrument", id="visa:scope")
    _admit(store, _admission("run-1", resource))
    _start(store, "run-1", executor_id="kernel")

    with pytest.raises(
        ControlPlaneConflict,
        match="resources are busy",
    ) as caught:
        store.open_instrument_session(
            operation_id="open-1",
            actor="alice",
            setup=SetupRevisionRef(
                revision_id="baseline", content_hash=f"sha256:{'a' * 64}"
            ),
            instrument_ids=("scope",),
            exclusivity_keys=("visa:scope",),
            ttl=SESSION_TTL,
            expected_setup_generation=0,
            at=NOW,
        )
    assert "visa:scope" not in str(caught.value)


@pytest.mark.parametrize(
    ("instrument_ids", "exclusivity_keys", "message"),
    [
        (("scope",), (), "equal length"),
        (("scope",), ("",), "must be non-empty"),
        (
            ("scope-a", "scope-b"),
            ("visa:scope", "visa:scope"),
            "must be unique",
        ),
    ],
)
def test_instrument_session_rejects_invalid_exclusivity_keys(
    tmp_path: Path,
    instrument_ids: tuple[str, ...],
    exclusivity_keys: tuple[str, ...],
    message: str,
) -> None:
    store = _store(tmp_path / "control.sqlite3")

    with pytest.raises(ValueError, match=message):
        store.open_instrument_session(
            operation_id="open-1",
            actor="alice",
            setup=SetupRevisionRef(
                revision_id="baseline", content_hash=f"sha256:{'a' * 64}"
            ),
            instrument_ids=instrument_ids,
            exclusivity_keys=exclusivity_keys,
            ttl=SESSION_TTL,
            expected_setup_generation=0,
            at=NOW,
        )
