"""Explicit direct-control finish choices; driver lifecycle remains unchanged."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from scopecat.records.config import InstrumentSafeOperation
from scopecat.records.instrument import InstrumentStateSetting


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
