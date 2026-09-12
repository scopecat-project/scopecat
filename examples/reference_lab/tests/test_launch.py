"""Real project-worker catalog/preview, immutable admission, and durable outcomes."""

from __future__ import annotations

import shutil
import time
from collections.abc import Generator
from dataclasses import dataclass

import httpx2
import pytest
from pydantic import ValidationError
from scopecat.api.run import RunHandle
from scopecat.application import LabApplication
from scopecat.application.launch import LaunchCatalog, LaunchPreview, LaunchSubmission
from scopecat.daemon.client import DaemonClient, DaemonConflictError
from scopecat.daemon.endpoint import DAEMON_URL_ENV
from scopecat.kernel.content_identity import sha256_json_hash
from scopecat.kernel.quantity import Quantity
from scopecat.planning.preflight import ExactQuantity, PreflightStage, UnknownQuantity
from scopecat.project import Project, load_project
from scopecat.records.launch_request import LaunchRequest
from scopecat.records.measurement import MeasurementScalar
from scopecat.records.run import ConfigRegistryRunConfigSource
from scopecat_server.lifecycle import start_project, stop_project
from scopecat_testkit.project_loading import isolated_project_imports

from reference_lab.configuration import EXAMPLE_ROOT


@dataclass(frozen=True)
class _Daemon:
    url: str


@pytest.fixture(scope="module")
def reference_lab_daemon(
    tmp_path_factory: pytest.TempPathFactory,
) -> Generator[_Daemon]:
    # Gallery notebooks may accept new defaults in their session daemon. Launcher
    # scenarios have their own project so a default request has a stable base.
    roots = [
        tmp_path_factory.mktemp(name) for name in ("foreign-project", "launch-project")
    ]
    projects: list[Project] = []
    for root in roots:
        for name in ("config", "src"):
            shutil.copytree(EXAMPLE_ROOT / name, root / name)
        shutil.copy2(EXAMPLE_ROOT / "scopecat.toml", root / "scopecat.toml")
        projects.append(load_project(root / "scopecat.toml"))
    foreign, project = projects
    with pytest.MonkeyPatch.context() as patch:
        patch.delenv(DAEMON_URL_ENV, raising=False)
        foreign_endpoint = start_project(foreign)
    try:
        # The target daemon itself inherits a live foreign endpoint. Its internal
        # preview and procedure workers must still use the target's project record.
        with pytest.MonkeyPatch.context() as patch:
            patch.setenv(DAEMON_URL_ENV, foreign_endpoint.base_url)
            endpoint = start_project(project)
        try:
            yield _Daemon(endpoint.base_url)
        finally:
            stop_project(project)
    finally:
        stop_project(foreign)


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
            "code_revision": preview.code_revision,
            "manual_state": preview.manual_state,
        }
    )


def assert_retained_shapes(stage: PreflightStage, run: RunHandle) -> None:
    schema = run.measurements().schema
    dimensions = {dimension.id: dimension.size for dimension in schema.dimensions}
    variables = {
        variable.id: variable
        for variable in schema.variables
        if variable.role == "observable"
    }
    retained = [
        product for product in stage.products if product.retention == "retained"
    ]
    assert {product.id for product in retained} == set(variables)
    for product in retained:
        variable = variables[product.id]
        assert product.dims == tuple(variable.dims)
        assert product.shape == tuple(
            dimensions[dimension] for dimension in variable.dims
        )
        assert product.dtype == variable.dtype
        assert product.unit == variable.unit


