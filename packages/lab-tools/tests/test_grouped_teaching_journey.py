"""Execute the installed grouping lesson against a retained, restarted daemon."""

from pathlib import Path

import pytest

import scopecat as sc
from lab_teaching.project import create_project
from lab_tools.verify_groups import GROUP_CELLS, GROUP_REOPEN_CELLS
from scopecat_server.lifecycle import start_project, stop_project


def test_grouped_teaching_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, notebook_imports
):
    root = create_project(tmp_path / "分组 教材").parent
    monkeypatch.syspath_prepend(str(root / "src"))
    monkeypatch.chdir(root)
    project = sc.open_project(root)
    for cells in (GROUP_CELLS, GROUP_REOPEN_CELLS):
        start_project(project, timeout=120)
        try:
            namespace: dict[str, object] = {}
            for cell in cells:
                exec(cell, namespace)  # noqa: S102 - maintained notebook cells
        finally:
            stop_project(project)
