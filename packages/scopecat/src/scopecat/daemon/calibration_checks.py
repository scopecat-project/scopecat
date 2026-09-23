"""Domain query contract for admitted calibration check declarations."""

from pydantic import BaseModel, ConfigDict, Field

from scopecat.automation import ProcedureRun
from scopecat.automation.calibration import CheckEvidence
from scopecat.records.calibration_check import (
    CalibrationCheckRequest,
    CalibrationContext,
    CalibrationScope,
)


class CalibrationCheckQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    scope: CalibrationScope | None = None
    context: CalibrationContext | None = None
    limit: int = Field(default=50, ge=1, le=200)
    cursor: int | None = Field(default=None, ge=1)


class CalibrationCheckView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    execution: ProcedureRun
    request: CalibrationCheckRequest
    evidence: CheckEvidence | None


class CalibrationCheckPage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    items: tuple[CalibrationCheckView, ...]
    next_cursor: int | None = None
