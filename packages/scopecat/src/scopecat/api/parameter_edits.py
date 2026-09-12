"""Shared transient edit descriptions for workspaces and external table imports."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from scopecat.records.parameter import ParameterAtomValue

type RowKey = ParameterAtomValue | tuple[ParameterAtomValue, ...]


def same_parameter_value(left: object, right: object) -> bool:
    # Python's True == 1 must not hide an invalid scalar/cell edit from validation.
    if isinstance(left, bool) != isinstance(right, bool):
        return False
    return left == right


@dataclass(frozen=True, slots=True)
class ParameterEdit:
    """One readable cell/row change; None denotes an absent value in this slice."""

    parameter: str
    key: RowKey | None
    field: str | None
    before: ParameterAtomValue | Mapping[str, ParameterAtomValue] | None
    after: ParameterAtomValue | Mapping[str, ParameterAtomValue] | None
