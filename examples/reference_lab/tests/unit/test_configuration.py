from __future__ import annotations

import pytest
import scopecat as sc
from scopecat.config.parameter_resolution import validate_parameter_snapshot
from scopecat.kernel.entity import EntityRef
from scopecat.kernel.quantity import Quantity
from scopecat.planning.catalog import InstrumentContractCatalog
from scopecat.planning.provider_binding import resolve_instrument_contract_catalog
from scopecat.records.config import ConfigProfileSnapshot, ResourceRoute
from scopecat.records.parameter import (
    TableParameterValue,
)
from scopecat_quantum._ids import QubitId
from scopecat_quantum.pulses import DriveSignal

from reference_lab.bench_interfaces import (
    DIGITIZER_FETCH_PROGRAM_IQ,
    DIGITIZER_INPUT,
    TRIGGER_COORDINATOR,
    TRIGGER_START_PROGRAM_IDEMPOTENT,
)
from reference_lab.configuration import bootstrap_config
from reference_lab.parameters import IqChainParameters, LoGroupParameters
from reference_lab.physical_policies import (
    DRIVE_AWG_OFFSET_GUARD,
    REFERENCE_IQ_OFFSET_POLICY,
)
from reference_lab.provider import ReferenceLabProvider
from reference_lab.targets.configuration import (
    DRIVE_LO_ROLE,
    DRIVE_Q_ROLE,
    LIST_MODE_TARGET_KIND,
    configured_rf_outputs,
)
from reference_lab.targets.list_mode import configured_list_mode_target


def _instrument_catalog(config: ConfigProfileSnapshot) -> InstrumentContractCatalog:
    provider = ReferenceLabProvider()
    return resolve_instrument_contract_catalog(
        config=config,
        provider_id=provider.provider_id,
        describe=provider.describe,
    )


def _configured_target(config: ConfigProfileSnapshot):
    return configured_list_mode_target(config, _instrument_catalog(config))


def _with_dsp_policy(
    config: ConfigProfileSnapshot,
    policy: str,
) -> ConfigProfileSnapshot:
    target = config.domain_target
    assert target is not None
    configuration = target.configuration.copy()
    capabilities = configuration["capabilities"]
    assert isinstance(capabilities, dict)
    capabilities = capabilities.copy()
    capabilities["acquisition_dsp_policy"] = policy
    configuration["capabilities"] = capabilities
    return config.model_copy(
        update={
            "system": config.system.model_copy(
                update={
                    "domain_target": target.model_copy(
                        update={"configuration": configuration}
                    )
                }
            )
        }
    )


def _without_onboard_dsp(
    catalog: InstrumentContractCatalog,
) -> InstrumentContractCatalog:
    instruments = tuple(
        description.model_copy(
            update={
                "interfaces": [
                    interface.model_copy(
                        update={
                            "acquisitions": [
                                acquisition
                                for acquisition in interface.acquisitions
                                if acquisition.id
                                != DIGITIZER_FETCH_PROGRAM_IQ.acquisition_id
                            ]
                        }
                    )
                    if interface.id == DIGITIZER_INPUT.interface_id
                    else interface
                    for interface in description.interfaces
                ]
            }
        )
        if description.instrument_id == "readout-digitizer"
        else description
        for description in catalog.instruments
    )
    return catalog.model_copy(update={"instruments": instruments})


def _with_trigger_policy(
    config: ConfigProfileSnapshot,
    policy: str,
) -> ConfigProfileSnapshot:
    target = config.domain_target
    assert target is not None
    configuration = target.configuration.copy()
    timing = configuration["timing"]
    assert isinstance(timing, dict)
    timing = timing.copy()
    timing["trigger_policy"] = policy
    configuration["timing"] = timing
    return config.model_copy(
        update={
            "system": config.system.model_copy(
                update={
                    "domain_target": target.model_copy(
                        update={"configuration": configuration}
                    )
                }
            )
        }
    )


def _without_idempotent_program_start(
    catalog: InstrumentContractCatalog,
) -> InstrumentContractCatalog:
    instruments = tuple(
        description.model_copy(
            update={
                "interfaces": [
                    interface.model_copy(
                        update={
                            "operations": [
                                operation
                                for operation in interface.operations
                                if operation.id
                                != TRIGGER_START_PROGRAM_IDEMPOTENT.operation_id
                            ]
                        }
                    )
                    if interface.id == TRIGGER_COORDINATOR.interface_id
                    else interface
                    for interface in description.interfaces
                ]
            }
        )
        if description.instrument_id == "timing-controller"
        else description
        for description in catalog.instruments
    )
    return catalog.model_copy(update={"instruments": instruments})


