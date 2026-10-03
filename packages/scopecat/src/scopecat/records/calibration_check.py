"""Declared calibration check identity, independent of execution implementation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from scopecat.kernel.frozen import thaw_json_value
from scopecat.records.calibration_dependencies import CalibrationDependencies
from scopecat.records.measurement_context import MeasurementContext
from scopecat.records.setup import SetupRevisionRef


@dataclass(frozen=True)
class CalibrationScope:
    """A capability and ordered target addresses within a resolved subject.

    Conditions and policy versions are explicit laboratory contracts, not
    inferred physical state. Update them when relevant semantics change.
    """

    capability: str
    targets: tuple[str, ...]
    conditions: str
    policy_version: str


class CalibrationCheckRequest(BaseModel):
    """Durable declaration stored as a procedure intent's calibration_check field.

    Execution step addresses locate evidence; they do not define capability
    identity. This initial adapter supports one measurement and one analysis.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")
    codec: Literal["scopecat.calibration-check.v1"] = "scopecat.calibration-check.v1"
    scope: CalibrationScope
    context: MeasurementContext
    setup: SetupRevisionRef
    measurement_step: str = Field(min_length=1)
    analysis_step: str = Field(min_length=1)
    result_output: str = Field(default="check", min_length=1)
    dependencies: CalibrationDependencies | None = None

    @field_validator("scope", "context", "setup", "dependencies", mode="before")
    @classmethod
    def thaw_retained_intent(cls, value: object) -> object:
        return thaw_json_value(value)

    @model_validator(mode="after")
    def distinct_steps(self) -> CalibrationCheckRequest:
        if self.measurement_step == self.analysis_step:
            raise ValueError("check measurement and analysis require distinct steps")
        return self


@dataclass(frozen=True)
class CalibrationCheckResult:
    """Scientific result retained in analysis, separate from execution status."""

    scope: CalibrationScope
    passed: bool
