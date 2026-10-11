"""Immutable device programs for the reference-lab list-mode target."""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from typing import Literal, cast, override

import numpy as np
from numpy.typing import NDArray
from scopecat.kernel.content_identity import sha256_json_hash
from scopecat_quantum._ids import (
    AcquisitionSlotId,
    PulseEventId,
    PulseProgramId,
    QubitId,
    TargetArtifactId,
    TargetCompileEntryId,
    TargetCompilerId,
    TargetId,
)
from scopecat_quantum.pulses import (
    AcquireSignal,
    DriveSignal,
    ReadoutSignal,
)
from scopecat_quantum.waveforms import RealizedEventTiming, TimingQuantizationMode

from reference_lab.physical_policies import (
    AwgChannelId,
    IqOffsetCouplingPolicy,
    OutputOffsetCouplingGroup,
    OutputOffsetRequirement,
)


@dataclass(frozen=True, slots=True, order=True)
class DigitizerInputId:
    """One physical ADC input, independent of target-owned DSP slots."""

    value: str
    instrument_id: str
    component_path: tuple[str, ...]


@dataclass(frozen=True, slots=True, order=True)
class DemodulatorSlotId:
    """One target-owned DSP result slot on a physical ADC stream."""

    value: str


type OutputSignal = DriveSignal | ReadoutSignal


def signal_key(
    signal: OutputSignal | AcquireSignal,
) -> tuple[str, str, str]:
    """Return a canonical hardware-independent key for one logical signal."""

    if isinstance(signal, DriveSignal):
        return ("drive", "qubit", signal.owner.value)
    owner_kind = "qubit" if isinstance(signal.owner, QubitId) else "readout_line"
    if isinstance(signal, ReadoutSignal):
        return ("readout", owner_kind, signal.owner.value)
    return ("acquire", owner_kind, signal.owner.value)


@dataclass(frozen=True, slots=True)
class IqMixerCalibration:
    """Lab-reviewed affine transform from ideal complex IQ to physical DACs."""

    ii: float
    iq: float
    qi: float
    qq: float
    i_offset_v: float
    q_offset_v: float


@dataclass(frozen=True, slots=True)
class IqOutputBinding:
    """Bind one logical envelope and signed IF to two physical DACs."""

    signal: OutputSignal
    iq_chain_id: str
    lo_group_id: str
    i_channel_id: AwgChannelId
    q_channel_id: AwgChannelId
    intermediate_frequency_hz: float
    mixer: IqMixerCalibration

    @property
    def channel_ids(self) -> tuple[AwgChannelId, AwgChannelId]:
        return (self.i_channel_id, self.q_channel_id)


@dataclass(frozen=True, slots=True)
class AcquisitionBinding:
    """Bind a logical result to one ADC input and one DSP slot."""

    signal: AcquireSignal
    input_id: DigitizerInputId
    demodulator_slot_id: DemodulatorSlotId
    demodulation_frequency_hz: float


@dataclass(frozen=True, slots=True, order=True)
class ListModePhysicalEndpoint:
    """One concrete channel selected by list-mode placement."""

    kind: Literal["waveform_output", "acquisition_input"]
    instrument_id: str
    channel_id: str
    component_path: tuple[str, ...]

    @property
    def id(self) -> str:
        return f"{self.instrument_id}:{self.channel_id}"


@dataclass(frozen=True, slots=True)
class ListModeSignalPlacement:
    """Configured route from one logical signal to physical endpoints."""

    signal: tuple[str, str, str]
    endpoints: tuple[ListModePhysicalEndpoint, ...]
    lo_group_id: str | None = None
    demodulator_slot_id: str | None = None


@dataclass(frozen=True, slots=True)
class ListModeDeviceSnapshot:
    """Immutable list-mode view of the configured quantum device."""

    target_id: TargetId
    configuration_fingerprint: str
    timing_instrument_id: str
    signal_placements: tuple[ListModeSignalPlacement, ...]
    snapshot_fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "snapshot_fingerprint",
            sha256_json_hash(device_snapshot_payload(self)),
        )

    def signal_placement(
        self,
        signal: tuple[str, str, str],
    ) -> ListModeSignalPlacement:
        for placement in self.signal_placements:
            if placement.signal == signal:
                return placement
        raise KeyError(signal)


@dataclass(frozen=True, slots=True)
class ListModeEventPlacement:
    """One scheduled event placed on its configured physical route."""

    entry_id: TargetCompileEntryId
    event_id: PulseEventId
    signal: ListModeSignalPlacement
    constraint_ids: tuple[str, ...]
    candidate_ids: tuple[str, ...]
    candidate_count: int


type ListModePlacementCandidateStatus = Literal["selected", "rejected"]
type ListModePlacementRejectionCode = Literal[
    "signal_role_mismatch",
    "entity_kind_mismatch",
    "entity_mismatch",
]


@dataclass(frozen=True, slots=True)
class ListModePlacementRejection:
    """One structured reason a physical route was not selected."""

    code: ListModePlacementRejectionCode
    message: str


@dataclass(frozen=True, slots=True)
class ListModePlacementCandidate:
    """One physical route considered for a requested logical signal."""

    id: str
    signal: tuple[str, str, str]
    route: ListModeSignalPlacement
    status: ListModePlacementCandidateStatus
    rejections: tuple[ListModePlacementRejection, ...]


type ListModePlacementConstraintKind = Literal[
    "configured_route",
    "shared_endpoint",
    "shared_local_oscillator",
    "demodulator_slot",
    "timing_domain",
]


