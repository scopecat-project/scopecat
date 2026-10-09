"""Ordinary synthetic four-dimensional data for slice browsing."""

import math

import scopecat as sc

FREQUENCY = sc.Control(
    "frequency", default=sc.Quantity(4.8, "GHz"), title="Frequency", scannable=True
)
BIAS = sc.Control("bias", default=sc.Quantity(0.0, "V"), title="Bias", scannable=True)
REPEAT = sc.coordinate("repeat", sc.IntType())
MODE = sc.coordinate("mode", sc.StringType())


def response(
    frequency: sc.Quantity, bias: sc.Quantity, repeat: int, mode: str
) -> complex:
    x = frequency.to("GHz").value
    y = bias.to("V").value
    return complex(
        math.cos(x * 2) + y + repeat, math.sin(x) * (1 if mode == "low" else 2)
    )


@sc.experiment(id="ui_signal.slicing", controls=sc.ControlSet((FREQUENCY, BIAS)))
def signal(context: sc.ExperimentContext):
    return context.compute(
        "response",
        fn=response,
        inputs={
            "frequency": FREQUENCY.ref,
            "bias": BIAS.ref,
            "repeat": REPEAT,
            "mode": MODE,
        },
    )
