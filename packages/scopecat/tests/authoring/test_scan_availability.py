from __future__ import annotations

from typing import Annotated

import pytest

import scopecat as sc
from scopecat.compiler.frontend import resolution
from scopecat.kernel.errors import CheckFailed
from scopecat.kernel.problems import model_location
from scopecat.program.values import compute as program_compute
from scopecat.records.run_request import AxisAroundSourceRecord, GridDomainRecord


def _quantity_scan_target() -> sc.CoordinateRef:
    quantity_type = sc.ScalarType(sc.QuantityType(unit="GHz"))
    return sc.coordinate("frequency", quantity_type)


def test_default_scan_center_rejects_external_operation() -> None:
    target = _quantity_scan_target()

    @sc.module(id="test.scan-stage.default")
    def module(context: sc.ModuleContext) -> sc.ValueRef:
        center = context.compute(
            "compute-center",
            fn=lambda: sc.Quantity(value=5.0, unit="GHz"),
            output_type=sc.ScalarType(sc.QuantityType(unit="GHz")),
        )
        return center

    call = module()
    center = call.result

    def experiment_definition(experiment: sc.ExperimentContext) -> None:
        experiment.use(call)
        experiment.grid(
            sc.axis(
                target,
                center=center,
                span=sc.Quantity(value=0.1, unit="GHz"),
                points=3,
            )
        )

    experiment = sc.experiment(id="test.scan-stage.default", kind="scan-stage")(
        experiment_definition
    )

    with pytest.raises(CheckFailed) as error:
        resolution.compile_invocation(experiment.build())

    problem = error.value.problems[0]
    assert problem.code == "value_requires_execution"
    assert problem.location == model_location(
        "point_domain",
        "axes",
        0,
        "center",
    )
    assert "axis center" in problem.message


def test_invocation_scan_center_rejects_external_operation() -> None:
    target = _quantity_scan_target()

    @sc.module(id="test.scan-stage.invocation")
    def module(context: sc.ModuleContext) -> sc.ValueRef:
        center = context.compute(
            "compute-center",
            fn=lambda: sc.Quantity(value=5.0, unit="GHz"),
            output_type=sc.ScalarType(sc.QuantityType(unit="GHz")),
        )
        return center

    call = module()
    center = call.result

    @sc.experiment(id="test.scan-stage.invocation", kind="scan-stage")
    def experiment_definition(experiment: sc.ExperimentContext) -> None:
        experiment.use(call)

    invocation = experiment_definition.build().with_axis(
        sc.axis(
            target,
            center=center,
            span=sc.Quantity(value=0.1, unit="GHz"),
            points=3,
        )
    )

    with pytest.raises(CheckFailed) as error:
        resolution.compile_invocation(invocation)

    problem = error.value.problems[0]
    assert problem.code == "value_requires_execution"
    assert problem.location == model_location("point_domain", "axes", 0, "center")


def test_scan_center_accepts_module_result_resolved_to_literal_input() -> None:
    @sc.module(id="test.scan-stage.input")
    def module(
        context: sc.ModuleContext,
        value: Annotated[sc.Input[sc.Quantity], sc.QuantityType(unit="GHz")],
    ) -> sc.ValueRef:
        del context
        return sc.input_ref(value)

    call = module(value=sc.Quantity(value=5.0, unit="GHz"))
    target = _quantity_scan_target()

    @sc.experiment(id="test.scan-stage.input", kind="scan-stage")
    def experiment_definition(experiment: sc.ExperimentContext) -> None:
        experiment.use(call)
        experiment.grid(
            sc.axis(
                target,
                center=call.result,
                span=sc.Quantity(value=0.1, unit="GHz"),
                points=3,
            )
        )

    compiled = resolution.compile_invocation(experiment_definition.build())

    domain = compiled.request.point_plan.domain
    assert isinstance(domain, GridDomainRecord)
    [axis] = domain.axes
    assert isinstance(axis.source, AxisAroundSourceRecord)
    assert axis.source.center == sc.Quantity(value=5.0, unit="GHz")


def test_parameter_lookup_key_rejects_external_operation() -> None:
    entity_type = sc.ScalarType(sc.EntityType())
    key = program_compute(
        "compute-key",
        fn=lambda: "q0",
        output_type=entity_type,
    )
    target_type = sc.ScalarType(sc.FloatType())

    with pytest.raises(
        TypeError,
        match="compute outputs cannot be bound inside scalar expressions",
    ):
        sc.parameter_lookup(
            "device-parameters",
            key={"device": key.output},
            column="gain",
            value_type=target_type,
        )
