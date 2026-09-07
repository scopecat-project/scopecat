"""Backend-neutral scalar cells and row-shaped runtime values."""

from __future__ import annotations

from typing import TypeGuard

from scopecat.kernel.entity import EntityRef
from scopecat.kernel.payloads import PayloadValue
from scopecat.kernel.quantity import Quantity

type ScalarValue = (
    str | int | float | complex | bool | Quantity | EntityRef | PayloadValue | None
)
type CellValue = ScalarValue | dict[str, object]
type Row = dict[str, CellValue]


def is_cell_value(value: object) -> TypeGuard[CellValue]:
    """Return whether a runtime value belongs to the scalar cell domain."""

    return value is None or isinstance(
        value,
        str | int | float | complex | bool | Quantity | EntityRef | PayloadValue | dict,
    )


__all__ = ["CellValue", "Row", "ScalarValue", "is_cell_value"]
