"""Numeric input metadata; Python parameters own names and defaults."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True, kw_only=True)
class ControlSpec:
    """Describe an ``Annotated[Input[T], ControlSpec(...)]`` experiment input.

    Names and defaults come from the function signature. Quantity defaults can
    supply the unit; required quantities must declare it explicitly here.
    The framework derives the existing ControlSet and injects a symbolic input
    or scan coordinate into the experiment body.
    """

    unit: str | None = None
    minimum: float | None = None
    maximum: float | None = None
    title: str = ""
    group: str = ""
    scannable: bool = False
