"""Preparation retries preserve user-owned files and never acquire data."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from unittest.mock import Mock

import pytest

from lab_teaching.lessons import TOPICS
from lab_tools import notebook_journey as journey


def test_other_window_observes_preparation_failure_and_retry(tmp_path, monkeypatch):
    runtime = Mock(home=tmp_path)
    source_started, allow_source, environment_started, allow_environment = (
        Event() for _ in range(4)
    )
    create = journey.create_lesson_source

    def create_slowly(*args):
        source_started.set()
        assert allow_source.wait(10)
        create(*args)

    def fail(*args):
        environment_started.set()
        assert allow_environment.wait(10)
        raise ValueError("dependency unavailable")

    monkeypatch.setattr(journey, "create_lesson_source", create_slowly)
    monkeypatch.setattr(journey, "create_client_environment", Mock())
    monkeypatch.setattr(journey, "prepare_execution_environment", fail)
    assert journey.status(runtime) == {"state": "not_started", "journey": None}
    with ThreadPoolExecutor(max_workers=1) as pool:
        preparation = pool.submit(journey.prepare, runtime)
        try:
            assert source_started.wait(10)
            assert journey.status(runtime) == {"state": "preparing", "journey": None}
            allow_source.set()
            assert environment_started.wait(10)
            observed = journey.status(runtime)
            assert observed["state"] == "preparing"
            pending = journey.current(runtime)
            assert pending is not None
            assert observed["journey"] == pending.view()
        finally:
            allow_source.set()
            allow_environment.set()
        with pytest.raises(ValueError, match="dependency unavailable"):
            preparation.result(timeout=10)
    assert journey.status(runtime)["state"] == "retryable"
    monkeypatch.setattr(journey, "prepare_execution_environment", Mock())
    ready = journey.prepare(runtime)
    assert ready.directory == pending.directory
    assert journey.status(runtime) == {"state": "ready", "journey": ready.view()}


def test_explicit_repair_retries_client_failure_without_replacing_edited_course(
    tmp_path, monkeypatch
):
    runtime = Mock(home=tmp_path)
    client = Mock()
    execution = Mock(return_value=Path("first-execution"))
    monkeypatch.setattr(journey, "create_client_environment", client)
    monkeypatch.setattr(journey, "prepare_execution_environment", execution)
    original = journey.prepare(runtime)
    source = original.directory / "src/my_experiment/teaching.py"
    source.write_text(source.read_text() + "\n# retained learner edits\n")
    original.notebook.write_bytes(original.notebook.read_bytes() + b"\n")
    edits = {path: path.read_bytes() for path in (source, original.notebook)}
    client.side_effect = ValueError("client repair interrupted")
    with pytest.raises(ValueError, match="client repair interrupted"):
        journey.prepare(runtime, repair=True)
    client.assert_called_with(runtime, original.directory, rebuild=True)
    assert journey.status(runtime)["state"] == "retryable"
    assert journey.current(runtime).rebuild_client
    client.side_effect = None
    execution.return_value = Path("new-execution")
    repaired = journey.prepare(runtime)
    client.assert_called_with(runtime, original.directory, rebuild=True)
    assert repaired.directory == original.directory and repaired.ready
    assert not repaired.rebuild_client
    runtime.register_source.assert_called_with(
        original.directory, python=Path("new-execution")
    )
    assert {path: path.read_bytes() for path in edits} == edits


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
    readme = (pending.directory / "README.md").read_text(encoding="utf-8")
    assert "从 Scopecat Help 开始或继续" in readme
    assert "运行与历史保存在同一应用中" in readme
    assert not (pending.directory / ".vscode/tasks.json").exists()
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


def test_dead_preparation_process_becomes_retryable(tmp_path):
    import subprocess
    import sys

    runtime = Mock(home=tmp_path)
    receipt = tmp_path / "learning/parameters.json"
    receipt.parent.mkdir()
    pending = journey.NotebookJourney(directory=tmp_path / "course")
    receipt.write_text(pending.model_dump_json())
    process = subprocess.Popen(  # noqa: S603 - test-owned lock holder, no application
        [
            sys.executable,
            "-c",
            "\n".join(
                [
                    "import os, sys",
                    "from filelock import FileLock",
                    "with FileLock(sys.argv[1]):",
                    "    print('locked', flush=True)",
                    "    sys.stdin.read(1)",
                    "    os._exit(0)",
                ]
            ),
            str(receipt.with_suffix(".lock")),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert process.stdout is not None and process.stdin is not None
        assert process.stdout.readline().strip() == "locked"
        assert journey.status(runtime)["state"] == "preparing"
        process.stdin.write("x")
        process.stdin.flush()
        process.wait(timeout=10)
        assert journey.status(runtime)["state"] == "retryable"
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=10)
