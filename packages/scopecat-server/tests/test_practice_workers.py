import subprocess
import sys
from pathlib import Path

from scopecat_server.services.project_workers import (
    ProjectProcedureWorkers,
    capture_worker_process,
)


def test_unregistered_worker_exits_before_loading_any_application(
    tmp_path: Path,
) -> None:
    result = subprocess.run(  # noqa: S603 - fixed interpreter and internal module
        [
            sys.executable,
            "-m",
            "scopecat_server.procedure_worker",
            str(tmp_path / "missing-application"),
            "unregistered",
        ],
        input=b"",
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 1
    assert b"Worker was not registered" in result.stderr
    assert b"Traceback" not in result.stderr


def test_retirement_after_restart_joins_only_the_recorded_process(
    tmp_path: Path,
) -> None:
    (tmp_path / "scopecat.toml").write_text("[lab]\n")
    with (
        subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"]
        ) as owned,
        subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"]
        ) as unrelated,
    ):
        try:
            workers = ProjectProcedureWorkers(lambda: tmp_path, lambda _: "ready")
            directory = workers._worker_dir("practice-task")
            directory.mkdir(parents=True)
            (directory / "process.json").write_text(
                capture_worker_process(owned.pid).model_dump_json()
            )
            restarted = ProjectProcedureWorkers(lambda: tmp_path, lambda _: "closed")
            restarted.retire_software("practice-task")
            assert owned.poll() is not None
            assert unrelated.poll() is None
            assert not directory.exists()
        finally:
            for child in (owned, unrelated):
                if child.poll() is None:
                    child.terminate()
                child.wait(timeout=5)
