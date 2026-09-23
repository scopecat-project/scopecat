"""Domain query contract for admitted calibration check declarations."""

from pydantic import BaseModel, ConfigDict, Field

from scopecat.automation import ProcedureRun
from scopecat.records.calibration_check import CalibrationContext, CalibrationScope


class CalibrationCheckQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    scope: CalibrationScope | None = None
    context: CalibrationContext | None = None
    limit: int = Field(default=50, ge=1, le=200)
    cursor: int | None = Field(default=None, ge=1)


class CalibrationCheckPage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    items: tuple[ProcedureRun, ...]
    next_cursor: int | None = None
