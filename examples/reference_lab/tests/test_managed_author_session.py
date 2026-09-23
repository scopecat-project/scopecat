"""An ordinary edit/scan/run survives closing the notebook connection."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import cast

import numpy as np
import pytest
import scopecat as sc
from scopecat.api.lab import LabClient
from scopecat.application.author_project import AuthorProject
from scopecat.daemon.client import DaemonClient, DaemonConflictError
from scopecat.records.parameter_revision import ParameterRevision
from scopecat.records.sample import SampleRevisionDraft


def test_workspace_run_reopens_in_fresh_python(
    independent_lab_daemon: str,
    independent_parameters: ParameterRevision,
    tmp_path: Path,
) -> None:
    endpoint = independent_lab_daemon
    with (
        AuthorProject(endpoint, receipts=tmp_path / "receipts") as author,
        LabClient(DaemonClient(endpoint)) as setup,
    ):
        equipment = setup.setup.active()
        sample = setup.samples.create(
            "managed-notebook",
            kind="synthetic",
            content=SampleRevisionDraft(display_name="Managed notebook"),
        )
        branch = author.parameters.create_branch(
            "managed-start", revision=independent_parameters
        )
        author.use(
            sample=sample.id, parameter_branch=branch.name, setup=equipment.revision.ref
        )
        parameters = author.parameters.workspace(branch.name)
        stale = parameters.copy()
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
        saved = parameters.save(note="Notebook edit")
        assert parameters.save() == saved
        stale["qubits"]["q0"]["drive_carrier_frequency"] = sc.Quantity(4.9, "GHz")
        with pytest.raises(DaemonConflictError, match="branch changed"):
            stale.save()
        assert author.parameters.workspace(branch.name).version == saved
        job = prepared.run().wait(timeout=60)
        run = job.result()
        values = cast(
            "tuple[float, ...]", run.measurements()["result"].require_values()
        )
        assert max(values) == 1.0
        snapshot = run.snapshot
        receipt = job.receipt
        assert run.snapshot.config_source == prepared.preview.reviewed.config_source
        assert setup.setup.active() == equipment
        assert setup.config.registry().entries == ()
    assert max(values) == 1.0
    assert snapshot.status == "completed"
    source = """import json, sys
from scopecat.application.author_project import AuthorProject
with AuthorProject(sys.argv[1]) as author:
    job = author.reopen(sys.argv[2])
    run = job.wait(timeout=5).result()
    values = list(run.measurements()["result"].require_values())
    latest = author.parameters.workspace("managed-start")
    assert latest.version.id == sys.argv[3]
    exact = author.parameters.get(sys.argv[4])
    assert exact.ref != latest.version.ref
    assert run.snapshot.config_source.parameters == exact.ref
    assert run.samples[0].sample_id == "managed-notebook"
    print(json.dumps({"run": run.id, "values": values}))
"""
    completed = subprocess.run(  # noqa: S603 - fixed notebook restart scenario
        [
            sys.executable,
            "-c",
            source,
            endpoint,
            str(receipt),
            saved.id,
            independent_parameters.id,
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    retained = cast("dict[str, object]", json.loads(completed.stdout))
    assert retained["run"] == run.id
    assert retained["values"] == list(values)


def test_lost_response_recovers_admitted_job_once(
    independent_lab_daemon: str,
    independent_parameters: ParameterRevision,
    tmp_path: Path,
) -> None:
    import httpx2
    import pytest
    from scopecat.application.author_project import AuthorSubmissionUncertain

    submitted: list[str] = []
    with (
        httpx2.HTTPTransport() as http,
        LabClient(DaemonClient(independent_lab_daemon)) as lab,
    ):

        def lose_submission_response(request: httpx2.Request) -> httpx2.Response:
            response = http.handle_request(request)
            if request.url.path.endswith("/submit"):
                response.read()
                response.close()
                submitted.append(request.url.path)
                raise httpx2.ReadError("lost after admission", request=request)
            return response

        with AuthorProject(
            independent_lab_daemon,
            receipts=tmp_path / "receipts",
            transport=httpx2.MockTransport(lose_submission_response),
        ) as author:
            author.use(
                parameters=independent_parameters.ref,
                setup=lab.setup.active().revision.ref,
            )
            prepared = author.prepare("signal")
            with pytest.raises(AuthorSubmissionUncertain) as caught:
                prepared.run()
            job = caught.value.job
            assert job.receipt.exists()
            job.wait(timeout=60)
            run_id = job.result().id
            assert author.reopen(job.receipt).result().id == run_id
    assert len(submitted) == 1