@pytest.mark.parametrize("experiment", ["temperature", "channel-timing"])
def test_real_http_preview_shares_catalog_and_never_admits_acquisition(
    reference_lab_daemon: _Daemon,
    launch_application: LabApplication,
    experiment: str,
) -> None:
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
        assert response.is_success, response.text
        catalog = LaunchCatalog.model_validate(response.json())
        expected = provider(lab, LaunchRequest(action="list"))
        assert isinstance(expected, LaunchCatalog)
        assert catalog.code_revision is not None
        assert [(item.id, item.title, item.controls) for item in catalog.entries] == [
            (item.id, item.title, item.controls) for item in expected.entries
        ]
        before = client.list_runs()
        active = lab.config.active()
        request = LaunchRequest(action="preview", experiment=experiment, version="1")
        response = http.post(
            "/api/v1/experiment-launcher/preview", json=request.model_dump(mode="json")
        )
        assert response.is_success, response.text
        preview = LaunchPreview.model_validate(response.json())
        repeated = provider(lab, request)
        assert isinstance(repeated, LaunchPreview)
        # Target inspection includes per-compilation timing/cache diagnostics.
        assert preview.code_revision == catalog.code_revision
        assert repeated.code_revision is None  # direct, deliberately unpinned provider
        selected_entry = next(item for item in catalog.entries if item.id == experiment)
        assert preview.definition_hash == sha256_json_hash(
            selected_entry.model_dump(mode="json")
        )
        assert repeated.definition_hash is None  # worker-owned catalog projection
        exclude = {
            "code_revision": True,
            "manual_state": True,
            "definition_hash": True,
            "preflight": {"stages": {"__all__": {"inspections"}}},
        }
        assert preview.model_dump(exclude=exclude) == repeated.model_dump(
            exclude=exclude
        )
        assert preview.preflight is not None
        assert len(preview.preflight.stages) == (
            1 if experiment == "temperature" else 2
        )
        assert all(stage.selected_points <= 1 for stage in preview.preflight.stages)
        assert all(stage.sampled_points <= 64 for stage in preview.preflight.stages)
        for stage in preview.preflight.stages:
            wall_time = next(cost for cost in stage.costs if cost.metric == "wall_time")
            assert isinstance(wall_time.quantity, UnknownQuantity)
            assert wall_time.scope == "experiment"
            assert len(stage.planned_settings) <= stage.planned_setting_limit == 64
            if experiment == "channel-timing":
                [frequency] = [
                    setting
                    for setting in stage.planned_settings
                    if setting.instrument_id == "drive-lo-a"
                    and setting.setting.target.property_id == "frequency"
                ]
                assert frequency.setting.value.root == Quantity(4_850_000_000, "Hz")
                assert frequency.point_index == 0
                assert not stage.planned_settings_truncated
                assert isinstance(stage.shots_per_point_per_entity, ExactQuantity)
                assert stage.shots_per_point_per_entity.value == 64
                playback = next(
                    cost
                    for cost in stage.costs
                    if cost.metric == "waveform_playback_time"
                )
                assert isinstance(playback.quantity, ExactQuantity)
                assert playback.quantity.value > 0
                assert playback.quantity.unit == "s"
                assert playback.scope == "inspected_artifact"
                assert playback.target_id == stage.inspections[0].target_id
                assert playback.artifact_fingerprint == (
                    stage.inspections[0].artifact_fingerprint
                )
        assert preview.point_count == (1 if experiment == "temperature" else 2)
        assert isinstance(preview.config_source, ConfigRegistryRunConfigSource)
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
        assert preview.preflight is not None
        assert_retained_shapes(preview.preflight.stages[0], run)
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
        assert preview.preflight is not None
        assert [stage.configuration for stage in preview.preflight.stages] == [
            "accepted",
            "proposed_candidate",
        ]
        for stage in preview.preflight.stages:
            output = handle.output(stage.id)
            assert output.kind == "run"
            assert_retained_shapes(stage, lab.get_run(output.run_id))
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
        assert response.is_success, response.text
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


def test_noop_candidate_preview_reports_reason_without_admitting_work(
    reference_lab_daemon: _Daemon,
    launch_application: LabApplication,
) -> None:
    import scopecat as sc
    from scopecat.config.parameter_updates import materialize_parameter_updates

    from reference_lab.parameters import ChannelCalibration

    with (
        launch_application.connect(reference_lab_daemon.url) as lab,
        DaemonClient(reference_lab_daemon.url) as client,
        httpx2.Client(
            base_url=reference_lab_daemon.url, trust_env=False, timeout=30
        ) as http,
    ):
        original = lab.config.active().config
        parameters, _ = materialize_parameter_updates(
            catalog=original.parameter_catalog,
            base=original.parameter_snapshot,
            updates=(
                sc.parameter_update(
                    ChannelCalibration.channel_delay,
                    sc.EntityRef(id="q1", kind="logical_qubit"),
                    1.0,
                ),
            ),
            candidate_id="already-at-requested-delay",
        )
        lab.config.set_default(
            original.model_copy(update={"parameter_snapshot": parameters})
        )
        try:
            before = client.list_runs()
            response = http.post(
                "/api/v1/experiment-launcher/preview",
                json={
                    "action": "preview",
                    "experiment": "channel-timing",
                    "version": "1",
                },
            )
            assert response.status_code == 422, response.text
            assert (
                "parameter change proposal does not change the base snapshot"
                in response.text
            )
            assert client.list_runs() == before
        finally:
            lab.config.set_default(original)