@dataclass(frozen=True, slots=True)
class ListModePlacementConstraint:
    """One explainable physical constraint shared by placed program events."""

    id: str
    kind: ListModePlacementConstraintKind
    label: str
    signals: tuple[tuple[str, str, str], ...]
    entity_ids: tuple[str, ...]
    resource_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ListModeProgramPlacement:
    """Exact per-event placement for a finite target compile request."""

    provider_id: str
    provider_fingerprint: str
    device_snapshot_fingerprint: str
    events: tuple[ListModeEventPlacement, ...]
    candidates: tuple[ListModePlacementCandidate, ...]
    candidate_count: int
    candidates_truncated: bool
    constraints: tuple[ListModePlacementConstraint, ...]

    @property
    def logical_qubit_ids(self) -> tuple[str, ...]:
        return tuple(sorted({event.signal.signal[2] for event in self.events}))


@dataclass(frozen=True, slots=True)
class ListModePhysicalFootprint:
    """Compact physical resource and memory footprint of one artifact."""

    instrument_ids: tuple[str, ...]
    waveform_outputs: tuple[ListModePhysicalEndpoint, ...]
    acquisition_inputs: tuple[ListModePhysicalEndpoint, ...]
    timing_instrument_id: str
    waveform_bytes: int
    result_bytes: int
    event_count: int
    acquisition_count: int


@dataclass(frozen=True, slots=True)
class ListModeCompilationKey:
    """Layered identities from scheduled semantics through artifact layout."""

    compiler_id: TargetCompilerId
    placement_provider_fingerprint: str
    device_snapshot_fingerprint: str
    scheduled_program_fingerprints: tuple[str, ...]
    semantic_program_fingerprint: str
    placement_fingerprint: str
    artifact_layout_fingerprint: str

    @property
    def value(self) -> str:
        """Return the final artifact-layout cache key."""

        return self.artifact_layout_fingerprint


@dataclass(frozen=True, slots=True)
class ListModeCompilationStageCacheInfo:
    """Observable behavior of one process-local compilation-stage LRU."""

    hits: int
    misses: int
    evictions: int
    size: int
    capacity: int
    retained_bytes: int
    max_retained_bytes: int
    oversize_skips: int


@dataclass(frozen=True, slots=True)
class ListModeCompilationCachePolicy:
    """Process-local entry and retained-memory budgets for compiler stages."""

    semantic_max_entries: int = 64
    semantic_max_bytes: int = 4 * 1024 * 1024
    placement_max_entries: int = 64
    placement_max_bytes: int = 16 * 1024 * 1024
    layout_max_entries: int = 64
    layout_max_bytes: int = 64 * 1024 * 1024
    artifact_max_entries: int = 32
    artifact_max_bytes: int = 64 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class ListModeCompilationCacheInfo:
    """Observable behavior of semantic, placement, layout, and artifact LRUs."""

    semantic: ListModeCompilationStageCacheInfo
    placement: ListModeCompilationStageCacheInfo
    layout: ListModeCompilationStageCacheInfo
    artifact: ListModeCompilationStageCacheInfo


type ListModeCompilationCacheOutcome = Literal["hit", "miss", "not_checked"]


@dataclass(frozen=True, slots=True)
class ListModeCompilationTrace:
    """Cache disposition of the stages consulted for one compile call."""

    semantic: ListModeCompilationCacheOutcome
    placement: ListModeCompilationCacheOutcome
    layout: ListModeCompilationCacheOutcome
    artifact: ListModeCompilationCacheOutcome
    semantic_seconds: float
    placement_seconds: float
    layout_seconds: float
    artifact_seconds: float
    cache_info: ListModeCompilationCacheInfo

    @property
    def artifact_reused(self) -> bool:
        return self.artifact == "hit"


@dataclass(frozen=True, slots=True)
class ListModeBudgetDimension:
    """Observed use and capacity for one target-owned resource dimension."""

    id: Literal[
        "list_entries",
        "waveform_memory_bytes",
        "event_count",
        "acquisition_count",
        "result_bytes",
        "result_chunk_bytes",
        "samples_per_entry",
        "repetitions",
    ]
    scope: Literal["batch", "entry", "invocation"]
    usage: int
    limit: int
    projected_point_capacity: int | None = None
    projected_shot_capacity: int | None = None


@dataclass(frozen=True, slots=True)
class ListModeCompilationBudget:
    """Multi-dimensional admission report for a compiled list-mode batch."""

    dimensions: tuple[ListModeBudgetDimension, ...]
    next_batch_max_points: int
    limiting_dimensions: tuple[str, ...]

    def dimension(self, id: str) -> ListModeBudgetDimension:
        for dimension in self.dimensions:
            if dimension.id == id:
                return dimension
        raise KeyError(id)


@dataclass(frozen=True, slots=True, order=True)
class ClockPreparation:
    """Shared reference-clock preparation for one physical instrument."""

    instrument_id: str
    source: Literal["internal", "external"]
    frequency_hz: float


@dataclass(frozen=True, slots=True, order=True)
class OutputChannelPreparation:
    """Domain-owned analog state for one active physical AWG output."""

    channel_id: AwgChannelId
    amplitude_v: float
    output_enabled: bool = True


@dataclass(frozen=True, slots=True)
class ListModeHostStateRequirements:
    """Named host state assumptions kept outside target runtime authority.

    The compiler projects active IQ chains through one lab-owned coupling policy.
    The resulting closure may include idle guard channels, while granting the
    target no authority to choose or write their reviewed offsets.
    """

    policy_id: str
    coupling_group_ids: tuple[str, ...]
    output_offsets: tuple[OutputOffsetRequirement, ...]


