"""Typed value edges used while composing symbolic programs."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Self, cast, overload, override
from uuid import uuid4

import numpy as np

from scopecat.kernel.entity import EntityRef
from scopecat.kernel.graph_identity import ValueId
from scopecat.kernel.payloads import PayloadValue
from scopecat.kernel.quantity import Quantity
from scopecat.kernel.symbols import SymbolId
from scopecat.kernel.value_data import Row
from scopecat.kernel.value_type_compatibility import is_assignable
from scopecat.kernel.value_types import Array, DataType, Scalar, Table, ValueType
from scopecat.kernel.value_validation import (
    ValuePath,
    coerce_literal,
)
from scopecat.program.expression_analysis import (
    expression_input_refs,
    expression_module_exports,
    expression_requires_execution,
    scalar_nodes,
)
from scopecat.program.expression_operators import ScalarOperator
from scopecat.program.expressions import (
    ArrayExpr,
    BinaryScalarExpr,
    ComputeResultArrayExpr,
    ComputeResultScalarExpr,
    InputArrayExpr,
    InputScalarExpr,
    LiteralArrayExpr,
    LiteralScalarExpr,
    ModuleExportArrayExpr,
    ModuleExportScalarExpr,
    ParameterLookupScalarExpr,
    ParameterLookupUse,
    ParameterScalarExpr,
    PointColumnScalarExpr,
    ScalarExpr,
    as_scalar_expr,
    input_ref,
    lit,
    parameter_lookup,
    point_col,
)
from scopecat.program.identities import InvocationKey
from scopecat.program.input_capture import capture_runtime_input
from scopecat.program.parameters import (
    ParameterContract,
    ParameterValueContract,
    merge_parameter_contracts,
)
from scopecat.program.table_values import (
    InputTableSource,
    LiteralTableSource,
    ParameterTableSource,
    TableSource,
    TopologyConnectionSetSource,
    TopologyEntitySetSource,
    literal_table_source,
)
from scopecat.program.value_graph import OperationId, operation_result_id

type FrozenScalarLiteral = (
    Quantity | EntityRef | PayloadValue | str | int | float | bool | None
)
type ScalarOperand = ValueRef | FrozenScalarLiteral


@dataclass(frozen=True, slots=True)
class _ModuleExportTableSource:
    """One unresolved table projection from a particular module invocation."""

    invocation_key: InvocationKey
    export_id: str
    value_type: Table


type _ValueSource = ArrayExpr | ScalarExpr | _ModuleExportTableSource | TableSource
type ComputeResultExpr = ComputeResultScalarExpr | ComputeResultArrayExpr
type ModuleExportExpr = ModuleExportScalarExpr | ModuleExportArrayExpr


def _fresh_value_id() -> ValueId:
    return ValueId(
        SymbolId(
            scope=("values",),
            local_id=f"v_{uuid4().hex}",
        )
    )


@dataclass(frozen=True, slots=True)
class PointValueDependency:
    """One point value consumed by a canonical scalar expression."""

    id: str
    value_type: Scalar


@dataclass(frozen=True, slots=True, eq=False, repr=False)
class ValueRef[T = object]:
    """Opaque public handle for one typed canonical program value.

    Values are created by DSL factories such as :func:`scopecat.input` and
    :func:`scopecat.parameter`. Their source expression and provenance remain
    compiler-facing semantic state.
    """

    source: _ValueSource
    value_type: ValueType
    id: ValueId = field(default_factory=_fresh_value_id)

    def __post_init__(self) -> None:
        source = self.source
        if isinstance(source, ScalarExpr):
            if not isinstance(self.value_type, Scalar) or not is_assignable(
                source.value_type,
                self.value_type,
            ):
                msg = "scalar value source must be assignable to its value type"
                raise TypeError(msg)
            return
        if isinstance(source, ArrayExpr):
            if not isinstance(self.value_type, Array) or not is_assignable(
                source.value_type,
                self.value_type,
            ):
                msg = "array value source must be assignable to its value type"
                raise TypeError(msg)
            return
        if not isinstance(self.value_type, Table):
            msg = "table value source requires a table value type"
            raise TypeError(msg)
        if isinstance(source, _ModuleExportTableSource) and not is_assignable(
            source.value_type, self.value_type
        ):
            msg = "module export table source must be assignable to its value type"
            raise TypeError(msg)

    def __copy__(self) -> Self:
        return self

    def __deepcopy__(self, memo: dict[int, object]) -> Self:
        del memo
        return self

    def __bool__(self) -> bool:
        raise TypeError(
            "symbolic ValueRef has no Python truth value; use an explicit "
            "authoring operation"
        )

    @override
    def __repr__(self) -> str:
        return f"{type(self).__qualname__}()"

    @override
    def __eq__(self, other: object) -> bool:
        return isinstance(other, ValueRef) and self.id == other.id

    @override
    def __hash__(self) -> int:
        return hash(self.id)

    @overload
    def __add__(
        self: ValueRef[Quantity],
        other: ValueRef[Quantity] | Quantity,
    ) -> ValueRef[Quantity]: ...

    @overload
    def __add__(self, other: object) -> ValueRef[object]: ...

    def __add__(self, other: object) -> ValueRef:
        return _binary_value(self, other, "+")

    def __radd__(self, other: object) -> ValueRef[object]:
        return _binary_value(other, self, "+")

    @overload
    def __sub__(
        self: ValueRef[Quantity],
        other: ValueRef[Quantity] | Quantity,
    ) -> ValueRef[Quantity]: ...

    @overload
    def __sub__(self, other: object) -> ValueRef[object]: ...

    def __sub__(self, other: object) -> ValueRef:
        return _binary_value(self, other, "-")

    @overload
    def __rsub__(
        self: ValueRef[Quantity],
        other: Quantity,
    ) -> ValueRef[Quantity]: ...

    @overload
    def __rsub__(self, other: object) -> ValueRef[object]: ...

    def __rsub__(self, other: object) -> ValueRef:
        return _binary_value(other, self, "-")

    @overload
    def __mul__(
        self: ValueRef[Quantity],
        other: ValueRef[Quantity] | Quantity,
    ) -> ValueRef[float]: ...

    @overload
    def __mul__(
        self: ValueRef[Quantity],
        other: ValueRef[int] | ValueRef[float] | float,
    ) -> ValueRef[Quantity]: ...

    @overload
    def __mul__(
        self: ValueRef[int] | ValueRef[float],
        other: ValueRef[Quantity] | Quantity,
    ) -> ValueRef[Quantity]: ...

    @overload
    def __mul__(self, other: object) -> ValueRef[object]: ...

    def __mul__(self, other: object) -> ValueRef:
        return _binary_value(self, other, "*")

    @overload
    def __rmul__(
        self: ValueRef[Quantity],
        other: Quantity,
    ) -> ValueRef[float]: ...

    @overload
    def __rmul__(
        self: ValueRef[Quantity],
        other: ValueRef[int] | ValueRef[float] | float,
    ) -> ValueRef[Quantity]: ...

    @overload
    def __rmul__(
        self: ValueRef[int] | ValueRef[float],
        other: ValueRef[Quantity] | Quantity,
    ) -> ValueRef[Quantity]: ...

    @overload
    def __rmul__(self, other: object) -> ValueRef[object]: ...

    def __rmul__(self, other: object) -> ValueRef:
        return _binary_value(other, self, "*")

    @overload
    def __truediv__(
        self: ValueRef[Quantity],
        other: ValueRef[Quantity] | Quantity,
    ) -> ValueRef[float]: ...

    @overload
    def __truediv__(
        self: ValueRef[Quantity],
        other: ValueRef[int] | ValueRef[float] | float,
    ) -> ValueRef[Quantity]: ...

    @overload
    def __truediv__(self, other: object) -> ValueRef[object]: ...

    def __truediv__(self, other: object) -> ValueRef:
        return _binary_value(self, other, "/")

    @overload
    def __rtruediv__(
        self: ValueRef[Quantity],
        other: Quantity,
    ) -> ValueRef[float]: ...

    @overload
    def __rtruediv__(self, other: object) -> ValueRef[object]: ...

    def __rtruediv__(self, other: object) -> ValueRef:
        return _binary_value(other, self, "/")


@dataclass(frozen=True, slots=True, eq=False, repr=False)
class CoordinateRef[T = object](ValueRef[T]):
    """A direct experiment-point coordinate accepted by dataset lookups."""

    source: PointColumnScalarExpr
    value_type: Scalar


def internal_input_value_ref(input_id: str, value_type: ValueType) -> ValueRef:
    if isinstance(value_type, Scalar):
        source: _ValueSource = input_ref(input_id, value_type)
    elif isinstance(value_type, Array):
        source = InputArrayExpr(name=input_id, value_type=value_type)
    else:
        source = InputTableSource(input_id)
    return ValueRef(
        source=source,
        value_type=value_type,
    )


def internal_operation_result_value_ref(
    operation_id: SymbolId | str,
    value_type: DataType,
    *,
    origin: tuple[object, ...] = (),
    point_dependencies: tuple[PointValueDependency, ...] = (),
) -> ValueRef:
    selected_operation_id = (
        operation_id
        if isinstance(operation_id, SymbolId)
        else SymbolId(local_id=operation_id)
    )
    result_id = operation_result_id(OperationId(selected_operation_id))
    dependencies = tuple(
        (dependency.id, dependency.value_type)
        for dependency in _merge_point_dependencies(point_dependencies)
    )
    source: _ValueSource
    if isinstance(value_type, Scalar):
        source = ComputeResultScalarExpr(
            value_id=result_id,
            value_type=value_type,
            origin=origin,
            point_dependencies=dependencies,
        )
    else:
        source = ComputeResultArrayExpr(
            value_id=result_id,
            value_type=value_type,
            origin=origin,
            point_dependencies=dependencies,
        )
    return ValueRef(
        source=source,
        value_type=value_type,
    )


def internal_point_value_ref(point_id: str, value_type: Scalar) -> CoordinateRef:
    """Create a typed value supplied by the current experiment point."""

    return CoordinateRef(
        source=point_col(point_id, value_type),
        value_type=value_type,
    )


def internal_coordinate_ref_id(value: CoordinateRef) -> str:
    """Return the canonical point id carried by a coordinate handle."""

    return value.source.name


def internal_parameter_lookup_value_ref(
    use: ParameterLookupUse,
    *,
    key: Mapping[str, ScalarOperand],
) -> ValueRef:
    """Create a direct parameter-cell reference retained by axis overlays."""

    captured_key = tuple(key.items())
    bound_values = tuple(
        value for _name, value in captured_key if isinstance(value, ValueRef)
    )
    _require_relation_bindings(bound_values)
    expression_key = {
        name: _scalar_expression(value) if isinstance(value, ValueRef) else value
        for name, value in captured_key
    }
    return ValueRef(
        source=parameter_lookup(use, key=expression_key),
        value_type=use.result_type,
    )


def internal_value_ref_parameter_lookup(
    value: ValueRef,
) -> tuple[ParameterLookupUse, tuple[tuple[str, ScalarOperand], ...]] | None:
    """Return the cell locator only for a direct parameter lookup."""

    source = value.source
    if not isinstance(source, ParameterLookupScalarExpr):
        return None
    literal_columns = source.use.literal_key_columns
    return source.use, tuple(
        (
            name,
            cast(
                "FrozenScalarLiteral",
                cast("LiteralScalarExpr", expression).value,
            )
            if name in literal_columns
            else ValueRef(
                source=expression,
                value_type=expression.value_type,
            ),
        )
        for name, expression in source.key.items()
    )


def internal_module_export_value_ref(
    invocation_key: InvocationKey,
    export_id: str,
    value_type: ValueType,
    *,
    source_value_id: str | None = None,
) -> ValueRef:
    """Create an unresolved use of one invocation's exported value.

    Module exports are interface projections in the canonical expression tree.
    Elaboration resolves them through the referenced module instance before the
    program crosses the compiler boundary.
    """

    if isinstance(value_type, Scalar):
        source: _ValueSource = ModuleExportScalarExpr(
            invocation_key=invocation_key,
            export_id=export_id,
            source_value_id=source_value_id,
            value_type=value_type,
        )
    elif isinstance(value_type, Array):
        source = ModuleExportArrayExpr(
            invocation_key=invocation_key,
            export_id=export_id,
            source_value_id=source_value_id,
            value_type=value_type,
        )
    else:
        source = _ModuleExportTableSource(
            invocation_key=invocation_key,
            export_id=export_id,
            value_type=value_type,
        )
    return ValueRef(source=source, value_type=value_type)


def internal_value_ref_input_id(value: ValueRef) -> str | None:
    source = value.source
    if isinstance(source, InputScalarExpr | InputArrayExpr):
        return source.name
    return source.input_id if isinstance(source, InputTableSource) else None


def internal_value_ref_point_id(value: ValueRef) -> str | None:
    """Return the point coordinate id carried by a point value."""

    source = value.source
    return source.name if isinstance(source, PointColumnScalarExpr) else None


def internal_value_ref_operation_id(value: ValueRef) -> SymbolId | None:
    source = value.source
    if not isinstance(source, ComputeResultScalarExpr | ComputeResultArrayExpr):
        return None
    result_id = source.value_id
    scope = result_id.scope
    if len(scope) < 2 or scope[-1] != "outputs":
        raise AssertionError("compute result value id has no canonical producer scope")
    return SymbolId(scope=scope[:-2], local_id=scope[-2])


def internal_value_ref_operation_origin(value: ValueRef) -> tuple[object, ...]:
    source = value.source
    return (
        source.origin
        if isinstance(source, ComputeResultScalarExpr | ComputeResultArrayExpr)
        else ()
    )


def internal_value_ref_source_id(value: ValueRef) -> str | None:
    """Return the stable source identity of a directly named scalar value."""

    operation_id = internal_value_ref_operation_id(value)
    if operation_id is not None:
        return operation_id.qualified_name
    source = value.source
    if isinstance(
        source,
        PointColumnScalarExpr | InputScalarExpr | ParameterScalarExpr,
    ):
        return source.name
    if isinstance(source, ParameterLookupScalarExpr):
        return SymbolId(
            scope=(source.use.table_id,),
            local_id=source.use.column_id,
        ).qualified_name
    if isinstance(source, ModuleExportScalarExpr | ModuleExportArrayExpr):
        return source.source_value_id
    return None


def internal_value_ref_record_source_id(value: ValueRef) -> str:
    """Return the durable provenance identity for any recordable value.

    Directly named values retain their public source identity. Pure expressions
    use their graph identity, keeping the source distinct from whichever
    human-facing record name selected them.
    """

    return internal_value_ref_source_id(value) or value.id.qualified_name


def internal_value_ref_module_export(
    value: ValueRef,
) -> tuple[InvocationKey, str] | None:
    """Return the invocation and export identity for a direct export use."""

    source = value.source
    if isinstance(
        source,
        ModuleExportScalarExpr | ModuleExportArrayExpr | _ModuleExportTableSource,
    ):
        return source.invocation_key, source.export_id
    return None


def internal_require_resolved_value_ref(
    value: ValueRef,
    *,
    context: str = "value",
) -> None:
    """Reject a value whose canonical source still contains a module export."""

    unresolved = internal_value_ref_first_module_export(value)
    if unresolved is None:
        return
    _invocation_key, export_id = unresolved
    msg = (
        f"{context} contains unresolved module export {export_id!r}; "
        "module elaboration must resolve exports before lowering"
    )
    raise ValueError(msg)


def internal_value_ref_first_module_export(
    value: ValueRef,
) -> tuple[InvocationKey, str] | None:
    direct = internal_value_ref_module_export(value)
    if direct is not None:
        return direct
    source = value.source
    if not isinstance(source, ScalarExpr):
        return None
    exports = expression_module_exports(source)
    return exports[0] if exports else None


def internal_value_ref_parameter_contracts(
    value: ValueRef,
) -> tuple[ParameterContract, ...]:
    source = value.source
    if isinstance(source, ParameterTableSource):
        return (ParameterValueContract(source.parameter_id, value.value_type),)
    if not isinstance(source, ScalarExpr):
        return ()
    contracts: list[tuple[ParameterContract, ...]] = []
    for node in scalar_nodes(source):
        if isinstance(node, ParameterScalarExpr):
            contracts.append((ParameterValueContract(node.name, node.value_type),))
        elif isinstance(node, ParameterLookupScalarExpr):
            contracts.append((node.use,))
    return merge_parameter_contracts(*contracts)


def internal_value_ref_point_dependencies(
    value: ValueRef,
) -> tuple[PointValueDependency, ...]:
    """Return point ids and types consumed by the canonical expression."""

    source = value.source
    if isinstance(source, ComputeResultArrayExpr):
        return tuple(
            PointValueDependency(point_id, value_type)
            for point_id, value_type in source.point_dependencies
        )
    if not isinstance(source, ScalarExpr):
        return ()
    groups: list[tuple[PointValueDependency, ...]] = []
    for node in scalar_nodes(source):
        if isinstance(node, PointColumnScalarExpr):
            groups.append((PointValueDependency(node.name, node.value_type),))
        elif isinstance(node, ComputeResultScalarExpr):
            groups.append(
                tuple(
                    PointValueDependency(point_id, value_type)
                    for point_id, value_type in node.point_dependencies
                )
            )
    return _merge_point_dependencies(*groups)


def internal_value_ref_scalar_input_ids(value: ValueRef) -> frozenset[str]:
    """Return scalar imports remaining after authored input bindings."""

    source = value.source
    if isinstance(source, InputArrayExpr):
        return frozenset((source.name,))
    if not isinstance(source, ScalarExpr):
        return frozenset()
    return frozenset(expression_input_refs(source))


def internal_value_ref_requires_execution(value: ValueRef) -> bool:
    """Return whether a value contains an opaque compute-result expression."""

    unresolved = internal_value_ref_first_module_export(value)
    if unresolved is not None:
        _invocation_key, export_id = unresolved
        msg = f"cannot determine dependencies of unresolved module export {export_id!r}"
        raise ValueError(msg)
    source = value.source
    return isinstance(source, ComputeResultArrayExpr) or (
        isinstance(source, ScalarExpr) and expression_requires_execution(source)
    )


def internal_lower_value_ref(
    value: ValueRef,
) -> ArrayExpr | ScalarExpr | TableSource:
    """Expose a value's canonical source at the private compiler boundary."""

    source = value.source
    unresolved = internal_value_ref_first_module_export(value)
    if unresolved is not None:
        _invocation_key, export_id = unresolved
        msg = (
            f"cannot lower unresolved module export {export_id!r}; "
            "module elaboration must resolve exports first"
        )
        raise ValueError(msg)
    if isinstance(source, ScalarExpr | ArrayExpr):
        return source
    if isinstance(
        source,
        LiteralTableSource
        | ParameterTableSource
        | InputTableSource
        | TopologyEntitySetSource
        | TopologyConnectionSetSource,
    ):
        return source
    raise AssertionError("resolved value source has an unsupported shape")


