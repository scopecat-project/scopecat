# pyright: reportUnusedFunction=false

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import assert_type

import pytest
from scopecat_testkit.authoring import bind_invocation, load_config

import scopecat as sc
from scopecat.compiler.frontend.elaboration import compose_module
from scopecat.compiler.frontend.resolution import compile_invocation
from scopecat.kernel.errors import CheckFailed
from scopecat.kernel.instrument_members import AcquisitionResultRef
from scopecat.kernel.product_identity import (
    ProductId,
)
from scopecat.kernel.resource_identity import logical_resource_port_id
from scopecat.kernel.symbols import SymbolId
from scopecat.program.identities import InvocationKey
from scopecat.program.measurement_types import MeasurementArrayData
from scopecat.program.module import ModuleInstanceLookup, ModuleProductExport
from scopecat.program.products import (
    ModuleProductDecl,
    ProductRef,
    ProductValueSpec,
    RecordSelection,
    product_axis,
    product_axis_dimension_id,
    record_product,
    record_ref_from_product,
)
from scopecat.sdk.instruments import InterfaceRef

_SCALAR_SIGNAL = InterfaceRef("test.scalar_signal/v1")
_SAMPLE = _SCALAR_SIGNAL.acquisition("sample")


def test_typed_product_schema_survives_export_projection() -> None:
    axis = product_axis("sample", size=4, kind="sample", unit="s")
    expected: ProductValueSpec[MeasurementArrayData] = ProductValueSpec(
        dtype="complex128",
        unit="V",
        axes=(axis,),
    )
    declaration: ModuleProductDecl[MeasurementArrayData] = ModuleProductDecl(
        "trace",
        value_spec=expected,
    )

    declared_ref = ProductRef.from_declaration(declaration)
    export = ModuleProductExport.from_declaration(declaration)
    projected = export.projected_by(
        ModuleInstanceLookup(
            invocation_key=InvocationKey.fresh(),
            instance_id="child",
        )
    )
    projected_ref = ProductRef.from_export(projected)

    assert_type(declared_ref, ProductRef[MeasurementArrayData])
    assert_type(export, ModuleProductExport[MeasurementArrayData])
    assert_type(projected, ModuleProductExport[MeasurementArrayData])
    assert_type(projected_ref, ProductRef[MeasurementArrayData])

    def accept_default_types(
        product_declaration: ModuleProductDecl,
        product_export: ModuleProductExport,
        product_ref: ProductRef,
    ) -> None:
        assert product_declaration.id == product_export.id == product_ref.local_id

    accept_default_types(declaration, export, declared_ref)
    assert declaration.value_spec is expected
    assert declaration.dtype == expected.dtype
    assert declaration.unit == expected.unit
    assert declaration.axes == expected.axes
    assert declared_ref.value_spec == expected
    assert export.value_spec == expected
    assert projected.value_spec == expected
    assert projected_ref.value_spec == expected


def test_product_record_handle_preserves_logical_schema_identity() -> None:
    axis = product_axis("sample", size=4, kind="sample", unit="s")
    declaration: ModuleProductDecl[MeasurementArrayData] = ModuleProductDecl(
        "trace",
        scope=("capture",),
        value_spec=ProductValueSpec(
            unit="V",
            dtype="complex128",
            axes=(axis,),
        ),
    )
    product = ProductRef.from_declaration(declaration)
    selection = record_product(
        product,
        record_id="calibration/trace",
        recording_group_id="calibration/readout",
    )

    record = record_ref_from_product(product, selection)

    assert_type(record, sc.RecordRef[MeasurementArrayData])
    assert record.id == "calibration/trace"
    assert record.dtype == "complex128"
    assert record.unit == "V"
    assert record.dims == ("point", "product/capture/trace/sample")


