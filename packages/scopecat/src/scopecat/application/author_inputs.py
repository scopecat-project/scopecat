"""Derive the bounded JSON form surface from ordinary Python declarations."""

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Literal, cast, get_args, get_origin, get_type_hints

from pydantic import BaseModel, ConfigDict, create_model

from scopecat.authoring.experiments import Experiment
from scopecat.program.controls import ControlSet
from scopecat.program.definitions import ExperimentInvocation


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
    invocation: ExperimentInvocation,
    controls: ControlSet,
) -> type[BaseModel]:
    """Controls and runtime ports keep their existing ownership and validation."""
    excluded = {field.id for field in controls.fields} | {
        port.id for port in invocation.definition.inputs
    }
    hints: dict[str, object] = get_type_hints(
        experiment.__wrapped__, include_extras=True
    )
    fields: dict[str, tuple[object, object]] = {}
    for name, parameter in inspect.signature(experiment).parameters.items():
        if name in excluded:
            continue
        annotation = hints.get(name)
        if not _json_scalar(annotation):
            raise TypeError(
                f"author input {name!r} needs a JSON scalar annotation "
                "(str/int/float/bool or a Literal of one scalar type); "
                "complex structural objects need maintained composition"
            )
        default = cast("object", parameter.default)
        if default is inspect.Parameter.empty:
            raise ValueError(f"author input {name!r} needs a default for discovery")
        fields[name] = (annotation, default)
    factory = cast("Callable[..., type[BaseModel]]", create_model)
    model = factory(
        f"{experiment.__name__}Inputs",
        __config__=ConfigDict(extra="forbid", strict=True, validate_default=True),
        **fields,
    )
    _ = model.model_validate({})
    return model
