"""Local settings admission never prevents stopping or rewrites science data."""

import json
import sys
from pathlib import Path

import pytest

from lab_tools.services import Services
from scopecat.lab_settings import lab_settings_identity
from scopecat.project import open_project
from scopecat_server.lifecycle import inspect_daemon, stop_project
from scopecat_server.scaffold import write_project_scaffold


def test_settings_edit_requires_stopped_recheck_and_missing_file_allows_stop(
    tmp_path: Path,
) -> None:
    root = tmp_path / "experiment"
    root.mkdir()
    write_project_scaffold(root)
    settings = tmp_path / "lab.json"
    settings.write_text('{"initial_configuration":"simulator"}')
    state = root / ".scopecat"
    (root / "scopecat.runtime.toml").write_text(
        f"[runtime]\ndata_root = {json.dumps(str(state))}\n"
        f"deployment_root = {json.dumps(str(state))}\n"
        f"settings_file = {json.dumps(str(settings))}\n"
    )
    gui = tmp_path / "gui"
    gui.mkdir()
    (gui / "index.html").write_text("<html>workbench</html>")
    store = Services(tmp_path / "home")
    service = store.register(root, Path(sys.executable), name="lab", static_dir=gui)
    assert service.settings_identity == lab_settings_identity(root)
    project = open_project(root)
    try:
        store.start(service.id)
        settings.write_text('{"initial_configuration":"physical"}')
        with pytest.raises(ValueError, match="已停止"):
            store.recheck(service.id, operation_id="test")
        store.stop(service.id)
        with pytest.raises(ValueError, match="设置已改变"):
            store.start(service.id)
        assert inspect_daemon(project).state == "stopped"
        updated = store.recheck(service.id, operation_id="test")
        assert updated.settings_identity != service.settings_identity
        store.start(service.id)
        settings.unlink()
        store.stop(service.id)
        assert inspect_daemon(project).state == "stopped"
        with pytest.raises(ValueError, match="cannot read lab settings"):
            store.start(service.id)
    finally:
        stop_project(project)