def test_product_schema_survives_module_definition_and_nested_invocation() -> None:
    axis = product_axis("sample", size=8, kind="sample", unit="s")
    expected = ProductValueSpec(
        dtype="complex128",
        unit="V",
        axes=(axis,),
    )

    @sc.module(id="test.products.typed-source")
    def source(context: sc.ModuleContext) -> sc.ProductRef:
        return context._product(
            "trace",
            unit="V",
            dtype="complex128",
            axes=(axis,),
        )

    child = source.instantiate("child")

    @sc.module(id="test.products.typed-wrapper")
    def wrapper(context: sc.ModuleContext) -> sc.ProductRef:
        context.use(child)
        return child.result

    assert source.definition.products[0].value_spec == expected
    assert source().result.value_spec == expected
    assert wrapper.definition.products[0].value_spec == expected
    assert wrapper().result.value_spec == expected


@dataclass(frozen=True, slots=True)
class _MappedProducts:
    first: sc.ProductRef
    second: sc.ProductRef
    default: sc.ProductRef


@dataclass(frozen=True, slots=True)
class _InstanceProducts:
    left: sc.ProductRef
    right: sc.ProductRef


def _product_module() -> sc.ExperimentModule[sc.ProductRef, ...]:
    @sc.module(id="test.products.source")
    def module(context: sc.ModuleContext) -> sc.ProductRef:
        source = context._resource("source", requires=(_SCALAR_SIGNAL,))
        signal = context._product(
            "signal",
            unit="ratio",
        )
        context._acquire(
            "read-signal",
            resource=source,
            results={_SAMPLE.result("signal"): signal},
            metadata={"adapter_mode": "default"},
        )
        return signal

    return module


def test_selected_product_lowers_schema_and_acquisition_metadata_independently(
    tmp_path: Path,
) -> None:
    @sc.module(id="test.products.metadata")
    def module(context: sc.ModuleContext) -> sc.ProductRef:
        source = context._resource("source", requires=(_SCALAR_SIGNAL,))
        signal = context._product(
            "signal",
            metadata={"schema_owner": "analysis"},
        )
        context._acquire(
            "read-signal",
            resource=source,
            results={_SAMPLE.result("signal"): signal},
            metadata={"adapter_mode": "fast"},
        )
        return signal

    call = module()

    @sc.experiment(id="test.products.metadata", kind="module_products")
    def experiment_definition(experiment: sc.ExperimentContext) -> None:
        experiment.use(call)
        experiment.alias(call.result)

    resolved = bind_invocation(
        experiment_definition.build(),
        config_profile=load_config(),
    )

    assert resolved.bindings.product_defs[0].metadata == {"schema_owner": "analysis"}
    assert resolved.program.program.acquisitions[0].results[0].metadata == {
        "adapter_mode": "fast"
    }
    assert [record.id for record in resolved.bindings.record_uses] == [
        "metadata/signal"
    ]


def test_product_axes_use_product_local_dimensions_by_default() -> None:
    @sc.module(id="test.products.local-axis")
    def module(context: sc.ModuleContext) -> None:
        context._product(
            "i",
            axes=(product_axis("sample", size=2),),
        )
        context._product(
            "q",
            axes=(
                product_axis(
                    "sample",
                    size=3,
                    kind="independent_sample",
                ),
            ),
        )

    call = module()

    @sc.experiment(id="test.products.local-axis", kind="module_products")
    def experiment_definition(experiment: sc.ExperimentContext) -> None:
        experiment.use(call)

    resolved = bind_invocation(
        experiment_definition.build(),
        config_profile=load_config(),
    )

    dimensions = [
        product.axes[0].dimension_id for product in resolved.bindings.product_defs
    ]
    assert len(set(dimensions)) == 2
    assert all(dimension.startswith("product/") for dimension in dimensions)


def test_product_axes_share_dimensions_only_when_explicit() -> None:
    @sc.module(id="test.products.shared-axis")
    def module(context: sc.ModuleContext) -> None:
        context._product(
            "i",
            axes=(
                product_axis(
                    "i_sample",
                    size=2,
                    kind="sample",
                    shared_as="sample",
                ),
            ),
        )
        context._product(
            "q",
            axes=(
                product_axis(
                    "q_sample",
                    size=2,
                    kind="sample",
                    shared_as="sample",
                ),
            ),
        )

    call = module()

    @sc.experiment(id="test.products.shared-axis", kind="module_products")
    def experiment_definition(experiment: sc.ExperimentContext) -> None:
        experiment.use(call)

    resolved = bind_invocation(
        experiment_definition.build(),
        config_profile=load_config(),
    )

    dimensions = [
        product.axes[0].dimension_id for product in resolved.bindings.product_defs
    ]
    assert len(set(dimensions)) == 1
    assert dimensions[0].startswith("shared/")


