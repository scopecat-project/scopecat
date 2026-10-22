"""Finite physical-line lists and their shared measurement template, not progress."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from scopecat.records.experiment_plan import ExperimentPlanDefinition
from scopecat.records.manual_line import PhysicalLine


class ManualLineScanCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)
    scan_id: str = Field(pattern=r"^lines_[0-9a-f]{32}$")
    saved_by: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=200)
    lines: tuple[PhysicalLine, ...] = Field(min_length=1, max_length=1000)
    template: ExperimentPlanDefinition

    @model_validator(mode="after")
    def unique_lines(self) -> ManualLineScanCreate:
        if len({line.line_id for line in self.lines}) != len(self.lines):
            raise ValueError("physical lines require distinct stable IDs")
        return self


class ManualLineScan(ManualLineScanCreate):
    revision: int = Field(ge=1)
    saved_at: datetime


class ManualLineLabels(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)
    expected_revision: int = Field(ge=1)
    saved_by: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=200)
    lines: tuple[PhysicalLine, ...] = Field(min_length=1, max_length=1000)
