"""An ordinary edit/scan/run survives closing the notebook connection."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import cast

import numpy as np
import scopecat as sc
from scopecat.api.lab import LabClient
from scopecat.application.author_project import AuthorProject
from scopecat.daemon.client import DaemonClient
from scopecat.records.config_context import ConfigContextRef
from scopecat.records.sample import SampleRevisionDraft

from .conftest import ReferenceLabDaemon


def test_workspace_run_reopens_in_fresh_python(
    reference_lab_daemon: ReferenceLabDaemon, tmp_path: Path
) -> None:
    endpoint = reference_lab_daemon.url
    with (
        AuthorProject(endpoint, receipts=tmp_path / "receipts") as author,
        LabClient(DaemonClient(endpoint)) as setup,
    ):
        active = author.config.active()
        sample = setup.samples.create(
            "managed-notebook",
            kind="synthetic",
            content=SampleRevisionDraft(display_name="Managed notebook"),
        )
        author.config.save_context(
            entry_id="managed-start",
            base=ConfigContextRef(
                entry_id=active.entry.id, content_hash=active.entry.content_hash
            ),
            sample=sample.selector(),
            working_point_id="start",
            label="Start",
            parameters=active.config.parameter_snapshot,
        )
        parameters = author.config.workspace(context="managed-start")
        parameters["qubits"]["q0"]["drive_carrier_frequency"] = sc.Quantity(5.1, "GHz")
        prepared = author.prepare(
            "signal",
            parameters=parameters,
            fixed={"gain": 1.0},
            scans={"frequency": np.linspace(5.0, 5.2, 3)},
        )
        assert prepared.preview.point_count == 3
        assert (
            author.prepare(
                "signal", scans={"frequency": range(4, 7)}
            ).preview.point_count
            == 3
        )
        parameters["qubits"]["q0"]["drive_carrier_frequency"] = sc.Quantity(4.8, "GHz")
        job = prepared.run().wait(timeout=60)
        run = job.result()
        values = cast(
            "tuple[float, ...]", run.measurements()["result"].require_values()
        )
        assert max(values) == 1.0
        snapshot = run.snapshot
        receipt = job.receipt
        assert run.snapshot.config_source == prepared.preview.config_source
        assert author.config.active() == active
    assert max(values) == 1.0
    assert snapshot.status == "completed"
    source = """import json, sys
from scopecat.application.author_project import AuthorProject
with AuthorProject(sys.argv[1]) as author:
    job = author.reopen(sys.argv[2])
    run = job.wait(timeout=5).result()
    values = list(run.measurements()["result"].require_values())
    print(json.dumps({"run": run.id, "values": values}))
"""
    completed = subprocess.run(  # noqa: S603 - fixed notebook restart scenario
        [sys.executable, "-c", source, endpoint, str(receipt)],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    retained = cast("dict[str, object]", json.loads(completed.stdout))
    assert retained["run"] == run.id
    assert retained["values"] == list(values)


def test_lost_response_recovers_admitted_job_once(
    reference_lab_daemon: ReferenceLabDaemon, tmp_path: Path
) -> None:
    import httpx2
    import pytest
    from scopecat.application.author_project import AuthorSubmissionUncertain

    submitted: list[str] = []
    with httpx2.HTTPTransport() as http:

        def lose_submission_response(request: httpx2.Request) -> httpx2.Response:
            response = http.handle_request(request)
            if request.url.path.endswith("/submit"):
                response.read()
                response.close()
                submitted.append(request.url.path)
                raise httpx2.ReadError("lost after admission", request=request)
            return response

        with AuthorProject(
            reference_lab_daemon.url,
            receipts=tmp_path / "receipts",
            transport=httpx2.MockTransport(lose_submission_response),
        ) as author:
            prepared = author.prepare("signal")
            with pytest.raises(AuthorSubmissionUncertain) as caught:
                prepared.run()
            job = caught.value.job
            assert job.receipt.exists()
            job.wait(timeout=60)
            run_id = job.result().id
            assert author.reopen(job.receipt).result().id == run_id
    assert len(submitted) == 1


def test_invalid_names_preserve_warm_author_worker(
    reference_lab_daemon: ReferenceLabDaemon,
) -> None:
    import httpx2
    import psutil
    import pytest

    def worker_ids() -> set[int]:
        identities: set[int] = set()
        for process in psutil.Process(reference_lab_daemon.pid).children(
            recursive=True
        ):
            try:
                command = process.cmdline()
            except psutil.NoSuchProcess:
                continue
            if any(
                name in command
                for name in (
                    "scopecat_server.validation_worker",
                    "scopecat_server.launch_worker",
                )
            ):
                identities.add(process.pid)
        return identities

    with AuthorProject(reference_lab_daemon.url) as author:
        author.prepare("signal")
        initial_workers = worker_ids()
        assert initial_workers
        initial_state = author.state()
        with pytest.raises(
            httpx2.HTTPStatusError, match=r"available controls:.*frequency"
        ):
            author.prepare("signal", scans={"frequncy": [5.0, 5.1]})
        assert worker_ids() == initial_workers
        author.prepare("signal")
        with pytest.raises(httpx2.HTTPStatusError, match="polarty"):
            author.prepare("signal", inputs={"polarty": "positive"})
        assert worker_ids() == initial_workers
        author.prepare("signal")
        assert worker_ids() == initial_workers
        assert author.state() == initial_state
