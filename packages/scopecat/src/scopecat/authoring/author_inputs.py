"""Derive the bounded JSON form surface from ordinary Python declarations."""

from __future__ import annotations

from collections.abc import Callable
from typing import (
    Annotated,
    Literal,
    TypeAliasType,
    TypeGuard,
    cast,
    get_args,
    get_origin,
)

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    TypeAdapter,
    WithJsonSchema,
    create_model,
)
from typing_extensions import TypeForm

from scopecat.authoring.definitions import Input
from scopecat.authoring.experiments import Experiment
from scopecat.kernel.quantity import Quantity
from scopecat.kernel.value_types import Quantity as QuantityType
from scopecat.kernel.value_types import Scalar
from scopecat.kernel.value_validation import coerce_literal
from scopecat.program.controls import ControlSet


def _unalias(annotation: object) -> object:
    while isinstance(annotation, TypeAliasType):
        annotation = cast("object", annotation.__value__)
    return annotation


def _json_scalar(annotation: object) -> TypeGuard[TypeForm[object]]:
    if any(annotation is scalar for scalar in (str, int, float, bool)):
        return True
    origin = get_origin(annotation)
    if origin is Literal:
        values = cast("tuple[object, ...]", get_args(annotation))
        return (
            bool(values)
            and type(values[0]) in (str, int, float, bool)
            and all(type(value) is type(values[0]) for value in values)
        )
    return False


def _quantity_annotation(metadata: tuple[object, ...]) -> object:
    declared = [item.atom if isinstance(item, Scalar) else item for item in metadata]
    constraint = next(
        (item for item in declared if isinstance(item, QuantityType)), QuantityType()
    )

    if not constraint.finite:
        raise TypeError(
            "author Quantity inputs require finite=True for JSON round trips; "
            "finite=False declarations are not supported by the author form"
        )

    def validate(value: Quantity) -> Quantity:
        # Validate converted bounds without replacing the explicitly entered unit.
        coerce_literal(Scalar(constraint), value)
        return value

    schema = Quantity.model_json_schema()
    schema.pop("title", None)
    schema["x-scopecat-quantity"] = {
        "dimension": constraint.dimension,
        "unit": constraint.unit,
        "minimum": constraint.minimum,
        "maximum": constraint.maximum,
        "finite": constraint.finite,
    }
    return Annotated[Quantity, AfterValidator(validate), WithJsonSchema(schema)]


def author_input_model(
    experiment: Experiment[..., object],
    controls: ControlSet,
) -> type[BaseModel]:
    """Expose scalar and explicit Quantity inputs; controls retain their own surface."""
    excluded = {field.id for field in controls.fields}
    fields: dict[str, tuple[object, object]] = {}
    for item in experiment.inputs:
        name = item.name
        if name in excluded:
            continue
        annotation = _unalias(item.annotation)
        metadata: tuple[object, ...] = ()
        if get_origin(annotation) is Annotated:
            head, *extras = cast("tuple[object, ...]", get_args(annotation))
            metadata = tuple(extras)
            annotation = _unalias(head)
        if get_origin(annotation) is Input:
            [annotation] = cast("tuple[object, ...]", get_args(annotation))
            annotation = _unalias(annotation)
        if annotation is Quantity and item.runtime:
            annotation = _quantity_annotation(metadata)
        elif not _json_scalar(annotation):
            raise TypeError(
                f"author input {name!r} needs a JSON scalar annotation "
                "(str/int/float/bool or a Literal of one scalar type), "
                "or Input[Quantity]; "
                "complex structural objects need maintained composition"
            )
        if not item.required:
            TypeAdapter[object](cast("TypeForm[object]", annotation)).validate_python(
                item.default, strict=True
            )
        fields[name] = (annotation, ... if item.required else item.default)
    factory = cast("Callable[..., type[BaseModel]]", create_model)
    model = factory(
        f"{experiment.__name__}Inputs",
        __config__=ConfigDict(extra="forbid", strict=True, validate_default=True),
        **fields,
    )
    return model
