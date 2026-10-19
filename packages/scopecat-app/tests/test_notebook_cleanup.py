"""Only the explicitly enrolled course directory can be removed, including retry."""

from pathlib import Path
from unittest.mock import Mock

import httpx2
import pytest

from scopecat.author_workspaces import LocalAuthorWorkspace, LocalAuthorWorkspaces
from scopecat.records.data_cleanup import DataCleanupPreview, DataCleanupSelection
from scopecat.records.practice import NotebookPracticeClearCommand, PracticeScope
from scopecat_app import notebook_cleanup as cleanup
from scopecat_app.notebook_journey import (
    NotebookJourney,
    current,
    receipt_path,
    save_journey,
)


@pytest.fixture
def registered_course(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    runtime = Mock(home=tmp_path / "home", root=tmp_path / "service")
    runtime.status.return_value.record.base_url = "http://127.0.0.1:1234"
    directory = tmp_path / "edited-course"
    directory.mkdir()
    (directory / "notes.txt").write_text("my edits")
    info = directory.stat()
    course = NotebookJourney(
        directory=directory,
        ready=True,
        registered_context=True,
        scope_id="course",
        workspace_id="source",
        directory_device=info.st_dev,
        directory_inode=info.st_ino,
    )
    receipt = receipt_path(runtime, "parameters")
    receipt.parent.mkdir(parents=True)
    save_journey(receipt, course)
    registry = tmp_path / "bindings.json"
    registry.write_text(
        LocalAuthorWorkspaces(
            service_root=runtime.root,
            items=(
                LocalAuthorWorkspace(
                    id="source",
                    name="Course",
                    root=directory,
                    python=directory / ".venv/bin/python",
                ),
            ),
        ).model_dump_json()
    )
    monkeypatch.setattr(cleanup, "author_bindings_path", lambda _root: registry)
    scope = PracticeScope(
        id="course",
        title="Course",
        lesson="parameters",
        directory=str(directory),
        kind="notebook",
        workspace_id="source",
        state="cleared",
    )
    post = Mock(
        return_value=httpx2.Response(
            200,
            content=scope.model_dump_json(),
            request=httpx2.Request("POST", "http://local/clear"),
        )
    )
    monkeypatch.setattr(cleanup.httpx2, "post", post)
    preview = DataCleanupPreview(
        selection=DataCleanupSelection(),
        fingerprint="empty",
        blockers=(),
        record_count=0,
        bytes_to_reclaim=0,
    )
    return runtime, course, registry, preview, post


def test_default_clear_keeps_edits_and_explicit_discard_removes_only_owned_folder(
    registered_course, tmp_path: Path
):
    runtime, course, registry, preview, _ = registered_course
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("keep")
    (course.directory / "link").symlink_to(outside, target_is_directory=True)
    command = NotebookPracticeClearCommand(preview=preview)
    result = cleanup.clear(runtime, "parameters", command)
    assert (
        result["cleared"] and (course.directory / "notes.txt").read_text() == "my edits"
    )
    assert LocalAuthorWorkspaces.model_validate_json(registry.read_bytes()).items
    # A fresh fixture receipt represents a separate explicit cleanup choice.
    save_journey(receipt_path(runtime, "parameters"), course)
    command = NotebookPracticeClearCommand(preview=preview, files="discard")
    assert cleanup.clear(runtime, "parameters", command)["cleared"]
    assert not course.directory.exists()
    assert (outside / "keep.txt").read_text() == "keep"
    assert not LocalAuthorWorkspaces.model_validate_json(registry.read_bytes()).items
    assert cleanup.clear(runtime, "parameters", command)["cleared"]


def test_replaced_directory_is_refused_before_data_request(registered_course):
    runtime, course, _, preview, post = registered_course
    course.directory.rename(course.directory.with_name("original"))
    course.directory.mkdir()
    with pytest.raises(ValueError, match="替换"):
        cleanup.clear(
            runtime,
            "parameters",
            NotebookPracticeClearCommand(preview=preview, files="discard"),
        )
    post.assert_not_called()
    assert course.directory.exists()


def test_failed_source_removal_resumes_exact_quarantined_directory(
    registered_course, monkeypatch: pytest.MonkeyPatch
):
    runtime, course, registry, preview, _ = registered_course
    remove = cleanup.shutil.rmtree
    monkeypatch.setattr(cleanup.shutil, "rmtree", Mock(side_effect=OSError("busy")))
    command = NotebookPracticeClearCommand(preview=preview, files="discard")
    with pytest.raises(OSError, match="busy"):
        cleanup.clear(runtime, "parameters", command)
    retained = current(runtime)
    assert (
        retained is not None
        and retained.cleanup_command == command
        and not retained.cleared
    )
    assert not course.directory.exists()
    monkeypatch.setattr(cleanup.shutil, "rmtree", remove)
    assert cleanup.clear(runtime, "parameters", command)["cleared"]
    assert not LocalAuthorWorkspaces.model_validate_json(registry.read_bytes()).items


def test_old_unowned_receipt_is_not_adopted(registered_course):
    runtime, course, _, preview, post = registered_course
    save_journey(
        receipt_path(runtime, "parameters"),
        NotebookJourney(directory=course.directory, ready=True),
    )
    with pytest.raises(ValueError, match="可靠归属"):
        cleanup.clear(
            runtime, "parameters", NotebookPracticeClearCommand(preview=preview)
        )
    post.assert_not_called()


@pytest.mark.parametrize("retained", [False, True])
@pytest.mark.parametrize("target_inside", [False, True])
def test_shared_interpreter_link_and_target_block_before_data_request(
    registered_course, tmp_path: Path, retained: bool, target_inside: bool
):
    runtime, course, registry, preview, post = registered_course
    other = tmp_path / "other-author"
    other.mkdir()
    target = (course.directory if target_inside else other) / "python-target"
    target.write_text("shared interpreter")
    link = (other if target_inside else course.directory) / "python-link"
    link.symlink_to(target)
    original = LocalAuthorWorkspaces.model_validate_json(registry.read_bytes())
    shared = LocalAuthorWorkspace(
        id="other",
        name="Other",
        root=other,
        python=other / "current-python" if retained else link,
        retained_pythons=(link,) if retained else (),
    )
    registry.write_text(
        original.model_copy(
            update={"items": (*original.items, shared)}
        ).model_dump_json()
    )
    before = registry.read_bytes()
    with pytest.raises(ValueError, match="其他作者环境"):
        cleanup.clear(
            runtime,
            "parameters",
            NotebookPracticeClearCommand(preview=preview, files="discard"),
        )
    post.assert_not_called()
    assert registry.read_bytes() == before
    assert link.read_text() == "shared interpreter"
    assert (course.directory / "notes.txt").read_text() == "my edits"
    assert current(runtime) == course
