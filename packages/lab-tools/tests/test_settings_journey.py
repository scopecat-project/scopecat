"""Local settings admission never prevents stopping or rewrites science data."""

import json
import sys
from pathlib import Path

import pytest

from lab_tools.application_runtime import ApplicationRuntime
from scopecat.lab_settings import lab_settings_identity
from scopecat.project import open_project
from scopecat_server.lifecycle import inspect_daemon, stop_project


def test_settings_edit_requires_stopped_recheck_and_missing_file_allows_stop(
    tmp_path: Path,
) -> None:
    store = ApplicationRuntime(tmp_path / "home")
    root = store.root
    root.mkdir(parents=True)
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
    service = store.configure(static_dir=gui)
    assert service.settings_identity == lab_settings_identity(root)
    project = open_project(root)
    original_binding = (root / "scopecat.runtime.toml").read_bytes()
    try:
        store.start()
        assert (root / "scopecat.runtime.toml").read_bytes() == original_binding
        settings.write_text('{"initial_configuration":"physical"}')
        candidate = store.qualify(Path(sys.executable), gui)
        with pytest.raises(ValueError, match="仍在运行或更新中"):
            store.select(candidate)
        store.stop()
        with pytest.raises(ValueError, match="设置已改变"):
            store.start()
        assert inspect_daemon(project).state == "stopped"
        store.select(candidate)
        updated = store.installation()
        assert updated.settings_identity != service.settings_identity
        store.start()
        settings.unlink()
        store.stop()
        assert inspect_daemon(project).state == "stopped"
        with pytest.raises(ValueError, match="cannot read lab settings"):
            store.start()
    finally:
        stop_project(project)
