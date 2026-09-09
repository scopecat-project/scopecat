"""Durable scalar/axis edit using the existing axis source protocol."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, StrictFloat, model_validator

from scopecat.kernel.quantity import Quantity
from scopecat.records.run_request import AxisSourceRecord


class ControlEdit(BaseModel):
    """Choose one existing scalar or axis source; no hidden inactive value."""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

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
