from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx2
import pytest
from pydantic import BaseModel
from scopecat_testkit.workflow_fixtures import load_config

from scopecat.automation.wire import ProcedureRunListQuery
from scopecat.config.inventory import InstrumentInventoryRekey
from scopecat.config.registry.records import (
    ConfigActivationOperation,
    ConfigPublishOperation,
    ConfigRegistryActivationRecord,
    ConfigRegistryEntry,
    DirectConfigRegistrySource,
)
from scopecat.control.models import (
    RunExecutionSegment,
    RunExecutionSegmentPage,
    RunPlanSummary,
)
from scopecat.daemon.client import (
    DaemonClient,
    DaemonConflictError,
    DaemonNotFoundError,
)
from scopecat.daemon.points import RunPointPlanView
from scopecat.daemon.views import (
    MeasurementTracePreview,
    MeasurementTracePreviewQuery,
    MeasurementTraceSeries,
    RunAdmissionView,
    RunControlView,
    RunPlanView,
    RunSummary,
    RunSummaryPage,
)
from scopecat.daemon.wire import (
    AnalysisSaveCommand,
    AnalysisSaveReceipt,
    AttentionResolutionCommand,
    AttentionResolutionReceipt,
    ConfigActivationReceipt,
    ConfigEntryActivationCommand,
    ConfigPublishCommand,
    ConfigPublishReceipt,
    DirectConfigRevisionSource,
    ExecutorLease,
    ExecutorStartRequest,
    InstrumentConfiguredDefaultsApplyCommand,
    InstrumentContractCatalogRequest,
    InstrumentDriverProbeCommand,
    InstrumentDriverProbeReceipt,
    InstrumentInventoryMigrationCommand,
    InstrumentInventoryMigrationReceipt,
    InstrumentSessionLeaseReceipt,
    PayloadObjectReceipt,
    RunAdmission,
    RunCancellationReceipt,
    RunInstrumentProvisionCommand,
    RunInstrumentProvisionReceipt,
    RunSubmission,
)
from scopecat.kernel.content_identity import sha256_content_hash_segments
from scopecat.kernel.state import PayloadRef, StateValue
from scopecat.planning.catalog import InstrumentContractCatalog
from scopecat.records.config import (
    InstrumentBindingSpec,
    VirtualInstrumentConnection,
    config_content_hash,
)
from scopecat.records.content import (
    BlobPayloadBody,
    CommandPayload,
    ContentEntry,
    InlinePayloadBody,
    SegmentedInlinePayloadBody,
    command_payload_from_bytes,
)
from scopecat.records.instrument import InstrumentStateSnapshot
from scopecat.records.run import RunSnapshot
from scopecat.records.run_request import RunRequest
from scopecat.sdk.instruments.catalog import DriverCatalog
from scopecat.sdk.instruments.commands import (
    InstrumentConfiguredDefaultsApplyReceipt,
    InstrumentOperationArgument,
    InvokeCommand,
    InvokeReceipt,
)
from scopecat.sdk.instruments.contracts import InstrumentDescription

_NOW = datetime(2026, 7, 23, 9, tzinfo=UTC)
_HASH = f"sha256:{'a' * 64}"
_REQUEST = RunRequest(experiment_id="request-1")


def test_get_query_and_post_body_use_typed_wire_models() -> None:
    requests: list[httpx2.Request] = []
    client = _client(requests)

    runs = client.list_runs(limit=5, before=2, state="queued")
    admission = client.submit_run(_submission())

    assert isinstance(runs, RunSummaryPage)
    assert runs.items[0].snapshot.run_id == admission.run_id
    assert admission.submission_id == "submission-1"

    list_request = requests[0]
    assert list_request.method == "GET"
    assert list_request.url.path == "/api/v1/runs"
    assert dict(list_request.url.params) == {
        "limit": "5",
        "before": "2",
        "state": "queued",
    }
    submit_request = requests[1]
    assert submit_request.method == "POST"
    assert submit_request.url.path == "/api/v1/runs"
    assert RunSubmission.model_validate_json(submit_request.content) == _submission()


