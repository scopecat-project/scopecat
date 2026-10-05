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
