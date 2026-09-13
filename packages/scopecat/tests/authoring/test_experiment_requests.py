"""Request editing captures data without caching or constructing an experiment."""

import inspect
from typing import TYPE_CHECKING, assert_type

import numpy as np
import pytest

import scopecat as sc


def test_request_creation_and_copy_capture_mutable_inputs() -> None:
    @sc.experiment
    def untouched(ctx: sc.ExperimentContext, targets: list[str]) -> None:
        raise AssertionError("request creation must not build a program")

    original = ["q0"]
    request = untouched(original)
    original.append("q1")
    alternative = request.copy()
    alternative.values["targets"] = ["q2"]
    assert request.values == {"targets": ["q0"]}
    assert alternative.declaration is request.declaration
    assert not hasattr(request, "output")
    assert not hasattr(request, "definition")
    if TYPE_CHECKING:
        assert_type(request, sc.ExperimentRequest[None])
        untouched(123)  # pyright: ignore[reportArgumentType]
    with pytest.raises(TypeError, match="targets"):
        untouched()  # pyright: ignore[reportCallIssue]


def test_scan_captures_array_and_copy_isolates_later_edits() -> None:
    values = np.array([0.1, 0.2, 0.3])
    scan = sc.Scan(values)
    values[:] = 0.9
    assert scan.values == (0.1, 0.2, 0.3)


def test_explicit_build_is_separate_from_editable_call() -> None:
    built: list[str] = []

    @sc.experiment
    def probe(ctx: sc.ExperimentContext, target: str) -> None:
        built.append(target)

    request = probe("q0")
    request.values["target"] = "q1"
    assert built == []
    invocation = assert_type(probe.build("q0"), sc.ExperimentInvocation[None])
    assert built == ["q0"]
    assert invocation.output is None
    assert request.values == {"target": "q1"}
    assert inspect.signature(probe).return_annotation is sc.ExperimentRequest
    assert not hasattr(probe, "request")
    if TYPE_CHECKING:
        probe.build(123)  # pyright: ignore[reportArgumentType]
    with pytest.raises(TypeError, match="target"):
        probe.build()  # pyright: ignore[reportCallIssue]