def test_procedure_lookup_filters_request_key_before_pagination() -> None:
    requests: list[httpx2.Request] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(200, json={"items": [], "next_cursor": None})

    with DaemonClient(
        "http://daemon.test", transport=httpx2.MockTransport(handle)
    ) as client:
        page = client.list_procedures(
            ProcedureRunListQuery(request_key="original-key", limit=2, cursor=4)
        )
    assert page.items == ()
    assert requests[0].url.path == "/api/v1/procedures"
    assert dict(requests[0].url.params) == {
        "request_key": "original-key",
        "limit": "2",
        "cursor": "4",
    }


def test_trace_preview_posts_a_typed_bounded_query() -> None:
    requests: list[httpx2.Request] = []
    client = _client(requests)
    query = MeasurementTracePreviewQuery(
        recording_group_id="readout",
        observable_id="signal",
        coordinate_id="frequency",
        fixed_axis_indices={"bias": 1},
        max_series=4,
        max_samples=128,
        value_mode="phase",
    )

    preview = client.measurement_trace_preview("run-1", query)

    assert preview.observable_id == "signal"
    [request] = requests
    assert request.method == "POST"
    assert request.url.path == "/api/v1/runs/run-1/measurements/traces/query"
    assert MeasurementTracePreviewQuery.model_validate_json(request.content) == query


def test_executor_start_rejects_receipt_for_another_run() -> None:
    other_lease = _lease().model_copy(update={"run_id": "run-2"})
    client = DaemonClient(
        "http://daemon.local/",
        transport=httpx2.MockTransport(lambda _request: _model(other_lease)),
    )

    with pytest.raises(ValueError, match="does not match"):
        client.start_executor(
            "run-1",
            ExecutorStartRequest(
                executor_id="notebook-1",
            ),
        )


def test_execution_segments_use_bounded_run_subresource() -> None:
    requests: list[httpx2.Request] = []
    page = RunExecutionSegmentPage(
        items=(
            RunExecutionSegment(
                sequence=2,
                segment_id="segment-2",
                run_id="run-1",
                ordinal=1,
                executor_id="notebook-2",
                run_contract_fingerprint="a" * 64,
                started_at=_NOW,
                start_point_count=4,
            ),
        ),
        next_cursor=1,
    )

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return _model(page)

    client = DaemonClient(
        "http://daemon.local/",
        transport=httpx2.MockTransport(handler),
    )

    assert client.get_run_execution_segments("run-1", limit=1, before=3) == page
    [request] = requests
    assert request.method == "GET"
    assert request.url.path == "/api/v1/runs/run-1/execution-segments"
    assert dict(request.url.params) == {"limit": "1", "before": "3"}


def test_cancel_run_posts_to_the_idempotent_operator_endpoint() -> None:
    requests: list[httpx2.Request] = []
    receipt = RunCancellationReceipt(
        run_id="run-1",
        status="cancel_requested",
        cancellation_requested_at=_NOW,
    )

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return _model(receipt)

    client = DaemonClient(
        "http://daemon.local/",
        transport=httpx2.MockTransport(handler),
    )

    assert client.cancel_run("run-1") == receipt
    [request] = requests
    assert request.method == "POST"
    assert request.url.path == "/api/v1/runs/run-1/cancel"


def test_resolve_attention_posts_an_explicit_disposition() -> None:
    requests: list[httpx2.Request] = []
    command = AttentionResolutionCommand.continue_run(
        run_contract_fingerprint="a" * 64,
    )
    receipt = AttentionResolutionReceipt(
        run_id="run-1",
        disposition="continue",
        state="queued",
        released_resource_count=1,
    )

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return _model(receipt)

    client = DaemonClient(
        "http://daemon.local/",
        transport=httpx2.MockTransport(handler),
    )

    assert client.resolve_attention("run-1", command) == receipt
    [request] = requests
    assert request.method == "POST"
    assert request.url.path == "/api/v1/runs/run-1/attention"
    assert AttentionResolutionCommand.model_validate_json(request.content) == command


