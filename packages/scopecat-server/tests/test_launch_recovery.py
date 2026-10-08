from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import pytest
from scopecat.application.launch import LaunchCatalogEntry, LaunchInputSchema
from scopecat.automation import (
    ProcedureDefinitionRef,
    ProcedureRun,
    ProcedureSource,
    procedure_intent_hash,
)
from scopecat.records.author_revision import AuthorRevisionRef
from scopecat.records.launch_request import LaunchRequest
from scopecat.records.parameter_revision import ParameterRevisionRef
from scopecat.records.run import ParameterRunConfigSource
from scopecat.records.scientific_binding import (
    ResolvedScientificBinding,
    UnboundSubject,
)
from scopecat.records.scientific_selection import (
    ReviewedScientificSelection,
    ScientificSelection,
)
from scopecat.records.setup import SetupRevisionRef

from scopecat_server.errors import BackendConflict
from scopecat_server.launch_recovery import (
    LaunchAttemptSave,
    LaunchDraftInput,
    LaunchDraftSave,
    LaunchDraftTarget,
    RawControl,
)
from scopecat_server.services.launch_recovery import LaunchRecoveryService
from scopecat_server.storage.sqlite.automation import SQLiteAutomationStore
from scopecat_server.storage.sqlite.connection import SQLiteDatabase
from scopecat_server.storage.sqlite.project_store import SQLiteProjectStore


def store_at(root: Path):
    store = SQLiteProjectStore(
        SQLiteDatabase(root / "control.sqlite3"), root / "objects"
    )
    store.bootstrap()
    return store


def command(text: str = "1, nope, 3") -> LaunchDraftSave:
    declaration = LaunchCatalogEntry(
        id="signal",
        version="1",
        title="Signal",
        description="",
        actions=("preview", "submit"),
        kind="diagnostic",
        configuration_effect="none",
        request=LaunchInputSchema.model_validate(
            {"properties": {"center": {"type": "number", "default": 0}}}
        ),
    )
    return LaunchDraftSave(
        operation_id=uuid4(),
        target=LaunchDraftTarget(workspace_id="author", experiment="signal"),
        expected_revision=0,
        input=LaunchDraftInput(
            declaration=declaration,
            values={"center": "-", "removed": "valuable"},
            controls={"position": RawControl(mode="values", values=text)},
            selection=ScientificSelection(),
            actor="operator",
        ),
    )


def test_raw_input_conflict_retry_and_reopen(tmp_path: Path):
    store = store_at(tmp_path)
    service = LaunchRecoveryService(store.sqlite)
    first = command()
    saved = service.save(first)
    assert saved.saved is not None
    assert service.save(first).saved == saved.saved
    concurrent = [command("window one"), command("window two")]
    with ThreadPoolExecutor(max_workers=2) as pool:
        copies = list(pool.map(service.save, concurrent))
    assert all(item.saved and item.saved.state == "conflict" for item in copies)
    reopened = LaunchRecoveryService(store_at(tmp_path).sqlite)
    assert reopened.read(first.target).head == saved.saved
    assert len(reopened.history().items) == 3
    assert saved.saved.input.values == {"center": "-", "removed": "valuable"}
    assert saved.saved.input.controls["position"].values == "1, nope, 3"
    with pytest.raises(BackendConflict, match="operation identity"):
        service.save(first.model_copy(update={"discard": True}))
    # Explicit choice uses the observed head; both former copies remain retained.
    adopted = service.save(
        concurrent[0].model_copy(
            update={"operation_id": uuid4(), "expected_revision": saved.saved.revision}
        )
    )
    assert adopted.saved and adopted.saved.state == "saved"
    assert len(service.history().items) == 4
    # Stale discard cannot remove another window's chosen input.
    discarded = service.save(
        first.model_copy(update={"operation_id": uuid4(), "discard": True})
    )
    assert discarded.saved and discarded.saved.state == "conflict"
    assert service.read(first.target).head == adopted.saved


def original() -> LaunchAttemptSave:
    source = ParameterRunConfigSource(
        parameters=ParameterRevisionRef(
            revision_id="config", content_hash="sha256:" + "a" * 64
        ),
        setup=SetupRevisionRef(revision_id="bench", content_hash="sha256:" + "e" * 64),
        content_hash="sha256:" + "a" * 64,
    )
    reviewed = ReviewedScientificSelection(
        binding=ResolvedScientificBinding(
            subject=UnboundSubject(),
            config_content_hash=source.content_hash,
            setup_content_hash="sha256:" + "e" * 64,
        ),
        config_source=source,
    )
    request = LaunchRequest(
        workspace_id="author",
        action="preview",
        experiment="signal",
        version="1",
        reviewed=reviewed,
        code_revision=AuthorRevisionRef(content_hash="sha256:" + "b" * 64),
    )
    request = LaunchRequest.model_validate(
        {
            **request.model_dump(),
            "action": "submit",
            "request_key": "retained",
            "expected_request_hash": request.request_hash,
        }
    )
    return LaunchAttemptSave(
        definition=ProcedureDefinitionRef(
            id="scopecat.author:signal", version="1", fingerprint="sha256:" + "c" * 64
        ),
        request=request,
    )


