"""Parameter authors can save before any device or measurement context exists."""

from pathlib import Path

import httpx2
from fastapi.testclient import TestClient
from scopecat.api.lab import LabClient
from scopecat.application.launch_config import resolve_launch_config
from scopecat.config.registry.records import BoundParameterRegistrySource
from scopecat.daemon.client import DaemonClient
from scopecat.daemon.views import ConfigEntryView
from scopecat.daemon.wire import (
    ParameterBindCommand,
    ParameterSaveCommand,
    SampleCreateCommand,
    SetupSaveCommand,
)
from scopecat.records.config_context import ConfigContextRef
from scopecat.records.experimental_batch import ExperimentalBatchEdit
from scopecat.records.launch_request import LaunchRequest
from scopecat.records.parameter import ParameterSnapshot, ScalarParameterValue
from scopecat.records.parameter_revision import ParameterRevision
from scopecat.records.plan_ref import PlanConfigRef
from scopecat.records.sample import SampleRevisionDraft, SampleSelector
from scopecat.records.scientific_scope import DeclaredBatch
from scopecat.records.scientific_selection import (
    SampleSubjectChoice,
    SavedConfiguration,
    ScientificSelection,
    WorkingPointConfiguration,
)
from scopecat.records.setup import ExecutableSetupSnapshot
from scopecat_testkit.workflow_fixtures import load_config

from scopecat_server.runtime import LocalDaemonRuntime


def test_independent_parameters_bind_without_selecting_and_reopen(
    tmp_path: Path,
) -> None:
    config = load_config()
    command = ParameterSaveCommand(
        revision_id="author/initial",
        catalog=config.parameter_catalog,
        parameters=config.parameter_snapshot,
        actor="author",
    )
    for first in (True, False):
        with (
            LocalDaemonRuntime(tmp_path) as runtime,
            TestClient(runtime.app()) as client,
        ):
            response = client.post(
                "/api/v1/parameters/revisions", json=command.model_dump(mode="json")
            )
            assert response.status_code == 200
            revision = ParameterRevision.model_validate(response.json())
            assert (
                client.get("/api/v1/parameters/revisions/author%2Finitial").json()
                == response.json()
            )
            assert client.get("/api/v1/parameters/revisions").json()["items"] == [
                response.json()
            ]
            assert client.get("/api/v1/setup/active").status_code == 404
            assert runtime.application.config.get_config_registry().activation is None
            if first:
                assert not runtime.application.setup.list()
                assert not runtime.application.config.get_config_registry().entries
                changed = command.model_copy(update={"note": "changed intent"})
                assert (
                    client.post(
                        "/api/v1/parameters/revisions",
                        json=changed.model_dump(mode="json"),
                    ).status_code
                    == 409
                )
                setup = runtime.application.setup.save(
                    SetupSaveCommand(
                        revision_id="bench",
                        setup=ExecutableSetupSnapshot.from_config(config),
                        actor="maintainer",
                    )
                )
                binding = ParameterBindCommand(
                    parameters=revision.ref,
                    setup=setup.ref,
                    entry_id="prepared-inputs",
                    system_id=config.system.id,
                    actor="author",
                )
                stale = binding.model_copy(
                    update={
                        "parameters": revision.ref.model_copy(
                            update={"content_hash": "sha256:" + "0" * 64}
                        ),
                    }
                )
                assert (
                    client.post(
                        "/api/v1/parameters/bindings",
                        json=stale.model_dump(mode="json"),
                    ).status_code
                    == 409
                )
                assert not runtime.application.config.get_config_registry().entries
                response = client.post(
                    "/api/v1/parameters/bindings", json=binding.model_dump(mode="json")
                )
                assert response.status_code == 200
                assert (
                    client.post(
                        "/api/v1/parameters/bindings",
                        json=binding.model_dump(mode="json"),
                    ).json()
                    == response.json()
                )
                prepared = ConfigEntryView.model_validate(response.json())
                assert prepared.config.parameter_snapshot == revision.parameters
                assert prepared.entry.source == BoundParameterRegistrySource(
                    parameters=revision.ref, setup=setup.ref
                )
            else:
                saved = runtime.application.config.get_config_entry("prepared-inputs")
                assert isinstance(saved.entry.source, BoundParameterRegistrySource)
                assert saved.entry.source.parameters == revision.ref
                assert saved.config.parameter_snapshot == config.parameter_snapshot


