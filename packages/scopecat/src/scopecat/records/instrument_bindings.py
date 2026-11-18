"""Shared, non-executing progressive instrument binding contracts."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from scopecat.records.author_revision import AuthorRevisionRef
from scopecat.records.config import (
    InstrumentFailureAction,
    InstrumentRunStartPolicy,
    InstrumentSuccessAction,
)
from scopecat.records.execution_scenario import SoftwareExecutionScenario
from scopecat.records.setup import SetupDefinition


class _BindingModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class BindingDiagnostic(_BindingModel):
    code: str
    message: str
    port_id: str | None = None
    device_id: str | None = None


class InstrumentRequirement(_BindingModel):
    port_id: str
    interface_id: str
    role_id: str | None = None


class BindingRequirements(_BindingModel):
    kind: Literal["binding_requirements"] = "binding_requirements"
    workspace_id: str
    code_revision: AuthorRevisionRef | None = None
    experiment_id: str
    version: str
    inputs_hash: str
    supported: bool
    requirements: tuple[InstrumentRequirement, ...] = ()
    diagnostics: tuple[BindingDiagnostic, ...] = ()


class BindingCandidate(_BindingModel):
    port_id: str
    device_id: str
    device_label: str
    connection_revision_id: str
    interface_id: str
    component_path: tuple[str, ...] = ()

    def select(
        self,
        *,
        run_start: InstrumentRunStartPolicy,
        success_action: InstrumentSuccessAction,
        failure_action: InstrumentFailureAction,
    ) -> InstrumentBindingChoice:
        """Copy this explicit endpoint choice; lifecycle policies remain explicit."""
        return InstrumentBindingChoice(
            port_id=self.port_id,
            device_id=self.device_id,
            connection_revision_id=self.connection_revision_id,
            component_path=self.component_path,
            run_start=run_start,
            success_action=success_action,
            failure_action=failure_action,
        )


class BindingOptions(_BindingModel):
    requirements: BindingRequirements
    candidates: tuple[BindingCandidate, ...] = ()
    diagnostics: tuple[BindingDiagnostic, ...] = ()


class InstrumentBindingChoice(_BindingModel):
    port_id: str
    device_id: str
    connection_revision_id: str
    component_path: tuple[str, ...] = ()
    run_start: InstrumentRunStartPolicy
    success_action: InstrumentSuccessAction
    failure_action: InstrumentFailureAction


class InstrumentBindingPlan(_BindingModel):
    requirements: BindingRequirements
    choices: tuple[InstrumentBindingChoice, ...] = Field(default=(), max_length=256)
    scenario: SoftwareExecutionScenario | None = None


class BindingCheck(_BindingModel):
    definition: SetupDefinition | None = None
    diagnostics: tuple[BindingDiagnostic, ...] = ()
