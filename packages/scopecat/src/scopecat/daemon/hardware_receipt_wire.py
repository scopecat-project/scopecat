"""Binary-array HTTP framing for hardware collection receipts."""

from __future__ import annotations

from typing import Annotated, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from scopecat.kernel.frozen import thaw_json_value
from scopecat.kernel.problems import Problem
from scopecat.measurements.array_wire import (
    EncodedMeasurementArray,
    MeasurementArrayWireError,
    decode_measurement_array,
    encode_measurement_array,
)
from scopecat.program.measurement_types import MeasurementDType
from scopecat.records.costs import OperationCostMeasurement
from scopecat.records.instrument import InstrumentReadback
from scopecat.records.measurement import (
    InstrumentAcquisitionEvidence,
    MeasurementAcquisitionValue,
    MeasurementArray,
    MeasurementPartitionedArray,
    MeasurementScalar,
    MeasurementUnavailable,
)
from scopecat.records.metadata import JsonMetadata
from scopecat.sdk.attachments import (
    AttachmentBundle,
    AttachmentBundleError,
    AttachmentBundleLimits,
    ImmutableBuffer,
)
from scopecat.sdk.instruments.commands import CollectReceipt
from scopecat.sdk.instruments.execution import (
    RunHardwareBatchReceipt,
    RunHardwareStateActionReceipt,
    RunHardwareValue,
)

HARDWARE_RECEIPT_MEDIA_TYPE = "application/vnd.scopecat.hardware-receipt.v2"
_BUNDLE_LIMITS = AttachmentBundleLimits(max_header_bytes=8 * 1024 * 1024)


class HardwareReceiptWireError(ValueError):
    """A framed hardware receipt is invalid."""


class _WireModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        allow_inf_nan=False,
    )


class _ArrayReference(_WireModel):
    kind: Literal["array_attachment"] = "array_attachment"
    index: int = Field(ge=0)
    dtype: MeasurementDType
    unit: str | None = None
    shape: tuple[Annotated[int, Field(ge=0)], ...] = Field(min_length=1)
    metadata: JsonMetadata = Field(default_factory=dict)


type _WireValue = Annotated[
    MeasurementScalar | MeasurementUnavailable | _ArrayReference,
    Field(discriminator="kind"),
]


class _CollectReadbackWire(_WireModel):
    values: dict[str, _WireValue] = Field(default_factory=dict)
    metadata: JsonMetadata = Field(default_factory=dict)


class _CollectReceiptWire(_WireModel):
    format_id: Literal["scopecat.collect_receipt.v1"] = "scopecat.collect_receipt.v1"
    status: Literal["collected", "not_collected", "unknown"]
    problems: tuple[Problem, ...] = ()
    readback: _CollectReadbackWire | None = None
    metadata: JsonMetadata = Field(default_factory=dict)
    measured_cost: OperationCostMeasurement | None = None


class _RunHardwareValueWire(_WireModel):
    point_index: int | None = Field(default=None, ge=0)
    value_id: str = Field(min_length=1)
    value: _WireValue
    evidence: InstrumentAcquisitionEvidence


class _RunHardwareReceiptWire(_WireModel):
    format_id: Literal["scopecat.run_hardware_receipt.v1"] = (
        "scopecat.run_hardware_receipt.v1"
    )
    operation_id: str = Field(min_length=1)
    values: tuple[_RunHardwareValueWire, ...] = ()
    state_actions: tuple[RunHardwareStateActionReceipt, ...] = ()
    problems: tuple[Problem, ...] = ()
    indeterminate: bool = False


def encode_collect_receipt(receipt: CollectReceipt) -> bytes:
    """Encode a direct collection receipt with binary array attachments."""

    attachments: list[EncodedMeasurementArray] = []
    readback = receipt.readback
    header = _CollectReceiptWire(
        status=receipt.status,
        problems=receipt.problems,
        readback=(
            None
            if readback is None
            else _CollectReadbackWire(
                values={
                    request_id: _encode_value(value, attachments)
                    for request_id, value in sorted(readback.values.items())
                },
                metadata=readback.metadata,
            )
        ),
        metadata=receipt.metadata,
        measured_cost=receipt.measured_cost,
    )
    return _encode_bundle(header, attachments)


def decode_collect_receipt(content: bytes) -> CollectReceipt:
    """Decode a framed direct collection receipt."""

    header, bundle = _decode_bundle(content, _CollectReceiptWire)
    references = tuple(
        value
        for value in (
            () if header.readback is None else header.readback.values.values()
        )
        if isinstance(value, _ArrayReference)
    )
    attachments = _validated_attachments(bundle, references)
    readback = header.readback
    try:
        return CollectReceipt(
            status=header.status,
            problems=header.problems,
            readback=(
                None
                if readback is None
                else InstrumentReadback(
                    values={
                        request_id: _decode_value(value, attachments)
                        for request_id, value in readback.values.items()
                    },
                    metadata=readback.metadata,
                )
            ),
            metadata=header.metadata,
            measured_cost=header.measured_cost,
        )
    except ValidationError as error:
        raise HardwareReceiptWireError("invalid decoded collect receipt") from error


