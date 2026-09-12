"""Build the list-mode target from laboratory-owned configuration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, cast

import scopecat as sc
from pydantic import BaseModel, ConfigDict, Field
from scopecat.kernel.entity import EntityRef
from scopecat.kernel.quantity import Quantity
from scopecat.planning.catalog import InstrumentContractCatalog
from scopecat.records.config import ConfigProfileSnapshot, config_content_hash
from scopecat.records.parameter import TableParameterValue
from scopecat.sdk.instruments import InstrumentDescription
from scopecat_quantum._ids import TargetId
from scopecat_quantum.pulses import AcquireSignal

from reference_lab.bench_interfaces import (
    ANALOG_WAVEFORM_OUTPUT,
    AWG_ARM_PROGRAM,
    AWG_LOAD_PROGRAM,
    AWG_SEQUENCER,
    DIGITIZER_ARM_PROGRAM,
    DIGITIZER_CONTROL,
    DIGITIZER_FETCH_PROGRAM,
    DIGITIZER_FETCH_PROGRAM_IQ,
    DIGITIZER_FETCH_PROGRAM_IQ_VALUE,
    DIGITIZER_FETCH_PROGRAM_VALUE,
    DIGITIZER_INPUT,
    DIGITIZER_LOAD_PROGRAM,
    TRIGGER_COORDINATOR,
    TRIGGER_LOAD_PROGRAM,
    TRIGGER_START_PROGRAM,
    TRIGGER_START_PROGRAM_IDEMPOTENT,
)
from reference_lab.parameters import (
    AwgOutputBaseline,
    IqChainParameters,
    LoGroupParameters,
    QubitParameters,
    ReadoutResonator,
)
from reference_lab.physical_policies import (
    IqOffsetOutputSlot,
    OutputOffsetRequirement,
    grouped_iq_offset_policy,
    iq_offset_output_slots,
    iq_offset_policy_definition,
)
from reference_lab.targets.configuration import (
    DRIVE_I_ROLE,
    DRIVE_Q_ROLE,
    LIST_MODE_TARGET_KIND,
    READOUT_I_ROLE,
    READOUT_Q_ROLE,
    ConfiguredQuantumRoute,
    ConfiguredRfOutput,
    configured_acquisition_signal,
    configured_output_signal,
    configured_quantum_routes,
    configured_rf_outputs,
)
from reference_lab.targets.list_mode.model import (
    AcquisitionBinding,
    AwgChannelId,
    ClockPreparation,
    DemodulatorSlotId,
    DigitizerInputId,
    IqMixerCalibration,
    IqOutputBinding,
    ListModePreparation,
    ListModeTarget,
    OutputChannelPreparation,
    OutputSignal,
    TimingDomainPreparation,
    signal_key,
)


class _CapabilityModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    max_list_entries: int = Field(gt=0)
    max_samples_per_entry: int = Field(gt=0)
    max_program_waveform_bytes: int = Field(gt=0)
    max_program_event_count: int = Field(gt=0)
    max_program_acquisition_count: int = Field(gt=0)
    max_result_bytes: int = Field(gt=0)
    max_result_chunk_bytes: int = Field(gt=0)
    max_repetitions: int = Field(gt=0)
    max_abs_amplitude: float = Field(gt=0.0)
    acquisition_dsp_policy: Literal["target", "device", "prefer_device"]


class _ReferenceClockModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source: Literal["internal", "external"]
    frequency_hz: float = Field(gt=0.0)


class _IqChainModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    chain_id: str = Field(min_length=1)
    i_channel_id: str = Field(min_length=1)
    q_channel_id: str = Field(min_length=1)


class _IqOffsetPolicyModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    policy_id: str = Field(min_length=1)


class _TimingDomainModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    domain_id: str = Field(min_length=1)
    trigger_instrument_id: str = Field(min_length=1)
    trigger_policy: Literal[
        "require_session_idempotent",
        "allow_non_idempotent_program_start",
    ]
    digitizer_trigger_source: Literal["external"]
    phase_reference: Literal["entry_trigger_reset"]


class _ConfigurationModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    sample_rate_hz: int = Field(gt=0)
    capabilities: _CapabilityModel
    reference_clock: _ReferenceClockModel
    timing: _TimingDomainModel
    iq_chains: tuple[_IqChainModel, ...]
    iq_offset_policy: _IqOffsetPolicyModel


@dataclass(frozen=True, slots=True)
class _SignalCalibration:
    carrier_frequency_hz: float


@dataclass(frozen=True, slots=True)
class _IqChainCalibration:
    mixer: IqMixerCalibration


@dataclass(frozen=True, slots=True)
class _LoCalibration:
    frequency_hz: float


def configured_list_mode_target(
    config: ConfigProfileSnapshot,
    instrument_catalog: InstrumentContractCatalog,
) -> ListModeTarget:
    """Resolve physical routes, signed IFs, preparation, and capabilities."""

    if instrument_catalog.config_content_hash != config_content_hash(config):
        raise ValueError(
            "list-mode target instrument catalog belongs to another configuration"
        )

    target_id, routes = configured_quantum_routes(
        config,
        target_kind=LIST_MODE_TARGET_KIND,
    )
    rf_outputs = configured_rf_outputs(
        config,
        target_kind=LIST_MODE_TARGET_KIND,
    )
    target_binding = config.domain_target
    assert target_binding is not None
    settings = _ConfigurationModel.model_validate(target_binding.configuration)
    referenced_instrument_ids = {
        *(route.instrument_id for route in routes),
        settings.timing.trigger_instrument_id,
    }
    unauthorized = sorted(
        referenced_instrument_ids - set(target_binding.instrument_ids)
    )
    if unauthorized:
        raise ValueError(
            "list-mode target configuration references instruments outside its "
            "authority: " + ", ".join(unauthorized)
        )
    signal_calibrations = _signal_calibrations(config)
    iq_chain_calibrations = _iq_chain_calibrations(config)
    lo_calibrations = _lo_calibrations(config)
    output_bindings = _configured_output_bindings(
        routes,
        rf_outputs=rf_outputs,
        settings=settings,
        signal_calibrations=signal_calibrations,
        iq_chain_calibrations=iq_chain_calibrations,
        lo_calibrations=lo_calibrations,
    )
    policy = iq_offset_policy_definition(settings.iq_offset_policy.policy_id)
    required_slots = iq_offset_output_slots(policy)
    output_slots = _configured_output_slots(
        config,
        slots=required_slots,
    )
    output_baselines = _awg_output_baselines(config)
    host_state_policy = grouped_iq_offset_policy(
        policy=policy,
        output_slots={
            slot.id: OutputOffsetRequirement(
                channel_id=_required_output_slot(
                    slot.id,
                    output_slots=output_slots,
                ),
                offset_v=_required_output_baseline(
                    slot.id,
                    output_baselines=output_baselines,
                ),
            )
            for slot in required_slots
        },
        chain_outputs=_offset_requirements_by_chain(output_bindings),
    )
    acquisition_bindings = _configured_acquisition_bindings(
        routes,
        output_bindings=output_bindings,
    )
    _validate_program_devices(
        output_instrument_ids={
            channel.instrument_id
            for binding in output_bindings
            for channel in binding.channel_ids
        },
        digitizer_instrument_ids={
            binding.input_id.instrument_id for binding in acquisition_bindings
        },
        instrument_catalog=instrument_catalog,
    )
    digitizer_result_representation = _digitizer_result_representation(
        acquisition_bindings,
        policy=settings.capabilities.acquisition_dsp_policy,
        instrument_catalog=instrument_catalog,
    )
    program_start_guarantee = _program_start_guarantee(
        settings.timing,
        instrument_catalog=instrument_catalog,
    )
    output_instrument_ids = sorted(
        {
            channel.instrument_id
            for binding in output_bindings
            for channel in binding.channel_ids
        }
    )
    preparation = ListModePreparation(
        clocks=tuple(
            ClockPreparation(
                instrument_id=instrument_id,
                source=settings.reference_clock.source,
                frequency_hz=settings.reference_clock.frequency_hz,
            )
            for instrument_id in output_instrument_ids
        ),
        outputs=_output_preparations(output_bindings),
        timing=TimingDomainPreparation(
            domain_id=settings.timing.domain_id,
            trigger_instrument_id=settings.timing.trigger_instrument_id,
            program_start_guarantee=program_start_guarantee,
            digitizer_trigger_source=settings.timing.digitizer_trigger_source,
            phase_reference=settings.timing.phase_reference,
        ),
    )
    capability = settings.capabilities
    return ListModeTarget(
        id=TargetId(target_id),
        sample_rate_hz=settings.sample_rate_hz,
        max_list_entries=capability.max_list_entries,
        max_samples_per_entry=capability.max_samples_per_entry,
        max_program_waveform_bytes=capability.max_program_waveform_bytes,
        max_program_event_count=capability.max_program_event_count,
        max_program_acquisition_count=capability.max_program_acquisition_count,
        max_result_bytes=capability.max_result_bytes,
        max_result_chunk_bytes=capability.max_result_chunk_bytes,
        max_repetitions=capability.max_repetitions,
        max_abs_amplitude=capability.max_abs_amplitude,
        acquisition_dsp_policy=capability.acquisition_dsp_policy,
        digitizer_result_representation=digitizer_result_representation,
        preparation=preparation,
        host_state_policy=host_state_policy,
        output_bindings=output_bindings,
        acquisition_bindings=acquisition_bindings,
    )


def _digitizer_result_representation(
    bindings: tuple[AcquisitionBinding, ...],
    *,
    policy: Literal["target", "device", "prefer_device"],
    instrument_catalog: InstrumentContractCatalog,
) -> Literal["raw_trace", "integrated_iq"]:
    descriptions = {
        description.instrument_id: description
        for description in instrument_catalog.instruments
    }
    digitizer_ids = sorted({binding.input_id.instrument_id for binding in bindings})
    missing = [
        instrument_id
        for instrument_id in digitizer_ids
        if instrument_id not in descriptions
    ]
    if missing:
        raise ValueError(
            "list-mode target digitizers are absent from the instrument catalog: "
            + ", ".join(missing)
        )
    supports_raw = all(
        _supports_raw_trace(descriptions[instrument_id])
        for instrument_id in digitizer_ids
    )
    supports_device = all(
        _supports_integrated_iq(descriptions[instrument_id])
        for instrument_id in digitizer_ids
    )
    if policy == "device":
        if not supports_device:
            raise ValueError(
                "acquisition DSP policy requires onboard integrated-IQ support"
            )
        return "integrated_iq"
    if policy == "prefer_device" and supports_device:
        return "integrated_iq"
    if not supports_raw:
        raise ValueError("acquisition DSP policy requires raw digitizer traces")
    return "raw_trace"


def _program_start_guarantee(
    timing: _TimingDomainModel,
    *,
    instrument_catalog: InstrumentContractCatalog,
) -> Literal["non_idempotent", "session_idempotent"]:
    description = next(
        (
            description
            for description in instrument_catalog.instruments
            if description.instrument_id == timing.trigger_instrument_id
        ),
        None,
    )
    if description is None:
        raise ValueError("trigger instrument is absent from the instrument catalog")
    interface = next(
        (
            interface
            for interface in description.interfaces
            if interface.id == TRIGGER_COORDINATOR.interface_id
        ),
        None,
    )
    operation_ids: set[str] = (
        set()
        if interface is None
        else {operation.id for operation in interface.operations}
    )
    if TRIGGER_LOAD_PROGRAM.operation_id not in operation_ids:
        raise ValueError("trigger instrument cannot load realtime programs")
    if TRIGGER_START_PROGRAM_IDEMPOTENT.operation_id in operation_ids:
        return "session_idempotent"
    if timing.trigger_policy == "require_session_idempotent":
        raise ValueError("trigger policy requires session-idempotent program support")
    if TRIGGER_START_PROGRAM.operation_id not in operation_ids:
        raise ValueError("trigger instrument cannot start realtime programs")
    return "non_idempotent"


def _validate_program_devices(
    *,
    output_instrument_ids: set[str],
    digitizer_instrument_ids: set[str],
    instrument_catalog: InstrumentContractCatalog,
) -> None:
    descriptions = {
        description.instrument_id: description
        for description in instrument_catalog.instruments
    }
    for instrument_id in sorted(output_instrument_ids):
        description = descriptions.get(instrument_id)
        if description is None or not _has_operations(
            description,
            interface_id=AWG_SEQUENCER.interface_id,
            operation_ids={
                AWG_LOAD_PROGRAM.operation_id,
                AWG_ARM_PROGRAM.operation_id,
            },
        ):
            raise ValueError(
                f"list-mode AWG {instrument_id!r} cannot run loaded programs"
            )
    for instrument_id in sorted(digitizer_instrument_ids):
        description = descriptions.get(instrument_id)
        if description is None or not _has_operations(
            description,
            interface_id=DIGITIZER_CONTROL.interface_id,
            operation_ids={
                DIGITIZER_LOAD_PROGRAM.operation_id,
                DIGITIZER_ARM_PROGRAM.operation_id,
            },
        ):
            raise ValueError(
                f"list-mode digitizer {instrument_id!r} cannot run loaded programs"
            )


def _has_operations(
    description: InstrumentDescription,
    *,
    interface_id: object,
    operation_ids: set[str],
) -> bool:
    return any(
        operation_ids <= {operation.id for operation in interface.operations}
        for interface in description.interfaces
        if interface.id == interface_id
    )


def _supports_raw_trace(description: InstrumentDescription) -> bool:
    return _has_acquisition_result(
        description,
        interface_id=DIGITIZER_INPUT.interface_id,
        acquisition_id=DIGITIZER_FETCH_PROGRAM.acquisition_id,
        result_id=DIGITIZER_FETCH_PROGRAM_VALUE.result_id,
        dtype="float64",
    )


def _supports_integrated_iq(description: InstrumentDescription) -> bool:
    return _has_acquisition_result(
        description,
        interface_id=DIGITIZER_INPUT.interface_id,
        acquisition_id=DIGITIZER_FETCH_PROGRAM_IQ.acquisition_id,
        result_id=DIGITIZER_FETCH_PROGRAM_IQ_VALUE.result_id,
        dtype="complex128",
    )


def _has_acquisition_result(
    description: InstrumentDescription,
    *,
    interface_id: object,
    acquisition_id: str,
    result_id: str,
    dtype: Literal["float64", "complex128"],
) -> bool:
    return any(
        result.id == result_id and result.dtype == dtype
        for interface in description.interfaces
        if interface.id == interface_id
        for acquisition in interface.acquisitions
        if acquisition.id == acquisition_id
        for result in acquisition.results
    )


def _configured_output_bindings(
    routes: tuple[ConfiguredQuantumRoute, ...],
    *,
    rf_outputs: tuple[ConfiguredRfOutput, ...],
    settings: _ConfigurationModel,
    signal_calibrations: dict[tuple[str, str], _SignalCalibration],
    iq_chain_calibrations: dict[str, _IqChainCalibration],
    lo_calibrations: dict[str, _LoCalibration],
) -> tuple[IqOutputBinding, ...]:
    selected: dict[
        tuple[str, str, str], tuple[OutputSignal, list[ConfiguredQuantumRoute]]
    ] = {}
    for route in routes:
        signal = configured_output_signal(route)
        if signal is None:
            continue
        key = signal_key(signal)
        bound_signal, selected_routes = selected.setdefault(key, (signal, []))
        selected_routes.append(route)
        selected[key] = (bound_signal, selected_routes)

    bindings: list[IqOutputBinding] = []
    for signal, signal_routes in selected.values():
        kind, _, entity_id = signal_key(signal)
        output_kind: Literal["drive", "readout"] = (
            "drive" if kind == "drive" else "readout"
        )
        i_role, q_role = (
            (DRIVE_I_ROLE, DRIVE_Q_ROLE)
            if kind == "drive"
            else (READOUT_I_ROLE, READOUT_Q_ROLE)
        )
        i_route = _one_role(signal_routes, role=i_role, signal=signal)
        q_route = _one_role(signal_routes, role=q_role, signal=signal)
        if i_route.instrument_id != q_route.instrument_id:
            raise ValueError(
                f"IQ route {_signal_label(signal)!r} spans multiple AWGs: "
                f"{i_route.instrument_id!r}, {q_route.instrument_id!r}"
            )
        i_channel = _awg_channel(i_route)
        q_channel = _awg_channel(q_route)
        if i_channel == q_channel:
            raise ValueError(
                f"IQ route {_signal_label(signal)!r} maps I and Q to the same DAC"
            )
        calibration = _signal_calibration(
            signal_calibrations,
            kind=output_kind,
            entity_id=entity_id,
        )
        chain_id = _iq_chain_id(
            settings,
            i_channel_id=i_channel,
            q_channel_id=q_channel,
        )
        oscillator = _local_oscillator(
            rf_outputs,
            kind=output_kind,
            entity_id=entity_id,
        )
        try:
            lo_frequency = lo_calibrations[oscillator.group_id].frequency_hz
        except KeyError as error:
            raise ValueError(
                f"missing LO calibration for group {oscillator.group_id!r}"
            ) from error
        bindings.append(
            IqOutputBinding(
                signal=signal,
                iq_chain_id=chain_id,
                lo_group_id=oscillator.group_id,
                i_channel_id=i_channel,
                q_channel_id=q_channel,
                intermediate_frequency_hz=(
                    calibration.carrier_frequency_hz - lo_frequency
                ),
                mixer=iq_chain_calibrations[chain_id].mixer,
            )
        )
    return tuple(bindings)


def _configured_acquisition_bindings(
    routes: tuple[ConfiguredQuantumRoute, ...],
    *,
    output_bindings: tuple[IqOutputBinding, ...],
) -> tuple[AcquisitionBinding, ...]:
    readout_if_by_entity = {
        signal_key(binding.signal)[2]: binding.intermediate_frequency_hz
        for binding in output_bindings
        if signal_key(binding.signal)[0] == "readout"
    }
    bindings: list[AcquisitionBinding] = []
    seen_signals: set[tuple[str, str, str]] = set()
    for route in routes:
        signal = configured_acquisition_signal(route)
        if signal is None:
            continue
        key = signal_key(signal)
        if key in seen_signals:
            raise ValueError(
                f"acquisition signal {_signal_label(signal)!r} has two routes"
            )
        seen_signals.add(key)
        if not route.component_path:
            raise ValueError(
                f"acquisition signal {_signal_label(signal)!r} has no ADC "
                "component path"
            )
        entity_id = key[2]
        bindings.append(
            AcquisitionBinding(
                signal=signal,
                input_id=DigitizerInputId(
                    value=f"{route.instrument_id}:{'/'.join(route.component_path)}",
                    instrument_id=route.instrument_id,
                    component_path=route.component_path,
                ),
                demodulator_slot_id=DemodulatorSlotId(route.channel_id),
                demodulation_frequency_hz=readout_if_by_entity[entity_id],
            )
        )
    return tuple(bindings)


def _one_role(
    routes: list[ConfiguredQuantumRoute],
    *,
    role: str,
    signal: OutputSignal,
) -> ConfiguredQuantumRoute:
    selected = [route for route in routes if route.role_id == role]
    if len(selected) != 1:
        raise ValueError(
            f"IQ route {_signal_label(signal)!r} requires exactly one {role!r} "
            f"endpoint; found {len(selected)}"
        )
    [route] = selected
    if not route.component_path:
        raise ValueError(
            f"IQ route {_signal_label(signal)!r} {role!r} endpoint has no DAC path"
        )
    return route


def _awg_channel(route: ConfiguredQuantumRoute) -> AwgChannelId:
    return AwgChannelId(
        value=route.endpoint_id,
        instrument_id=route.instrument_id,
        component_path=route.component_path,
    )


def _signal_calibration(
    calibrations: dict[tuple[str, str], _SignalCalibration],
    *,
    kind: Literal["drive", "readout"],
    entity_id: str,
) -> _SignalCalibration:
    try:
        return calibrations[(kind, entity_id)]
    except KeyError as error:
        raise ValueError(
            f"missing {kind} signal calibration for entity {entity_id!r}"
        ) from error


def _local_oscillator(
    outputs: tuple[ConfiguredRfOutput, ...],
    *,
    kind: Literal["drive", "readout"],
    entity_id: str,
) -> ConfiguredRfOutput:
    selected = [
        output
        for output in outputs
        if output.signal == kind and entity_id in output.entity_ids
    ]
    if len(selected) != 1:
        raise ValueError(
            f"{kind} signal for entity {entity_id!r} requires exactly one LO group; "
            f"found {len(selected)}"
        )
    return selected[0]


def _iq_chain_id(
    settings: _ConfigurationModel,
    *,
    i_channel_id: AwgChannelId,
    q_channel_id: AwgChannelId,
) -> str:
    selected = [
        chain
        for chain in settings.iq_chains
        if chain.i_channel_id == i_channel_id.value
        and chain.q_channel_id == q_channel_id.value
    ]
    if len(selected) != 1:
        raise ValueError(
            f"physical IQ pair {i_channel_id.value!r}, {q_channel_id.value!r} "
            f"requires exactly one calibration chain; found {len(selected)}"
        )
    return selected[0].chain_id


def _output_preparations(
    bindings: tuple[IqOutputBinding, ...],
) -> tuple[OutputChannelPreparation, ...]:
    channels = {
        channel_id for binding in bindings for channel_id in binding.channel_ids
    }
    return tuple(
        OutputChannelPreparation(
            channel_id=channel_id,
            amplitude_v=1.0,
        )
        for channel_id in sorted(channels)
    )


def _offset_requirements_by_chain(
    bindings: tuple[IqOutputBinding, ...],
) -> dict[str, tuple[OutputOffsetRequirement, ...]]:
    grouped: dict[str, list[OutputOffsetRequirement]] = {}
    for binding in bindings:
        grouped.setdefault(binding.iq_chain_id, []).extend(
            (
                OutputOffsetRequirement(
                    channel_id=binding.i_channel_id,
                    offset_v=binding.mixer.i_offset_v,
                ),
                OutputOffsetRequirement(
                    channel_id=binding.q_channel_id,
                    offset_v=binding.mixer.q_offset_v,
                ),
            )
        )
    return {chain_id: tuple(requirements) for chain_id, requirements in grouped.items()}


def _configured_output_slots(
    config: ConfigProfileSnapshot,
    *,
    slots: tuple[IqOffsetOutputSlot, ...],
) -> dict[str, AwgChannelId]:
    outputs: dict[str, AwgChannelId] = {}
    for slot in slots:
        slot_id = slot.id
        routes = tuple(
            route for route in config.routing.routes if route.role_id == slot.role_id
        )
        if len(routes) != 1:
            raise ValueError(
                f"IQ offset output slot {slot_id!r} requires exactly one route with "
                f"role {slot.role_id!r}; found {len(routes)}"
            )
        [route] = routes
        endpoints = tuple(
            endpoint
            for endpoint in route.endpoints
            if endpoint.interface_id == ANALOG_WAVEFORM_OUTPUT.interface_id
            and endpoint.channel_id is not None
        )
        if len(endpoints) != 1:
            raise ValueError(
                f"IQ offset output slot {slot_id!r} requires exactly one physical "
                f"AWG output endpoint; found {len(endpoints)}"
            )
        [endpoint] = endpoints
        outputs[slot_id] = AwgChannelId(
            value=f"{route.instrument_id}:{endpoint.channel_id}",
            instrument_id=route.instrument_id,
            component_path=tuple(endpoint.component_path),
        )
    return outputs


def _awg_output_baselines(config: ConfigProfileSnapshot) -> dict[str, float]:
    table = config.parameter_snapshot.get(sc.parameter_table_name(AwgOutputBaseline))
    if not isinstance(table, TableParameterValue):
        raise ValueError("AWG output baseline table is missing")
    return {
        cast("str", row[AwgOutputBaseline.slot.name]): _quantity_value(
            row[AwgOutputBaseline.offset.name], "V"
        )
        for row in table.rows
    }


def _required_output_slot(
    slot_id: str,
    *,
    output_slots: dict[str, AwgChannelId],
) -> AwgChannelId:
    try:
        return output_slots[slot_id]
    except KeyError as error:
        raise ValueError(
            f"IQ offset policy references unresolved output slot {slot_id!r}"
        ) from error


def _required_output_baseline(
    slot_id: str,
    *,
    output_baselines: dict[str, float],
) -> float:
    try:
        return output_baselines[slot_id]
    except KeyError as error:
        raise ValueError(
            f"IQ offset policy has no reviewed baseline for output slot {slot_id!r}"
        ) from error


def _signal_calibrations(
    config: ConfigProfileSnapshot,
) -> dict[tuple[str, str], _SignalCalibration]:
    carriers = _carrier_frequencies(config)
    return {
        (signal, entity_id): _SignalCalibration(
            carrier_frequency_hz=carriers[(signal, entity_id)],
        )
        for signal, entity_id in carriers
    }


def _iq_chain_calibrations(
    config: ConfigProfileSnapshot,
) -> dict[str, _IqChainCalibration]:
    table = config.parameter_snapshot.get(sc.parameter_table_name(IqChainParameters))
    if not isinstance(table, TableParameterValue):
        raise ValueError("IQ chain calibration table is missing")
    return {
        cast("str", row[IqChainParameters.chain.name]): _IqChainCalibration(
            mixer=IqMixerCalibration(
                ii=cast("float", row[IqChainParameters.mixer_ii.name]),
                iq=cast("float", row[IqChainParameters.mixer_iq.name]),
                qi=cast("float", row[IqChainParameters.mixer_qi.name]),
                qq=cast("float", row[IqChainParameters.mixer_qq.name]),
                i_offset_v=_quantity_value(
                    row[IqChainParameters.mixer_i_offset.name], "V"
                ),
                q_offset_v=_quantity_value(
                    row[IqChainParameters.mixer_q_offset.name], "V"
                ),
            )
        )
        for row in table.rows
    }


def _carrier_frequencies(config: ConfigProfileSnapshot) -> dict[tuple[str, str], float]:
    qubits = config.parameter_snapshot.get(sc.parameter_table_name(QubitParameters))
    resonators = config.parameter_snapshot.get(
        sc.parameter_table_name(ReadoutResonator)
    )
    if not isinstance(qubits, TableParameterValue):
        raise ValueError("qubit calibration table is missing")
    if not isinstance(resonators, TableParameterValue):
        raise ValueError("readout resonator calibration table is missing")
    return {
        **{
            (
                "drive",
                cast("EntityRef", row[QubitParameters.qubit.name]).id,
            ): _quantity_value(row[QubitParameters.drive_carrier_frequency.name], "Hz")
            for row in qubits.rows
        },
        **{
            (
                "readout",
                cast("EntityRef", row[ReadoutResonator.resonator.name]).id,
            ): _quantity_value(row[ReadoutResonator.resonance_frequency.name], "Hz")
            for row in resonators.rows
        },
    }


def _lo_calibrations(
    config: ConfigProfileSnapshot,
) -> dict[str, _LoCalibration]:
    table = config.parameter_snapshot.get(sc.parameter_table_name(LoGroupParameters))
    if not isinstance(table, TableParameterValue):
        raise ValueError("LO group calibration table is missing")
    return {
        cast("str", row[LoGroupParameters.group.name]): _LoCalibration(
            frequency_hz=_quantity_value(row[LoGroupParameters.frequency.name], "Hz")
        )
        for row in table.rows
    }


def _quantity_value(value: object, unit: str) -> float:
    return float(cast("Quantity", value).to(unit).value)


def _signal_label(signal: OutputSignal | AcquireSignal) -> str:
    return "/".join(signal_key(signal))


__all__ = ["configured_list_mode_target"]