@dataclass(frozen=True, slots=True, order=True)
class TimingDomainPreparation:
    """Programmed shared-trigger and phase policy for all target members."""

    domain_id: str
    trigger_instrument_id: str
    program_start_guarantee: Literal["non_idempotent", "session_idempotent"]
    digitizer_trigger_source: Literal["external"]
    phase_reference: Literal["entry_trigger_reset"]


@dataclass(frozen=True, slots=True)
class ListModePreparation:
    """Run-wide device state required before loading target programs."""

    clocks: tuple[ClockPreparation, ...]
    outputs: tuple[OutputChannelPreparation, ...]
    timing: TimingDomainPreparation


def physical_endpoint_payload(endpoint: ListModePhysicalEndpoint) -> dict[str, object]:
    return {
        "kind": endpoint.kind,
        "instrument_id": endpoint.instrument_id,
        "channel_id": endpoint.channel_id,
        "component_path": list(endpoint.component_path),
    }


def signal_placement_payload(placement: ListModeSignalPlacement) -> dict[str, object]:
    return {
        "signal": list(placement.signal),
        "endpoints": [
            physical_endpoint_payload(endpoint) for endpoint in placement.endpoints
        ],
        "lo_group_id": placement.lo_group_id,
        "demodulator_slot_id": placement.demodulator_slot_id,
    }


def device_snapshot_payload(snapshot: ListModeDeviceSnapshot) -> dict[str, object]:
    return {
        "schema": "reference_lab.list_mode_device_snapshot.v1",
        "target_id": snapshot.target_id.value,
        "configuration_fingerprint": snapshot.configuration_fingerprint,
        "timing_instrument_id": snapshot.timing_instrument_id,
        "signal_placements": [
            signal_placement_payload(placement)
            for placement in snapshot.signal_placements
        ],
    }


def program_placement_payload(placement: ListModeProgramPlacement) -> dict[str, object]:
    return {
        "provider_id": placement.provider_id,
        "provider_fingerprint": placement.provider_fingerprint,
        "device_snapshot_fingerprint": placement.device_snapshot_fingerprint,
        "events": [
            {
                "entry_id": event.entry_id.value,
                "event_id": pulse_event_identity_payload(event.event_id),
                "signal": signal_placement_payload(event.signal),
                "constraint_ids": list(event.constraint_ids),
                "candidate_ids": list(event.candidate_ids),
                "candidate_count": event.candidate_count,
            }
            for event in placement.events
        ],
        "candidates": [
            {
                "id": candidate.id,
                "signal": list(candidate.signal),
                "route": signal_placement_payload(candidate.route),
                "status": candidate.status,
                "rejections": [
                    {
                        "code": rejection.code,
                        "message": rejection.message,
                    }
                    for rejection in candidate.rejections
                ],
            }
            for candidate in placement.candidates
        ],
        "candidate_count": placement.candidate_count,
        "candidates_truncated": placement.candidates_truncated,
        "constraints": [
            {
                "id": constraint.id,
                "kind": constraint.kind,
                "label": constraint.label,
                "signals": [list(signal) for signal in constraint.signals],
                "entity_ids": list(constraint.entity_ids),
                "resource_ids": list(constraint.resource_ids),
            }
            for constraint in placement.constraints
        ],
    }


def physical_footprint_payload(
    footprint: ListModePhysicalFootprint,
) -> dict[str, object]:
    return {
        "instrument_ids": list(footprint.instrument_ids),
        "waveform_outputs": [
            physical_endpoint_payload(endpoint)
            for endpoint in footprint.waveform_outputs
        ],
        "acquisition_inputs": [
            physical_endpoint_payload(endpoint)
            for endpoint in footprint.acquisition_inputs
        ],
        "timing_instrument_id": footprint.timing_instrument_id,
        "waveform_bytes": footprint.waveform_bytes,
        "result_bytes": footprint.result_bytes,
        "event_count": footprint.event_count,
        "acquisition_count": footprint.acquisition_count,
    }


def compilation_key_payload(key: ListModeCompilationKey) -> dict[str, object]:
    return {
        "schema": "reference_lab.list_mode_compilation_key.v2",
        "compiler_id": key.compiler_id.value,
        "placement_provider_fingerprint": key.placement_provider_fingerprint,
        "device_snapshot_fingerprint": key.device_snapshot_fingerprint,
        "scheduled_program_fingerprints": list(key.scheduled_program_fingerprints),
        "semantic_program_fingerprint": key.semantic_program_fingerprint,
        "placement_fingerprint": key.placement_fingerprint,
        "artifact_layout_fingerprint": key.artifact_layout_fingerprint,
    }


def compilation_budget_payload(
    budget: ListModeCompilationBudget,
) -> dict[str, object]:
    return {
        "dimensions": [
            {
                "id": dimension.id,
                "scope": dimension.scope,
                "usage": dimension.usage,
                "limit": dimension.limit,
                "projected_point_capacity": dimension.projected_point_capacity,
                "projected_shot_capacity": dimension.projected_shot_capacity,
            }
            for dimension in budget.dimensions
        ],
        "next_batch_max_points": budget.next_batch_max_points,
        "limiting_dimensions": list(budget.limiting_dimensions),
    }


