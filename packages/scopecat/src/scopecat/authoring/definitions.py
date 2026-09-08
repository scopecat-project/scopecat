"""Function-based module and experiment definitions."""

from __future__ import annotations

import inspect
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, fields, is_dataclass, replace
from types import UnionType
from typing import (
    Annotated,
    Concatenate,
    TypeAliasType,
    cast,
    get_args,
    get_origin,
    get_type_hints,
    overload,
)

from scopecat.authoring._experiment_module import (
    ExperimentModule,
    create_experiment_module_internal,
    create_parametric_experiment_module_internal,
)
from scopecat.authoring._module_context import (
    DefinitionResource,
    ModuleContext,
    build_ensure_state_intent,
)
from scopecat.authoring._module_invocation import (
    DomainCallProvider,
    ModuleInvocation,
    domain_use_call,
)
from scopecat.authoring._module_results import (
    ProductBundle,
    ProductBundleKernel,
    RecordedProducts,
    module_result_value_exports,
)
from scopecat.authoring.entity_selection import PerEntity
from scopecat.authoring.experiments import (
    Experiment,
)
from scopecat.authoring.member_projection import StateProjector
from scopecat.authoring.scans import (
    Axis,
    PointRow,
    ScanCenterInput,
    ScanCoordinate,
    ScanValue,
    ScanValueType,
    scan_axis,
)
from scopecat.kernel.entity import EntityRef, entity_axis_fingerprint, entity_identity
from scopecat.kernel.frozen import freeze_json_mapping
from scopecat.kernel.instrument_members import (
    AcquisitionResultRef,
    InstrumentCapabilityRef,
    OperationArgumentRef,
    OperationRef,
    PropertyRef,
)
from scopecat.kernel.payloads import PayloadValue
from scopecat.kernel.product_identity import parse_product_id
from scopecat.kernel.quantity import Quantity as QuantityValue
from scopecat.kernel.resource_identity import ResourceRoleInput
from scopecat.program.bindings import BindingIntent
from scopecat.program.controls import ControlSet
from scopecat.program.definitions import (
    ExperimentDef,
    ExperimentInvocation,
    capture_experiment_inputs,
    create_experiment_def,
)
from scopecat.program.domain import DomainCall
from scopecat.program.measurement_contracts import SingleMeasurementComputeKernel
from scopecat.program.measurement_types import (
    EntityAcquisitionPolicy,
    EntityAcquisitionSemantics,
    MeasurementArrayData,
    MeasurementDType,
    MeasurementSegmentedData,
    MeasurementVariableRole,
    NativeMeasurementValue,
    measurement_value_spec_from_scalar,
)
from scopecat.program.operations import ModuleInputPort
from scopecat.program.products import (
    EntityAxisDef,
    EntityRecordMemberSelection,
    EntityRecordSelection,
    ProductAxis,
    ProductRecording,
    ProductRef,
    ProductRefs,
    RecordSelection,
    _product_axis_dimension_id,
    record_alias,
    record_coordinate,
    record_product,
    record_ref_from_product,
)
from scopecat.program.record_refs import RecordRef
from scopecat.program.recording import (
    ExperimentResultField,
    ProgramRecordSelection,
    ValueRecordSelection,
)
from scopecat.program.scans import (
    GridSpec,
    PointDomainSpec,
    PointGrouping,
    PointPlan,
    PointTraversal,
    RepeatMode,
    points_spec,
)
from scopecat.program.state import StateBinding
from scopecat.program.value_refs import (
    CoordinateRef,
    ValueRef,
    internal_coordinate_ref_id,
    internal_value_ref_point_dependencies,
    internal_value_ref_point_id,
    internal_value_ref_record_source_id,
    internal_value_ref_requires_execution,
    internal_value_ref_source_id,
)
from scopecat.program.value_types import (
    Array,
    Bool,
    Complex,
    DataType,
    Entity,
    Float,
    Int,
    Payload,
    Quantity,
    Scalar,
    String,
    Table,
    ValueType,
)
from scopecat.program.values import (
    ComputeFunction,
    ComputeInput,
    MetadataValue,
    ModuleInput,
    RuntimeInput,
)
from scopecat.program.values import input as authoring_input
from scopecat.program.verification import validate_experiment_inputs

# pyright: reportPrivateUsage=false

type DefinitionFunction = Callable[..., object]
type Symbolic[T] = T | ValueRef[T]
type Input[T] = T | ValueRef[T]


@dataclass(frozen=True, slots=True)
class Result:
    """Durable recording policy attached to an experiment result field."""

    id: str | None = None
    namespace: str | None = None
    role: MeasurementVariableRole | None = None
    metadata: Mapping[str, MetadataValue] | None = None

    def __post_init__(self) -> None:
        if self.id is not None and not self.id:
            raise ValueError("result id must be non-empty")
        if self.namespace is not None:
            _record_namespace_segments(self.namespace)
        if self.id is not None and self.namespace is not None:
            raise ValueError("result id and namespace cannot be used together")
        object.__setattr__(self, "metadata", freeze_json_mapping(self.metadata or {}))


def input_ref[T](value: Input[T]) -> ValueRef[T]:
    """View a decorator function input as its symbolic authoring reference.

    ``@module`` and ``@experiment`` evaluate their Python bodies with symbolic
    values, while ``Input[T]`` also preserves the concrete caller contract.
    Use this helper only when a symbolic-only operation, such as a table
    transform, needs that distinction for static typing.
    """

    return cast("ValueRef[T]", value)


def _recording_role_is_coordinate(product: ProductRef) -> bool:
    return product._recording_role == "coordinate"


def _record_namespace_segments(namespace: str) -> tuple[str, ...]:
    if not namespace:
        raise ValueError("record namespace must be non-empty")
    namespace_id = parse_product_id(namespace)
    return (*namespace_id.scope, namespace_id.local_id)


def _namespaced_record_id(
    product: ProductRef,
    namespace: tuple[str, ...],
) -> str:
    return product.product_id.prefixed(*namespace).qualified_name


def _recording_group_id(
    product: ProductRef,
    namespace: tuple[str, ...],
) -> str | None:
    if product._recording is None:
        return None
    return product._recording.occurrence.prefixed(*namespace).qualified_name


@dataclass(frozen=True, slots=True)
class _ModuleContract:
    """One module function's runtime-import and structural argument split."""

    signature: inspect.Signature
    runtime_arguments: tuple[tuple[str, ValueRef], ...]

    @property
    def runtime_values(self) -> dict[str, ValueRef]:
        return dict(self.runtime_arguments)

    @property
    def runtime_names(self) -> frozenset[str]:
        return frozenset(name for name, _value in self.runtime_arguments)

    @property
    def structural_names(self) -> frozenset[str]:
        return frozenset(self.signature.parameters) - self.runtime_names


@dataclass(frozen=True, slots=True)
class _ExperimentContract:
    """One experiment function's runtime-input and structural argument split."""

    signature: inspect.Signature
    runtime_arguments: tuple[tuple[str, ValueRef], ...]

    @property
    def runtime_values(self) -> dict[str, ValueRef]:
        return dict(self.runtime_arguments)

    @property
    def runtime_names(self) -> frozenset[str]:
        return frozenset(name for name, _value in self.runtime_arguments)

    @property
    def structural_names(self) -> frozenset[str]:
        return frozenset(self.signature.parameters) - self.runtime_names


