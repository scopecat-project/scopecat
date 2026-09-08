"""Declared numeric controls on existing immutable inputs and grid axes."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Literal

from scopecat.kernel.quantity import Quantity
from scopecat.kernel.value_types import Float, Scalar
from scopecat.kernel.value_types import Quantity as QuantityType
from scopecat.kernel.value_validation import coerce_literal
from scopecat.program.control_contract import ControlValidationContext
from scopecat.program.scans import AxisSpec, GridSpec, RangeScanSource, ValuesScanSource
from scopecat.program.value_refs import CoordinateRef
from scopecat.program.values import coordinate

if TYPE_CHECKING:
    from scopecat.program.definitions import ExperimentInvocation
    from scopecat.records.config import ConfigProfileSnapshot


type ControlScalar = float | Quantity
type ControlOwnership = Literal["editable", "derived", "configuration"]


@dataclass(frozen=True, slots=True)
class Control:
    """One numeric field; bounds use its declared unit, where present."""

    id: str
    default: ControlScalar | None = None
    unit: str | None = None
    minimum: float | None = None
    maximum: float | None = None
    title: str = ""
    group: str = ""
    scannable: bool = False
    ownership: ControlOwnership = "editable"
    provenance: str = "Project default"
    resolve: Callable[[ControlValidationContext], ControlScalar] | None = None

    def __post_init__(self) -> None:
        if self.unit is None and isinstance(self.default, Quantity):
            object.__setattr__(self, "unit", self.default.unit)
        if self.default is not None:
            object.__setattr__(self, "default", self.normalize(self.default))
        if self.ownership == "editable" and self.default is None:
            raise ValueError(f"editable control {self.id!r} needs a scalar default")
        if self.ownership != "editable" and (self.resolve is None or self.scannable):
            raise ValueError("owned controls need a resolver and cannot be scanned")

    @property
    def value_type(self) -> Scalar:
        return Scalar(Float() if self.unit is None else QuantityType(unit=self.unit))

    @property
    def ref(self) -> CoordinateRef[object]:
        """The single point-coordinate source of a scannable control."""
        if not self.scannable:
            raise TypeError(f"control {self.id!r} is not a scan coordinate")
        return coordinate(self.id, self.value_type)

    def normalize(self, value: object) -> ControlScalar:
        normalized = coerce_literal(self.value_type, value)
        assert isinstance(normalized, float | Quantity)
        magnitude = normalized.value if isinstance(normalized, Quantity) else normalized
        if self.minimum is not None and magnitude < self.minimum:
            raise ValueError(
                f"{self.id} must be at least {self.minimum} {self.unit or ''}"
            )
        if self.maximum is not None and magnitude > self.maximum:
            raise ValueError(
                f"{self.id} must be at most {self.maximum} {self.unit or ''}"
            )
        return normalized

    def fixed_axis(self, value: object) -> AxisSpec:
        return AxisSpec(
            id=self.id,
            value_type=self.value_type,
            source=ValuesScanSource((self.normalize(value),)),
            mode="fixed",
        )

    def axis_values(self, axis: AxisSpec) -> tuple[ControlScalar, ...]:
        """Validate explicit values or range endpoints, never a Cartesian product."""
        if axis.value_type != self.value_type or axis.overlay is not None:
            raise ValueError(
                f"{self.id} axis must retain its declared type and ownership"
            )
        if isinstance(axis.source, ValuesScanSource):
            values = tuple(self.normalize(value) for value in axis.source.values)
        elif isinstance(axis.source, RangeScanSource):
            values = (
                self.normalize(axis.source.start),
                self.normalize(axis.source.stop),
            )
        else:
            raise ValueError("declared controls support explicit values or range axes")
        if not values:
            raise ValueError(f"{self.id} axis cannot be empty")
        if axis.mode == "fixed" and (
            not isinstance(axis.source, ValuesScanSource) or len(values) != 1
        ):
            raise ValueError("a fixed control must have exactly one scalar source")
        return values


@dataclass(frozen=True, slots=True)
class ControlSet:
    """One maintained declaration shared by notebook edits and launch consumers."""

    fields: tuple[Control, ...]
    validator: Callable[[ControlValidationContext], None] | None = None

    def __post_init__(self) -> None:
        if len({field.id for field in self.fields}) != len(self.fields):
            raise ValueError("control ids must be unique")

    def default_axes(self) -> tuple[AxisSpec, ...]:
        return tuple(
            field.fixed_axis(field.default) for field in self.fields if field.scannable
        )

    def validate(self, context: ControlValidationContext) -> None:
        invocation = context.invocation
        if not isinstance(invocation.point_plan.domain, GridSpec):
            raise ValueError(
                "declared controls require an existing Cartesian grid plan"
            )
        axes = {axis.id: axis for axis in context.axes}
        for field in self.fields:
            if field.ownership != "editable":
                if field.id in axes or field.id in invocation.input_overrides:
                    raise ValueError(f"{field.id} is {field.ownership}-owned")
                continue
            if field.scannable:
                if field.id in invocation.input_overrides:
                    raise ValueError(
                        f"{field.id} has an axis source, not an input override"
                    )
                if field.id not in axes:
                    raise ValueError(f"{field.id} needs a scalar/default or scan axis")
                field.axis_values(axes[field.id])
            else:
                if field.id in axes:
                    raise ValueError(f"{field.id} is not scannable")
                field.normalize(invocation.input_overrides.get(field.id, field.default))
        if self.validator is not None:
            self.validator(context)

    def apply[ResultT](
        self,
        invocation: ExperimentInvocation[ResultT],
        *,
        config: ConfigProfileSnapshot,
        edits: Mapping[str, ControlScalar | AxisSpec] | None = None,
        reset: tuple[str, ...] = (),
    ) -> ExperimentInvocation[ResultT]:
        """Replace each selected source, then validate the complete edited plan."""
        if invocation.definition.controls is not self:
            raise ValueError("controls must be declared on this experiment")
        fields = {field.id: field for field in self.fields}
        selected: dict[str, ControlScalar | AxisSpec | None] = dict(edits or {})
        for name in reset:
            if name not in fields:
                raise ValueError(f"unknown control {name!r}")
            selected[name] = fields[name].default
        edited = invocation
        for name, value in selected.items():
            if name not in fields:
                raise ValueError(f"unknown control {name!r}")
            field = fields[name]
            if field.ownership != "editable":
                raise ValueError(f"{name} is {field.ownership}-owned")
            if isinstance(value, AxisSpec):
                if not field.scannable or value.id != field.id:
                    raise ValueError(f"{name} does not accept this axis")
                field.axis_values(value)
                edited = edited.with_axis(replace(value, mode="scan"))
            elif field.scannable:
                edited = edited.with_axis(field.fixed_axis(value))
            else:
                edited = edited.bind(**{name: field.normalize(value)})
        self.validate(ControlValidationContext(edited, config))
        return edited
