"""Real pipe/process ownership without a lab or acquisition fixture."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, TextIO, cast
from unittest.mock import patch

import pytest
from scopecat.records.author_revision import AuthorRevisionRef
from scopecat.records.launch_request import LaunchRequest

from scopecat_server.services.revision_workers import (
    AuthorWorkerBinding,
    RevisionWorkers,
)

if TYPE_CHECKING:
    from scopecat.application import LabApplication


_CODE = """
import json, os, sys, time
for line in sys.stdin:
    command = json.loads(line)
    if command['experiment'] == 'fail':
        print('controlled failure', file=sys.stderr, flush=True)
        sys.exit(1)
    if command['experiment'] == 'slow':
        time.sleep(120)
    result = {'pid': os.getpid(), 'revision': sys.argv[1], 'workspace': sys.argv[2]}
    print(json.dumps(result), flush=True)
"""


def request(revision: str = "a", *, experiment: str = "") -> LaunchRequest:
    return LaunchRequest(
        action="list",
        experiment=experiment,
        code_revision=AuthorRevisionRef(content_hash="sha256:" + revision * 64),
    )


def test_reuse_isolation_eviction_and_failure(tmp_path: Path) -> None:
    real_spawn = subprocess.Popen
    children: list[subprocess.Popen[str]] = []

    def spawn(
        args: list[str],
        *,
        stdin: int,
        stdout: int,
        stderr: TextIO,
        encoding: str,
        creationflags: int,
    ) -> subprocess.Popen[str]:
        # Preserve the real pipe/encoding/diagnostics protocol, replace project code.
        child = real_spawn(
            [args[0], "-c", _CODE, args[-1], args[3]],
            stdin=stdin,
            stdout=stdout,
            stderr=stderr,
            encoding=encoding,
            creationflags=creationflags,
        )
        children.append(child)
        return child

    binding = AuthorWorkerBinding(tmp_path, Path(sys.executable).absolute())
    pool = RevisionWorkers()
    try:
        with patch(
            "scopecat_server.services.revision_workers.subprocess.Popen",
            side_effect=spawn,
        ):
            a = pool.call(binding, request())
            assert a.returncode == 0
            assert pool.call(binding, request()).stdout == a.stdout
            b = pool.call(binding, request("b"))
            assert json.loads(a.stdout)["pid"] != json.loads(b.stdout)["pid"]
            assert pool.call(binding, request()).stdout == a.stdout
            assert pool.call(binding, request("c")).returncode == 0
            assert children[1].poll() is not None  # b was least recently used
            failed = pool.call(binding, request(experiment="fail"))
            assert failed.returncode == 1
            assert "controlled failure" in failed.stderr
            assert len(children) == 3  # no implicit retry
            recovered = pool.call(binding, request())
            assert recovered.returncode == 0
            assert recovered.stdout != a.stdout
            with pytest.raises(subprocess.TimeoutExpired):
                pool.call(binding, request(experiment="slow"), timeout=0.1)
            assert children[-1].poll() is not None
            assert len(children) == 4  # timeout also never retries
            original = pool.call(binding, request())
            other_binding = AuthorWorkerBinding(tmp_path / "other", binding.python)
            other = pool.call(other_binding, request())
            assert json.loads(original.stdout)["pid"] != json.loads(other.stdout)["pid"]
            assert json.loads(other.stdout)["workspace"] == str(other_binding.workspace)
            assert pool.call(binding, request()).stdout == original.stdout
    finally:
        pool.close()
    assert all(child.poll() is not None for child in children)


def test_serve_rejects_request_without_reloading_application() -> None:
    import io

    from scopecat.application.launch import LaunchCatalog, LaunchRequestRejected

    from scopecat_server.launch_worker import serve

    command = request()
    assert command.code_revision is not None
    application = cast("LabApplication", object())
    output = io.StringIO()
    with (
        patch(
            "sys.stdin",
            io.StringIO(
                command.model_dump_json() + "\n" + command.model_dump_json() + "\n"
            ),
        ),
        patch("sys.stdout", output),
        patch(
            "scopecat_server.launch_worker.launch",
            side_effect=[
                LaunchRequestRejected("unknown control 'amplitudes'"),
                LaunchCatalog(),
            ],
        ) as launch,
    ):
        serve(Path.cwd(), command.code_revision, application=application)
    replies = [json.loads(line) for line in output.getvalue().splitlines()]
    assert replies[0] == {
        "kind": "launch_rejection",
        "message": "unknown control 'amplitudes'",
        "problems": [],
        "scenario": None,
    }
    assert replies[1]["entries"] == []
    assert all(call.args[0] is application for call in launch.call_args_list)


def test_serve_does_not_hide_author_code_failure() -> None:
    import io

    from scopecat_server.launch_worker import serve

    command = request()
    assert command.code_revision is not None
    with (
        patch("sys.stdin", io.StringIO(command.model_dump_json() + "\n")),
        patch(
            "scopecat_server.launch_worker.launch", side_effect=ValueError("author bug")
        ),
        pytest.raises(ValueError, match="author bug"),
    ):
        serve(
            Path.cwd(),
            command.code_revision,
            application=cast("LabApplication", object()),
        )


def test_serve_preserves_structured_check_and_keeps_worker_ready() -> None:
    import io

    from scopecat.application.launch import LaunchCatalog, LaunchRequestRejected
    from scopecat.kernel.problems import ProblemPhase, model_location, problem
    from scopecat.records.execution_scenario import SoftwareExecutionScenario
    from scopecat.records.launch_rejection import LaunchRejection

    from scopecat_server.launch_worker import serve

    command = request()
    assert command.code_revision is not None
    finding = problem(
        "instrument_operation_unsupported",
        "No response readback",
        phase=ProblemPhase.PLANNING,
        location=model_location("operation", "read"),
        details={"instrument": "source"},
    )
    scenario = SoftwareExecutionScenario(
        id="software",
        label="Software",
        model_id="test",
        model_version="1",
        capabilities=("writes",),
    )
    output = io.StringIO()
    with (
        patch("sys.stdin", io.StringIO((command.model_dump_json() + "\n") * 2)),
        patch("sys.stdout", output),
        patch(
            "scopecat_server.launch_worker.launch",
            side_effect=[
                LaunchRequestRejected(
                    "Preparation failed", problems=(finding,), scenario=scenario
                ),
                LaunchCatalog(),
            ],
        ),
    ):
        serve(
            Path.cwd(),
            command.code_revision,
            application=cast("LabApplication", object()),
        )
    first, second = output.getvalue().splitlines()
    diagnostic = LaunchRejection.model_validate_json(first)
    assert diagnostic.problems == (finding,)
    assert diagnostic.scenario == scenario
    assert json.loads(second)["entries"] == []
