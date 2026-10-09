"""A synthetic peak with labeled coordinates and explicit configured center."""

import math
from typing import Annotated

import scopecat as sc


class SignalParameters(sc.ParameterModel, table="signal"):
    id: sc.Param[str] = sc.param(key=True)
    center: sc.Magnitude[float] = sc.quantity(unit="GHz")


FREQUENCY = sc.Control(
    "frequency", default=sc.Quantity(4.8, "GHz"), title="Frequency", scannable=True
)
AMPLITUDE = sc.Control(
    "amplitude", default=sc.Quantity(0.1, "V"), title="Amplitude", scannable=True
)


def response(
    frequency: sc.Quantity, amplitude: sc.Quantity, center: sc.Quantity
) -> Annotated[sc.Quantity, sc.ScalarType(sc.QuantityType(unit="V"))]:
    offset = frequency.to("GHz").value - center.to("GHz").value
    return sc.Quantity(amplitude.to("V").value * math.cos(2 * math.pi * offset), "V")


@sc.experiment(id="ui_signal.signal", controls=sc.ControlSet((FREQUENCY, AMPLITUDE)))
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