def test_categorical_product_axis_lowers_to_its_label_count() -> None:
    @sc.module(id="test.products.categorical-axis")
    def module(context: sc.ModuleContext) -> sc.ProductRef:
        source = context._resource("source", requires=(_SCALAR_SIGNAL,))
        iq = context._product(
            "iq",
            dtype="complex128",
            axes=(
                product_axis(
                    "component",
                    size=("I", "Q"),
                    kind="component",
                ),
            ),
        )
        context._acquire(
            "read-iq",
            resource=source,
            results={_SAMPLE.result("iq"): iq},
        )
        return iq

    call = module()

    @sc.experiment(id="test.products.categorical-axis", kind="module_products")
    def experiment_definition(experiment: sc.ExperimentContext) -> None:
        experiment.use(call)

    resolved = bind_invocation(
        experiment_definition.build(),
        config_profile=load_config(),
    )

    [axis] = resolved.bindings.product_defs[0].axes
    assert axis.kind == "component"
    assert axis.size == 2
    assert axis.metadata == {}


def test_variable_product_axis_lowers_without_inventing_a_fixed_extent() -> None:
    @sc.module(id="test.products.ragged-axis")
    def module(context: sc.ModuleContext) -> None:
        context._product(
            "trace",
            axes=(product_axis("sample", size=None),),
        )

    @sc.experiment(id="test.products.ragged-axis", kind="module_products")
    def experiment_definition(experiment: sc.ExperimentContext) -> None:
        experiment.use(module())

    resolved = bind_invocation(
        experiment_definition.build(),
        config_profile=load_config(),
    )

    [axis] = resolved.bindings.product_defs[0].axes
    assert axis.size is None
    assert axis.kind == "sample"


def test_local_and_shared_axis_namespaces_cannot_collide() -> None:
    local_product = ModuleProductDecl(
        id="capture",
        value_spec=ProductValueSpec(),
        scope=("nested",),
    )
    shared_product = ModuleProductDecl(
        id="derived",
        value_spec=ProductValueSpec(),
        scope=("nested", "capture"),
    )
    local_axis = product_axis("sample", size=2)
    shared_axis = product_axis("other", size=2, shared_as="sample")

    assert product_axis_dimension_id(
        local_product,
        local_axis,
    ) != product_axis_dimension_id(shared_product, shared_axis)


def test_conflicting_explicitly_shared_product_axes_are_rejected() -> None:
    @sc.module(id="test.products.shared-axis-conflict")
    def module(context: sc.ModuleContext) -> None:
        context._product(
            "i",
            axes=(
                product_axis(
                    "sample",
                    size=2,
                    shared_as="sample",
                ),
            ),
        )
        context._product(
            "q",
            axes=(
                product_axis(
                    "sample",
                    size=3,
                    shared_as="sample",
                ),
            ),
        )

    call = module()

    @sc.experiment(id="test.products.shared-axis-conflict", kind="module_products")
    def experiment_definition(experiment: sc.ExperimentContext) -> None:
        experiment.use(call)

    with pytest.raises(CheckFailed) as error:
        compile_invocation(experiment_definition.build())

    assert [problem.code for problem in error.value.problems] == [
        "product_axis_conflict"
    ]


def test_acquire_is_an_ordered_effect() -> None:
    @sc.module(id="test.products.acquire")
    def module(context: sc.ModuleContext) -> sc.ProductRef:
        source = context._resource("source", requires=(_SCALAR_SIGNAL,))
        signal = context._product("signal")
        context._acquire(
            "read-signal",
            resource=source,
            results={_SAMPLE.result("signal"): signal},
        )
        return signal

    assembly = compose_module(module.definition)

    acquire = assembly.acquisitions[0]
    assert assembly.effects == (acquire,)
    assert acquire.product_ids == (module.definition.body.products[0].product_id,)


