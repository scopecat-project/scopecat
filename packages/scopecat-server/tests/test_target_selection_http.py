"""Registered-target selection crosses real catalog/config/admission HTTP boundaries."""

from pathlib import Path

import httpx2
import pytest
from fastapi.testclient import TestClient
from scopecat.api.lab import LabClient
from scopecat.application.launch_config import resolve_launch_config
from scopecat.control.models import RunPlanSummary
from scopecat.daemon.client import DaemonClient, DaemonConflictError
from scopecat.daemon.wire import (
    ConfigContextSaveCommand,
    RunSubmission,
    SampleCreateCommand,
    SampleReviseCommand,
)
from scopecat.records.config_context import ConfigContextRef
from scopecat.records.launch_request import LaunchRequest
from scopecat.records.run_request import RunRequest
from scopecat.records.sample import SampleRevisionDraft, SampleSelector
from scopecat.records.scientific_binding import RegisteredTargetSubject
from scopecat.records.scientific_scope import (
    DeclaredBatch,
    MeasurementTarget,
    TargetMember,
    UnscopedBatch,
)
from scopecat.records.scientific_selection import (
    RegisteredTargetChoice,
    ScientificSelection,
    WorkingPointConfiguration,
)
from scopecat.records.target_catalog import (
    TargetCreateCommand,
    TargetReviseCommand,
    TargetRevisionDraft,
)
from scopecat_testkit.config_registry import load_config

from scopecat_server import LocalDaemonRuntime


def _client(transport: TestClient) -> DaemonClient:
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

    return DaemonClient("http://testserver", transport=httpx2.MockTransport(send))


def _selection(client: DaemonClient) -> ScientificSelection:
    active = client.active_config()
    sample = client.create_sample(
        SampleCreateCommand(
            operation_id="create:chip",
            sample_id="chip",
            kind="chip",
            actor="operator",
            content=SampleRevisionDraft(
                display_name="Chip", topology=active.config.topology
            ),
        )
    ).revision
    target = client.create_target(
        TargetCreateCommand(
            catalog_id=client.health().project_id,
            target_id="target",
            draft=TargetRevisionDraft(
                name="Target",
                actor="operator",
                content=MeasurementTarget(
                    members=(
                        TargetMember(
                            id="A",
                            sample_id=sample.sample_id,
                            revision=sample.revision,
                            content_hash=sample.content_hash,
                        ),
                    )
                ),
            ),
        )
    )
    batch = client.create_experimental_batch("Cooldown")
    context = client.save_context(
        ConfigContextSaveCommand(
            entry_id="parked-v1",
            base=ConfigContextRef(
                entry_id=active.entry.id, content_hash=active.entry.content_hash
            ),
            sample=SampleSelector(
                sample_id=sample.sample_id, revision=sample.revision, batch_id=batch.id
            ),
            working_point_id="parked",
            label="Parked",
            actor="operator",
        )
    )
    return ScientificSelection(
        subject=RegisteredTargetChoice(ref=target.ref),
        configuration=WorkingPointConfiguration(
            ref=ConfigContextRef(
                entry_id=context.entry.id, content_hash=context.entry.content_hash
            )
        ),
        batch=DeclaredBatch(id=batch.id),
    )