def test_resolve_instrument_contracts_posts_the_exact_config_snapshot() -> None:
    requests: list[httpx2.Request] = []
    config = load_config()
    catalog = InstrumentContractCatalog(
        config_content_hash=config_content_hash(config),
        provider_id=None,
    )

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return _model(catalog)

    client = DaemonClient(
        "http://daemon.local/",
        transport=httpx2.MockTransport(handler),
    )

    assert client.resolve_instrument_contracts(config) == catalog
    [request] = requests
    assert request.method == "POST"
    assert request.url.path == "/api/v1/instrument-contracts/resolve"
    assert InstrumentContractCatalogRequest.model_validate_json(request.content) == (
        InstrumentContractCatalogRequest(config=config)
    )


def test_driver_catalog_reads_project_backend_metadata() -> None:
    requests: list[httpx2.Request] = []
    catalog = DriverCatalog(provider_id="tests.provider")

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return _model(catalog)

    client = DaemonClient(
        "http://daemon.local/",
        transport=httpx2.MockTransport(handler),
    )

    assert client.driver_catalog() == catalog
    [request] = requests
    assert request.method == "GET"
    assert request.url.path == "/api/v1/instrument-drivers"


def test_driver_probe_posts_the_candidate_binding() -> None:
    requests: list[httpx2.Request] = []
    command = InstrumentDriverProbeCommand(
        binding=InstrumentBindingSpec(
            id="source-0",
            driver_id="tests.signal",
            connection=VirtualInstrumentConnection(),
        )
    )
    receipt = InstrumentDriverProbeReceipt(
        status="connected",
        description=InstrumentDescription(
            instrument_id=command.binding.id,
            implementation_id=command.binding.driver_id,
            implementation_version="v1",
        ),
    )

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return _model(receipt)

    client = DaemonClient(
        "http://daemon.local/",
        transport=httpx2.MockTransport(handler),
    )

    assert client.probe_driver(command) == receipt
    [request] = requests
    assert request.method == "POST"
    assert request.url.path == "/api/v1/instrument-drivers/probe"
    assert InstrumentDriverProbeCommand.model_validate_json(request.content) == command


def test_run_instrument_provision_retries_the_same_operation_after_response_loss() -> (
    None
):
    requests: list[httpx2.Request] = []
    command = RunInstrumentProvisionCommand(
        lease_id="lease-1",
        operation_id="lifecycle.provide-instruments",
    )
    receipt = RunInstrumentProvisionReceipt(
        run_id="run-1",
        operation_id=command.operation_id,
        status="ready",
    )

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        if len(requests) == 1:
            raise httpx2.ReadError("response was lost", request=request)
        return _model(receipt)

    client = DaemonClient(
        "http://daemon.local/",
        transport=httpx2.MockTransport(handler),
    )

    assert client.provision_run_instruments("run-1", command) == receipt
    assert [request.url.path for request in requests] == [
        "/api/v1/runs/run-1/instruments/provision",
        "/api/v1/runs/run-1/instruments/provision",
    ]
    assert [
        RunInstrumentProvisionCommand.model_validate_json(request.content)
        for request in requests
    ] == [command, command]


@pytest.mark.parametrize("scope", ["run", "project"])
def test_analysis_save_retries_the_exact_command_after_response_loss(
    scope: str,
) -> None:
    requests: list[httpx2.Request] = []
    command = AnalysisSaveCommand(
        title="fit",
        analysis_key="fit",
        step_id="tests.fit",
    )
    receipt = AnalysisSaveReceipt(
        record=ContentEntry(
            role="record",
            id="analysis-fit-r1",
            kind="analysis",
            content_hash=_HASH,
        ),
        analysis_key=command.analysis_key,
    )

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        if len(requests) == 1:
            raise httpx2.ReadError("analysis response was lost", request=request)
        return _model(receipt, status_code=201)

    client = DaemonClient(
        "http://daemon.local/",
        transport=httpx2.MockTransport(handler),
    )

    saved = (
        client.save_analysis("run-1", command)
        if scope == "run"
        else client.save_project_analysis(command)
    )

    assert saved == receipt
    expected_path = (
        "/api/v1/runs/run-1/analyses" if scope == "run" else "/api/v1/analyses"
    )
    assert [request.url.path for request in requests] == [
        expected_path,
        expected_path,
    ]
    assert [
        AnalysisSaveCommand.model_validate_json(request.content) for request in requests
    ] == [command, command]


