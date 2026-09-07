"""First-class typed values for symbolic programs."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from inspect import Parameter, signature
from typing import cast, overload

from scopecat.kernel.entity import EntityRef
from scopecat.kernel.json_types import JsonValue
from scopecat.kernel.payloads import PayloadValue
from scopecat.kernel.quantity import Quantity
from scopecat.kernel.value_type_compatibility import literal_scalar_type
from scopecat.kernel.value_types import (
    Array,
    Bool,
    Complex,
    Entity,
    Float,
    Int,
    Payload,
    Scalar,
    String,
    Table,
    ValueType,
)
from scopecat.kernel.value_types import Quantity as QuantityType
from scopecat.program.expressions import (
    ParameterLookupUse,
    param,
)
from scopecat.program.identities import ComputeDeclarationKey
from scopecat.program.input_capture import capture_runtime_input
from scopecat.program.table_values import ParameterTableSource
from scopecat.program.value_refs import (
    CoordinateRef,
    ValueRef,
    internal_input_value_ref,
    internal_operation_result_value_ref,
    internal_parameter_lookup_value_ref,
    internal_point_value_ref,
    internal_table_value_ref,
    internal_value_ref_from_expression,
    internal_value_ref_point_dependencies,
)

type ComputeFunction = Callable[..., object]
type ScalarValueType = (
    Bool | Complex | Entity | Float | Int | Payload | QuantityType | String
)
type ScalarInput = (
    Quantity | EntityRef | PayloadValue | str | int | float | complex | bool | None
)
type ComputeInput = ValueRef | ScalarInput
type RuntimeInput = (
    Quantity
    | EntityRef
    | str
    | int
    | float
    | complex
    | bool
    | list[RuntimeInput]
    | tuple[RuntimeInput, ...]
    | Mapping[str, RuntimeInput]
    | None
)
type MetadataValue = JsonValue
type ModuleInput = (
    ValueRef
    | ScalarInput
    | list[ModuleInput]
    | tuple[ModuleInput, ...]
    | Mapping[str, ModuleInput]
)
type ParameterKeyInput = (
    ValueRef | Quantity | EntityRef | str | int | float | bool | None
)


def constant(value: object, *, schema: str) -> PayloadValue:
    """Capture one immutable typed payload as an explicit compute input."""

    return PayloadValue(schema_id=schema, payload=value)


@dataclass(frozen=True, slots=True, repr=False)
class Compute:
    """A typed compute declaration and its composable output value."""

    id: str
    fn: ComputeFunction
    inputs: tuple[tuple[str, ComputeInput], ...]
    output_type: Scalar | Array
    declaration_key: ComputeDeclarationKey

    def __post_init__(self) -> None:
        if not self.id:
            msg = "compute id must be non-empty"
            raise ValueError(msg)
        input_names = tuple(name for name, _value in self.inputs)
        if any(not name for name in input_names):
            msg = "compute input names must be non-empty"
            raise ValueError(msg)
        if len(set(input_names)) != len(input_names):
            msg = f"compute {self.id!r} has duplicate input names"
            raise ValueError(msg)
        invalid = [name for name, value in self.inputs if not _is_compute_input(value)]
        if invalid:
            msg = (
                f"compute {self.id!r} inputs must be typed values or "
                f"scalar literals; invalid inputs: {', '.join(invalid)}"
            )
            raise TypeError(msg)
        validate_compute_function_internal(self.id, self.fn, input_names)

    @property
    def output(self) -> ValueRef:
        return internal_operation_result_value_ref(
            self.id,
            self.output_type,
            origin=(self.declaration_key,),
            point_dependencies=tuple(
                dependency
                for _name, value in self.inputs
                if isinstance(value, ValueRef)
                for dependency in internal_value_ref_point_dependencies(value)
            ),
        )

    @property
    def input_types(self) -> tuple[tuple[str, Scalar | Array], ...]:
        """Return the declared consumer type of every compute input edge."""

        return tuple(
            (name, _compute_input_value_type(value)) for name, value in self.inputs
        )


def input(
    id: str,
    value_type: ValueType,
) -> ValueRef:
    """Declare one typed module input value.

    Prefer a typed ``@module`` parameter for user-authored module inputs. This
    constructor exists for programmatic domain adapters and compiler-focused
    tests.
    """

    return internal_input_value_ref(id, value_type)


@overload
def coordinate(id: str, value_type: Bool) -> CoordinateRef[bool]: ...


@overload
def coordinate(id: str, value_type: Entity) -> CoordinateRef[EntityRef | str]: ...


@overload
def coordinate(id: str, value_type: Float) -> CoordinateRef[float]: ...


@overload
def coordinate(id: str, value_type: Int) -> CoordinateRef[int]: ...


@overload
def coordinate(id: str, value_type: Payload) -> CoordinateRef[PayloadValue]: ...


@overload
def coordinate(id: str, value_type: QuantityType) -> CoordinateRef[Quantity]: ...


@overload
def coordinate(id: str, value_type: String) -> CoordinateRef[str]: ...


@overload
def coordinate(id: str, value_type: Scalar) -> CoordinateRef[object]: ...


def coordinate(id: str, value_type: Scalar | ScalarValueType) -> CoordinateRef[object]:
    """Declare a typed scalar coordinate supplied by each experiment point.

    Pass the same value to module bindings and axis declarations. This keeps the
    coordinate identity and its semantic type on one first-class value edge.
    """

    return internal_point_value_ref(id, _as_scalar(value_type))


@overload
def parameter(id: str, value_type: Bool) -> ValueRef[bool]: ...


@overload
def parameter(id: str, value_type: Entity) -> ValueRef[EntityRef | str]: ...


@overload
def parameter(id: str, value_type: Float) -> ValueRef[float]: ...


@overload
def parameter(id: str, value_type: Int) -> ValueRef[int]: ...


@overload
def parameter(id: str, value_type: Payload) -> ValueRef[PayloadValue]: ...


@overload
def parameter(id: str, value_type: QuantityType) -> ValueRef[Quantity]: ...


@overload
def parameter(id: str, value_type: String) -> ValueRef[str]: ...


@overload
def parameter(id: str, value_type: Scalar) -> ValueRef[object]: ...


@overload
def parameter(
    id: str,
    value_type: Table,
) -> ValueRef[list[dict[str, object]]]: ...


def parameter(
    id: str,
    value_type: Scalar | ScalarValueType | Table,
) -> ValueRef[object]:
    """Declare a typed scalar or table parameter dependency."""

    if isinstance(value_type, Table):
        return internal_table_value_ref(
            ParameterTableSource(id),
            value_type,
        )
    selected_type = _as_scalar(value_type)
    return internal_value_ref_from_expression(
        param(id, selected_type),
        selected_type,
    )


@overload
def parameter_lookup(
    table_id: str,
    *,
    key: Mapping[str, ParameterKeyInput],
    column: str,
    value_type: Bool,
) -> ValueRef[bool]: ...


@overload
def parameter_lookup(
    table_id: str,
    *,
    key: Mapping[str, ParameterKeyInput],
    column: str,
    value_type: Entity,
) -> ValueRef[EntityRef | str]: ...


@overload
def parameter_lookup(
    table_id: str,
    *,
    key: Mapping[str, ParameterKeyInput],
    column: str,
    value_type: Float,
) -> ValueRef[float]: ...


@overload
def parameter_lookup(
    table_id: str,
    *,
    key: Mapping[str, ParameterKeyInput],
    column: str,
    value_type: Int,
) -> ValueRef[int]: ...


@overload
def parameter_lookup(
    table_id: str,
    *,
    key: Mapping[str, ParameterKeyInput],
    column: str,
    value_type: Payload,
) -> ValueRef[PayloadValue]: ...


@overload
def parameter_lookup(
    table_id: str,
    *,
    key: Mapping[str, ParameterKeyInput],
    column: str,
    value_type: QuantityType,
) -> ValueRef[Quantity]: ...


@overload
def parameter_lookup(
    table_id: str,
    *,
    key: Mapping[str, ParameterKeyInput],
    column: str,
    value_type: String,
) -> ValueRef[str]: ...


@overload
def parameter_lookup(
    table_id: str,
    *,
    key: Mapping[str, ParameterKeyInput],
    column: str,
    value_type: Scalar,
) -> ValueRef[object]: ...


def parameter_lookup(
    table_id: str,
    *,
    key: Mapping[str, ParameterKeyInput],
    column: str,
    value_type: Scalar | ScalarValueType,
) -> ValueRef[object]:
    """Declare one typed parameter-table lookup dependency."""

    if any(
        not isinstance(name, str) or not name or not _is_parameter_key_input(value)
        for name, value in cast("Mapping[object, object]", key).items()
    ):
        msg = "parameter lookup keys require typed scalar values or scalar literals"
        raise TypeError(msg)

    captured_key = {
        name: value
        if isinstance(value, ValueRef)
        else cast("ParameterKeyInput", capture_runtime_input(value))
        for name, value in key.items()
    }
    selected_type = _as_scalar(value_type)
    lookup_use = ParameterLookupUse(
        table_id=table_id,
        key_input_types=tuple(
            (name, _parameter_key_value_type(value))
            for name, value in captured_key.items()
        ),
        literal_key_columns=frozenset(
            name
            for name, value in captured_key.items()
            if not isinstance(value, ValueRef)
        ),
        column_id=column,
        result_type=selected_type,
    )
    return internal_parameter_lookup_value_ref(
        lookup_use,
        key=captured_key,
    )


def _as_scalar(value_type: Scalar | ScalarValueType) -> Scalar:
    return value_type if isinstance(value_type, Scalar) else Scalar(value_type)


def compute(
    id: str,
    *,
    fn: ComputeFunction,
    inputs: Mapping[str, ComputeInput] | None = None,
    output_type: Scalar | Array,
) -> Compute:
    """Declare a compute node whose output is a first-class typed value."""

    selected_inputs = tuple((inputs or {}).items())
    invalid = [name for name, value in selected_inputs if not _is_compute_input(value)]
    if invalid:
        msg = (
            f"compute {id!r} inputs must be typed values or "
            f"scalar literals; invalid inputs: {', '.join(invalid)}"
        )
        raise TypeError(msg)
    return Compute(
        id=id,
        fn=fn,
        inputs=tuple(
            (name, capture_compute_input_internal(value))
            for name, value in selected_inputs
        ),
        output_type=output_type,
        declaration_key=ComputeDeclarationKey.fresh(),
    )


def capture_compute_input_internal(value: ComputeInput) -> ComputeInput:
    """Snapshot one trusted compute input for another program declaration."""

    if isinstance(value, ValueRef):
        return value
    if isinstance(value, PayloadValue):
        return value
    return cast("ComputeInput", capture_runtime_input(value))


def _is_compute_input(value: object) -> bool:
    return (
        (isinstance(value, ValueRef) and isinstance(value.value_type, Scalar | Array))
        or value is None
        or isinstance(
            value,
            Quantity | EntityRef | PayloadValue | str | int | float | complex | bool,
        )
    )


def _compute_input_value_type(value: ComputeInput) -> Scalar | Array:
    if isinstance(value, ValueRef):
        return cast("Scalar | Array", value.value_type)
    return literal_scalar_type(value)


def _is_parameter_key_input(value: object) -> bool:
    return (
        value is None
        or isinstance(value, Quantity | EntityRef | str | int | float | bool)
        or (isinstance(value, ValueRef) and isinstance(value.value_type, Scalar))
    )


def _parameter_key_value_type(value: ParameterKeyInput) -> Scalar:
    if isinstance(value, ValueRef):
        return cast("Scalar", value.value_type)
    return literal_scalar_type(value)


def validate_compute_function_internal(
    compute_id: str,
    fn: ComputeFunction,
    input_names: tuple[str, ...],
) -> None:
    try:
        function_signature = signature(fn)
    except (TypeError, ValueError) as error:
        msg = f"compute {compute_id!r} function must have an inspectable signature"
        raise TypeError(msg) from error
    unsupported = [
        parameter.name
        for parameter in function_signature.parameters.values()
        if parameter.kind
        in {
            Parameter.POSITIONAL_ONLY,
            Parameter.VAR_POSITIONAL,
            Parameter.VAR_KEYWORD,
        }
    ]
    if unsupported:
        msg = (
            f"compute {compute_id!r} function must use explicit named parameters; "
            f"unsupported parameters: {', '.join(unsupported)}"
        )
        raise TypeError(msg)
    try:
        function_signature.bind(**dict.fromkeys(input_names, object()))
    except TypeError as error:
        msg = (
            f"compute {compute_id!r} function signature does not match declared "
            f"inputs {input_names}: {error}"
        )
        raise TypeError(msg) from error


__all__ = [
    "Compute",
    "ComputeInput",
    "CoordinateRef",
    "MetadataValue",
    "ModuleInput",
    "ParameterKeyInput",
    "RuntimeInput",
    "ScalarInput",
    "ValueRef",
    "compute",
    "coordinate",
    "input",
    "parameter",
    "parameter_lookup",
]
