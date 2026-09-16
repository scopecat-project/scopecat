"""Small, JSON-backed managed argument boundary; source owns native decoding."""

from __future__ import annotations

import inspect
from collections.abc import Callable, Mapping, Sequence
from types import UnionType
from typing import Annotated, Literal, cast, get_args, get_origin, get_type_hints

from pydantic import JsonValue, TypeAdapter, ValidationError

from scopecat.kernel.quantity import Quantity

type AnalysisArgument = (
    bool
    | int
    | float
    | str
    | Quantity
    | Sequence[AnalysisArgument]
    | Mapping[str, AnalysisArgument]
    | None
)


def encode_arguments(
    analysis: str, arguments: Mapping[str, AnalysisArgument] | None
) -> dict[str, JsonValue]:
    """Keep requested units/values, without pickling or importing author code."""
    return {
        name: _encode(value, f"{analysis} argument {name!r}")
        for name, value in (arguments or {}).items()
    }


def _encode(value: object, label: str) -> JsonValue:
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, Quantity):
        return {"value": value.value, "unit": value.unit}
    if isinstance(value, Mapping):
        selected = cast("Mapping[object, object]", value)
        if not all(isinstance(key, str) for key in selected):
            raise TypeError(f"{label}: mappings require string keys")
        return {
            cast("str", key): _encode(item, f"{label}[{key!r}]")
            for key, item in selected.items()
        }
    if isinstance(value, Sequence) and not isinstance(value, bytes | bytearray):
        return [
            _encode(item, f"{label}[{index}]")
            for index, item in enumerate(cast("Sequence[object]", value))
        ]
    raise TypeError(
        f"{label}: unsupported {type(value).__qualname__}; use JSON values or Quantity"
    )


def _supported(annotation: object) -> bool:
    if any(
        annotation is item for item in (bool, int, float, str, type(None), Quantity)
    ):
        return True
    origin = get_origin(annotation)
    args = cast("tuple[object, ...]", get_args(annotation))
    if origin is Annotated:
        return _supported(args[0])
    if origin is Literal:
        return all(
            item is None or isinstance(item, bool | int | float | str) for item in args
        )
    if origin in (UnionType, list):
        return bool(args) and all(_supported(item) for item in args)
    if origin is dict:
        return len(args) == 2 and args[0] is str and _supported(args[1])
    return False


def bind_arguments(
    function: Callable[..., object], arguments: Mapping[str, JsonValue]
) -> dict[str, object]:
    """Validate against the selected retained source, including its defaults."""
    name = f"{function.__module__}:{function.__qualname__}"
    signature = inspect.signature(function)
    parameters = tuple(signature.parameters.values())[1:]
    try:
        bound = signature.replace(parameters=parameters).bind(**arguments)
    except TypeError as error:
        raise TypeError(f"{name}: {error}") from error
    bound.apply_defaults()
    hints = cast("Mapping[str, object]", get_type_hints(function, include_extras=True))
    values: dict[str, object] = {}
    for key, value in cast("Mapping[str, object]", bound.arguments).items():
        annotation = hints.get(key)
        label = f"{name} argument {key!r}"
        if annotation is None:
            values[key] = value
            continue
        if not _supported(annotation):
            raise TypeError(f"{label}: unsupported annotation {annotation!r}")
        adapter: TypeAdapter[object] = TypeAdapter(annotation)
        try:
            values[key] = adapter.validate_python(value, strict=True)
        except ValidationError as error:
            detail = error.errors(include_url=False)[0]["msg"]
            raise TypeError(f"{label}: expected {annotation!r}; {detail}") from error
    return values
