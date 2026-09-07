"""Rewrite scalar input references against canonical program expressions.

This module is independent of authoring value handles. Frontends adapt their
source values into scalar expressions before crossing this boundary.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, replace
from typing import cast, override

from scopecat.kernel.entity import EntityRef
from scopecat.kernel.payloads import PayloadValue
from scopecat.kernel.quantity import Quantity
from scopecat.kernel.value_data import CellValue
from scopecat.kernel.value_type_compatibility import is_assignable
from scopecat.program.expressions import (
    BinaryScalarExpr,
    InputScalarExpr,
    ParameterLookupScalarExpr,
    ScalarExpr,
    lit,
)

_EMPTY_INPUT_RESOLUTION: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class _LexicalReplacement:
    """One parent expression closed against the current child environment."""

    value: object


class _LexicalReplacements(Mapping[str, object]):
    """Lazily mark one composition layer as single-substitution values."""

    def __init__(self, values: Mapping[str, object]) -> None:
        self._values = values

    @override
    def __getitem__(self, key: str) -> object:
        return _LexicalReplacement(self._values[key])

    @override
    def __iter__(self) -> Iterator[str]:
        return iter(self._values)

    @override
    def __len__(self) -> int:
        return len(self._values)


def bind_scalar_input_refs(
    expression: ScalarExpr,
    inputs: Mapping[str, object],
    *,
    resolving: frozenset[str] = _EMPTY_INPUT_RESOLUTION,
) -> ScalarExpr:
    """Bind scalar input nodes without exposing relation syntax to users."""

    scalar = expression
    if isinstance(scalar, InputScalarExpr):
        input_name = scalar.name
        if input_name not in inputs:
            return scalar
        selected = inputs[input_name]
        substitute_once = isinstance(selected, _LexicalReplacement)
        value = selected.value if substitute_once else selected
        next_resolving = _descend_input_resolution(input_name, resolving)
        if isinstance(value, ScalarExpr):
            value_scalar = value
            if not is_assignable(value_scalar.value_type, scalar.value_type):
                msg = f"input {input_name!r} replacement has an incompatible value type"
                raise TypeError(msg)
            if substitute_once:
                return value_scalar
            if (
                isinstance(value_scalar, InputScalarExpr)
                and value_scalar.name == input_name
            ):
                return value_scalar
            bound = bind_scalar_input_refs(
                value_scalar,
                inputs,
                resolving=next_resolving,
            )
            return bound
        return lit(input_cell(value), scalar.value_type)
    if isinstance(scalar, ParameterLookupScalarExpr):
        return replace(
            scalar,
            key={
                name: bind_scalar_input_refs(
                    value,
                    inputs,
                    resolving=resolving,
                )
                for name, value in scalar.key.items()
            },
        )
    if isinstance(scalar, BinaryScalarExpr):
        return replace(
            scalar,
            left=bind_scalar_input_refs(
                scalar.left,
                inputs,
                resolving=resolving,
            ),
            right=bind_scalar_input_refs(
                scalar.right,
                inputs,
                resolving=resolving,
            ),
        )
    return scalar


def substitute_scalar_input_refs(
    expression: ScalarExpr,
    inputs: Mapping[str, object],
) -> ScalarExpr:
    """Substitute one composition layer while preserving unbound input nodes."""

    return bind_scalar_input_refs(
        expression,
        _LexicalReplacements(inputs),
    )


def input_cell(value: object) -> CellValue:
    if (
        isinstance(
            value,
            Quantity | EntityRef | PayloadValue | str | int | float | complex | bool,
        )
        or value is None
    ):
        return value
    if isinstance(value, Mapping):
        mapping = cast("Mapping[object, object]", value)
        if not all(isinstance(key, str) for key in mapping):
            msg = "record input keys must be strings"
            raise TypeError(msg)
        return cast("dict[str, object]", dict(mapping))
    msg = f"input value is not available as a scalar expression value: {value!r}"
    raise TypeError(msg)


def _descend_input_resolution(
    input_name: str,
    resolving: frozenset[str],
) -> frozenset[str]:
    if input_name in resolving:
        msg = f"cyclic module input reference: {input_name}"
        raise ValueError(msg)
    return resolving | {input_name}