def test_bootstrap_config_provides_valid_drag_compiler_parameters() -> None:
    config = bootstrap_config()

    assert (
        validate_parameter_snapshot(
            config.parameter_catalog,
            config.parameter_snapshot,
        )
        == ()
    )
    qubits = config.parameter_snapshot.get("qubits")
    assert isinstance(qubits, TableParameterValue)
    q0 = next(
        row
        for row in qubits.rows
        if row["qubit"] == EntityRef(id="q0", kind="logical_qubit")
    )
    assert q0["drag_beta"] == Quantity(value=0.5, unit="ns")
    assert q0["quarter_turn_duration"] == Quantity(value=16, unit="ns")
    assert q0["quarter_turn_amplitude"] == Quantity(value=0.2, unit="arb")


def test_target_configuration_keeps_topology_separate_from_calibration() -> None:
    config = bootstrap_config()
    domain_target = config.domain_target
    assert domain_target is not None
    chains = domain_target.configuration["iq_chains"]
    offset_policy = domain_target.configuration["iq_offset_policy"]

    assert "local_oscillators" not in domain_target.configuration
    assert isinstance(chains, list)
    assert all(
        isinstance(chain, dict)
        and set(chain) == {"chain_id", "i_channel_id", "q_channel_id"}
        for chain in chains
    )
    assert offset_policy == {
        "policy_id": "reference_lab.iq-offset.coupling-groups.v2",
    }
    assert [
        group.id
        for group in _configured_target(config).host_state_policy.coupling_groups
    ] == [group.id for group in REFERENCE_IQ_OFFSET_POLICY.coupling_groups]
    assert isinstance(
        config.parameter_snapshot.get(sc.parameter_table_name(IqChainParameters)),
        TableParameterValue,
    )
    assert isinstance(
        config.parameter_snapshot.get(sc.parameter_table_name(LoGroupParameters)),
        TableParameterValue,
    )


def test_iq_guard_slot_resolves_physical_output_only_through_routing() -> None:
    config = bootstrap_config()
    routes = [
        route.model_copy(
            update={
                "endpoints": [
                    endpoint.model_copy(
                        update={
                            "channel_id": "drive.awg0.ch10",
                            "component_path": ("outputs", "ch10"),
                        }
                    )
                    for endpoint in route.endpoints
                ]
            }
        )
        if route.role_id == DRIVE_AWG_OFFSET_GUARD.role_id
        else route
        for route in config.routing.routes
    ]
    changed = config.model_copy(
        update={
            "system": config.system.model_copy(
                update={"routing": config.routing.model_copy(update={"routes": routes})}
            )
        }
    )

    target = _configured_target(changed)
    [guard] = [
        requirement
        for group in target.host_state_policy.coupling_groups
        for requirement in group.output_offsets
        if requirement.channel_id.component_path == ("outputs", "ch10")
    ]

    assert guard.channel_id.value == "drive-awg:drive.awg0.ch10"
    assert guard.offset_v == 0.007


def test_target_rejects_an_unknown_lab_iq_policy() -> None:
    config = bootstrap_config()
    target = config.domain_target
    assert target is not None
    configuration = target.configuration.copy()
    configuration["iq_offset_policy"] = {"policy_id": "reference_lab.unknown"}
    changed = config.model_copy(
        update={
            "system": config.system.model_copy(
                update={
                    "domain_target": target.model_copy(
                        update={"configuration": configuration}
                    )
                }
            )
        }
    )

    with pytest.raises(ValueError, match="unsupported IQ offset policy"):
        _configured_target(changed)


