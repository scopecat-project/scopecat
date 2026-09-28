"""Application device ownership, independent of experiment aliases and setups."""

from __future__ import annotations

from datetime import UTC, datetime
from ipaddress import ip_address
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from scopecat.kernel.content_identity import sha256_json_hash
from scopecat.records.config import (
    InstrumentConnection,
    InstrumentSafeOperation,
    InstrumentSafeStateRequirement,
    InstrumentSpec,
    SerialInstrumentConnection,
    TcpipSocketInstrumentConnection,
)
from scopecat.records.content import Sha256ContentHash
from scopecat.records.instrument import InstrumentStateSetting, state_member_identity


class _DeviceModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DriverImplementationRef(_DeviceModel):
    """Identity of the loaded artifact, not merely its advertised version."""

    provider_id: str = Field(min_length=1)
    driver_id: str = Field(min_length=1)
    artifact_hash: Sha256ContentHash


class DeviceSafetyPolicy(_DeviceModel):
    safe_state: tuple[InstrumentStateSetting, ...] = ()
    safe_operations: tuple[InstrumentSafeOperation, ...] = ()
    safe_state_requirement: InstrumentSafeStateRequirement = "best_effort"
    require_safe_success: bool = False
    require_safe_failure: bool = False

    @field_validator("safe_state")
    @classmethod
    def unique_targets(
        cls, values: tuple[InstrumentStateSetting, ...]
    ) -> tuple[InstrumentStateSetting, ...]:
        keys = [state_member_identity(value.target) for value in values]
        if len(keys) != len(set(keys)):
            raise ValueError("device safe-state targets must be unique")
        return values

    @model_validator(mode="after")
    def validate_actions(self) -> DeviceSafetyPolicy:
        if (
            self.require_safe_success
            or self.require_safe_failure
            or self.safe_state_requirement == "required"
        ) and not (self.safe_state or self.safe_operations):
            raise ValueError("device safety requirements need safe-state actions")
        if self.safe_state_requirement == "required" and not (
            self.require_safe_success or self.require_safe_failure
        ):
            raise ValueError(
                "required device safe state must specify "
                "success or failure finalization"
            )
        return self

    @classmethod
    def from_instrument(cls, instrument: InstrumentSpec) -> DeviceSafetyPolicy:
        """Explicit recipe import retains its declared mandatory finalization."""
        required = instrument.safe_state_requirement == "required"
        return cls(
            safe_state=tuple(instrument.safe_state),
            safe_operations=tuple(instrument.safe_operations),
            safe_state_requirement=instrument.safe_state_requirement,
            require_safe_success=required
            and instrument.success_action == "apply_safe_state",
            require_safe_failure=required
            and instrument.failure_action == "abort_then_safe_state",
        )


class DeviceConnection(_DeviceModel):
    driver: DriverImplementationRef
    connection: InstrumentConnection
    safety: DeviceSafetyPolicy = Field(default_factory=DeviceSafetyPolicy)
    access_aliases: tuple[str, ...] = ()

    @property
    def content_hash(self) -> Sha256ContentHash:
        return sha256_json_hash(
            {
                "codec": "scopecat.device-connection.v1",
                "value": self.model_dump(mode="json"),
            }
        )


class DeviceRevisionRef(_DeviceModel):
    device_id: str = Field(min_length=1)
    revision_id: str = Field(min_length=1)
    content_hash: Sha256ContentHash


class DeviceConnectionRevision(_DeviceModel):
    id: str = Field(min_length=1)
    device_id: str = Field(min_length=1)
    content: DeviceConnection
    previous: DeviceRevisionRef | None = None
    actor: str = Field(min_length=1)
    note: str = ""
    recorded_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @property
    def ref(self) -> DeviceRevisionRef:
        return DeviceRevisionRef(
            device_id=self.device_id,
            revision_id=self.id,
            content_hash=self.content.content_hash,
        )


class RegisteredDevice(_DeviceModel):
    id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    state: Literal["available", "retired"] = "available"
    head: DeviceRevisionRef

    @model_validator(mode="after")
    def validate_head(self) -> RegisteredDevice:
        if self.head.device_id != self.id:
            raise ValueError("connection revision belongs to another device")
        return self


def device_resource_key(device_id: str) -> str:
    # ResourceKey already supplies the instrument namespace.
    return device_id


def connection_access_alias(connection: InstrumentConnection) -> str | None:
    """Normalize transport addresses only; never infer identity from a driver name.

    DNS aliases and multiple ports on one instrument need explicit declarations.
    Serial paths are not resolved on a client filesystem.
    """
    if isinstance(connection, TcpipSocketInstrumentConnection):
        host = connection.host.strip().rstrip(".").lower()
        try:
            host = ip_address(host).compressed
        except ValueError:
            host = host.encode("idna").decode("ascii")
        return f"tcp:{host}:{connection.port}"
    if isinstance(connection, SerialInstrumentConnection):
        port = connection.port
        if port.upper().startswith("COM") and port[3:].isdigit():
            port = port.upper()
        return f"serial:{port}"
    return None