def internal_lower_scalar_value_ref(value: ValueRef) -> ScalarExpr:
    """Expose a canonical pure scalar expression at the compiler boundary."""

    if not isinstance(value.value_type, Scalar):
        msg = "scalar expression requires a scalar value"
        raise TypeError(msg)
    lowered = internal_lower_value_ref(value)
    if isinstance(lowered, ComputeResultScalarExpr):
        msg = "compute outputs must be connected as standalone values"
        raise TypeError(msg)
    if not isinstance(lowered, ScalarExpr):
        msg = "scalar expression requires a scalar value"
        raise TypeError(msg)
    return lowered


def internal_value_ref_from_expression(
    expression: ScalarExpr,
    value_type: Scalar,
) -> ValueRef:
    """Construct a typed expression edge inside the authoring implementation."""

    return ValueRef(
        source=expression,
        value_type=value_type,
    )


def internal_table_value_ref(
    source: TableSource,
    value_type: Table,
) -> ValueRef:
    """Construct a direct whole-table edge for a domain compiler input."""

    return ValueRef(
        source=source,
        value_type=value_type,
    )


def internal_literal_value_ref(
    value: object,
    value_type: ValueType,
    *,
    path: ValuePath,
) -> ValueRef:
    """Capture one closed literal as a typed edge without exposing raw IR."""

    from scopecat.program.expression_binding import input_cell

    coerced = coerce_literal(value_type, value, path=path)
    if isinstance(value_type, Table):
        return internal_table_value_ref(
            literal_table_source(cast("tuple[Row, ...]", coerced)),
            value_type,
        )
    if isinstance(value_type, Array):
        return ValueRef(
            source=LiteralArrayExpr(cast("np.ndarray", coerced), value_type),
            value_type=value_type,
        )
    return internal_value_ref_from_expression(
        lit(input_cell(coerced), value_type),
        value_type,
    )


