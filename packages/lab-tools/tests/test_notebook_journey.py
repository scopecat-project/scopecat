"""Preparation retries preserve user-owned files and never acquire data."""

from pathlib import Path
from unittest.mock import Mock

import pytest

from lab_teaching.lessons import TOPICS
from lab_tools import notebook_journey as journey


@pytest.mark.parametrize("topic", TOPICS)
def test_retry_and_continue_preserve_files_and_environment(
    tmp_path, monkeypatch, topic
):
    runtime = Mock(home=tmp_path)
    client = Mock()
    execution = Mock(side_effect=ValueError("dependency unavailable"))
    monkeypatch.setattr(journey, "create_client_environment", client)
    monkeypatch.setattr(journey, "prepare_execution_environment", execution)
    with pytest.raises(ValueError, match="dependency unavailable"):
        journey.prepare(runtime, topic=topic)
    pending = journey.current(runtime, topic)
    assert pending is not None and not pending.ready
    source = pending.directory / "src/my_experiment/parameters.py"
    source.write_text(source.read_text() + "\n# retained learner edit\n")
    original = pending.notebook.read_bytes()
    python = journey.environment_python(pending.directory / ".venv")
    python.parent.mkdir(parents=True)
    python.touch()
    execution.side_effect = None
    execution.return_value = python
    ready = journey.prepare(runtime, topic=topic)
    assert ready.ready and ready.directory == pending.directory
    execution.reset_mock()
    client.reset_mock()
    runtime.register_source.reset_mock()
    assert journey.prepare(runtime, topic=topic) == ready
    assert "# retained learner edit" in source.read_text()
    assert pending.notebook.read_bytes() == original
    execution.assert_not_called()
    client.assert_not_called()
    runtime.register_source.assert_not_called()
    pending.notebook.unlink()
    with pytest.raises(ValueError, match="不会重建或覆盖"):
        journey.prepare(runtime, topic=topic)
    assert not pending.notebook.exists()


def test_fresh_sources_have_independent_identity_and_no_manager_tasks(tmp_path):
    first, second = (tmp_path / name for name in ("first", "second"))
    for source in (first, second):
        journey.create_lesson_source(source)
        assert not (source / ".vscode/tasks.json").exists()
        assert "lab_teaching" not in (source / "scopecat.toml").read_text()
        assert (
            "teaching-bench" not in (source / "src/my_experiment/setup.py").read_text()
        )
    identity = Path("src/my_experiment/lesson_identity.py")
    assert (first / identity).read_bytes() != (second / identity).read_bytes()
    with pytest.raises(ValueError, match="未覆盖"):
        journey.create_lesson_source(first)


@pytest.mark.parametrize("topic", ["parameters", "groups"])
def test_windows_editor_opens_folder_and_notebook_without_command_shell(
    tmp_path, monkeypatch, topic
):
    installation = tmp_path / "VS Code"
    (installation / "bin").mkdir(parents=True)
    executable = installation / "Code.exe"
    executable.touch()
    monkeypatch.setattr(journey.sys, "platform", "win32")
    monkeypatch.setattr(
        journey.shutil, "which", lambda _: str(installation / "bin/code.cmd")
    )
    launched = Mock()
    monkeypatch.setattr(journey.subprocess, "Popen", launched)
    saved = journey.NotebookJourney(
        topic=topic, directory=tmp_path / "work & notes", ready=True
    )
    journey.open_editor(saved)
    assert launched.call_args.args[0] == [
        str(executable),
        "--new-window",
        str(saved.directory),
        str(saved.notebook),
    ]
    assert "shell" not in launched.call_args.kwargs


def test_old_parameters_receipt_is_continued_without_replacement(tmp_path, monkeypatch):
    import json

    runtime = Mock(home=tmp_path)
    source = tmp_path / "existing"
    journey.create_lesson_source(source)
    python = journey.environment_python(source / ".venv")
    python.parent.mkdir(parents=True)
    python.touch()
    receipt = tmp_path / "learning/parameters.json"
    receipt.parent.mkdir()
    original = json.dumps({"directory": str(source), "ready": True})
    receipt.write_text(original)
    client = Mock()
    monkeypatch.setattr(journey, "create_client_environment", client)
    assert journey.prepare(runtime).directory == source
    assert receipt.read_text() == original
    assert journey.current(runtime, "groups") is None
    client.assert_not_called()


def test_topics_keep_separate_receipts_and_reject_unknown_topics(tmp_path, monkeypatch):
    runtime = Mock(home=tmp_path)
    monkeypatch.setattr(journey, "create_client_environment", Mock())
    monkeypatch.setattr(journey, "prepare_execution_environment", Mock())
    saved = {topic: journey.prepare(runtime, topic=topic) for topic in TOPICS}
    assert len({item.directory for item in saved.values()}) == len(TOPICS)
    identities = set()
    for topic, item in saved.items():
        assert journey.current(Mock(home=tmp_path), topic) == item
        assert item.notebook.name == f"{topic}.ipynb"
        identities.add(
            (item.directory / "src/my_experiment/lesson_identity.py").read_text()
        )
    assert len(identities) == len(TOPICS)
    parameters = saved["parameters"]
    with pytest.raises(ValueError, match="尚不支持"):
        journey.prepare(runtime, topic="../outside")
    receipt = tmp_path / "learning/groups.json"
    receipt.write_text(parameters.model_dump_json())
    with pytest.raises(ValueError, match="不符"):
        journey.current(runtime, "groups")
