from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest
from scopecat.analysis.facts import AnalysisFactSchema
from scopecat.automation import (
    InterpretationRequest,
    ProcedureDefinitionRef,
    ProcedureRun,
    ProcedureStepAttempt,
    procedure_intent_hash,
)
from scopecat.project import load_project

from scopecat_server.decision_drafts import (
    DecisionDraftBaseline,
    DecisionDraftInput,
    DecisionDraftSave,
    DecisionDraftTarget,
)
from scopecat_server.errors import BackendNotFound
from scopecat_server.services.decision_drafts import DecisionDraftService
from scopecat_server.snapshots import create_snapshot, restore_snapshot
from scopecat_server.storage.sqlite.automation import SQLiteAutomationStore
from scopecat_server.storage.sqlite.connection import SQLiteDatabase
from scopecat_server.storage.sqlite.project_store import (
    SchemaVersionError,
    SQLiteProjectStore,
)


@dataclass
class Answer:
    frequency: float
    rationale: str


def make_store(root: Path):
    store = SQLiteProjectStore(
        SQLiteDatabase(root / "control.sqlite3"), root / "objects"
    )
    store.bootstrap()
    return store


def seed(store: SQLiteProjectStore) -> DecisionDraftSave:
    now = datetime.now(UTC)
    schema = AnalysisFactSchema("review.v1", Answer)
    request = InterpretationRequest(
        title="Review",
        instructions="Choose",
        schema_id="review.v1",
        schema_hash=schema.schema_hash,
        structure=schema.structure,
    )
    definition = ProcedureDefinitionRef(
        id="review", version="1", fingerprint="sha256:" + "2" * 64
    )
    run = ProcedureRun(
        procedure_run_id="procedure-1",
        request_key="test",
        definition=definition,
        intent={},
        intent_hash=procedure_intent_hash(definition, {}),
        revision=4,
        state="waiting_for_input",
        created_at=now,
        updated_at=now,
    )
    step = ProcedureStepAttempt(
        procedure_run_id=run.procedure_run_id,
        step_key="select/peak",
        attempt=1,
        operation="interpretation",
        intent_hash=request.request_hash,
        revision=2,
        state="waiting_for_input",
        started_at=now,
        updated_at=now,
        interpretation_request=request,
    )
    automation = SQLiteAutomationStore(store.sqlite)
    with store.sqlite.write_transaction() as connection:
        automation.insert_run_in_transaction(connection, run)
        automation.insert_step_attempt_in_transaction(connection, step)
    return DecisionDraftSave(
        target=DecisionDraftTarget(
            procedure_run_id=run.procedure_run_id,
            step_key=step.step_key,
            attempt=step.attempt,
        ),
        baseline=DecisionDraftBaseline(
            run_revision=run.revision,
            step_revision=step.revision,
            request_hash=step.intent_hash,
        ),
        expected_revision=0,
        input=DecisionDraftInput(
            actor="",
            actor_kind="human",
            note="unfinished reasoning",
            value_text='{"frequencies": ["1e", "-"]',
            use_json=True,
        ),
    )


def test_invalid_input_reopens_per_home_without_scientific_effects(tmp_path: Path):
    store = make_store(tmp_path / "one")
    command = seed(store)
    service = DecisionDraftService(store.sqlite)
    saved = service.save(command)
    assert saved.draft is not None and saved.draft.input == command.input
    with store.sqlite.read_transaction() as connection:
        assert (
            connection.execute("SELECT state FROM procedure_runs").fetchone()[0]
            == "waiting_for_input"
        )
        assert (
            connection.execute("SELECT state FROM procedure_step_attempts").fetchone()[
                0
            ]
            == "waiting_for_input"
        )
        assert connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0
    store.close()
    reopened = make_store(tmp_path / "one")
    assert DecisionDraftService(reopened.sqlite).read(command.target) == saved
    other = make_store(tmp_path / "two")
    seed(other)  # Same real logical ids, different data home.
    assert DecisionDraftService(other.sqlite).read(command.target).draft is None
    other.close()
    reopened.close()


