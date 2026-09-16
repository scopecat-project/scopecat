"""Validate native dataclass readers against retained measurement result contracts."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import fields, is_dataclass
from functools import cache
from typing import (
    Annotated,
    Protocol,
    TypeAliasType,
    TypeVar,
    cast,
    get_args,
    get_origin,
    get_type_hints,
)

import numpy as np
from numpy.typing import NDArray

from scopecat.kernel.annotations import value_annotation_metadata
from scopecat.kernel.qualified_name import parse_qualified_name
from scopecat.kernel.quantity import Quantity
from scopecat.kernel.value_types import Array, Scalar
from scopecat.kernel.value_types import Quantity as QuantityType
from scopecat.kernel.value_validation import validate_literal
from scopecat.program.measurement_types import measurement_value_spec_from_scalar
from scopecat.records.measurement import MeasurementDatasetSchema


class _Variable(Protocol):
    @property
    def dtype(self) -> str: ...
    @property
    def unit(self) -> str | None: ...
    @property
    def dims(self) -> tuple[str, ...]: ...
    @property
    def availability(self) -> tuple[object | None, ...]: ...
    def require_values(self) -> tuple[object, ...]: ...
    def require_quantities(self) -> tuple[Quantity, ...]: ...


class _DatasetSchema(Protocol):
    @property
    def schema(self) -> MeasurementDatasetSchema: ...
    @property
    def dims(self) -> Mapping[str, int | None]: ...


class _ResultView(Protocol):
    @property
    def paths(self) -> tuple[tuple[str, ...], ...]: ...
    @property
    def dataset(self) -> _DatasetSchema: ...
    def variable(self, path: tuple[str, ...], /) -> _Variable: ...
    def __len__(self) -> int: ...


type _Reader = Callable[[int], object]


def result_rows_as[RowT](
    view: _ResultView,
    row_type: type[RowT],
) -> tuple[RowT, ...]:
    """Require the complete persisted field structure; never rebuild author code."""
    paths: set[tuple[str, ...]] = set()
    reader = _reader(row_type, view, (), {}, paths, ())
    if paths != set(view.paths):
        missing = sorted(set(view.paths) - paths)
        extra = sorted(paths - set(view.paths))
        raise TypeError(
            f"result dataclass fields differ: unread={missing}, absent={extra}"
        )
    return tuple(cast("RowT", reader(point)) for point in range(len(view)))


def _resolve(annotation: object, substitutions: Mapping[object, object]) -> object:
    if isinstance(annotation, TypeVar):
        return substitutions.get(annotation, annotation)
    while isinstance(annotation, TypeAliasType):
        annotation = cast("object", annotation.__value__)
    return annotation


def _reader(
    annotation: object,
    view: _ResultView,
    path: tuple[str, ...],
    substitutions: Mapping[object, object],
    paths: set[tuple[str, ...]],
    ancestors: tuple[object, ...],
) -> _Reader:
    annotation = _resolve(annotation, substitutions)
    origin = cast("object", get_origin(annotation))
    cls = origin or annotation
    if isinstance(cls, type) and is_dataclass(cls):
        if cls in ancestors:
            raise TypeError(f"recursive result dataclass at {path!r}")
        parameters = cast("tuple[object, ...]", getattr(cls, "__parameters__", ()))
        arguments = cast("tuple[object, ...]", get_args(annotation))
        bound = {
            **substitutions,
            **dict(
                zip(
                    parameters,
                    (_resolve(arg, substitutions) for arg in arguments),
                    strict=False,
                )
            ),
        }
        hints = cast("dict[str, object]", get_type_hints(cls, include_extras=True))
        children: dict[str, _Reader] = {}
        for member in fields(cls):
            if not member.init:
                raise TypeError(
                    f"result dataclass field {member.name!r} must be an init field"
                )
            children[member.name] = _reader(
                hints[member.name],
                view,
                (*path, member.name),
                bound,
                paths,
                (*ancestors, cls),
            )
        constructor = cast("Callable[..., object]", cls)
        return lambda point: constructor(
            **{name: read(point) for name, read in children.items()}
        )
    if not path:
        raise TypeError("rows_as requires a native dataclass type")
    paths.add(path)
    if path not in view.paths:
        raise TypeError(f"result has no persisted field {'/'.join(path)!r}")
    declared: Array | Scalar | None = None
    if origin is Annotated:
        native, *metadata = cast("tuple[object, ...]", get_args(annotation))
        annotation = _resolve(native, substitutions)
        declared, unit = value_annotation_metadata(metadata)
        if unit is not None and view.variable(path).unit != unit.name:
            raise TypeError(
                f"result unit disagrees at {path!r}: "
                f"expected {unit.name}, got {view.variable(path).unit}"
            )
    return _leaf_reader(annotation, declared, view, path, substitutions)


def _leaf_reader(
    native: object,
    declared: Array | Scalar | None,
    view: _ResultView,
    path: tuple[str, ...],
    substitutions: Mapping[object, object],
) -> _Reader:
    variable = view.variable(path)
    scalar_dtypes = {
        bool: "bool",
        int: "int64",
        float: "float64",
        complex: "complex128",
        str: "string",
    }
    dtype = scalar_dtypes.get(native) if isinstance(native, type) else None
    quantity = native is Quantity
    array_dtype = _array_dtype(native, substitutions)
    if quantity:
        valid = (
            len(variable.dims) == 1
            and variable.dtype in {"int64", "float64"}
            and variable.unit is not None
        )
    elif dtype is not None:
        valid = len(variable.dims) == 1 and variable.dtype == dtype
    elif array_dtype is not None:
        valid = len(variable.dims) > 1 and variable.dtype == array_dtype
    else:
        raise TypeError(f"unsupported native result type at {path!r}: {native!r}")
    if not valid:
        raise TypeError(
            f"result field {path!r} does not match {native!r}: "
            f"dtype={variable.dtype}, dims={variable.dims}"
        )
    if declared is not None:
        _validate_declared(
            declared, view, path, quantity=quantity, array=array_dtype is not None
        )

    @cache
    def values() -> tuple[object, ...]:
        if any(item is not None for item in variable.availability):
            raise ValueError(
                f"result field {path!r} has unavailable values; "
                "select complete points with where_available first"
            )
        return variable.require_quantities() if quantity else variable.require_values()

    def read(index: int) -> object:
        value = values()[index]
        if array_dtype is not None and not isinstance(value, np.ndarray):
            raise TypeError(
                f"result field {path!r} is segmented; a dense ndarray was requested"
            )
        if isinstance(declared, Scalar):
            validate_literal(declared, value, path=("result", *path))
        elif isinstance(declared, Array) and isinstance(value, np.ndarray):
            array_value = cast("NDArray[np.generic]", value)
            shape = cast("tuple[int, ...]", array_value.shape)
            for size, axis in zip(shape, declared.dimensions, strict=True):
                if axis.size is not None and size != axis.size:
                    raise ValueError(
                        f"result field {path!r} axis {axis.id!r} "
                        f"requires {axis.size} values, got {size}"
                    )
        return cast("object", value)

    return read


def _array_dtype(native: object, substitutions: Mapping[object, object]) -> str | None:
    origin = get_origin(native)
    arguments = cast("tuple[object, ...]", get_args(native))
    if origin is NDArray:
        dtype_args = arguments
    elif origin is np.ndarray and len(arguments) == 2:
        if arguments[0] != tuple[int, ...]:
            return None
        dtype_args = cast("tuple[object, ...]", get_args(arguments[1]))
    else:
        return None
    if len(dtype_args) != 1:
        return None
    dtype = _resolve(dtype_args[0], substitutions)
    dtypes: dict[object, str] = {
        np.bool_: "bool",
        np.int64: "int64",
        np.float64: "float64",
        np.complex128: "complex128",
        np.str_: "string",
    }
    return dtypes.get(dtype)


def _validate_declared(
    declared: Scalar | Array,
    view: _ResultView,
    path: tuple[str, ...],
    *,
    quantity: bool,
    array: bool,
) -> None:
    variable = view.variable(path)
    if isinstance(declared, Scalar):
        if array or quantity != isinstance(declared.atom, QuantityType):
            raise TypeError(f"native result type and ScalarType disagree at {path!r}")
        expected_dtype, expected_unit = measurement_value_spec_from_scalar(declared)
        if variable.dtype != expected_dtype or variable.unit != expected_unit:
            raise TypeError(
                f"result dtype/unit disagree at {path!r}: "
                f"expected {expected_dtype}/{expected_unit}, "
                f"got {variable.dtype}/{variable.unit}"
            )
        return
    if not array or variable.dtype != declared.dtype or variable.unit != declared.unit:
        raise TypeError(f"result array dtype/unit disagree at {path!r}")
    if len(variable.dims) != len(declared.dimensions) + 1:
        raise TypeError(f"result array rank disagrees at {path!r}")
    dimensions = {item.id: item for item in view.dataset.schema.dimensions}
    for dimension_id, expected in zip(
        variable.dims[1:], declared.dimensions, strict=True
    ):
        actual = dimensions[dimension_id]
        name = parse_qualified_name(actual.id)[1]
        if expected.id not in (actual.id, name) or (
            expected.kind is not None and expected.kind != actual.kind
        ):
            raise TypeError(
                f"result array axis disagrees at {path!r}: "
                f"expected {expected.id}/{expected.kind}, got {actual.id}/{actual.kind}"
            )
        if expected.unit is not None and actual.metadata.get("unit") != expected.unit:
            raise TypeError(
                f"result array axis unit disagrees at {path!r}: {expected.id}"
            )
        size = view.dataset.dims[dimension_id]
        if expected.size is not None and size is not None and expected.size != size:
            raise TypeError(
                f"result array extent disagrees at {path!r}: "
                f"expected {expected.size}, got {size}"
            )
