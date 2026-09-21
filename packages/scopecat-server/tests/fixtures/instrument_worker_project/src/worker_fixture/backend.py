from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, fields
from pathlib import Path

import numpy as np
from scopecat.kernel.value_types import Payload as PayloadType
from scopecat.kernel.value_types import Scalar
from scopecat.records.measurement import (
    MeasurementAcquisitionValue,
    MeasurementArray,
    MeasurementScalar,
    MeasurementUnavailable,
)
from scopecat.sdk.instruments import (
    AcquisitionResultRef,
    DriverAcquisition,
    DriverCatalog,
    DriverOperation,
    DriverOutcome,
    DriverPayload,
    DriverReadback,
    DriverScalar,
    DriverStatePatch,
    DriverStateReadback,
    DriverStateReadRequest,
    DriverSuccess,
    InstrumentBackend,
    InstrumentConnectionContext,
    InstrumentDescription,
    InstrumentProviderContext,
    InstrumentProviderDescription,
    PropertyRef,
    acquisition,
    acquisition_axis,
    acquisition_result,
    float_property,
    interface,
    operation,
    operation_argument,
    state_readback,
)
from scopecat.sdk.payloads import PayloadCodecRegistry, byte_payload_codec


@dataclass(frozen=True, slots=True)
class _DecodedProgram:
    content: bytes


class _Driver:
    implementation_id = "tests.spawned_driver"
    implementation_version = "v1"

    def __init__(self, instrument_id: str, project_root: Path) -> None:
        self.instrument_id = instrument_id
        self._project_root = project_root
        self._state: dict[tuple[str, str], DriverScalar] = {
            ("tests.control/v1", "gain"): 0.0
        }

    def describe(self) -> InstrumentDescription:
        return InstrumentDescription(
            instrument_id=self.instrument_id,
            implementation_id=self.implementation_id,
            implementation_version=self.implementation_version,
            interfaces=[
                interface(
                    "tests.control/v1",
                    properties=[float_property("gain")],
                    operations=[
                        operation(
                            "play",
                            arguments=[
                                operation_argument(
                                    "program",
                                    value_type=Scalar(
                                        PayloadType(schema_id="pulse_program")
                                    ),
                                )
                            ],
                        ),
                        operation("block"),
                    ],
                    acquisitions=[
                        acquisition(
                            "sample",
                            results=[
                                acquisition_result("signal", unit="ratio"),
                                acquisition_result(
                                    "ragged_trace",
                                    unit="V",
                                    axes=[acquisition_axis("sample", size=None)],
                                ),
                            ],
                        )
                    ],
                )
            ],
        )

    def read_state(self, request: DriverStateReadRequest) -> DriverStateReadback:
        return state_readback(
            request,
            {
                target: self._state[(target.interface_id, target.property_id)]
                for target in request.targets
                if isinstance(target, PropertyRef)
            },
            evidence={"worker_pid": os.getpid()},
        )

    def apply_state(
        self,
        request: DriverStatePatch,
    ) -> DriverOutcome[DriverStateReadback | None]:
        for entry in request.entries:
            assert isinstance(entry.target, PropertyRef)
            self._state[(entry.target.interface_id, entry.target.property_id)] = (
                entry.value
            )
        return DriverSuccess(None)

    def invoke(
        self,
        request: DriverOperation,
    ) -> DriverOutcome[DriverStateReadback | None]:
        if request.target.operation_id == "block":
            entered = self._project_root / f"driver-blocked-{self.instrument_id}"
            release = self._project_root / f"driver-release-{self.instrument_id}"
            entered.touch()
            while not release.exists():
                time.sleep(0.01)
        programs: list[_DecodedProgram] = []
        content_hashes: list[str] = []
        for argument in request.arguments.values():
            if isinstance(argument, DriverPayload):
                assert isinstance(argument.value, _DecodedProgram)
                programs.append(argument.value)
                content_hashes.append(argument.content_hash)
        content = b"".join(program.content for program in programs)
        return DriverSuccess(
            None,
            metadata={
                "payload_hex": content.hex(),
                "payload_types": [type(program).__name__ for program in programs],
                "payload_hashes": list(content_hashes),
                "worker_pid": os.getpid(),
            },
        )

    def collect(
        self,
        request: DriverAcquisition,
    ) -> DriverOutcome[DriverReadback]:
        gain = self._state[("tests.control/v1", "gain")]
        assert isinstance(gain, float)
        values: dict[AcquisitionResultRef, MeasurementAcquisitionValue] = {
            result: _measurement_value(result.result_id, gain=gain)
            for result in request.results
        }
        return DriverSuccess(
            DriverReadback(
                values=values,
                metadata={"worker_pid": os.getpid()},
            ),
        )

    def abort(self) -> None:
        _append_marker(self._project_root, "abort")

    def disconnect(self) -> None:
        _append_marker(self._project_root, f"disconnect:{self.instrument_id}")


