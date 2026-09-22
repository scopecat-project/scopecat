"""Structural and lexical identity for transient recipe declarations."""

import inspect
from collections.abc import Mapping, Sequence
from dataclasses import fields, is_dataclass
from functools import partial
from typing import cast

from scopecat.kernel.content_identity import content_fingerprint
from scopecat.kernel.python_source import python_source_identity


def recipe_definition_identity(value: object) -> object:
    """Fingerprint declared data and lexical functions, excluding derived caches.

    Transitive module dependencies are owned by the retained author revision.
    Mutating globals or recipe objects after construction is not supported.
    """
    value_type = type(value)
    type_name = f"{value_type.__module__}.{value_type.__qualname__}"
    if inspect.isfunction(value):
        return {
            "function": python_source_identity(value, label="pulse recipe"),
            "defaults": recipe_definition_identity(value.__defaults__),
            "kwdefaults": recipe_definition_identity(value.__kwdefaults__),
            "captures": recipe_definition_identity(
                inspect.getclosurevars(value).nonlocals
            ),
        }
    if isinstance(value, partial):
        return {
            "partial": recipe_definition_identity(value.func),
            "args": recipe_definition_identity(value.args),
            "keywords": recipe_definition_identity(value.keywords),
        }
    if is_dataclass(value) and not isinstance(value, type):
        return {
            "type": type_name,
            "fields": tuple(
                (
                    field.name,
                    recipe_definition_identity(
                        cast("object", getattr(value, field.name))
                    ),
                )
                for field in fields(value)
                if field.init
            ),
        }
    if isinstance(value, Mapping):
        return {
            "type": type_name,
            "items": tuple(
                (recipe_definition_identity(key), recipe_definition_identity(item))
                for key, item in cast("Mapping[object, object]", value).items()
            ),
        }
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        return {
            "type": type_name,
            "items": tuple(recipe_definition_identity(item) for item in value),
        }
    return content_fingerprint(value)
