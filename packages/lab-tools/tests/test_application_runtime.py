"""Application ownership is independent of source folders and live interpreters."""

import sys
from pathlib import Path

import psutil
import pytest
from filelock import FileLock

from lab_tools.application_runtime import ApplicationRuntime
from scopecat.project import load_captured_project, open_project
from scopecat.project_sources import capture_sources, materialize_sources
from scopecat_server.lifecycle import write_daemon_endpoint_record


@pytest.fixture
def application(tmp_path: Path):
    gui = tmp_path / "gui"
    gui.mkdir()
    (gui / "index.html").write_text("<html>application</html>")
    runtime = ApplicationRuntime(tmp_path / "home")
    runtime.configure(static_dir=gui)
    yield runtime
    runtime.stop()


def test_two_sources_share_empty_application_without_owning_it(application, tmp_path):
    sources = []
    for name in ("first", "second"):
        root = tmp_path / name
        (root / "src").mkdir(parents=True)
        (root / "scopecat.toml").write_text(
            '[authors]\nsource_roots=["src"]\nrefresh_roots=["src"]\n'
            'modules=["experiment"]\ndependencies=[]\n'
        )
        (root / "src/experiment.py").write_text(f"name = {name!r}\n")
        identity = application.register_source(root)
        assert application.source(root) == identity
        sources.append((root, identity))
    assert sources[0][1] != sources[1][1]
    record = application.start()
    assert application.start() == record
    assert record.project_root == application.root
    assert all(
        open_project(root).runtime_binding.data_root == record.data_root
        for root, _ in sources
    )
    bundle = capture_sources(open_project(sources[0][0]))
    archived = materialize_sources(bundle, tmp_path / "archive")
    application.stop()
    # Captured sources do not consult a current source registration on reopen.
    assert load_captured_project(archived).author_only
    assert load_captured_project(archived).lab_adapter is None
    assert application.start().data_root == record.data_root


def test_killed_owner_reconciles_without_touching_data(application):
    record = application.start()
    evidence = record.data_root / "retained-evidence"
    evidence.write_bytes(b"retain")
    process = psutil.Process(record.pid)
    process.kill()
    process.wait(timeout=10)
    assert application.status().state == "stale"
    with FileLock(record.data_root / "daemon.lock"):
        with pytest.raises(ValueError, match="仍在运行或更新中"):
            application.select(application.installation())
        assert application.status().state == "stale"
    application.select(application.installation())
    assert application.status().state == "stopped"
    assert evidence.read_bytes() == b"retain"
    assert application.start().data_root == record.data_root


def test_conflicting_interpreter_has_explicit_stop_recovery(application):
    record = application.start()
    assert record.python == Path(sys.executable).absolute()
    write_daemon_endpoint_record(record.model_copy(update={"python": None}))
    with pytest.raises(ValueError, match="停止服务"):
        application.start()
    application.stop()
    assert application.start().python == Path(sys.executable).absolute()


def test_failed_candidate_does_not_replace_selected_runtime(application):
    before = application.installation()
    application.start()
    with pytest.raises(ValueError, match="仍在运行或更新中"):
        application.select(before)
    assert application.installation() == before
    application.stop()
    changed = before.model_copy(update={"adapter_identity": "changed"})
    with pytest.raises(ValueError, match="候选环境在准备后改变"):
        application.select(changed)
    assert application.installation() == before
    assert not application.pending.exists()