def test_http_controls_persist_one_source_and_match_notebook_edits(
    reference_lab_daemon: _Daemon, launch_application: LabApplication
) -> None:
    from scopecat.application.controls import edit_controls
    from scopecat.compiler.frontend.resolution import compile_invocation
    from scopecat.records.control_edit import ControlEdit

    from reference_lab.configuration import bootstrap_config
    from reference_lab.workflows.frequency_amplitude import (
        CONTROLS,
        frequency_amplitude,
    )

    values: list[float] = []
    with (
        launch_application.connect(reference_lab_daemon.url) as lab,
        httpx2.Client(
            base_url=reference_lab_daemon.url, trust_env=False, timeout=30
        ) as http,
    ):
        for mode in ("fixed", "scan"):
            frequency = {"value": 4900.0, "unit": "MHz"}
            edit = (
                {"mode": "fixed", "value": frequency}
                if mode == "fixed"
                else {"mode": "scan", "axis": {"kind": "values", "values": [frequency]}}
            )
            request = LaunchRequest(
                action="preview",
                experiment="frequency-amplitude",
                version="1",
                control_edits={
                    "frequency": ControlEdit.model_validate(edit),
                    "amplitude": ControlEdit(mode="fixed", value=Quantity(100, "mV")),
                },
            )
            response = http.post(
                "/api/v1/experiment-launcher/preview",
                json=request.model_dump(mode="json"),
            )
            assert response.is_success, response.text
            preview = LaunchPreview.model_validate(response.json())
            assert preview.point_count == 1
            assert preview.controls[0].state == (
                "fixed" if mode == "fixed" else "scanned"
            )
            notebook = edit_controls(
                CONTROLS,
                frequency_amplitude(),
                config=bootstrap_config(),
                edits=request.control_edits,
            )
            expected = compile_invocation(notebook).request
            command = submit_request(request, preview, f"controls-{mode}")
            response = http.post(
                "/api/v1/experiment-launcher/submit",
                json=command.model_dump(mode="json"),
            )
            assert response.is_success, response.text
            admission = LaunchSubmission.model_validate(response.json())
            assert admission.dispatch_error is None
            handle = lab.procedures.get(admission.procedure_id)
            deadline = time.monotonic() + 30
            while (
                handle.state not in {"closed", "attention_required"}
                and time.monotonic() < deadline
            ):
                time.sleep(0.05)
            assert handle.state == "closed"
            output = handle.output("signal")
            assert output.kind == "run"
            run = lab.get_run(output.run_id)
            assert run.request.point_plan == expected.point_plan
            assert run.request.inputs == expected.inputs == {}
            [record] = run.measurements().records
            measured = record.observables["response"]
            assert isinstance(measured, MeasurementScalar) and isinstance(
                measured.value, float
            )
            values.append(measured.value)
        assert values[0] == values[1]
        unsafe = request.model_copy(
            update={
                "control_edits": {
                    "frequency": ControlEdit(mode="fixed", value=Quantity(5.4, "GHz")),
                    "amplitude": ControlEdit(mode="fixed", value=Quantity(0.3, "V")),
                }
            }
        )
        response = http.post(
            "/api/v1/experiment-launcher/preview", json=unsafe.model_dump(mode="json")
        )
        assert response.status_code == 422 and "Amplitude" in response.text
        unknown = unsafe.model_copy(update={"experiment": "temperature"})
        response = http.post(
            "/api/v1/experiment-launcher/preview", json=unknown.model_dump(mode="json")
        )
        assert response.status_code == 422 and "unknown control" in response.text
