"""A configured signal model with one declaration for fixed and scanned controls."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Annotated, cast

import scopecat as sc
from scopecat.program.scans import ValuesScanSource
from scopecat.records.parameter import TableParameterValue

from reference_lab.parameters import DRIVE_CARRIER_FREQUENCY, Q0, QUBIT, QUBITS

FREQUENCY = sc.Control(
    "frequency",
    default=sc.Quantity(4.8, "GHz"),
    minimum=4.5,
    maximum=5.5,
    title="Frequency",
    group="Signal",
    scannable=True,
    provenance="Reference model operating frequency",
)
AMPLITUDE = sc.Control(
    "amplitude",
    default=sc.Quantity(0.1, "V"),
    minimum=0,
    maximum=0.5,
    title="Amplitude",
    group="Signal",
    scannable=True,
    provenance="Reference model low-amplitude default",
)


def configured_carrier(context: sc.ControlValidationContext) -> sc.Quantity:
    table = context.config.parameter_snapshot.get(QUBITS.id)
    assert isinstance(table, TableParameterValue)
    [row] = [row for row in table.rows if row[QUBIT.id] == Q0.key[0].value]
    value = row[DRIVE_CARRIER_FREQUENCY.id]
    assert isinstance(value, float | sc.Quantity)
    selected = DRIVE_CARRIER_FREQUENCY.value(value).value
    assert isinstance(selected, sc.Quantity)
    return selected.to("GHz")


def maximum_detuning(context: sc.ControlValidationContext) -> sc.Quantity:
    reference = configured_carrier(context).value
    values = FREQUENCY.axis_values(context.axis(FREQUENCY.id))
    return sc.Quantity(
        max(
            abs(value.value - reference)
            for value in values
            if isinstance(value, sc.Quantity)
        ),
        "GHz",
    )


def validate_signal_controls(context: sc.ControlValidationContext) -> None:
    """Project limits inspect per-axis extrema, not every Cartesian combination."""
    amplitudes = AMPLITUDE.axis_values(context.axis(AMPLITUDE.id))
    peak = max(value.value for value in amplitudes if isinstance(value, sc.Quantity))
    if maximum_detuning(context).value > 0.25 and peak > 0.2:
        raise ValueError("Amplitude must be at most 0.2 V beyond 0.25 GHz detuning")
    count = math.prod(
        len(axis.source.values)
        if isinstance(axis.source, ValuesScanSource)
        else axis.source.points
        for axis in context.axes
    )
    count *= context.invocation.point_plan.repeat
    if count > 64:
        raise ValueError("The maintained reference model allows at most 64 grid points")


CONTROLS = sc.ControlSet(
    (
        FREQUENCY,
        AMPLITUDE,
        sc.Control(
            "reference_frequency",
            unit="GHz",
            title="Reviewed carrier",
            group="Configuration",
            ownership="configuration",
            resolve=configured_carrier,
            provenance=(
                "Accepted qubits[q0].drive_carrier_frequency; "
                "also used by the signal compute"
            ),
        ),
        sc.Control(
            "maximum_detuning",
            unit="GHz",
            title="Maximum detuning",
            group="Derived",
            ownership="derived",
            resolve=maximum_detuning,
            provenance="Maximum requested frequency offset from the reviewed carrier",
        ),
    ),
    validator=validate_signal_controls,
)


def signal_model(
    frequency: sc.Quantity,
    amplitude: sc.Quantity,
    reference: sc.Quantity,
) -> Annotated[sc.Quantity, sc.ScalarType(sc.QuantityType(unit="V"))]:
    """Evaluate against the same configured carrier shown in preview."""
    phase = 2 * math.pi * (frequency.to("GHz").value - reference.to("GHz").value)
    return sc.Quantity(amplitude.to("V").value * math.cos(phase), "V")


@dataclass(frozen=True, slots=True)
class SignalDataset:
    frequency: sc.CoordinateRef[object]
    amplitude: sc.CoordinateRef[object]
    response: sc.ProductRef[float]


@sc.experiment(id="reference_lab.frequency_amplitude", controls=CONTROLS)
def frequency_amplitude(context: sc.ExperimentContext) -> SignalDataset:
    """Run a configured analytic signal model; this workflow needs no device calls."""
    response = cast(
        "sc.ProductRef[float]",
        context.compute(
            "response",
            fn=signal_model,
            inputs={
                "frequency": FREQUENCY.ref,
                "amplitude": AMPLITUDE.ref,
                "reference": Q0[DRIVE_CARRIER_FREQUENCY].ref,
            },
        ),
    )
    return SignalDataset(FREQUENCY.ref, AMPLITUDE.ref, response)
