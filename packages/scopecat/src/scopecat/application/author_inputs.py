"""Derive the bounded JSON form surface from ordinary Python declarations."""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Literal, cast, get_args, get_origin

from pydantic import BaseModel, ConfigDict, TypeAdapter, create_model

from scopecat.authoring.definitions import Input
from scopecat.authoring.experiments import Experiment
from scopecat.program.controls import ControlSet


def _json_scalar(annotation: object) -> bool:
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


def author_input_model(
    experiment: Experiment[..., object],
    controls: ControlSet,
) -> type[BaseModel]:
    """Expose scalar function inputs; controls retain their own form surface."""
    excluded = {field.id for field in controls.fields}
    fields: dict[str, tuple[object, object]] = {}
    for item in experiment.inputs:
        name = item.name
        if name in excluded:
            continue
        annotation = item.annotation
        if get_origin(annotation) is Annotated:
            annotation = cast("tuple[object, ...]", get_args(annotation))[0]
        if get_origin(annotation) is Input:
            [annotation] = cast("tuple[object, ...]", get_args(annotation))
        if not _json_scalar(annotation):
            raise TypeError(
                f"author input {name!r} needs a JSON scalar annotation "
                "(str/int/float/bool or a Literal of one scalar type); "
                "complex structural objects need maintained composition"
            )
        if not item.required:
            TypeAdapter[object](
                annotation, config=ConfigDict(strict=True)
            ).validate_python(item.default)
        fields[name] = (annotation, ... if item.required else item.default)
    factory = cast("Callable[..., type[BaseModel]]", create_model)
    model = factory(
        f"{experiment.__name__}Inputs",
        __config__=ConfigDict(extra="forbid", strict=True, validate_default=True),
        **fields,
    )
    return model
