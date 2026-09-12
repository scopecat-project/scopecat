# pyright: reportUnnecessaryTypeIgnoreComment=true
# pyright: reportUnknownArgumentType=false, reportUnknownMemberType=false

from __future__ import annotations

import inspect
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated, assert_type, cast

import pyarrow as pa
import pytest

import scopecat as sc
from scopecat.api.analysis import AnalysisDefinition, AnalysisInvocation
from scopecat.authoring._module_results import ProductBundle
from scopecat.compiler.frontend.resolution import compile_invocation
from scopecat.kernel.errors import CheckFailed
from scopecat.program.products import (
    EntityRecordSelection,
    ProductAxis,
    RecordSelection,
)
from scopecat.sdk.instruments import InterfaceRef

_COUNT_TYPE = sc.IntType(minimum=0)
_COUNTER = InterfaceRef("test.counter/v1")
_COUNTER_COUNT = _COUNTER.property("count")
_GLOBAL_COUNT = sc.coordinate("global_count", sc.ScalarType(_COUNT_TYPE))


@dataclass(frozen=True, slots=True)
class _StructuralValues:
    ordered: tuple[sc.ValueRef[int], ...]
    named: Mapping[str, sc.ValueRef[int]]


@dataclass(frozen=True, slots=True)
class _CountDataset:
    count: sc.CoordinateRef[int]
    recorded_count: sc.RecordRef[int]


@dataclass(frozen=True, slots=True)
class _ReturnedSignals(ProductBundle):
    signal: sc.ProductRef
    reference: sc.ProductRef


@dataclass(frozen=True, slots=True)
class _ReturnedDataset:
    capture: _ReturnedSignals
    score: sc.ValueRef[float]


@dataclass(frozen=True, slots=True)
class _PolicyDataset:
    capture: Annotated[
        _ReturnedSignals,
        sc.Result(
            namespace="science",
            role="coordinate",
            metadata={"reviewed": True},
        ),
    ]
    score: Annotated[
        sc.ValueRef[float],
        sc.Result(id="quality", metadata={"metric": "quality"}),
    ]


@dataclass(frozen=True, slots=True)
class _ReturnedAliases:
    primary: sc.ProductRef
    diagnostic: sc.ProductRef


def _identity_count(*, value: object) -> object:
    return value


def _default_count() -> int:
    return 1


def test_symbolic_factories_preserve_python_value_types() -> None:
    assert_type(
        sc.coordinate("enabled", sc.BoolType()),
        sc.CoordinateRef[bool],
    )
    assert_type(
        sc.parameter("frequency", sc.QuantityType(unit="GHz")),
        sc.ValueRef[sc.Quantity],
    )
    assert_type(
        sc.parameter_lookup(
            "device_parameters",
            key={"device": "q0"},
            column="label",
            value_type=sc.StringType(),
        ),
        sc.ValueRef[str],
    )


def test_module_decorator_injects_one_explicit_context() -> None:
    elaborations = 0

    @sc.module()
    def count_source(
        module: sc.ModuleContext,
        count: Annotated[sc.Input[int], _COUNT_TYPE],
    ) -> None:
        """Expose the selected count."""

        nonlocal elaborations
        elaborations += 1
        count_ref = assert_type(sc.input_ref(count), sc.ValueRef[int])
        counter = module._resource("test.counter/v1", requires=(_COUNTER,))
        module._bind_property(counter, _COUNTER_COUNT, value=count_ref)

    assert elaborations == 1
    assert count_source.id.endswith(".count_source")
    assert count_source.metadata["description"] == "Expose the selected count."
    assert count_source.definition.interface.imports[0].value_type == sc.ScalarType(
        _COUNT_TYPE
    )
    signature = inspect.signature(count_source)
    assert tuple(signature.parameters) == ("count",)
    assert signature.return_annotation is sc.ModuleInvocation
    assert count_source.__wrapped__.__name__ == "count_source"
    assert isinstance(count_source, sc.ExperimentModule)
    invocation = assert_type(count_source(2), sc.ModuleInvocation[None])
    assert invocation.instance_id == "count_source"

    if TYPE_CHECKING:
        count_source(count="invalid")  # pyright: ignore[reportArgumentType]
        count_source(unknown=2)  # pyright: ignore[reportCallIssue]


