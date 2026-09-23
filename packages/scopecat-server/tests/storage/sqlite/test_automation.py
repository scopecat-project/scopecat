from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
from scopecat.automation import (
    ConfigPublishOutputRef,
    ParameterBranchPublishOutputRef,
    ProcedureDefinitionRef,
    ProcedureRun,
    ProcedureStepAttempt,
    procedure_intent_hash,
)
from scopecat.records.parameter_branch import ParameterBranch
from scopecat.records.parameter_revision import ParameterRevisionRef

from scopecat_server.storage.sqlite.automation import (
    AutomationConflict,
    ProcedureLeaseRecord,
    SQLiteAutomationStore,
)
from scopecat_server.storage.sqlite.connection import SQLiteDatabase
from scopecat_server.storage.sqlite.project_store import SQLiteProjectStore

_START = datetime(2026, 8, 18, 9, tzinfo=UTC)
_HASH = "sha256:" + "1" * 64
_STEP_HASH = "sha256:" + "2" * 64


def _store(tmp_path: Path) -> SQLiteAutomationStore:
    sqlite = SQLiteDatabase(tmp_path / "control.sqlite3")
    SQLiteProjectStore(sqlite, tmp_path / "objects").bootstrap()
    return SQLiteAutomationStore(sqlite)


def _run(*, procedure_run_id: str = "procedure-1") -> ProcedureRun:
    definition = ProcedureDefinitionRef(
        id="drag-calibration",
        version="1",
        fingerprint=_HASH,
    )
    intent = {"qubits": ["q0"]}
    return ProcedureRun(
        procedure_run_id=procedure_run_id,
        request_key=procedure_run_id,
        definition=definition,
        intent=intent,
        intent_hash=procedure_intent_hash(definition, intent),
        revision=1,
        state="ready",
        created_at=_START,
        updated_at=_START,
    )


def _attempt(
    *,
    procedure_run_id: str = "procedure-1",
    step_key: str = "baseline",
) -> ProcedureStepAttempt:
    return ProcedureStepAttempt(
        procedure_run_id=procedure_run_id,
        step_key=step_key,
        attempt=1,
        operation="run",
        intent_hash=_STEP_HASH,
        revision=1,
        state="running",
        started_at=_START,
        updated_at=_START,
    )


