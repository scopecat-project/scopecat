"""A synthetic peak with labeled coordinates and explicit configured center."""

import math
from typing import Annotated

import scopecat as sc
from scopecat.records.parameter import TableParameterValue


class SignalParameters(sc.ParameterModel, table="signal"):
    id: sc.Param[str] = sc.param(key=True)
    center: sc.Magnitude[float] = sc.quantity(unit="GHz")


FREQUENCY = sc.Control(
    "frequency", default=sc.Quantity(4.8, "GHz"), title="Frequency", scannable=True
)
AMPLITUDE = sc.Control(
    "amplitude", default=sc.Quantity(0.1, "V"), title="Amplitude", scannable=True
)


def configured_center(context: sc.ControlValidationContext) -> sc.Quantity:
    table = context.config.parameter_snapshot.get("signal")
    assert isinstance(table, TableParameterValue)
    [row] = [row for row in table.rows if row["id"] == "signal"]
    value = row["center"]
    assert isinstance(value, sc.Quantity)
    return value.to("GHz")


def maximum_detuning(context: sc.ControlValidationContext) -> sc.Quantity:
    center = configured_center(context).value
    values = FREQUENCY.axis_values(context.axis(FREQUENCY.id))
    return sc.Quantity(
        max(
            abs(value.to("GHz").value - center)
            for value in values
            if isinstance(value, sc.Quantity)
        ),
        "GHz",
    )


CONTROLS = sc.ControlSet(
    (
        FREQUENCY,
        AMPLITUDE,
        sc.Control(
            "reference_frequency",
            unit="GHz",
            title="Reviewed center",
            ownership="configuration",
            resolve=configured_center,
            provenance="Explicit signal[signal].center",
        ),
        sc.Control(
            "maximum_detuning",
            unit="GHz",
            title="Maximum detuning",
            ownership="derived",
            resolve=maximum_detuning,
            provenance="Requested frequency offset from the reviewed center",
        ),
    )
)


def response(
    frequency: sc.Quantity, amplitude: sc.Quantity, center: sc.Quantity
) -> Annotated[sc.Quantity, sc.ScalarType(sc.QuantityType(unit="V"))]:
    offset = frequency.to("GHz").value - center.to("GHz").value
    return sc.Quantity(amplitude.to("V").value * math.cos(2 * math.pi * offset), "V")


@sc.experiment(id="ui_signal.signal", controls=CONTROLS)
def signal(context: sc.ExperimentContext):
    return context.compute(
        "response",
        fn=response,
        inputs={
            "frequency": FREQUENCY.ref,
            "amplitude": AMPLITUDE.ref,
            "center": sc.parameter_ref(SignalParameters.center, "signal"),
        },
    )