def test_target_working_point_preview_and_http_submit_keep_exact_heads(
    tmp_path: Path,
) -> None:
    with (
        LocalDaemonRuntime(tmp_path, bootstrap_config=load_config()) as runtime,
        TestClient(runtime.app()) as transport,
    ):
        client = _client(transport)
        lab = LabClient(client)
        selection = _selection(client)
        request = LaunchRequest(
            action="preview",
            experiment="target-check",
            version="1",
            selection=selection,
        )
        checked = resolve_launch_config(lab, request)
        subject = checked.reviewed.binding.subject
        assert isinstance(subject, RegisteredTargetSubject)
        assert subject.content.members[0].id == "A"
        sample = checked.reviewed.binding.samples[0]
        assert sample.context_id == "parked"
        assert isinstance(selection.batch, DeclaredBatch)
        assert sample.batch_id == selection.batch.id
        target = client.resolve_target(subject.ref)
        revised = client.revise_sample(
            "chip",
            SampleReviseCommand(
                operation_id="revise:chip",
                expected_revision=1,
                actor="operator",
                content=SampleRevisionDraft(
                    display_name="New chip description", topology=load_config().topology
                ),
            ),
        )
        advanced = client.revise_target(
            TargetReviseCommand(
                expected=target.ref,
                draft=TargetRevisionDraft(
                    name="Next target",
                    content=MeasurementTarget(
                        members=(
                            target.content.members[0].model_copy(
                                update={
                                    "revision": revised.revision.revision,
                                    "content_hash": revised.revision.content_hash,
                                }
                            ),
                        )
                    ),
                    actor="operator",
                ),
            )
        )
        assert advanced.ref.revision == 2
        assert advanced.ref.content_hash != target.ref.content_hash
        assert isinstance(selection.configuration, WorkingPointConfiguration)
        client.save_context(
            ConfigContextSaveCommand(
                entry_id="parked-v2",
                base=selection.configuration.ref,
                sample=SampleSelector(
                    sample_id="chip", revision=1, batch_id=sample.batch_id
                ),
                working_point_id="parked",
                label="Parked next",
                actor="operator",
                advance=True,
            )
        )
        # Refreshing source or preview cannot reinterpret these exact references.
        assert resolve_launch_config(lab, request) == checked
        reviewed_request = request.model_copy(update={"reviewed": checked.reviewed})
        submit = LaunchRequest.model_validate(
            {
                **reviewed_request.model_dump(),
                "action": "submit",
                "request_key": "exact-target",
                "expected_request_hash": reviewed_request.request_hash,
            }
        )
        assert resolve_launch_config(lab, submit) == checked
        submission = RunSubmission(
            submission_id="exact-target",
            config=checked.config,
            config_source=checked.reviewed.config_source,
            scientific_binding=checked.reviewed.binding,
            request=RunRequest(
                experiment_id="target-check",
                samples=checked.reviewed.binding.sample_selectors(),
            ),
            plan=RunPlanSummary(
                experiment_id="target-check",
                experiment_kind="target-check",
                point_plan_fingerprint="a" * 64,
                measurement_contract_fingerprint="b" * 64,
                point_count=1,
                initial_point_count=1,
                point_limit=1,
            ),
        )
        admitted = client.submit_run(submission)
        assert admitted.snapshot.scientific_binding == checked.reviewed.binding
        assert admitted.snapshot.scientific_binding.subject == subject
        assert admitted.snapshot.config_source == checked.reviewed.config_source
        assert client.submit_run(submission).run_id == admitted.run_id


def test_target_http_selection_rejects_foreign_assembly_and_wrong_batch(
    tmp_path: Path,
) -> None:
    with (
        LocalDaemonRuntime(tmp_path, bootstrap_config=load_config()) as runtime,
        TestClient(runtime.app()) as transport,
    ):
        client = _client(transport)
        lab = LabClient(client)
        selection = _selection(client)
        assert isinstance(selection.subject, RegisteredTargetChoice)
        request = LaunchRequest(
            action="preview",
            experiment="target-check",
            version="1",
            selection=selection,
        )
        foreign = selection.subject.ref.model_copy(
            update={"catalog_id": "local:elsewhere"}
        )
        with pytest.raises(DaemonConflictError, match="another catalog"):
            resolve_launch_config(
                lab,
                request.model_copy(
                    update={
                        "selection": selection.model_copy(
                            update={"subject": RegisteredTargetChoice(ref=foreign)}
                        )
                    }
                ),
            )
        target = client.resolve_target(selection.subject.ref)
        member = target.content.members[0]
        assembly = client.revise_target(
            TargetReviseCommand(
                expected=target.ref,
                draft=TargetRevisionDraft(
                    name="Assembly",
                    actor="operator",
                    content=MeasurementTarget(
                        members=(member, member.model_copy(update={"id": "B"}))
                    ),
                ),
            )
        )
        with pytest.raises(ValueError, match="one target member and no connections"):
            resolve_launch_config(
                lab,
                request.model_copy(
                    update={
                        "selection": selection.model_copy(
                            update={"subject": RegisteredTargetChoice(ref=assembly.ref)}
                        )
                    }
                ),
            )
        with pytest.raises(ValueError, match="subject/batch"):
            resolve_launch_config(
                lab,
                request.model_copy(
                    update={
                        "selection": selection.model_copy(
                            update={"batch": UnscopedBatch()}
                        )
                    }
                ),
            )
        assert client.list_runs().items == ()
