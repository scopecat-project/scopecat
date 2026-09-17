"""The public CLI delegates tutorial generation and menu arguments to one owner."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from lab_tools import project, sandbox
from scopecat_server.cli import app


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


def test_public_teach_forwards_sandbox_options(monkeypatch: pytest.MonkeyPatch) -> None:
    received: list[str] = []
    monkeypatch.setattr(sandbox, "main", received.extend)
    arguments = ["compute", "--reset", "--home", "a folder"]
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
    print("中文路径")
    stream.flush()
    assert output.getvalue() == "中文路径\n".encode()
