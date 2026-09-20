"""The public diagnostic command needs neither a lab nor a reference project."""

import json
import sys
import zipfile
from pathlib import Path

from typer.testing import CliRunner

from scopecat_server.cli import app


def test_public_command_retains_original_failure_without_a_project(
    tmp_path: Path,
) -> None:
    output = tmp_path / "evidence"
    result = CliRunner().invoke(
        app,
        [
            "diagnose",
            "--output",
            str(output),
            "--cwd",
            str(tmp_path),
            "--",
            sys.executable,
            "-c",
            "print('original diagnostic'); raise SystemExit(7)",
        ],
    )
    assert result.exit_code == 1, result.output
    summary = json.loads((output / "summary.json").read_text())
    assert summary["passed"] is False
    assert summary["cases"][0]["returncode"] == 7
    assert "original diagnostic" in (output / "command/worker.log").read_text()
    with zipfile.ZipFile(tmp_path / "evidence.zip") as archive:
        assert "command/worker.log" in archive.namelist()
    assert not (tmp_path / "scopecat.toml").exists()
    repeated = CliRunner().invoke(
        app, ["diagnose", "--output", str(output), "--", sys.executable, "-c", "pass"]
    )
    assert repeated.exit_code != 0
    assert json.loads((output / "command/outcome.json").read_text())["returncode"] == 7
