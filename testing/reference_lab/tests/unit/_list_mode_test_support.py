from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import cast

import numpy as np
import pytest
from scopecat import Quantity
from scopecat.inspection import CompiledArtifactInspection, CompiledProgramInspection
from scopecat.planning.provider_binding import resolve_instrument_contract_catalog
from scopecat.records.measurement import (
    InstrumentAcquisitionEvidence,
    MeasurementArray,
    MeasurementPartitionedArray,
)
from scopecat.sdk.instruments.execution import (
    RunHardwareBatch,
    RunHardwareBatchReceipt,
    RunHardwareCollect,
    RunHardwareValue,
)
from scopecat_quantum._ids import (
    AcquisitionSlotId,
    PulseEventId,
    PulseProgramId,
    QubitId,
    TargetCompileEntryId,
    TargetCompilerId,
)
from scopecat_quantum.acquisitions import INTEGRATED_IQ_RESULT
from scopecat_quantum.pulses import (
    Acquire,
    AcquireSignal,
    AcquisitionSlot,
    Constant,
    DerivativeQuadrature,
    DriveSignal,
    FrequencyShift,
    Gaussian,
    Play,
    PulseProgram,
    ReadoutSignal,
    ScheduledPulseProgram,
    ShiftPhase,
    schedule,
)
from scopecat_quantum.pulses import Parallel as PulseParallel
from scopecat_quantum.pulses import Sequence as PulseSequence
from scopecat_quantum.realtime import TargetProgram
from scopecat_quantum.targets import (
    TargetAcquisitionAddress,
    TargetCompilationError,
    TargetCompileEntry,
    TargetCompileRequest,
)
from scopecat_quantum.waveforms import (
    Float64ReferenceRenderer,
    RenderedWaveforms,
    SampledWaveformPlan,
)

from reference_lab.configuration import bootstrap_config
from reference_lab.physical_policies import (
    IQ_OFFSET_COUPLING_POLICY_ID,
    IqOffsetCouplingGroupDefinition,
    IqOffsetPolicyDefinition,
    OutputOffsetRequirement,
    grouped_iq_offset_policy,
)
from reference_lab.provider import ReferenceLabProvider
from reference_lab.targets.list_mode import (
    ArtifactInspectionBounds,
    ListModeArtifact,
    ListModeCompilationCachePolicy,
    ListModeDeviceSnapshot,
    ListModePlacementDecision,
    ListModeTarget,
    ListModeTargetCompiler,
    build_list_mode_artifact_inspection_snapshot,
    configured_list_mode_target,
)
from reference_lab.targets.list_mode.circuit_runtime import (
    realize_integrated_iq_chunks,
    realize_integrated_iq_value,
)
from reference_lab.targets.list_mode.device_execution import InstrumentListModeRuntime
from reference_lab.targets.list_mode.model import (
    IqMixerCalibration,
    IqOutputBinding,
    OutputSignal,
)

Q0 = QubitId("q0")

Q1 = QubitId("q1")

DRIVE_Q0 = DriveSignal(Q0)

ACQUIRE_Q0 = AcquireSignal(Q0)

READOUT_Q0 = ReadoutSignal(Q0)

READOUT_Q1 = ReadoutSignal(Q1)


class _ReroutingPlacementProvider:
    id = "test.rerouting-placement.v1"
    fingerprint = "sha256:test-rerouting-placement-v1"

    def __init__(self) -> None:
        self.calls = 0

    def place(
        self,
        selected_signals: tuple[tuple[str, str, str], ...],
        snapshot: ListModeDeviceSnapshot,
    ) -> ListModePlacementDecision:
        self.calls += 1
        placements = tuple(
            replace(
                snapshot.signal_placement(("drive", "qubit", "q1")),
                signal=signal,
            )
            if signal == ("drive", "qubit", "q0")
            else snapshot.signal_placement(signal)
            for signal in selected_signals
        )
        empty_ids = tuple((signal, ()) for signal in selected_signals)
        return ListModePlacementDecision(
            provider_id=self.id,
            provider_fingerprint=self.fingerprint,
            device_snapshot_fingerprint=snapshot.snapshot_fingerprint,
            placements=placements,
            candidates=(),
            candidate_count=0,
            constraints=(),
            constraint_ids_by_signal=empty_ids,
            candidate_ids_by_signal=empty_ids,
            candidate_counts_by_signal=tuple(
                (signal, 0) for signal in selected_signals
            ),
        )