def test_component_scoped_members_lower_complete_targets() -> None:
    interface = InterfaceRef("test.component_signal/v1")
    channel = interface.component("rack").component("channel")

    @sc.module(id="test.products.component-targets")
    def module(context: sc.ModuleContext) -> None:
        source = context._resource("source", requires=(interface,))
        context._bind_property(source, channel.property("gain"), value=1.0)
        context._invoke(
            "zero-channel",
            resource=source,
            operation=channel.operation("zero"),
        )
        signal = context._product("signal")
        context._acquire(
            "read-signal",
            resource=source,
            results={channel.acquisition("sample").result("signal"): signal},
        )

    assembly = compose_module(module.definition)
    [binding] = assembly.bindings
    [invocation] = assembly.invocations
    [acquisition] = assembly.acquisitions

    assert binding.interface_id == interface.interface_id
    assert binding.component_path == ("rack", "channel")
    assert binding.property_id == "gain"
    assert invocation.interface_id == interface.interface_id
    assert invocation.component_path == ("rack", "channel")
    assert invocation.operation_id == "zero"
    assert acquisition.interface_id == interface.interface_id
    assert acquisition.component_path == ("rack", "channel")
    assert acquisition.acquisition_id == "sample"
    assert acquisition.results[0].result_id == "signal"


def test_multi_product_result_mapping_lowers_from_public_authoring_api(
    tmp_path: Path,
) -> None:
    @sc.module(id="test.products.result-mapping")
    def module(context: sc.ModuleContext) -> _MappedProducts:
        source = context._resource("source", requires=(_SCALAR_SIGNAL,))
        first = context._product("first")
        second = context._product("second")
        default = context._product("default")
        context._acquire(
            "read-all",
            resource=source,
            results={
                _SAMPLE.result("raw-first"): first,
                _SAMPLE.result("raw-second"): second,
                _SAMPLE.result("default"): default,
            },
        )
        return _MappedProducts(first, second, default)

    call = module()

    @sc.experiment(id="test.products.result-mapping", kind="module_products")
    def experiment_definition(experiment: sc.ExperimentContext) -> None:
        experiment.use(call)
        experiment.alias(call.result.first)
        experiment.alias(call.result.second)
        experiment.alias(call.result.default)

    resolved = bind_invocation(
        experiment_definition.build(),
        config_profile=load_config(),
    )

    [acquisition] = resolved.program.program.acquisitions
    assert acquisition.interface_id == "test.scalar_signal/v1"
    assert [
        (result.product_id.local_id, result.result_id) for result in acquisition.results
    ] == [
        ("first", "raw-first"),
        ("second", "raw-second"),
        ("default", "default"),
    ]


def test_acquire_rejects_invalid_result_mappings() -> None:
    with pytest.raises(ValueError, match="non-empty id and result mapping"):

        @sc.module(id="test.products.invalid-result-mapping.empty")
        def empty_results(context: sc.ModuleContext) -> None:
            source = context._resource("source", requires=(_SCALAR_SIGNAL,))
            context._acquire("read-both", resource=source, results={})

    mismatched_results = (
        _SCALAR_SIGNAL.acquisition("other").result("raw-second"),
        _SCALAR_SIGNAL.component("channel").acquisition("sample").result("raw-second"),
        InterfaceRef("test.other_signal/v1").acquisition("sample").result("raw-second"),
    )

    def define_mismatched_acquisition(
        mismatched_result: AcquisitionResultRef,
    ) -> None:
        @sc.module(id="test.products.invalid-result-mapping.acquisition")
        def mismatched_acquisition(context: sc.ModuleContext) -> None:
            source = context._resource("source", requires=(_SCALAR_SIGNAL,))
            first = context._product("first")
            second = context._product("second")
            context._acquire(
                "read-both",
                resource=source,
                results={
                    _SAMPLE.result("raw-first"): first,
                    mismatched_result: second,
                },
            )

    for mismatched_result in mismatched_results:
        with pytest.raises(ValueError, match="one acquisition"):
            define_mismatched_acquisition(mismatched_result)

    with pytest.raises(ValueError, match="unique products"):

        @sc.module(id="test.products.invalid-result-mapping.duplicate")
        def duplicate_product(context: sc.ModuleContext) -> None:
            source = context._resource("source", requires=(_SCALAR_SIGNAL,))
            first = context._product("first")
            context._acquire(
                "read-both",
                resource=source,
                results={
                    _SAMPLE.result("raw-first"): first,
                    _SAMPLE.result("raw-second"): first,
                },
            )

    @sc.module(id="test.products.foreign")
    def foreign(context: sc.ModuleContext) -> sc.ProductRef:
        return context._product("first")

    with pytest.raises(ValueError, match="outside this module"):

        @sc.module(id="test.products.invalid-result-mapping.foreign")
        def foreign_product(context: sc.ModuleContext) -> None:
            source = context._resource("source", requires=(_SCALAR_SIGNAL,))
            context._acquire(
                "read-foreign",
                resource=source,
                results={_SAMPLE.result("raw-first"): foreign().result},
            )