def test_store_round_trips_and_pages_revisioned_procedure_runs(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    first = _run()
    second = _run(procedure_run_id="procedure-2")

    with store.write_transaction() as connection:
        store.insert_run_in_transaction(connection, first)
        store.insert_run_in_transaction(connection, second)

    page = store.list_runs(limit=1)
    assert page.items == (second,)
    assert page.next_cursor is not None
    assert store.list_runs(limit=1, before=page.next_cursor).items == (first,)
    assert store.find_run_by_request(first.definition.id, first.request_key) == first

    updated = first.model_copy(
        update={
            "revision": 2,
            "state": "leased",
            "updated_at": _START + timedelta(seconds=1),
        }
    )
    with store.write_transaction() as connection:
        store.replace_run_in_transaction(connection, updated, expected_revision=1)
    assert store.read_run(first.procedure_run_id) == updated

    with (
        pytest.raises(AutomationConflict, match="revision changed"),
        store.write_transaction() as connection,
    ):
        store.replace_run_in_transaction(connection, updated, expected_revision=1)


def test_store_bounds_step_history_and_enforces_one_running_attempt(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    run = _run()
    baseline = _attempt()
    verify = _attempt(step_key="verify")
    with store.write_transaction() as connection:
        store.insert_run_in_transaction(connection, run)
        store.insert_step_attempt_in_transaction(connection, baseline)
        with pytest.raises(AutomationConflict, match="conflicts"):
            store.insert_step_attempt_in_transaction(connection, verify)

    finished_at = _START + timedelta(seconds=1)
    succeeded = ProcedureStepAttempt.model_validate(
        {
            **baseline.model_dump(),
            "revision": 2,
            "state": "succeeded",
            "updated_at": finished_at,
            "finished_at": finished_at,
            "output": {"kind": "run", "run_id": "run-1"},
        }
    )
    with store.write_transaction() as connection:
        store.replace_step_attempt_in_transaction(
            connection,
            succeeded,
            expected_revision=1,
        )
        store.insert_step_attempt_in_transaction(connection, verify)

    first_page = store.list_step_attempts(run.procedure_run_id, limit=1)
    assert first_page.items == (verify,)
    assert first_page.next_cursor is not None
    assert store.list_step_attempts(
        run.procedure_run_id,
        limit=1,
        before=first_page.next_cursor,
    ).items == (succeeded,)


@pytest.mark.parametrize("operation", ["config_publish", "parameter_publish"])
def test_round_trips_publication_step_attempt(tmp_path: Path, operation: str) -> None:
    store = _store(tmp_path)
    run = _run()
    running = _attempt(step_key="accept-candidate").model_copy(
        update={"operation": operation}
    )
    with store.write_transaction() as connection:
        store.insert_run_in_transaction(connection, run)
        store.insert_step_attempt_in_transaction(connection, running)

    finished_at = _START + timedelta(seconds=1)
    succeeded = running.model_copy(
        update={
            "revision": 2,
            "state": "succeeded",
            "updated_at": finished_at,
            "finished_at": finished_at,
            "output": ParameterBranchPublishOutputRef(
                branch=ParameterBranch(
                    name="daily",
                    generation=2,
                    actor="automation",
                    revision=ParameterRevisionRef(
                        revision_id="accepted-candidate", content_hash=_HASH
                    ),
                )
            )
            if operation == "parameter_publish"
            else ConfigPublishOutputRef(
                generation=2,
                entry_id="accepted-candidate",
                entry_content_hash=_HASH,
            ),
        }
    )
    with store.write_transaction() as connection:
        store.replace_step_attempt_in_transaction(
            connection,
            succeeded,
            expected_revision=1,
        )
        persisted = store.read_step_attempt_in_transaction(
            connection,
            run.procedure_run_id,
            running.step_key,
            running.attempt,
        )

    assert persisted == succeeded


def test_store_replaces_worker_lease_for_expired_takeover(tmp_path: Path) -> None:
    store = _store(tmp_path)
    run = _run()
    first = ProcedureLeaseRecord(
        procedure_run_id=run.procedure_run_id,
        worker_id="worker-1",
        token="token-1",  # noqa: S106 - fixture fencing token
        acquired_at=_START,
        renewed_at=_START,
        expires_at=_START + timedelta(seconds=30),
    )
    second = ProcedureLeaseRecord(
        procedure_run_id=run.procedure_run_id,
        worker_id="worker-2",
        token="token-2",  # noqa: S106 - fixture fencing token
        acquired_at=_START + timedelta(minutes=1),
        renewed_at=_START + timedelta(minutes=1),
        expires_at=_START + timedelta(minutes=1, seconds=30),
    )
    with store.write_transaction() as connection:
        store.insert_run_in_transaction(connection, run)
        store.put_lease_in_transaction(connection, first)
        assert (
            store.read_lease_in_transaction(connection, run.procedure_run_id) == first
        )
        store.put_lease_in_transaction(connection, second)
        assert (
            store.read_lease_in_transaction(connection, run.procedure_run_id) == second
        )
        with pytest.raises(AutomationConflict, match="not held"):
            store.delete_lease_in_transaction(
                connection,
                run.procedure_run_id,
                token=first.token,
            )


def test_store_lists_exact_capability_ready_and_expired_runs_oldest_first(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    ready = _run(procedure_run_id="ready")
    expired = _run(procedure_run_id="expired").model_copy(
        update={"revision": 2, "state": "leased"}
    )
    live = _run(procedure_run_id="live").model_copy(
        update={"revision": 2, "state": "leased"}
    )
    incompatible = _run(procedure_run_id="incompatible").model_copy(
        update={
            "definition": ProcedureDefinitionRef(
                id=ready.definition.id,
                version=ready.definition.version,
                fingerprint="sha256:" + "9" * 64,
            )
        }
    )
    incompatible = incompatible.model_copy(
        update={
            "intent_hash": procedure_intent_hash(
                incompatible.definition,
                incompatible.intent,
            )
        }
    )
    with store.write_transaction() as connection:
        for run in (ready, incompatible, expired, live):
            store.insert_run_in_transaction(connection, run)
        store.put_lease_in_transaction(
            connection,
            ProcedureLeaseRecord(
                procedure_run_id=expired.procedure_run_id,
                worker_id="old-worker",
                token="expired-token",  # noqa: S106 - fixture fencing token
                acquired_at=_START,
                renewed_at=_START,
                expires_at=_START + timedelta(seconds=10),
            ),
        )
        store.put_lease_in_transaction(
            connection,
            ProcedureLeaseRecord(
                procedure_run_id=live.procedure_run_id,
                worker_id="live-worker",
                token="live-token",  # noqa: S106 - fixture fencing token
                acquired_at=_START,
                renewed_at=_START,
                expires_at=_START + timedelta(minutes=2),
            ),
        )

    offset_now = (_START + timedelta(seconds=30)).astimezone(
        timezone(timedelta(hours=8))
    )
    first = store.list_runnable((ready.definition,), at=offset_now, limit=1)
    assert first.items == (ready,)
    assert first.has_more is True
    assert store.list_runnable(
        (ready.definition,),
        at=offset_now,
        limit=10,
    ).items == (ready, expired)
    assert store.list_runnable(
        (incompatible.definition,),
        at=offset_now,
        limit=10,
    ).items == (incompatible,)
