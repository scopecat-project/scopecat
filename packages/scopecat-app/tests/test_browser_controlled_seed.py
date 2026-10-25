"""Exercise the browser's real transport gates without a browser build."""

import ast
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Protocol

import pytest

from scopecat.daemon.client import DaemonClient
from scopecat.kernel.ids import validate_run_id
from scopecat_server.lifecycle import initialize_project, start_project, stop_project

ROOT = Path(__file__).resolve().parents[3]
SEED = ROOT / "apps/scopecat-ui/e2e/fixtures/controlled_experiment.py"


class _SeedProcess(Protocol):
    def poll(self) -> int | None: ...

    def communicate(self) -> tuple[str, str]: ...


def _wait_for_marker(marker: Path, child: _SeedProcess) -> str:
    deadline = time.monotonic() + 45
    while True:
        assert child.poll() is None, child.communicate()
        assert time.monotonic() < deadline, marker.name
        if marker.exists():
            value = marker.read_text(encoding="utf-8")
            try:
                validate_run_id(value)
            except ValueError:
                pass  # Creation or a partial write is not a ready run identity.
            else:
                return value
        time.sleep(0.02)


def test_marker_waits_for_content_after_file_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    marker = tmp_path / "ready"
    marker.touch()
    expected = "01a17570-c701-7162-9b6e-7a4445407274"
    original_read = Path.read_text
    reads: list[str] = []

    def read_then_finish_write(path: Path, *, encoding: str) -> str:
        value = original_read(path, encoding=encoding)
        reads.append(value)
        if len(reads) <= 2:
            # Expose both create-before-write and partial-write intervals.
            # Return the value already read, not the next writer's content.
            path.write_text(
                expected[:8] if len(reads) == 1 else expected, encoding=encoding
            )
        assert len(reads) <= 3
        return value

    monkeypatch.setattr(Path, "read_text", read_then_finish_write)

    class RunningSeed:
        checks = 0

        def poll(self) -> None:
            self.checks += 1

        def communicate(self) -> tuple[str, str]:
            raise AssertionError("The seed is still running")

    child = RunningSeed()
    assert _wait_for_marker(marker, child) == expected
    assert reads == ["", expected[:8], expected]
    assert child.checks == 3


def test_browser_seed_gates_admission_execution_and_first_measurement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SCOPECAT_DAEMON_URL", raising=False)
    project = initialize_project(tmp_path / "project")
    endpoint = start_project(project)
    try:
        # Use the browser fixture's own starter setup, including its saved names.
        subprocess.run(  # noqa: S603 - repository-generated starter notebook
            [sys.executable, str(project.root / "notebooks/01_first_run.py")],
            check=True,
            capture_output=True,
            text=True,
            timeout=120,
        )
        script = project.root / "e2e_live_run.py"
        shutil.copyfile(SEED, script)
        with subprocess.Popen(  # noqa: S603 - exact repository seed in isolated project
            [sys.executable, str(script)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        ) as child:
            try:
                with (
                    project.connect() as lab,
                    DaemonClient(endpoint.base_url) as client,
                ):
                    run_id = None
                    for stage, status in (
                        ("accepted", "queued"),
                        ("running", "leased"),
                        ("measurement", "leased"),
                    ):
                        marker = project.root / f".scopecat/e2e-{stage}-ready"
                        observed = _wait_for_marker(marker, child)
                        if run_id is None:
                            run_id = observed
                        assert observed == run_id
                        assert client.get_run(run_id).control.state == status
                        if stage == "measurement":
                            preview = client.measurement_preview(run_id)
                            assert preview.dataset_schema is not None
                            live = client.measurement_live_preview(
                                run_id, dataset_schema=preview.dataset_schema
                            )
                            assert live.active
                            assert live.received_record_count == 1
                            assert live.durable_record_count == 0
                            assert live.latest is not None
                        (project.root / f".scopecat/e2e-release-{stage}").touch()
                    stdout, stderr = child.communicate(timeout=45)
                    assert child.returncode == 0, stdout + stderr
                    assert ast.literal_eval(stdout.strip()) == {
                        "run_id": run_id,
                        "status": "completed",
                    }
                    assert run_id is not None
                    retained = lab.get_run(run_id)
                    assert retained.status == "completed"
                    assert len(retained.measurements()) == 15
            finally:
                if child.poll() is None:
                    child.terminate()
                    child.communicate(timeout=10)
    finally:
        stop_project(project)