def pulse_event_identity_payload(event_id: PulseEventId) -> dict[str, object]:
    """Project structural event identity without coupling hashes to its display."""

    return {
        "scope": list(event_id.scope),
        "local_id": event_id.local_id,
    }


def acquisition_slot_identity_payload(
    slot_id: AcquisitionSlotId,
) -> dict[str, object]:
    """Project structural result identity without coupling hashes to its display."""

    return {
        "scope": list(slot_id.scope),
        "local_id": slot_id.local_id,
    }


def preparation_payload(preparation: ListModePreparation) -> dict[str, object]:
    """Return the canonical preparation representation used by fingerprints."""

    return {
        "clocks": [
            {
                "instrument_id": clock.instrument_id,
                "source": clock.source,
                "frequency_hz": float(clock.frequency_hz).hex(),
            }
            for clock in preparation.clocks
        ],
        "outputs": [
            {
                "channel_id": output.channel_id.value,
                "instrument_id": output.channel_id.instrument_id,
                "component_path": list(output.channel_id.component_path),
                "amplitude_v": float(output.amplitude_v).hex(),
                "output_enabled": output.output_enabled,
            }
            for output in preparation.outputs
        ],
        "timing": {
            "domain_id": preparation.timing.domain_id,
            "trigger_instrument_id": preparation.timing.trigger_instrument_id,
            "program_start_guarantee": (preparation.timing.program_start_guarantee),
            "digitizer_trigger_source": (preparation.timing.digitizer_trigger_source),
            "phase_reference": preparation.timing.phase_reference,
        },
    }


def host_state_requirements_payload(
    requirements: ListModeHostStateRequirements,
) -> dict[str, object]:
    """Return canonical host assumptions used by target fingerprints."""

    return {
        "policy_id": requirements.policy_id,
        "coupling_group_ids": list(requirements.coupling_group_ids),
        "output_offsets": [
            {
                "channel_id": requirement.channel_id.value,
                "instrument_id": requirement.channel_id.instrument_id,
                "component_path": list(requirement.channel_id.component_path),
                "offset_v": float(requirement.offset_v).hex(),
            }
            for requirement in requirements.output_offsets
        ],
    }


def host_state_policy_payload(
    policy: IqOffsetCouplingPolicy,
) -> dict[str, object]:
    """Return the canonical lab coupling policy used by target fingerprints."""

    return {
        "policy_id": policy.id,
        "coupling_groups": [
            {
                "id": group.id,
                "activation_channels": [
                    {
                        "channel_id": channel.value,
                        "instrument_id": channel.instrument_id,
                        "component_path": list(channel.component_path),
                    }
                    for channel in group.activation_channels
                ],
                "output_offsets": [
                    {
                        "channel_id": requirement.channel_id.value,
                        "instrument_id": requirement.channel_id.instrument_id,
                        "component_path": list(requirement.channel_id.component_path),
                        "offset_v": float(requirement.offset_v).hex(),
                    }
                    for requirement in group.output_offsets
                ],
            }
            for group in policy.coupling_groups
        ],
    }