def _binary_value(left: object, right: object, operator: str) -> ValueRef:
    selected_operator = cast("ScalarOperator", operator)
    left_operand = _capture_scalar_operand(left)
    right_operand = _capture_scalar_operand(right)
    operands = tuple(
        operand
        for operand in (left_operand, right_operand)
        if isinstance(operand, ValueRef)
    )
    _require_relation_bindings(operands)

    left_expression = _scalar_operand_expression(left_operand)
    right_expression = _scalar_operand_expression(right_operand)
    expression = BinaryScalarExpr(
        op=selected_operator,
        left=left_expression,
        right=right_expression,
    )
    return ValueRef(
        source=expression,
        value_type=expression.value_type,
    )


def _scalar_operand_expression(operand: ScalarOperand) -> ScalarExpr:
    return (
        _scalar_expression(operand)
        if isinstance(operand, ValueRef)
        else as_scalar_expr(operand)
    )


def _capture_scalar_operand(value: object) -> ScalarOperand:
    if isinstance(value, ValueRef):
        return value
    if isinstance(value, PayloadValue):
        return value

    try:
        captured = capture_runtime_input(value)
    except TypeError as error:
        msg = "scalar operations require typed values or closed scalar literals"
        raise TypeError(msg) from error
    return cast("FrozenScalarLiteral", captured)


