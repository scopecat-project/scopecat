"""Consumer-owned unit contracts shared by value and measured compute calls."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from fractions import Fraction
from functools import wraps
from inspect import Parameter, signature
from typing import Annotated, TypeAliasType, cast, get_args, get_origin, get_type_hints

import numpy as np
from numpy.typing import NDArray

from scopecat.kernel.annotations import unit_scalar_type, value_annotation_metadata
from scopecat.kernel.entity import EntityRef
from scopecat.kernel.errors import ComputeInputContractError
from scopecat.kernel.problems import ProblemPhase, problem
from scopecat.kernel.quantity import Quantity
from scopecat.kernel.units import compatible_units
from scopecat.kernel.value_type_compatibility import (
    describe_value_type,
    is_assignable,
    literal_scalar_type,
)
from scopecat.kernel.value_types import Array as ArrayType
from scopecat.kernel.value_types import (
    ArrayDimension,
    Bool,
    Complex,
    DataType,
    Float,
    Int,
    Payload,
    String,
)
from scopecat.kernel.value_types import Entity as EntityType
from scopecat.kernel.value_types import Quantity as QuantityType
from scopecat.kernel.value_types import Scalar as ScalarType
from scopecat.kernel.value_validation import coerce_literal
from scopecat.program.products import ProductRef
from scopecat.program.value_refs import ValueRef, internal_literal_value_ref
from scopecat.program.values import ComputeFunction, ComputeInput

type ComputeValueArgument = ComputeInput | NDArray[np.generic] | Sequence[object]
type ComputeArgument = ComputeValueArgument | ProductRef


def validate_compute_annotation(
    native_type: object,
    declared: DataType,
    *,
    position: str = "return",
) -> None:
    """Keep inferred native read types consistent with the declared wire schema."""
    while isinstance(native_type, TypeAliasType):
        native_type = cast("object", native_type.__value__)
    if isinstance(declared, ScalarType):
        if isinstance(declared.atom, Payload) and (
            native_type is object or get_origin(native_type) in (dict, Mapping)
        ):
            # Opaque command payloads use their own codec contract and do not
            # participate in native measurement-result inference.
            return
        expected: dict[type[object], object] = {
            Bool: bool,
            Int: int,
            Float: float,
            Complex: complex,
            String: str,
            QuantityType: Quantity,
            EntityType: EntityRef,
        }
        if native_type is expected.get(type(declared.atom)):
            return
    else:
        import numpy as np
        from numpy.typing import NDArray

        arguments = get_args(native_type)
        scalar_types: dict[str, object] = {
            "bool": np.bool_,
            "int64": np.int64,
            "float64": np.float64,
            "complex128": np.complex128,
            "string": np.str_,
        }
        if get_origin(native_type) is NDArray:
            dtype_arguments = arguments
        elif get_origin(native_type) is np.ndarray and len(arguments) == 2:
            dtype_arguments = get_args(arguments[1])
        else:
            dtype_arguments = ()
        if dtype_arguments == (scalar_types[declared.dtype],):
            return
    raise TypeError(
        f"compute {position} annotation disagrees with its declared "
        "ScalarType/ArrayType: "
        f"{native_type!r} with {describe_value_type(declared)}"
    )


def compute_input_data_type(value: object) -> DataType:
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


def _unit(schema: DataType) -> str | None:
    if isinstance(schema, ArrayType):
        return schema.unit
    if isinstance(schema.atom, QuantityType | Complex):
        return schema.atom.unit
    return None


def _quantity(schema: DataType) -> bool:
    return isinstance(schema, ScalarType) and isinstance(schema.atom, QuantityType)


@dataclass(frozen=True, slots=True)
class ComputeInputAdapter:
    source: DataType
    target: DataType
    path: tuple[str, ...]
    magnitude: bool = False

    @property
    def source_quantity(self) -> bool:
        return _quantity(self.source)

    def apply(self, value: object) -> object:
        source_unit, target_unit = _unit(self.source), _unit(self.target)
        if isinstance(self.target, ArrayType):
            value = coerce_literal(self.source, value, path=self.path)
            if source_unit is not None and target_unit != source_unit:
                assert target_unit is not None
                value = convert_array_unit(value, source_unit, target_unit)
        elif (
            isinstance(self.target.atom, Complex)
            and source_unit is not None
            and target_unit != source_unit
        ):
            assert target_unit is not None
            scale = Quantity(1.0, source_unit).to(target_unit).value
            value = cast("complex", value) * scale
        normalized = coerce_literal(self.target, value, path=self.path)
        return cast("Quantity", normalized).value if self.magnitude else normalized


def convert_array_unit(value: object, source_unit: str, target_unit: str) -> object:
    """Scale arrays while preserving dtype and rejecting truncation or overflow."""
    import numpy as np

    source = np.asarray(value)
    if source_unit == target_unit:
        return source
    scale = Quantity(1.0, source_unit).to(target_unit).value
    if source.dtype.kind in "iu":
        factor = Fraction(str(scale))
        numerator = source.astype(object) * factor.numerator
        if np.any(cast("np.ndarray", numerator % factor.denominator != 0)):
            raise ValueError(
                "unit conversion would truncate integer array values; "
                "use a floating-point producer"
            )
        result = numerator // factor.denominator
        limits = np.iinfo(source.dtype)
        if np.any(result < limits.min) or np.any(result > limits.max):
            raise ValueError("unit conversion overflows integer array dtype")
        return np.asarray(result, dtype=source.dtype)
    if source.dtype.kind not in "fc":
        raise TypeError("unit conversion requires a numeric array")
    with np.errstate(over="raise", invalid="raise"):
        return source * scale


def _parameter_contracts(
    fn: ComputeFunction,
) -> dict[str, tuple[object, DataType | None]]:
    hints = cast("Mapping[str, object]", get_type_hints(fn, include_extras=True))
    contracts: dict[str, tuple[object, DataType | None]] = {}
    for name, annotation in hints.items():
        if name == "return":
            continue
        metadata: list[object] = []
        while True:
            if isinstance(annotation, TypeAliasType):
                annotation = cast("object", annotation.__value__)
            elif get_origin(annotation) is Annotated:
                annotation, *extra = cast("tuple[object, ...]", get_args(annotation))
                metadata.extend(extra)
            else:
                break
        declared, unit = value_annotation_metadata(metadata)
        if unit is not None:
            declared = unit_scalar_type(
                Quantity if annotation is float else annotation, unit
            )
        if declared is not None:
            if annotation is float and _quantity(declared):
                if _unit(declared) is None:
                    raise TypeError(
                        f"compute input {name!r}: float magnitudes require "
                        "an explicit target unit"
                    )
            else:
                validate_compute_annotation(
                    annotation, declared, position=f"input {name!r}"
                )
        elif annotation is int:
            declared = ScalarType(Int())
        elif annotation is bool:
            declared = ScalarType(Bool())
        elif annotation is str:
            declared = ScalarType(String())
        elif annotation is float:
            declared = ScalarType(Float())
        elif annotation is complex:
            declared = ScalarType(Complex())
        contracts[name] = annotation, declared
    return contracts


def bind_compute_inputs(
    compute_id: str,
    fn: ComputeFunction,
    inputs: dict[str, ComputeArgument],
) -> dict[str, ComputeInputAdapter]:
    try:
        contracts = _parameter_contracts(fn)
    except TypeError as error:
        raise _contract_error(f"compute {compute_id!r}: {error}") from error
    parameters = signature(fn).parameters
    for name, parameter in parameters.items():
        default = cast("object", parameter.default)
        if name not in inputs and default is not Parameter.empty:
            native, declared = contracts.get(name, (None, None))
            if (
                declared is not None
                or native is Quantity
                or isinstance(default, Quantity | str | int | float | complex | bool)
            ):
                inputs[name] = cast("ComputeArgument", default)
    adapters: dict[str, ComputeInputAdapter] = {}
    for name, value in tuple(inputs.items()):
        native, target = contracts.get(name, (None, None))
        try:
            if (
                value is None
                and target is None
                and cast("object", parameters[name].default) is None
            ):
                # Preserve native optional defaults without inventing a null wire type.
                del inputs[name]
                continue
            if isinstance(target, ArrayType) and not isinstance(
                value, ValueRef | ProductRef
            ):
                # Native arrays/lists have no source unit. The explicit consumer
                # contract supplies axes and the unit in which magnitudes are written.
                value = internal_literal_value_ref(
                    value,
                    replace(target, unit=None),
                    path=("compute", compute_id, name),
                )
                inputs[name] = value
            if isinstance(value, ValueRef) and not isinstance(
                value.value_type, ScalarType | ArrayType
            ):
                if target is None:
                    # Unannotated tables keep structured cells, including quantities.
                    continue
                raise TypeError(
                    f"expects {describe_value_type(target)}, "
                    f"got {describe_value_type(value.value_type)}"
                )
            if value is None and target is not None:
                raise TypeError(f"expects {describe_value_type(target)}, got NoneType")
            source = compute_input_data_type(value)
            source_unit = _unit(source)
            if native is Quantity and target is None:
                if not _quantity(source):
                    raise TypeError("expects Quantity")
                target = ScalarType(QuantityType())
            elif (source_unit is not None or _quantity(source)) and (
                target is None
                or (
                    _unit(target) is None
                    and not (native is Quantity and _quantity(target))
                )
            ):
                raise TypeError(
                    "unit-bearing input requires a consumer unit contract; "
                    "declare Quantity or Annotated[..., Unit(...)]/"
                    "ArrayType(unit=...), "
                    "or explicitly convert and extract magnitudes before this call"
                    + (
                        f"; expects {describe_value_type(target)}, "
                        f"got {describe_value_type(source)}"
                        if target is not None
                        else ""
                    )
                )
            if target is None:
                continue
            numeric_magnitude = (
                native is float
                and _quantity(target)
                and isinstance(source, ScalarType)
                and isinstance(source.atom, Float | Int)
            )
            if not numeric_magnitude and not _assignable(source, target):
                raise TypeError(
                    f"expects {describe_value_type(target)}, "
                    f"got {describe_value_type(source)}"
                )
            adapter = ComputeInputAdapter(
                source,
                target,
                path=("compute", compute_id, name),
                magnitude=native is float and _quantity(target),
            )
            # Diagnose concrete invalid inputs before recording a graph.
            if not isinstance(value, ValueRef | ProductRef):
                adapter.apply(value)
            adapters[name] = adapter
        except (TypeError, ValueError, ArithmeticError) as error:
            raise _contract_error(
                f"compute {compute_id!r} input {name!r} {error}"
            ) from error
    return adapters


def _contract_error(message: str) -> ComputeInputContractError:
    return ComputeInputContractError(
        problem("compute_input_contract", message, phase=ProblemPhase.AUTHORING)
    )


def _assignable(source: DataType, target: DataType) -> bool:
    source_unit, target_unit = _unit(source), _unit(target)
    if isinstance(source, ArrayType) and isinstance(target, ArrayType):
        if source_unit != target_unit:
            if target_unit is None or (
                source_unit is not None
                and not compatible_units(source_unit, target_unit)
            ):
                return False
            source = replace(source, unit=target_unit)
        return is_assignable(source, target)
    if isinstance(source, ScalarType) and isinstance(target, ScalarType):
        if (
            isinstance(source.atom, Int | Float)
            and isinstance(target.atom, Complex)
            and target_unit is None
        ):
            return True
        if isinstance(source.atom, Int) and isinstance(target.atom, Float):
            return is_assignable(
                ScalarType(
                    Float(minimum=source.atom.minimum, maximum=source.atom.maximum)
                ),
                target,
            )
        if isinstance(source.atom, Complex) and isinstance(target.atom, Complex):
            return (
                source_unit == target_unit
                or source_unit is None
                or (
                    target_unit is not None
                    and compatible_units(source_unit, target_unit)
                )
            )
    return is_assignable(source, target)


def adapt_compute_function(
    fn: ComputeFunction, adapters: Mapping[str, ComputeInputAdapter]
) -> ComputeFunction:
    if not adapters:
        return fn

    @wraps(fn)
    def adapted(**values: object) -> object:
        normalized = dict(values)
        for name, adapter in adapters.items():
            try:
                normalized[name] = adapter.apply(values[name])
            except (TypeError, ValueError, ArithmeticError) as error:
                raise ValueError(f"{'.'.join(adapter.path)}: {error}") from error
        return fn(**normalized)

    return adapted
