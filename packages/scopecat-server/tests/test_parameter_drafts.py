"""Real application transactions for recoverable parameter edits, without devices."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from scopecat.daemon.wire import ParameterBranchCommitCommand, ParameterSaveCommand
from scopecat.kernel.quantity import Quantity
from scopecat.records.parameter import ParameterCatalog, ParameterSnapshot
from scopecat.records.parameter_revision import ParameterRevision

from scopecat_server.errors import BackendConflict
from scopecat_server.parameter_drafts import (
    ParameterDraft,
    ParameterDraftCommit,
    ParameterDraftSave,
    ParameterDraftStart,
    ParameterDraftView,
)
from scopecat_server.runtime import LocalDaemonRuntime
from scopecat_server.storage.sqlite import parameter_drafts as storage


def seed(runtime: LocalDaemonRuntime) -> ParameterRevision:
    return runtime.application.config.save_parameters(
        ParameterSaveCommand(
            revision_id="initial",
            actor="author",
            catalog=ParameterCatalog.model_validate(
                {
                    "id": "catalog",
                    "definitions": [
                        {
                            "id": "frequency",
                            "value_type": {
                                "shape": "scalar",
                                "atom": {
                                    "type": "quantity",
                                    "finite": True,
                                    "unit": "GHz",
                                },
                            },
                        },
                        {
                            "id": "count",
                            "value_type": {"shape": "scalar", "atom": {"type": "int"}},
                        },
                    ],
                }
            ),
            parameters=ParameterSnapshot.model_validate(
                {
                    "id": "values",
                    "values": [
                        {
                            "id": "frequency",
                            "shape": "scalar",
                            "value": {"value": 4.8, "unit": "GHz"},
                        },
                        {"id": "count", "shape": "scalar", "value": 1},
                    ],
                }
            ),
        )
    )


def edit(
    view: ParameterDraftView, text: str = "1e", **fields: object
) -> ParameterDraftSave:
    input = view.draft.input.model_dump()
    input.update(fields)
    input["values"][0]["value"]["text"] = text
    return ParameterDraftSave.model_validate(
        {"expected_revision": view.head_revision, "input": input}
    )


def test_reopen_invalid_input_conflicts_fork_discard_and_separate_homes(tmp_path: Path):
    home = tmp_path / "a"
    with LocalDaemonRuntime(home) as runtime:
        base = seed(runtime)
        service = runtime.application.parameter_drafts
        start = ParameterDraftStart(draft_id=uuid4(), base=base.ref, actor="operator")
        initial = service.start(start)
        invalid = service.save(
            initial.draft.draft_id, edit(initial, note="long reasoning")
        )
        assert service.start(start).draft == invalid.draft
        assert (
            service.start(start.model_copy(update={"draft_id": uuid4()})).draft
            == invalid.draft
        )
        assert len(runtime.application.config.parameter_revisions()) == 1
        with pytest.raises(BackendConflict, match="Complete parameter input"):
            service.commit(
                invalid.draft.draft_id,
                ParameterDraftCommit(expected_revision=invalid.head_revision),
            )
        assert service.read(invalid.draft.draft_id).draft.state == "saved"

        def concurrent_save(note: str) -> ParameterDraftView:
            return service.save(invalid.draft.draft_id, edit(invalid, note=note))

        with ThreadPoolExecutor(2) as workers:
            results = list(
                workers.map(
                    concurrent_save,
                    ["A", "B"],
                )
            )
        assert sorted(item.draft.state for item in results) == ["conflict", "saved"]
        head = service.read(invalid.draft.draft_id)
        fork = service.start(
            ParameterDraftStart(
                draft_id=uuid4(),
                base=base.ref,
                actor="operator",
                copy_from=head.draft.draft_id,
            )
        )
        assert fork.draft.draft_id != head.draft.draft_id
        assert fork.draft.input == head.draft.input
        discarded = service.save(
            fork.draft.draft_id,
            ParameterDraftSave(
                expected_revision=fork.head_revision,
                input=fork.draft.input,
                discard=True,
            ),
        )
        assert discarded.draft.state == "discarded"
        assert service.read(head.draft.draft_id).draft.state == "saved"
        assert len(service.history(base.id).items) == 6
    with LocalDaemonRuntime(home) as runtime:
        recovered = runtime.application.parameter_drafts.read(head.draft.draft_id)
        assert recovered.draft == head.draft
        assert recovered.draft.input.values[0].value is not None
        assert recovered.draft.input.values[0].value.text == "1e"
    with LocalDaemonRuntime(tmp_path / "b") as runtime:
        other = seed(runtime)
        clean = runtime.application.parameter_drafts.start(
            ParameterDraftStart(draft_id=uuid4(), base=other.ref, actor="other")
        )
        assert clean.draft.input.values[0].value is not None
        assert clean.draft.input.values[0].value.text == "4.8"


def test_atomic_completion_replay_and_changed_branch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    with LocalDaemonRuntime(tmp_path) as runtime:
        base = seed(runtime)
        config = runtime.application.config
        service = runtime.application.parameter_drafts
        config.commit_parameter_branch(
            ParameterBranchCommitCommand(
                name="daily", expected_generation=0, source=base.ref, actor="author"
            )
        )
        initial = service.start(
            ParameterDraftStart(draft_id=uuid4(), base=base.ref, actor="operator")
        )
        stale = service.save(
            initial.draft.draft_id,
            edit(initial, "5.1", name="saved", branch="daily", branch_generation=0),
        )
        assert stale.branch_changed
        with pytest.raises(BackendConflict, match="Branch changed"):
            service.commit(
                stale.draft.draft_id,
                ParameterDraftCommit(expected_revision=stale.head_revision),
            )
        valid = service.save(
            stale.draft.draft_id, edit(stale, "5.1", branch_generation=1)
        )
        original = storage.append

        def fail_completed(
            connection: sqlite3.Connection, draft: ParameterDraft
        ) -> ParameterDraft:
            if draft.state == "completed":
                raise RuntimeError("interrupt before completion")
            return original(connection, draft)

        with monkeypatch.context() as patch:
            patch.setattr(storage, "append", fail_completed)
            with pytest.raises(RuntimeError, match="interrupt"):
                service.commit(
                    valid.draft.draft_id,
                    ParameterDraftCommit(expected_revision=valid.head_revision),
                )
        assert len(config.parameter_revisions()) == 1
        assert config.parameter_branch("daily").generation == 1
        command = ParameterDraftCommit(expected_revision=valid.head_revision)
        completed = service.commit(valid.draft.draft_id, command)
        assert completed.draft.state == "completed"
        assert not completed.branch_changed
        assert service.commit(valid.draft.draft_id, command) == completed
        assert config.parameter_branch("daily").generation == 2
        assert len(config.parameter_revisions()) == 2
        late = service.save(valid.draft.draft_id, edit(valid, "6e"))
        assert late.draft.state == "conflict"
        assert service.read(valid.draft.draft_id).draft == completed.draft
        assert service.commit(valid.draft.draft_id, command) == completed


def test_http_raw_inputs_and_explicit_commit(tmp_path: Path):
    with LocalDaemonRuntime(tmp_path) as runtime, TestClient(runtime.app()) as client:
        base = seed(runtime)
        response = client.post(
            "/api/v1/parameter-drafts/start",
            json={
                "draft_id": str(uuid4()),
                "base": base.ref.model_dump(),
                "actor": "author",
            },
        )
        assert response.status_code == 200, response.text
        view = response.json()
        draft_id = view["draft"]["draft_id"]
        input = view["draft"]["input"]
        input["name"] = "revised"
        input["values"][1]["value"]["text"] = "1e3"
        saved = client.post(
            f"/api/v1/parameter-drafts/{draft_id}/save",
            json={"expected_revision": view["head_revision"], "input": input},
        )
        assert saved.status_code == 200, saved.text
        committed = client.post(
            f"/api/v1/parameter-drafts/{draft_id}/commit",
            json={"expected_revision": saved.json()["head_revision"]},
        )
        assert committed.status_code == 200, committed.text
        value = runtime.application.config.parameter_revision(
            "revised"
        ).parameters.values[1]
        assert value.shape == "scalar" and value.value == 1000


def test_working_branch_recovers_original_base_and_freezes_without_publication(
    tmp_path: Path,
):
    with LocalDaemonRuntime(tmp_path) as runtime:
        base = seed(runtime)
        config = runtime.application.config
        service = runtime.application.parameter_drafts
        branch = config.commit_parameter_branch(
            ParameterBranchCommitCommand(
                name="daily", expected_generation=0, source=base.ref, actor="author"
            )
        )
        initial = service.start(
            ParameterDraftStart(
                draft_id=uuid4(),
                base=base.ref,
                actor="author",
                working_branch="daily",
                branch_generation=branch.generation,
            )
        )
        valid = service.save(initial.draft.draft_id, edit(initial, "5.2"))
        frozen = service.freeze(
            valid.draft.draft_id,
            ParameterDraftCommit(expected_revision=valid.head_revision),
        )
        assert frozen.configuration.ref == base.ref
        assert len(frozen.configuration.overrides) == 1
        assert len(config.parameter_revisions()) == 1
        assert config.parameter_branch("daily").generation == 1
        assert service.read(valid.draft.draft_id).draft.state == "saved"
        invalid = service.save(valid.draft.draft_id, edit(valid))
        with pytest.raises(BackendConflict, match="Complete working input"):
            service.freeze(
                invalid.draft.draft_id,
                ParameterDraftCommit(expected_revision=invalid.head_revision),
            )
        # Frozen input remains independent from subsequent editor mutations.
        override = frozen.configuration.overrides[0]
        assert override.kind == "replace_parameter"
        assert override.value.shape == "scalar"
        assert isinstance(override.value.value, Quantity)
        assert override.value.value.value == 5.2
        config.commit_parameter_branch(
            ParameterBranchCommitCommand(
                name="daily", expected_generation=1, source=base.ref, actor="external"
            )
        )
        recovered = service.start(
            ParameterDraftStart(
                draft_id=uuid4(),
                base=base.ref,
                actor="author",
                working_branch="daily",
                branch_generation=2,
            )
        )
        assert recovered.draft == invalid.draft
        assert recovered.branch_changed
        with pytest.raises(BackendConflict, match="Branch changed"):
            service.freeze(
                recovered.draft.draft_id,
                ParameterDraftCommit(expected_revision=recovered.head_revision),
            )
        with pytest.raises(BackendConflict, match="retains its branch"):
            service.save(recovered.draft.draft_id, edit(recovered, "5.3", branch=""))
        with pytest.raises(BackendConflict, match="independent"):
            service.start(
                ParameterDraftStart(
                    draft_id=uuid4(),
                    base=base.ref,
                    actor="author",
                    working_branch="other",
                    copy_from=recovered.draft.draft_id,
                )
            )
        reviewed = service.save(
            recovered.draft.draft_id, edit(recovered, "5.3", branch_generation=2)
        )
        assert (
            service.freeze(
                reviewed.draft.draft_id,
                ParameterDraftCommit(expected_revision=reviewed.head_revision),
            ).configuration.ref
            == base.ref
        )
        assert config.parameter_branch("daily").generation == 2


def test_copy_exact_conflict_history_and_unknown_freeze_limit(tmp_path: Path):
    with LocalDaemonRuntime(tmp_path) as runtime:
        base = seed(runtime)
        service = runtime.application.parameter_drafts
        original = service.start(
            ParameterDraftStart(draft_id=uuid4(), base=base.ref, actor="author")
        )
        service.save(original.draft.draft_id, edit(original, "5.1"))
        conflict = service.save(original.draft.draft_id, edit(original, "6e"))
        copy = service.start(
            ParameterDraftStart(
                draft_id=uuid4(),
                base=base.ref,
                actor="author",
                copy_from=original.draft.draft_id,
                copy_revision=conflict.draft.revision,
            )
        )
        assert copy.draft.input.values[0].value is not None
        assert copy.draft.input.values[0].value.text == "6e"
        unknown = service.save(
            copy.draft.draft_id,
            ParameterDraftSave(
                expected_revision=copy.head_revision,
                input=copy.draft.input.model_copy(update={"values": []}),
            ),
        )
        with pytest.raises(BackendConflict, match="checkpoint"):
            service.freeze(
                unknown.draft.draft_id,
                ParameterDraftCommit(expected_revision=unknown.head_revision),
            )


def test_current_backup_restores_raw_conflicting_and_completed_history(tmp_path: Path):
    from scopecat.project import load_project

    from scopecat_server.snapshots import create_snapshot, restore_snapshot

    home = tmp_path / "source"
    home.mkdir()
    (home / "scopecat.toml").write_text("[lab]\n")
    with LocalDaemonRuntime(home) as runtime:
        base = seed(runtime)
        service = runtime.application.parameter_drafts
        initial = service.start(
            ParameterDraftStart(draft_id=uuid4(), base=base.ref, actor="author")
        )
        valid = service.save(initial.draft.draft_id, edit(initial, "5.2"))
        service.save(initial.draft.draft_id, edit(initial, "-"))
        service.commit(
            valid.draft.draft_id,
            ParameterDraftCommit(expected_revision=valid.head_revision),
        )
        history = service.history(base.id)
    create_snapshot(load_project(home / "scopecat.toml"), tmp_path / "backup")
    restore_snapshot(tmp_path / "backup", tmp_path / "restored")
    with LocalDaemonRuntime(tmp_path / "restored") as runtime:
        assert runtime.application.parameter_drafts.history(base.id) == history
