"""Read-only planning context for one project's declared control validation."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from scopecat.program.scans import AxisSpec, PointPlan
from scopecat.program.values import RuntimeInput

if TYPE_CHECKING:
    from scopecat.records.config import ConfigProfileSnapshot


class ControlInvocation(Protocol):
    @property
    def point_plan(self) -> PointPlan: ...

    @property
    def input_overrides(self) -> Mapping[str, RuntimeInput]: ...


@dataclass(frozen=True, slots=True)
class ControlValidationContext:
    """Actual edited invocation and the immutable configuration used to plan it."""

    invocation: ControlInvocation
    config: ConfigProfileSnapshot

    @property
    def axes(self) -> tuple[AxisSpec, ...]:
        return self.invocation.point_plan.domain.axes

    def axis(self, id: str) -> AxisSpec:
        return next(axis for axis in self.axes if axis.id == id)


class InvocationControls(Protocol):
    def validate(self, context: ControlValidationContext) -> None: ...
