from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest
from scopecat_testkit.authoring import bind_invocation, load_config

import scopecat as sc
from scopecat.kernel.errors import CheckFailed
from scopecat.measurements.results import MeasurementValue
from scopecat.sdk.instruments import InterfaceRef

_SCALAR_SIGNAL = InterfaceRef("test.scalar_signal/v1")
_SCALAR_SIGNAL_SAMPLE_RAW = _SCALAR_SIGNAL.acquisition("sample").result("raw")


def _kernel(value: MeasurementValue) -> dict[str, MeasurementValue]:
    return {"result": value}


@dataclass(frozen=True, slots=True)
class _DerivedProducts:
    left: sc.ProductRef
    right: sc.ProductRef


def test_record_demand_retains_source_use_and_prunes_dead_compute(
    tmp_path: Path,
) -> None:
    @sc.module(id="test.compute.lowering")
    def module(context: sc.ModuleContext) -> sc.ProductRef:
        source = context._resource("source", requires=(_SCALAR_SIGNAL,))
        raw = context._product("raw")
        first = context._product("first")
        second = context._product("second")
        derived = context._product("derived")
        dead = context._product("dead")
        context._measurement_compute(
            "final",
            input=second,
            outputs={"result": derived},
            kernel=_kernel,
        )
        context._measurement_compute(
            "dead",
            input=raw,
            outputs={"result": dead},
            kernel=_kernel,
        )
        context._measurement_compute(
            "middle",
            input=first,
            outputs={"result": second},
            kernel=_kernel,
        )
        context._measurement_compute(
            "first",
            input=raw,
            outputs={"result": first},
            kernel=_kernel,
        )
        context._acquire(
            "read-raw",
            resource=source,
            results={_SCALAR_SIGNAL_SAMPLE_RAW: raw},
        )
        return derived

    @sc.experiment(id="test.compute.lowering", kind="compute")
    def experiment(experiment: sc.ExperimentContext) -> None:
        result = experiment.use(module())
        experiment.alias(result, record_id="first")
        experiment.alias(result, record_id="second")

    resolved = bind_invocation(experiment.build(), config_profile=load_config())
    program = resolved.bindings

    first, middle, final = program.measurement_computes
    assert [compute.id.qualified_name for compute in program.measurement_computes] == [
        "lowering/first",
        "lowering/middle",
        "lowering/final",
    ]
    assert first.inputs[0].product_id.qualified_name == "lowering/raw"
    assert first.outputs[0].product_use_ids == (middle.inputs[0].product_use_id,)
    assert middle.inputs[0].product_id.qualified_name == "lowering/first"
    assert middle.outputs[0].product_use_ids == (final.inputs[0].product_use_id,)
    assert final.inputs[0].product_id.qualified_name == "lowering/second"
    assert final.outputs[0].product_use_ids == tuple(
        product_use_id
        for record in program.product_record_uses
        for product_use_id in record.product_use_ids
    )
    assert {use.product_id.qualified_name for use in program.product_uses} == {
        "lowering/raw",
        "lowering/first",
        "lowering/second",
        "lowering/derived",
    }
    assert {
        result.product_id.qualified_name
        for acquisition in resolved.program.program.acquisitions
        for result in acquisition.results
    } == {"lowering/raw"}
    assert all(callable(compute.kernel) for compute in program.measurement_computes)


def test_hidden_input_use_ids_are_stable_and_scoped(tmp_path: Path) -> None:
    @sc.module(id="test.compute.hidden-id.child")
    def child(context: sc.ModuleContext) -> sc.ProductRef:
        source = context._resource("source", requires=(_SCALAR_SIGNAL,))
        raw = context._product("raw")
        derived = context._product("derived")
        context._measurement_compute(
            "derive",
            input=raw,
            outputs={"result": derived},
            kernel=_kernel,
        )
        context._acquire(
            "read-raw",
            resource=source,
            results={_SCALAR_SIGNAL_SAMPLE_RAW: raw},
        )
        return derived

    left = child.instantiate("left")
    right = child.instantiate("right")

    @sc.module(id="test.compute.hidden-id.root")
    def root(context: sc.ModuleContext) -> _DerivedProducts:
        context.use(left)
        context.use(right)
        return _DerivedProducts(left=left.result, right=right.result)

    @sc.experiment(id="test.compute.hidden-id", kind="compute")
    def experiment(experiment: sc.ExperimentContext) -> None:
        result = experiment.use(root())
        experiment.alias(result.left, record_id="left")
        experiment.alias(result.right, record_id="right")

    def compile_input_use_ids() -> dict[str, str]:
        program = bind_invocation(
            experiment.build(),
            config_profile=load_config(),
        ).bindings
        return {
            compute.id.qualified_name: (compute.inputs[0].product_use_id.value)
            for compute in program.measurement_computes
        }

    assert (
        compile_input_use_ids()
        == compile_input_use_ids()
        == {
            "root/left/derive": (
                "scopecat.measurement-compute/root/left/derive/inputs/input"
            ),
            "root/right/derive": (
                "scopecat.measurement-compute/root/right/derive/inputs/input"
            ),
        }
    )


def test_measurement_compute_mints_one_live_use_for_each_named_input() -> None:
    def add(*, left: float, right: float) -> float:
        return left + right

    @sc.module(id="test.measurement-compute.lowering")
    def module(context: sc.ModuleContext) -> sc.ProductRef:
        source = context._resource("source", requires=(_SCALAR_SIGNAL,))
        left = context._product("left")
        right = context._product("right")
        result = context.compute(
            "sum",
            fn=add,
            inputs={"left": left, "right": right},
            output_type=sc.ScalarType(sc.FloatType()),
        )
        context._acquire(
            "read-left",
            resource=source,
            results={_SCALAR_SIGNAL_SAMPLE_RAW: left},
        )
        context._acquire(
            "read-right",
            resource=source,
            results={_SCALAR_SIGNAL_SAMPLE_RAW: right},
        )
        return result

    @sc.experiment(id="test.measurement-compute.lowering", kind="compute")
    def experiment(experiment: sc.ExperimentContext) -> None:
        result = experiment.use(module())
        experiment.alias(result)

    bound = bind_invocation(experiment.build(), config_profile=load_config())

    [compute] = bound.bindings.measurement_computes
    assert [(item.id, item.product_id.qualified_name) for item in compute.inputs] == [
        ("left", "lowering/left"),
        ("right", "lowering/right"),
    ]
    assert {use.product_id.qualified_name for use in bound.bindings.product_uses} == {
        "lowering/left",
        "lowering/right",
        "lowering/sum",
    }


def test_recorded_product_requires_a_producer() -> None:
    @sc.module(id="test.product.owner")
    def module(context: sc.ModuleContext) -> sc.ProductRef:
        return context._product("orphan")

    @sc.experiment(id="test.product.owner", kind="product-owner")
    def experiment(experiment: sc.ExperimentContext) -> None:
        result = experiment.use(module())
        experiment.alias(result)

    with pytest.raises(CheckFailed) as error:
        bind_invocation(experiment.build(), config_profile=load_config())

    assert [problem.code for problem in error.value.problems] == [
        "product_acquire_missing"
    ]
