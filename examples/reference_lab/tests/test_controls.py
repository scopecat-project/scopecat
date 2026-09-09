"""Project constraints apply to direct invocation and typed control form edits."""

from __future__ import annotations

import pytest
import scopecat as sc
from scopecat.application.controls import edit_controls
from scopecat.planning.catalog import InstrumentContractCatalog
from scopecat.planning.service import plan_experiment_invocation
from scopecat.planning.system import ExperimentSystem
from scopecat.records.config import config_content_hash
from scopecat.records.control_edit import ControlEdit
from scopecat.records.run_request import AxisRangeSourceRecord

from reference_lab.configuration import bootstrap_config
from reference_lab.workflows.frequency_amplitude import (
    AMPLITUDE,
    CONTROLS,
    FREQUENCY,
    frequency_amplitude,
)


def test_project_constraints_reject_direct_and_form_edits_before_planning() -> None:
    config = bootstrap_config()
    invocation = (
        frequency_amplitude()
        .with_axis(sc.axis(FREQUENCY.ref, [sc.Quantity(5.4, "GHz")]))
        .with_axis(sc.axis(AMPLITUDE.ref, [sc.Quantity(0.3, "V")]))
    )
    system = ExperimentSystem(
        InstrumentContractCatalog(config_content_hash=config_content_hash(config))
    )
    with pytest.raises(ValueError, match="Amplitude"):
        plan_experiment_invocation(invocation, config=config, system=system)
    with pytest.raises(ValueError, match="Amplitude"):
        edit_controls(
            CONTROLS,
            frequency_amplitude(),
            config=config,
            edits={
                "frequency": ControlEdit(mode="fixed", value=sc.Quantity(5400, "MHz")),
                "amplitude": ControlEdit(mode="fixed", value=sc.Quantity(300, "mV")),
            },
        )
    with pytest.raises(ValueError, match="64"):
        edit_controls(
            CONTROLS,
            frequency_amplitude(),
            config=config,
            edits={
                "frequency": ControlEdit(
                    mode="scan",
                    axis=AxisRangeSourceRecord(
                        start=sc.Quantity(4.7, "GHz"),
                        stop=sc.Quantity(4.9, "GHz"),
                        points=65,
                    ),
                )
            },
        )

    with pytest.raises(ValueError, match="64"):
        plan_experiment_invocation(
            frequency_amplitude().with_repeat(65), config=config, system=system
        )
