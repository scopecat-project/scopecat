"""Maintenance rehearsal uses a current snapshot, without a schema upgrade."""

import subprocess
import sys
from pathlib import Path

from lab_tools.verify_maintenance import verify_restore
from scopecat.project import load_project
from scopecat_server.snapshots import verify_snapshot
from scopecat_server.storage.sqlite.connection import SQLiteDatabase
from scopecat_server.storage.sqlite.project_store import SQLiteProjectStore


def test_current_restore_prepares_only_the_recovered_environment(tmp_path, monkeypatch):
    root = tmp_path / "project"
    root.mkdir()
    (root / "scopecat.toml").write_text("[lab]\n")
    (root / "science.py").write_text("# retained source\n")
    project = load_project(root / "scopecat.toml")
    data = project.runtime_binding.data_root
    store = SQLiteProjectStore(
        SQLiteDatabase(data / "control.sqlite3"), data / "objects"
    )
    store.bootstrap()
    store.close()
    restored = root.with_name("project-maintenance") / "restored"
    prepared = []

    def prepare(path, *, bundle):
        assert path == restored
        assert (path / "science.py").read_text() == "# retained source\n"
        prepared.append(path)
        return Path(sys.executable)

    run = subprocess.run

    def execute(arguments, **kwargs):
        if "lab_tools.verify_maintenance" in arguments:
            assert arguments[-1] == str(restored)
            return subprocess.CompletedProcess(arguments, 0)
        return run(arguments, **kwargs)

    monkeypatch.setattr("lab_tools.environment.prepare_project", prepare)
    monkeypatch.setattr(subprocess, "run", execute)
    verify_restore(root)
    assert prepared == [restored]
    verify_snapshot(restored.parent / "original")
    assert (root / "science.py").read_text() == "# retained source\n"
