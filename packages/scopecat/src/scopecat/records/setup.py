"""Immutable executable setup revisions, separate from parameter ownership."""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from scopecat.kernel.content_identity import sha256_json_hash
from scopecat.records.config import (
    ConfigProfileSnapshot,
    DomainTargetBinding,
    InstrumentRegistry,
    RoutingGraph,
    SystemSpec,
    Topology,
)
from scopecat.records.content import Sha256ContentHash
from scopecat.records.parameter import ParameterCatalog


class _SetupModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ExecutableSetupSnapshot(_SetupModel):
    primary_entity_id: str
    topology: Topology
    instrument_registry: InstrumentRegistry
    routing: RoutingGraph
    domain_target: DomainTargetBinding | None

    @model_validator(mode="after")
    def validate_structure(self) -> ExecutableSetupSnapshot:
        SystemSpec(
            id="setup-validation",
            primary_entity_id=self.primary_entity_id,
            topology=self.topology,
            instrument_registry=self.instrument_registry,
            routing=self.routing,
            domain_target=self.domain_target,
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
                "codec": "scopecat.setup-revision.v1",
                "setup": self.model_dump(mode="json"),
            }
        )

    @property
    def execution_content_hash(self) -> Sha256ContentHash:
        """Execution identity preserves the existing scientific-scope codec."""
        system = self.model_dump(
            mode="json",
            exclude={
                "topology": {"entities": {"__all__": {"metadata"}}},
                "routing": {"roles": {"__all__": {"description"}}},
            },
        )
        return sha256_json_hash(
            {"codec": "scopecat.setup-content.v1", "system": system}
        )


class SetupRevisionRef(_SetupModel):
    revision_id: str = Field(min_length=1)
    content_hash: Sha256ContentHash


class SetupRevision(_SetupModel):
    id: str = Field(min_length=1)
    content_hash: Sha256ContentHash
    setup: ExecutableSetupSnapshot
    actor: str = Field(min_length=1)
    note: str = ""
    recorded_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def validate_content(self) -> SetupRevision:
        if self.content_hash != self.setup.content_hash:
            raise ValueError("setup revision content hash does not match payload")
        return self

    @property
    def ref(self) -> SetupRevisionRef:
        return SetupRevisionRef(revision_id=self.id, content_hash=self.content_hash)


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