def encode_run_hardware_receipt(receipt: RunHardwareBatchReceipt) -> bytes:
    """Encode one run hardware receipt with binary array attachments."""

    attachments: list[EncodedMeasurementArray] = []
    header = _RunHardwareReceiptWire(
        operation_id=receipt.operation_id,
        values=tuple(
            _RunHardwareValueWire(
                point_index=value.point_index,
                value_id=value.value_id,
                value=_encode_value(value.value, attachments),
                evidence=value.evidence,
            )
            for value in receipt.values
        ),
        state_actions=receipt.state_actions,
        problems=receipt.problems,
        indeterminate=receipt.indeterminate,
    )
    return _encode_bundle(header, attachments)


def decode_run_hardware_receipt(content: bytes) -> RunHardwareBatchReceipt:
    """Decode one framed run hardware receipt."""

    header, bundle = _decode_bundle(content, _RunHardwareReceiptWire)
    references = tuple(
        value.value
        for value in header.values
        if isinstance(value.value, _ArrayReference)
    )
    attachments = _validated_attachments(bundle, references)
    try:
        return RunHardwareBatchReceipt(
            operation_id=header.operation_id,
            values=tuple(
                RunHardwareValue(
                    point_index=value.point_index,
                    value_id=value.value_id,
                    value=_decode_value(value.value, attachments),
                    evidence=value.evidence,
                )
                for value in header.values
            ),
            state_actions=header.state_actions,
            problems=header.problems,
            indeterminate=header.indeterminate,
        )
    except ValidationError as error:
        raise HardwareReceiptWireError(
            "invalid decoded run hardware receipt"
        ) from error


def _encode_value(
    value: MeasurementAcquisitionValue,
    attachments: list[EncodedMeasurementArray],
) -> _WireValue:
    if isinstance(value, MeasurementPartitionedArray):
        value = value.materialize()
    if not isinstance(value, MeasurementArray):
        return value
    try:
        content = encode_measurement_array(value)
    except MeasurementArrayWireError as error:
        raise HardwareReceiptWireError(
            "invalid measurement array attachment"
        ) from error
    reference = _ArrayReference(
        index=len(attachments),
        dtype=value.dtype,
        unit=value.unit,
        shape=value.shape,
        metadata=cast("JsonMetadata", thaw_json_value(value.metadata)),
    )
    attachments.append(content)
    return reference


def _decode_value(
    value: _WireValue,
    attachments: tuple[ImmutableBuffer, ...],
) -> MeasurementAcquisitionValue:
    if not isinstance(value, _ArrayReference):
        return value
    try:
        return decode_measurement_array(
            attachments[value.index],
            dtype=value.dtype,
            unit=value.unit,
            shape=value.shape,
            metadata=value.metadata,
        )
    except MeasurementArrayWireError as error:
        raise HardwareReceiptWireError(
            "invalid measurement array attachment"
        ) from error


def _encode_bundle(
    header: BaseModel,
    attachments: list[EncodedMeasurementArray],
) -> bytes:
    try:
        return AttachmentBundle(
            header=header.model_dump_json().encode("utf-8"),
            attachments=tuple(attachments),
        ).to_bytes(_BUNDLE_LIMITS)
    except AttachmentBundleError as error:
        raise HardwareReceiptWireError(str(error)) from error


def _decode_bundle[WireT: BaseModel](
    content: bytes,
    model: type[WireT],
) -> tuple[WireT, AttachmentBundle]:
    try:
        bundle = AttachmentBundle.from_bytes(content, _BUNDLE_LIMITS)
    except AttachmentBundleError as error:
        raise HardwareReceiptWireError(str(error)) from error
    try:
        header = model.model_validate_json(bundle.header)
    except ValidationError as error:
        raise HardwareReceiptWireError("invalid hardware receipt header") from error
    return header, bundle


def _validated_attachments(
    bundle: AttachmentBundle,
    references: tuple[_ArrayReference, ...],
) -> tuple[ImmutableBuffer, ...]:
    selected = tuple(sorted(references, key=lambda item: item.index))
    if tuple(reference.index for reference in selected) != tuple(range(len(selected))):
        raise HardwareReceiptWireError(
            "hardware receipt attachment indexes are invalid"
        )
    if len(selected) != len(bundle.attachments):
        raise HardwareReceiptWireError(
            "hardware receipt attachment count does not match its references"
        )
    return bundle.attachments


__all__ = [
    "HARDWARE_RECEIPT_MEDIA_TYPE",
    "HardwareReceiptWireError",
    "decode_collect_receipt",
    "decode_run_hardware_receipt",
    "encode_collect_receipt",
    "encode_run_hardware_receipt",
]
