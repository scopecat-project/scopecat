"""Run-scoped instrument programs available to admitted execution adapters."""

from __future__ import annotations

from typing import Annotated, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from scopecat.kernel.interface_identity import InterfaceId
from scopecat.kernel.problems import Problem
from scopecat.records.content import CommandPayload
from scopecat.records.execution import (
    InstrumentFinalizationActionEvidence,
    InstrumentStateActionEvidence,
)
from scopecat.records.instrument import CommandChannelBinding, InstrumentStateSnapshot
from scopecat.records.measurement import (
    InstrumentAcquisitionEvidence,
    MeasurementAcquisitionValue,
)
from scopecat.sdk.instruments.commands import (
    CollectCommand,
    CollectResultRequest,
    InstrumentOperationArgument,
    InstrumentStateAssignment,
    InstrumentStateCommand,
    InvokeCommand,
)


def _exclude_empty(value: object) -> bool:
    return not value


class _HardwareModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RunHardwareApply(_HardwareModel):
    kind: Literal["apply"] = "apply"
    effect_id: str = Field(min_length=1)
    point_index: int | None = Field(default=None, ge=0)
    instrument_id: str = Field(min_length=1)
    assignments: tuple[InstrumentStateAssignment, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_command(self) -> RunHardwareApply:
        InstrumentStateCommand(
            command_id=self.effect_id,
            instrument_id=self.instrument_id,
            assignments=list(self.assignments),
        )
        return self


class RunHardwareInvoke(_HardwareModel):
    kind: Literal["invoke"] = "invoke"
    effect_id: str = Field(min_length=1)
    point_index: int | None = Field(default=None, ge=0)
    instrument_id: str = Field(min_length=1)
    resource_id: str = Field(min_length=1)
    interface_id: InterfaceId
    component_path: tuple[str, ...] = ()
    operation_id: str = Field(min_length=1)
    arguments: tuple[InstrumentOperationArgument, ...] = ()
    payloads: dict[str, CommandPayload] = Field(default_factory=dict)
    entity_ids: tuple[str, ...] = ()
    channel_bindings: tuple[CommandChannelBinding, ...] = ()

    @model_validator(mode="after")
    def validate_command(self) -> RunHardwareInvoke:
        InvokeCommand(
            command_id=self.effect_id,
            instrument_id=self.instrument_id,
            resource_id=self.resource_id,
            interface_id=self.interface_id,
            component_path=list(self.component_path),
            operation_id=self.operation_id,
            arguments=list(self.arguments),
            payloads=self.payloads,
            entity_ids=list(self.entity_ids),
            channel_bindings=list(self.channel_bindings),
        )
        return self


class RunHardwareCollectBinding(_HardwareModel):
    request_id: str = Field(min_length=1)
    value_ids: tuple[str, ...] = ()


class RunHardwareCollect(_HardwareModel):
    kind: Literal["collect"] = "collect"
    effect_id: str = Field(min_length=1)
    point_index: int | None = Field(default=None, ge=0)
    instrument_id: str = Field(min_length=1)
    point_count: int = Field(ge=1)
    requests: tuple[CollectResultRequest, ...] = Field(min_length=1)
    bindings: tuple[RunHardwareCollectBinding, ...]

    @model_validator(mode="after")
    def validate_bindings(self) -> RunHardwareCollect:
        CollectCommand(
            command_id=self.effect_id,
            instrument_id=self.instrument_id,
            point_index=self.point_index,
            point_count=self.point_count,
            requests=list(self.requests),
        )
        request_ids = {request.id for request in self.requests}
        binding_ids = {binding.request_id for binding in self.bindings}
        if request_ids != binding_ids:
            raise ValueError("hardware collect bindings must match requested results")
        return self


type RunHardwareAction = Annotated[
    RunHardwareApply | RunHardwareInvoke | RunHardwareCollect,
    Field(discriminator="kind"),
]


class RunHardwareBatch(_HardwareModel):
    operation_id: str = Field(min_length=1)
    actions: tuple[RunHardwareAction, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_effect_ids(self) -> RunHardwareBatch:
        effect_ids = [action.effect_id for action in self.actions]
        if len(effect_ids) != len(set(effect_ids)):
            raise ValueError("hardware batch effect ids must be unique")
        return self


class RunHardwareValue(_HardwareModel):
    point_index: int | None = Field(default=None, ge=0)
    value_id: str = Field(min_length=1)
    value: MeasurementAcquisitionValue
    evidence: InstrumentAcquisitionEvidence


class RunHardwareStateActionReceipt(InstrumentStateActionEvidence):
    """One state action confirmed by the daemon during a hardware batch."""


class RunHardwareBatchReceipt(_HardwareModel):
    operation_id: str = Field(min_length=1)
    # Ordered prefix acknowledged before a rejection or uncertain action.
    completed_effect_ids: tuple[str, ...] = ()
    values: tuple[RunHardwareValue, ...] = ()
    state_actions: tuple[RunHardwareStateActionReceipt, ...] = Field(
        default=(),
        exclude_if=_exclude_empty,
    )
    problems: tuple[Problem, ...] = ()
    indeterminate: bool = False


class RunHardwareFinalizationActionReceipt(InstrumentFinalizationActionEvidence):
    """One confirmed or rejected action attempted during device finalization."""


class RunHardwareFinalizationReceipt(_HardwareModel):
    """Terminal action, readback, and release evidence for one run host."""

    operation_id: str = Field(min_length=1)
    actions: tuple[RunHardwareFinalizationActionReceipt, ...] = Field(
        default=(),
        exclude_if=_exclude_empty,
    )
    final_state: tuple[InstrumentStateSnapshot, ...] = ()
    problems: tuple[Problem, ...] = ()
    indeterminate: bool = False


class RunInstrumentHost(Protocol):
    """Submit concrete hardware work without exposing daemon-owned drivers."""

    @property
    def ready(self) -> bool: ...

    @property
    def setup_problems(self) -> tuple[Problem, ...]: ...

    @property
    def observed_state(self) -> tuple[InstrumentStateSnapshot, ...]:
        """Return fresh state read after the run acquired exclusive ownership."""
        ...

    @property
    def baseline_state(self) -> tuple[InstrumentStateSnapshot, ...]:
        """Return the execution baseline after applying the run policy."""
        ...

    def execute(self, batch: RunHardwareBatch) -> RunHardwareBatchReceipt: ...

    def finish(
        self,
        *,
        operation_id: str,
        failed: bool,
    ) -> RunHardwareFinalizationReceipt:
        """Abort when failed, read terminal state, and release ownership."""
        ...


__all__ = [
    "RunHardwareAction",
    "RunHardwareApply",
    "RunHardwareBatch",
    "RunHardwareBatchReceipt",
    "RunHardwareCollect",
    "RunHardwareCollectBinding",
    "RunHardwareFinalizationActionReceipt",
    "RunHardwareFinalizationReceipt",
    "RunHardwareInvoke",
    "RunHardwareStateActionReceipt",
    "RunHardwareValue",
    "RunInstrumentHost",
]