def _require_relation_bindings(values: tuple[ValueRef, ...]) -> None:
    if any(
        internal_value_ref_first_module_export(value) is None
        and internal_value_ref_requires_execution(value)
        for value in values
    ):
        msg = (
            "compute outputs cannot be bound inside scalar expressions; "
            "express this calculation with ModuleContext.compute"
        )
        raise TypeError(msg)


def _merge_point_dependencies(
    *groups: tuple[PointValueDependency, ...],
) -> tuple[PointValueDependency, ...]:
    selected: dict[str, PointValueDependency] = {}
    for dependency in (item for group in groups for item in group):
        existing = selected.get(dependency.id)
        if existing is not None and existing.value_type != dependency.value_type:
            msg = (
                f"point value {dependency.id!r} is used with conflicting declared types"
            )
            raise TypeError(msg)
        selected.setdefault(dependency.id, dependency)
    return tuple(selected.values())


def _scalar_expression(value: ValueRef) -> ScalarExpr:
    source = value.source
    if not isinstance(source, ScalarExpr):
        msg = "scalar expression requires a scalar value"
        raise TypeError(msg)
    if isinstance(source, ComputeResultScalarExpr):
        msg = (
            "compute outputs cannot be bound inside scalar expressions; "
            "express this calculation with ModuleContext.compute"
        )
        raise TypeError(msg)
    return source
