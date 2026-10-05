"""The public CLI delegates tutorial generation and menu arguments to one owner."""

from pathlib import Path

import pytest
from rich.text import Text
from typer.testing import CliRunner

from lab_tools import practice, project
from lab_tools.public_cli import app


def test_public_init_can_create_a_complete_topic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(project, "environment_identity", dict)
    root = tmp_path / "compute"
    result = CliRunner().invoke(app, ["init", str(root), "--topic", "compute"])
    assert result.exit_code == 0, result.output
    assert (root / "notebooks/compute.ipynb").is_file()
    assert "def mean_iq" in (root / "src/my_experiment/teaching.py").read_text(
        encoding="utf-8"
    )


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
