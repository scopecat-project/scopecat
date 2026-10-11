"""Reference-lab interfaces shared across physical device drivers."""

from __future__ import annotations

from typing import Protocol

from scopecat.kernel.quantity import Quantity
from scopecat.sdk.instruments.declarations import (
    Member,
    compile_interface,
    declared_property_ref,
    instrument_interface,
    member,
)


@instrument_interface(
    "reference_lab.clock_timing/v1",
    label="Clock timing",
    description="Lab AWG reference-frequency configuration and lock state.",
)
class ClockTimingInterface(Protocol):
    frequency: Member[Quantity] = member(
        access="read_write",
        unit="Hz",
        minimum=1.0,
        label="Reference frequency",
    )
    locked: Member[bool] = member(
        access="read_only",
        label="Reference locked",
    )


_COMPILED_CLOCK_TIMING = compile_interface(ClockTimingInterface)
CLOCK_TIMING_SPEC = _COMPILED_CLOCK_TIMING.spec
CLOCK_TIMING = _COMPILED_CLOCK_TIMING.ref
CLOCK_TIMING_FREQUENCY = declared_property_ref(ClockTimingInterface, "frequency")
CLOCK_TIMING_LOCKED = declared_property_ref(ClockTimingInterface, "locked")


__all__ = [
    "CLOCK_TIMING",
    "CLOCK_TIMING_FREQUENCY",
    "CLOCK_TIMING_LOCKED",
    "CLOCK_TIMING_SPEC",
    "ClockTimingInterface",
]