def test_module_definition_requires_an_annotated_context() -> None:
    def missing_context(count: int) -> None:
        del count

    with pytest.raises(TypeError, match="first definition parameter"):
        sc.module(  # pyright: ignore[reportCallIssue]
            missing_context  # pyright: ignore[reportArgumentType]
        )


def test_module_definition_rejects_global_symbolic_values() -> None:
    def global_inputs() -> dict[str, sc.ValueRef]:
        return {"value": _GLOBAL_COUNT}

    def captured(module: sc.ModuleContext) -> None:
        module.compute(
            "captured",
            fn=_identity_count,
            inputs=global_inputs(),
            output_type=sc.ScalarType(_COUNT_TYPE),
        )

    with pytest.raises(CheckFailed, match="declare a typed module input"):
        sc.module(captured)


def test_compute_infers_stable_ids_from_functions_and_allocates_lambda_ids() -> None:
    @sc.module
    def computed(
        module: sc.ModuleContext,
    ) -> tuple[sc.ValueRef, sc.ValueRef, sc.ValueRef]:
        named = module.compute(
            fn=_default_count,
            output_type=sc.ScalarType(_COUNT_TYPE),
        )
        first_lambda = module.compute(
            fn=lambda: 2,
            output_type=sc.ScalarType(_COUNT_TYPE),
        )
        second_lambda = module.compute(
            fn=lambda: 3,
            output_type=sc.ScalarType(_COUNT_TYPE),
        )
        return named, first_lambda, second_lambda

    assert [operation.id for operation in computed.definition.body.operations] == [
        "default_count",
        "compute",
        "compute.2",
    ]


def test_compute_binds_keyword_inputs_and_infers_scalar_return_type() -> None:
    def add(*, left: int, right: int) -> int:
        return left + right

    @sc.module
    def computed(module: sc.ModuleContext) -> sc.ValueRef:
        result = module.compute(fn=add, left=1, right=2)
        assert isinstance(result, sc.ValueRef)
        return result

    [operation] = computed.definition.body.operations
    assert [name for name, _value in operation.inputs] == ["left", "right"]
    assert operation.output_type == sc.ScalarType(sc.IntType())


def test_convert_preserves_host_availability_and_changes_the_unit_contract() -> None:
    @sc.module
    def converted(module: sc.ModuleContext) -> sc.ValueRef[sc.Quantity]:
        voltage = cast(
            "sc.ValueRef[sc.Quantity]",
            module.compute(
                "voltage",
                fn=lambda: sc.Quantity(0.125, "V"),
                output_type=sc.ScalarType(sc.QuantityType(unit="V")),
            ),
        )
        return assert_type(
            module.convert(voltage, "mV"),
            sc.ValueRef[sc.Quantity],
        )

    _voltage, conversion = converted.definition.body.operations
    assert conversion.id == "convert_unit_value"
    assert conversion.output_type == sc.ScalarType(sc.QuantityType(unit="mV"))
    assert conversion.inputs[0][0] == "value"


def test_experiment_infers_identity_description_and_runtime_defaults() -> None:
    elaborations = 0

    @sc.module
    def count_source(
        module: sc.ModuleContext,
        count: Annotated[sc.Input[int], _COUNT_TYPE],
    ) -> None:
        counter = module._resource("test.counter/v1", requires=(_COUNTER,))
        module._bind_property(counter, _COUNTER_COUNT, value=count)

    @sc.experiment
    def count_experiment(
        experiment: sc.ExperimentContext,
        count: Annotated[sc.Input[int], _COUNT_TYPE] = 2,
    ) -> None:
        """Run one count experiment."""

        nonlocal elaborations
        elaborations += 1
        experiment.use(count_source(count=count))

    assert elaborations == 0
    assert count_experiment.id == "count_experiment"
    assert count_experiment.kind == "count_experiment"
    assert count_experiment.metadata["description"] == "Run one count experiment."
    default_invocation = count_experiment()
    assert elaborations == 1
    assert default_invocation.definition.id == count_experiment.id
    assert default_invocation.definition.inputs[0].default == 2
    assert default_invocation.input_overrides == {}
    signature = inspect.signature(count_experiment)
    assert signature.parameters["count"].default == 2
    assert signature.return_annotation is sc.ExperimentInvocation
    assert isinstance(count_experiment, sc.Experiment)
    invocation = assert_type(count_experiment(3), sc.ExperimentInvocation[None])
    assert invocation.definition is default_invocation.definition
    assert invocation.input_overrides == {"count": 3}

    if TYPE_CHECKING:
        count_experiment(count="invalid")  # pyright: ignore[reportArgumentType]
        count_experiment(unknown=3)  # pyright: ignore[reportCallIssue]


