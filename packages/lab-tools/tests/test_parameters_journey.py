"""Preparation retries preserve user-owned files and never acquire data."""

from pathlib import Path
from unittest.mock import Mock

import pytest

from lab_tools import parameters_journey as journey


def test_retry_and_continue_preserve_files_and_environment(tmp_path, monkeypatch):
    runtime = Mock(home=tmp_path)
    client = Mock()
    execution = Mock(side_effect=ValueError("dependency unavailable"))
    monkeypatch.setattr(journey, "create_client_environment", client)
    monkeypatch.setattr(journey, "prepare_execution_environment", execution)
    with pytest.raises(ValueError, match="dependency unavailable"):
        journey.prepare(runtime)
    pending = journey.current(runtime)
    assert pending is not None and not pending.ready
    source = pending.directory / "src/my_experiment/teaching.py"
    source.write_text(source.read_text().replace("shots: int = 64", "shots: int = 32"))
    original = pending.notebook.read_bytes()
    python = journey.environment_python(pending.directory / ".venv")
    python.parent.mkdir(parents=True)
    python.touch()
    execution.side_effect = None
    execution.return_value = python
    ready = journey.prepare(runtime)
    assert ready.ready and ready.directory == pending.directory
    execution.reset_mock()
    client.reset_mock()
    runtime.register_source.reset_mock()
    assert journey.prepare(runtime) == ready
    assert "shots: int = 32" in source.read_text()
    assert pending.notebook.read_bytes() == original
    execution.assert_not_called()
    client.assert_not_called()
    runtime.register_source.assert_not_called()
    pending.notebook.unlink()
    with pytest.raises(ValueError, match="不会重建或覆盖"):
        journey.prepare(runtime)
    assert not pending.notebook.exists()


def test_fresh_sources_have_independent_identity_and_no_manager_tasks(tmp_path):
    first, second = (tmp_path / name for name in ("first", "second"))
    for source in (first, second):
        journey.create_parameters_source(source)
        assert not (source / ".vscode/tasks.json").exists()
        assert "lab_teaching" not in (source / "scopecat.toml").read_text()
        assert (
            "teaching-bench" not in (source / "src/my_experiment/setup.py").read_text()
        )
    identity = Path("src/my_experiment/lesson_identity.py")
    assert (first / identity).read_bytes() != (second / identity).read_bytes()
    with pytest.raises(ValueError, match="未覆盖"):
        journey.create_parameters_source(first)


def test_windows_editor_opens_folder_and_notebook_without_command_shell(
    tmp_path, monkeypatch
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
    saved = journey.ParametersJourney(directory=tmp_path / "work & notes", ready=True)
    journey.open_editor(saved)
    assert launched.call_args.args[0] == [
        str(executable),
        "--new-window",
        str(saved.directory),
        str(saved.notebook),
    ]
    assert "shell" not in launched.call_args.kwargs