def test_apply_configured_defaults_posts_the_typed_command_to_the_instrument() -> None:
    requests: list[httpx2.Request] = []
    command = InstrumentConfiguredDefaultsApplyCommand(operation_id="defaults.apply-1")
    receipt = InstrumentConfiguredDefaultsApplyReceipt(
        session_id="session-1",
        operation_id=command.operation_id,
        instrument_id="source-0",
        config_entry_id="baseline",
        status="applied",
        state=InstrumentStateSnapshot(instrument_id="source-0"),
    )

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return _model(receipt)

    client = DaemonClient(
        "http://daemon.local/",
        transport=httpx2.MockTransport(handler),
    )

    assert (
        client.apply_instrument_configured_defaults(
            "session-1",
            "source-0",
            command,
        )
        == receipt
    )
    [request] = requests
    assert request.method == "POST"
    assert request.url.path == (
        "/api/v1/instrument-sessions/session-1/"
        "instruments/source-0/configured-defaults/apply"
    )
    assert (
        InstrumentConfiguredDefaultsApplyCommand.model_validate_json(request.content)
        == command
    )


def test_renew_instrument_session_posts_an_empty_heartbeat() -> None:
    requests: list[httpx2.Request] = []
    receipt = InstrumentSessionLeaseReceipt(
        session_id="session-1",
        renewed_at=_NOW,
        expires_at=_NOW + timedelta(minutes=1),
    )

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return _model(receipt)

    client = DaemonClient(
        "http://daemon.local/",
        transport=httpx2.MockTransport(handler),
    )

    assert client.renew_instrument_session("session-1") == receipt
    [request] = requests
    assert request.method == "POST"
    assert request.url.path == "/api/v1/instrument-sessions/session-1/heartbeat"
    assert request.content == b""


def test_migrate_instrument_inventory_posts_the_typed_command() -> None:
    requests: list[httpx2.Request] = []
    config = load_config()
    command = InstrumentInventoryMigrationCommand(
        config=config,
        entry_id="inventory-v2",
        changes=(
            InstrumentInventoryRekey(
                instrument_id="source-0",
                from_exclusivity_key="source-0",
                to_exclusivity_key="rack-a/source",
            ),
        ),
        actor="operator",
        expected_generation=1,
        note="move source",
    )
    entry = ConfigRegistryEntry(
        id=command.entry_id,
        config_ref="config-registry/entries/inventory-v2/config.json",
        content_hash=config_content_hash(config),
        source=DirectConfigRegistrySource(),
        actor=command.actor,
    )
    receipt = InstrumentInventoryMigrationReceipt(
        entry=entry,
        activation=ConfigRegistryActivationRecord(
            generation=2,
            action="inventory_migration",
            entry_id=entry.id,
            entry_content_hash=entry.content_hash,
            actor=command.actor,
        ),
        changes=command.changes,
    )

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return _model(receipt)

    client = DaemonClient(
        "http://daemon.local/",
        transport=httpx2.MockTransport(handler),
    )

    assert client.migrate_instrument_inventory(command) == receipt
    [request] = requests
    assert request.method == "POST"
    assert request.url.path == "/api/v1/config-registry/instrument-inventory-migrations"
    assert (
        InstrumentInventoryMigrationCommand.model_validate_json(request.content)
        == command
    )


def test_config_activation_retries_exact_command_and_supports_lookup() -> None:
    config = load_config()
    entry = ConfigRegistryEntry(
        id="baseline",
        config_ref="config-registry/entries/baseline/config.json",
        content_hash=config_content_hash(config),
        source=DirectConfigRegistrySource(),
        actor="notebook",
    )
    command = ConfigEntryActivationCommand(
        operation_id="activate-baseline",
        entry_id=entry.id,
        actor="operator",
        expected_generation=0,
    )
    activation = ConfigRegistryActivationRecord(
        generation=1,
        action="activation",
        entry_id=entry.id,
        entry_content_hash=entry.content_hash,
        actor=command.actor,
    )
    receipt = ConfigActivationReceipt(
        operation=ConfigActivationOperation(
            operation_id=command.operation_id,
            intent_hash=command.intent_hash,
            entry_id=command.entry_id,
            expected_generation=command.expected_generation,
            actor=command.actor,
            activation_generation=activation.generation,
        ),
        activation=activation,
    )
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        if request.method == "POST" and len(requests) == 1:
            raise httpx2.ReadError("activation response was lost", request=request)
        return _model(receipt)

    client = DaemonClient(
        "http://daemon.local/",
        transport=httpx2.MockTransport(handler),
    )

    assert client.activate_config_entry(command) == receipt
    assert client.config_activation_operation(command.operation_id) == receipt
    assert [request.method for request in requests] == ["POST", "POST", "GET"]
    assert requests[0].content == requests[1].content
    assert requests[0].url.path == ("/api/v1/config-registry/activation-operations")
    assert requests[2].url.path == (
        "/api/v1/config-registry/activation-operations/activate-baseline"
    )


