"""Host-visible job adapter for the list-mode target."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import cast

from scopecat.kernel.json_types import JsonValue
from scopecat.sdk.domain import (
    DomainExecutionReceipt,
    DomainExecutionResult,
    DomainInstrumentExecutor,
    DomainInvocationSpec,
    DomainResultValue,
)
from scopecat_quantum.program_results import (
    MappedQuantumTarget,
    QuantumTargetResultAddress,
)

from reference_lab.targets.list_mode.circuit_runtime import (
    correlate_list_mode_run,
    realize_measurements,
)
from reference_lab.targets.list_mode.device_execution import (
    WORKER_ADC_DSP_FINGERPRINT,
    InstrumentListModeRuntime,
)
from reference_lab.targets.list_mode.execution_model import (
    ListModeRun,
)
from reference_lab.targets.list_mode.model import (
    ListModeArtifact,
    acquisition_slot_identity_payload,
)

type MappedListModeTarget = MappedQuantumTarget[ListModeArtifact]
type ListModeMeasurementInvocationSpec = DomainInvocationSpec[MappedListModeTarget]


def list_mode_measurement_invocation_spec(
    mapped_target: MappedListModeTarget,
    *,
    invocation_id: str,
    response_intent: Mapping[str, JsonValue] | None = None,
) -> ListModeMeasurementInvocationSpec:
    """Declare target identity, realization, and response-affecting intent.

    A custom device response must supply stable ``response_intent`` whose
    content covers that response's fingerprint and configuration.
    """

    artifact = mapped_target.artifact
    selected_response_intent = (
        {
            "schema": "reference_lab.worker_adc_dsp_intent.v1",
            "response_fingerprint": WORKER_ADC_DSP_FINGERPRINT,
        }
        if response_intent is None
        else response_intent
    )
    return DomainInvocationSpec(
        invocation_id=invocation_id,
        target_id=artifact.target_id.value,
        compiler_id=artifact.compiler_id.value,
        capability_fingerprint=artifact.capability_fingerprint,
        artifact_id=artifact.id.value,
        artifact_fingerprint=artifact.artifact_fingerprint,
        execution_summary=_execution_summary(artifact),
        target_intent={
            "schema": "reference_lab.list_mode_measurement_invocation.v6",
            "configuration_fingerprint": artifact.configuration_fingerprint,
            "results": [
                _result_address_intent(result.result_address)
                for result in mapped_target.mapping.results
            ],
            "response": selected_response_intent,
        },
        payload=mapped_target,
    )


def _execution_summary(artifact: ListModeArtifact) -> dict[str, JsonValue]:
    """Project useful physical provenance without retaining device-state logs."""

    return cast(
        "dict[str, JsonValue]",
        {
            "schema": "reference_lab.list_mode_execution_summary.v10",
            "compilation": {
                "key": artifact.compilation_key.value,
                "semantic_program_fingerprint": (
                    artifact.compilation_key.semantic_program_fingerprint
                ),
                "placement_fingerprint": (
                    artifact.compilation_key.placement_fingerprint
                ),
                "next_batch_max_points": (
                    artifact.compilation_budget.next_batch_max_points
                ),
                "limiting_dimensions": list(
                    artifact.compilation_budget.limiting_dimensions
                ),
                "dimensions": {
                    dimension.id: {
                        "scope": dimension.scope,
                        "usage": dimension.usage,
                        "limit": dimension.limit,
                        "projected_point_capacity": (
                            dimension.projected_point_capacity
                        ),
                        "projected_shot_capacity": (dimension.projected_shot_capacity),
                    }
                    for dimension in artifact.compilation_budget.dimensions
                },
            },
            "waveform_outputs": {
                program.instrument_id: sorted(
                    {
                        waveform.channel_id.value
                        for entry in artifact.entries
                        for waveform in entry.waveforms
                        if waveform.channel_id.instrument_id == program.instrument_id
                    }
                    | {
                        channel_id.value
                        for template in artifact.phase_templates
                        for channel_id in template.channel_ids
                        if channel_id.instrument_id == program.instrument_id
                    }
                )
                for program in artifact.awg_programs
            },
            "digitizer_inputs": {
                program.instrument_id: {
                    "input_ids": sorted(
                        {
                            input_id.value
                            for entry in program.entries
                            for input_id in entry.input_ids
                        }
                    ),
                    "result_representation": program.result_representation,
                }
                for program in artifact.digitizer_programs
            },
            "acquisition_semantics": sorted(
                {
                    window.intent.semantics_id
                    for entry in artifact.entries
                    for window in entry.acquisitions
                }
            ),
            "timing": {
                "domain_id": artifact.preparation.timing.domain_id,
                "trigger_instrument_id": (
                    artifact.preparation.timing.trigger_instrument_id
                ),
                "program_start_guarantee": (
                    artifact.preparation.timing.program_start_guarantee
                ),
            },
            "preparation": {
                "scope": "invocation",
                "order": "program_load_then_host_reassert_then_realtime_prepare",
            },
            "placement": {
                "provider_id": artifact.placement.provider_id,
                "provider_fingerprint": artifact.placement.provider_fingerprint,
                "device_snapshot_fingerprint": (
                    artifact.device_snapshot.snapshot_fingerprint
                ),
                "logical_qubit_count": len(artifact.placement.logical_qubit_ids),
                "event_count": len(artifact.placement.events),
                "constraint_count": len(artifact.placement.constraints),
                "constraint_kinds": sorted(
                    {constraint.kind for constraint in artifact.placement.constraints}
                ),
            },
            "physical_footprint": {
                "instrument_count": len(artifact.physical_footprint.instrument_ids),
                "waveform_output_count": len(
                    artifact.physical_footprint.waveform_outputs
                ),
                "acquisition_input_count": len(
                    artifact.physical_footprint.acquisition_inputs
                ),
                "waveform_bytes": artifact.physical_footprint.waveform_bytes,
                "result_bytes": artifact.physical_footprint.result_bytes,
            },
            "host_state_requirements": {
                "policy_id": artifact.host_state_requirements.policy_id,
                "coupling_group_count": len(
                    artifact.host_state_requirements.coupling_group_ids
                ),
                "output_offset_count": len(
                    artifact.host_state_requirements.output_offsets
                ),
            },
        },
    )


def _result_address_intent(
    address: QuantumTargetResultAddress,
) -> dict[str, JsonValue]:
    return cast(
        "dict[str, JsonValue]",
        {
            "entry_id": address.entry_id.value,
            "acquisitions": [
                acquisition_slot_identity_payload(acquisition.slot_id)
                for acquisition in address.acquisitions
            ],
        },
    )


class ListModeDomainJobRuntime:
    """Start one list-mode job through its worker devices."""

    def __init__(self) -> None:
        self._device = InstrumentListModeRuntime()

    def prepare(
        self,
        execution_key: str,
        mapped_target: MappedListModeTarget,
        *,
        instruments: DomainInstrumentExecutor,
    ) -> None:
        self._device.prepare(
            mapped_target.artifact,
            execution_id=execution_key,
            instruments=instruments,
        )

    def start(
        self,
        execution_key: str,
        mapped_target: MappedListModeTarget,
        *,
        instruments: DomainInstrumentExecutor,
    ) -> DomainExecutionResult[ListModeRun]:
        target_run = self._execute_target(
            mapped_target.artifact,
            execution_id=execution_key,
            instruments=instruments,
        )
        return DomainExecutionResult(
            receipt=DomainExecutionReceipt(
                execution_key=execution_key,
                status="completed",
                result_fingerprint=target_run.fingerprint,
                result_count=target_run.results.result_count,
                execution_evidence={
                    "schema": "reference_lab.list_mode_execution_evidence.v1",
                    "artifact_fingerprint": (
                        mapped_target.artifact.artifact_fingerprint
                    ),
                    "response_fingerprint": WORKER_ADC_DSP_FINGERPRINT,
                },
            ),
            result=target_run,
        )

    def _execute_target(
        self,
        artifact: ListModeArtifact,
        *,
        execution_id: str,
        instruments: DomainInstrumentExecutor,
    ) -> ListModeRun:
        return self._device.execute(
            artifact,
            execution_id=execution_id,
            instruments=instruments,
        )


def realize_executed_measurements(
    mapped_target: MappedListModeTarget,
    executed: DomainExecutionResult[ListModeRun],
) -> Iterable[DomainResultValue[QuantumTargetResultAddress]]:
    """Correlate and decode one complete raw run under selected policies."""

    if executed.receipt.result_fingerprint != executed.result.fingerprint:
        msg = "list-mode target receipt does not cover its raw run"
        raise ValueError(msg)
    if executed.receipt.result_count != executed.result.results.result_count:
        msg = "list-mode target receipt has the wrong raw result count"
        raise ValueError(msg)
    correlated = correlate_list_mode_run(
        mapped_target,
        executed.result,
    )
    return realize_measurements(correlated)


__all__ = [
    "ListModeDomainJobRuntime",
    "ListModeMeasurementInvocationSpec",
    "MappedListModeTarget",
    "list_mode_measurement_invocation_spec",
    "realize_executed_measurements",
]
