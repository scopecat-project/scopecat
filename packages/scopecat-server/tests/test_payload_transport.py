from __future__ import annotations

import asyncio
import os
import shutil
import sys
from collections.abc import AsyncIterator, Callable
from mmap import mmap
from pathlib import Path
from typing import Literal, override

import httpx2
import pytest
from fastapi.testclient import TestClient
from scopecat.control.models import RunPlanSummary, RunResourceRequirement
from scopecat.daemon.client import DaemonClient, DaemonConflictError
from scopecat.daemon.wire import (
    ExecutorStartRequest,
    InstrumentSessionOpenCommand,
    PayloadObjectReceipt,
    RunHardwareBatchCommand,
    RunInstrumentProvisionCommand,
    RunSubmission,
)
from scopecat.kernel.content_identity import sha256_content_hash
from scopecat.kernel.state import PayloadRef, StateValue
from scopecat.planning.provider_validation import instrument_contract_fingerprint
from scopecat.records.content import (
    BlobPayloadBody,
    CommandPayload,
    command_payload_from_bytes,
)
from scopecat.records.run_request import RunRequest
from scopecat.sdk.attachments import AttachmentBundle
from scopecat.sdk.instruments import (
    DriverCatalog,
    DriverOperation,
    DriverOutcome,
    DriverPayload,
    DriverStateReadback,
    InstrumentBackend,
    InstrumentConnectionContext,
    InstrumentProviderContext,
    InstrumentProviderDescription,
)
from scopecat.sdk.instruments.commands import (
    CollectResultRequest,
    InstrumentOperationArgument,
    InvokeCommand,
)
from scopecat.sdk.instruments.execution import (
    RunHardwareBatch,
    RunHardwareCollect,
    RunHardwareCollectBinding,
    RunHardwareInvoke,
)
from scopecat.sdk.payloads import (
    EncodedPayloadContent,
    PayloadCodecRegistry,
    byte_payload_codec,
    command_payload_from_encoded_content,
)
from scopecat_testkit.instrument_drivers import SignalInstrumentDriver, load_config

from scopecat_server import LocalDaemonRuntime
from scopecat_server.command_payloads import (
    DEFAULT_MAX_PAYLOAD_OBJECT_BYTES,
    CommandPayloadService,
    CommandPayloadTooLarge,
    run_payload_scope,
    session_payload_scope,
)
from scopecat_server.instruments.backend import LocalInstrumentBackendEndpoint
from scopecat_server.instruments.worker import SubprocessInstrumentBackendEndpoint

_PAYLOAD_BYTES = b"\x00\xff\x80SCPI\x00program\n"
_MEDIA_TYPE = "application/octet-stream"
_CODEC_ID = "tests.raw-bytes"
_CODEC_VERSION = 1
_WORKER_BACKEND = "worker_fixture.backend:create_backend"
_WORKER_FIXTURE = Path(__file__).parent / "fixtures" / "instrument_worker_project"
_WORKER_MODULE = "worker_fixture.backend"
_WORKER_PAYLOAD_CODEC_ID = "tests.raw"


class _PayloadConsumerDriver(SignalInstrumentDriver):
    def __init__(self, instrument_id: str) -> None:
        super().__init__(instrument_id=instrument_id)
        self.consumed_payloads: list[bytes] = []

    @override
    def invoke(
        self,
        request: DriverOperation,
    ) -> DriverOutcome[DriverStateReadback | None]:
        for argument in request.arguments.values():
            if isinstance(argument, DriverPayload):
                assert isinstance(argument.value, bytes)
                self.consumed_payloads.append(argument.value)
        return super().invoke(request)


class _PayloadProvider:
    provider_id = "tests.payload_transport_provider"

    def __init__(self) -> None:
        self.drivers: list[_PayloadConsumerDriver] = []

    def describe(
        self,
        context: InstrumentProviderContext,
    ) -> InstrumentProviderDescription:
        return InstrumentProviderDescription(
            provider_id=self.provider_id,
            instruments=tuple(
                _PayloadConsumerDriver(binding.id).describe()
                for binding in context.bindings
            ),
        )

    def connect(
        self,
        context: InstrumentConnectionContext,
    ) -> _PayloadConsumerDriver:
        driver = _PayloadConsumerDriver(context.binding.id)
        self.drivers.append(driver)
        return driver


