"""Reusable capability policy, independent of report transport and task execution."""

from __future__ import annotations

from datetime import datetime, timedelta
from graphlib import CycleError, TopologicalSorter

from pydantic import BaseModel, ConfigDict, Field, model_validator

from scopecat.records.calibration_check import CalibrationScope


class CalibrationRequirement(BaseModel):
    """One explicitly requested capability; no inferred physical dependencies."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str = Field(min_length=1)
    scope: CalibrationScope
    max_age: timedelta = Field(gt=timedelta(0))
    depends_on: tuple[str, ...] = ()


class CalibrationRequirements(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    requirements: tuple[CalibrationRequirement, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_requirements(self) -> CalibrationRequirements:
        ids = {item.id for item in self.requirements}
        if len(ids) != len(self.requirements):
            raise ValueError("requirement IDs must be unique")
        for item in self.requirements:
            if len(set(item.depends_on)) != len(item.depends_on):
                raise ValueError("requirement dependencies must be unique")
            if not set(item.depends_on) <= ids:
                raise ValueError("requirement dependency names an unknown requirement")
        try:
            tuple(
                TopologicalSorter(
                    {item.id: item.depends_on for item in self.requirements}
                ).static_order()
            )
        except CycleError as error:
            raise ValueError("requirement dependencies must be acyclic") from error
        return self


class CalibrationProfile(CalibrationRequirements):
    """Immutable named requirements, evaluated against a separately chosen context."""

    id: str = Field(
        min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$"
    )
    description: str = Field(default="", max_length=4000)

    def select(
        self, requirement_ids: tuple[str, ...]
    ) -> tuple[CalibrationRequirement, ...]:
        """Include each requested capability's full declared prerequisite closure.

        Preserve profile order for deterministic reports. These are scientific
        prerequisites, not an execution schedule or an automatic repair plan.
        """
        if not requirement_ids or len(set(requirement_ids)) != len(requirement_ids):
            raise ValueError("select nonempty, unique requirement IDs")
        by_id = {item.id: item for item in self.requirements}
        unknown = set(requirement_ids) - by_id.keys()
        if unknown:
            raise ValueError(
                f"unknown profile requirements: {', '.join(sorted(unknown))}"
            )
        selected: set[str] = set()
        pending = list(requirement_ids)
        while pending:
            identity = pending.pop()
            if identity not in selected:
                selected.add(identity)
                pending.extend(by_id[identity].depends_on)
        return tuple(item for item in self.requirements if item.id in selected)


class CalibrationProfileRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    profile: CalibrationProfile
    created_at: datetime