class _Provider:
    provider_id = "tests.spawned_provider"

    def __init__(self, project_root: Path) -> None:
        self._project_root = project_root

    def describe(
        self,
        context: InstrumentProviderContext,
    ) -> InstrumentProviderDescription:
        assert tuple(field.name for field in fields(context)) == ("bindings",)
        _write_context(
            self._project_root / "describe-context.json",
            {
                "bindings": [
                    binding.model_dump(mode="json") for binding in context.bindings
                ]
            },
        )
        return InstrumentProviderDescription(
            provider_id=self.provider_id,
            instruments=tuple(
                _Driver(binding.id, self._project_root).describe()
                for binding in context.bindings
            ),
        )

    def connect(self, context: InstrumentConnectionContext) -> _Driver:
        assert tuple(field.name for field in fields(context)) == ("binding",)
        _write_context(
            self._project_root / "connect-context.json",
            {"binding": context.binding.model_dump(mode="json")},
        )
        return _Driver(context.binding.id, self._project_root)


def create_backend(project_root: Path) -> InstrumentBackend:
    _write_pid(project_root / "worker.pid")
    provider = _Provider(project_root)
    return InstrumentBackend(
        provider=provider,
        driver_catalog=DriverCatalog(provider_id=provider.provider_id),
        payload_codecs=PayloadCodecRegistry(
            {
                "pulse_program": byte_payload_codec(
                    id="tests.raw",
                    version=1,
                    media_type="application/octet-stream",
                    encoder=_encode_bytes,
                    decoder=_decode_bytes,
                )
            }
        ),
    )


def create_failing_backend(project_root: Path) -> InstrumentBackend:
    _write_pid(project_root / "failed-worker.pid")
    raise RuntimeError("fixture startup failure")


def _measurement_value(
    result_id: str,
    *,
    gain: float = 0.0,
) -> MeasurementAcquisitionValue:
    if result_id == "ragged_trace":
        length = int(gain)
        return MeasurementArray.create(
            dtype="float64",
            unit="V",
            values=tuple(gain + index / 10 for index in range(length)),
        )
    if result_id == "complex_array":
        return MeasurementArray.create(
            dtype="complex128",
            unit="ratio",
            values=(complex(1.0, -0.5),),
        )
    if result_id == "invalid_array":
        return MeasurementArray.model_construct(
            kind="array",
            dtype="complex128",
            unit="ratio",
            shape=(1,),
            values=np.asarray([complex(float("inf"), -0.5)]),
            metadata={},
        )
    if result_id == "large_array":
        return MeasurementArray.create(
            dtype="string",
            values=("x" * (2 * 1024 * 1024),),
        )
    if result_id == "unavailable":
        return MeasurementUnavailable.create(
            dtype="float64",
            unit="ratio",
            shape=(4,),
            reason="overload",
            metadata={"source": "spawned-worker"},
        )
    return MeasurementScalar.create(dtype="float64", value=1.25, unit="ratio")


def _encode_bytes(value: object) -> bytes:
    if not isinstance(value, bytes):
        raise TypeError("fixture codec accepts bytes")
    return value


def _decode_bytes(content: bytes) -> object:
    return _DecodedProgram(content=content)


def _write_pid(path: Path) -> None:
    path.write_text(str(os.getpid()), encoding="utf-8")


def _write_context(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")


def _append_marker(project_root: Path, marker: str) -> None:
    with (project_root / "driver-events.log").open("a", encoding="utf-8") as stream:
        stream.write(f"{marker}\n")