class ExperimentContext:
    """Explicit recorder injected into one complete experiment definition."""

    __slots__ = (
        "_entity_axes",
        "_point_domain_mode",
        "_point_plan",
        "_program",
        "_record_selections",
        "_result_fields",
        "_success_state_bindings",
    )

    def __init__(self) -> None:
        self._program = ModuleContext()
        self._point_plan = PointPlan()
        self._point_domain_mode = "none"
        self._entity_axes: dict[str, EntityAxisDef] = {}
        self._record_selections: list[ProgramRecordSelection] = []
        self._result_fields: list[ExperimentResultField] = []
        self._success_state_bindings: list[BindingIntent] = []

    def close_definition_internal(
        self,
        *,
        id: str,
        kind: str,
        metadata: Mapping[str, MetadataValue] | None,
        input_ports: Sequence[ModuleInputPort] = (),
        input_defaults: Mapping[str, RuntimeInput],
        required_inputs: Sequence[str],
        controls: ControlSet | None = None,
    ) -> ExperimentDef:
        """Freeze this context directly as one experiment definition."""

        interface, body, python_implementations = (
            self._program.close_experiment_parts_internal(
                input_ports=input_ports,
            )
        )
        return create_experiment_def(
            id=id,
            kind=kind,
            interface=interface,
            body=body,
            python_implementations=python_implementations,
            record_selections=self._record_selections,
            result_fields=self._result_fields,
            input_defaults=input_defaults,
            required_inputs=required_inputs,
            default_point_plan=self._point_plan,
            controls=controls,
            success_state_bindings=self._success_state_bindings,
            metadata=metadata,
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
        """Place one module or domain occurrence in the root experiment."""

        if isinstance(part, ModuleInvocation):
            invocation = cast("ModuleInvocation[object]", part)
            self._program.append_invocation_internal(invocation)
            return invocation.result
        try:
            call = domain_use_call(part)
        except TypeError as error:
            raise TypeError(
                "use() requires a module invocation or domain call"
            ) from error
        self._program.append_domain_call_internal(call)
        return call.results

    def _resource(
        self,
        id: str,
        *,
        requires: Sequence[InstrumentCapabilityRef] = (),
        for_entities: Sequence[ValueRef] = (),
        role: ResourceRoleInput = None,
    ) -> DefinitionResource:
        """Declare a logical resource for a generated symbolic client."""

        return self._program._resource(
            id,
            requires=requires,
            for_entities=for_entities,
            role=role,
        )

    def _allocate_resource_id(self, name_hint: str) -> str:
        """Allocate an internal namespace for a generated symbolic client."""

        return self._program._allocate_resource_id(name_hint)

    def _allocate_effect_id(self, name_hint: str, *, explicit: bool = False) -> str:
        """Allocate a public effect/data namespace independently of routing."""

        return self._program._allocate_effect_id(name_hint, explicit=explicit)

    def _bind_property(
        self,
        resource: DefinitionResource,
        property: PropertyRef,
        *,
        value: StateBinding,
    ) -> None:
        """Bind a property for an internal authoring extension."""

        self._program._bind_property(resource, property, value=value)

    def _ensure(
        self,
        resource: DefinitionResource,
        assignments: Mapping[PropertyRef, StateBinding],
    ) -> None:
        """Declare a target state for a generated symbolic client."""

        self._program._ensure(resource, assignments)

    def _ensure_many(
        self,
        targets: Sequence[
            tuple[DefinitionResource, Mapping[PropertyRef, StateBinding]]
        ],
    ) -> None:
        """Declare one coherent state target across logical resources."""

        self._program._ensure_many(targets)

    def _invoke(
        self,
        id: str,
        *,
        resource: DefinitionResource,
        operation: OperationRef,
        arguments: Mapping[
            OperationArgumentRef,
            StateBinding | PayloadValue | None,
        ]
        | None = None,
    ) -> None:
        """Append an operation for a generated symbolic client."""

        self._program._invoke(
            id,
            resource=resource,
            operation=operation,
            arguments=arguments,
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

        return self._program._product(
            id,
            scope=scope,
            unit=unit,
            dtype=dtype,
            axes=axes,
            recording=recording,
            metadata=metadata,
        )

    def _acquire(
        self,
        id: str,
        *,
        resource: DefinitionResource,
        results: Mapping[AcquisitionResultRef, ProductRef],
        metadata: Mapping[str, MetadataValue] | None = None,
    ) -> None:
        """Map acquisition results for a generated symbolic client."""

        self._program._acquire(
            id,
            resource=resource,
            results=results,
            metadata=metadata,
        )

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
        """Convert a unit-bearing reference at its inferred compute placement."""

        return self._program.convert(value, unit, id=id)

    @overload
    def compute(
        self,
        id: str | None = None,
        *,
        fn: ComputeFunction,
        inputs: Mapping[str, ProductRef],
        output_type: Scalar | Array,
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
        output_type: Scalar | Array,
        axes_from: ProductRef | None = None,
    ) -> ValueRef: ...

    @overload
    def compute(
        self,
        id: str | None = None,
        *,
        fn: ComputeFunction,
        inputs: Mapping[str, ComputeInput | ProductRef],
        output_type: Scalar | Array,
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
        output_type: Scalar | Array | Mapping[str, DataType] | None = None,
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
            Scalar | Array | Mapping[str, DataType] | type[ProductBundle] | None
        ) = None,
        axes_from: ProductRef | None = None,
        **input_bindings: ComputeInput | ProductRef,
    ) -> ValueRef | ProductRef | ProductRefs | ProductBundle:
        """Declare an experiment compute, optionally reusing matching input axes."""

        return self._program.compute(
            id,
            fn=fn,
            inputs=inputs,
            output_type=output_type,
            axes_from=axes_from,
            **input_bindings,
        )

    def _measurement_compute(
        self,
        id: str,
        *,
        input: ProductRef,
        outputs: Mapping[str, ProductRef],
        kernel: SingleMeasurementComputeKernel,
    ) -> None:
        """Register a typed producer's point-local measurement calculation."""

        self._program._measurement_compute(
            id,
            input=input,
            outputs=outputs,
            kernel=kernel,
        )

    def grid(
        self,
        *axes: Axis,
        repeat: int = 1,
        repeat_mode: RepeatMode = "point",
        traversal: PointTraversal = "forward",
    ) -> None:
        """Declare the complete Cartesian point domain for this experiment."""

        self._declare_point_domain(
            GridSpec(tuple(axes)),
            repeat=repeat,
            repeat_mode=repeat_mode,
            traversal=traversal,
        )

    def group_points(
        self,
        id: str,
        *,
        varying: Sequence[CoordinateRef],
    ) -> None:
        """Prefer related points together and restart an interrupted group.

        Coordinates in ``varying`` may differ inside a group. Every other
        coordinate identifies the group, so group sizes may vary naturally.
        This is a scheduling preference and recovery boundary, not a hardware
        batch requirement.
        """

        self._point_plan = replace(
            self._point_plan,
            schedule=replace(
                self._point_plan.schedule,
                grouping=PointGrouping(
                    id=id,
                    varying_coordinate_ids=tuple(
                        internal_coordinate_ref_id(coordinate) for coordinate in varying
                    ),
                ),
            ),
        )

    @overload
    def scan[T: ScanValue](
        self,
        id: str,
        values: Iterable[T],
        *,
        value_type: ScanValueType | None = None,
        overlay: None = None,
        unit: None = None,
    ) -> CoordinateRef[T]: ...

    @overload
    def scan(
        self,
        id: str,
        values: Iterable[int | float],
        *,
        value_type: ScanValueType | None = None,
        overlay: None = None,
        unit: str,
    ) -> CoordinateRef[QuantityValue]: ...

    @overload
    def scan[T](
        self,
        id: str,
        values: None = None,
        *,
        value_type: ScanValueType | None = None,
        overlay: ValueRef[T],
        unit: str | None = None,
        span: ScanCoordinate,
        points: int,
    ) -> CoordinateRef[T]: ...

    @overload
    def scan(
        self,
        id: str,
        values: None = None,
        *,
        value_type: ScanValueType | None = None,
        overlay: None = None,
        unit: str | None = None,
        start: QuantityValue | str,
        stop: QuantityValue | str,
        points: int,
    ) -> CoordinateRef[QuantityValue]: ...

    @overload
    def scan(
        self,
        id: str,
        values: None = None,
        *,
        value_type: ScanValueType | None = None,
        overlay: None = None,
        unit: None = None,
        start: int,
        stop: int,
        points: int,
    ) -> CoordinateRef[int]: ...

    @overload
    def scan(
        self,
        id: str,
        values: None = None,
        *,
        value_type: ScanValueType | None = None,
        overlay: None = None,
        unit: None = None,
        start: float,
        stop: float,
        points: int,
    ) -> CoordinateRef[float]: ...

    def scan(
        self,
        id: str,
        values: Iterable[ScanValue] | None = None,
        *,
        value_type: ScanValueType | None = None,
        overlay: ValueRef | None = None,
        unit: str | None = None,
        start: ScanCoordinate | None = None,
        stop: ScanCoordinate | None = None,
        center: ScanCenterInput | None = None,
        span: ScanCoordinate | None = None,
        points: int | None = None,
    ) -> CoordinateRef[object]:
        """Declare one inferred grid coordinate and return its symbolic value."""

        if self._point_domain_mode == "explicit":
            raise ValueError(
                "experiment scan cannot be combined with grid() or points()"
            )
        target, selected_axis = scan_axis(
            id,
            values,
            value_type=value_type,
            overlay=overlay,
            unit=unit,
            start=start,
            stop=stop,
            center=center,
            span=span,
            points=points,
        )
        domain = self._point_plan.domain
        existing_axes = domain.axes if isinstance(domain, GridSpec) else ()
        self._point_plan = replace(
            self._point_plan,
            domain=GridSpec((*existing_axes, selected_axis)),
        )
        self._point_domain_mode = "scan"
        return target

    def points(
        self,
        rows: Sequence[PointRow],
        *,
        coordinates: Sequence[CoordinateRef] = (),
        repeat: int = 1,
        repeat_mode: RepeatMode = "point",
    ) -> None:
        """Declare the complete point cloud in its explicit row order."""

        self._declare_point_domain(
            points_spec(rows, coordinates=coordinates),
            repeat=repeat,
            repeat_mode=repeat_mode,
            traversal="forward",
        )

    def _declare_point_domain(
        self,
        domain: PointDomainSpec,
        *,
        repeat: int,
        repeat_mode: RepeatMode,
        traversal: PointTraversal,
    ) -> None:
        if self._point_domain_mode == "scan":
            raise ValueError(
                "experiment grid() or points() cannot be combined with scan"
            )
        if self._point_domain_mode == "explicit":
            raise ValueError("experiment point domain can only be declared once")
        self._point_plan = replace(
            self._point_plan,
            domain=domain,
            repeat=repeat,
            repeat_mode=repeat_mode,
            schedule=replace(
                self._point_plan.schedule,
                traversal=traversal,
            ),
        )
        self._point_domain_mode = "explicit"

    @overload
    def alias[T: NativeMeasurementValue](
        self,
        value: ProductRef[T],
        /,
        *,
        record_id: str | None = None,
        namespace: str | None = None,
        metadata: Mapping[str, MetadataValue] | None = None,
    ) -> RecordRef[T]: ...

    @overload
    def alias(
        self,
        value: ProductBundle,
        /,
        *,
        record_id: None = None,
        namespace: str | None = None,
        metadata: Mapping[str, MetadataValue] | None = None,
    ) -> RecordedProducts: ...

    @overload
    def alias(
        self,
        value: ValueRef[QuantityValue],
        /,
        *,
        record_id: str | None = None,
        namespace: str | None = None,
        role: MeasurementVariableRole = "observable",
        metadata: Mapping[str, MetadataValue] | None = None,
    ) -> RecordRef[float]: ...

    @overload
    def alias(
        self,
        value: ValueRef[EntityRef | str],
        /,
        *,
        record_id: str | None = None,
        namespace: str | None = None,
        role: MeasurementVariableRole = "observable",
        metadata: Mapping[str, MetadataValue] | None = None,
    ) -> RecordRef[str]: ...

    @overload
    def alias[T: bool | int | float | str](
        self,
        value: ValueRef[T],
        /,
        *,
        record_id: str | None = None,
        namespace: str | None = None,
        role: MeasurementVariableRole = "observable",
        metadata: Mapping[str, MetadataValue] | None = None,
    ) -> RecordRef[T]: ...

    @overload
    def alias(
        self,
        value: ValueRef[object],
        /,
        *,
        record_id: str | None = None,
        namespace: str | None = None,
        role: MeasurementVariableRole = "observable",
        metadata: Mapping[str, MetadataValue] | None = None,
    ) -> RecordRef[NativeMeasurementValue]: ...

    def alias(
        self,
        value: ProductRef | ProductBundle | ValueRef,
        /,
        *,
        record_id: str | None = None,
        namespace: str | None = None,
        role: MeasurementVariableRole | None = None,
        metadata: Mapping[str, MetadataValue] | None = None,
    ) -> object:
        """Create an explicit durable alias outside the normal return tree.

        Experiment returns remain the ordinary durability path. Use an alias
        only when one source needs an additional dynamic destination.
        """

        if record_id is not None and namespace is not None:
            raise ValueError("record_id and namespace cannot be used together")
        if role is not None and not isinstance(value, ValueRef):
            raise TypeError("record role can only be selected for symbolic values")
        if isinstance(value, PerEntity):
            raise TypeError(
                "alias() does not expand PerEntity values; use stack_entities() "
                "for homogeneous products"
            )
        if isinstance(value, ProductBundle):
            if record_id is not None:
                raise ValueError("record_id cannot name a product bundle")

            def record_product_leaf[T: NativeMeasurementValue](
                product: ProductRef[T],
                /,
            ) -> RecordRef[T]:
                return self._record_product(
                    product,
                    namespace=namespace,
                    role=None,
                    metadata=metadata,
                )

            return value._records_internal(record_product_leaf)
        if isinstance(value, ProductRef):
            return self._record_product(
                value,
                record_id=record_id,
                namespace=namespace,
                role=None,
                metadata=metadata,
            )
        return self._record_value(
            value,
            record_id=record_id,
            namespace=namespace,
            role="observable" if role is None else role,
            metadata=metadata,
        )

    def stack_entities(
        self,
        value: PerEntity[ProductRef],
        /,
        *,
        record_id: str,
        axis: str | EntityAxisDef | None = None,
        role: MeasurementVariableRole | None = None,
        acquisition_policy: EntityAcquisitionPolicy = "independent",
        cohort_id: str | None = None,
        metadata: Mapping[str, MetadataValue] | None = None,
    ) -> RecordRef[MeasurementArrayData | MeasurementSegmentedData]:
        """Record homogeneous entity products as one indexed array variable."""

        if not record_id:
            raise ValueError("entity record id must be non-empty")
        if isinstance(axis, str) and not axis:
            raise ValueError("entity record axis must be non-empty")
        return _record_entity_products_output(
            self,
            value,
            record_id=record_id,
            axis=axis,
            role=role,
            acquisition=EntityAcquisitionSemantics(
                policy=acquisition_policy,
                cohort_id=cohort_id,
            ),
            metadata=metadata,
        )

    def _record_product[T: NativeMeasurementValue](
        self,
        value: ProductRef[T],
        *,
        record_id: str | None = None,
        namespace: str | None,
        role: MeasurementVariableRole | None,
        metadata: Mapping[str, MetadataValue] | None,
    ) -> RecordRef[T]:
        namespace_segments = (
            () if namespace is None else _record_namespace_segments(namespace)
        )
        selection_factory = (
            record_coordinate
            if role == "coordinate"
            or (role is None and _recording_role_is_coordinate(value))
            else record_product
        )
        selection = selection_factory(
            value,
            record_id=(
                _namespaced_record_id(value, namespace_segments)
                if namespace_segments
                else record_id
            ),
            recording_group_id=(
                _recording_group_id(value, namespace_segments)
                if namespace_segments
                else None
            ),
            metadata=freeze_json_mapping(metadata or {}),
        )
        self._record_selections.append(selection)
        return record_ref_from_product(value, selection)

    def _record_value(
        self,
        value: ValueRef,
        *,
        record_id: str | None,
        namespace: str | None,
        role: MeasurementVariableRole,
        metadata: Mapping[str, MetadataValue] | None,
    ) -> RecordRef[NativeMeasurementValue]:
        value_type = value.value_type
        if isinstance(value_type, Scalar):
            atom = value_type.atom
            if isinstance(atom, Payload):
                raise TypeError("opaque payload values cannot be recorded in a dataset")
            if isinstance(atom, Quantity) and atom.unit is None:
                raise TypeError(
                    "recorded quantity values require an explicit declared unit"
                )
        elif not isinstance(value_type, Array):
            raise TypeError("dataset value records must be scalar or array values")
        namespace_segments = (
            () if namespace is None else _record_namespace_segments(namespace)
        )
        default_source_id = internal_value_ref_source_id(value)
        if record_id is None and default_source_id is None:
            raise ValueError(
                "recording an unnamed symbolic expression requires record_id"
            )
        source_value_id = internal_value_ref_record_source_id(value)
        selected_id = (
            record_id
            or parse_product_id(source_value_id)
            .prefixed(*namespace_segments)
            .qualified_name
        )
        self._record_selections.append(
            ValueRecordSelection(
                value=value,
                record_id=record_id,
                namespace=namespace_segments,
                role=role,
                metadata=freeze_json_mapping(metadata or {}),
            )
        )
        if isinstance(value_type, Scalar):
            dtype, unit = measurement_value_spec_from_scalar(value_type)
            dims = ("point",)
        else:
            dtype, unit = value_type.dtype, value_type.unit
            dims = ("point", *(dimension.id for dimension in value_type.dimensions))
        return RecordRef(
            id=selected_id,
            dtype=dtype,
            unit=unit,
            dims=dims,
        )

    def on_success[StateT](
        self,
        resource: StateProjector[StateT],
        target: StateT,
    ) -> None:
        """Declare fixed state after normal point/effect completion."""

        for logical_resource, desired_state in resource.state_targets(target):
            self._append_success_state_target(logical_resource, desired_state)

    def _append_success_state_target(
        self,
        resource: DefinitionResource,
        assignments: Mapping[PropertyRef, StateBinding],
    ) -> None:
        """Validate and append one normalized success-state target."""

        self._program.require_owned_resource_internal(resource)
        intent = build_ensure_state_intent(resource.port_id, assignments)
        for assignment in intent.assignments:
            value = assignment.value
            if not isinstance(value, ValueRef):
                continue
            if internal_value_ref_requires_execution(value):
                raise ValueError(
                    "experiment on_success state cannot depend on point-local "
                    "computation"
                )
            if internal_value_ref_point_dependencies(value):
                raise ValueError(
                    "experiment on_success state cannot depend on point coordinates"
                )
        self._success_state_bindings.extend(intent.assignments)


@overload
def module[ResultT, **P](
    definition: Callable[Concatenate[ModuleContext, P], ResultT],
    /,
    *,
    id: str | None = None,
    metadata: Mapping[str, MetadataValue] | None = None,
) -> ExperimentModule[ResultT, P]: ...


@overload
def module[ResultT, **P](
    definition: None = None,
    /,
    *,
    id: str | None = None,
    metadata: Mapping[str, MetadataValue] | None = None,
) -> Callable[
    [Callable[Concatenate[ModuleContext, P], ResultT]],
    ExperimentModule[ResultT, P],
]: ...


def module[ResultT, **P](
    definition: Callable[Concatenate[ModuleContext, P], ResultT] | None = None,
    /,
    *,
    id: str | None = None,
    metadata: Mapping[str, MetadataValue] | None = None,
) -> (
    ExperimentModule[ResultT, P]
    | Callable[
        [Callable[Concatenate[ModuleContext, P], ResultT]],
        ExperimentModule[ResultT, P],
    ]
):
    """Define a typed module with runtime imports and structural arguments.

    Parameters annotated as ``Input[T]`` become typed module imports. Every
    other parameter specializes a closed module definition for that invocation.
    """

    if definition is None:
        return lambda fn: _module_from_function(fn, id=id, metadata=metadata)
    return _module_from_function(definition, id=id, metadata=metadata)


@overload
def experiment[ResultT, **P](
    definition: Callable[Concatenate[ExperimentContext, P], ResultT],
    /,
    *,
    id: str | None = None,
    kind: str | None = None,
    metadata: Mapping[str, MetadataValue] | None = None,
    controls: ControlSet | None = None,
) -> Experiment[P, ResultT]: ...


@overload
def experiment[ResultT, **P](
    definition: None = None,
    /,
    *,
    id: str | None = None,
    kind: str | None = None,
    metadata: Mapping[str, MetadataValue] | None = None,
    controls: ControlSet | None = None,
) -> Callable[
    [Callable[Concatenate[ExperimentContext, P], ResultT]],
    Experiment[P, ResultT],
]: ...


def experiment[ResultT, **P](
    definition: Callable[Concatenate[ExperimentContext, P], ResultT] | None = None,
    /,
    *,
    id: str | None = None,
    kind: str | None = None,
    metadata: Mapping[str, MetadataValue] | None = None,
    controls: ControlSet | None = None,
) -> (
    Experiment[P, ResultT]
    | Callable[
        [Callable[Concatenate[ExperimentContext, P], ResultT]],
        Experiment[P, ResultT],
    ]
):
    """Define one experiment with explicit runtime and structural arguments.

    Parameters annotated as ``Input[T]`` become typed runtime inputs. Every
    other parameter is an ordinary Python value evaluated while constructing
    that invocation's graph.
    """

    def decorate(
        fn: Callable[Concatenate[ExperimentContext, P], ResultT],
    ) -> Experiment[P, ResultT]:
        return _experiment_from_function(
            fn,
            id=id,
            kind=kind,
            metadata=metadata,
            controls=controls,
        )

    return decorate(definition) if definition is not None else decorate


def _module_from_function[ResultT, **P](
    fn: Callable[Concatenate[ModuleContext, P], ResultT],
    *,
    id: str | None,
    metadata: Mapping[str, MetadataValue] | None,
) -> ExperimentModule[ResultT, P]:
    source = cast("DefinitionFunction", fn)
    contract = _module_contract(source)
    signature = contract.signature
    runtime_names = contract.runtime_names
    selected_metadata = dict(metadata or {})
    doc = inspect.getdoc(fn)
    if doc is not None:
        selected_metadata.setdefault("description", doc)
    selected_metadata = dict(freeze_json_mapping(selected_metadata))
    selected_id = id or _definition_id(fn)

    def close(
        structural_values: Mapping[str, object],
        *,
        capture_external_values: bool,
    ) -> ExperimentModule[ResultT, P]:
        runtime_values = contract.runtime_values
        context = ModuleContext(
            capture_external_values=capture_external_values,
            declared_inputs=runtime_values,
        )
        values: dict[str, object] = dict(runtime_values)
        for name, value in structural_values.items():
            values[name] = context.capture_structural_value_internal(value)
        result = context.capture_result_internal(source(context, **values))
        module_def = context.close_definition_internal(
            id=selected_id,
            input_ports=tuple(
                _input_port(name, value) for name, value in contract.runtime_arguments
            ),
            value_exports=module_result_value_exports(result),
            metadata=selected_metadata,
        )
        return create_experiment_module_internal(
            module_def,
            definition=cast("Callable[P, ResultT]", fn),
            signature=signature,
            result=cast("ResultT", result),
            implicit_inputs=context.captured_input_bindings_internal,
        )

    if not contract.structural_names:
        return close({}, capture_external_values=False)

    def build(
        instance_id: str,
        arguments: Mapping[str, object],
    ) -> ModuleInvocation[ResultT]:
        structural_values: dict[str, object] = {}
        runtime_inputs: dict[str, ModuleInput] = {}
        for parameter in signature.parameters.values():
            if parameter.name in runtime_names:
                runtime_inputs[parameter.name] = cast(
                    "ModuleInput", arguments[parameter.name]
                )
                continue
            if parameter.name in arguments:
                structural_values[parameter.name] = arguments[parameter.name]
                continue
            default = cast("object", parameter.default)
            if default is not inspect.Parameter.empty:
                structural_values[parameter.name] = default
                continue
            raise TypeError(f"missing required structural argument: {parameter.name!r}")
        closed = close(structural_values, capture_external_values=True)
        return closed._invocation(
            instance_id,
            runtime_inputs,
        )

    return create_parametric_experiment_module_internal(
        id=selected_id,
        metadata=selected_metadata,
        definition=cast("Callable[P, ResultT]", fn),
        signature=signature,
        builder=build,
    )


def _apply_control_defaults(
    controls: ControlSet,
    contract: _ExperimentContract,
    input_defaults: dict[str, RuntimeInput],
    required_inputs: list[str],
) -> None:
    for control in controls.fields:
        if control.ownership != "editable" or control.scannable:
            continue
        if control.id not in contract.runtime_names:
            raise ValueError(f"control {control.id!r} needs a declared Input parameter")
        declared_type = dict(contract.runtime_arguments)[control.id].value_type
        if declared_type != control.value_type:
            raise TypeError(
                f"control {control.id!r} type must match its Input declaration"
            )
        if control.id in input_defaults:
            raise ValueError("declare controlled defaults only on Control")
        input_defaults[control.id] = control.default
        required_inputs.remove(control.id)


def _experiment_from_function[ResultT, **P](
    fn: Callable[Concatenate[ExperimentContext, P], ResultT],
    *,
    id: str | None,
    kind: str | None,
    metadata: Mapping[str, MetadataValue] | None,
    controls: ControlSet | None,
) -> Experiment[P, ResultT]:
    source = cast("DefinitionFunction", fn)
    contract = _experiment_contract(source)
    signature = contract.signature
    runtime_names = contract.runtime_names
    runtime_parameters = tuple(
        parameter
        for parameter in signature.parameters.values()
        if parameter.name in runtime_names
    )
    input_defaults: dict[str, RuntimeInput] = {}
    required_inputs: list[str] = []
    for parameter in runtime_parameters:
        default = cast("object", parameter.default)
        if default is inspect.Parameter.empty:
            required_inputs.append(parameter.name)
        else:
            input_defaults[parameter.name] = cast("RuntimeInput", default)
    if controls is not None:
        _apply_control_defaults(controls, contract, input_defaults, required_inputs)
    selected_metadata = dict(metadata or {})
    doc = inspect.getdoc(fn)
    if doc is not None:
        selected_metadata.setdefault("description", doc)
    selected_metadata = dict(freeze_json_mapping(selected_metadata))
    selected_id = id or source.__name__
    selected_kind = kind or source.__name__
    cached_build: (
        tuple[
            ExperimentDef,
            ResultT,
            Mapping[tuple[str, ...], ProductRef | RecordRef | ValueRef],
        ]
        | None
    ) = None

    def build(arguments: Mapping[str, object]) -> ExperimentInvocation[ResultT]:
        nonlocal cached_build
        values: dict[str, object] = dict(contract.runtime_values)
        runtime_inputs: dict[str, RuntimeInput] = {}
        for parameter in signature.parameters.values():
            if parameter.name in runtime_names:
                if parameter.name in arguments:
                    runtime_inputs[parameter.name] = cast(
                        "RuntimeInput", arguments[parameter.name]
                    )
                continue
            if parameter.name in arguments:
                values[parameter.name] = arguments[parameter.name]
                continue
            default = cast("object", parameter.default)
            if default is not inspect.Parameter.empty:
                values[parameter.name] = default
                continue
            raise TypeError(f"missing required structural argument: {parameter.name!r}")

        built = cached_build
        if built is None:
            context = ExperimentContext()
            if controls is not None:
                context.grid(*controls.default_axes())
            output = cast("ResultT", source(context, **values))
            recorded_tree = _record_experiment_output(context, output)
            recorded_result_refs = dict(_recorded_result_ref_items(recorded_tree))
            definition = context.close_definition_internal(
                id=selected_id,
                kind=selected_kind,
                metadata=selected_metadata,
                input_ports=tuple(
                    _input_port(name, value)
                    for name, value in contract.runtime_arguments
                ),
                input_defaults=input_defaults,
                required_inputs=tuple(required_inputs),
                controls=controls,
            )
            if set(recorded_result_refs) != {
                field.path for field in definition.result_fields
            }:
                raise AssertionError(
                    "recorded result handles do not match the durable result contract"
                )
            if not contract.structural_names:
                cached_build = (definition, output, recorded_result_refs)
        else:
            definition, output, recorded_result_refs = built
        captured_inputs = capture_experiment_inputs(runtime_inputs)
        validate_experiment_inputs(
            definitions=definition.inputs,
            inputs=captured_inputs,
        )
        return ExperimentInvocation(
            definition=definition,
            input_overrides=captured_inputs,
            point_plan_override=None,
            output=output,
            recorded_result_refs=recorded_result_refs,
        )

    authored = Experiment(
        _callable=cast("Callable[P, ResultT]", fn),
        _signature=signature.replace(
            return_annotation=ExperimentInvocation,
        ),
        _builder=build,
        id=selected_id,
        kind=selected_kind,
        metadata=selected_metadata,
    )
    if not contract.structural_names:
        build({})
    return authored


def _experiment_contract(fn: DefinitionFunction) -> _ExperimentContract:
    signature = _context_signature(fn, ExperimentContext)
    hints = cast("Mapping[str, object]", get_type_hints(fn, include_extras=True))
    runtime_arguments: list[tuple[str, ValueRef]] = []
    for parameter in signature.parameters.values():
        if parameter.kind in (
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
            inspect.Parameter.POSITIONAL_ONLY,
        ):
            raise TypeError("experiment functions require named parameters")
        annotation = hints.get(
            parameter.name,
            cast("object", parameter.annotation),
        )
        if not _is_runtime_input_annotation(annotation):
            continue
        runtime_arguments.append(
            (
                parameter.name,
                authoring_input(
                    parameter.name,
                    _annotation_value_type(annotation, parameter=parameter.name),
                ),
            )
        )
    return _ExperimentContract(signature, tuple(runtime_arguments))


def _module_contract(fn: DefinitionFunction) -> _ModuleContract:
    signature = _context_signature(fn, ModuleContext)
    hints = cast("Mapping[str, object]", get_type_hints(fn, include_extras=True))
    runtime_arguments: list[tuple[str, ValueRef]] = []
    for parameter in signature.parameters.values():
        if parameter.kind in (
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
            inspect.Parameter.POSITIONAL_ONLY,
        ):
            raise TypeError("module functions require named parameters")
        annotation = hints.get(
            parameter.name,
            cast("object", parameter.annotation),
        )
        if not _is_runtime_input_annotation(annotation):
            continue
        default = cast("object", parameter.default)
        if default is not inspect.Parameter.empty:
            raise TypeError("module inputs cannot declare Python defaults")
        runtime_arguments.append(
            (
                parameter.name,
                authoring_input(
                    parameter.name,
                    _annotation_value_type(annotation, parameter=parameter.name),
                ),
            )
        )
    return _ModuleContract(signature, tuple(runtime_arguments))


def _context_signature(
    fn: DefinitionFunction,
    context_type: type[object],
) -> inspect.Signature:
    signature = inspect.signature(fn)
    parameters = tuple(signature.parameters.values())
    if not parameters:
        raise TypeError(
            f"definition function requires a leading {context_type.__name__} parameter"
        )
    context = parameters[0]
    hints = cast("Mapping[str, object]", get_type_hints(fn, include_extras=True))
    annotation = hints.get(context.name, cast("object", context.annotation))
    if annotation is not context_type:
        raise TypeError(
            f"first definition parameter must be annotated as {context_type.__name__}"
        )
    if context.kind is not inspect.Parameter.POSITIONAL_OR_KEYWORD:
        raise TypeError("definition context must be the first positional parameter")
    return signature.replace(parameters=parameters[1:])


def _record_experiment_output(
    context: ExperimentContext,
    value: object,
    *,
    path: tuple[str, ...] = (),
    policy: Result | None = None,
    explicit_sources: frozenset[tuple[object, ...]] | None = None,
    recording_group_id: str | None = None,
) -> object:
    """Treat returned data as the experiment's durable output selection."""

    if explicit_sources is None:
        explicit_sources = frozenset(
            _record_selection_source_key(selection)
            for selection in context._record_selections
            if not isinstance(selection, EntityRecordSelection)
        )
    if value is None:
        return None
    selected_policy = policy or Result()
    result_path = path or ("result",)
    if isinstance(value, RecordRef):
        context._result_fields.append(
            ExperimentResultField(path=result_path, variable_id=value.id)
        )
        return value
    if isinstance(value, ValueRef):
        point_id = internal_value_ref_point_id(value)
        if point_id is not None:
            context._result_fields.append(
                ExperimentResultField(path=result_path, variable_id=point_id)
            )
            return value
        if _value_record_source_key(value) in explicit_sources:
            context._result_fields.append(
                ExperimentResultField(
                    path=result_path,
                    variable_id=_existing_record_id(context, value),
                )
            )
            return value
        record_id = _result_record_id(path, selected_policy)
        context._result_fields.append(
            ExperimentResultField(path=result_path, variable_id=record_id)
        )
        context.alias(
            value,
            record_id=record_id,
            role=selected_policy.role or "observable",
            metadata=selected_policy.metadata,
        )
        return value
    if isinstance(value, ProductRef):
        if _product_record_source_key(value) in explicit_sources:
            context._result_fields.append(
                ExperimentResultField(
                    path=result_path,
                    variable_id=_existing_record_id(context, value),
                )
            )
            return value
        record_id = _result_record_id(path, selected_policy)
        context._result_fields.append(
            ExperimentResultField(path=result_path, variable_id=record_id)
        )
        _record_product_output(
            context,
            value,
            record_id=record_id,
            role=selected_policy.role,
            metadata=selected_policy.metadata,
        )
        return value
    if isinstance(value, ProductBundle):
        _reject_structured_result_id(selected_policy)
        if not is_dataclass(value):
            raise TypeError("experiment output product bundles must be dataclasses")
        members = fields(value)
        if not members:
            raise TypeError("experiment output product bundles must not be empty")
        hints = cast(
            "Mapping[str, object]",
            get_type_hints(type(value), include_extras=True),
        )
        return replace(
            value,
            **{
                member.name: _record_experiment_output(
                    context,
                    cast("object", getattr(value, member.name)),
                    path=(*path, member.name),
                    policy=_merge_result_policy(
                        selected_policy,
                        _result_policy(hints.get(member.name)),
                    ),
                    explicit_sources=explicit_sources,
                )
                for member in members
            },
        )
    if isinstance(value, PerEntity):
        _reject_structured_result_id(selected_policy)
        items = tuple(value.items())
        if items and all(isinstance(item, ProductRef) for _entity, item in items):
            record_id = _result_record_id(path, selected_policy)
            context._result_fields.append(
                ExperimentResultField(path=result_path, variable_id=record_id)
            )
            return _record_entity_products_output(
                context,
                cast("PerEntity[ProductRef]", value),
                record_id=record_id,
                axis=None,
                role=selected_policy.role,
                recording_group_id=recording_group_id,
                metadata=selected_policy.metadata,
            )
        if items and all(isinstance(item, ProductBundle) for _entity, item in items):
            bundle_types = {type(item) for _entity, item in items}
            if len(bundle_types) != 1:
                raise TypeError(
                    "PerEntity product bundles must share one dataclass type"
                )
            first_bundle = cast("ProductBundle", items[0][1])
            if not is_dataclass(first_bundle):
                raise TypeError("experiment output product bundles must be dataclasses")
            hints = cast(
                "Mapping[str, object]",
                get_type_hints(type(first_bundle), include_extras=True),
            )
            return RecordedProducts(
                {
                    member.name: _record_experiment_output(
                        context,
                        PerEntity(
                            (
                                entity,
                                cast("object", getattr(item, member.name)),
                            )
                            for entity, item in items
                        ),
                        path=(*path, member.name),
                        policy=_merge_result_policy(
                            selected_policy,
                            _result_policy(hints.get(member.name)),
                        ),
                        explicit_sources=explicit_sources,
                        recording_group_id=_result_record_id(path, selected_policy),
                    )
                    for member in fields(first_bundle)
                }
            )
        return PerEntity(
            (
                entity,
                _record_experiment_output(
                    context,
                    item,
                    path=(*path, entity.kind or "entity", entity.id),
                    policy=selected_policy,
                    explicit_sources=explicit_sources,
                ),
            )
            for entity, item in value.items()
        )
    if isinstance(value, tuple):
        _reject_structured_result_id(selected_policy)
        return tuple(
            _record_experiment_output(
                context,
                item,
                path=(*path, str(index)),
                policy=selected_policy,
                explicit_sources=explicit_sources,
            )
            for index, item in enumerate(cast("tuple[object, ...]", value))
        )
    if is_dataclass(value) and not isinstance(value, type):
        _reject_structured_result_id(selected_policy)
        members = fields(value)
        if not members:
            raise TypeError("experiment output dataclasses must not be empty")
        hints = cast(
            "Mapping[str, object]",
            get_type_hints(type(value), include_extras=True),
        )
        return replace(
            value,
            **{
                member.name: _record_experiment_output(
                    context,
                    cast("object", getattr(value, member.name)),
                    path=(*path, member.name),
                    policy=_merge_result_policy(
                        selected_policy,
                        _result_policy(hints.get(member.name)),
                    ),
                    explicit_sources=explicit_sources,
                )
                for member in members
            },
        )
    raise TypeError(
        "experiment functions must return None or a tuple/dataclass/PerEntity "
        "tree of data references"
    )


def _recorded_result_ref_items(
    value: object,
    path: tuple[str, ...] = (),
) -> Iterator[tuple[tuple[str, ...], ProductRef | RecordRef | ValueRef]]:
    if isinstance(value, ProductRef | RecordRef | ValueRef):
        yield path or ("result",), value
        return
    if value is None:
        return
    if isinstance(value, RecordedProducts):
        for name, item in value.items():
            yield from _recorded_result_ref_items(item, (*path, name))
        return
    if isinstance(value, PerEntity):
        for entity, item in value.items():
            yield from _recorded_result_ref_items(
                item,
                (*path, entity.kind or "entity", entity.id),
            )
        return
    if isinstance(value, tuple):
        for index, item in enumerate(cast("tuple[object, ...]", value)):
            yield from _recorded_result_ref_items(item, (*path, str(index)))
        return
    if is_dataclass(value) and not isinstance(value, type):
        for member in fields(value):
            yield from _recorded_result_ref_items(
                cast("object", getattr(value, member.name)),
                (*path, member.name),
            )
        return
    raise TypeError(
        "recorded result handles must form a tuple/dataclass/PerEntity tree"
    )


def _record_entity_products_output(
    context: ExperimentContext,
    products: PerEntity[ProductRef],
    *,
    record_id: str,
    axis: str | EntityAxisDef | None,
    role: MeasurementVariableRole | None,
    acquisition: EntityAcquisitionSemantics | None = None,
    recording_group_id: str | None = None,
    metadata: Mapping[str, MetadataValue] | None,
) -> RecordRef[MeasurementArrayData | MeasurementSegmentedData]:
    entities = tuple(products)
    if not entities:
        raise ValueError("entity records require at least one product")
    selected_axis_id = (
        axis.id
        if isinstance(axis, EntityAxisDef)
        else axis or _entity_record_axis_id(entities)
    )
    selected_axis = _register_entity_axis(
        context,
        selected_axis_id,
        entities,
        provided=axis if isinstance(axis, EntityAxisDef) else None,
    )
    selected_role = role
    if selected_role is None:
        roles = {products[entity]._recording_role for entity in entities}
        if len(roles) != 1:
            raise TypeError(
                f"entity record {record_id!r} products must share one recording role"
            )
        selected_role = roles.pop()
    selections = tuple(
        (
            record_coordinate(
                products[entity],
                record_id=record_id,
                metadata=metadata,
            )
            if selected_role == "coordinate"
            else record_product(
                products[entity],
                record_id=record_id,
                metadata=metadata,
            )
        )
        for entity in entities
    )
    recording_group_ids = {selection.recording_group_id for selection in selections}
    selected_recording_group_id = (
        recording_group_id
        if recording_group_id is not None
        else next(iter(recording_group_ids))
        if len(recording_group_ids) == 1
        else None
    )
    context._record_selections.append(
        EntityRecordSelection(
            record_id=record_id,
            axis=selected_axis,
            members=tuple(
                EntityRecordMemberSelection(
                    entity=entity,
                    product_use=selection.product_use,
                    product_origin=selection.product_origin,
                )
                for entity, selection in zip(entities, selections, strict=True)
            ),
            role=cast("MeasurementVariableRole", selected_role),
            recording_group_id=selected_recording_group_id,
            acquisition=acquisition or EntityAcquisitionSemantics(),
            metadata=freeze_json_mapping(metadata or {}),
        )
    )
    first = products[entities[0]]
    local_dimensions = tuple(
        (
            next(iter(dimension_ids))
            if len(dimension_ids) == 1
            else f"{record_id}/{axis.id}"
        )
        for axis_index, axis in enumerate(first.value_spec.axes)
        if (
            dimension_ids := {
                _product_axis_dimension_id(
                    products[entity].product_id,
                    products[entity].value_spec.axes[axis_index],
                )
                for entity in entities
            }
        )
    )
    return RecordRef[MeasurementArrayData | MeasurementSegmentedData](
        id=record_id,
        dtype=first.value_spec.dtype,
        unit=first.value_spec.unit,
        dims=("point", selected_axis_id, *local_dimensions),
        role=cast("MeasurementVariableRole", selected_role),
        entity_axis_id=selected_axis_id,
        entity_axis_fingerprint=entity_axis_fingerprint(selected_axis.values),
    )


def _entity_record_axis_id(entities: Sequence[EntityRef]) -> str:
    kinds = {entity.kind for entity in entities}
    return (
        cast("str", kinds.pop()) if len(kinds) == 1 and None not in kinds else "entity"
    )


def _register_entity_axis(
    context: ExperimentContext,
    axis_id: str,
    entities: Sequence[EntityRef],
    *,
    provided: EntityAxisDef | None,
) -> EntityAxisDef:
    selected = provided or EntityAxisDef(
        id=axis_id,
        values=tuple(entities),
    )
    selected_identities = tuple(entity_identity(entity) for entity in selected.values)
    if selected_identities != tuple(entity_identity(entity) for entity in entities):
        raise ValueError("provided entity axis does not match the selected products")
    existing = context._entity_axes.get(axis_id)
    if existing is None:
        context._entity_axes[axis_id] = selected
        return selected
    if (
        tuple(entity_identity(entity) for entity in existing.values)
        != selected_identities
    ):
        raise ValueError(
            f"entity axis {axis_id!r} is already registered with different members"
        )
    return existing


def _existing_record_id(
    context: ExperimentContext,
    value: ProductRef | ValueRef,
) -> str:
    source_key = (
        _product_record_source_key(value)
        if isinstance(value, ProductRef)
        else _value_record_source_key(value)
    )
    matches = tuple(
        selection
        for selection in context._record_selections
        if not isinstance(selection, EntityRecordSelection)
        if _record_selection_source_key(selection) == source_key
    )
    if len(matches) != 1:
        raise ValueError(
            "returning a data source with multiple explicit records is ambiguous; "
            "return the selected RecordRef instead"
        )
    selection = matches[0]
    if isinstance(selection, ValueRecordSelection):
        source_value_id = internal_value_ref_record_source_id(selection.value)
        return (
            selection.record_id
            or parse_product_id(source_value_id)
            .prefixed(*selection.namespace)
            .qualified_name
        )
    if isinstance(selection, EntityRecordSelection):
        raise AssertionError("entity records cannot match one scalar product source")
    return selection.record_id or selection.product_id.qualified_name


def _record_product_output[T: NativeMeasurementValue](
    context: ExperimentContext,
    product: ProductRef[T],
    *,
    record_id: str,
    role: MeasurementVariableRole | None,
    metadata: Mapping[str, MetadataValue] | None,
) -> None:
    existing = next(
        (
            selection
            for selection in context._record_selections
            if isinstance(selection, RecordSelection)
            and selection.product_id == product.product_id
            and selection.product_origin == product.origin
        ),
        None,
    )
    if existing is not None:
        context._record_selections.append(
            record_alias(
                existing,
                record_id=record_id,
                role=role,
                metadata=metadata,
            )
        )
        return
    context._record_product(
        product,
        record_id=record_id,
        namespace=None,
        role=role,
        metadata=metadata,
    )


def _result_policy(annotation: object) -> Result | None:
    if get_origin(annotation) is not Annotated:
        return None
    _value_type, *metadata = cast("tuple[object, ...]", get_args(annotation))
    policies = tuple(item for item in metadata if isinstance(item, Result))
    if len(policies) > 1:
        raise TypeError("result fields may declare at most one Result policy")
    return policies[0] if policies else None


def _merge_result_policy(parent: Result, child: Result | None) -> Result:
    if child is None:
        return parent
    namespace_segments = (*_result_namespace(parent), *_result_namespace(child))
    return Result(
        id=child.id,
        namespace="/".join(namespace_segments) if namespace_segments else None,
        role=child.role if child.role is not None else parent.role,
        metadata={**(parent.metadata or {}), **(child.metadata or {})},
    )


def _result_namespace(policy: Result) -> tuple[str, ...]:
    return (
        () if policy.namespace is None else _record_namespace_segments(policy.namespace)
    )


def _result_record_id(path: tuple[str, ...], policy: Result) -> str:
    if policy.id is not None:
        return policy.id
    return "/".join((*_result_namespace(policy), *(path or ("result",))))


def _reject_structured_result_id(policy: Result) -> None:
    if policy.id is not None:
        raise TypeError("Result(id=...) can only annotate one data-reference leaf")


def _record_selection_source_key(
    selection: ProgramRecordSelection,
) -> tuple[object, ...]:
    if isinstance(selection, ValueRecordSelection):
        return _value_record_source_key(selection.value)
    if isinstance(selection, EntityRecordSelection):
        return "entity-record", selection.record_id
    return (
        "product",
        selection.product_id,
        selection.product_origin,
    )


def _value_record_source_key(value: ValueRef) -> tuple[object, ...]:
    return "value", value.id


def _product_record_source_key(product: ProductRef) -> tuple[object, ...]:
    return "product", product.product_id, product.origin


def _is_runtime_input_annotation(annotation: object) -> bool:
    while isinstance(annotation, TypeAliasType):
        annotation = cast("object", annotation.__value__)
    if get_origin(annotation) is Annotated:
        annotation = cast("tuple[object, ...]", get_args(annotation))[0]
    while isinstance(annotation, TypeAliasType):
        annotation = cast("object", annotation.__value__)
    return get_origin(annotation) is Input


def _annotation_value_type(annotation: object, *, parameter: str) -> ValueType:
    while isinstance(annotation, TypeAliasType):
        annotation = cast("object", annotation.__value__)
    if get_origin(annotation) is Annotated:
        python_type, *metadata = cast("tuple[object, ...]", get_args(annotation))
        declared = tuple(item for item in metadata if _is_value_type(item))
        if len(declared) != 1:
            raise TypeError(
                f"parameter {parameter!r} requires one ValueType in Annotated metadata"
            )
        selected = _as_value_type(declared[0])
        if not _python_annotation_matches(python_type, selected):
            raise TypeError(
                f"parameter {parameter!r} Python annotation is incompatible "
                f"with {selected!r}"
            )
        return selected
    if get_origin(annotation) is Input:
        [annotation] = cast("tuple[object, ...]", get_args(annotation))
    if _is_value_type(annotation):
        return _as_value_type(annotation)
    inferred: dict[object, ValueType] = {
        bool: Scalar(Bool()),
        int: Scalar(Int()),
        float: Scalar(Float()),
        complex: Scalar(Complex()),
        str: Scalar(String()),
        EntityRef: Scalar(Entity()),
        QuantityValue: Scalar(Quantity()),
    }
    selected = inferred.get(annotation)
    if selected is None:
        raise TypeError(
            f"parameter {parameter!r} needs a scalar Python type or Annotated ValueType"
        )
    return selected


def _is_value_type(value: object) -> bool:
    if isinstance(value, Scalar | Table):
        return True
    return isinstance(
        value,
        Bool | Entity | Float | Int | Payload | Quantity | String,
    )


def _as_value_type(value: object) -> ValueType:
    if isinstance(value, Scalar | Table):
        return value
    if isinstance(
        value,
        Bool | Entity | Float | Int | Payload | Quantity | String,
    ):
        return Scalar(value)
    raise AssertionError("value-type metadata was checked before normalization")


def _python_annotation_matches(annotation: object, value_type: ValueType) -> bool:
    if get_origin(annotation) is Input:
        [annotation] = cast("tuple[object, ...]", get_args(annotation))
    origin = get_origin(annotation)
    if origin is UnionType:
        return all(
            _python_annotation_matches(option, value_type)
            for option in cast("tuple[object, ...]", get_args(annotation))
        )
    if annotation is object:
        return True
    if isinstance(value_type, Scalar):
        atom = value_type.atom
        if isinstance(atom, Payload) and origin in (dict, Mapping):
            return True
        expected: dict[type[object], tuple[object, ...]] = {
            Bool: (bool,),
            Entity: (str, EntityRef),
            Float: (float,),
            Complex: (complex,),
            Int: (int,),
            Payload: (dict, Mapping),
            Quantity: (QuantityValue,),
            String: (str,),
        }
        return annotation in expected[type(atom)]
    if origin not in (tuple, list, Sequence):
        return False
    row_types = cast("tuple[object, ...]", get_args(annotation))
    if not row_types:
        return False
    row_type = row_types[0]
    row_origin = get_origin(row_type)
    return (
        row_origin is dict
        or row_origin is Mapping
        or row_type is dict
        or row_type is Mapping
    )


def _input_port(name: str, value: ValueRef) -> ModuleInputPort:
    return ModuleInputPort(id=name, value_type=value.value_type)


def _definition_id(fn: DefinitionFunction) -> str:
    return f"{fn.__module__}.{fn.__qualname__}"


__all__ = [
    "Experiment",
    "ExperimentContext",
    "Input",
    "Symbolic",
    "experiment",
    "input_ref",
    "module",
]
