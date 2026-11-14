"""Explicit direct-control finish choices; driver lifecycle remains unchanged."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from scopecat.records.config import InstrumentSafeOperation
from scopecat.records.instrument import InstrumentStateSetting

from ..errors import BackendConflict


class InstrumentFinishAction(BaseModel):
    instrument_id: str
    states: list[InstrumentStateSetting]
    operations: list[InstrumentSafeOperation]


class InstrumentFinishPlan(BaseModel):
    actions: list[InstrumentFinishAction]
    configured_finish_available: bool


class InstrumentFinishCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation_id: str = Field(min_length=1, max_length=200)
    mode: Literal["keep_current", "configured_finish"]


class InstrumentFinishRejection(BaseModel):
    kind: Literal["instrument_finish_rejected"] = "instrument_finish_rejected"
    session_id: str
    operation_id: str
    message: str


class InstrumentFinishRejectionResponse(BaseModel):
    detail: InstrumentFinishRejection


class InstrumentFinishRejected(BackendConflict):
    """A writer-fenced rejection before this session has any finish selection."""

    def __init__(self, session_id: str, operation_id: str, message: str) -> None:
        super().__init__(message)
        self.rejection = InstrumentFinishRejection(
            session_id=session_id, operation_id=operation_id, message=message
        )
