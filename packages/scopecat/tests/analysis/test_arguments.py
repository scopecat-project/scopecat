from dataclasses import dataclass
from typing import assert_type

import pytest

import scopecat as sc
from scopecat.analysis.arguments import bind_arguments, encode_arguments
from scopecat.api.analysis import AnalysisInvocation
from scopecat.measurements.dataset import Dataset


@dataclass
class Result:
    value: sc.Quantity


@sc.analysis_function
def consume(
    data: Dataset, *, frequency: sc.Quantity, threshold: float | None = 0.2
) -> Result:
    return Result(frequency)


def test_native_and_json_arguments_use_selected_signature_and_defaults() -> None:
    q = sc.Quantity(4.8, "GHz")
    encoded = encode_arguments("consume", {"frequency": q})
    assert encoded == {"frequency": {"value": 4.8, "unit": "GHz"}}
    assert bind_arguments(consume.function, encoded) == {
        "frequency": q,
        "threshold": 0.2,
    }
    assert bind_arguments(consume.function, {**encoded, "threshold": None}) == {
        "frequency": q,
        "threshold": None,
    }
    assert_type(consume(frequency=q), AnalysisInvocation)


def test_argument_errors_identify_function_and_parameter() -> None:
    with pytest.raises(TypeError, match="consume argument 'frequency'"):
        bind_arguments(consume.function, {"frequency": 4.8})
    with pytest.raises(TypeError, match=r"consume.*frequency"):
        bind_arguments(consume.function, {})
    with pytest.raises(TypeError, match=r"consume argument 'frequency'.*unsupported"):
        encode_arguments("consume", {"frequency": object()})  # pyright: ignore[reportArgumentType]
    with pytest.raises(TypeError, match="threshold"):
        bind_arguments(
            consume.function,
            {
                "frequency": {"value": 4.8, "unit": "GHz"},
                "threshold": "0.2",
            },
        )