def admitted(command: LaunchAttemptSave) -> ProcedureRun:
    request = command.request
    assert request.reviewed and request.code_revision
    intent = {
        "request_hash": request.expected_request_hash,
        "config_source": request.reviewed.config_source.model_dump(mode="json"),
        "manual_state": None,
    }
    return ProcedureRun(
        procedure_run_id="original-procedure",
        request_key=request.request_key,
        definition=command.definition,
        intent=intent,
        intent_hash=procedure_intent_hash(
            command.definition,
            intent,
            scientific_binding=request.reviewed.binding,
            source=ProcedureSource(
                workspace_id=request.workspace_id, code_revision=request.code_revision
            ),
        ),
        revision=1,
        state="ready",
        source=ProcedureSource(
            workspace_id=request.workspace_id, code_revision=request.code_revision
        ),
        scientific_binding=request.reviewed.binding,
    )


def test_attempt_retained_before_admission_and_recovered_read_only(tmp_path: Path):
    store = store_at(tmp_path)
    service = LaunchRecoveryService(store.sqlite)
    command = original()
    receipt = service.retain(command)
    assert service.retain(command) == receipt
    assert service.resolve(receipt.sequence).procedure_id is None
    run = admitted(command)
    with store.sqlite.write_transaction() as connection:
        SQLiteAutomationStore(store.sqlite).insert_run_in_transaction(connection, run)
    reopened = LaunchRecoveryService(store_at(tmp_path).sqlite)
    assert reopened.resolve(receipt.sequence).procedure_id == run.procedure_run_id
    assert reopened.attempts().items == [receipt]
    # Same key under a different definition never resolves this task.
    other = command.model_copy(
        update={"definition": command.definition.model_copy(update={"id": "different"})}
    )
    assert reopened.resolve(reopened.retain(other).sequence).procedure_id is None
    with pytest.raises(BackendConflict, match="different input"):
        reopened.retain(
            command.model_copy(
                update={
                    "definition": command.definition.model_copy(
                        update={"fingerprint": "sha256:" + "d" * 64}
                    )
                }
            )
        )


@pytest.mark.parametrize(
    "mismatch", ["fingerprint", "hash", "config", "binding", "source", "manual", "plan"]
)
def test_recovery_rejects_identity_collision(tmp_path: Path, mismatch: str):
    store = store_at(tmp_path)
    service = LaunchRecoveryService(store.sqlite)
    command = original()
    receipt = service.retain(command)
    run = admitted(command)
    if mismatch == "fingerprint":
        run = run.model_copy(
            update={
                "definition": run.definition.model_copy(
                    update={"fingerprint": "sha256:" + "d" * 64}
                )
            }
        )
    elif mismatch in {"hash", "config", "manual"}:
        key = {
            "hash": "request_hash",
            "config": "config_source",
            "manual": "manual_state",
        }[mismatch]
        run = run.model_copy(update={"intent": {**run.intent, key: "different"}})
    elif mismatch == "binding":
        run = run.model_copy(update={"scientific_binding": None})
    elif mismatch == "source":
        run = run.model_copy(update={"source": None})
    else:
        from scopecat.records.plan_ref import ExperimentPlanRef

        run = run.model_copy(
            update={
                "plan_ref": ExperimentPlanRef(
                    plan_id="plan", revision=1, content_hash="sha256:" + "d" * 64
                )
            }
        )
    run = run.model_copy(
        update={
            "intent_hash": procedure_intent_hash(
                run.definition,
                run.intent,
                scientific_binding=run.scientific_binding,
                source=run.source,
                plan_ref=run.plan_ref,
            )
        }
    )
    with store.sqlite.write_transaction() as connection:
        SQLiteAutomationStore(store.sqlite).insert_run_in_transaction(connection, run)
    with pytest.raises(BackendConflict, match="identity"):
        service.resolve(receipt.sequence)


def test_current_backup_retains_raw_conflicts_and_original_requests(tmp_path: Path):
    from scopecat.project import load_project

    from scopecat_server.snapshots import create_snapshot, restore_snapshot

    root = tmp_path / "source"
    root.mkdir()
    (root / "scopecat.toml").write_text("[lab]\n")
    store = store_at(root / ".scopecat")
    service = LaunchRecoveryService(store.sqlite)
    first = command()
    service.save(first)
    service.save(command("conflicting raw input"))
    receipt = service.retain(original())
    edits = service.history()
    store.close()
    create_snapshot(load_project(root / "scopecat.toml"), tmp_path / "backup")
    restore_snapshot(tmp_path / "backup", tmp_path / "restored")
    restored = store_at(tmp_path / "restored" / ".scopecat")
    reopened = LaunchRecoveryService(restored.sqlite)
    assert reopened.history() == edits
    assert reopened.attempts().items == [receipt]
    assert reopened.resolve(receipt.sequence).procedure_id is None
    restored.close()