def test_config_publish_retries_exact_command_and_supports_lookup() -> None:
    config = load_config()
    command = ConfigPublishCommand(
        operation_id="publish-baseline",
        source=DirectConfigRevisionSource(config=config),
        entry_id="baseline",
        actor="operator",
        expected_generation=0,
    )
    entry = ConfigRegistryEntry(
        id=command.entry_id,
        config_ref="config-registry/entries/baseline/config.json",
        content_hash=config_content_hash(config),
        source=DirectConfigRegistrySource(),
        actor=command.actor,
    )
    activation = ConfigRegistryActivationRecord(
        generation=1,
        action="activation",
        entry_id=entry.id,
        entry_content_hash=entry.content_hash,
        actor=command.actor,
    )
    receipt = ConfigPublishReceipt(
        operation=ConfigPublishOperation(
            operation_id=command.operation_id,
            intent_hash=command.intent_hash,
            source_intent_hash=command.source_intent_hash,
            entry_id=command.entry_id,
            expected_generation=command.expected_generation,
            actor=command.actor,
            activation_generation=activation.generation,
        ),
        entry=entry,
        activation=activation,
    )
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        if request.method == "POST" and len(requests) == 1:
            raise httpx2.ReadError("publish response was lost", request=request)
        return _model(receipt)

    client = DaemonClient(
        "http://daemon.local/",
        transport=httpx2.MockTransport(handler),
    )

    assert client.publish_config(command) == receipt
    assert client.config_publish_operation(command.operation_id) == receipt
    assert [request.method for request in requests] == ["POST", "POST", "GET"]
    assert requests[0].content == requests[1].content
    assert requests[0].url.path == "/api/v1/config-registry/publish-operations"
    assert requests[2].url.path == (
        "/api/v1/config-registry/publish-operations/publish-baseline"
    )


def test_invoke_externalizes_inline_payload_before_command_post() -> None:
    requests: list[httpx2.Request] = []
    content = b"opaque-program"
    payload = command_payload_from_bytes(
        id="program-1",
        schema_id="pulse_program",
        codec_id="tests.raw",
        codec_version=1,
        media_type="application/octet-stream",
        content=content,
    )
    command = InvokeCommand(
        command_id="invoke-payload-1",
        instrument_id="source-0",
        resource_id="source-0",
        interface_id="test.play_program/v1",
        operation_id="play",
        arguments=[
            InstrumentOperationArgument(
                id="program",
                value=StateValue(PayloadRef(payload_id=payload.id)),
            )
        ],
        payloads={payload.id: payload},
    )

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        if request.method == "PUT":
            return _model(
                PayloadObjectReceipt(
                    ref=payload.content_hash,
                    content_hash=payload.content_hash,
                    size_bytes=len(content),
                ),
                status_code=201,
            )
        return _model(InvokeReceipt(status="invoked"))

    client = DaemonClient(
        "http://daemon.local/",
        transport=httpx2.MockTransport(handler),
    )

    assert (
        client.invoke_instrument("session-1", "source-0", command).status == "invoked"
    )
    assert [request.method for request in requests] == ["PUT", "POST"]
    assert requests[0].url.path == (
        "/api/v1/instrument-sessions/session-1/payload-objects/"
        f"{payload.content_hash.removeprefix('sha256:')}"
    )
    assert requests[0].content == content
    assert requests[0].headers["x-scopecat-payload-command-id"] == command.command_id
    assert requests[1].url.path.endswith("/instruments/source-0/invoke")
    posted = InvokeCommand.model_validate_json(requests[1].content)
    posted_payload = posted.payloads[payload.id]
    assert isinstance(payload.body, InlinePayloadBody)
    assert isinstance(posted_payload.body, BlobPayloadBody)
    assert posted_payload.body.ref == payload.content_hash


