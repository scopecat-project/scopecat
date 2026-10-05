"""Real OS locks and atomic application-state replacement."""

import subprocess
import sys

from lab_tools.application_runtime import ApplicationRuntime, _write


def test_application_lock_excludes_process_and_state_replaces(tmp_path):
    runtime = ApplicationRuntime(tmp_path)
    child = (
        "import sys\nfrom filelock import FileLock, Timeout\n"
        "try:\n    with FileLock(sys.argv[1], timeout=0): pass\n"
        "except Timeout:\n    sys.exit(23)\n"
    )
    command = [sys.executable, "-I", "-c", child, str(tmp_path / "application.lock")]
    with runtime.lock:
        assert subprocess.run(command, check=False, timeout=30).returncode == 23  # noqa: S603
        _write(runtime.selection, "original")
        _write(runtime.selection, "replacement")
        assert runtime.selection.read_text() == "replacement"
    assert subprocess.run(command, check=False, timeout=30).returncode == 0  # noqa: S603
    assert not list(tmp_path.glob(".installation.json-*"))
