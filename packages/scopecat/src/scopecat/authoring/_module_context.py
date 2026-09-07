"""Explicit module definition recorder and authoring-boundary normalization."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, fields, is_dataclass, replace
from typing import (
    Annotated,
    TypeAliasType,
    cast,
    get_args,
    get_origin,
    get_type_hints,
    overload,
)

from scopecat.authoring._module_invocation import (
    DomainCallProvider,
    ModuleInvocation,
    domain_use_call,
    module_instance,
)
from scopecat.authoring._module_results import (
    ProductBundle,
    ProductBundleKernel,
    create_product_bundle_internal,
    product_bundle_kernel_type_internal,
    product_bundle_schema_internal,
)
from scopecat.kernel.entity import EntityRef
from scopecat.kernel.frozen import freeze_json_mapping
from scopecat.kernel.instrument_members import (
    AcquisitionResultRef,
    InstrumentCapabilityRef,
    OperationArgumentRef,
    OperationRef,
    PropertyRef,
)
from scopecat.kernel.payloads import PayloadValue, unwrap_payload_values
from scopecat.kernel.quantity import Quantity
from scopecat.kernel.resource_identity import (
    LogicalResourcePortId,
    ResourceRoleInput,
    logical_resource_port_id,
    normalize_resource_role,
)
from scopecat.kernel.units import compatible_units
from scopecat.kernel.value_type_compatibility import (
    describe_value_type,
    is_assignable,
    literal_scalar_type,
)
from scopecat.kernel.value_types import (
    Array,
    ArrayDimension,
    Bool,
    Complex,
    DataType,
    Float,
    Int,
    Payload,
    String,
)
from scopecat.kernel.value_types import Quantity as QuantityType
from scopecat.kernel.value_validation import coerce_literal
from scopecat.program.bindings import (
    BindingIntent,
    EnsureStateIntent,
    ResourcePort,
    ResourceSelector,
    invoke_operation,
    resource_port,
)
from scopecat.program.bindings import bind_property as binding_property
from scopecat.program.domain import DomainCall
from scopecat.program.input_capture import capture_runtime_input
from scopecat.program.measurement_contracts import SingleMeasurementComputeKernel
from scopecat.program.measurement_types import (
    MeasurementDType,
    measurement_value_spec_from_scalar,
)
from scopecat.program.measurements import (
    MeasurementCompute,
    create_measurement_compute_internal,
    create_single_measurement_compute_internal,
)
from scopecat.program.module import (
    ModuleAcquireEffect,
    ModuleAcquireResult,
    ModuleBody,
    ModuleDef,
    ModuleEffect,
    ModuleInstance,
    ModuleInterface,
    ModulePythonImplementation,
    ModuleValueExport,
)
from scopecat.program.operations import ModuleInputPort, ModuleOperationDecl
from scopecat.program.products import (
    ModuleProductDecl,
    ProductAxis,
    ProductRecording,
    ProductRef,
    ProductRefs,
    ProductValueSpec,
    inherit_product_axis_dimension_internal,
)
from scopecat.program.state import StateBinding
from scopecat.program.value_refs import (
    ValueRef,
    internal_input_value_ref,
    internal_value_ref_first_module_export,
    internal_value_ref_input_id,
    internal_value_ref_operation_id,
    internal_value_ref_parameter_contracts,
    internal_value_ref_point_dependencies,
    internal_value_ref_scalar_input_ids,
)
from scopecat.program.value_types import Array as ArrayType
from scopecat.program.value_types import Entity as EntityType
from scopecat.program.value_types import Scalar as ScalarType
from scopecat.program.value_types import ValueType
from scopecat.program.values import (
    ComputeFunction,
    ComputeInput,
    MetadataValue,
    validate_compute_function_internal,
)
from scopecat.program.values import compute as define_compute
from scopecat.records.measurement import (
    MeasurementArray,
    MeasurementPartitionedArray,
    MeasurementScalar,
    MeasurementValue,
)
from scopecat.sdk.compute import (
    compute_capture_names_internal,
    compute_implementation_internal,
    mark_compute_implementation_internal,
)

type BindingInput = StateBinding
type InvocationInput = BindingInput | PayloadValue | None

_BUNDLE_FIELD_IMPLEMENTATION = "scopecat.bundle-field"


@mark_compute_implementation_internal(_BUNDLE_FIELD_IMPLEMENTATION, "1")
def _bundle_field(
    *,
    bundle: object,
    field: str,
    index: int,
) -> object:
    if isinstance(bundle, Mapping):
        return bundle[field]
    if isinstance(bundle, tuple):
        return cast("object", bundle[index])
    if is_dataclass(bundle) and not isinstance(bundle, type):
        return cast("object", getattr(bundle, field))
    raise TypeError("structured compute must return a mapping, tuple, or dataclass")


def _as_product_bundle_type(value: object) -> type[ProductBundle] | None:
    if not isinstance(value, type) or not issubclass(value, ProductBundle):
        return None
    return value


def _infer_compute_output_type(
    fn: ComputeFunction,
) -> DataType | type[ProductBundle]:
    bundle_type = product_bundle_kernel_type_internal(fn)
    if bundle_type is not None:
        return bundle_type
    hints = cast("Mapping[str, object]", get_type_hints(fn, include_extras=True))
    annotation = hints.get("return")
    while isinstance(annotation, TypeAliasType):
        annotation = cast("object", annotation.__value__)
    if get_origin(annotation) is Annotated:
        _native_type, *metadata = cast(
            "tuple[object, ...]",
            get_args(annotation),
        )
        declared = tuple(
            item for item in metadata if isinstance(item, ScalarType | ArrayType)
        )
        if len(declared) == 1:
            return declared[0]
    if annotation is bool:
        return ScalarType(Bool())
    if annotation is int:
        return ScalarType(Int())
    if annotation is float:
        return ScalarType(Float())
    if annotation is complex:
        return ScalarType(Complex())
    if annotation is str:
        return ScalarType(String())
    raise TypeError(
        "compute output_type is required unless the function return annotation "
        "is bool, int, float, complex, str, or Annotated with ScalarType/ArrayType, or "
        "the function is decorated with ProductBundle.kernel"
    )


def _compute_parameter_contracts(fn: ComputeFunction) -> dict[str, DataType]:
    hints = cast("Mapping[str, object]", get_type_hints(fn, include_extras=True))
    contracts: dict[str, DataType] = {}
    for name, annotation in hints.items():
        if name == "return" or get_origin(annotation) is not Annotated:
            if name == "return":
                continue
            while isinstance(annotation, TypeAliasType):
                annotation = cast("object", annotation.__value__)
            if get_origin(annotation) is not Annotated:
                continue
        _native_type, *metadata = cast(
            "tuple[object, ...]",
            get_args(annotation),
        )
        declared = tuple(
            item for item in metadata if isinstance(item, ScalarType | ArrayType)
        )
        if len(declared) > 1:
            raise TypeError(
                f"compute parameter {name!r} has multiple value type annotations"
            )
        if declared:
            contracts[name] = declared[0]
    return contracts


def _validate_compute_input_contracts(
    compute_id: str,
    fn: ComputeFunction,
    inputs: Mapping[str, ComputeInput | ProductRef],
) -> None:
    for name, expected in _compute_parameter_contracts(fn).items():
        value = inputs.get(name)
        if value is None and name not in inputs:
            continue
        actual = _compute_input_data_type(value)
        if _compute_contract_assignable(actual, expected):
            continue
        raise TypeError(
            f"compute {compute_id!r} input {name!r} expects "
            f"{describe_value_type(expected)}, got {describe_value_type(actual)}"
        )


def _compute_input_data_type(value: object) -> DataType:
    if isinstance(value, ProductRef):
        spec = value.value_spec
        if spec.axes:
            return ArrayType(
                dtype=spec.dtype,
                unit=spec.unit,
                dimensions=tuple(
                    ArrayDimension(
                        id=axis.id,
                        size=(
                            axis.size
                            if isinstance(axis.size, int)
                            and not isinstance(axis.size, bool)
                            else None
                        ),
                        kind=axis.kind,
                        unit=axis.unit,
                    )
                    for axis in spec.axes
                ),
            )
        if spec.dtype == "bool":
            return ScalarType(Bool())
        if spec.dtype == "int64" and spec.unit is None:
            return ScalarType(Int())
        if spec.dtype == "float64":
            return ScalarType(
                Float() if spec.unit is None else QuantityType(unit=spec.unit)
            )
        if spec.dtype == "complex128":
            return ScalarType(Complex(unit=spec.unit))
        if spec.dtype == "string" and spec.unit is None:
            return ScalarType(String())
        raise TypeError(
            f"product {value.id!r} scalar schema cannot be expressed as a compute "
            "parameter contract"
        )
    if isinstance(value, ValueRef):
        if not isinstance(value.value_type, ScalarType | ArrayType):
            raise TypeError(
                "compute parameter contracts require scalar or array values"
            )
        return value.value_type
    return literal_scalar_type(value)


def _compute_contract_assignable(actual: DataType, expected: DataType) -> bool:
    if (
        isinstance(actual, ScalarType)
        and isinstance(expected, ScalarType)
        and isinstance(actual.atom, QuantityType)
        and isinstance(expected.atom, QuantityType)
        and actual.atom.unit != expected.atom.unit
    ):
        return False
    return is_assignable(actual, expected)


def _measurement_compute_output_spec(
    value_type: DataType,
    *,
    axes_from: ProductRef | None = None,
) -> tuple[MeasurementDType, str | None, tuple[ProductAxis, ...]]:
    if isinstance(value_type, Array):
        if axes_from is None:
            inherited_axes: dict[str, ProductAxis] = {}
        else:
            inherited_axes = {
                axis.id: inherit_product_axis_dimension_internal(axes_from, axis)
                for axis in axes_from.value_spec.axes
            }
        return (
            value_type.dtype,
            value_type.unit,
            tuple(
                inherited_axes.get(
                    dimension.id,
                    ProductAxis(
                        id=dimension.id,
                        size=dimension.size,
                        kind=dimension.kind,
                        unit=dimension.unit,
                        shared_as=dimension.id,
                    ),
                )
                for dimension in value_type.dimensions
            ),
        )
    dtype, unit = measurement_value_spec_from_scalar(value_type)
    return dtype, unit, ()


def _native_measurement_value(value: MeasurementValue) -> object:
    if isinstance(value, MeasurementArray | MeasurementPartitionedArray):
        return value.values
    if isinstance(value, MeasurementScalar):
        return value.value
    raise AssertionError(
        "unavailable measurement values must not reach compute kernels"
    )


def _converted_unit_type(value_type: DataType, unit: str) -> DataType:
    if isinstance(value_type, ArrayType):
        source_unit = value_type.unit
        if source_unit is None:
            raise TypeError("unit conversion requires a unit-bearing array")
        if not compatible_units(source_unit, unit):
            raise ValueError(f"cannot convert {source_unit!r} to {unit!r}")
        return replace(value_type, unit=unit)
    if isinstance(value_type.atom, Complex):
        source_unit = value_type.atom.unit
        if source_unit is None:
            raise TypeError("unit conversion requires a unit-bearing complex scalar")
        from scopecat.kernel.units import UNIT_SCALE_TO_BASE

        if not compatible_units(source_unit, unit) or any(
            selected not in UNIT_SCALE_TO_BASE for selected in (source_unit, unit)
        ):
            raise ValueError(
                "complex scalar conversion requires compatible linear units"
            )
        return ScalarType(replace(value_type.atom, unit=unit))
    if not isinstance(value_type.atom, QuantityType):
        raise TypeError("unit conversion requires a quantity value")
    source_unit = value_type.atom.unit
    if source_unit is None:
        raise TypeError("unit conversion requires a quantity with a declared unit")
    if not compatible_units(source_unit, unit):
        raise ValueError(f"cannot convert {source_unit!r} to {unit!r}")

    def convert_bound(value: float | None) -> float | None:
        if value is None:
            return None
        return float(Quantity(value, source_unit).to(unit).value)

    return ScalarType(
        replace(
            value_type.atom,
            unit=unit,
            minimum=convert_bound(value_type.atom.minimum),
            maximum=convert_bound(value_type.atom.maximum),
        )
    )


def _convert_unit_value(
    *,
    value: object,
    source_unit: str,
    target_unit: str,
) -> object:
    if isinstance(value, Quantity):
        return value.to(target_unit)
    scale = Quantity(1.0, source_unit).to(target_unit).value
    return cast("object", value * scale)  # pyright: ignore[reportOperatorIssue]


def _measurement_compute_result(
    value: object,
    value_type: DataType,
) -> MeasurementValue:
    selected = coerce_literal(value_type, value, path=("measurement_compute", "output"))
    if isinstance(value_type, Array):
        return MeasurementArray.create(
            values=selected,
            dtype=value_type.dtype,
            unit=value_type.unit,
        )
    dtype, unit = measurement_value_spec_from_scalar(value_type)
    if isinstance(selected, Quantity):
        scalar_value: object = selected.value
    elif isinstance(selected, EntityRef):
        scalar_value = selected.id
    else:
        scalar_value = selected
    return MeasurementScalar.create(
        value=scalar_value,
        dtype=dtype,
        unit=unit,
    )


def _is_entity_input_type(value_type: ValueType) -> bool:
    return isinstance(value_type, ScalarType) and isinstance(
        value_type.atom, EntityType
    )


def _is_public_state_binding(value: object) -> bool:
    return (
        isinstance(value, ValueRef) and isinstance(value.value_type, ScalarType)
    ) or isinstance(value, Quantity | EntityRef | str | int | float | complex | bool)


def _is_public_invocation_input(value: object) -> bool:
    return (
        value is None
        or _is_payload_binding_input(value)
        or _is_public_state_binding(value)
    )


def _require_public_state_binding(value: object) -> None:
    if value is None:
        raise TypeError(
            "persistent state bindings cannot be None; omit the property instead"
        )
    if _is_payload_binding_input(value):
        raise TypeError("persistent properties cannot contain opaque payloads")
    if not _is_public_state_binding(value):
        raise TypeError(
            "module bindings require a scalar typed value or scalar literal"
        )


@dataclass(frozen=True, slots=True)
class DefinitionResource:
    """One logical resource owned by a module definition context."""

    port_id: LogicalResourcePortId
    owner: object = field(repr=False, compare=False)

    @property
    def id(self) -> str:
        return self.port_id.local_id


class ModuleContext:
    """Explicit typed recorder injected into one ``@module`` definition."""

    __slots__ = (
        "_capture_external_values",
        "_captured_input_bindings",
        "_captured_input_ports",
        "_captured_values",
        "_declared_input_ids",
        "_effect_namespaces",
        "_effects",
        "_local_value_ids",
        "_measurement_computes",
        "_operations",
        "_owner",
        "_product_declarations",
        "_python_implementations",
        "_resource_namespaces",
        "_resources",
    )

    def __init__(
        self,
        *,
        capture_external_values: bool = False,
        declared_inputs: Mapping[str, ValueRef] | None = None,
    ) -> None:
        self._owner = object()
        selected_inputs = declared_inputs or {}
        self._capture_external_values = capture_external_values
        self._declared_input_ids = set(selected_inputs)
        self._local_value_ids = {value.id for value in selected_inputs.values()}
        self._captured_values: dict[object, ValueRef] = {}
        self._captured_input_ports: list[ModuleInputPort] = []
        self._captured_input_bindings: dict[str, ValueRef] = {}
        self._effect_namespaces: set[str] = set()
        self._resource_namespaces: set[str] = set()
        self._resources: list[ResourcePort] = []
        self._effects: list[ModuleEffect] = []
        self._operations: list[ModuleOperationDecl] = []
        self._python_implementations: list[ModulePythonImplementation] = []
        self._measurement_computes: list[MeasurementCompute] = []
        self._product_declarations: list[ModuleProductDecl] = []

    def append_invocation_internal[ResultT](
        self,
        invocation: ModuleInvocation[ResultT],
    ) -> None:
        """Append one child after immediately closing its interface bindings."""

        selected = cast(
            "ModuleInvocation[ResultT]",
            self._capture_nested_values(invocation),
        )
        self._effects.append(module_instance(selected))

    def append_domain_call_internal(self, call: DomainCall) -> None:
        """Append one native domain occurrence and its result declarations."""

        selected = self._capture_domain_call(call)
        self._effects.append(selected.execution)
        self._product_declarations.extend(selected.product_declarations)

    def capture_structural_value_internal[T](self, value: T) -> T:
        """Recursively close symbolic structural arguments behind private imports."""

        if not self._capture_external_values:
            return value
        return cast("T", self._capture_nested_values(value))

    def capture_result_internal[T](self, value: T) -> T:
        """Close external value leaves exposed by a parametric module result."""

        return cast("T", self._capture_nested_values(value))

    @property
    def captured_input_bindings_internal(self) -> Mapping[str, ValueRef]:
        """Return invocation-owned bindings for generated private imports."""

        return dict(self._captured_input_bindings)

    def close_definition_internal(
        self,
        *,
        id: str,
        input_ports: Sequence[ModuleInputPort] = (),
        value_exports: Sequence[ModuleValueExport] = (),
        metadata: Mapping[str, MetadataValue] | None = None,
    ) -> ModuleDef:
        """Freeze this context directly as one reusable module definition."""

        return ModuleDef(
            id=id,
            interface=ModuleInterface(
                imports=(*input_ports, *self._captured_input_ports),
                exports=tuple(value_exports),
                resources=tuple(self._resources),
            ),
            body=self._close_body(),
            python_implementations=tuple(self._python_implementations),
            metadata=freeze_json_mapping(metadata or {}),
        )

    def _capture_domain_call(self, call: DomainCall) -> DomainCall:
        if not self._capture_external_values:
            return call

        execution = replace(
            call.execution,
            input_bindings=tuple(
                (name, self._capture_domain_value(value))
                for name, value in call.execution.input_bindings
            ),
            compiler_input_bindings=tuple(
                (name, self._capture_domain_value(value))
                for name, value in call.execution.compiler_input_bindings
            ),
        )
        products = tuple(
            replace(
                product,
                value_spec=replace(
                    product.value_spec,
                    axes=tuple(
                        replace(
                            axis,
                            size=self._capture_domain_value(axis.size),
                        )
                        if isinstance(axis.size, ValueRef)
                        else axis
                        for axis in product.axes
                    ),
                ),
            )
            for product in call.product_declarations
        )
        return replace(
            call,
            execution=execution,
            product_declarations=products,
        )

    def _capture_nested_values(self, value: object) -> object:
        if not self._capture_external_values:
            return value
        if isinstance(value, ValueRef):
            return self._capture_domain_value(value)
        if isinstance(value, ModuleInvocation):
            invocation = cast("ModuleInvocation[object]", value)
            selected_inputs = {
                name: cast("ValueRef", self._capture_domain_value(source))
                for name, source in invocation.inputs.items()
            }
            if all(
                selected_inputs[name] is source
                for name, source in invocation.inputs.items()
            ):
                return invocation
            return replace(invocation, inputs=selected_inputs)
        if isinstance(value, DomainCall):
            return self._capture_domain_call(value)
        if isinstance(value, tuple):
            source = cast("tuple[object, ...]", value)
            selected_tuple: tuple[object, ...] = tuple(
                self._capture_nested_values(item) for item in source
            )
            return (
                cast("object", value)
                if all(a is b for a, b in zip(selected_tuple, source, strict=True))
                else selected_tuple
            )
        if isinstance(value, list):
            source = cast("list[object]", value)
            selected_list: list[object] = [
                self._capture_nested_values(item) for item in source
            ]
            return (
                cast("object", value)
                if all(a is b for a, b in zip(selected_list, source, strict=True))
                else selected_list
            )
        if isinstance(value, Mapping):
            source = cast("Mapping[object, object]", value)
            selected_mapping: dict[object, object] = {
                key: self._capture_nested_values(item) for key, item in source.items()
            }
            return (
                cast("object", value)
                if all(selected_mapping[key] is item for key, item in source.items())
                else selected_mapping
            )
        if is_dataclass(value) and not isinstance(value, type):
            updates = {
                member.name: self._capture_nested_values(
                    cast("object", getattr(value, member.name))
                )
                for member in fields(value)
                if member.init
            }
            if all(
                selected is getattr(value, name) for name, selected in updates.items()
            ):
                return value
            return replace(value, **updates)
        return value

    def _capture_domain_value(self, value: object) -> object:
        if (
            not self._capture_external_values
            or not isinstance(value, ValueRef)
            or not self._is_external_value(value)
        ):
            return value
        return self._capture_external_value(value)

    def _is_external_value(self, value: ValueRef) -> bool:
        if value.id in self._local_value_ids:
            return False
        if internal_value_ref_input_id(value) is not None:
            return True
        if internal_value_ref_point_dependencies(value):
            return True
        if internal_value_ref_parameter_contracts(value):
            return True
        input_ids = internal_value_ref_scalar_input_ids(value)
        if input_ids:
            return not input_ids <= self._declared_input_ids
        operation_id = internal_value_ref_operation_id(value)
        if operation_id is not None:
            return True
        selected_export = internal_value_ref_first_module_export(value)
        if selected_export is None:
            return False
        invocation_key, _export_id = selected_export
        return all(
            not isinstance(effect, ModuleInstance)
            or effect.invocation_key != invocation_key
            for effect in self._effects
        )

    def _capture_external_value(self, value: ValueRef) -> ValueRef:
        selected = self._captured_values.get(value.id)
        if selected is not None:
            return selected
        local_inputs: frozenset[str] = (
            frozenset()
            if internal_value_ref_input_id(value) is not None
            else internal_value_ref_scalar_input_ids(value) & self._declared_input_ids
        )
        if local_inputs:
            rendered = ", ".join(sorted(local_inputs))
            raise TypeError(
                "captured structural values cannot also depend on module inputs: "
                f"{rendered}"
            )
        capture_id = self._next_capture_id()
        selected = internal_input_value_ref(capture_id, value.value_type)
        self._captured_values[value.id] = selected
        self._captured_input_ports.append(
            ModuleInputPort(id=capture_id, value_type=value.value_type)
        )
        self._captured_input_bindings[capture_id] = value
        self._declared_input_ids.add(capture_id)
        self._local_value_ids.add(selected.id)
        return selected

    def _next_capture_id(self) -> str:
        index = len(self._captured_input_ports)
        while True:
            selected = f"__structural_{index}"
            if selected not in self._declared_input_ids:
                return selected
            index += 1

    def close_experiment_parts_internal(
        self,
        *,
        input_ports: Sequence[ModuleInputPort] = (),
    ) -> tuple[ModuleInterface, ModuleBody, tuple[ModulePythonImplementation, ...]]:
        """Freeze the structural parts owned directly by an experiment."""

        return (
            ModuleInterface(
                imports=tuple(input_ports),
                resources=tuple(self._resources),
            ),
            self._close_body(),
            tuple(self._python_implementations),
        )

    def _close_body(self) -> ModuleBody:
        return ModuleBody(
            effects=tuple(self._effects),
            operations=tuple(self._operations),
            measurement_computes=tuple(self._measurement_computes),
            products=tuple(self._product_declarations),
        )

    @overload
    def use[ResultT](
        self,
        part: ModuleInvocation[ResultT],
    ) -> ResultT: ...

    @overload
    def use(self, part: DomainCall) -> ProductRefs: ...

    @overload
    def use(self, part: DomainCallProvider) -> ProductRefs: ...

    def use(
        self,
        part: object,
    ) -> object:
        """Place one explicitly constructed module or domain occurrence."""

        if isinstance(part, ModuleInvocation):
            invocation = cast("ModuleInvocation[object]", part)
            self.append_invocation_internal(invocation)
            return invocation.result
        try:
            call = domain_use_call(part)
        except TypeError as error:
            raise TypeError(
                "use() requires a module invocation or domain call"
            ) from error
        self.append_domain_call_internal(call)
        return call.results

    def _resource(
        self,
        id: str,
        *,
        requires: Sequence[InstrumentCapabilityRef] = (),
        for_entities: Sequence[ValueRef] = (),
        role: ResourceRoleInput = None,
    ) -> DefinitionResource:
        """Declare a logical resource and its required instrument capabilities."""

        selected_entities = tuple(
            cast("ValueRef", self._capture_domain_value(value))
            for value in for_entities
        )
        for value in selected_entities:
            if not _is_entity_input_type(value.value_type):
                raise TypeError("resource for_entities values must be entity-shaped")
            if internal_value_ref_operation_id(value) is not None:
                raise TypeError("resource for_entities cannot use compute outputs")
        self._resources.append(
            resource_port(
                id,
                ResourceSelector(
                    capabilities=tuple(requires),
                    entity_inputs=selected_entities,
                    role=normalize_resource_role(role),
                ),
            )
        )
        return DefinitionResource(logical_resource_port_id(id), self._owner)

    def _allocate_resource_id(self, name_hint: str) -> str:
        """Allocate one stable authoring-local namespace for a typed client."""

        if not name_hint:
            raise ValueError("resource name hint must be non-empty")
        used = {
            *self._resource_namespaces,
            *(resource.id for resource in self._resources),
        }
        resource_id = name_hint
        suffix = 2
        while resource_id in used:
            resource_id = f"{name_hint}.{suffix}"
            suffix += 1
        self._resource_namespaces.add(resource_id)
        return resource_id

    def _allocate_effect_id(self, name_hint: str, *, explicit: bool = False) -> str:
        """Allocate a data/effect namespace independently of resource ports."""

        if not name_hint:
            raise ValueError("effect name hint must be non-empty")
        if explicit:
            if name_hint in self._effect_namespaces:
                raise ValueError(f"duplicate explicit effect id: {name_hint}")
            self._effect_namespaces.add(name_hint)
            return name_hint
        effect_id = name_hint
        suffix = 2
        while effect_id in self._effect_namespaces:
            effect_id = f"{name_hint}.{suffix}"
            suffix += 1
        self._effect_namespaces.add(effect_id)
        return effect_id

    def _bind_property(
        self,
        resource: DefinitionResource,
        property: PropertyRef,
        *,
        value: BindingInput,
    ) -> None:
        """Record a generated client's persistent property binding."""

        self._require_owned_resource(resource)
        selected_value = cast("BindingInput", self._capture_domain_value(value))
        _require_public_state_binding(selected_value)
        self._effects.append(
            binding_property(
                resource.id,
                interface=property.interface_id,
                component_path=property.component_path,
                property=property.property_id,
                value=cast(
                    "BindingInput",
                    _capture_binding_literal(selected_value),
                ),
            )
        )

    def _ensure(
        self,
        resource: DefinitionResource,
        assignments: Mapping[PropertyRef, StateBinding],
    ) -> None:
        """Record a generated client's coherent resource target state."""

        self._require_owned_resource(resource)
        selected_assignments = {
            property: cast("StateBinding", self._capture_domain_value(value))
            for property, value in assignments.items()
        }
        self._effects.append(
            build_ensure_state_intent(resource.port_id, selected_assignments)
        )

    def _ensure_many(
        self,
        targets: Sequence[
            tuple[DefinitionResource, Mapping[PropertyRef, StateBinding]]
        ],
    ) -> None:
        """Record one coherent state effect spanning logical resources."""

        bindings: list[BindingIntent] = []
        for resource, assignments in targets:
            self._require_owned_resource(resource)
            selected_assignments = {
                property: cast("StateBinding", self._capture_domain_value(value))
                for property, value in assignments.items()
            }
            bindings.extend(
                build_ensure_state_intent(
                    resource.port_id,
                    selected_assignments,
                ).assignments
            )
        if not bindings:
            raise ValueError("ensure requires at least one target assignment")
        self._effects.append(EnsureStateIntent(tuple(bindings)))

    def _invoke(
        self,
        id: str,
        *,
        resource: DefinitionResource,
        operation: OperationRef,
        arguments: Mapping[OperationArgumentRef, InvocationInput] | None = None,
    ) -> None:
        """Record a generated client's ordered atomic hardware operation."""

        self._require_owned_resource(resource)
        selected_arguments = {
            target: cast("InvocationInput", self._capture_domain_value(value))
            for target, value in (arguments or {}).items()
        }
        if any(target.operation != operation for target in selected_arguments):
            raise ValueError(
                "module invocation arguments must belong to the selected operation"
            )
        if any(
            not _is_public_invocation_input(value)
            for value in selected_arguments.values()
        ):
            raise TypeError("module invocation arguments require scalar values")
        self._effects.append(
            invoke_operation(
                id,
                port_id=resource.id,
                interface=operation.interface_id,
                component_path=operation.component_path,
                operation=operation.operation_id,
                arguments={
                    target.argument_id: cast(
                        "InvocationInput", _capture_binding_literal(value)
                    )
                    for target, value in selected_arguments.items()
                },
            )
        )

    def _product(
        self,
        id: str,
        *,
        scope: Sequence[str] = (),
        unit: str | None = "ratio",
        dtype: MeasurementDType = "float64",
        axes: Sequence[ProductAxis] = (),
        recording: ProductRecording | None = None,
        metadata: Mapping[str, MetadataValue] | None = None,
    ) -> ProductRef:
        """Declare a logical product for a generated acquisition or producer."""

        selected_axes = tuple(
            replace(
                axis,
                size=self._capture_domain_value(axis.size),
            )
            if isinstance(axis.size, ValueRef)
            else axis
            for axis in axes
        )
        declaration = ModuleProductDecl(
            id,
            scope=tuple(scope),
            origin=(object(),),
            value_spec=ProductValueSpec(
                dtype=dtype,
                unit=unit,
                axes=selected_axes,
            ),
            recording=recording,
            metadata=freeze_json_mapping(metadata or {}),
        )
        self._product_declarations.append(declaration)
        return ProductRef.from_declaration(declaration)

    def _acquire(
        self,
        id: str,
        *,
        resource: DefinitionResource,
        results: Mapping[AcquisitionResultRef, ProductRef],
        metadata: Mapping[str, MetadataValue] | None = None,
    ) -> None:
        """Map a generated client's acquisition results to declared products."""

        self._require_owned_resource(resource)
        if not id or not results:
            raise ValueError("acquire requires a non-empty id and result mapping")
        available_products = self._products()
        selected_results: list[tuple[AcquisitionResultRef, ProductRef]] = []
        for result, product in results.items():
            expected_product = available_products.get(product.id)
            if (
                expected_product is None
                or expected_product.product_id != product.product_id
                or expected_product.origin != product.origin
            ):
                raise ValueError(
                    "acquire result references a product outside this module: "
                    f"{product.id!r}"
                )
            selected_results.append((result, expected_product))
        acquisition = selected_results[0][0].acquisition
        if any(result.acquisition != acquisition for result, _ in selected_results):
            raise ValueError("acquire results must belong to one acquisition")
        selected_products = tuple(product for _, product in selected_results)
        if len({product.product_id for product in selected_products}) != len(
            selected_products
        ):
            raise ValueError("acquire results must map to unique products")
        selected_metadata = freeze_json_mapping(metadata or {})
        self._effects.append(
            ModuleAcquireEffect(
                id=id,
                resource_port_id=resource.port_id,
                interface_id=acquisition.interface_id,
                component_path=acquisition.component_path,
                acquisition_id=acquisition.acquisition_id,
                results=tuple(
                    ModuleAcquireResult(
                        product=product,
                        result_id=result.result_id,
                        metadata=selected_metadata,
                    )
                    for result, product in selected_results
                ),
            )
        )

    def _products(self) -> ProductRefs:
        products = (
            *(
                ProductRef.from_export(
                    product.projected_by(instance.lookup),
                )
                for instance in self._effects
                if isinstance(instance, ModuleInstance)
                for product in instance.module.products
            ),
            *(
                ProductRef.from_declaration(product)
                for product in self._product_declarations
            ),
        )
        return ProductRefs({product.id: product for product in products})

    def _require_owned_resource(self, resource: DefinitionResource) -> None:
        if resource.owner is not self._owner:
            raise ValueError("definition resource must belong to this module context")

    def require_owned_resource_internal(
        self,
        resource: DefinitionResource,
    ) -> None:
        """Validate a resource at a containing authoring boundary."""

        self._require_owned_resource(resource)

    @overload
    def convert[T](
        self,
        value: ValueRef[T],
        unit: str,
        *,
        id: str | None = None,
    ) -> ValueRef[T]: ...

    @overload
    def convert(
        self,
        value: ProductRef,
        unit: str,
        *,
        id: str | None = None,
    ) -> ProductRef: ...

    def convert(
        self,
        value: ValueRef | ProductRef,
        unit: str,
        *,
        id: str | None = None,
    ) -> ValueRef | ProductRef:
        """Convert a unit-bearing reference without changing its availability."""

        source_type = _compute_input_data_type(value)
        output_type = _converted_unit_type(source_type, unit)
        source_unit = (
            source_type.unit
            if isinstance(source_type, ArrayType)
            else cast("QuantityType | Complex", source_type.atom).unit
        )
        if source_unit is None:
            raise AssertionError("converted unit types must have a source unit")
        return cast(
            "ValueRef | ProductRef",
            self.compute(
                id,
                fn=_convert_unit_value,
                value=value,
                source_unit=source_unit,
                target_unit=unit,
                output_type=output_type,
            ),
        )

    @overload
    def compute(
        self,
        id: str | None = None,
        *,
        fn: ComputeFunction,
        inputs: Mapping[str, ProductRef],
        output_type: ScalarType | ArrayType,
        axes_from: ProductRef | None = None,
    ) -> ProductRef: ...

    @overload
    def compute(
        self,
        id: str | None = None,
        *,
        fn: ComputeFunction,
        inputs: Mapping[str, ProductRef],
        output_type: Mapping[str, DataType],
        axes_from: ProductRef | None = None,
    ) -> ProductRefs: ...

    @overload
    def compute(
        self,
        id: str | None = None,
        *,
        fn: ComputeFunction,
        inputs: Mapping[str, ComputeInput] | None = None,
        output_type: ScalarType | ArrayType,
        axes_from: ProductRef | None = None,
    ) -> ValueRef: ...

    @overload
    def compute(
        self,
        id: str | None = None,
        *,
        fn: ComputeFunction,
        inputs: Mapping[str, ComputeInput | ProductRef],
        output_type: ScalarType | ArrayType,
        axes_from: ProductRef | None = None,
    ) -> ProductRef: ...

    @overload
    def compute(
        self,
        id: str | None = None,
        *,
        fn: ComputeFunction,
        inputs: Mapping[str, ComputeInput | ProductRef],
        output_type: Mapping[str, DataType],
        axes_from: ProductRef | None = None,
    ) -> ProductRefs: ...

    @overload
    def compute[BundleT: ProductBundle](
        self,
        id: str | None = None,
        *,
        fn: ProductBundleKernel[BundleT],
        inputs: Mapping[str, ComputeInput | ProductRef] | None = None,
        output_type: None = None,
        axes_from: ProductRef | None = None,
        **input_bindings: ComputeInput | ProductRef,
    ) -> BundleT: ...

    @overload
    def compute[BundleT: ProductBundle](
        self,
        id: str | None = None,
        *,
        fn: ComputeFunction,
        inputs: Mapping[str, ComputeInput | ProductRef] | None = None,
        output_type: type[BundleT],
        axes_from: ProductRef | None = None,
        **input_bindings: ComputeInput | ProductRef,
    ) -> BundleT: ...

    @overload
    def compute(
        self,
        id: str | None = None,
        *,
        fn: ComputeFunction,
        inputs: Mapping[str, ComputeInput | ProductRef] | None = None,
        output_type: ScalarType | ArrayType | Mapping[str, DataType] | None = None,
        axes_from: ProductRef | None = None,
        **input_bindings: ComputeInput | ProductRef,
    ) -> ValueRef | ProductRef | ProductRefs: ...

    def compute(
        self,
        id: str | None = None,
        *,
        fn: ComputeFunction,
        inputs: Mapping[str, ComputeInput | ProductRef] | None = None,
        output_type: (
            ScalarType | ArrayType | Mapping[str, DataType] | type[ProductBundle] | None
        ) = None,
        axes_from: ProductRef | None = None,
        **input_bindings: ComputeInput | ProductRef,
    ) -> ValueRef | ProductRef | ProductRefs | ProductBundle:
        """Declare a compute where its inputs exist.

        Array dimensions matching an ``axes_from`` input axis reuse its complete
        logical identity, including entity values and shared-axis semantics.
        """

        duplicate_inputs = set(inputs or {}) & set(input_bindings)
        if duplicate_inputs:
            rendered = ", ".join(sorted(duplicate_inputs))
            raise TypeError(f"compute inputs were bound more than once: {rendered}")
        selected_inputs = {**(inputs or {}), **input_bindings}
        declared_bundle_type = product_bundle_kernel_type_internal(fn)
        if declared_bundle_type is not None and output_type is not None:
            raise TypeError(
                "compute output_type must be omitted when ProductBundle.kernel "
                "declares the structured schema"
            )
        selected_output_type = (
            _infer_compute_output_type(fn) if output_type is None else output_type
        )
        selected_id = self._allocate_effect_id(
            _compute_name_hint(fn) if id is None else id,
            explicit=id is not None,
        )
        _validate_compute_input_contracts(selected_id, fn, selected_inputs)
        if any(isinstance(value, ProductRef) for value in selected_inputs.values()):
            return self._compute_measurements(
                selected_id,
                fn=fn,
                inputs=selected_inputs,
                output_type=selected_output_type,
                axes_from=axes_from,
            )
        if axes_from is not None:
            raise ValueError("compute axes_from must name one of its measured inputs")

        bundle_type = _as_product_bundle_type(selected_output_type)
        if bundle_type is not None:
            return self._compute_values_bundle(
                selected_id,
                fn=fn,
                inputs=selected_inputs,
                bundle_type=bundle_type,
            )
        if not isinstance(selected_output_type, ScalarType | ArrayType):
            raise TypeError("structured compute output must be a product bundle type")

        captured_inputs = {
            name: cast("ComputeInput", self._capture_domain_value(value))
            for name, value in selected_inputs.items()
        }
        definition = define_compute(
            selected_id,
            fn=fn,
            inputs=captured_inputs,
            output_type=selected_output_type,
        )
        self._operations.append(
            ModuleOperationDecl(
                id=definition.id,
                declaration_key=definition.declaration_key,
                input_types=definition.input_types,
                inputs=definition.inputs,
                output_type=definition.output_type,
            )
        )
        self._python_implementations.append(
            ModulePythonImplementation(
                declaration_key=definition.declaration_key,
                fn=definition.fn,
            )
        )
        self._local_value_ids.add(definition.output.id)
        return definition.output

    def _compute_values_bundle[BundleT: ProductBundle](
        self,
        id: str,
        *,
        fn: ComputeFunction,
        inputs: Mapping[str, ComputeInput | ProductRef],
        bundle_type: type[BundleT],
    ) -> BundleT:
        """Lower one structured host compute to an opaque result plus projections."""

        output_types = dict(product_bundle_schema_internal(bundle_type))
        captured_inputs = {
            name: cast("ComputeInput", self._capture_domain_value(value))
            for name, value in inputs.items()
        }
        definition = define_compute(
            id,
            fn=fn,
            inputs=captured_inputs,
            output_type=ScalarType(Payload("scopecat.compute-bundle.v1")),
        )
        self._operations.append(
            ModuleOperationDecl(
                id=definition.id,
                declaration_key=definition.declaration_key,
                input_types=definition.input_types,
                inputs=definition.inputs,
                output_type=definition.output_type,
            )
        )
        self._python_implementations.append(
            ModulePythonImplementation(
                declaration_key=definition.declaration_key,
                fn=definition.fn,
            )
        )
        self._local_value_ids.add(definition.output.id)

        outputs: dict[str, ValueRef] = {}
        for index, (name, output_type) in enumerate(output_types.items()):
            projection_id = self._allocate_effect_id(
                f"{id}.outputs.{name}",
                explicit=False,
            )
            projection = define_compute(
                projection_id,
                fn=_bundle_field,
                inputs={
                    "bundle": definition.output,
                    "field": name,
                    "index": index,
                },
                output_type=output_type,
            )
            self._operations.append(
                ModuleOperationDecl(
                    id=projection.id,
                    declaration_key=projection.declaration_key,
                    input_types=projection.input_types,
                    inputs=projection.inputs,
                    output_type=projection.output_type,
                )
            )
            self._python_implementations.append(
                ModulePythonImplementation(
                    declaration_key=projection.declaration_key,
                    fn=projection.fn,
                )
            )
            self._local_value_ids.add(projection.output.id)
            outputs[name] = projection.output
        return create_product_bundle_internal(bundle_type, outputs)

    def _compute_measurements(
        self,
        id: str,
        *,
        fn: ComputeFunction,
        inputs: Mapping[str, ComputeInput | ProductRef],
        output_type: (DataType | Mapping[str, DataType] | type[ProductBundle]),
        axes_from: ProductRef | None,
    ) -> ProductRef | ProductRefs | ProductBundle:
        """Lower measured inputs to the point-local observation stage."""

        input_names = tuple(inputs)
        validate_compute_function_internal(id, fn, input_names)
        product_inputs = {
            name: value
            for name, value in inputs.items()
            if isinstance(value, ProductRef)
        }
        if axes_from is not None and all(
            product.product_id != axes_from.product_id
            for product in product_inputs.values()
        ):
            raise ValueError("compute axes_from must name one of its measured inputs")
        value_inputs = {
            name: cast("ComputeInput", self._capture_domain_value(value))
            for name, value in inputs.items()
            if not isinstance(value, ProductRef)
        }
        bundle_type = _as_product_bundle_type(output_type)
        if bundle_type is not None:
            output_types = dict(product_bundle_schema_internal(bundle_type))
        elif isinstance(output_type, ScalarType | ArrayType):
            output_types = {"result": output_type}
        elif isinstance(output_type, Mapping):
            output_types = dict(output_type)
        else:
            raise TypeError("structured compute output must be a product bundle type")
        if not output_types or any(not name for name in output_types):
            raise ValueError("structured compute output names must be non-empty")
        structured = not isinstance(output_type, ScalarType | ArrayType)
        outputs: dict[str, ProductRef] = {}
        for name, value_type in output_types.items():
            dtype, unit, axes = _measurement_compute_output_spec(
                value_type,
                axes_from=axes_from,
            )
            outputs[name] = self._product(
                name if structured else id,
                scope=(id,) if bundle_type is not None else (),
                unit=unit,
                dtype=dtype,
                axes=axes,
            )

        def kernel(
            values: Mapping[str, object],
        ) -> Mapping[str, MeasurementValue]:
            raw = fn(
                **{
                    name: (
                        _native_measurement_value(
                            cast("MeasurementValue", values[name])
                        )
                        if name in product_inputs
                        else unwrap_payload_values(values[name])
                    )
                    for name in input_names
                }
            )
            raw_outputs: Mapping[str, object]
            if (
                bundle_type is not None
                and is_dataclass(raw)
                and not isinstance(raw, type)
            ):
                raw_outputs = {
                    name: cast("object", getattr(raw, name)) for name in output_types
                }
            elif bundle_type is not None and isinstance(raw, tuple):
                raw_tuple = cast("tuple[object, ...]", raw)
                if len(raw_tuple) != len(output_types):
                    raise ValueError(
                        "structured compute tuple length must match its product bundle"
                    )
                raw_outputs = dict(zip(output_types, raw_tuple, strict=True))
            else:
                raw_outputs = (
                    cast("Mapping[str, object]", raw) if structured else {"result": raw}
                )
            if set(raw_outputs) != set(output_types):
                raise ValueError(
                    "structured compute result keys must exactly match output_type"
                )
            return {
                name: _measurement_compute_result(raw_outputs[name], value_type)
                for name, value_type in output_types.items()
            }

        implementation = compute_implementation_internal(fn)
        self._measurement_computes.append(
            create_measurement_compute_internal(
                id,
                inputs=product_inputs,
                value_inputs=value_inputs,
                outputs=outputs,
                kernel=kernel,
                implementation=(
                    None if implementation is None else implementation.reference
                ),
                deterministic=(
                    False if implementation is None else implementation.deterministic
                ),
                captures=compute_capture_names_internal(fn),
            )
        )
        if bundle_type is not None:
            return create_product_bundle_internal(bundle_type, outputs)
        return ProductRefs(outputs) if structured else outputs["result"]

    def _measurement_compute(
        self,
        id: str,
        *,
        input: ProductRef,
        outputs: Mapping[str, ProductRef],
        kernel: SingleMeasurementComputeKernel,
    ) -> None:
        """Register a typed producer's point-local measurement calculation."""

        self._measurement_computes.append(
            create_single_measurement_compute_internal(
                id,
                input=input,
                outputs=outputs,
                kernel=kernel,
            )
        )