def test_segmented_payload_upload_streams_exact_content() -> None:
    requests: list[httpx2.Request] = []
    segments = (b"bundle-prefix", memoryview(b"waveform-samples"))
    content_hash = sha256_content_hash_segments(segments)
    payload = CommandPayload(
        id="segmented-program",
        schema_id="pulse_program",
        codec_id="tests.segmented",
        codec_version=1,
        media_type="application/octet-stream",
        content_format="attachment_bundle",
        content_hash=content_hash,
        size_bytes=sum(memoryview(segment).nbytes for segment in segments),
        body=SegmentedInlinePayloadBody.from_segments(segments),
    )
    command = InvokeCommand(
        command_id="segmented-invoke",
        instrument_id="source-0",
        resource_id="source-0",
        interface_id="test.play_program/v1",
        operation_id="play",
        arguments=[
            InstrumentOperationArgument(
                id="program",
                value=StateValue(PayloadRef(payload_id=payload.id)),
            )
        ],
        payloads={payload.id: payload},
    )

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        if request.method == "PUT":
            return _model(
                PayloadObjectReceipt(
                    ref=content_hash,
                    content_hash=content_hash,
                    size_bytes=payload.size_bytes,
                ),
                status_code=201,
            )
        return _model(InvokeReceipt(status="invoked"))

    client = DaemonClient(
        "http://daemon.local/",
        transport=httpx2.MockTransport(handler),
    )

    assert client.invoke_instrument("session-1", "source-0", command).status == (
        "invoked"
    )
    assert [request.method for request in requests] == ["PUT", "POST"]
    assert requests[0].content == b"".join(segments)
    assert int(requests[0].headers["content-length"]) == payload.size_bytes


def test_payload_object_put_retries_once_after_transport_failure() -> None:
    requests: list[httpx2.Request] = []
    content = b"retry-payload"
    payload = command_payload_from_bytes(
        id="retry-program",
        schema_id="pulse_program",
        codec_id="tests.raw",
        codec_version=1,
        media_type="application/octet-stream",
        content=content,
    )
    command = InvokeCommand(
        command_id="retry-invoke",
        instrument_id="source-0",
        resource_id="source-0",
        interface_id="test.play_program/v1",
        operation_id="play",
        arguments=[
            InstrumentOperationArgument(
                id="program",
                value=StateValue(PayloadRef(payload_id=payload.id)),
            )
        ],
        payloads={payload.id: payload},
    )

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        if len(requests) == 1:
            raise httpx2.ReadError("response was lost", request=request)
        if request.method == "PUT":
            return _model(
                PayloadObjectReceipt(
                    ref=payload.content_hash,
                    content_hash=payload.content_hash,
                    size_bytes=len(content),
                ),
                status_code=201,
            )
        return _model(InvokeReceipt(status="invoked"))

    client = DaemonClient(
        "http://daemon.local/",
        transport=httpx2.MockTransport(handler),
    )

    assert (
        client.invoke_instrument("session-1", "source-0", command).status == "invoked"
    )
    assert [request.method for request in requests] == ["PUT", "PUT", "POST"]
    assert requests[0].url == requests[1].url
    assert requests[0].content == requests[1].content == content


def test_not_found_and_conflict_are_typed_and_other_http_errors_raise() -> None:
    client = _client([])

    with pytest.raises(DaemonNotFoundError) as missing:
        client.get_run("missing")
    with pytest.raises(DaemonConflictError) as conflict:
        client.submit_run(_submission("duplicate"))
    with pytest.raises(httpx2.HTTPStatusError):
        client.get_run("invalid")

    assert missing.value.detail == "run was not found: missing"
    assert missing.value.response.status_code == 404
    assert conflict.value.detail == "submission already exists"
    assert conflict.value.response.status_code == 409


