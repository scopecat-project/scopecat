"""Typed payload used only to inject traces into the virtual bench plant."""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, ConfigDict
from scopecat.sdk.payloads import PayloadContract
from scopecat.sdk.structured_payloads import (
    FrozenFloat64Vector,
    pydantic_buffer_bundle_codec,
)

VIRTUAL_CAPTURE_QUEUE_SCHEMA_ID = "reference_lab.virtual_capture_queue.v2"


class _CaptureDocument(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        arbitrary_types_allowed=True,
        extra="forbid",
        frozen=True,
        strict=True,
    )


class VirtualCaptureTraceDocument(_CaptureDocument):
    instrument_id: str
    component_path: tuple[str, ...]
    samples: FrozenFloat64Vector


class VirtualCaptureDocument(_CaptureDocument):
    traces: tuple[VirtualCaptureTraceDocument, ...]


class VirtualCaptureQueueDocument(_CaptureDocument):
    captures: tuple[VirtualCaptureDocument, ...]


VIRTUAL_CAPTURE_QUEUE_PAYLOAD = PayloadContract(
    schema_id=VIRTUAL_CAPTURE_QUEUE_SCHEMA_ID,
    codec=pydantic_buffer_bundle_codec(VirtualCaptureQueueDocument),
)


__all__ = [
    "VIRTUAL_CAPTURE_QUEUE_PAYLOAD",
    "VIRTUAL_CAPTURE_QUEUE_SCHEMA_ID",
    "VirtualCaptureDocument",
    "VirtualCaptureQueueDocument",
    "VirtualCaptureTraceDocument",
]