def test_independent_parameter_validation_does_not_need_setup(tmp_path: Path) -> None:
    config = load_config()
    command = ParameterSaveCommand(
        revision_id="invalid",
        catalog=config.parameter_catalog,
        parameters=ParameterSnapshot(
            id="invalid", values=(ScalarParameterValue(id="undeclared", value=1),)
        ),
        actor="author",
    )
    with LocalDaemonRuntime(tmp_path) as runtime, TestClient(runtime.app()) as client:
        assert (
            client.post(
                "/api/v1/parameters/revisions", json=command.model_dump(mode="json")
            ).status_code
            == 409
        )
        assert not runtime.application.config.parameter_revisions()
        assert not runtime.application.setup.list()


def test_prepared_inputs_share_subject_batch_and_working_point_resolution(
    tmp_path: Path,
) -> None:
    config = load_config()
    with (
        LocalDaemonRuntime(tmp_path) as runtime,
        TestClient(runtime.app()) as transport,
    ):

        def send(request: httpx2.Request) -> httpx2.Response:
            response = transport.request(
                request.method,
                request.url.raw_path.decode(),
                content=request.content,
                headers=dict(request.headers),
            )
            return httpx2.Response(
                response.status_code,
                content=response.content,
                headers=dict(response.headers),
            )

        with DaemonClient(
            "http://testserver", transport=httpx2.MockTransport(send)
        ) as client:
            lab = LabClient(client)
            parameters = lab.parameters.save(
                name="initial",
                catalog=config.parameter_catalog,
                parameters=config.parameter_snapshot,
            )
            setup = lab.setup.save(
                ExecutableSetupSnapshot.from_config(config), name="bench"
            )
            prepared = lab.parameters.bind(
                parameters, setup=setup, name="prepared", system_id="lab"
            )
            runtime.application.samples.create(
                SampleCreateCommand(
                    operation_id="sample",
                    sample_id="chip",
                    kind="chip",
                    actor="author",
                    content=SampleRevisionDraft(
                        display_name="Chip", topology=config.system.topology
                    ),
                )
            )
            runtime.application.experimental_batches.save(
                "cooldown", ExperimentalBatchEdit(name="Cooldown")
            )
            selection = ScientificSelection(
                subject=SampleSubjectChoice(sample_id="chip"),
                batch=DeclaredBatch(id="cooldown"),
                configuration=SavedConfiguration(
                    ref=PlanConfigRef(
                        entry_id=prepared.entry.id,
                        content_hash=prepared.entry.content_hash,
                    )
                ),
            )
            request = LaunchRequest(
                action="preview", experiment="test", version="1", selection=selection
            )
            resolved = resolve_launch_config(lab, request)
            assert resolved.config == prepared.config
            sample = resolved.reviewed.binding.samples[0]
            assert (sample.sample_id, sample.batch_id) == ("chip", "cooldown")
            point = lab.config.save_context(
                entry_id="parked",
                base=ConfigContextRef(
                    entry_id=prepared.entry.id, content_hash=prepared.entry.content_hash
                ),
                sample=SampleSelector(
                    sample_id="chip", revision=1, batch_id="cooldown"
                ),
                working_point_id="parked",
                label="Parked",
            )
            context_selection = selection.model_copy(
                update={
                    "configuration": WorkingPointConfiguration(
                        ref=ConfigContextRef(
                            entry_id=point.entry.id,
                            content_hash=point.entry.content_hash,
                        ),
                    )
                }
            )
            context = resolve_launch_config(
                lab, request.model_copy(update={"selection": context_selection})
            )
            assert context.reviewed.binding.samples[0].sample_id == sample.sample_id
            assert context.reviewed.binding.samples[0].batch_id == sample.batch_id
            assert lab.config.registry().activation is None
            assert lab.parameters.get(parameters.id) == parameters
