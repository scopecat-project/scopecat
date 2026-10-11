"""Virtual acquisition plant layered outside the physical list-mode target."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Protocol, cast, override

import numpy as np
from numpy.typing import NDArray
from scopecat.kernel.state import PayloadRef, StateValue
from scopecat.sdk.domain import (
    DomainInstrumentExecutor,
)
from scopecat.sdk.instruments.commands import InstrumentOperationArgument
from scopecat.sdk.instruments.declarations import (
    argument,
    compile_interface,
    declared_argument_ref,
    declared_operation_ref,
    instrument_interface,
    operation,
)
from scopecat.sdk.instruments.execution import RunHardwareBatch, RunHardwareInvoke
from scopecat_quantum.targets import TargetAcquisitionAddress

from reference_lab.targets.list_mode.execution_model import (
    AcquisitionResponse,
    DigitizerResultBatch,
    DigitizerResultChunk,
    DigitizerValueBlock,
    ListModeRun,
    digitizer_addresses,
    run_fingerprint,
)
from reference_lab.targets.list_mode.job_runtime import ListModeDomainJobRuntime
from reference_lab.targets.list_mode.model import (
    DigitizerAcquisitionWindow,
    DigitizerInputId,
    ListModeArtifact,
)
from reference_lab.virtual_lab.capture_payload import (
    VIRTUAL_CAPTURE_QUEUE_PAYLOAD,
    VIRTUAL_CAPTURE_QUEUE_SCHEMA_ID,
    VirtualCaptureDocument,
    VirtualCaptureQueueDocument,
    VirtualCaptureTraceDocument,
)


@instrument_interface(
    "reference_lab.virtual_capture_source/v1",
    label="Virtual capture source",
    description="Test-only plant input for queued raw ADC captures.",
)
class VirtualCaptureSourceInterface(Protocol):
    @operation(label="Load raw capture queue")
    def load(
        self,
        *,
        captures: Annotated[
            object,
            argument(payload_schema_id=VIRTUAL_CAPTURE_QUEUE_SCHEMA_ID),
        ],
    ) -> None: ...


_COMPILED_VIRTUAL_CAPTURE_SOURCE = compile_interface(VirtualCaptureSourceInterface)
VIRTUAL_CAPTURE_SOURCE_SPEC = _COMPILED_VIRTUAL_CAPTURE_SOURCE.spec
VIRTUAL_CAPTURE_SOURCE = _COMPILED_VIRTUAL_CAPTURE_SOURCE.ref
VIRTUAL_CAPTURE_LOAD = declared_operation_ref(VirtualCaptureSourceInterface, "load")
VIRTUAL_CAPTURE_QUEUE = declared_argument_ref(
    VirtualCaptureSourceInterface,
    "load",
    "captures",
)

type _DigitizerValue = complex | None


class VirtualListModeDomainJobRuntime(ListModeDomainJobRuntime):
    """Program the worker-owned virtual plant before normal target execution."""

    def __init__(self, response: AcquisitionResponse) -> None:
        super().__init__()
        self._response = response

    @override
    def _execute_target(
        self,
        artifact: ListModeArtifact,
        *,
        execution_id: str,
        instruments: DomainInstrumentExecutor,
    ) -> ListModeRun:
        plant = _virtual_plant_preparation(
            artifact,
            self._response,
            execution_id=execution_id,
        )
        receipt = instruments.execute(plant.batch)
        if receipt.indeterminate or receipt.problems:
            raise RuntimeError("virtual capture plant could not be prepared")
        target_run = super()._execute_target(
            artifact,
            execution_id=execution_id,
            instruments=instruments,
        )
        results = DigitizerResultBatch(
            addresses=target_run.results.addresses,
            shot_count=target_run.results.shot_count,
            chunks=tuple(
                DigitizerResultChunk(
                    shot_start=chunk.shot_start,
                    values=chunk.values,
                    available=(
                        chunk.available
                        & plant.available[:, chunk.shot_start : chunk.shot_stop]
                    ),
                )
                for chunk in target_run.results.chunks
            ),
        )
        return ListModeRun(
            results=results,
            artifact=artifact,
            fingerprint=run_fingerprint(
                artifact=artifact,
                results=results,
                response_fingerprint=self._response.fingerprint,
            ),
        )


@dataclass(frozen=True, slots=True)
class _VirtualPlantPreparation:
    batch: RunHardwareBatch
    available: NDArray[np.bool_]


def _virtual_plant_preparation(
    artifact: ListModeArtifact,
    response: AcquisitionResponse,
    *,
    execution_id: str,
) -> _VirtualPlantPreparation:
    addresses = digitizer_addresses(artifact)
    row_by_address = {address: row_index for row_index, address in enumerate(addresses)}
    shot_indices = np.arange(artifact.repetitions, dtype=np.int64)
    response_blocks: dict[TargetAcquisitionAddress, DigitizerValueBlock] = {}
    available = np.empty(
        (len(addresses), artifact.repetitions),
        dtype=np.bool_,
    )
    for entry in artifact.entries:
        for window in entry.acquisitions:
            address = TargetAcquisitionAddress(
                entry_id=entry.entry_id,
                slot_id=window.slot_id,
            )
            block = response.values_for(
                entry_id=entry.entry_id,
                window=window,
                shot_indices=shot_indices,
            )
            response_blocks[address] = block
            available[row_by_address[address]] = block.available

    captures: list[VirtualCaptureDocument] = []
    for shot_index in range(artifact.repetitions):
        for entry in artifact.entries:
            windows_by_input: dict[
                DigitizerInputId, list[DigitizerAcquisitionWindow]
            ] = {}
            for window in entry.acquisitions:
                windows_by_input.setdefault(window.input_id, []).append(window)
            traces: list[VirtualCaptureTraceDocument] = []
            for input_id, windows in windows_by_input.items():
                desired = tuple(
                    _block_value(
                        response_blocks[
                            TargetAcquisitionAddress(
                                entry_id=entry.entry_id,
                                slot_id=window.slot_id,
                            )
                        ],
                        shot_index,
                    )
                    for window in windows
                )
                traces.append(
                    VirtualCaptureTraceDocument(
                        instrument_id=input_id.instrument_id,
                        component_path=input_id.component_path,
                        samples=_synthesize_trace(
                            sample_count=entry.sample_count,
                            sample_rate_hz=artifact.sample_rate_hz,
                            windows=tuple(windows),
                            desired=desired,
                        ),
                    )
                )
            captures.append(VirtualCaptureDocument(traces=tuple(traces)))

    payload_id = f"virtual-captures-{artifact.id.value}"
    payload = VIRTUAL_CAPTURE_QUEUE_PAYLOAD.command_payload(
        payload_id,
        VirtualCaptureQueueDocument(captures=tuple(captures)),
    )
    timing_id = artifact.preparation.timing.trigger_instrument_id
    prefix = f"target:{execution_id}:{artifact.id.value}"
    return _VirtualPlantPreparation(
        batch=RunHardwareBatch(
            operation_id=f"{prefix}:virtual-plant",
            actions=(
                RunHardwareInvoke(
                    effect_id=f"{prefix}:virtual-plant:{timing_id}",
                    instrument_id=timing_id,
                    resource_id=timing_id,
                    interface_id=VIRTUAL_CAPTURE_LOAD.interface_id,
                    operation_id=VIRTUAL_CAPTURE_LOAD.operation_id,
                    arguments=(
                        InstrumentOperationArgument(
                            id=VIRTUAL_CAPTURE_QUEUE.argument_id,
                            value=StateValue(PayloadRef(payload_id=payload.id)),
                        ),
                    ),
                    payloads={payload.id: payload},
                ),
            ),
        ),
        available=available,
    )


def _block_value(block: DigitizerValueBlock, shot_index: int) -> _DigitizerValue:
    return (
        complex(cast("np.complex128", block.values[shot_index]))
        if block.available[shot_index]
        else None
    )


def _synthesize_trace(
    *,
    sample_count: int,
    sample_rate_hz: int,
    windows: tuple[DigitizerAcquisitionWindow, ...],
    desired: tuple[_DigitizerValue, ...],
) -> NDArray[np.float64]:
    rows: list[NDArray[np.float64]] = []
    targets: list[float] = []
    for window, value in zip(windows, desired, strict=True):
        if value is None:
            continue
        coefficients = np.zeros(sample_count, dtype=np.float64)
        quadrature = np.zeros(sample_count, dtype=np.float64)
        frequency_hz = window.intent.demodulation_frequency_hz
        normalization = 1.0 if frequency_hz == 0.0 else 2.0
        for index in range(window.sample_count):
            absolute_index = window.start_sample + index
            phase = np.pi * 2.0 * frequency_hz * (absolute_index + 0.5) / sample_rate_hz
            coefficients[absolute_index] = (
                normalization * np.cos(phase) / window.sample_count
            )
            quadrature[absolute_index] = (
                -normalization * np.sin(phase) / window.sample_count
            )
        rows.extend((coefficients, quadrature))
        targets.extend((value.real, value.imag))
    if not rows:
        return np.zeros(sample_count, dtype=np.float64)
    matrix = np.stack(rows)
    solution, _residuals, _rank, _singular_values = np.linalg.lstsq(
        matrix,
        np.asarray(targets),
        rcond=None,
    )
    return np.asarray(solution, dtype=np.float64)


__all__ = [
    "VIRTUAL_CAPTURE_LOAD",
    "VIRTUAL_CAPTURE_QUEUE",
    "VIRTUAL_CAPTURE_SOURCE",
    "VirtualCaptureSourceInterface",
    "VirtualListModeDomainJobRuntime",
]