@dataclass(frozen=True, slots=True)
class ListModeTarget:
    """Capabilities, physical routes, and preparation of the target."""

    id: TargetId
    sample_rate_hz: int
    max_list_entries: int
    max_samples_per_entry: int
    max_program_waveform_bytes: int
    max_program_event_count: int
    max_program_acquisition_count: int
    max_result_bytes: int
    max_result_chunk_bytes: int
    max_repetitions: int
    max_abs_amplitude: float
    acquisition_dsp_policy: Literal["target", "device", "prefer_device"]
    digitizer_result_representation: Literal["raw_trace", "integrated_iq"]
    preparation: ListModePreparation
    host_state_policy: IqOffsetCouplingPolicy
    output_bindings: tuple[IqOutputBinding, ...]
    acquisition_bindings: tuple[AcquisitionBinding, ...]
    timing_quantization: TimingQuantizationMode = "nearest"
    _capability_fingerprint: str = field(init=False, repr=False)
    _configuration_fingerprint: str = field(init=False, repr=False)

    def __post_init__(self) -> None:
        canonical_outputs = tuple(
            sorted(
                self.output_bindings,
                key=lambda binding: (
                    *signal_key(binding.signal),
                    binding.lo_group_id,
                    binding.i_channel_id,
                    binding.q_channel_id,
                    binding.intermediate_frequency_hz,
                ),
            )
        )
        canonical_acquisitions = tuple(
            sorted(
                self.acquisition_bindings,
                key=lambda binding: (
                    *signal_key(binding.signal),
                    binding.input_id,
                    binding.demodulator_slot_id,
                ),
            )
        )
        object.__setattr__(self, "output_bindings", canonical_outputs)
        object.__setattr__(self, "acquisition_bindings", canonical_acquisitions)
        object.__setattr__(
            self,
            "_capability_fingerprint",
            sha256_json_hash(self._capability_payload()),
        )
        object.__setattr__(
            self,
            "_configuration_fingerprint",
            sha256_json_hash(self._configuration_payload()),
        )

    @property
    def capability_fingerprint(self) -> str:
        return self._capability_fingerprint

    @property
    def configuration_fingerprint(self) -> str:
        return self._configuration_fingerprint

    @property
    def device_snapshot(self) -> ListModeDeviceSnapshot:
        """Return the exact logical-to-physical routing snapshot."""

        placements = tuple(
            sorted(
                (
                    *(
                        ListModeSignalPlacement(
                            signal=signal_key(binding.signal),
                            endpoints=tuple(
                                ListModePhysicalEndpoint(
                                    kind="waveform_output",
                                    instrument_id=channel.instrument_id,
                                    channel_id=channel.value,
                                    component_path=channel.component_path,
                                )
                                for channel in binding.channel_ids
                            ),
                            lo_group_id=binding.lo_group_id,
                        )
                        for binding in self.output_bindings
                    ),
                    *(
                        ListModeSignalPlacement(
                            signal=signal_key(binding.signal),
                            endpoints=(
                                ListModePhysicalEndpoint(
                                    kind="acquisition_input",
                                    instrument_id=binding.input_id.instrument_id,
                                    channel_id=binding.input_id.value,
                                    component_path=binding.input_id.component_path,
                                ),
                            ),
                            demodulator_slot_id=binding.demodulator_slot_id.value,
                        )
                        for binding in self.acquisition_bindings
                    ),
                ),
                key=lambda placement: placement.signal,
            )
        )
        return ListModeDeviceSnapshot(
            target_id=self.id,
            configuration_fingerprint=self.configuration_fingerprint,
            timing_instrument_id=self.preparation.timing.trigger_instrument_id,
            signal_placements=placements,
        )

    @property
    def supported_envelopes(self) -> tuple[str, ...]:
        return (
            "constant",
            "gaussian",
            "cosine_flat_top",
            "derivative_quadrature",
            "frequency_shift",
        )

    def _capability_payload(self) -> dict[str, object]:
        return {
            "schema": "reference_lab.list_mode_target.capabilities.v10",
            "target_id": self.id.value,
            "sample_rate_hz": self.sample_rate_hz,
            "timing_quantization": self.timing_quantization,
            "max_list_entries": self.max_list_entries,
            "max_samples_per_entry": self.max_samples_per_entry,
            "max_program_waveform_bytes": self.max_program_waveform_bytes,
            "max_program_event_count": self.max_program_event_count,
            "max_program_acquisition_count": self.max_program_acquisition_count,
            "max_result_bytes": self.max_result_bytes,
            "max_result_chunk_bytes": self.max_result_chunk_bytes,
            "max_repetitions": self.max_repetitions,
            "max_abs_amplitude": float(self.max_abs_amplitude).hex(),
            "digitizer_result_representation": (self.digitizer_result_representation),
            "supported_envelopes": list(self.supported_envelopes),
        }

    def _configuration_payload(self) -> dict[str, object]:
        return {
            "schema": "reference_lab.list_mode_target.configuration.v3",
            "target_id": self.id.value,
            "capability_fingerprint": self.capability_fingerprint,
            "acquisition_dsp_policy": self.acquisition_dsp_policy,
            "output_bindings": [
                {
                    "signal": signal_key(binding.signal),
                    "iq_chain_id": binding.iq_chain_id,
                    "lo_group_id": binding.lo_group_id,
                    "instrument_id": binding.i_channel_id.instrument_id,
                    "i_channel_id": binding.i_channel_id.value,
                    "i_component_path": list(binding.i_channel_id.component_path),
                    "q_channel_id": binding.q_channel_id.value,
                    "q_component_path": list(binding.q_channel_id.component_path),
                    "intermediate_frequency_hz": float(
                        binding.intermediate_frequency_hz
                    ).hex(),
                    "mixer": {
                        "ii": float(binding.mixer.ii).hex(),
                        "iq": float(binding.mixer.iq).hex(),
                        "qi": float(binding.mixer.qi).hex(),
                        "qq": float(binding.mixer.qq).hex(),
                        "i_offset_v": float(binding.mixer.i_offset_v).hex(),
                        "q_offset_v": float(binding.mixer.q_offset_v).hex(),
                    },
                }
                for binding in self.output_bindings
            ],
            "acquisition_bindings": [
                {
                    "signal": signal_key(binding.signal),
                    "input_id": binding.input_id.value,
                    "instrument_id": binding.input_id.instrument_id,
                    "component_path": list(binding.input_id.component_path),
                    "demodulator_slot_id": binding.demodulator_slot_id.value,
                    "demodulation_frequency_hz": float(
                        binding.demodulation_frequency_hz
                    ).hex(),
                }
                for binding in self.acquisition_bindings
            ],
            "preparation": preparation_payload(self.preparation),
            "host_state_policy": host_state_policy_payload(self.host_state_policy),
        }


@dataclass(frozen=True, slots=True, eq=False)
class AwgChannelWaveform:
    """One immutable, zero-padded physical DAC buffer."""

    channel_id: AwgChannelId
    samples: NDArray[np.float64] = field(repr=False)
    samples_sha256: str = field(init=False)

    def __post_init__(self) -> None:
        canonical_samples = np.asarray(self.samples, dtype="<f8", order="C")
        object.__setattr__(
            self,
            "samples_sha256",
            hashlib.sha256(canonical_samples).hexdigest(),
        )

    @override
    def __eq__(self, other: object) -> bool:
        if not isinstance(other, AwgChannelWaveform):
            return NotImplemented
        return (
            self.channel_id == other.channel_id
            and self.samples_sha256 == other.samples_sha256
            and bool(np.array_equal(self.samples, other.samples))
        )


