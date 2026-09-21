from __future__ import annotations

import pytest
from pydantic import BaseModel, ValidationError

from scopecat.kernel.state import PayloadRef, StateValue
from scopecat.records.content import command_payload_from_bytes
from scopecat.records.instrument import state_member_target
from scopecat.sdk.instruments import (
    DriverPayload,
    InterfaceRef,
    PropertyRef,
)
from scopecat.sdk.instruments.backend import (
    BackendApplyRequest,
    BackendCollectRequest,
    BackendCollectResult,
    BackendInvokeRequest,
    BackendOperationArgument,
    BackendPayload,
    BackendStateMemberWrite,
    decode_driver_operation,
    lower_backend_apply_request,
    lower_backend_collect_request,
    lower_backend_invoke_request,
)
from scopecat.sdk.instruments.commands import (
    CollectAxisRequest,
    CollectCommand,
    CollectResultRequest,
    InstrumentOperationArgument,
    InstrumentStateAssignment,
    InstrumentStateCommand,
    InvokeCommand,
)
from scopecat.sdk.payloads import (
    EncodedPayloadContent,
    PayloadCodecRegistry,
    byte_payload_codec,
)


def test_apply_command_lowers_to_backend_property_writes() -> None:
    command = InstrumentStateCommand(
        command_id="apply-1",
        instrument_id="source-1",
        assignments=[
            InstrumentStateAssignment(
                resource_id="source-1",
                target=state_member_target(
                    PropertyRef(
                        "test.dc_source/v1",
                        ("channel-a",),
                        "level",
                    )
                ),
                value=StateValue(1.25),
                entity_ids=["logical-source"],
            )
        ],
    )

    request = lower_backend_apply_request(command)

    assert request.model_dump(mode="json") == {
        "assignments": [
            {
                "target": {
                    "kind": "interface",
                    "interface_id": "test.dc_source/v1",
                    "component_path": ["channel-a"],
                    "property_id": "level",
                },
                "value": 1.25,
                "entity_ids": ["logical-source"],
                "channel_bindings": [],
            }
        ]
    }
    assert request.assignments[0].member == InterfaceRef("test.dc_source/v1").component(
        "channel-a"
    ).property("level")
    assert "member" not in request.assignments[0].model_dump()
    assert BackendApplyRequest.model_validate_json(request.model_dump_json()) == request


def test_invoke_command_lowers_with_opaque_payload() -> None:
    payload = command_payload_from_bytes(
        id="program-a",
        schema_id="test.pulse_program/v1",
        codec_id="test.binary",
        codec_version=1,
        media_type="application/octet-stream",
        content=b"\x00\xffprogram",
    )
    command = InvokeCommand(
        command_id="invoke-1",
        instrument_id="drive-1",
        resource_id="drive-1",
        interface_id="test.pulse_player/v1",
        component_path=["channel-a"],
        operation_id="play",
        arguments=[
            InstrumentOperationArgument(
                id="program",
                value=StateValue(PayloadRef(payload_id=payload.id)),
            )
        ],
        payloads={payload.id: payload},
        entity_ids=["logical-drive"],
    )

    materialized = BackendPayload(
        id=payload.id,
        schema_id=payload.schema_id,
        codec_id=payload.codec_id,
        codec_version=payload.codec_version,
        media_type=payload.media_type,
        content_format="bytes",
        content=EncodedPayloadContent.from_bytes(payload.inline_bytes()),
    )
    backend_request = lower_backend_invoke_request(
        command,
        materialized_payloads={payload.id: materialized},
    )

    assert backend_request.interface_id == "test.pulse_player/v1"
    assert backend_request.component_path == ("channel-a",)
    assert backend_request.operation_id == "play"
    assert backend_request.arguments == (
        BackendOperationArgument(
            id="program",
            value=StateValue(PayloadRef(payload_id=payload.id)),
        ),
    )
    assert backend_request.payloads == {payload.id: materialized}
    assert (
        backend_request.payloads[payload.id].content.require_bytes()
        == b"\x00\xffprogram"
    )
    assert {
        "body",
        "content_hash",
        "size_bytes",
    }.isdisjoint(type(backend_request.payloads[payload.id]).model_fields)
    assert {
        "command_id",
        "instrument_id",
        "resource_id",
    }.isdisjoint(backend_request.model_dump())
    assert backend_request.entity_ids == ("logical-drive",)
    assert backend_request.channel_bindings == ()
    decoded_content: list[bytes] = []

    def decode_program(content: bytes) -> object:
        decoded_content.append(content)
        return {"program": content}

    operation = decode_driver_operation(
        backend_request,
        PayloadCodecRegistry(
            {
                payload.schema_id: byte_payload_codec(
                    id=payload.codec_id,
                    version=payload.codec_version,
                    media_type=payload.media_type,
                    encoder=lambda _value: b"",
                    decoder=decode_program,
                )
            }
        ),
    )

    assert operation.target == InterfaceRef("test.pulse_player/v1").component(
        "channel-a"
    ).operation("play")
    assert operation.arguments == {
        "program": DriverPayload(
            schema_id=payload.schema_id,
            content_hash=payload.content_hash,
            value={"program": b"\x00\xffprogram"},
        )
    }
    assert decoded_content == [b"\x00\xffprogram"]