class _RecordingInstrumentExecutor:
    def __init__(self) -> None:
        self.batches: list[RunHardwareBatch] = []

    def execute(self, batch: RunHardwareBatch) -> RunHardwareBatchReceipt:
        self.batches.append(batch)
        now = datetime.now(UTC)
        values: list[RunHardwareValue] = []
        for action in batch.actions:
            if not isinstance(action, RunHardwareCollect):
                continue
            requests = {request.id: request for request in action.requests}
            for binding in action.bindings:
                request = requests[binding.request_id]
                [dimension] = request.dimensions
                assert dimension.size is not None
                for value_id in binding.value_ids:
                    capture_value: float | complex = float(len(values) + 1)
                    if request.dtype == "complex128":
                        capture_value = complex(capture_value)
                    values.append(
                        RunHardwareValue(
                            point_index=action.point_index,
                            value_id=value_id,
                            value=MeasurementArray.create(
                                dtype=request.dtype,
                                unit="V",
                                values=(capture_value,) * dimension.size,
                            ),
                            evidence=InstrumentAcquisitionEvidence(
                                command_id=action.effect_id,
                                instrument_id=action.instrument_id,
                                interface_id=request.interface_id,
                                component_path=tuple(request.component_path),
                                acquisition_id=request.acquisition_id,
                                result_id=request.result_id,
                                started_at=now,
                                completed_at=now,
                            ),
                        )
                    )
        return RunHardwareBatchReceipt(
            operation_id=batch.operation_id,
            values=tuple(values),
        )


class _IndeterminateInstrumentExecutor:
    def __init__(self) -> None:
        self.batches: list[RunHardwareBatch] = []

    def execute(self, batch: RunHardwareBatch) -> RunHardwareBatchReceipt:
        self.batches.append(batch)
        return RunHardwareBatchReceipt(
            operation_id=batch.operation_id,
            indeterminate=True,
        )


def _modulated_samples(
    amplitude: float,
    *,
    start_sample: int,
    sample_count: int,
    intermediate_frequency_hz: float,
    sample_rate_hz: int,
) -> tuple[complex, ...]:
    return tuple(
        amplitude
        * complex(
            math.cos(
                math.tau
                * intermediate_frequency_hz
                * (start_sample + index + 0.5)
                / sample_rate_hz
            ),
            math.sin(
                math.tau
                * intermediate_frequency_hz
                * (start_sample + index + 0.5)
                / sample_rate_hz
            ),
        )
        for index in range(sample_count)
    )


def _target() -> ListModeTarget:
    config = bootstrap_config()
    provider = ReferenceLabProvider()
    catalog = resolve_instrument_contract_catalog(
        config=config,
        provider_id=provider.provider_id,
        describe=provider.describe,
    )
    return configured_list_mode_target(config, catalog)


def _output_binding(
    target: ListModeTarget,
    signal: OutputSignal,
) -> IqOutputBinding:
    return next(
        binding for binding in target.output_bindings if binding.signal == signal
    )


def _artifact_inspection(
    artifact: ListModeArtifact,
    *,
    bounds: ArtifactInspectionBounds,
) -> CompiledArtifactInspection:
    program = CompiledProgramInspection(
        dialect_id="test.list-mode",
        program_id=artifact.entries[0].program_id.value,
        snapshot_id=artifact.artifact_fingerprint,
        layers=(),
    )
    snapshot = build_list_mode_artifact_inspection_snapshot(
        artifact,
        program_projector=lambda query: replace(program, query=query),
        bounds=bounds,
    )
    return snapshot.project()


