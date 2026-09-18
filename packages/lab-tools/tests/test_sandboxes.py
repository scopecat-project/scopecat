"""沙盒失败恢复、目录边界与独立教材起点。"""

import json

import pytest

from lab_teaching.lessons import TOPICS
from lab_tools import environment, project, sandbox


@pytest.fixture
def managed(tmp_path, monkeypatch):
    monkeypatch.setattr(project, "environment_identity", dict)
    monkeypatch.setattr(sandbox, "sandbox_key", lambda _source: "release-test")
    monkeypatch.setattr(sandbox, "run", lambda _command: None)

    def prepare(root):
        python = root / ".venv/bin/python"
        python.parent.mkdir(parents=True)
        python.touch()
        return python

    monkeypatch.setattr(environment, "prepare_project", prepare)
    monkeypatch.setattr(
        sandbox, "project_python", lambda root: root / ".venv/bin/python"
    )
    return tmp_path / "home"


def test_reset_keeps_previous_edits_and_failed_install_does_not_publish(
    managed, monkeypatch
):
    first = sandbox.select_project(managed, "compute")
    evidence = first / "notes.txt"
    evidence.write_text("保留选择权", encoding="utf-8")
    assert sandbox.select_project(managed, "compute") == first

    def fail(root):
        raise OSError("安装中断")

    with monkeypatch.context() as patch:
        patch.setattr(environment, "prepare_project", fail)
        with pytest.raises(OSError, match="安装中断"):
            sandbox.select_project(managed, "compute", reset=True)
    assert sandbox.select_project(managed, "compute") == first
    fresh = sandbox.select_project(managed, "compute", reset=True)
    assert fresh != first
    assert not (fresh / "notes.txt").exists()
    assert evidence.read_text(encoding="utf-8") == "保留选择权"


def test_pointer_cannot_escape_sandbox(managed):
    first = sandbox.select_project(managed, "refresh")
    (first.parent / "current.json").write_text(
        json.dumps({"generation": "../../real-lab"})
    )
    with pytest.raises(ValueError, match="沙盒记录损坏"):
        sandbox.select_project(managed, "refresh")


def test_stop_does_not_create_project(managed):
    with pytest.raises(ValueError, match="没有需要停止"):
        sandbox.select_project(managed, "groups", existing_only=True)
    assert not list(managed.rglob("scopecat.toml"))


@pytest.mark.parametrize("topic", TOPICS)
def test_each_lesson_is_self_contained_and_rejects_wrong_kernel(
    managed, monkeypatch, topic
):
    from IPython.core.interactiveshell import InteractiveShell

    shell = InteractiveShell()
    monkeypatch.setattr("IPython.get_ipython", lambda: shell)
    root = sandbox.select_project(managed, topic)
    notebook = json.loads(
        (root / f"notebooks/{topic}.ipynb").read_text(encoding="utf-8")
    )
    first = next(c for c in notebook["cells"] if c["cell_type"] == "code")
    monkeypatch.chdir(root / "notebooks")
    result = shell.run_cell("".join(first["source"]))
    assert isinstance(result.error_in_exec, RuntimeError)
    assert "Select Kernel" in str(result.error_in_exec)
    if topic == "compute":
        assert "def mean_iq(" in (root / "src/my_experiment/teaching.py").read_text(
            encoding="utf-8"
        )
    if topic == "refresh":
        assert (root / "examples/extra.py").is_file()
        assert not (root / "src/my_experiment/extra.py").exists()


def test_unknown_topic_never_creates_target(tmp_path):
    with pytest.raises(ValueError, match="未知专题"):
        project.create_project(tmp_path / "missing", topic="not-a-topic")
    assert not (tmp_path / "missing").exists()


def test_cleanup_preserves_current_live_and_unmanaged_paths(managed, monkeypatch):
    from lab_tools import cleanup

    first = sandbox.select_project(managed, "compute")
    second = sandbox.select_project(managed, "compute", reset=True)
    assert first in {
        item.root for item in cleanup.old_sandboxes(managed, "release-test")
    }
    with pytest.raises(ValueError, match="当前练习"):
        cleanup.remove_old_sandbox(managed, "release-test", second)
    monkeypatch.setattr(cleanup, "in_use", lambda _root: True)
    with pytest.raises(ValueError, match="内核"):
        cleanup.remove_old_sandbox(managed, "release-test", first)
    assert first.exists()
    monkeypatch.setattr(cleanup, "in_use", lambda _root: False)
    cleanup.remove_old_sandbox(managed, "release-test", first)
    assert not first.exists()
    assert second.exists()
    with pytest.raises(ValueError, match="旧教学副本"):
        cleanup.remove_old_sandbox(managed, "release-test", managed)


def test_cleanup_detects_a_real_process(managed):
    import subprocess
    import sys

    from lab_tools import cleanup

    root = sandbox.select_project(managed, "compute")
    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"], cwd=root
    )
    try:
        assert cleanup.in_use(root)
    finally:
        child.terminate()
        child.wait(timeout=10)
    assert not cleanup.in_use(root)