def test_explicit_instances_select_same_named_products_independently(
    tmp_path: Path,
) -> None:
    source = _product_module()
    left = source.instantiate("left")
    right = source.instantiate("right")

    @sc.module(id="test.products.root")
    def root(context: sc.ModuleContext) -> _InstanceProducts:
        context.use(left)
        context.use(right)
        return _InstanceProducts(left.result, right.result)

    assert isinstance(left.result, sc.ProductRef)
    assert left.result.id == "left/signal"
    assert right.result.id == "right/signal"

    assembly = compose_module(root.definition)
    assert [product.qualified_id for product in assembly.product_declarations] == [
        "left/signal",
        "right/signal",
    ]
    assert [port.qualified_id for port in assembly.resource_ports] == [
        "left/source",
        "right/source",
    ]
    call = root()

    @sc.experiment(id="test.products.root", kind="module_products")
    def experiment_definition(experiment: sc.ExperimentContext) -> None:
        experiment.use(call)
        experiment.alias(
            call.result.left,
            record_id="left_signal",
        )
        experiment.alias(
            call.result.right,
            record_id="right_signal",
        )

    resolved = bind_invocation(
        experiment_definition.build(),
        config_profile=load_config(),
    )

    assert [record.id for record in resolved.bindings.record_uses] == [
        "left_signal",
        "right_signal",
    ]
    uses_by_id = {use.id: use for use in resolved.bindings.product_uses}
    products_by_id = {product.id: product for product in resolved.bindings.product_defs}
    selected_products = [
        products_by_id[uses_by_id[product_use_id].product_id]
        for record in resolved.bindings.product_record_uses
        for product_use_id in record.product_use_ids
    ]
    acquisitions_by_product = {
        result.product_id: (acquisition, result)
        for acquisition in resolved.program.program.acquisitions
        for result in acquisition.results
    }
    selected_acquisitions = [
        acquisitions_by_product[product.id] for product in selected_products
    ]
    assert [product.id.qualified_name for product in selected_products] == [
        "root/left/signal",
        "root/right/signal",
    ]
    assert [product.product_id for _acquisition, product in selected_acquisitions] == [
        product.id for product in selected_products
    ]
    assert [product.result_id for _acquisition, product in selected_acquisitions] == [
        "signal",
        "signal",
    ]
    assert [
        acquisition.resource_port_id for acquisition, _product in selected_acquisitions
    ] == [
        logical_resource_port_id(SymbolId(scope=("root", "left"), local_id="source")),
        logical_resource_port_id(SymbolId(scope=("root", "right"), local_id="source")),
    ]
    assert [
        acquisition.interface_id for acquisition, _product in selected_acquisitions
    ] == ["test.scalar_signal/v1", "test.scalar_signal/v1"]
    assert [product.metadata for _acquisition, product in selected_acquisitions] == [
        {"adapter_mode": "default"},
        {"adapter_mode": "default"},
    ]