@dataclass(frozen=True, slots=True, eq=False)
class AwgPhaseTemplate:
    """Quadrature basis synthesized into ordinary DAC buffers by a worker."""

    id: str
    i_channel_id: AwgChannelId
    q_channel_id: AwgChannelId
    start_sample: int
    sample_count: int
    logical_i: NDArray[np.float64] = field(repr=False)
    logical_q: NDArray[np.float64] = field(repr=False)
    mixer: IqMixerCalibration
    content_sha256: str = field(init=False)

    def __post_init__(self) -> None:
        logical_i = np.ascontiguousarray(self.logical_i, dtype="<f8")
        logical_q = np.ascontiguousarray(self.logical_q, dtype="<f8")
        logical_i.flags.writeable = False
        logical_q.flags.writeable = False
        digest = hashlib.sha256()
        digest.update(logical_i)
        digest.update(logical_q)
        object.__setattr__(self, "logical_i", logical_i)
        object.__setattr__(self, "logical_q", logical_q)
        object.__setattr__(self, "content_sha256", digest.hexdigest())

    @property
    def channel_ids(self) -> tuple[AwgChannelId, AwgChannelId]:
        return (self.i_channel_id, self.q_channel_id)


@dataclass(frozen=True, slots=True)
class AwgPhaseTemplateUse:
    """One list-entry phase applied to a worker-local waveform template."""

    template_id: str
    phase_radians: float


def awg_phase_template_identity_payload(
    template: AwgPhaseTemplate,
) -> dict[str, object]:
    """Return stable identity for one phase-synthesis template."""

    return {
        "id": template.id,
        "i_channel_id": template.i_channel_id.value,
        "q_channel_id": template.q_channel_id.value,
        "instrument_id": template.i_channel_id.instrument_id,
        "i_component_path": list(template.i_channel_id.component_path),
        "q_component_path": list(template.q_channel_id.component_path),
        "start_sample": template.start_sample,
        "sample_count": template.sample_count,
        "sample_encoding": "float64-le-quadrature-basis",
        "content_sha256": template.content_sha256,
        "mixer": {
            "ii": float(template.mixer.ii).hex(),
            "iq": float(template.mixer.iq).hex(),
            "qi": float(template.mixer.qi).hex(),
            "qq": float(template.mixer.qq).hex(),
        },
    }


def awg_waveform_identity_payload(
    waveform: AwgChannelWaveform,
) -> dict[str, object]:
    """Return a compact, platform-stable identity for one physical buffer."""

    return {
        "channel_id": waveform.channel_id.value,
        "instrument_id": waveform.channel_id.instrument_id,
        "component_path": list(waveform.channel_id.component_path),
        "sample_encoding": "float64-le",
        "sample_count": int(waveform.samples.size),
        "samples_sha256": waveform.samples_sha256,
    }


@dataclass(frozen=True, slots=True)
class AcquisitionIntent:
    """Semantic integrated-IQ result requested by the pulse program.

    The semantics id fixes sample-center timing, ``exp(-iωt)`` demodulation,
    rectangular averaging, and single-sideband amplitude normalization. Device
    representation and DSP placement are selected separately during lowering.
    """

    semantics_id: Literal["reference_lab.integrated_iq.ssb_midpoint.v1"]
    output_representation: Literal["integrated_iq"]
    demodulation_frequency_hz: float
    integration_weight: Literal["rectangular"]
    normalization: Literal["single_sideband_amplitude"]


@dataclass(frozen=True, slots=True)
class TargetAcquisitionLowering:
    """Fetch raw ADC voltage and realize semantic IQ in the target runtime."""

    execution: Literal["target"] = "target"
    device_result_representation: Literal["raw_trace"] = "raw_trace"


@dataclass(frozen=True, slots=True)
class DeviceAcquisitionLowering:
    """Realize the same versioned semantic IQ in onboard digitizer DSP."""

    execution: Literal["device"] = "device"
    device_result_representation: Literal["integrated_iq"] = "integrated_iq"


type AcquisitionLowering = TargetAcquisitionLowering | DeviceAcquisitionLowering


@dataclass(frozen=True, slots=True)
class DigitizerAcquisitionWindow:
    """One ADC and demodulation window in a list entry."""

    event_id: PulseEventId
    slot_id: AcquisitionSlotId
    signal: AcquireSignal
    input_id: DigitizerInputId
    demodulator_slot_id: DemodulatorSlotId
    intent: AcquisitionIntent
    lowering: AcquisitionLowering
    start_sample: int
    sample_count: int


@dataclass(frozen=True, slots=True)
class ListModeEntry:
    """One target list row with physical output and acquisition work."""

    list_index: int
    entry_id: TargetCompileEntryId
    program_id: PulseProgramId
    sample_count: int
    waveforms: tuple[AwgChannelWaveform, ...]
    acquisitions: tuple[DigitizerAcquisitionWindow, ...]
    event_timings: tuple[RealizedEventTiming, ...]
    phase_template_uses: tuple[AwgPhaseTemplateUse, ...] = ()


@dataclass(frozen=True, slots=True)
class MaterializedAwgProgramEntry:
    """One list row projected to a single physical AWG."""

    entry_id: TargetCompileEntryId
    sample_count: int
    waveforms: tuple[AwgChannelWaveform, ...]


@dataclass(frozen=True, slots=True)
class MaterializedAwgProgram:
    """List rows already rendered into physical DAC buffers."""

    instrument_id: str
    max_abs_amplitude: float
    entries: tuple[MaterializedAwgProgramEntry, ...]


@dataclass(frozen=True, slots=True)
class PhaseSynthesizedAwgProgramEntry:
    """One list row represented by phase parameters over shared bases."""

    entry_id: TargetCompileEntryId
    sample_count: int
    template_uses: tuple[AwgPhaseTemplateUse, ...]


