"""Concrete numeric quantities shared by durable and runtime contracts."""

from __future__ import annotations

from typing import Protocol, overload

from pydantic import BaseModel, ConfigDict, field_validator

from scopecat.kernel.units import (
    compatible_units,
    convert_linear_value,
    divide_quantities_to_dimensionless,
    is_supported_unit,
    multiply_quantities_to_dimensionless,
)


class _ReflectedQuantityAdd[T](Protocol):
    def __radd__(self, other: Quantity, /) -> T: ...


class _ReflectedQuantitySubtract[T](Protocol):
    def __rsub__(self, other: Quantity, /) -> T: ...


class Quantity(BaseModel):
    """A numeric value with an explicit unit."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
    )

    value: float
    unit: str

    @field_validator("value", mode="before")
    @classmethod
    def validate_value(cls, value: object) -> object:
        if not isinstance(value, int | float) or isinstance(value, bool):
            msg = "quantity value must be an int or float"
            raise ValueError(msg)
        return value

    def __init__(
        self,
        value: float | None = None,
        unit: str | None = None,
        **data: object,
    ) -> None:
        if value is not None:
            if "value" in data:
                msg = "Quantity value was provided twice"
                raise TypeError(msg)
            data["value"] = value
        if unit is not None:
            if "unit" in data:
                msg = "Quantity unit was provided twice"
                raise TypeError(msg)
            data["unit"] = unit
        super().__init__(**data)

    @field_validator("unit")
    @classmethod
    def validate_unit(cls, value: str) -> str:
        if not is_supported_unit(value):
            msg = f"unsupported unit: {value}"
            raise ValueError(msg)
        return value

    def to(self, unit: str) -> Quantity:
        """Return this quantity converted to another compatible linear unit."""

        if not is_supported_unit(unit):
            msg = f"unsupported unit: {unit}"
            raise ValueError(msg)
        if self.unit == unit:
            return self
        if not compatible_units(self.unit, unit):
            msg = f"cannot convert {self.unit!r} to {unit!r}"
            raise ValueError(msg)
        converted = convert_linear_value(self.value, self.unit, unit)
        if converted is None:
            msg = f"unit conversion is not linear: {self.unit!r} to {unit!r}"
            raise ValueError(msg)
        return Quantity(value=converted, unit=unit)

    @overload
    def __add__(self, other: Quantity) -> Quantity: ...

    @overload
    def __add__[T](self, other: _ReflectedQuantityAdd[T]) -> T: ...

    def __add__(self, other: object) -> object:
        if not isinstance(other, Quantity):
            return NotImplemented
        converted = other.to(self.unit)
        return Quantity(value=self.value + converted.value, unit=self.unit)

    @overload
    def __sub__(self, other: Quantity) -> Quantity: ...

    @overload
    def __sub__[T](self, other: _ReflectedQuantitySubtract[T]) -> T: ...

    def __sub__(self, other: object) -> object:
        if not isinstance(other, Quantity):
            return NotImplemented
        converted = other.to(self.unit)
        return Quantity(value=self.value - converted.value, unit=self.unit)

    def __neg__(self) -> Quantity:
        """Negate this quantity without changing its units."""
        return Quantity(value=-self.value, unit=self.unit)

    @overload
    def __mul__(self, other: float) -> Quantity: ...

    @overload
    def __mul__(self, other: Quantity) -> float: ...

    def __mul__(self, other: object) -> Quantity | float:
        if isinstance(other, Quantity):
            product = multiply_quantities_to_dimensionless(
                self.value,
                self.unit,
                other.value,
                other.unit,
            )
            if product is None:
                return NotImplemented
            return product
        if isinstance(other, int | float) and not isinstance(other, bool):
            return Quantity(value=self.value * float(other), unit=self.unit)
        return NotImplemented

    @overload
    def __rmul__(self, other: float) -> Quantity: ...

    @overload
    def __rmul__(self, other: Quantity) -> float: ...

    def __rmul__(self, other: object) -> Quantity | float:
        if isinstance(other, Quantity):
            return other * self
        if isinstance(other, int | float) and not isinstance(other, bool):
            return Quantity(value=float(other) * self.value, unit=self.unit)
        return NotImplemented

    @overload
    def __truediv__(self, other: float) -> Quantity: ...

    @overload
    def __truediv__(self, other: Quantity) -> float: ...

    def __truediv__(self, other: object) -> Quantity | float:
        if isinstance(other, Quantity):
            ratio = divide_quantities_to_dimensionless(
                self.value,
                self.unit,
                other.value,
                other.unit,
            )
            if ratio is None:
                return NotImplemented
            return ratio
        if isinstance(other, int | float) and not isinstance(other, bool):
            if other == 0:
                msg = "cannot divide quantity by zero"
                raise ZeroDivisionError(msg)
            return Quantity(value=self.value / float(other), unit=self.unit)
        return NotImplemented
