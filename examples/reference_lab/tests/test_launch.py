"""Real project-worker catalog/preview, immutable admission, and durable outcomes."""

from __future__ import annotations

import shutil
import time
from collections.abc import Generator
from dataclasses import dataclass
from typing import cast

import httpx2
import pytest
from pydantic import ValidationError
from scopecat.api.lab import LabClient
from scopecat.api.run import RunHandle
from scopecat.application import LabApplication
from scopecat.application.launch import LaunchCatalog, LaunchPreview, LaunchSubmission
from scopecat.daemon.client import DaemonClient, DaemonConflictError
from scopecat.daemon.endpoint import DAEMON_URL_ENV
from scopecat.kernel.content_identity import sha256_json_hash
from scopecat.planning.preflight import PreflightStage, UnknownQuantity
from scopecat.project import Project, load_project
from scopecat.records.launch_request import LaunchRequest
from scopecat.records.measurement import MeasurementScalar
from scopecat.records.run import ParameterRunConfigSource
from scopecat.records.scientific_selection import (
    ParameterConfiguration,
    ScientificSelection,
)
from scopecat_server.lifecycle import start_project, stop_project
from scopecat_testkit.authoring import source_workspace_id
from scopecat_testkit.project_loading import isolated_project_imports

from reference_lab.configuration import EXAMPLE_ROOT, bootstrap_config


@dataclass(frozen=True)
class _Daemon:
    url: str
    selection: ScientificSelection


@pytest.fixture(scope="module")
def reference_lab_daemon(
    tmp_path_factory: pytest.TempPathFactory,
) -> Generator[_Daemon]:
    # Separate projects exercise endpoint isolation and setup authority changes.
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
            with LabClient(DaemonClient(endpoint.base_url)) as lab:
                config = bootstrap_config()
                parameters = lab.parameters.save(
                    name="launcher-inputs",
                    catalog=config.parameter_catalog,
                    parameters=config.parameter_snapshot,
                )
                selection = ScientificSelection(
                    configuration=ParameterConfiguration(
                        ref=parameters.ref, setup=lab.setup.get("initial").ref
                    )
                )
                assert lab.config.registry().entries == ()
            yield _Daemon(endpoint.base_url, selection)
            with LabClient(DaemonClient(endpoint.base_url)) as lab:
                assert lab.config.registry().entries == ()
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
            "reviewed": preview.reviewed,
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