def build_ensure_state_intent(
    resource: LogicalResourcePortId,
    assignments: Mapping[PropertyRef, StateBinding],
) -> EnsureStateIntent:
    """Normalize coherent property assignments at an authoring boundary."""

    assignment_items = tuple(assignments.items())
    if not assignment_items:
        raise ValueError("ensure requires at least one target assignment")

    bindings: list[BindingIntent] = []
    for property, value in assignment_items:
        _require_public_state_binding(value)
        bindings.append(
            binding_property(
                resource,
                interface=property.interface_id,
                component_path=property.component_path,
                property=property.property_id,
                value=cast("BindingInput", _capture_binding_literal(value)),
            )
        )
    return EnsureStateIntent(tuple(bindings))


def _capture_binding_literal(value: object) -> object:
    if isinstance(value, ValueRef | PayloadValue):
        return value
    return capture_runtime_input(value)


def _compute_name_hint(fn: ComputeFunction) -> str:
    name = cast("str", getattr(fn, "__name__", ""))
    selected = name.lstrip("_")
    return selected if selected and selected != "<lambda>" else "compute"


def _is_payload_binding_input(value: object) -> bool:
    return isinstance(value, PayloadValue) or (
        isinstance(value, ValueRef)
        and isinstance(value.value_type, ScalarType)
        and isinstance(value.value_type.atom, Payload)
    )