def test_cas_retains_conflicts_and_discard_history(tmp_path: Path):
    store = make_store(tmp_path)
    command = seed(store)
    service = DecisionDraftService(store.sqlite)
    commands = [
        command.model_copy(
            update={"input": command.input.model_copy(update={"note": str(i)})}
        )
        for i in range(2)
    ]
    with ThreadPoolExecutor(max_workers=2) as pool:
        saved = list(pool.map(service.save, commands))
    assert {view.draft.state for view in saved if view.draft} == {"saved", "conflict"}
    current = service.read(command.target)
    assert current.draft is not None
    history = service.history(limit=1)
    assert history.next_cursor is not None
    older = service.history(before=history.next_cursor, limit=1)
    assert {history.items[0].input.note, older.items[0].input.note} == {"0", "1"}
    discarded = service.save(
        command.model_copy(
            update={"expected_revision": current.head_revision, "discard": True}
        )
    )
    assert discarded.draft is not None and discarded.draft.state == "discarded"
    assert len(service.history().items) == 3
    assert service.read(command.target) == discarded
    store.close()


def test_changed_or_completed_request_keeps_input_but_invalidates_authority(
    tmp_path: Path,
):
    store = make_store(tmp_path)
    command = seed(store)
    service = DecisionDraftService(store.sqlite)
    service.save(command)
    # Use stored models just as an ordinary procedure transition does.
    automation = SQLiteAutomationStore(store.sqlite)
    with store.sqlite.write_transaction() as connection:
        run = automation.read_run_in_transaction(
            connection, command.target.procedure_run_id
        )
        changed = run.model_copy(update={"revision": run.revision + 1})
        connection.execute(
            "UPDATE procedure_runs SET run_json=?, revision=?",
            (changed.model_dump_json(), changed.revision),
        )
    assert service.read(command.target).validity == "baseline_changed"
    with store.sqlite.write_transaction() as connection:
        changed = changed.model_copy(update={"state": "ready"})
        connection.execute(
            "UPDATE procedure_runs SET run_json=?, state='ready'",
            (changed.model_dump_json(),),
        )
    restored = service.read(command.target)
    assert restored.validity == "no_longer_waiting"
    assert restored.draft is not None and restored.draft.input == command.input
    # Saving a late response after completion cannot re-enable the decision.
    assert service.save(command).validity == "no_longer_waiting"
    unknown = command.model_copy(
        update={"target": command.target.model_copy(update={"attempt": 2})}
    )
    with pytest.raises(BackendNotFound):
        service.save(unknown)
    store.close()


def test_current_backup_includes_saved_conflicting_and_discarded_drafts(tmp_path: Path):
    root = tmp_path / "source"
    root.mkdir()
    (root / "scopecat.toml").write_text("[lab]\n")
    store = make_store(root / ".scopecat")
    command = seed(store)
    service = DecisionDraftService(store.sqlite)
    first = service.save(command)
    service.save(command)
    service.save(
        command.model_copy(
            update={"expected_revision": first.head_revision, "discard": True}
        )
    )
    history = service.history()
    store.close()
    create_snapshot(load_project(root / "scopecat.toml"), tmp_path / "backup")
    restore_snapshot(tmp_path / "backup", tmp_path / "restored")
    restored = make_store(tmp_path / "restored" / ".scopecat")
    assert DecisionDraftService(restored.sqlite).history() == history
    restored.close()


def test_previous_development_schema_is_rejected_without_migration_or_deletion(
    tmp_path: Path,
):
    store = make_store(tmp_path)
    with store.sqlite.write_transaction() as connection:
        connection.execute("UPDATE project_schema SET version=110")
    store.close()
    before = {
        path.name: path.read_bytes() for path in tmp_path.iterdir() if path.is_file()
    }
    with pytest.raises(SchemaVersionError, match="expected 113"):
        make_store(tmp_path)
    assert before == {
        path.name: path.read_bytes() for path in tmp_path.iterdir() if path.is_file()
    }
