from __future__ import annotations

from typing import Annotated

import pytest
from scopecat_testkit.authoring import bind_invocation, load_config

import scopecat as sc
from scopecat.compiler.frontend.resolution import compile_invocation
from scopecat.kernel.errors import CheckFailed
from scopecat.kernel.problems import model_location

_FREQUENCY_ATOM = sc.QuantityType(unit="GHz")
_FREQUENCY_TYPE = sc.ScalarType(_FREQUENCY_ATOM)


def _identity(value: object) -> object:
    return value


def _point_module(
    *,
    module_id: str,
) -> sc.ExperimentModule[None, ...]:
    @sc.module(id=module_id)
    def module(
        context: sc.ModuleContext,
        point: Annotated[sc.Input[sc.Quantity], _FREQUENCY_TYPE],
    ) -> None:
        context.compute(
            f"{module_id}.consume",
            fn=_identity,
            inputs={"value": point},
            output_type=_FREQUENCY_TYPE,
        )

    return module


def _resolve(
    module: sc.ExperimentModule[None, ...],
    point: sc.ValueRef,
    *,
    scan: sc.Axis | None = None,
) -> None:
    call = module(point)

    @sc.experiment(id="test.point-dependency", kind="point_dependency")
    def experiment_definition(experiment: sc.ExperimentContext) -> None:
        experiment.use(call)
        if scan is not None:
            experiment.grid(scan)

    bind_invocation(
        experiment_definition.build(),
        config_profile=load_config(),
    )


def test_direct_point_dependency_requires_a_scan() -> None:
    frequency = sc.coordinate("frequency", _FREQUENCY_ATOM)
    module = _point_module(module_id="test.direct-point")

    with pytest.raises(CheckFailed) as error:
        _resolve(module, frequency)

    problem = error.value.problems[0]
    assert problem.code == "experiment_point_dependency_missing"
    assert problem.location == model_location("point_domain", "frequency")


def test_direct_point_dependency_rejects_same_id_with_wrong_type() -> None:
    frequency = sc.coordinate("frequency", _FREQUENCY_ATOM)
    module = _point_module(module_id="test.mistyped-point")
    wrong_frequency = sc.coordinate(
        "frequency",
        sc.StringType(),
    )

    with pytest.raises(CheckFailed) as error:
        _resolve(module, frequency, scan=sc.axis(wrong_frequency, ("5 GHz",)))

    problem = error.value.problems[0]
    assert problem.code == "experiment_point_dependency_type_mismatch"
    assert problem.location == model_location("point_domain", "frequency")
    assert "Scalar[String]" in problem.message
    assert "Scalar[Quantity[GHz]]" in problem.message


def test_direct_point_dependency_accepts_matching_scan() -> None:
    frequency = sc.coordinate("frequency", _FREQUENCY_ATOM)
    module = _point_module(module_id="test.matching-point")

    _resolve(module, frequency, scan=sc.axis(frequency, (5.0,), unit="GHz"))


def test_nested_module_preserves_bound_point_dependency() -> None:
    @sc.module(id="test.point-child")
    def child(
        context: sc.ModuleContext,
        frequency: Annotated[sc.Input[sc.Quantity], _FREQUENCY_TYPE],
    ) -> None:
        context.compute(
            "test.point-child.consume",
            fn=_identity,
            inputs={"value": frequency},
            output_type=_FREQUENCY_TYPE,
        )

    parent_frequency = sc.coordinate("frequency", _FREQUENCY_ATOM)

    @sc.module(id="test.point-parent")
    def parent(
        context: sc.ModuleContext,
        frequency: Annotated[sc.Input[sc.Quantity], _FREQUENCY_TYPE],
    ) -> None:
        context.use(child.instantiate("point-child", frequency=frequency))

    @sc.experiment(id="test.point-parent", kind="point_dependency")
    def experiment(experiment: sc.ExperimentContext) -> None:
        experiment.use(parent(parent_frequency))
        experiment.grid(sc.axis(parent_frequency, (5.0,), unit="GHz"))

    assembly = compile_invocation(experiment.build()).program.program
    assert tuple(
        (dependency.id, dependency.value_type)
        for dependency in assembly.point_dependencies
    ) == (("frequency", _FREQUENCY_TYPE),)

    with pytest.raises(CheckFailed) as error:
        _resolve(parent, parent_frequency)
    assert error.value.problems[0].code == "experiment_point_dependency_missing"

    _resolve(
        parent,
        parent_frequency,
        scan=sc.axis(parent_frequency, (5.0,), unit="GHz"),
    )
