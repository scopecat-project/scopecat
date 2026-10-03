"""The maintained benchmark uses independent scientific owners."""

from __future__ import annotations

from pathlib import Path
from typing import cast

from benchmarks.e2e.author_prepare import measure
from scopecat_testkit.project_loading import isolated_project_imports


def test_author_prepare_with_explicit_context(tmp_path: Path) -> None:
    with isolated_project_imports():
        result = measure(tmp_path, repetitions=0)
    samples = cast("list[dict[str, object]]", result["samples"])
    operations = {sample["operation"]: sample for sample in samples}
    assert operations["first"]["points"] == 1
    assert operations["edit_scan"]["points"] == 3
    assert "failed_refresh" in operations
    assert "after_failed_refresh" in operations
