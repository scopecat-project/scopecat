"""Exercise the browser's real transport gates without a browser build."""

import ast
import shutil
import subprocess
import sys
import time
from pathlib import Path

from scopecat.daemon.client import DaemonClient
from scopecat_server.lifecycle import initialize_project, start_project, stop_project

ROOT = Path(__file__).resolve().parents[3]
SEED = ROOT / "apps/scopecat-ui/e2e/fixtures/controlled_experiment.py"


def test_browser_seed_gates_admission_execution_and_first_measurement(
    tmp_path: Path,
) -> None:
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
                        deadline = time.monotonic() + 45
                        while not marker.exists():
                            assert child.poll() is None, child.communicate()
                            assert time.monotonic() < deadline, stage
                            time.sleep(0.02)
                        observed = marker.read_text(encoding="utf-8")
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