def test_opaque_payload_crosses_http_and_spawned_worker_boundary(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    shutil.copytree(_WORKER_FIXTURE, project)
    assert _WORKER_MODULE not in sys.modules
    endpoint = SubprocessInstrumentBackendEndpoint(project, _WORKER_BACKEND)

    try:
        with (
            LocalDaemonRuntime(
                project,
                bootstrap_config=load_config(),
                instrument_endpoint=endpoint,
            ) as runtime,
            TestClient(runtime.app()) as transport,
        ):
            daemon = _daemon_client(transport)
            session = daemon.open_instrument_session(
                InstrumentSessionOpenCommand(
                    operation_id="open-spawned-payload-session",
                    actor="payload-test",
                    instrument_ids=("source-0",),
                )
            )
            payload = _payload_with_contract(
                "spawned-program",
                _PAYLOAD_BYTES,
                codec_id=_WORKER_PAYLOAD_CODEC_ID,
            )

            receipt = daemon.invoke_instrument(
                session.session_id,
                "source-0",
                _direct_payload_command(
                    "spawned-payload-invoke",
                    payload,
                    interface_id="tests.control/v1",
                ),
            )

            assert receipt.status == "invoked"
            assert endpoint.worker_pid != os.getpid()
            assert receipt.metadata == {
                "payload_hex": _PAYLOAD_BYTES.hex(),
                "payload_types": ["_DecodedProgram"],
                "worker_pid": endpoint.worker_pid,
            }
            assert receipt.readback is None
            assert _WORKER_MODULE not in sys.modules
            daemon.close_instrument_session(session.session_id)
    finally:
        endpoint.shutdown()


def test_binary_command_payload_crosses_real_json_http_boundary(
    tmp_path: Path,
) -> None:
    provider = _PayloadProvider()
    with (
        _runtime(tmp_path, provider) as runtime,
        TestClient(runtime.app()) as transport,
    ):
        daemon = _daemon_client(transport)
        run_id, lease_id = _start_run(daemon)
        _provision(daemon, run_id, lease_id)
        payload = _inline_payload("program-inline", _PAYLOAD_BYTES)

        receipt = daemon.execute_run_hardware(
            run_id,
            _payload_batch(lease_id, "inline-batch", payload),
        )

        assert receipt.problems == ()
        assert runtime.application.payloads.spooled_size_bytes() == 0
        assert not _payload_object_path(runtime, payload.content_hash).exists()
        [driver] = provider.drivers
        assert driver.consumed_payloads == [_PAYLOAD_BYTES]
        [event] = [
            event
            for event in daemon.replay_events(run_id=run_id).items
            if event.kind.startswith("run_hardware_batch_")
        ]
        assert event.kind == "run_hardware_batch_measured"
        measured = daemon.get_run_measured_costs(run_id)
        assert len(measured.operations) == 1
        assert measured.operations[0].operation == "invoke"
        assert measured.operations[0].measured is None  # No inferred bus byte count.
        assert _PAYLOAD_BYTES.hex() not in str(event.payload)


def test_direct_invoke_uses_the_same_payload_object_boundary(
    tmp_path: Path,
) -> None:
    provider = _PayloadProvider()
    with (
        _runtime(tmp_path, provider) as runtime,
        TestClient(runtime.app()) as transport,
    ):
        daemon = _daemon_client(transport)
        session = daemon.open_instrument_session(
            InstrumentSessionOpenCommand(
                operation_id="open-payload-session",
                actor="payload-test",
                instrument_ids=("source-0",),
            )
        )
        payload = _inline_payload("direct-program", _PAYLOAD_BYTES)
        command = InvokeCommand(
            command_id="direct-payload-invoke",
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

        receipt = daemon.invoke_instrument(
            session.session_id,
            "source-0",
            command,
        )
        assert runtime.application.payloads.spooled_size_bytes() == 0
        daemon.close_instrument_session(session.session_id)

        assert receipt.status == "invoked"
        [driver] = provider.drivers
        assert driver.consumed_payloads == [_PAYLOAD_BYTES]
        [argument] = driver.invoked[0].arguments.values()
        assert isinstance(argument, DriverPayload)
        assert argument.schema_id == payload.schema_id
        assert argument.value == _PAYLOAD_BYTES


def test_direct_payload_decode_rejection_does_not_reach_driver_or_quarantine(
    tmp_path: Path,
) -> None:
    provider = _PayloadProvider()
    with (
        _runtime(
            tmp_path,
            provider,
            payload_codecs=_payload_codecs(decoder=_reject_payload_decoder),
        ) as runtime,
        TestClient(runtime.app()) as transport,
    ):
        daemon = _daemon_client(transport)
        session = daemon.open_instrument_session(
            InstrumentSessionOpenCommand(
                operation_id="open-rejected-payload-session",
                actor="payload-test",
                instrument_ids=("source-0",),
            )
        )
        payload = _inline_payload("rejected-program", _PAYLOAD_BYTES)

        receipt = daemon.invoke_instrument(
            session.session_id,
            "source-0",
            _direct_payload_command("rejected-payload-invoke", payload),
        )

        assert receipt.status == "not_invoked"
        assert [problem.code for problem in receipt.problems] == [
            "instrument_payload_decode_failed"
        ]
        [driver] = provider.drivers
        assert driver.invoked == []
        assert driver.consumed_payloads == []
        assert (
            daemon.read_instrument_state(
                session.session_id,
                "source-0",
            ).instrument_id
            == "source-0"
        )
        [owned] = daemon.list_instruments().items
        assert owned.availability == "active"
        assert owned.owner_id == session.session_id

        daemon.close_instrument_session(session.session_id)


@pytest.mark.parametrize("first_body_kind", ["inline", "blob"])
def test_direct_invoke_idempotency_uses_payload_content_not_transport_body(
    tmp_path: Path,
    first_body_kind: Literal["inline", "blob"],
) -> None:
    provider = _PayloadProvider()
    with (
        _runtime(tmp_path, provider) as runtime,
        TestClient(runtime.app()) as transport,
    ):
        daemon = _daemon_client(transport)
        session = daemon.open_instrument_session(
            InstrumentSessionOpenCommand(
                operation_id=f"open-canonical-{first_body_kind}",
                actor="payload-test",
                instrument_ids=("source-0",),
            )
        )
        inline = _inline_payload("canonical-program", _PAYLOAD_BYTES)
        stored = _put_session_payload_object(
            transport,
            session.session_id,
            "canonical-invoke",
            _PAYLOAD_BYTES,
            content_hash=inline.content_hash,
        )
        blob = command_payload_from_bytes(
            id=inline.id,
            schema_id=inline.schema_id,
            codec_id=inline.codec_id,
            codec_version=inline.codec_version,
            media_type=inline.media_type,
            content=_PAYLOAD_BYTES,
            blob_ref=stored.ref,
        )
        commands = {
            "inline": _direct_payload_command(
                "canonical-invoke",
                inline,
            ),
            "blob": _direct_payload_command(
                "canonical-invoke",
                blob,
            ),
        }
        first = commands[first_body_kind]
        second = commands["blob" if first_body_kind == "inline" else "inline"]
        path = (
            f"/api/v1/instrument-sessions/{session.session_id}/"
            "instruments/source-0/invoke"
        )

        first_response = transport.post(
            path,
            json=first.model_dump(mode="json"),
        )
        assert runtime.application.payloads.spooled_size_bytes() == 0
        second_response = transport.post(
            path,
            json=second.model_dump(mode="json"),
        )
        daemon.close_instrument_session(session.session_id)

        assert first_response.status_code == 200
        assert second_response.status_code == 200
        assert second_response.json() == first_response.json()
        [driver] = provider.drivers
        assert len(driver.invoked) == 1
        assert driver.consumed_payloads == [_PAYLOAD_BYTES]


def test_binary_blob_payload_is_materialized_before_driver_call(
    tmp_path: Path,
) -> None:
    provider = _PayloadProvider()
    with (
        _runtime(tmp_path, provider) as runtime,
        TestClient(runtime.app()) as transport,
    ):
        daemon = _daemon_client(transport)
        run_id, lease_id = _start_run(daemon)
        _provision(daemon, run_id, lease_id)
        inline = _inline_payload("program-blob", _PAYLOAD_BYTES)
        stored = _put_run_payload_object(
            transport,
            run_id,
            lease_id,
            "blob-batch",
            _PAYLOAD_BYTES,
            content_hash=inline.content_hash,
        )
        blob = command_payload_from_bytes(
            id=inline.id,
            schema_id=inline.schema_id,
            codec_id=inline.codec_id,
            codec_version=inline.codec_version,
            media_type=inline.media_type,
            content=_PAYLOAD_BYTES,
            blob_ref=stored.ref,
        )

        receipt = daemon.execute_run_hardware(
            run_id,
            _payload_batch(lease_id, "blob-batch", blob),
        )

        assert receipt.problems == ()
        [driver] = provider.drivers
        assert driver.consumed_payloads == [_PAYLOAD_BYTES]


def test_payload_object_upload_rejects_hash_mismatch(tmp_path: Path) -> None:
    provider = _PayloadProvider()
    with (
        _runtime(tmp_path, provider) as runtime,
        TestClient(runtime.app()) as transport,
    ):
        daemon = _daemon_client(transport)
        session = daemon.open_instrument_session(
            InstrumentSessionOpenCommand(
                operation_id="open-hash-mismatch-payload-session",
                actor="payload-test",
                instrument_ids=("source-0",),
            )
        )
        wrong_hexdigest = "0" * 64

        response = transport.put(
            (
                f"/api/v1/instrument-sessions/{session.session_id}/"
                f"payload-objects/{wrong_hexdigest}"
            ),
            content=_PAYLOAD_BYTES,
            headers={
                "content-type": _MEDIA_TYPE,
                "x-scopecat-payload-command-id": "hash-mismatch",
            },
        )
        daemon.close_instrument_session(session.session_id)

        assert response.status_code == 422
        assert "hash mismatch" in response.text
        assert not _payload_object_path(
            runtime,
            f"sha256:{wrong_hexdigest}",
        ).exists()
        assert not _payload_object_path(
            runtime,
            sha256_content_hash(_PAYLOAD_BYTES),
        ).exists()
        [driver] = provider.drivers
        assert driver.invoked == []


def test_payload_object_upload_rejects_oversize_before_storage(
    tmp_path: Path,
) -> None:
    provider = _PayloadProvider()
    content = b"must-not-be-stored"
    content_hash = sha256_content_hash(content)
    with (
        _runtime(tmp_path, provider) as runtime,
        TestClient(runtime.app()) as transport,
    ):
        daemon = _daemon_client(transport)
        session = daemon.open_instrument_session(
            InstrumentSessionOpenCommand(
                operation_id="open-oversize-payload-session",
                actor="payload-test",
                instrument_ids=("source-0",),
            )
        )
        response = transport.put(
            (
                f"/api/v1/instrument-sessions/{session.session_id}/payload-objects/"
                f"{content_hash.removeprefix('sha256:')}"
            ),
            content=content,
            headers={
                "content-length": str(DEFAULT_MAX_PAYLOAD_OBJECT_BYTES + 1),
                "content-type": _MEDIA_TYPE,
                "x-scopecat-payload-command-id": "oversize",
            },
        )
        daemon.close_instrument_session(session.session_id)

        assert response.status_code == 413
        assert not _payload_object_path(runtime, content_hash).exists()
        [driver] = provider.drivers
        assert driver.invoked == []


def test_payload_object_stream_enforces_limit_across_chunks(
    tmp_path: Path,
) -> None:
    content = b"five!"
    service = CommandPayloadService(
        max_object_bytes=4,
        max_inline_bytes=4,
    )

    async def chunks() -> AsyncIterator[bytes]:
        yield content[:3]
        yield content[3:]

    with pytest.raises(CommandPayloadTooLarge):
        asyncio.run(
            service.put_object_stream(
                chunks(),
                scope=session_payload_scope("session", "oversize"),
                expected_content_hash=sha256_content_hash(content),
            )
        )

    assert service.spooled_size_bytes() == 0


def test_blob_descriptor_is_bounded_before_spool_read() -> None:
    content = b"five!"
    service = CommandPayloadService(
        max_object_bytes=4,
        max_inline_bytes=4,
    )
    inline = _inline_payload("oversized-blob", content)
    blob = command_payload_from_bytes(
        id=inline.id,
        schema_id=inline.schema_id,
        codec_id=inline.codec_id,
        codec_version=inline.codec_version,
        media_type=inline.media_type,
        content=content,
        blob_ref=inline.content_hash,
    )

    with pytest.raises(CommandPayloadTooLarge):
        service.materialize_payloads(
            {blob.id: blob},
            scope=run_payload_scope("run", "oversized"),
        )


def test_payload_materialization_restores_attachment_bundle_parts() -> None:
    content = EncodedPayloadContent.from_bundle(
        AttachmentBundle(
            header=b'{"program":"readout"}',
            attachments=(memoryview(b"first"), memoryview(b"second")),
        )
    )
    payload = command_payload_from_encoded_content(
        id="bundle-program",
        schema_id="tests.bundle/v1",
        codec_id="tests.bundle",
        codec_version=1,
        media_type="application/vnd.tests.bundle",
        content=content,
    )
    service = CommandPayloadService()
    scope = session_payload_scope("session", "bundle")

    canonical = service.canonicalize_invoke_command(
        _direct_payload_command("bundle", payload),
        scope=scope,
    )
    materialized = service.materialize_payloads(canonical.payloads, scope=scope)

    restored = materialized[payload.id].content.require_bundle()
    assert restored.header == b'{"program":"readout"}'
    assert tuple(map(bytes, restored.attachments)) == (b"first", b"second")


def test_streamed_attachment_bundle_materializes_from_file_mapping() -> None:
    content = EncodedPayloadContent.from_bundle(
        AttachmentBundle(
            header=b'{"program":"mapped"}',
            attachments=(memoryview(b"0123456789abcdef"),),
        )
    )
    payload = command_payload_from_encoded_content(
        id="mapped-program",
        schema_id="tests.bundle/v1",
        codec_id="tests.bundle",
        codec_version=1,
        media_type="application/vnd.tests.bundle",
        content=content,
    )
    flat = content.to_bytes()
    service = CommandPayloadService(max_inline_bytes=8)
    scope = session_payload_scope("session", "mapped")

    async def chunks() -> AsyncIterator[bytes]:
        yield flat[:7]
        yield flat[7:]

    asyncio.run(
        service.put_object_stream(
            chunks(),
            scope=scope,
            expected_content_hash=payload.content_hash,
            declared_size_bytes=len(flat),
        )
    )
    blob = payload.model_copy(
        update={"body": BlobPayloadBody(ref=payload.content_hash)}
    )
    materialized = service.materialize_payloads({payload.id: blob}, scope=scope)

    restored = materialized[payload.id].content.require_bundle()
    assert restored.header == b'{"program":"mapped"}'
    assert tuple(map(bytes, restored.attachments)) == (b"0123456789abcdef",)
    assert isinstance(restored.attachments[0], memoryview)
    assert isinstance(restored.attachments[0].obj, mmap)

    del restored, materialized
    service.release(scope)
    assert service.spooled_size_bytes() == 0


def test_closed_direct_session_cannot_leave_an_uploaded_payload(
    tmp_path: Path,
) -> None:
    provider = _PayloadProvider()
    content = b"closed-session-must-not-store"
    payload = _inline_payload("closed-session-program", content)
    with (
        _runtime(tmp_path, provider) as runtime,
        TestClient(runtime.app()) as transport,
    ):
        daemon = _daemon_client(transport)
        session = daemon.open_instrument_session(
            InstrumentSessionOpenCommand(
                operation_id="open-then-close-payload-session",
                actor="payload-test",
                instrument_ids=("source-0",),
            )
        )
        _put_session_payload_object(
            transport,
            session.session_id,
            "orphan-upload",
            content,
            content_hash=payload.content_hash,
        )
        assert runtime.application.payloads.spooled_size_bytes() == len(content)
        daemon.close_instrument_session(session.session_id)
        assert runtime.application.payloads.spooled_size_bytes() == 0
        object_path = _payload_object_path(runtime, payload.content_hash)
        assert not object_path.exists()

        with pytest.raises(DaemonConflictError):
            daemon.invoke_instrument(
                session.session_id,
                "source-0",
                _direct_payload_command("closed-session-invoke", payload),
            )

        assert not object_path.exists()
        [driver] = provider.drivers
        assert driver.invoked == []
        assert driver.consumed_payloads == []


def test_stale_run_lease_cannot_leave_an_uploaded_payload(tmp_path: Path) -> None:
    provider = _PayloadProvider()
    content = b"stale-lease-must-not-store"
    payload = _inline_payload("stale-lease-program", content)
    with (
        _runtime(tmp_path, provider) as runtime,
        TestClient(runtime.app()) as transport,
    ):
        daemon = _daemon_client(transport)
        run_id, lease_id = _start_run(daemon)
        _provision(daemon, run_id, lease_id)
        object_path = _payload_object_path(runtime, payload.content_hash)
        assert not object_path.exists()

        with pytest.raises(DaemonConflictError):
            daemon.execute_run_hardware(
                run_id,
                _payload_batch(
                    f"{lease_id}-stale",
                    "stale-lease-batch",
                    payload,
                ),
            )

        assert not object_path.exists()
        [driver] = provider.drivers
        assert driver.invoked == []
        assert driver.consumed_payloads == []


def test_payload_blob_cannot_cross_hardware_operation_scope(tmp_path: Path) -> None:
    provider = _PayloadProvider()
    with (
        _runtime(tmp_path, provider) as runtime,
        TestClient(runtime.app()) as transport,
    ):
        daemon = _daemon_client(transport)
        run_id, lease_id = _start_run(daemon)
        _provision(daemon, run_id, lease_id)
        inline = _inline_payload("program-missing", _PAYLOAD_BYTES)
        missing = command_payload_from_bytes(
            id=inline.id,
            schema_id=inline.schema_id,
            codec_id=inline.codec_id,
            codec_version=inline.codec_version,
            media_type=inline.media_type,
            content=_PAYLOAD_BYTES,
            blob_ref=inline.content_hash,
        )
        _put_run_payload_object(
            transport,
            run_id,
            lease_id,
            "different-batch",
            _PAYLOAD_BYTES,
            content_hash=inline.content_hash,
        )

        with pytest.raises(httpx2.HTTPStatusError) as caught:
            daemon.execute_run_hardware(
                run_id,
                _payload_batch(
                    lease_id,
                    "missing-blob-batch",
                    missing,
                ),
            )

        assert caught.value.response.status_code == 422
        assert "payload object was not found" in caught.value.response.text
        assert runtime.application.payloads.spooled_size_bytes() == len(_PAYLOAD_BYTES)
        [driver] = provider.drivers
        assert driver.consumed_payloads == []
        assert driver.invoked == []

    assert runtime.application.payloads.spooled_size_bytes() == 0


def test_batch_materializes_all_payloads_before_first_driver_call(
    tmp_path: Path,
) -> None:
    provider = _PayloadProvider()
    with (
        _runtime(tmp_path, provider) as runtime,
        TestClient(runtime.app()) as transport,
    ):
        daemon = _daemon_client(transport)
        run_id, lease_id = _start_run(daemon)
        _provision(daemon, run_id, lease_id)
        valid = _inline_payload("program-valid", _PAYLOAD_BYTES)
        missing_content = b"missing-second"
        missing_inline = _inline_payload(
            "program-missing-second",
            missing_content,
        )
        missing = command_payload_from_bytes(
            id=missing_inline.id,
            schema_id=missing_inline.schema_id,
            codec_id=missing_inline.codec_id,
            codec_version=missing_inline.codec_version,
            media_type=missing_inline.media_type,
            content=missing_content,
            blob_ref=missing_inline.content_hash,
        )
        [valid_action] = _payload_batch(
            lease_id,
            "atomic-valid",
            valid,
        ).batch.actions
        [missing_action] = _payload_batch(
            lease_id,
            "atomic-missing",
            missing,
        ).batch.actions
        command = RunHardwareBatchCommand(
            lease_id=lease_id,
            sequence=0,
            batch=RunHardwareBatch(
                operation_id="atomic-materialization",
                actions=(valid_action, missing_action),
            ),
        )

        with pytest.raises(httpx2.HTTPStatusError) as caught:
            daemon.execute_run_hardware(run_id, command)

        assert caught.value.response.status_code == 422
        [driver] = provider.drivers
        assert driver.consumed_payloads == []
        assert driver.invoked == []
        assert not any(
            event.kind.startswith("run_hardware_batch_")
            for event in daemon.replay_events(run_id=run_id).items
        )


def test_payload_invocations_are_not_suppressed_by_reused_payload_id(
    tmp_path: Path,
) -> None:
    provider = _PayloadProvider()
    with (
        _runtime(tmp_path, provider) as runtime,
        TestClient(runtime.app()) as transport,
    ):
        daemon = _daemon_client(transport)
        run_id, lease_id = _start_run(daemon)
        _provision(daemon, run_id, lease_id)
        first = _inline_payload("reused-program", b"first-program")
        second = _inline_payload("reused-program", b"second-program")
        [first_action] = _payload_batch(
            lease_id,
            "reused-program-first",
            first,
        ).batch.actions
        [second_action] = _payload_batch(
            lease_id,
            "reused-program-second",
            second,
        ).batch.actions
        command = RunHardwareBatchCommand(
            lease_id=lease_id,
            sequence=0,
            batch=RunHardwareBatch(
                operation_id="reused-program-content-change",
                actions=(first_action, second_action),
            ),
        )

        receipt = daemon.execute_run_hardware(run_id, command)

        assert receipt.problems == ()
        [driver] = provider.drivers
        assert driver.consumed_payloads == [b"first-program", b"second-program"]
        assert len(driver.invoked) == 2


@pytest.mark.parametrize(
    "invalid_contract",
    ["payload_schema", "payload_codec", "collect"],
)
def test_batch_prevalidates_every_action_before_first_driver_call(
    tmp_path: Path,
    invalid_contract: Literal["payload_schema", "payload_codec", "collect"],
) -> None:
    provider = _PayloadProvider()
    with (
        _runtime(tmp_path, provider) as runtime,
        TestClient(runtime.app()) as transport,
    ):
        daemon = _daemon_client(transport)
        run_id, lease_id = _start_run(daemon)
        _provision(daemon, run_id, lease_id)
        valid = _inline_payload("preflight-valid", b"valid-first-action")
        [valid_action] = _payload_batch(
            lease_id,
            "preflight-valid",
            valid,
        ).batch.actions
        invalid_action = _invalid_contract_action(
            invalid_contract,
            lease_id=lease_id,
        )
        command = RunHardwareBatchCommand(
            lease_id=lease_id,
            sequence=0,
            batch=RunHardwareBatch(
                operation_id=f"preflight-{invalid_contract}",
                actions=(valid_action, invalid_action),
            ),
        )

        receipt = daemon.execute_run_hardware(run_id, command)

        assert receipt.problems
        [driver] = provider.drivers
        assert driver.consumed_payloads == []
        assert driver.invoked == []
        assert driver.collect_requests == []
        assert not any(
            event.kind.startswith("run_hardware_batch_")
            for event in daemon.replay_events(run_id=run_id).items
        )


def _runtime(
    root: Path,
    provider: _PayloadProvider,
    *,
    payload_codecs: PayloadCodecRegistry | None = None,
) -> LocalDaemonRuntime:
    return LocalDaemonRuntime(
        root,
        bootstrap_config=load_config(),
        instrument_endpoint=LocalInstrumentBackendEndpoint(
            InstrumentBackend(
                provider=provider,
                driver_catalog=DriverCatalog(provider_id=provider.provider_id),
                payload_codecs=(
                    _payload_codecs() if payload_codecs is None else payload_codecs
                ),
            )
        ),
    )


def _daemon_client(transport: TestClient) -> DaemonClient:
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

    return DaemonClient(
        "http://testserver",
        transport=httpx2.MockTransport(send),
    )


def _put_session_payload_object(
    transport: TestClient,
    session_id: str,
    command_id: str,
    content: bytes,
    *,
    content_hash: str,
) -> PayloadObjectReceipt:
    response = transport.put(
        (
            f"/api/v1/instrument-sessions/{session_id}/payload-objects/"
            f"{content_hash.removeprefix('sha256:')}"
        ),
        content=content,
        headers={
            "content-type": _MEDIA_TYPE,
            "x-scopecat-payload-command-id": command_id,
        },
    )
    assert response.status_code == 201
    return PayloadObjectReceipt.model_validate(response.json())


def _put_run_payload_object(
    transport: TestClient,
    run_id: str,
    lease_id: str,
    operation_id: str,
    content: bytes,
    *,
    content_hash: str,
) -> PayloadObjectReceipt:
    response = transport.put(
        (
            f"/api/v1/runs/{run_id}/payload-objects/"
            f"{content_hash.removeprefix('sha256:')}"
        ),
        content=content,
        headers={
            "content-type": _MEDIA_TYPE,
            "x-scopecat-lease-id": lease_id,
            "x-scopecat-payload-operation-id": operation_id,
        },
    )
    assert response.status_code == 201
    return PayloadObjectReceipt.model_validate(response.json())


def _start_run(daemon: DaemonClient) -> tuple[str, str]:
    config = load_config()
    instrument_ids = ("source-0",)
    fingerprint = instrument_contract_fingerprint(
        _PayloadProvider.provider_id,
        tuple(
            _PayloadConsumerDriver(instrument_id).describe()
            for instrument_id in instrument_ids
        ),
    )
    admission = daemon.submit_run(
        RunSubmission(
            submission_id="payload-transport",
            config=config,
            request=RunRequest(experiment_id="payload-transport"),
            plan=RunPlanSummary(
                experiment_id="payload-transport",
                experiment_kind="payload-transport",
                point_plan_fingerprint="a" * 64,
                measurement_contract_fingerprint="b" * 64,
                point_count=1,
                initial_point_count=1,
                point_limit=1,
                host_instrument_order=instrument_ids,
                host_provider_id=_PayloadProvider.provider_id,
                host_contract_fingerprint=fingerprint,
                run_resource_requirements=(
                    RunResourceRequirement(
                        kind="instrument",
                        id="source-0",
                    ),
                ),
            ),
        )
    )
    lease = daemon.start_executor(
        admission.run_id,
        ExecutorStartRequest(executor_id="payload-transport-executor"),
    )
    return admission.run_id, lease.lease_id


def _provision(daemon: DaemonClient, run_id: str, lease_id: str) -> None:
    receipt = daemon.provision_run_instruments(
        run_id,
        RunInstrumentProvisionCommand(
            lease_id=lease_id,
            operation_id="payload-transport-provision",
        ),
    )
    assert receipt.status == "ready"


def _inline_payload(payload_id: str, content: bytes) -> CommandPayload:
    return _payload_with_contract(payload_id, content)


def _payload_with_contract(
    payload_id: str,
    content: bytes,
    *,
    schema_id: str = "pulse_program",
    codec_id: str = _CODEC_ID,
) -> CommandPayload:
    return command_payload_from_bytes(
        id=payload_id,
        schema_id=schema_id,
        codec_id=codec_id,
        codec_version=_CODEC_VERSION,
        media_type=_MEDIA_TYPE,
        content=content,
    )


def _direct_payload_command(
    command_id: str,
    payload: CommandPayload,
    *,
    interface_id: str = "test.play_program/v1",
) -> InvokeCommand:
    return InvokeCommand(
        command_id=command_id,
        instrument_id="source-0",
        resource_id="source-0",
        interface_id=interface_id,
        operation_id="play",
        arguments=[
            InstrumentOperationArgument(
                id="program",
                value=StateValue(PayloadRef(payload_id=payload.id)),
            )
        ],
        payloads={payload.id: payload},
    )


def _payload_batch(
    lease_id: str,
    operation_id: str,
    payload: CommandPayload,
    *,
    sequence: int = 0,
) -> RunHardwareBatchCommand:
    return RunHardwareBatchCommand(
        lease_id=lease_id,
        sequence=sequence,
        batch=RunHardwareBatch(
            operation_id=operation_id,
            actions=(
                RunHardwareInvoke(
                    effect_id=f"{operation_id}.invoke",
                    point_index=0,
                    instrument_id="source-0",
                    resource_id="source-0",
                    interface_id="test.play_program/v1",
                    operation_id="play",
                    arguments=(
                        InstrumentOperationArgument(
                            id="program",
                            value=StateValue(PayloadRef(payload_id=payload.id)),
                        ),
                    ),
                    payloads={payload.id: payload},
                ),
            ),
        ),
    )


def _invalid_contract_action(
    invalid_contract: Literal["payload_schema", "payload_codec", "collect"],
    *,
    lease_id: str,
) -> RunHardwareInvoke | RunHardwareCollect:
    if invalid_contract == "collect":
        return RunHardwareCollect(
            effect_id="preflight-invalid-collect.collect",
            point_index=0,
            instrument_id="source-0",
            point_count=1,
            requests=(
                CollectResultRequest(
                    id="signal",
                    interface_id="test.missing_interface/v1",
                    acquisition_id="sample",
                    result_id="signal",
                ),
            ),
            bindings=(
                RunHardwareCollectBinding(
                    request_id="signal",
                    value_ids=("preflight-invalid-collect.signal",),
                ),
            ),
        )
    payload = _payload_with_contract(
        f"preflight-invalid-{invalid_contract}",
        f"invalid-{invalid_contract}".encode(),
        schema_id=(
            "tests.wrong-payload-schema"
            if invalid_contract == "payload_schema"
            else "pulse_program"
        ),
        codec_id=(
            "tests.wrong-codec" if invalid_contract == "payload_codec" else _CODEC_ID
        ),
    )
    [action] = _payload_batch(
        lease_id,
        f"preflight-invalid-{invalid_contract}",
        payload,
    ).batch.actions
    assert isinstance(action, RunHardwareInvoke)
    return action


def _payload_codecs(
    *,
    decoder: Callable[[bytes], object] | None = None,
) -> PayloadCodecRegistry:
    return PayloadCodecRegistry(
        {
            "pulse_program": byte_payload_codec(
                id=_CODEC_ID,
                version=_CODEC_VERSION,
                media_type=_MEDIA_TYPE,
                encoder=_unused_payload_encoder,
                decoder=_identity_payload_decoder if decoder is None else decoder,
            )
        }
    )


def _unused_payload_encoder(_value: object) -> bytes:
    return b""


def _identity_payload_decoder(content: bytes) -> object:
    return content


def _reject_payload_decoder(_content: bytes) -> object:
    raise ValueError("fixture rejected payload bytes")


def _payload_object_path(
    runtime: LocalDaemonRuntime,
    content_hash: str,
) -> Path:
    hexdigest = content_hash.removeprefix("sha256:")
    return runtime.state_dir / "objects" / hexdigest[:2] / hexdigest[2:]