def test_experiment_returns_a_typed_dataset_schema() -> None:
    @sc.experiment
    def count_experiment(
        experiment: sc.ExperimentContext,
    ) -> _CountDataset:
        count = experiment.scan("count", (1, 2, 3))
        return _CountDataset(
            count=count,
            recorded_count=experiment.alias(count, record_id="count_copy"),
        )

    invocation = assert_type(
        count_experiment(),
        sc.ExperimentInvocation[_CountDataset],
    )
    output = assert_type(invocation.output, _CountDataset)

    assert output.count is invocation.with_repeat(2).output.count
    assert output.recorded_count.id == "count_copy"
    assert output.recorded_count.dims == ("point",)


def test_returned_values_are_durable_without_explicit_record_calls() -> None:
    def computed(experiment: sc.ExperimentContext) -> sc.ValueRef[object]:
        count = experiment.scan("count", (1, 2, 3))
        return count + 1

    computed_experiment = sc.experiment(computed)
    computed_invocation = computed_experiment()
    [computed_record] = computed_invocation.definition.record_selections
    assert computed_record.record_id == "result"
    [logical_record] = compile_invocation(
        computed_invocation
    ).program.program.value_record_selections
    assert (
        logical_record.source_value_id == computed_invocation.output.id.qualified_name
    )

    @sc.module
    def product_source(module: sc.ModuleContext) -> sc.ProductRef:
        return module._product("signal")

    def product(experiment: sc.ExperimentContext) -> sc.ProductRef:
        return experiment.use(product_source())

    product_experiment = sc.experiment(product)
    [product_record] = product_experiment().definition.record_selections
    assert isinstance(product_record, RecordSelection)
    assert product_record.product_id.local_id == "signal"
    assert product_record.record_id == "result"


def test_return_paths_name_product_bundles_and_named_values() -> None:
    @sc.experiment(id="test.return-schema", kind="return")
    def definition(experiment: sc.ExperimentContext) -> _ReturnedDataset:
        signal = experiment._product("source/signal")
        reference = experiment._product("source/reference")
        score = experiment.compute(
            "internal-score-operation",
            fn=lambda: 0.75,
            output_type=sc.ScalarType(sc.FloatType()),
        )
        return _ReturnedDataset(
            capture=_ReturnedSignals(signal=signal, reference=reference),
            score=cast("sc.ValueRef[float]", score),
        )

    selections = definition().definition.record_selections

    assert [selection.record_id for selection in selections] == [
        "capture/signal",
        "capture/reference",
        "score",
    ]


def test_homogeneous_per_entity_return_uses_one_entity_indexed_record() -> None:
    q0 = sc.EntityRef(id="q0", kind="qubit")
    q1 = sc.EntityRef(id="q1", kind="qubit")

    @sc.experiment(id="test.entity-return", kind="return")
    def definition(
        experiment: sc.ExperimentContext,
    ) -> sc.PerEntity[sc.ProductRef]:
        return sc.PerEntity(
            (
                (q0, experiment._product("readout", scope=("q0",))),
                (q1, experiment._product("readout", scope=("q1",))),
            )
        )

    invocation = definition()
    selections = invocation.definition.record_selections

    [selection] = selections
    assert isinstance(selection, EntityRecordSelection)
    assert selection.record_id == "result"
    assert selection.axis.id == "qubit"
    assert selection.axis.values == (q0, q1)
    assert [member.entity for member in selection.members] == [q0, q1]
    [result_field] = invocation.definition.result_fields
    assert result_field.path == ("result",)
    assert result_field.variable_id == "result"
    assert isinstance(invocation.output, sc.PerEntity)
    assert invocation.output[q0].id == "q0/readout"
    output = invocation.entity_result_ref("result")
    assert output.id == "result"
    assert output.dims == ("point", "qubit")
    assert output.entity_axis_id == "qubit"
    assert output.entity_axis_fingerprint is not None


