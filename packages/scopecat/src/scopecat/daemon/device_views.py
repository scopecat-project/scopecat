"""Retained connection checks and device status for application clients."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from scopecat.records.device import (
    DeviceConnectionRevision,
    DeviceRevisionRef,
    RegisteredDevice,
)
from scopecat.sdk.instruments.contracts import InstrumentDescription


class _DeviceAccessModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DeviceConnectionTest(_DeviceAccessModel):
    """Observed connection result for one exact revision; never a readiness promise."""

    operation_id: str
    revision: DeviceRevisionRef
    actor: str
    recorded_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    description: InstrumentDescription | None = None
    error: str | None = None


class DeviceView(_DeviceAccessModel):
    device: RegisteredDevice
    revision: DeviceConnectionRevision
    availability: Literal["idle", "active", "quarantined"] = "idle"
    owner_kind: Literal["run", "instrument_session"] | None = None
    owner_id: str | None = None
    last_connection_test: DeviceConnectionTest | None = None

    @model_validator(mode="after")
    def validate_head(self) -> DeviceView:
        if self.device.head != self.revision.ref:
            raise ValueError("device head does not match connection revision")
        return self
