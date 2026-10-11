from __future__ import annotations

import scopecat as sc
from scopecat.records.config import InstrumentBindingSpec, VirtualInstrumentConnection
from scopecat.records.instrument import CommandChannelBinding
from scopecat.sdk.instruments import (
    DriverStateAssignment,
    DriverStatePatch,
    DriverStateReadRequest,
    DriverSuccess,
    InstrumentConnectionContext,
    InstrumentProviderContext,
    InterfaceRef,
)
from scopecat_instruments.members import DC_BIAS_TARGET_VOLTAGE
from scopecat_instruments.virtual import VirtualLabWorld

from reference_lab.provider import (
    MultiChannelVirtualDcSource,
    ReferenceLabProvider,
)


def test_virtual_provider_catalog_and_connection_use_exact_bindings() -> None:
    provider = ReferenceLabProvider()
    binding = InstrumentBindingSpec(
        id="drive-awg",
        driver_id="reference_lab.virtual.awg",
        connection=VirtualInstrumentConnection(),
    )

    described = provider.describe(InstrumentProviderContext(bindings=(binding,)))
    connected = provider.connect(InstrumentConnectionContext(binding=binding))

    assert [item.instrument_id for item in described.instruments] == ["drive-awg"]
    assert connected.instrument_id == "drive-awg"
    assert connected.implementation_id == binding.driver_id
    connected.disconnect()


def test_multichannel_driver_dispatches_by_component_with_shared_provenance() -> None:
    world = VirtualLabWorld(seed=7)
    driver = MultiChannelVirtualDcSource("flux-dac", world)
    target = (
        InterfaceRef(DC_BIAS_TARGET_VOLTAGE.interface_id)
        .component("channels")
        .component("ch1")
        .property(DC_BIAS_TARGET_VOLTAGE.property_id)
    )
    bindings = (
        CommandChannelBinding(
            entity_id="q0",
            channel_id="flux.ch1.primary",
            interface_id=target.interface_id,
        ),
        CommandChannelBinding(
            entity_id="coupler0",
            channel_id="flux.ch1.secondary",
            interface_id=target.interface_id,
        ),
    )

    outcome = driver.apply_state(
        DriverStatePatch(
            scoped_values=(
                DriverStateAssignment(
                    target=target,
                    value=sc.Quantity(0.125, "V"),
                    entity_ids=("q0", "coupler0"),
                    channel_bindings=bindings,
                ),
            )
        )
    )

    assert isinstance(outcome, DriverSuccess)
    assert world.dc_source("flux-dac:flux.ch1.primary").voltage_level_v == 0.125
    readback = driver.read_state(DriverStateReadRequest(frozenset({target})))
    state_entry = next(
        entry for entry in readback.observations if entry.target == target
    )
    assert state_entry.entity_ids == ("q0", "coupler0")
    assert {binding.channel_id for binding in state_entry.channel_bindings} == {
        "flux.ch1.primary",
        "flux.ch1.secondary",
    }
