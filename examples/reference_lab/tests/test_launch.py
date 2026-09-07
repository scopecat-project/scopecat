"""Real project-worker catalog/preview, immutable admission, and durable outcomes."""

from __future__ import annotations

import time
from typing import Protocol

import httpx2
import pytest
from pydantic import ValidationError
from scopecat.application import LabApplication
from scopecat.application.launch import (
    LaunchCatalog,
    LaunchPreview,
    LaunchRequest,
    LaunchSubmission,
)
from scopecat.daemon.client import DaemonClient, DaemonConflictError
from scopecat.project import load_project
from scopecat.records.measurement import MeasurementScalar
from scopecat_testkit.project_loading import isolated_project_imports

from reference_lab.configuration import EXAMPLE_ROOT


class _Daemon(Protocol):
    url: str


@pytest.fixture
def launch_application() -> LabApplication:
    # Reproduce a preceding manifest-discovery test and its import cleanup,
    # independent of xdist scheduling. Collection-time project imports are stale.
    with isolated_project_imports():
        load_project(EXAMPLE_ROOT / "scopecat.toml").load_bootstrap()
    # Compose the callback and registry from the same current project imports.
    from reference_lab.application import create_application

    return create_application(EXAMPLE_ROOT)


def submit_request(
    request: LaunchRequest, preview: LaunchPreview, key: str
) -> LaunchRequest:
    return LaunchRequest.model_validate(
        {
            **request.model_dump(),
            "action": "submit",
            "request_key": key,
            "expected_request_hash": preview.request_hash,
            "config_source": preview.config_source,
        }
    )


@pytest.mark.parametrize("experiment", ["temperature", "channel-timing"])
def test_real_http_preview_shares_catalog_and_never_admits_acquisition(
    reference_lab_daemon: _Daemon,
    launch_application: LabApplication,
    experiment: str,
) -> None:
    from reference_lab.launch import CATALOG

    provider = launch_application.launch_provider
    assert provider is not None
    with (
        launch_application.connect(reference_lab_daemon.url) as lab,
        DaemonClient(reference_lab_daemon.url) as client,
        httpx2.Client(
            base_url=reference_lab_daemon.url, trust_env=False, timeout=30
        ) as http,
    ):
        response = http.get("/api/v1/experiment-launcher")
        response.raise_for_status()
        assert LaunchCatalog.model_validate(response.json()) == CATALOG
        before = client.list_runs()
        active = lab.config.active()
        request = LaunchRequest(action="preview", experiment=experiment, version="1")
        response = http.post(
            "/api/v1/experiment-launcher/preview", json=request.model_dump(mode="json")
        )
        response.raise_for_status()
        preview = LaunchPreview.model_validate(response.json())
        assert preview == provider(lab, request)
        assert preview.point_count == (1 if experiment == "temperature" else 2)
        assert preview.config_source.entry_id == active.entry.id
        assert client.list_runs() == before
        assert lab.config.active() == active


def test_submission_fences_new_stale_work_but_replays_exact_admission(
    reference_lab_daemon: _Daemon,
    launch_application: LabApplication,
) -> None:
    provider = launch_application.launch_provider
    assert provider is not None
    with launch_application.connect(reference_lab_daemon.url) as lab:
        request = LaunchRequest(action="preview", experiment="temperature", version="1")
        preview = provider(lab, request)
        assert isinstance(preview, LaunchPreview)
        command = submit_request(request, preview, "launch-retry")
        admitted = provider(lab, command)
        assert isinstance(admitted, LaunchSubmission)
        original_config = lab.config.active().config
        lab.config.set_default(original_config)
        assert provider(lab, command) == admitted
        with pytest.raises(DaemonConflictError, match="active configuration changed"):
            provider(lab, submit_request(request, preview, "new-stale-request"))
        changed = request.model_copy(update={"actor": "another-operator"})
        with pytest.raises(ValidationError, match="request changed"):
            submit_request(changed, preview, "launch-retry")
        new_preview = provider(lab, changed)
        assert isinstance(new_preview, LaunchPreview)
        with pytest.raises(DaemonConflictError, match="different intent"):
            provider(lab, submit_request(changed, new_preview, "launch-retry"))
        handle = lab.procedures.get(admitted.procedure_id).resume()
        assert handle.state == "closed"
        output = handle.output("diagnostic")
        assert output.kind == "run"
        run = lab.get_run(output.run_id)
        assert run.status == "completed"
        assert run.snapshot.config_source == preview.config_source
        records = run.measurements().records
        temperature = records[0].observables["temperature"]
        assert isinstance(temperature, MeasurementScalar) and temperature.unit == "K"
        with DaemonClient(reference_lab_daemon.url) as client:
            assert client.measurement_preview(run.id).items == records


