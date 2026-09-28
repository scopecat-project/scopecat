"""Exercise coupled virtual devices through the normal notebook API."""

from __future__ import annotations

from pathlib import Path

import scopecat as sc
from scopecat_instruments import (
    DCSourcePatch,
    dc_source,
    network_sweep,
    temperature_readout,
)

from reference_lab.configuration import bootstrap_config
from reference_lab.notebook import show

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FLUX_SOURCE = dc_source("bench-source")
MIXING_CHAMBER = temperature_readout("mixing-chamber")
READOUT_VNA = network_sweep("readout-vna")


# %%
with sc.open_project(PROJECT_ROOT).connect(operator="notebook-demo") as lab:
    setup = lab.setup.save(bootstrap_config(), name="direct-control").ref
    inventory = [
        (item.instrument_id, item.availability)
        for item in lab.instruments.list(setup=setup).items
    ]

    with lab.instruments.open(
        FLUX_SOURCE,
        MIXING_CHAMBER,
        READOUT_VNA,
        setup=setup,
    ) as devices:
        source = devices[FLUX_SOURCE]
        chamber = devices[MIXING_CHAMBER]
        vna = devices[READOUT_VNA]

        source.source_voltage(
            range=sc.Quantity(1.0, "V"),
            level=sc.Quantity(0.05, "V"),
        )
        source.apply(output_enabled=True)
        try:
            temperature = chamber.sample()
            vna.apply(
                start_frequency=sc.Quantity(4.8, "GHz"),
                stop_frequency=sc.Quantity(5.2, "GHz"),
                points=201,
            )
            trace = vna.sweep()
            trace_results = {
                "frequency": trace.frequency.model_dump(
                    mode="json",
                    include={"shape"},
                ),
                "s_parameter": trace.s_parameter.model_dump(
                    mode="json",
                    include={"shape"},
                ),
            }
        finally:
            source.apply(DCSourcePatch(output_enabled=False))

direct_control_summary = {
    "inventory": inventory,
    "temperature": {
        "status": temperature.receipt.status,
        "temperature": temperature.temperature,
        "resistance": temperature.resistance,
    },
    "trace": {
        "status": trace.receipt.status,
        "results": trace_results,
    },
}
show(direct_control_summary)
