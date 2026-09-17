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
    assert "def mean_iq" in (root / "src/my_experiment/teaching.py").read_text()


def test_public_teach_forwards_sandbox_options(monkeypatch: pytest.MonkeyPatch) -> None:
    received: list[str] = []
    monkeypatch.setattr(sandbox, "main", received.extend)
    arguments = ["compute", "--reset", "--home", "a folder"]
    result = CliRunner().invoke(app, ["teach", *arguments])
    assert result.exit_code == 0, result.output
    assert received == arguments
