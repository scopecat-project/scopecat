"""Domain query contract for admitted calibration check declarations."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from scopecat.automation import ProcedureRun
from scopecat.automation.calibration import CheckEvidence
from scopecat.records.calibration_check import (
    CalibrationCheckRequest,
    CalibrationContext,
    CalibrationScope,
)

MAX_CHECK_OBSERVATIONS = 2000


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


class CalibrationCheckObservation(BaseModel):
    """Previously read execution revisions and the first filtered request."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    scope: CalibrationScope | None = None
    context: CalibrationContext | None = None
    head: str | None = Field(default=None, min_length=1)
    revisions: dict[
        Annotated[str, Field(min_length=1)], Annotated[int, Field(ge=1)]
    ] = Field(
        max_length=MAX_CHECK_OBSERVATIONS,
    )


class CalibrationCheckObservationResult(BaseModel):
    """Comparison at one read snapshot, not permission for a later write."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    changed_procedures: tuple[str, ...]
    head_changed: bool
