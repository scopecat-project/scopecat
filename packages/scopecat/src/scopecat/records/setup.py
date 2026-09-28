"""Immutable executable setup revisions, separate from parameter ownership."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from scopecat.kernel.content_identity import sha256_json_hash
from scopecat.records.config import (
    ConfigProfileSnapshot,
    DomainTargetBinding,
    InstrumentFailureAction,
    InstrumentRegistry,
    InstrumentRunStartPolicy,
    InstrumentSpec,
    InstrumentSuccessAction,
    RoutingGraph,
    SystemSpec,
    Topology,
)
from scopecat.records.content import Sha256ContentHash
from scopecat.records.device import (
    DeviceConnectionRevision,
    DeviceRevisionRef,
    device_resource_key,
)
from scopecat.records.execution_scenario import SoftwareExecutionScenario
from scopecat.records.instrument import InstrumentStateSetting
from scopecat.records.parameter import ParameterCatalog


class _SetupModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SetupInstrumentBinding(_SetupModel):
    """Logical experiment identity and policy; connections belong to devices."""

    id: str = Field(min_length=1)
    device_id: str = Field(min_length=1)
    default_state: tuple[InstrumentStateSetting, ...] = ()
    run_start: InstrumentRunStartPolicy
    success_action: InstrumentSuccessAction
    failure_action: InstrumentFailureAction

    def resolve(self, device: DeviceConnectionRevision) -> InstrumentSpec:
        if device.device_id != self.device_id:
            raise ValueError("setup binding references another device")
        safety = device.content.safety
        if safety.require_safe_success and self.success_action != "apply_safe_state":
            raise ValueError(
                f"{self.id}: device requires safe-state success finalization"
            )
        if (
            safety.require_safe_failure
            and self.failure_action != "abort_then_safe_state"
        ):
            raise ValueError(
                f"{self.id}: device requires safe-state failure finalization"
            )
        return InstrumentSpec(
            id=self.id,
            exclusivity_key=device_resource_key(self.device_id),
            driver_id=device.content.driver.driver_id,
            connection=device.content.connection,
            default_state=list(self.default_state),
            run_start=self.run_start,
            success_action=self.success_action,
            failure_action=self.failure_action,
            safe_state=list(safety.safe_state),
            safe_operations=list(safety.safe_operations),
            safe_state_requirement=safety.safe_state_requirement,
        )


class SetupDefinition(_SetupModel):
    topology: Topology
    instruments: tuple[SetupInstrumentBinding, ...]
    routing: RoutingGraph
    domain_target: DomainTargetBinding | None
    scenario: SoftwareExecutionScenario | None = None

    @model_validator(mode="after")
    def validate_bindings(self) -> SetupDefinition:
        if len({item.id for item in self.instruments}) != len(self.instruments):
            raise ValueError("setup instrument aliases must be unique")
        if len({item.device_id for item in self.instruments}) != len(self.instruments):
            raise ValueError(
                "bind each device once; assign multiple roles through routing"
            )
        return self

    @property
    def content_hash(self) -> Sha256ContentHash:
        return sha256_json_hash(
            {
                "codec": "scopecat.setup-definition.v1",
                "value": self.model_dump(mode="json"),
            }
        )

    def resolve(
        self, devices: dict[str, DeviceConnectionRevision]
    ) -> ExecutableSetupSnapshot:
        return ExecutableSetupSnapshot(
            topology=self.topology,
            instrument_registry=InstrumentRegistry(
                instruments=[
                    item.resolve(devices[item.device_id]) for item in self.instruments
                ]
            ),
            routing=self.routing,
            domain_target=self.domain_target,
            scenario=self.scenario,
        )


class SetupDefinitionRevision(_SetupModel):
    id: str = Field(min_length=1)
    definition: SetupDefinition
    purpose: Literal["experiment", "device_access"] = "experiment"
    actor: str = Field(min_length=1)
    note: str = ""
    recorded_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class SetupDeviceResolution(_SetupModel):
    definition_id: str = Field(min_length=1)
    definition_hash: Sha256ContentHash
    devices: tuple[DeviceRevisionRef, ...]


class ExecutableSetupSnapshot(_SetupModel):
    topology: Topology
    instrument_registry: InstrumentRegistry
    routing: RoutingGraph
    domain_target: DomainTargetBinding | None
    scenario: SoftwareExecutionScenario | None = None

    @model_validator(mode="after")
    def validate_structure(self) -> ExecutableSetupSnapshot:
        SystemSpec(
            id="setup-validation",
            topology=self.topology,
            instrument_registry=self.instrument_registry,
            routing=self.routing,
            domain_target=self.domain_target,
            scenario=self.scenario,
            parameter_catalog=ParameterCatalog(id="setup-validation"),
        )
        return self

    @classmethod
    def from_config(cls, config: ConfigProfileSnapshot) -> ExecutableSetupSnapshot:
        return cls.model_validate(
            config.system.model_dump(include=set(cls.model_fields))
        )

    def compose(self, base: ConfigProfileSnapshot) -> ConfigProfileSnapshot:
        """Explicitly replace executable fields, retaining the supplied parameters."""
        system = base.system.model_dump()
        system.update(self.model_dump())
        return ConfigProfileSnapshot(
            id=base.id,
            system=SystemSpec.model_validate(system),
            parameter_snapshot=base.parameter_snapshot,
        )

    @property
    def content_hash(self) -> Sha256ContentHash:
        """Exact payload identity, including descriptive metadata."""
        return sha256_json_hash(
            {
                "codec": "scopecat.setup-revision.v3",
                "setup": self.model_dump(mode="json"),
            }
        )

    @property
    def execution_content_hash(self) -> Sha256ContentHash:
        """Execution identity preserves the existing scientific-scope codec."""
        return executable_setup_content_hash(self)


def executable_setup_content_hash(
    setup: ExecutableSetupSnapshot | ConfigProfileSnapshot,
) -> Sha256ContentHash:
    """Canonical executable projection shared by payloads and complete snapshots."""
    source = setup.system if isinstance(setup, ConfigProfileSnapshot) else setup
    system = source.model_dump(
        mode="json",
        exclude={
            "id": True,
            "parameter_catalog": True,
            "topology": {"entities": {"__all__": {"metadata"}}},
            "routing": {"roles": {"__all__": {"description"}}},
        },
    )
    return sha256_json_hash({"codec": "scopecat.setup-content.v3", "system": system})


class SetupRevisionRef(_SetupModel):
    revision_id: str = Field(min_length=1)
    content_hash: Sha256ContentHash


class SetupRevision(_SetupModel):
    id: str = Field(min_length=1)
    content_hash: Sha256ContentHash
    setup: ExecutableSetupSnapshot
    resolution: SetupDeviceResolution
    actor: str = Field(min_length=1)
    note: str = ""
    recorded_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def validate_content(self) -> SetupRevision:
        if self.content_hash != resolved_setup_hash(self.setup, self.resolution):
            raise ValueError("setup revision content hash does not match payload")
        expected = {
            device_resource_key(item.device_id) for item in self.resolution.devices
        }
        actual = {
            item.exclusivity_key for item in self.setup.instrument_registry.instruments
        }
        if expected != actual or len(expected) != len(self.resolution.devices):
            raise ValueError(
                "resolved setup must retain exactly one revision for each bound device"
            )
        return self

    @property
    def ref(self) -> SetupRevisionRef:
        return SetupRevisionRef(revision_id=self.id, content_hash=self.content_hash)


def resolved_setup_hash(
    setup: ExecutableSetupSnapshot, resolution: SetupDeviceResolution
) -> Sha256ContentHash:
    return sha256_json_hash(
        {
            "codec": "scopecat.resolved-setup.v1",
            "setup": setup.model_dump(mode="json"),
            "resolution": resolution.model_dump(mode="json"),
        }
    )


class SetupActivationRecord(_SetupModel):
    generation: int = Field(ge=1)
    revision: SetupRevisionRef
    previous_revision: SetupRevisionRef | None = None
    actor: str = Field(min_length=1)
    note: str = ""
    recorded_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ActiveSetupView(_SetupModel):
    revision: SetupRevision
    activation: SetupActivationRecord

    @model_validator(mode="after")
    def validate_revision(self) -> ActiveSetupView:
        if self.revision.ref != self.activation.revision:
            raise ValueError("active setup revision does not match activation")
        return self


class SetupActivationOperation(_SetupModel):
    operation_id: str = Field(min_length=1)
    intent_hash: Sha256ContentHash
    revision: SetupRevisionRef
    expected_generation: int = Field(ge=0)
    actor: str = Field(min_length=1)
    note: str = ""
    result: ActiveSetupView
