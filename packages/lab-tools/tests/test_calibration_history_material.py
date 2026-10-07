"""Lesson reopen cells read only their own durable, fully paged history."""

import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from lab_teaching.project import create_project


@pytest.mark.parametrize(
    ("topic", "cell_id", "prefix", "api"),
    [
        ("calibration", "calibration-history", "zero-", "procedures"),
        ("joint-calibration", "joint-history", "joint-", "procedures"),
        ("task-calibration", "task-history", "background-", "calibration_tasks"),
    ],
)
def test_history_reads_all_pages_and_excludes_other_lessons(
    tmp_path: Path, monkeypatch, topic, cell_id, prefix, api
):
    root = create_project(tmp_path / topic, topic=topic).parent
    notebook = root / "notebooks" / f"{topic}.ipynb"
    cells = json.loads(notebook.read_text())["cells"]
    source = "".join(next(cell for cell in cells if cell["id"] == cell_id)["source"])
    identity = ModuleType("my_experiment.lesson_identity")
    vars(identity)["IDENTITY"] = "this-directory"
    monkeypatch.setitem(sys.modules, "my_experiment.lesson_identity", identity)

    def record(key):
        return SimpleNamespace(
            request_key=key,
            procedure_run_id=key,
            state="ready",
            mode="running",
            specification=SimpleNamespace(task_id=key or "unrelated"),
        )

    own = record(prefix + "this-directory-first")
    older = record(prefix + "this-directory-older")
    pages = {
        None: SimpleNamespace(
            items=[record(None), record(prefix + "another-directory-first"), own],
            next_cursor=7,
        ),
        7: SimpleNamespace(items=[older], next_cursor=None),
    }
    visited = []

    def read_page(*, cursor):
        visited.append(cursor)
        return pages[cursor]

    # The shipped cell receives only the read API: prepare/submit/start do not exist.
    session = SimpleNamespace(**{api: SimpleNamespace(list=read_page)})
    namespace = {"session": session}
    exec(source, namespace)  # noqa: S102 - exercise the shipped teaching cell
    assert namespace["lesson_history"] == [own, older]
    assert visited == [None, 7]