def test_return_annotations_refine_durable_record_policy() -> None:
    def score_value() -> float:
        return 0.75

    @sc.experiment(id="test.return-policy", kind="return")
    def definition(experiment: sc.ExperimentContext) -> _PolicyDataset:
        score = experiment.compute(fn=score_value)
        return _PolicyDataset(
            capture=_ReturnedSignals(
                signal=experiment._product("source/signal"),
                reference=experiment._product("source/reference"),
            ),
            score=cast("sc.ValueRef[float]", score),
        )

    signal, reference, score = definition().definition.record_selections

    assert [selection.record_id for selection in (signal, reference, score)] == [
        "science/capture/signal",
        "science/capture/reference",
        "quality",
    ]
    assert signal.role == reference.role == "coordinate"
    assert signal.metadata == reference.metadata == {"reviewed": True}
    assert score.metadata == {"metric": "quality"}


def test_repeated_return_source_creates_aliases_without_duplicate_product_uses() -> (
    None
):
    @sc.experiment(id="test.return-alias", kind="return")
    def definition(experiment: sc.ExperimentContext) -> _ReturnedAliases:
        signal = experiment._product("internal/signal")
        return _ReturnedAliases(primary=signal, diagnostic=signal)

    primary, diagnostic = definition().definition.record_selections

    assert isinstance(primary, RecordSelection)
    assert isinstance(diagnostic, RecordSelection)
    assert [primary.record_id, diagnostic.record_id] == [
        "primary",
        "diagnostic",
    ]
    assert primary.product_use.id == diagnostic.product_use.id


def test_returned_explicit_record_is_not_selected_twice() -> None:
    @sc.experiment(id="test.returned-record", kind="return")
    def definition(experiment: sc.ExperimentContext) -> sc.ValueRef[object]:
        score = experiment.compute(
            "score",
            fn=lambda: 1.0,
            output_type=sc.ScalarType(sc.FloatType()),
        )
        experiment.alias(score)
        return score

    assert len(definition().definition.record_selections) == 1


def test_analysis_decorator_preserves_configuration_signature() -> None:
    evaluations = 0

    @sc.analysis_step(id="readout.fit")
    def readout_fit(
        context: sc.AnalysisContext,
        *,
        qubit: str,
        attempts: int = 2,
    ) -> sc.Analysis:
        nonlocal evaluations
        evaluations += 1
        return (
            context.result(f"readout fit for {qubit}")
            .dataset("attempts", pa.table({"attempts": [attempts]}))
            .table(dataset="attempts")
        )

    assert evaluations == 0
    assert readout_fit.id == "readout.fit"
    assert readout_fit.__wrapped__.__name__ == "readout_fit"
    assert isinstance(readout_fit, AnalysisDefinition)
    signature = inspect.signature(readout_fit)
    assert tuple(signature.parameters) == ("qubit", "attempts")
    assert signature.return_annotation is AnalysisInvocation
    step = assert_type(readout_fit(qubit="q0"), AnalysisInvocation)
    assert step.id == "readout.fit"
    assert step.arguments == (("qubit", "q0"),)

    if TYPE_CHECKING:
        readout_fit(qubit=1)  # pyright: ignore[reportArgumentType]
        readout_fit(unknown="q0")  # pyright: ignore[reportCallIssue]


def test_analysis_implementation_fingerprint_covers_nonlocal_values() -> None:
    def invocation(marker: str) -> AnalysisInvocation:
        @sc.analysis_step(id="tests.analysis-fingerprint")
        def fingerprinted(context: sc.AnalysisContext) -> sc.Analysis:
            raise AssertionError(f"analysis {marker} must not run in this test")

        return fingerprinted()

    first = invocation("first")
    same = invocation("first")
    changed = invocation("changed")

    assert first.implementation_fingerprint == same.implementation_fingerprint
    assert first.implementation_fingerprint != changed.implementation_fingerprint


def test_experiment_separates_runtime_inputs_from_structural_arguments() -> None:
    count = sc.coordinate("count", sc.ScalarType(_COUNT_TYPE))
    elaborations = 0

    @sc.module
    def count_source(
        module: sc.ModuleContext,
        value: Annotated[sc.Input[int], _COUNT_TYPE],
    ) -> None:
        counter = module._resource("test.counter/v1", requires=(_COUNTER,))
        module._bind_property(counter, _COUNTER_COUNT, value=value)

    @sc.experiment(id="test.function.mixed", kind="count")
    def mixed(
        experiment: sc.ExperimentContext,
        value: Annotated[sc.Input[int], _COUNT_TYPE],
        *,
        scan_values: tuple[int, ...],
    ) -> None:
        nonlocal elaborations
        elaborations += 1
        experiment.use(count_source(value=value))
        experiment.grid(sc.axis(count, scan_values))

    assert elaborations == 0
    with pytest.raises(TypeError, match=r"structural argument.*scan_values"):
        mixed.bind(value=2)

    first = mixed.bind(scan_values=(1, 2, 3))
    second = mixed(value=4, scan_values=(5, 6))

    assert elaborations == 2
    assert [input_.id for input_ in first.definition.inputs] == ["value"]
    assert first.input_overrides == {}
    assert second.input_overrides == {"value": 4}
    assert first.definition.default_point_plan != second.definition.default_point_plan
    compile_invocation(first.bind(value=2))
    compile_invocation(second)