def test_list_mode_target_owns_only_real_time_members() -> None:
    config = bootstrap_config()
    domain_target = config.domain_target
    assert domain_target is not None
    assert domain_target.instrument_ids == [
        "drive-awg",
        "readout-awg",
        "readout-digitizer",
        "timing-controller",
    ]

    target = _configured_target(config)
    assert len(target.output_bindings) == 8
    assert len(target.acquisition_bindings) == 4
    assert target.preparation.timing.trigger_instrument_id == "timing-controller"
    assert all(
        binding.i_channel_id != binding.q_channel_id
        for binding in target.output_bindings
    )
    drive_ifs = {
        binding.signal.qubit.value: binding.intermediate_frequency_hz
        for binding in target.output_bindings
        if isinstance(binding.signal, DriveSignal)
    }
    drive_lo_groups = {
        binding.signal.qubit.value: binding.lo_group_id
        for binding in target.output_bindings
        if isinstance(binding.signal, DriveSignal)
    }
    assert drive_ifs == {
        "q0": -50.0e6,
        "q1": 50.0e6,
        "q2": -50.0e6,
        "q3": 50.0e6,
    }
    assert drive_lo_groups == {
        "q0": "drive-a",
        "q1": "drive-a",
        "q2": "drive-b",
        "q3": "drive-b",
    }
    assert {binding.input_id for binding in target.acquisition_bindings} == {
        target.acquisition_bindings[0].input_id
    }
    assert (
        len({binding.demodulator_slot_id for binding in target.acquisition_bindings})
        == 4
    )
    assert target.max_list_entries == 256
    assert target.max_program_waveform_bytes == 48 * 1024 * 1024
    assert target.max_program_event_count == 256 * 1024
    assert target.max_program_acquisition_count == 64 * 1024
    assert target.max_result_bytes == 512 * 1024 * 1024
    assert target.max_result_chunk_bytes == 8 * 1024 * 1024


def test_acquisition_dsp_policy_selects_only_advertised_lowerings() -> None:
    config = bootstrap_config()
    target = config.domain_target
    assert target is not None
    capabilities = target.configuration["capabilities"]
    assert isinstance(capabilities, dict)
    assert capabilities["acquisition_dsp_policy"] == "prefer_device"
    full_catalog = _instrument_catalog(config)

    selected = configured_list_mode_target(config, full_catalog)

    assert selected.digitizer_result_representation == "integrated_iq"

    limited_catalog = _without_onboard_dsp(full_catalog)
    selected = configured_list_mode_target(config, limited_catalog)

    assert selected.digitizer_result_representation == "raw_trace"

    require_device = _with_dsp_policy(config, "device")
    with pytest.raises(ValueError, match="requires onboard integrated-IQ"):
        configured_list_mode_target(
            require_device,
            _without_onboard_dsp(_instrument_catalog(require_device)),
        )


def test_target_configuration_cannot_exceed_its_instrument_authority() -> None:
    config = bootstrap_config()
    target = config.domain_target
    assert target is not None
    changed_target = target.model_copy(
        update={
            "instrument_ids": [
                instrument_id
                for instrument_id in target.instrument_ids
                if instrument_id != "timing-controller"
            ]
        }
    )
    changed = config.model_copy(
        update={
            "system": config.system.model_copy(update={"domain_target": changed_target})
        }
    )

    with pytest.raises(ValueError, match="outside its authority: timing-controller"):
        _configured_target(changed)


def test_trigger_policy_selects_an_explicit_guarantee() -> None:
    config = bootstrap_config()
    selected = _configured_target(config)

    assert selected.preparation.timing.program_start_guarantee == "session_idempotent"

    non_idempotent = _with_trigger_policy(
        config,
        "allow_non_idempotent_program_start",
    )
    selected = configured_list_mode_target(
        non_idempotent,
        _without_idempotent_program_start(_instrument_catalog(non_idempotent)),
    )

    assert selected.preparation.timing.program_start_guarantee == "non_idempotent"

    with pytest.raises(ValueError, match="requires session-idempotent"):
        configured_list_mode_target(
            config,
            _without_idempotent_program_start(_instrument_catalog(config)),
        )


def test_list_mode_target_reports_incomplete_iq_pair_by_signal() -> None:
    config = bootstrap_config()
    routes: list[ResourceRoute] = []
    for route in config.routing.routes:
        if route.role_id != DRIVE_Q_ROLE:
            routes.append(route)
            continue
        routes.append(
            route.model_copy(
                update={
                    "endpoints": [
                        endpoint
                        for endpoint in route.endpoints
                        if not (
                            endpoint.entity_id == "q0"
                            and endpoint.channel_id is not None
                        )
                    ]
                }
            )
        )
    changed_system = config.system.model_copy(
        update={"routing": config.routing.model_copy(update={"routes": routes})}
    )
    changed = config.model_copy(update={"system": changed_system})

    with pytest.raises(
        ValueError,
        match=r"drive/qubit/q0.*exactly one 'drive-q' endpoint; found 0",
    ):
        _configured_target(changed)