@dataclass(frozen=True, slots=True)
class PhaseSynthesizedAwgProgram:
    """Shared quadrature bases plus phase rows for an ordinary AWG program."""

    instrument_id: str
    max_abs_amplitude: float
    templates: tuple[AwgPhaseTemplate, ...]
    entries: tuple[PhaseSynthesizedAwgProgramEntry, ...]


type AwgProgram = MaterializedAwgProgram | PhaseSynthesizedAwgProgram


@dataclass(frozen=True, slots=True)
class DigitizerProgramEntry:
    """One raw-capture row projected to one physical digitizer."""

    entry_id: TargetCompileEntryId
    sample_count: int
    input_ids: tuple[DigitizerInputId, ...]


@dataclass(frozen=True, slots=True)
class DigitizerProgram:
    """Physical ADC work after target/device DSP placement."""

    instrument_id: str
    sample_rate_hz: int
    trigger_source: Literal["external", "software"]
    result_representation: Literal["raw_trace", "integrated_iq"]
    entries: tuple[DigitizerProgramEntry, ...]


@dataclass(frozen=True, slots=True)
class TriggerParticipants:
    """Physical devices participating in one timing-program entry."""

    awg_instrument_ids: tuple[str, ...]
    digitizer_instrument_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ListModeArtifact:
    """Deeply immutable target artifact and its device-program projections."""

    id: TargetArtifactId
    target_id: TargetId
    compiler_id: TargetCompilerId
    capability_fingerprint: str
    configuration_fingerprint: str
    artifact_fingerprint: str
    source_entry_ids: tuple[TargetCompileEntryId, ...]
    repetitions: int
    sample_rate_hz: int
    waveform_semantics_id: str
    max_abs_amplitude: float
    max_result_chunk_bytes: int
    timing_quantization: TimingQuantizationMode
    compilation_key: ListModeCompilationKey
    compilation_budget: ListModeCompilationBudget
    device_snapshot: ListModeDeviceSnapshot
    placement: ListModeProgramPlacement
    physical_footprint: ListModePhysicalFootprint
    preparation: ListModePreparation
    host_state_requirements: ListModeHostStateRequirements
    entries: tuple[ListModeEntry, ...]
    phase_templates: tuple[AwgPhaseTemplate, ...] = ()

    @property
    def instrument_ids(self) -> tuple[str, ...]:
        """Return the exact physical footprint of this compiled batch."""

        return self.physical_footprint.instrument_ids

    def trigger_participants(
        self,
        entry: ListModeEntry,
    ) -> TriggerParticipants:
        """Project one list row into its shared-trigger participants."""

        return TriggerParticipants(
            awg_instrument_ids=tuple(
                program.instrument_id
                for program in self.awg_programs
                if self._entry_uses_awg(entry, program.instrument_id)
            ),
            digitizer_instrument_ids=tuple(
                program.instrument_id
                for program in self.digitizer_programs
                if next(
                    selected
                    for selected in program.entries
                    if selected.entry_id == entry.entry_id
                ).input_ids
            ),
        )

    @property
    def awg_programs(self) -> tuple[AwgProgram, ...]:
        """Project the artifact into per-instrument AWG programs."""

        instrument_ids = sorted(
            {
                waveform.channel_id.instrument_id
                for entry in self.entries
                for waveform in entry.waveforms
            }
            | {template.i_channel_id.instrument_id for template in self.phase_templates}
        )
        if self.phase_templates:
            return tuple(
                PhaseSynthesizedAwgProgram(
                    instrument_id=instrument_id,
                    max_abs_amplitude=self.max_abs_amplitude,
                    templates=tuple(
                        template
                        for template in self.phase_templates
                        if template.i_channel_id.instrument_id == instrument_id
                    ),
                    entries=tuple(
                        PhaseSynthesizedAwgProgramEntry(
                            entry_id=entry.entry_id,
                            sample_count=entry.sample_count,
                            template_uses=tuple(
                                use
                                for use in entry.phase_template_uses
                                if self._template(
                                    use.template_id
                                ).i_channel_id.instrument_id
                                == instrument_id
                            ),
                        )
                        for entry in self.entries
                    ),
                )
                for instrument_id in instrument_ids
            )
        return tuple(
            MaterializedAwgProgram(
                instrument_id=instrument_id,
                max_abs_amplitude=self.max_abs_amplitude,
                entries=tuple(
                    MaterializedAwgProgramEntry(
                        entry_id=entry.entry_id,
                        sample_count=entry.sample_count,
                        waveforms=tuple(
                            waveform
                            for waveform in entry.waveforms
                            if waveform.channel_id.instrument_id == instrument_id
                        ),
                    )
                    for entry in self.entries
                ),
            )
            for instrument_id in instrument_ids
        )

    def entry_waveforms(
        self,
        entry: ListModeEntry,
    ) -> tuple[AwgChannelWaveform, ...]:
        """Materialize one entry at the target/ordinary-AWG boundary."""

        if not entry.phase_template_uses:
            return entry.waveforms
        buffers: dict[AwgChannelId, NDArray[np.float64]] = {}
        for use in entry.phase_template_uses:
            template = self._template(use.template_id)
            cosine = math.cos(use.phase_radians)
            sine = math.sin(use.phase_radians)
            logical_i = template.logical_i
            logical_q = template.logical_q
            selected = slice(
                template.start_sample,
                template.start_sample + template.sample_count,
            )
            for channel_id, mixer_i, mixer_q in (
                (
                    template.i_channel_id,
                    template.mixer.ii,
                    template.mixer.iq,
                ),
                (
                    template.q_channel_id,
                    template.mixer.qi,
                    template.mixer.qq,
                ),
            ):
                buffer = buffers.setdefault(
                    channel_id,
                    np.zeros(entry.sample_count, dtype=np.float64),
                )
                buffer[selected] += cosine * (
                    mixer_i * logical_i + mixer_q * logical_q
                ) + sine * (-mixer_i * logical_q + mixer_q * logical_i)
        waveforms = tuple(
            AwgChannelWaveform(channel_id=channel_id, samples=samples)
            for channel_id, samples in sorted(buffers.items())
        )
        for waveform in waveforms:
            peak = (
                float(cast("np.float64", np.max(np.abs(waveform.samples))))
                if waveform.samples.size
                else 0.0
            )
            if peak > self.max_abs_amplitude:
                raise ValueError(
                    f"waveform on channel {waveform.channel_id.value!r} has magnitude "
                    f"{peak!r}; target limit is {self.max_abs_amplitude!r}"
                )
        return waveforms

    def materialized_waveform_bytes(self, entry: ListModeEntry) -> int:
        """Return physical DAC memory required for one list entry."""

        if not entry.phase_template_uses:
            return sum(waveform.samples.nbytes for waveform in entry.waveforms)
        channel_ids = {
            channel_id
            for use in entry.phase_template_uses
            for channel_id in self._template(use.template_id).channel_ids
        }
        return len(channel_ids) * entry.sample_count * np.dtype("<f8").itemsize

    def _entry_uses_awg(self, entry: ListModeEntry, instrument_id: str) -> bool:
        return any(
            waveform.channel_id.instrument_id == instrument_id
            for waveform in entry.waveforms
        ) or any(
            self._template(use.template_id).i_channel_id.instrument_id == instrument_id
            for use in entry.phase_template_uses
        )

    def _template(self, template_id: str) -> AwgPhaseTemplate:
        return next(
            template for template in self.phase_templates if template.id == template_id
        )

    @property
    def digitizer_programs(self) -> tuple[DigitizerProgram, ...]:
        """Project ADC inputs and demodulator slots per digitizer."""

        instrument_ids = sorted(
            {
                window.input_id.instrument_id
                for entry in self.entries
                for window in entry.acquisitions
            }
        )
        return tuple(
            DigitizerProgram(
                instrument_id=instrument_id,
                sample_rate_hz=self.sample_rate_hz,
                trigger_source=self.preparation.timing.digitizer_trigger_source,
                result_representation=next(
                    window.lowering.device_result_representation
                    for entry in self.entries
                    for window in entry.acquisitions
                    if window.input_id.instrument_id == instrument_id
                ),
                entries=tuple(
                    DigitizerProgramEntry(
                        entry_id=entry.entry_id,
                        sample_count=entry.sample_count,
                        input_ids=tuple(
                            sorted(
                                {
                                    window.input_id
                                    for window in entry.acquisitions
                                    if window.input_id.instrument_id == instrument_id
                                }
                            )
                        ),
                    )
                    for entry in self.entries
                ),
            )
            for instrument_id in instrument_ids
        )