def test_candidate_uses_existing_review_state_and_retains_result_references(
    reference_lab_daemon: _Daemon,
    launch_application: LabApplication,
) -> None:
    from reference_lab.launch import TIMING_REVIEW, TimingReview

    provider = launch_application.launch_provider
    assert provider is not None
    with launch_application.connect(reference_lab_daemon.url) as lab:
        request = LaunchRequest(
            action="preview", experiment="channel-timing", version="1"
        )
        preview = provider(lab, request)
        assert isinstance(preview, LaunchPreview)
        before = lab.config.active()
        admitted = provider(
            lab, submit_request(request, preview, "launch-reviewed-candidate")
        )
        assert isinstance(admitted, LaunchSubmission)
        handle = lab.procedures.get(admitted.procedure_id).resume()
        assert handle.state == "waiting_for_input"
        assert handle.output("source").kind == "run"
        assert handle.output("proposal").kind == "analysis"
        candidate = handle.output("candidate")
        assert candidate.kind == "run"
        candidate_source = lab.get_run(candidate.run_id).snapshot.config_source
        assert (
            candidate_source is not None
            and candidate_source.kind == "analysis_candidate"
        )
        review = handle.step("review").interpretation_request
        assert review is not None and review.schema_id == TIMING_REVIEW.id
        handle.respond(
            "review",
            TimingReview(True, "Candidate checked"),
            schema=TIMING_REVIEW,
            actor="reviewer",
        )
        handle.resume()
        assert handle.state == "closed"
        assert lab.config.active() == before


def test_http_submission_dispatches_the_same_durable_diagnostic(
    reference_lab_daemon: _Daemon,
    launch_application: LabApplication,
) -> None:
    with (
        launch_application.connect(reference_lab_daemon.url) as lab,
        httpx2.Client(
            base_url=reference_lab_daemon.url, trust_env=False, timeout=30
        ) as http,
    ):
        request = LaunchRequest(action="preview", experiment="temperature", version="1")
        preview_response = http.post(
            "/api/v1/experiment-launcher/preview",
            json=request.model_dump(mode="json"),
        )
        preview_response.raise_for_status()
        preview = LaunchPreview.model_validate(preview_response.json())
        command = submit_request(request, preview, "http-dispatched-diagnostic")
        response = http.post(
            "/api/v1/experiment-launcher/submit",
            json=command.model_dump(mode="json"),
        )
        response.raise_for_status()
        admitted = LaunchSubmission.model_validate(response.json())
        assert admitted.dispatch_error is None
        handle = lab.procedures.get(admitted.procedure_id)
        deadline = time.monotonic() + 30
        while (
            handle.state not in {"closed", "attention_required"}
            and time.monotonic() < deadline
        ):
            time.sleep(0.05)
        assert handle.state == "closed"
        assert handle.output("diagnostic").kind == "run"
        retry = http.post(
            "/api/v1/experiment-launcher/submit",
            json=command.model_dump(mode="json"),
        )
        retry.raise_for_status()
        assert LaunchSubmission.model_validate(retry.json()) == admitted