def test_list_mode_target_reports_missing_lo_group() -> None:
    config = bootstrap_config()
    routes = [
        route.model_copy(
            update={
                "entity_ids": [
                    entity_id for entity_id in route.entity_ids if entity_id != "q0"
                ]
            }
        )
        if route.role_id == DRIVE_LO_ROLE and route.id == "drive-a"
        else route
        for route in config.routing.routes
    ]
    changed = config.model_copy(
        update={
            "system": config.system.model_copy(
                update={"routing": config.routing.model_copy(update={"routes": routes})}
            )
        }
    )

    with pytest.raises(
        ValueError,
        match=r"drive signal for entity 'q0' requires exactly one LO group; found 0",
    ):
        _configured_target(changed)


def test_lab_rf_routing_retains_component_scope_outside_domain_target() -> None:
    config = bootstrap_config()
    routes = [
        route.model_copy(
            update={
                "endpoints": [
                    endpoint.model_copy(
                        update={"component_path": ("outputs", "source1")}
                    )
                    for endpoint in route.endpoints
                ]
            }
        )
        if route.id == "drive-a"
        else route
        for route in config.routing.routes
    ]
    changed = config.model_copy(
        update={
            "system": config.system.model_copy(
                update={"routing": config.routing.model_copy(update={"routes": routes})}
            )
        }
    )

    outputs = configured_rf_outputs(changed, target_kind=LIST_MODE_TARGET_KIND)
    drive_a = next(output for output in outputs if output.group_id == "drive-a")

    assert drive_a.instrument_id == "drive-lo-a"
    assert drive_a.component_path == ("outputs", "source1")
    domain_target = changed.domain_target
    assert domain_target is not None
    assert "drive-lo-a" not in domain_target.instrument_ids
    _configured_target(changed)


def test_list_mode_target_resolves_lo_and_mixer_from_reviewed_parameters() -> None:
    config = bootstrap_config()
    lo_table = config.parameter_snapshot.get(sc.parameter_table_name(LoGroupParameters))
    mixer_table = config.parameter_snapshot.get(
        sc.parameter_table_name(IqChainParameters)
    )
    assert isinstance(lo_table, TableParameterValue)
    assert isinstance(mixer_table, TableParameterValue)

    lo_rows = tuple(
        {**dict(row), LoGroupParameters.frequency.name: Quantity(4800000000.0, "Hz")}
        if row[LoGroupParameters.group.name] == "drive-a"
        else row
        for row in lo_table.rows
    )
    mixer_rows = tuple(
        {
            **dict(row),
            IqChainParameters.mixer_ii.name: 0.9,
            IqChainParameters.mixer_iq.name: 0.1,
            IqChainParameters.mixer_qi.name: -0.2,
            IqChainParameters.mixer_qq.name: 1.1,
            IqChainParameters.mixer_i_offset.name: Quantity(0.01, "V"),
            IqChainParameters.mixer_q_offset.name: Quantity(-0.02, "V"),
        }
        if row[IqChainParameters.chain.name] == "drive-q0"
        else row
        for row in mixer_table.rows
    )
    replacements = {
        sc.parameter_table_name(LoGroupParameters): lo_table.model_copy(
            update={"rows": lo_rows}
        ),
        sc.parameter_table_name(IqChainParameters): mixer_table.model_copy(
            update={"rows": mixer_rows}
        ),
    }
    snapshot = config.parameter_snapshot.model_copy(
        update={
            "values": tuple(
                replacements.get(value.id, value)
                for value in config.parameter_snapshot.values
            )
        }
    )
    changed = config.model_copy(update={"parameter_snapshot": snapshot})

    baseline = _configured_target(config)
    target = _configured_target(changed)
    signal = DriveSignal(QubitId("q0"))
    binding = next(
        binding for binding in target.output_bindings if binding.signal == signal
    )
    assert binding.intermediate_frequency_hz == 0.0
    assert binding.mixer.ii == 0.9
    assert binding.mixer.iq == 0.1
    assert binding.mixer.qi == -0.2
    assert binding.mixer.qq == 1.1
    outputs = {
        output.channel_id: output
        for group in target.host_state_policy.coupling_groups
        for output in group.output_offsets
    }
    assert outputs[binding.i_channel_id].offset_v == 0.01
    assert outputs[binding.q_channel_id].offset_v == -0.02
    assert binding.lo_group_id == "drive-a"
    assert target.capability_fingerprint == baseline.capability_fingerprint
    assert target.configuration_fingerprint != baseline.configuration_fingerprint
