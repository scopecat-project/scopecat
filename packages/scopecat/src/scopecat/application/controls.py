"""Typed launch projection of the maintained numeric control declaration."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, StrictFloat, model_validator

from scopecat.authoring.scans import axis
from scopecat.compiler.frontend.scan_lowering import project_axis_record
from scopecat.kernel.quantity import Quantity
from scopecat.program.control_contract import ControlValidationContext
from scopecat.program.controls import ControlScalar, ControlSet
from scopecat.program.definitions import ExperimentInvocation
from scopecat.program.scans import AxisSpec
from scopecat.records.config import ConfigProfileSnapshot
from scopecat.records.run_request import AxisRecord, AxisSourceRecord


class _ControlModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class LaunchControl(_ControlModel):
    id: str
    title: str
    group: str
    default: StrictFloat | Quantity | None
    unit: str | None
    minimum: float | None
    maximum: float | None
    scannable: bool
    ownership: Literal["editable", "derived", "configuration"]
    provenance: str


class ControlEdit(_ControlModel):
    """Choose one existing scalar or axis source; no hidden inactive value."""

    mode: Literal["fixed", "scan", "default"]
    value: StrictFloat | Quantity | None = None
    axis: AxisSourceRecord | None = None

    @model_validator(mode="after")
    def validate_source(self) -> ControlEdit:
        if self.mode == "fixed" and (self.value is None or self.axis is not None):
            raise ValueError("fixed control edit requires only a scalar value")
        if self.mode == "scan" and (self.axis is None or self.value is not None):
            raise ValueError("scan control edit requires only an axis source")
        if self.mode == "default" and (self.axis is not None or self.value is not None):
            raise ValueError("default control edit cannot retain another source")
        return self


class LaunchControlValue(_ControlModel):
    id: str
    state: Literal["fixed", "scanned", "derived", "configuration"]
    value: StrictFloat | Quantity | None = None
    axis: AxisRecord | None = None
    provenance: str


def control_catalog(controls: ControlSet) -> tuple[LaunchControl, ...]:
    return tuple(
        LaunchControl(
            id=field.id,
            title=field.title or field.id,
            group=field.group,
            default=field.default,
            unit=field.unit,
            minimum=field.minimum,
            maximum=field.maximum,
            scannable=field.scannable,
            ownership=field.ownership,
            provenance=field.provenance,
        )
        for field in controls.fields
    )


def edit_controls[ResultT](
    controls: ControlSet,
    invocation: ExperimentInvocation[ResultT],
    *,
    config: ConfigProfileSnapshot,
    edits: dict[str, ControlEdit],
) -> ExperimentInvocation[ResultT]:
    fields = {field.id: field for field in controls.fields}
    values: dict[str, ControlScalar | AxisSpec] = {}
    reset: list[str] = []
    for name, edit in edits.items():
        if name not in fields:
            raise ValueError(f"unknown control {name!r}")
        field = fields[name]
        if field.ownership != "editable":
            raise ValueError(f"{name} is {field.ownership}-owned")
        if edit.mode == "default":
            reset.append(name)
        elif edit.mode == "fixed":
            assert edit.value is not None
            values[name] = edit.value
        else:
            source = edit.axis
            assert source is not None
            if source.kind == "values":
                selected = tuple(field.normalize(value) for value in source.values)
                values[name] = axis(field.ref, selected)
            elif source.kind == "range":
                values[name] = axis(
                    field.ref,
                    start=field.normalize(source.start),
                    stop=field.normalize(source.stop),
                    points=source.points,
                )
            else:
                raise ValueError("control forms support explicit values or range axes")
    return controls.apply(invocation, config=config, edits=values, reset=tuple(reset))


def control_values(
    controls: ControlSet,
    invocation: ExperimentInvocation,
    *,
    config: ConfigProfileSnapshot,
) -> tuple[LaunchControlValue, ...]:
    context = ControlValidationContext(invocation, config)
    controls.validate(context)
    values: list[LaunchControlValue] = []
    for field in controls.fields:
        if field.ownership != "editable":
            assert field.resolve is not None
            values.append(
                LaunchControlValue(
                    id=field.id,
                    state=field.ownership,
                    value=field.normalize(field.resolve(context)),
                    provenance=field.provenance,
                )
            )
        elif field.scannable:
            selected = context.axis(field.id)
            normalized = field.axis_values(selected)
            fixed = selected.mode == "fixed"
            values.append(
                LaunchControlValue(
                    id=field.id,
                    state="fixed" if fixed else "scanned",
                    value=normalized[0] if fixed else None,
                    axis=project_axis_record(selected),
                    provenance=(
                        f"Matches declared default: {field.provenance}"
                        if fixed and normalized[0] == field.default
                        else "Invocation axis edit"
                    ),
                )
            )
        else:
            values.append(
                LaunchControlValue(
                    id=field.id,
                    state="fixed",
                    value=field.normalize(
                        invocation.input_overrides.get(field.id, field.default)
                    ),
                    provenance=(
                        "Invocation input edit"
                        if field.id in invocation.input_overrides
                        else field.provenance
                    ),
                )
            )
    return tuple(values)