def test_plain_experiment_arguments_are_structural() -> None:
    elaborations = 0

    @sc.module
    def count_source(
        module: sc.ModuleContext,
        count: Annotated[sc.Input[int], _COUNT_TYPE],
    ) -> None:
        del module, count

    @sc.experiment
    def count_experiment(
        experiment: sc.ExperimentContext,
        count: int = 2,
    ) -> None:
        nonlocal elaborations
        elaborations += 1
        experiment.use(count_source(count=count))

    assert elaborations == 0
    signature = inspect.signature(count_experiment)
    assert signature.parameters["count"].default == 2
    assert signature.return_annotation is sc.ExperimentInvocation
    default_invocation = count_experiment()
    selected_invocation = assert_type(
        count_experiment(3),
        sc.ExperimentInvocation[None],
    )
    assert elaborations == 2
    assert default_invocation.definition.inputs == ()
    assert selected_invocation.input_overrides == {}
    assert default_invocation.definition.body != selected_invocation.definition.body
    compile_invocation(default_invocation)
    compile_invocation(selected_invocation)

    if TYPE_CHECKING:
        count_experiment("invalid")  # pyright: ignore[reportArgumentType]
        count_experiment(unknown=3)  # pyright: ignore[reportCallIssue]


def test_plain_module_arguments_specialize_a_closed_typed_invocation() -> None:
    elaborations = 0

    @sc.module(id="test.function.structural")
    def product_source(
        module: sc.ModuleContext,
        value: sc.Input[int],
        *,
        product_id: str = "result",
    ) -> sc.ProductRef:
        nonlocal elaborations
        elaborations += 1
        del value
        return module._product(product_id)

    assert elaborations == 0
    with pytest.raises(TypeError, match="no single definition"):
        _ = product_source.definition

    first = assert_type(
        product_source(1, product_id="left"),
        sc.ModuleInvocation[sc.ProductRef],
    )
    second = product_source.call("chosen", 2)

    assert elaborations == 2
    assert [port.id for port in first.module.definition.interface.imports] == ["value"]
    assert first.result.id == "structural/left"
    assert second.result.id == "chosen/result"

    if TYPE_CHECKING:
        product_source("invalid")  # pyright: ignore[reportArgumentType]
        product_source(1, product_id=2)  # pyright: ignore[reportArgumentType]


def test_symbolic_structural_argument_becomes_a_private_module_import() -> None:
    point = sc.coordinate("selected", sc.IntType())

    @sc.module(id="test.function.structural-value")
    def expose(
        module: sc.ModuleContext,
        value: sc.ValueRef[int],
    ) -> sc.ValueRef[int]:
        del module
        return value

    invocation = assert_type(
        expose(point),
        sc.ModuleInvocation[sc.ValueRef[int]],
    )
    [private_port] = invocation.module.definition.interface.imports
    assert private_port.id == "__structural_0"
    assert invocation.inputs[private_port.id] is point

    @sc.experiment(id="test.function.structural-value")
    def authored(experiment: sc.ExperimentContext) -> None:
        experiment.grid(sc.axis(point, (1, 2)))
        experiment.alias(
            experiment.use(invocation),
            record_id="selected_result",
        )

    compile_invocation(authored())


def test_nested_structural_values_are_deduplicated_deterministically() -> None:
    point = sc.coordinate("selected", sc.IntType())

    @sc.module(id="test.function.nested-structural-values")
    def expose(
        module: sc.ModuleContext,
        values: _StructuralValues,
    ) -> sc.ValueRef[int]:
        del module
        assert values.ordered[0].id == values.named["same"].id
        return values.named["same"]

    values = _StructuralValues((point,), {"same": point})
    first = expose(values)
    second = expose(values)

    assert [port.id for port in first.module.definition.interface.imports] == [
        "__structural_0"
    ]
    assert [port.id for port in second.module.definition.interface.imports] == [
        "__structural_0"
    ]
    assert first.inputs["__structural_0"] is point
    assert second.inputs["__structural_0"] is point


