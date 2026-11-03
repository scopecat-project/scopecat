"""Software-only shot records for validating a display reduction, not storage."""

import numpy as np
from numpy.typing import NDArray

import scopecat as sc

AMPLITUDE = sc.coordinate("amplitude", sc.FloatType())


def response(amplitude: float) -> NDArray[np.complex128]:
    # Exactly zero-mean offsets make the expected mean independently checkable.
    return np.asarray([amplitude - 1j, amplitude + 1j] * 32, dtype=np.complex128)


@sc.experiment(id="ui_signal.shots")
def signal(context: sc.ExperimentContext):
    iq = context.compute(
        "iq",
        fn=response,
        inputs={"amplitude": AMPLITUDE},
        output_type=sc.ArrayType(
            dtype="complex128",
            unit="ratio",
            dimensions=(sc.ArrayDimension("shot", 64, kind="shot"),),
        ),
    )

    return {"iq": iq}
