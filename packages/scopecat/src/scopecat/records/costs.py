"""Measured execution facts; estimates and hardware authority live elsewhere."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class OperationCostMeasurement(BaseModel):
    """Adapter measurements for one physical operation, never inferred estimates.

    Timings may overlap each other and the host's backend-call wall interval.
    Retained bytes is a current live-storage gauge, not transferred traffic.
    None means unavailable, including when a device cannot report a counter.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    source: str = Field(min_length=1)
    transfer_seconds: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    acquire_seconds: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    uploaded_bytes: int | None = Field(default=None, ge=0)
    reused_bytes: int | None = Field(default=None, ge=0)
    rendered_bytes: int | None = Field(default=None, ge=0)
    retained_bytes: int | None = Field(default=None, ge=0)
    unavailable_reason: str = "not measured by this adapter"


class RunOperationCost(BaseModel):
    """One server-observed operation interval and optional adapter subintervals."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    operation_id: str
    instrument_id: str
    operation: Literal["apply", "invoke", "collect", "prepare"]
    wall_seconds: float = Field(ge=0, allow_inf_nan=False)
    wall_source: Literal["server_backend_call"] = "server_backend_call"
    status: Literal["completed", "rejected", "unknown"]
    connection_generation: str
    connection_context: Literal["cold", "warm", "reconnect"]
    measured: OperationCostMeasurement | None = None


class RunCompilationCost(BaseModel):
    """Observed initial planning wall time; excludes lazy target compilation."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    seconds: float = Field(ge=0, allow_inf_nan=False)
    source: Literal["client_initial_planning_wall"] = "client_initial_planning_wall"
    lazy_compilation_seconds: float | None = None
    unavailable_reason: str = "lazy target compilation is not measured separately"


class RunFinalizationCost(BaseModel):
    """Run-host cleanup/readback/release wall interval, excluding terminal commit."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    operation_id: str
    seconds: float = Field(ge=0, allow_inf_nan=False)
    source: Literal["server_hardware_finalization_wall"] = (
        "server_hardware_finalization_wall"
    )


class RunMeasuredCosts(BaseModel):
    """Saved observations; no inferred totals over overlapping intervals."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    operations: tuple[RunOperationCost, ...] = ()
    finalizations: tuple[RunFinalizationCost, ...] = ()
    compilation: RunCompilationCost | None = None
    truncated: bool = False
    terminal_commit_seconds: None = None
    terminal_commit_unavailable_reason: str = (
        "the evidence commit cannot measure its own durable completion"
    )