def test_structural_child_invocation_closes_its_external_bindings() -> None:
    point = sc.coordinate("selected", sc.IntType())

    @sc.module(id="test.function.structural-child")
    def child(
        module: sc.ModuleContext,
        value: sc.Input[int],
    ) -> sc.ValueRef[int]:
        del module
        return sc.input_ref(value)

    @sc.module(id="test.function.structural-parent")
    def parent(
        module: sc.ModuleContext,
        invocation: sc.ModuleInvocation[sc.ValueRef[int]],
    ) -> sc.ValueRef[int]:
        return module.use(invocation)

    invocation = parent(child(point))
    [private_port] = invocation.module.definition.interface.imports
    [nested] = invocation.module.definition.body.child_instances
    [binding] = nested.input_bindings
    assert private_port.id == "__structural_0"
    assert binding.import_id == "value"
    assert binding.source.value_type == private_port.value_type
    assert invocation.inputs[private_port.id] is point


def test_parametric_context_ingestion_captures_external_values_once() -> None:
    point = sc.coordinate("selected", sc.IntType())

    @sc.module(id="test.function.structural-ingestion")
    def structural_ingestion(
        module: sc.ModuleContext,
        product_id: str,
    ) -> sc.ProductRef:
        module.compute(
            "copy",
            fn=_identity_count,
            inputs={"value": point},
            output_type=sc.ScalarType(_COUNT_TYPE),
        )
        return module._product(
            product_id,
            axes=(ProductAxis("sample", size=point),),
        )

    invocation = structural_ingestion("result")
    [private_port] = invocation.module.definition.interface.imports
    [operation] = invocation.module.definition.body.operations
    [product] = invocation.module.definition.body.products
    assert private_port.id == "__structural_0"
    assert cast("sc.ValueRef", dict(operation.inputs)["value"]).id == (
        cast("sc.ValueRef", product.axes[0].size).id
    )
    assert invocation.inputs[private_port.id] is point


def test_use_returns_typed_results_and_requires_an_occurrence() -> None:
    @sc.module(id="test.use.value")
    def value_source(module: sc.ModuleContext) -> sc.ValueRef:
        return module.compute(
            "value",
            fn=lambda: 1,
            output_type=sc.ScalarType(_COUNT_TYPE),
        )

    @sc.module(id="test.use.noop")
    def noop(module: sc.ModuleContext) -> None:
        del module

    @sc.module(id="test.use.parent")
    def parent(module: sc.ModuleContext) -> sc.ValueRef:
        value = assert_type(module.use(value_source()), sc.ValueRef)
        assert module.use(noop()) is None
        return value

    @sc.experiment(id="test.use.experiment", kind="use")
    def authored(experiment: sc.ExperimentContext) -> None:
        value = assert_type(experiment.use(parent()), sc.ValueRef)
        experiment.alias(value)

    compile_invocation(authored())

    for context in (sc.ModuleContext(), sc.ExperimentContext()):
        with pytest.raises(
            TypeError,
            match=r"use\(\) requires a module invocation or domain call",
        ):
            cast("Callable[[object], object]", context.use)(value_source)


def test_definition_annotations_require_an_unambiguous_value_type() -> None:
    def invalid(module: sc.ModuleContext, value: sc.Input[object]) -> None:
        del module, value

    with pytest.raises(TypeError, match="needs a scalar Python type"):
        sc.module(invalid)

    def mismatched(
        module: sc.ModuleContext,
        value: Annotated[sc.Input[str], sc.IntType()],
    ) -> None:
        del module, value

    with pytest.raises(TypeError, match="Python annotation is incompatible"):
        sc.module(mismatched)


def test_repeated_default_module_calls_require_explicit_instances() -> None:
    @sc.module
    def source(module: sc.ModuleContext) -> None:
        del module

    def repeated(experiment: sc.ExperimentContext) -> None:
        experiment.use(source())
        experiment.use(source())

    with pytest.raises(ValueError, match="duplicate module instance ids"):
        sc.experiment(id="test.repeated-defaults")(repeated)()

    @sc.experiment(id="test.explicit-instances")
    def explicit(experiment: sc.ExperimentContext) -> None:
        experiment.use(source.instantiate("left"))
        experiment.use(source.instantiate("right"))

    compile_invocation(explicit())