def test_driver_invoke_decode_materializes_each_payload_id_once() -> None:
    payload = BackendPayload(
        id="program-a",
        schema_id="test.pulse_program/v1",
        codec_id="test.binary",
        codec_version=1,
        media_type="application/octet-stream",
        content_format="bytes",
        content=EncodedPayloadContent.from_bytes(b"program"),
    )
    request = BackendInvokeRequest(
        interface_id="test.pulse_player/v1",
        operation_id="play",
        arguments=(
            BackendOperationArgument(id="wait", value=StateValue(0.25)),
            BackendOperationArgument(
                id="program",
                value=StateValue(PayloadRef(payload_id=payload.id)),
            ),
            BackendOperationArgument(
                id="mirror",
                value=StateValue(PayloadRef(payload_id=payload.id)),
            ),
        ),
        payloads={payload.id: payload},
    )
    decoded: list[bytes] = []

    def decode_program(content: bytes) -> object:
        decoded.append(content)
        return {"content": content}

    operation = decode_driver_operation(
        request,
        PayloadCodecRegistry(
            {
                payload.schema_id: byte_payload_codec(
                    id=payload.codec_id,
                    version=payload.codec_version,
                    media_type=payload.media_type,
                    encoder=lambda _value: b"",
                    decoder=decode_program,
                )
            }
        ),
    )

    assert operation.arguments["wait"] == 0.25
    program = operation.arguments["program"]
    mirror = operation.arguments["mirror"]
    assert isinstance(program, DriverPayload)
    assert isinstance(mirror, DriverPayload)
    assert program.schema_id == payload.schema_id
    assert program.content_hash == payload.content.content_hash()
    assert program is mirror
    assert program.value is mirror.value
    assert decoded == [payload.content.require_bytes()]


def test_collect_command_lowers_to_one_acquisition_request() -> None:
    command = CollectCommand(
        command_id="collect-1",
        instrument_id="vna-1",
        point_index=7,
        point_count=20,
        requests=[
            CollectResultRequest(
                id="frequency-axis",
                interface_id="test.network_sweep/v1",
                component_path=["trace-a"],
                acquisition_id="sweep",
                result_id="frequency",
                unit="Hz",
                dimensions=[
                    CollectAxisRequest(
                        id="frequency",
                        kind="frequency",
                        size=401,
                        unit="Hz",
                    )
                ],
                entity_ids=["logical-vna"],
                metadata={"product_id": "frequencies"},
            ),
            CollectResultRequest(
                id="s-parameter-trace",
                interface_id="test.network_sweep/v1",
                component_path=["trace-a"],
                acquisition_id="sweep",
                result_id="s_parameter",
                unit="ratio",
                dtype="complex128",
                dimensions=[
                    CollectAxisRequest(
                        id="frequency",
                        kind="frequency",
                        size=401,
                        unit="Hz",
                    )
                ],
                entity_ids=["logical-vna"],
                metadata={"product_id": "trace"},
            ),
        ],
    )

    request = lower_backend_collect_request(command)

    assert request.model_dump(mode="json") == {
        "interface_id": "test.network_sweep/v1",
        "component_path": ["trace-a"],
        "acquisition_id": "sweep",
        "results": [
            {
                "request_id": "frequency-axis",
                "result_id": "frequency",
                "dimensions": [
                    {
                        "id": "frequency",
                        "kind": "frequency",
                        "offset": None,
                        "size": 401,
                        "unit": "Hz",
                        "metadata": {},
                    }
                ],
            },
            {
                "request_id": "s-parameter-trace",
                "result_id": "s_parameter",
                "dimensions": [
                    {
                        "id": "frequency",
                        "kind": "frequency",
                        "offset": None,
                        "size": 401,
                        "unit": "Hz",
                        "metadata": {},
                    }
                ],
            },
        ],
        "entity_ids": ["logical-vna"],
        "channel_bindings": [],
    }
    acquisition = (
        InterfaceRef("test.network_sweep/v1").component("trace-a").acquisition("sweep")
    )
    assert request.target == acquisition
    assert request.result_target(request.results[0]) == acquisition.result("frequency")
    assert request.result_target(request.results[1]) == acquisition.result(
        "s_parameter"
    )
    assert "target" not in request.model_dump()
    restored = BackendCollectRequest.model_validate_json(request.model_dump_json())

    assert restored == request


@pytest.mark.parametrize(
    "request_model",
    [
        BackendStateMemberWrite(
            target=state_member_target(PropertyRef("test.dc_source/v1", (), "level")),
            value=StateValue(1.0),
        ),
        BackendApplyRequest(
            assignments=(
                BackendStateMemberWrite(
                    target=state_member_target(
                        PropertyRef("test.dc_source/v1", (), "level")
                    ),
                    value=StateValue(1.0),
                ),
            )
        ),
        BackendInvokeRequest(
            interface_id="test.pulse_player/v1",
            operation_id="play",
        ),
        BackendOperationArgument(id="wait", value=StateValue(1.0)),
        BackendPayload(
            id="program",
            schema_id="test.program/v1",
            codec_id="test.binary",
            codec_version=1,
            media_type="application/octet-stream",
            content_format="bytes",
            content=EncodedPayloadContent.from_bytes(b"\x00\xff"),
        ),
        BackendCollectResult(request_id="signal", result_id="signal"),
        BackendCollectRequest(
            interface_id="test.signal/v1",
            acquisition_id="sample",
            results=(BackendCollectResult(request_id="signal", result_id="signal"),),
        ),
    ],
)
def test_process_safe_request_models_are_frozen_and_closed(
    request_model: BaseModel,
) -> None:
    with pytest.raises(ValidationError, match="Instance is frozen"):
        request_model.__setattr__(
            next(iter(type(request_model).model_fields)),
            None,
        )

    wire = request_model.model_dump()
    wire["command_id"] = "daemon-owned"
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        type(request_model).model_validate(wire)
