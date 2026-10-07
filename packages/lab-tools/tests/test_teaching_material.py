"""Independent authoring examples remain fixtures, without a sandbox manager."""

import json
from pathlib import Path

import pytest

from lab_teaching.lessons import TOPICS
from lab_tools import project
from scopecat.daemon.endpoint import DaemonEndpointError


@pytest.mark.parametrize("topic", TOPICS)
def test_lesson_source_is_self_contained_and_never_starts_an_implicit_service(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, topic: str
) -> None:
    from IPython.core.interactiveshell import InteractiveShell

    monkeypatch.setattr(project, "environment_identity", dict)
    shell = InteractiveShell()
    monkeypatch.setattr("IPython.get_ipython", lambda: shell)
    root = tmp_path / topic
    project.create_project(root, topic=topic)
    notebook = json.loads((root / f"notebooks/{topic}.ipynb").read_text())
    first = next(cell for cell in notebook["cells"] if cell["cell_type"] == "code")
    monkeypatch.chdir(root / "notebooks")
    result = shell.run_cell("".join(first["source"]))
    assert isinstance(result.error_in_exec, DaemonEndpointError)
    assert "no daemon endpoint" in str(result.error_in_exec)
    assert not (root / ".scopecat/daemon.json").exists()
    if topic == "compute":
        assert "def mean_iq(" in (root / "src/my_experiment/teaching.py").read_text()
    if topic == "refresh":
        assert (root / "examples/extra.py").is_file()
        assert not (root / "src/my_experiment/extra.py").exists()


def test_unknown_topic_never_creates_target(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="未知专题"):
        project.create_project(tmp_path / "missing", topic="not-a-topic")
    assert not (tmp_path / "missing").exists()


@pytest.mark.parametrize("topic", [None, *TOPICS])
def test_generated_material_matches_reviewed_source_and_cannot_overlay_edits(
    tmp_path: Path, topic: str | None
) -> None:
    from lab_teaching.lessons import install_lesson
    from lab_teaching.project import create_project

    root = tmp_path / "author"
    create_project(root, topic=topic)
    material = (
        Path(__file__).resolve().parents[2]
        / "lab-teaching/src/lab_teaching/course_material"
    )
    source = root / "src/my_experiment"
    for name in ("parameters", "response"):
        assert (source / f"{name}.py").read_bytes() == (
            material / f"lessons/{name}.py.txt"
        ).read_bytes(), "Reinstall scopecat-lab-teaching: installed material is stale"
    if topic is None:
        for name, template in (
            ("teaching", "experiment"),
            ("setup", "setup"),
            ("analysis", "analysis"),
            ("session", "session"),
        ):
            assert (source / f"{name}.py").read_bytes() == (
                material / f"lessons/{template}.py.txt"
            ).read_bytes()
        for name in ("start", "reopen"):
            assert (root / f"notebooks/{name}.ipynb").read_bytes() == (
                material / f"{name}.ipynb"
            ).read_bytes()
    assert all("lab_teaching" not in file.read_text() for file in source.glob("*.py"))
    (source / "parameters.py").write_text("# my edited declaration\n")
    (root / "notebooks/notes.md").write_text("My notes\n")
    before = {
        str(file.relative_to(root)): file.read_bytes()
        for file in root.rglob("*")
        if file.is_file()
    }
    with pytest.raises(FileExistsError, match="未覆盖"):
        install_lesson(root, topic)
    assert before == {
        str(file.relative_to(root)): file.read_bytes()
        for file in root.rglob("*")
        if file.is_file()
    }
    with pytest.raises(FileExistsError):
        create_project(root, topic=topic)


@pytest.mark.parametrize("topic", ["refresh", "compute"])
def test_editing_verifier_follows_cell_ids_after_insertions(tmp_path: Path, topic: str):
    from nbformat import read, v4, write

    from lab_teaching.project import create_project
    from lab_tools.verify_editing import REFRESH_EDIT, editing_notebook, reopen_cells

    root = create_project(tmp_path / topic, topic=topic).parent
    path = root / f"notebooks/{topic}.ipynb"
    original = read(path, as_version=4)
    original.cells.insert(0, v4.new_markdown_cell("An added introduction"))
    original.cells.insert(7, v4.new_code_cell("learner_note = 'keep me'"))
    write(original, path)
    document = editing_notebook(root, topic)
    originals = {cell.id: cell.source for cell in original.cells}
    actual = {cell.id: cell.source for cell in document.cells}
    if topic == "compute":
        originals["compute-select"] = "number = session.run_number(mean)"
        reopened = reopen_cells(root)
        assert reopened[0] == originals["compute-1"]
        assert reopened[1] == originals["compute-history"]
        assert reopened[3] == reopened[5] == originals["compute-7"]
    else:
        target = next(i for i, c in enumerate(document.cells) if c.id == "refresh-5")
        assert document.cells[target - 1].source == REFRESH_EDIT
    assert {cell_id: actual[cell_id] for cell_id in originals} == originals
    assert [c.id for c in document.cells if c.id in originals] == list(originals)


@pytest.mark.parametrize(
    ("topic", "cell_id", "reopen"),
    [
        ("refresh", "refresh-5", False),
        ("compute", "compute-select", False),
        ("compute", "compute-1", True),
        ("compute", "compute-history", True),
        ("compute", "compute-7", True),
    ],
)
def test_editing_verifier_rejects_missing_cell_id(
    tmp_path: Path, topic: str, cell_id: str, reopen: bool
):
    from nbformat import read, write

    from lab_teaching.project import create_project
    from lab_tools.verify_editing import editing_notebook, reopen_cells

    root = create_project(tmp_path / topic, topic=topic).parent
    path = root / f"notebooks/{topic}.ipynb"
    document = read(path, as_version=4)
    document.cells = [cell for cell in document.cells if cell.id != cell_id]
    write(document, path)
    with pytest.raises(ValueError, match=cell_id):
        if reopen:
            reopen_cells(root)
        else:
            editing_notebook(root, topic)


@pytest.mark.parametrize(
    ("topic", "cell_id"),
    [
        ("calibration", "calibration-2"),
        ("calibration", "calibration-5"),
        ("joint-calibration", "joint-5"),
    ],
)
def test_calibration_anchors_follow_ids_and_reject_missing_or_duplicate(
    tmp_path: Path, topic: str, cell_id: str
) -> None:
    from lab_teaching.project import create_project
    from lab_tools.verify_editing import _cell_index

    root = create_project(tmp_path / topic, topic=topic).parent
    cells = json.loads((root / f"notebooks/{topic}.ipynb").read_text())["cells"]
    target = cells[_cell_index(cells, cell_id)]
    cells.insert(0, {"id": "added-introduction", "source": ["A new introduction"]})
    assert cells[_cell_index(cells, cell_id)] is target
    cells.remove(target)
    with pytest.raises(ValueError, match=f"{cell_id!r}; found 0"):
        _cell_index(cells, cell_id)
    cells.extend([target, dict(target)])
    with pytest.raises(ValueError, match=f"{cell_id!r}; found 2"):
        _cell_index(cells, cell_id)
