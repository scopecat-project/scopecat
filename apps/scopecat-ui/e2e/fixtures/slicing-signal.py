"""Ordinary synthetic four-dimensional data for slice browsing."""

import math

import numpy as np
from numpy.typing import NDArray

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


def trace_response(repeat: int) -> NDArray[np.float64]:
    return np.asarray([repeat, repeat + 1, repeat - 1], dtype=float)


@sc.experiment(id="ui_signal.overlay")
def overlay(context: sc.ExperimentContext):
    return context.compute(
        "trace",
        fn=trace_response,
        inputs={"repeat": REPEAT},
        output_type=sc.ArrayType(
            dtype="float64", dimensions=(sc.ArrayDimension("sample", 3),)
        ),
    )
