"""The public CLI delegates tutorial generation and menu arguments to one owner."""

from pathlib import Path

import pytest
from rich.text import Text
from typer.testing import CliRunner

from lab_teaching.lessons import TOPICS
from lab_tools import practice
from lab_tools.public_cli import app


@pytest.mark.parametrize("topic", TOPICS)
def test_public_init_creates_ordinary_author_source_without_overwriting_edits(
    tmp_path: Path,
    topic: str,
) -> None:
    import tomllib
    from importlib.resources import files

    root = tmp_path / "作者代码"
    result = CliRunner().invoke(app, ["init", str(root), "--topic", topic])
    assert result.exit_code == 0, result.output
    notebook = root / f"notebooks/{topic}.ipynb"
    assert (
        notebook.read_bytes()
        == files("lab_teaching.course_material")
        .joinpath(f"lessons/{topic}.ipynb")
        .read_bytes()
    )
    manifest = tomllib.loads((root / "scopecat.toml").read_text())
    assert "bootstrap" not in manifest.get("lab", {})
    assert not (root / ".vscode/tasks.json").exists()
    assert not (root / ".venv").exists()
    assert not (root / ".scopecat").exists()
    assert not (root / "author-environment.json").exists()
    notebook.write_bytes(notebook.read_bytes() + b"\n")
    note = root / "notebooks/notes.md"
    note.write_text("My notes")
    retained = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}
    result = CliRunner().invoke(app, ["init", str(root), "--topic", topic])
    assert result.exit_code == 1
    assert retained == {
        path: path.read_bytes() for path in root.rglob("*") if path.is_file()
    }


def test_unknown_cli_topic_leaves_no_source(tmp_path: Path) -> None:
    root = tmp_path / "unknown"
    result = CliRunner().invoke(app, ["init", str(root), "--topic", "unknown"])
    assert result.exit_code == 1
    assert not root.exists()


def test_public_teach_forwards_application_practice_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    received: list[str] = []
    monkeypatch.setattr(practice, "main", received.extend)
    arguments = ["--list", "--home", "a folder"]
    result = CliRunner().invoke(app, ["teach", *arguments])
    assert result.exit_code == 0, result.output
    assert received == arguments


def test_console_can_write_chinese_to_a_redirected_windows_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import io
    import sys

    from lab_tools.bundle import configure_console

    output = io.BytesIO()
    stream = io.TextIOWrapper(output, encoding="cp1252")
    monkeypatch.setattr(sys, "stdout", stream)
    monkeypatch.setenv("PYTHONUTF8", "0")
    configure_console()
    print("中文路径", end="")
    stream.flush()
    assert output.getvalue() == "中文路径".encode()


def test_public_application_forwards_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    from lab_tools import application

    received: list[str] = []
    monkeypatch.setattr(application, "main", received.extend)
    arguments = ["--home", "a folder", "--action", "status"]
    result = CliRunner().invoke(app, ["app", *arguments])
    assert result.exit_code == 0, result.output
    assert received == arguments


@pytest.mark.parametrize("color", [False, True])
def test_public_cli_composition_does_not_mutate_server_commands(
    color: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scopecat_server.cli import app as server_app

    monkeypatch.setattr("typer.rich_utils.FORCE_TERMINAL", color)
    monkeypatch.setattr("typer.rich_utils.COLOR_SYSTEM", "standard" if color else None)
    runner = CliRunner()
    application_help = runner.invoke(app, ["init", "--help"])
    server_help = runner.invoke(server_app, ["init", "--help"])
    assert application_help.exit_code == server_help.exit_code == 0
    assert "--topic" in Text.from_ansi(application_help.output).plain
    assert "--topic" not in Text.from_ansi(server_help.output).plain
    assert runner.invoke(server_app, ["app"]).exit_code == 2
    for command in ("config", "snapshot", "automation", "start", "stop", "status"):
        result = runner.invoke(app, [command, "--help"])
        assert result.exit_code == 0, result.output


def test_installed_console_uses_application_owner_without_loading_runtime() -> None:
    import subprocess
    import sys

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import sys
from importlib.metadata import distribution
entry, = [ep for ep in distribution("scopecat-lab-tools").entry_points
          if ep.group == "console_scripts" and ep.name == "scopecat"]
assert not any(ep.name == "scopecat-lab"
               for ep in distribution("scopecat-lab-tools").entry_points)
assert not any(ep.name == "scopecat"
               for ep in distribution("scopecat-server").entry_points)
main = entry.load()
try:
    main(["--help"])
except SystemExit as error:
    assert error.code == 0
forbidden = {"lab_tools.desktop", "lab_tools.application_runtime",
             "scopecat_server.runtime", "webview", "fastapi"}
assert not forbidden.intersection(sys.modules)
""",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize(
    ("command", "options"),
    [
        ("app", ("--home", "--action", "--workspace", "--no-browser")),
        ("notebook", ("--home", "workspace", "--no-browser")),
        ("teach", ("--home", "--list", "--clear", "--files", "--request-key")),
    ],
)
@pytest.mark.parametrize("help_option", ["--help", "-h"])
def test_public_wrapper_help_is_complete_and_inert(
    command: str,
    options: tuple[str, ...],
    help_option: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from lab_tools import application_runtime, author_notebook

    def unexpected_runtime(*args: object, **kwargs: object) -> None:
        pytest.fail("Help must exit before creating an application runtime")

    def unexpected_notebook(*args: object, **kwargs: object) -> None:
        pytest.fail("Help must exit before launching a Notebook")

    monkeypatch.setattr(
        application_runtime.ApplicationRuntime, "__init__", unexpected_runtime
    )
    monkeypatch.setattr(author_notebook, "launch_notebook", unexpected_notebook)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    result = CliRunner().invoke(app, [command, help_option], prog_name="scopecat")
    assert result.exit_code == 0, result.output
    output = Text.from_ansi(result.output).plain
    for option in options:
        assert option in output
    assert f"usage: scopecat {command}" in output
    assert list(tmp_path.iterdir()) == []
