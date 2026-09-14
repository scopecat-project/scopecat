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

from scopecat_server.services.revision_workers import RevisionWorkers

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
    print(json.dumps({'pid': os.getpid(), 'revision': sys.argv[1]}), flush=True)
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
            [sys.executable, "-c", _CODE, args[-1]],
            stdin=stdin,
            stdout=stdout,
            stderr=stderr,
            encoding=encoding,
            creationflags=creationflags,
        )
        children.append(child)
        return child

    pool = RevisionWorkers()
    try:
        with patch(
            "scopecat_server.services.revision_workers.subprocess.Popen",
            side_effect=spawn,
        ):
            a = pool.call(tmp_path, request())
            assert a.returncode == 0
            assert pool.call(tmp_path, request()).stdout == a.stdout
            b = pool.call(tmp_path, request("b"))
            assert json.loads(a.stdout)["pid"] != json.loads(b.stdout)["pid"]
            assert pool.call(tmp_path, request()).stdout == a.stdout
            assert pool.call(tmp_path, request("c")).returncode == 0
            assert children[1].poll() is not None  # b was least recently used
            failed = pool.call(tmp_path, request(experiment="fail"))
            assert failed.returncode == 1
            assert "controlled failure" in failed.stderr
            assert len(children) == 3  # no implicit retry
            recovered = pool.call(tmp_path, request())
            assert recovered.returncode == 0
            assert recovered.stdout != a.stdout
            with pytest.raises(subprocess.TimeoutExpired):
                pool.call(tmp_path, request(experiment="slow"), timeout=0.1)
            assert children[-1].poll() is not None
            assert len(children) == 4  # timeout also never retries
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
        "detail": "unknown control 'amplitudes'",
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
