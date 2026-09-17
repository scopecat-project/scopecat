# Quantity 值不可变, 可直接作为声明默认值。
# pyright: reportCallInDefaultInitializer=false

"""Synthetic course experiment; uses the selected parameter workspace."""

from dataclasses import dataclass
from typing import Annotated, cast

import scopecat as sc
from lab_teaching.parameters import Drive
from lab_teaching.synthetic import response


@dataclass(frozen=True)
class TeachingData:
    amplitude: sc.CoordinateRef[sc.Quantity]
    iq: sc.ProductRef


@sc.experiment(id="teaching.rabi")
def teaching_rabi(
    experiment: sc.ExperimentContext,
    shots: int = 64,
    seed: int = 200,
    no_response: bool = False,
    *,
    amplitude: Annotated[
        sc.Input[sc.Quantity],
        sc.ControlSpec(minimum=0, maximum=0.9, title="Drive amplitude", scannable=True),
    ] = sc.Quantity(0.1, "arb"),
) -> TeachingData:
    iq = experiment.compute(
        "teaching-iq",
        fn=response,
        frequency=sc.parameter_ref(Drive.frequency, "q0"),
        amplitude=amplitude,
        shots=shots,
        seed=seed,
        no_response=no_response,
        output_type=sc.ArrayType(
            dtype="complex128",
            unit="ratio",
            dimensions=(sc.ArrayDimension("shot", shots),),
        ),
    )
    return TeachingData(experiment.coordinate(amplitude), cast("sc.ProductRef", iq))