def test_nested_product_references_receive_each_parent_instance_prefix(
    tmp_path: Path,
) -> None:
    inner = _product_module().instantiate("inner")

    @sc.module(id="test.products.wrapper")
    def wrapper(context: sc.ModuleContext) -> sc.ProductRef:
        context.use(inner)
        return inner.result

    projected = wrapper.definition.products[0]
    expected_projection = ProductId(SymbolId(scope=("inner",), local_id="signal"))
    assert projected.symbol_id == expected_projection
    assert projected.target_id == expected_projection
    outer = wrapper.instantiate("outer")

    @sc.module(id="test.products.nested-root")
    def root(context: sc.ModuleContext) -> sc.ProductRef:
        context.use(outer)
        return outer.result

    nested_product = outer.result
    assert nested_product.id == "outer/inner/signal"
    assembly = compose_module(root.definition)
    assert [product.qualified_id for product in assembly.product_declarations] == [
        "outer/inner/signal"
    ]
    call = root()
    nested_product = call.result

    @sc.experiment(id="test.products.nested", kind="module_products")
    def experiment_definition(experiment: sc.ExperimentContext) -> None:
        experiment.use(call)
        experiment.alias(
            nested_product,
            record_id="nested_signal",
        )

    resolved = bind_invocation(
        experiment_definition.build(),
        config_profile=load_config(),
    )

    record = resolved.bindings.product_record_uses[0]
    [product_use_id] = record.product_use_ids
    use = next(
        use for use in resolved.bindings.product_uses if use.id == product_use_id
    )
    assert record.id == "nested_signal"
    expected_product_id = ProductId(
        SymbolId(scope=("nested-root", "outer", "inner"), local_id="signal")
    )
    assert use.product_id == expected_product_id
    [acquisition] = resolved.program.program.acquisitions
    [acquired_result] = acquisition.results
    assert acquired_result.product_id == expected_product_id
    assert acquisition.resource_port_id == logical_resource_port_id(
        SymbolId(
            scope=("nested-root", "outer", "inner"),
            local_id="source",
        )
    )
    assert acquired_result.result_id == "signal"
    assert acquisition.interface_id == "test.scalar_signal/v1"


def test_repeated_product_selection_creates_distinct_use_occurrences(
    tmp_path: Path,
) -> None:
    source = _product_module()
    selected = source.instantiate("selected")

    @sc.module(id="test.products.repeated-use")
    def root(context: sc.ModuleContext) -> sc.ProductRef:
        context.use(selected)
        return selected.result

    call = root()

    @sc.experiment(id="test.products.repeated-use", kind="module_products")
    def experiment_definition(experiment: sc.ExperimentContext) -> None:
        experiment.use(call)
        experiment.alias(
            call.result,
            record_id="first",
        )
        experiment.alias(
            call.result,
            record_id="second",
        )

    resolved = bind_invocation(
        experiment_definition.build(),
        config_profile=load_config(),
    )

    assert len(resolved.bindings.product_uses) == 2
    assert len({use.id for use in resolved.bindings.product_uses}) == 2
    assert {use.product_id for use in resolved.bindings.product_uses} == {
        ProductId(SymbolId(scope=("repeated-use", "selected"), local_id="signal"))
    }


def test_root_module_products_are_typed_experiment_refs() -> None:
    source = _product_module()
    call = source()

    @sc.experiment(id="test.products.root-ref", kind="module_products")
    def experiment_definition(experiment: sc.ExperimentContext) -> None:
        experiment.use(call)
        experiment.alias(call.result)

    [selection] = experiment_definition.bind().definition.record_selections
    assert isinstance(selection, RecordSelection)
    assert selection.product_id == ProductId(
        SymbolId(scope=("source",), local_id="signal")
    )
    assert selection.product_use.product_id == selection.product_id


def test_product_refs_are_nominally_owned_by_the_selected_instance() -> None:
    left_definition = _product_module()
    right_definition = _product_module()
    foreign = left_definition.instantiate("same")
    selected = right_definition.instantiate("same")

    def experiment_definition(experiment: sc.ExperimentContext) -> None:
        experiment.use(selected)
        experiment.alias(foreign.result)

    experiment = sc.experiment(id="test.products.nominal", kind="module_products")(
        experiment_definition
    )

    with pytest.raises(CheckFailed) as error:
        compile_invocation(experiment.build())

    assert [problem.code for problem in error.value.problems] == [
        "module_product_foreign_instance"
    ]