def _client(requests: list[httpx2.Request]) -> DaemonClient:
    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return _client_response(request)

    return DaemonClient(
        "http://daemon.local/",
        transport=httpx2.MockTransport(handler),
    )


def _client_response(request: httpx2.Request) -> httpx2.Response:
    path = request.url.path
    if path == "/api/v1/runs" and request.method == "GET":
        return _model(
            RunSummaryPage(
                items=(
                    RunSummary(
                        control=_control_run(),
                        snapshot=_accepted_manifest(),
                    ),
                )
            )
        )
    if path == "/api/v1/runs/missing":
        return _json({"detail": "run was not found: missing"}, status_code=404)
    if path == "/api/v1/runs/invalid":
        return _json({"detail": "invalid request"}, status_code=422)
    if path == "/api/v1/runs" and request.method == "POST":
        if b'"submission_id":"duplicate"' in request.content:
            return _json(
                {"detail": "submission already exists"},
                status_code=409,
            )
        submission = RunSubmission.model_validate_json(request.content)
        return _model(_admission(submission.submission_id), status_code=201)
    if path == "/api/v1/runs/run-1/measurements/traces/query":
        return _model(_trace_preview())
    raise AssertionError(f"unexpected request: {request.method} {path}")


def _model(model: BaseModel, *, status_code: int = 200) -> httpx2.Response:
    return _json(model.model_dump(mode="json"), status_code=status_code)


def _json(content: object, *, status_code: int = 200) -> httpx2.Response:
    return httpx2.Response(status_code, json=content)


def _control_run() -> RunControlView:
    return RunControlView(
        sequence=1,
        admission=RunAdmissionView(
            run_id="run-1",
            run_contract_fingerprint="a" * 64,
            plan=RunPlanView(
                experiment_id="scratch",
                experiment_kind="scratch",
                point_plan_fingerprint="a" * 64,
                measurement_contract_fingerprint="b" * 64,
                point_count=1,
                initial_point_count=1,
                point_limit=1,
            ),
            admitted_at=_NOW,
        ),
        state="queued",
        updated_at=_NOW,
        completed_point_count=0,
        point_plan=RunPointPlanView(
            run_id="run-1",
            initial_point_count=1,
            accepted_point_count=1,
            point_limit=1,
            decision_count=0,
            optimizer_attempt_count=0,
            operator_request_count=0,
            plan_closed=True,
            stop_reason="static point plan",
        ),
    )


def _admission(submission_id: str) -> RunAdmission:
    return RunAdmission(
        submission_id=submission_id,
        snapshot=_accepted_manifest(),
    )


def _submission(submission_id: str = "submission-1") -> RunSubmission:
    return RunSubmission(
        submission_id=submission_id,
        config=load_config(),
        request=_REQUEST,
        plan=RunPlanSummary(
            experiment_id="scratch",
            experiment_kind="scratch",
            point_plan_fingerprint="a" * 64,
            measurement_contract_fingerprint="b" * 64,
            point_count=1,
            initial_point_count=1,
            point_limit=1,
        ),
    )


def _lease() -> ExecutorLease:
    return ExecutorLease(
        lease_id="lease-1",
        segment_id="segment-1",
        run_id="run-1",
        executor_id="notebook-1",
        issued_at=_NOW,
        expires_at=_NOW + timedelta(seconds=30),
        heartbeat_interval_seconds=10,
    )


def _accepted_manifest() -> RunSnapshot:
    return RunSnapshot(
        run_id="run-1",
        created_at=_NOW,
        config_content_hash=_HASH,
    )


def _trace_preview() -> MeasurementTracePreview:
    series = MeasurementTraceSeries(
        point_index=0,
        label="Point 0",
        x=(0.0, 1.0),
        y=(0.0, 0.5),
        source_sample_count=2,
        available_sample_count=2,
    )
    return MeasurementTracePreview(
        dimension_id="sample",
        recording_group_id="readout",
        coordinate_id="frequency",
        observable_id="signal",
        value_mode="phase",
        value_unit="rad",
        downsampling="minmax",
        layout="overlay",
        series=(series,),
        selected_series_count=1,
        inspected_series_count=1,
        returned_series_count=1,
        source_sample_count=2,
        returned_sample_count=2,
    )