def _request(
    target: ListModeTarget,
    programs: Sequence[ScheduledPulseProgram],
    *,
    repetitions: int,
) -> tuple[ListModeTargetCompiler, TargetCompileRequest]:
    compiler = ListModeTargetCompiler(
        TargetCompilerId("list-mode-compiler.v1"),
        target,
    )
    request = TargetCompileRequest(
        entries=tuple(
            TargetCompileEntry(
                TargetCompileEntryId(f"entry-{index}"),
                TargetProgram.from_scheduled(program),
            )
            for index, program in enumerate(programs)
        ),
        repetitions=repetitions,
    )
    return compiler, request


def _calibrated_acquisition() -> tuple[ScheduledPulseProgram, AcquisitionSlot]:
    slot = AcquisitionSlot(
        AcquisitionSlotId("result"),
        INTEGRATED_IQ_RESULT,
        ACQUIRE_Q0,
    )
    scheduled = schedule(
        PulseProgram(
            PulseProgramId("x-then-readout"),
            PulseSequence(
                (
                    Play(
                        PulseEventId("drive"),
                        DRIVE_Q0,
                        Constant(Quantity(4, "ns"), Quantity(0.25, "arb")),
                    ),
                    PulseParallel(
                        (
                            Play(
                                PulseEventId("stimulus"),
                                READOUT_Q0,
                                Constant(
                                    Quantity(8, "ns"),
                                    Quantity(0.4, "arb"),
                                ),
                            ),
                            Acquire(
                                PulseEventId("capture"),
                                ACQUIRE_Q0,
                                slot.id,
                                Quantity(8, "ns"),
                            ),
                        )
                    ),
                )
            ),
            acquisition_slots=(slot,),
        )
    )
    return scheduled, slot


def _compiled_calibrated_acquisition() -> tuple[
    ListModeTarget,
    ScheduledPulseProgram,
    AcquisitionSlot,
    ListModeArtifact,
]:
    scheduled, slot = _calibrated_acquisition()
    target = _target()
    compiler, request = _request(target, (scheduled,), repetitions=2)
    return target, scheduled, slot, compiler.compile(request)


__all__ = [
    "DRIVE_Q0",
    "IQ_OFFSET_COUPLING_POLICY_ID",
    "Q1",
    "READOUT_Q0",
    "READOUT_Q1",
    "ArtifactInspectionBounds",
    "Constant",
    "Decimal",
    "DerivativeQuadrature",
    "DriveSignal",
    "Float64ReferenceRenderer",
    "FrequencyShift",
    "Gaussian",
    "InstrumentListModeRuntime",
    "IqMixerCalibration",
    "IqOffsetCouplingGroupDefinition",
    "IqOffsetPolicyDefinition",
    "ListModeCompilationCachePolicy",
    "ListModeTargetCompiler",
    "MeasurementArray",
    "MeasurementPartitionedArray",
    "OutputOffsetRequirement",
    "Play",
    "PulseEventId",
    "PulseParallel",
    "PulseProgram",
    "PulseProgramId",
    "PulseSequence",
    "Quantity",
    "QubitId",
    "RenderedWaveforms",
    "RunHardwareCollect",
    "SampledWaveformPlan",
    "ScheduledPulseProgram",
    "ShiftPhase",
    "TargetAcquisitionAddress",
    "TargetCompilationError",
    "TargetCompileEntryId",
    "TargetCompilerId",
    "_IndeterminateInstrumentExecutor",
    "_RecordingInstrumentExecutor",
    "_ReroutingPlacementProvider",
    "_artifact_inspection",
    "_calibrated_acquisition",
    "_compiled_calibrated_acquisition",
    "_modulated_samples",
    "_output_binding",
    "_request",
    "_target",
    "cast",
    "grouped_iq_offset_policy",
    "math",
    "np",
    "pytest",
    "realize_integrated_iq_chunks",
    "realize_integrated_iq_value",
    "replace",
    "schedule",
]
