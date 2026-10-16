"""The retained default fixture rejects the wrong Notebook interpreter."""

import json
from pathlib import Path

import pytest
from testing.app_support.project import create_project


def test_course_rejects_wrong_kernel_before_author_import(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    root = tmp_path / "course"
    create_project(root)
    monkeypatch.chdir(root / "notebooks")
    for name in ("start", "reopen"):
        notebook = json.loads(
            (root / f"notebooks/{name}.ipynb").read_text(encoding="utf-8")
        )
        first_code = next(
            cell for cell in notebook["cells"] if cell["cell_type"] == "code"
        )
        with pytest.raises(RuntimeError, match="Select Kernel"):
            exec("".join(first_code["source"]), {})  # noqa: S102 - execute the shipped notebook guard
    assert not (root / ".scopecat").exists()