__all__ = [
    "AcquisitionBinding",
    "AcquisitionIntent",
    "AcquisitionLowering",
    "AwgChannelId",
    "AwgChannelWaveform",
    "AwgPhaseTemplate",
    "AwgPhaseTemplateUse",
    "AwgProgram",
    "ClockPreparation",
    "DemodulatorSlotId",
    "DeviceAcquisitionLowering",
    "DigitizerAcquisitionWindow",
    "DigitizerInputId",
    "DigitizerProgram",
    "DigitizerProgramEntry",
    "IqMixerCalibration",
    "IqOffsetCouplingPolicy",
    "IqOutputBinding",
    "ListModeArtifact",
    "ListModeBudgetDimension",
    "ListModeCompilationBudget",
    "ListModeCompilationCacheInfo",
    "ListModeCompilationKey",
    "ListModeCompilationStageCacheInfo",
    "ListModeDeviceSnapshot",
    "ListModeEntry",
    "ListModeEventPlacement",
    "ListModeHostStateRequirements",
    "ListModePhysicalEndpoint",
    "ListModePhysicalFootprint",
    "ListModePlacementCandidate",
    "ListModePlacementCandidateStatus",
    "ListModePlacementConstraint",
    "ListModePlacementConstraintKind",
    "ListModePlacementRejection",
    "ListModePlacementRejectionCode",
    "ListModePreparation",
    "ListModeProgramPlacement",
    "ListModeSignalPlacement",
    "ListModeTarget",
    "MaterializedAwgProgram",
    "MaterializedAwgProgramEntry",
    "OutputChannelPreparation",
    "OutputOffsetCouplingGroup",
    "OutputOffsetRequirement",
    "OutputSignal",
    "PhaseSynthesizedAwgProgram",
    "PhaseSynthesizedAwgProgramEntry",
    "TargetAcquisitionLowering",
    "TimingDomainPreparation",
    "TriggerParticipants",
    "awg_phase_template_identity_payload",
    "awg_waveform_identity_payload",
    "compilation_budget_payload",
    "compilation_key_payload",
    "device_snapshot_payload",
    "host_state_policy_payload",
    "host_state_requirements_payload",
    "physical_footprint_payload",
    "preparation_payload",
    "program_placement_payload",
    "signal_placement_payload",
]
