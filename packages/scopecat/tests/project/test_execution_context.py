"""Captured source location and registered runtime binding are separate identities."""

from dataclasses import replace
from pathlib import Path

from scopecat_testkit.project_loading import isolated_project_imports

from scopecat.project import SourceExecutionContext, load_project
from scopecat.runtime_binding import RuntimeBinding


def test_captured_factory_uses_explicit_workspace_without_rebinding_source(
    tmp_path: Path,
) -> None:
    captured, workspace = tmp_path / "captured", tmp_path / "author"
    captured.mkdir()
    (captured / "scopecat.toml").write_text(
        '[lab]\napplication="captured_app:create"\n'
    )
    (captured / "captured_app.py").write_text(
        "from scopecat.application.lab import LabApplication\n"
        "from scopecat.project_sources import loading_workspace\n"
        "def create(root):\n"
        '    assert root.name == "author"\n'
        '    assert loading_workspace.get() == "registered-author"\n'
        "    return LabApplication()\n"
    )
    binding = RuntimeBinding(workspace, tmp_path / "data", tmp_path / "state")
    source = load_project(captured / "scopecat.toml")
    bound = replace(
        source, execution=SourceExecutionContext(binding, "registered-author", None)
    )
    with isolated_project_imports():
        bound.load_application()
    assert bound.root == captured
    assert bound.workspace_root == workspace
    assert bound.runtime_binding == binding
    assert not workspace.exists()
    assert not binding.data_root.exists()