def test_real_http_preview_shares_catalog_and_never_admits_acquisition(
    reference_lab_daemon: _Daemon,
    launch_application: LabApplication,
) -> None:
    experiment = "reference_lab.temperature_diagnostic"
    provider = launch_application.launch_provider
    assert provider is not None
    with (
        launch_application.connect(reference_lab_daemon.url) as lab,
        DaemonClient(reference_lab_daemon.url) as client,
        httpx2.Client(
            base_url=reference_lab_daemon.url, trust_env=False, timeout=30
        ) as http,
    ):
        response = http.get(
            "/api/v1/experiment-launcher",
            headers={
                "X-Scopecat-Workspace": source_workspace_id(reference_lab_daemon.url)
            },
        )
        assert response.is_success, response.text
        catalog = LaunchCatalog.model_validate(response.json())
        expected = provider(
            lab,
            LaunchRequest(
                workspace_id=source_workspace_id(reference_lab_daemon.url),
                action="list",
            ),
        )
        assert isinstance(expected, LaunchCatalog)
        assert catalog.code_revision is not None
        assert {"temperature", "frequency-amplitude"}.isdisjoint(
            item.id for item in catalog.entries
        )
        assert [(item.id, item.title, item.controls) for item in catalog.entries] == [
            (item.id, item.title, item.controls) for item in expected.entries
        ]
        before = client.list_runs()
        setup = lab.setup.get("initial")
        selected_entry = next(item for item in catalog.entries if item.id == experiment)
        request = LaunchRequest(
            workspace_id=source_workspace_id(reference_lab_daemon.url),
            action="preview",
            selection=reference_lab_daemon.selection,
            experiment=experiment,
            version=selected_entry.version,
        )
        response = http.post(
            "/api/v1/experiment-launcher/preview", json=request.model_dump(mode="json")
        )
        assert response.is_success, response.text
        preview = LaunchPreview.model_validate(response.json())
        local_entry = next(item for item in expected.entries if item.id == experiment)
        repeated = provider(
            lab, request.model_copy(update={"version": local_entry.version})
        )
        assert isinstance(repeated, LaunchPreview)
        # Target inspection includes per-compilation timing/cache diagnostics.
        assert preview.code_revision == catalog.code_revision
        assert repeated.code_revision is None  # direct, deliberately unpinned provider
        selected_entry = next(item for item in catalog.entries if item.id == experiment)
        assert preview.definition_hash == sha256_json_hash(
            selected_entry.model_dump(mode="json")
        )
        assert repeated.definition_hash is None  # worker-owned catalog projection
        assert (
            preview.request_hash
            == request.model_copy(update={"reviewed": preview.reviewed}).request_hash
        )
        assert preview.manual_state is not None
        assert preview.manual_state.binding.request_hash == preview.request_hash
        assert preview.procedure_definition is not None
        assert repeated.procedure_definition is not None
        assert preview.procedure_definition.id == repeated.procedure_definition.id
        assert (
            preview.procedure_definition.version
            == repeated.procedure_definition.version
        )
        exclude = {
            "request_hash": True,
            "code_revision": True,
            "manual_state": True,
            "definition_hash": True,
            # Installed author workers fingerprint their own retained declaration.
            "procedure_definition": True,
            "preflight": {"stages": {"__all__": {"inspections"}}},
        }
        assert preview.model_dump(exclude=exclude) == repeated.model_dump(
            exclude=exclude
        )
        assert preview.preflight is not None
        assert len(preview.preflight.stages) == 1
        assert all(stage.selected_points <= 1 for stage in preview.preflight.stages)
        assert all(stage.sampled_points <= 64 for stage in preview.preflight.stages)
        for stage in preview.preflight.stages:
            wall_time = next(cost for cost in stage.costs if cost.metric == "wall_time")
            assert isinstance(wall_time.quantity, UnknownQuantity)
            assert wall_time.scope == "experiment"
            assert len(stage.planned_settings) <= stage.planned_setting_limit == 64
        assert preview.point_count == 1
        assert isinstance(preview.reviewed.config_source, ParameterRunConfigSource)
        choice = reference_lab_daemon.selection.configuration
        assert isinstance(choice, ParameterConfiguration)
        assert preview.reviewed.config_source.parameters == choice.ref
        assert preview.reviewed.config_source.setup == setup.ref
        assert client.list_runs() == before
        assert lab.setup.get("initial") == setup
        assert lab.config.registry().entries == ()


def test_exact_context_survives_other_setup_saves_and_replays_exact_admission(
    reference_lab_daemon: _Daemon,
    launch_application: LabApplication,
) -> None:
    provider = launch_application.launch_provider
    assert provider is not None
    with launch_application.connect(reference_lab_daemon.url) as lab:
        catalog = provider(
            lab,
            LaunchRequest(
                workspace_id=source_workspace_id(reference_lab_daemon.url),
                action="list",
            ),
        )
        assert isinstance(catalog, LaunchCatalog)
        entry = next(
            item
            for item in catalog.entries
            if item.id == "reference_lab.temperature_diagnostic"
        )
        request = LaunchRequest(
            workspace_id=source_workspace_id(reference_lab_daemon.url),
            action="preview",
            selection=reference_lab_daemon.selection,
            experiment=entry.id,
            version=entry.version,
        )
        preview = provider(lab, request)
        assert isinstance(preview, LaunchPreview)
        command = submit_request(request, preview, "launch-retry")
        admitted = provider(lab, command)
        assert isinstance(admitted, LaunchSubmission)
        original_setup = lab.setup.get("initial")
        target = original_setup.setup.domain_target
        assert target is not None
        lab.setup.save(
            lab.setup.definition(
                original_setup.resolution.definition_id
            ).definition.model_copy(
                update={
                    "domain_target": target.model_copy(
                        update={"id": "changed-launch-target"}
                    )
                }
            ),
            name="changed-launch-setup",
        )
        assert provider(lab, command) == admitted
        independent = provider(
            lab, submit_request(request, preview, "new-exact-context-request")
        )
        assert isinstance(independent, LaunchSubmission)
        assert independent.procedure_id != admitted.procedure_id
        independent_handle = lab.procedures.get(independent.procedure_id).resume()
        independent_output = independent_handle.output("experiment")
        assert independent_output.kind == "run"
        assert (
            lab.get_run(independent_output.run_id).snapshot.config_source
            == preview.reviewed.config_source
        )
        changed = request.model_copy(update={"actor": "another-operator"})
        with pytest.raises(ValidationError, match="request changed"):
            submit_request(changed, preview, "launch-retry")
        new_preview = provider(lab, changed)
        assert isinstance(new_preview, LaunchPreview)
        with pytest.raises(DaemonConflictError, match="different intent"):
            provider(lab, submit_request(changed, new_preview, "launch-retry"))
        handle = lab.procedures.get(admitted.procedure_id).resume()
        assert handle.state == "closed"
        output = handle.output("experiment")
        assert output.kind == "run"
        run = lab.get_run(output.run_id)
        assert run.status == "completed"
        assert run.snapshot.config_source == preview.reviewed.config_source
        assert preview.preflight is not None
        assert_retained_shapes(preview.preflight.stages[0], run)
        records = run.measurements().records
        temperature = records[0].observables["temperature"]
        assert isinstance(temperature, MeasurementScalar) and temperature.unit == "K"
        with DaemonClient(reference_lab_daemon.url) as client:
            assert client.measurement_preview(run.id).items == records


def test_http_submission_dispatches_the_same_durable_diagnostic(
    reference_lab_daemon: _Daemon,
    launch_application: LabApplication,
) -> None:
    experiment = "reference_lab.temperature_diagnostic"
    with (
        launch_application.connect(reference_lab_daemon.url) as lab,
        httpx2.Client(
            base_url=reference_lab_daemon.url, trust_env=False, timeout=30
        ) as http,
    ):
        catalog = LaunchCatalog.model_validate(
            http.get(
                "/api/v1/experiment-launcher",
                headers={
                    "X-Scopecat-Workspace": source_workspace_id(
                        reference_lab_daemon.url
                    )
                },
            ).json()
        )
        entry = next(item for item in catalog.entries if item.id == experiment)
        request = LaunchRequest(
            workspace_id=source_workspace_id(reference_lab_daemon.url),
            action="preview",
            selection=reference_lab_daemon.selection,
            experiment=entry.id,
            version=entry.version,
        )
        preview_response = http.post(
            "/api/v1/experiment-launcher/preview",
            json=request.model_dump(mode="json"),
        )
        preview_response.raise_for_status()
        preview = LaunchPreview.model_validate(preview_response.json())
        command = submit_request(request, preview, f"http-dispatched-{experiment}")
        assert preview.procedure_definition is not None
        assert preview.code_revision is not None
        retained = http.post(
            "/api/v1/launch-attempts",
            json={
                "definition": preview.procedure_definition.model_dump(mode="json"),
                "request": command.model_dump(mode="json"),
            },
        )
        retained.raise_for_status()
        sequence = cast("int", retained.json()["sequence"])
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
            handle.state not in {"closed", "attention_required", "waiting_for_input"}
            and time.monotonic() < deadline
        ):
            time.sleep(0.05)
        assert handle.state == "closed"
        assert handle.output("experiment").kind == "run"
        # Successful worker execution reloaded the retained source and matched the
        # exact preview definition before running any procedure step.
        assert handle.snapshot.definition == preview.procedure_definition
        assert handle.snapshot.source is not None
        assert handle.snapshot.source.code_revision == preview.code_revision
        assert handle.snapshot.source.workspace_id == request.workspace_id
        assert handle.snapshot.scientific_binding == preview.reviewed.binding
        recovered = http.get(f"/api/v1/launch-attempts/{sequence}/resolve")
        recovered.raise_for_status()
        assert recovered.json()["procedure_id"] == admitted.procedure_id
        retry = http.post(
            "/api/v1/experiment-launcher/submit",
            json=command.model_dump(mode="json"),
        )
        retry.raise_for_status()
        assert LaunchSubmission.model_validate(retry.json()) == admitted
