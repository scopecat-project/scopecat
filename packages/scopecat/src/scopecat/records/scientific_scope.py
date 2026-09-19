"""Exact scientific applicability within one owning catalog.

These value contracts do not allocate target identities or make an assembly
executable. Cross-catalog comparisons require an explicit identity mapping.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from scopecat.kernel.content_identity import sha256_json_hash
from scopecat.records.config import ConfigProfileSnapshot
from scopecat.records.content import Sha256ContentHash
from scopecat.records.experimental_batch import ExperimentalBatchId
from scopecat.records.sample import SampleBinding, SampleId

_NonEmpty = Annotated[str, Field(min_length=1)]


class _ScopeModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TargetMember(_ScopeModel):
    """One physical sample revision in a target-local role."""

    id: _NonEmpty
    sample_id: SampleId
    revision: int = Field(ge=1)
    content_hash: Sha256ContentHash


class TargetEntity(_ScopeModel):
    """An entity address whose member prevents A/q0 and B/q0 collisions."""

    member_id: _NonEmpty
    entity_id: _NonEmpty


class TargetConnection(_ScopeModel):
    """A declared undirected interconnection, not an apparatus route."""

    id: _NonEmpty
    kind: _NonEmpty
    endpoints: tuple[TargetEntity, TargetEntity]

    @model_validator(mode="after")
    def distinct_endpoints(self) -> TargetConnection:
        if self.endpoints[0] == self.endpoints[1]:
            raise ValueError("target connection endpoints must be distinct")
        return self


class MeasurementTarget(_ScopeModel):
    """Exact target content; member IDs and connections carry scientific meaning.

    Ordering is not identity. Entity existence and connection capabilities must
    be checked against resolved member descriptions before executable admission.
    """

    members: tuple[TargetMember, ...] = Field(min_length=1)
    connections: tuple[TargetConnection, ...] = ()

    @model_validator(mode="after")
    def valid_members(self) -> MeasurementTarget:
        members = {member.id for member in self.members}
        if len(members) != len(self.members):
            raise ValueError("target member IDs must be unique")
        if len({item.id for item in self.connections}) != len(self.connections):
            raise ValueError("target connection IDs must be unique")
        if any(
            endpoint.member_id not in members
            for connection in self.connections
            for endpoint in connection.endpoints
        ):
            raise ValueError("target connection references an unknown member")
        return self

    @property
    def content_hash(self) -> Sha256ContentHash:
        return sha256_json_hash(
            {
                "codec": "scopecat.measurement-target.v1",
                "members": [
                    member.model_dump(mode="json")
                    for member in sorted(self.members, key=lambda member: member.id)
                ],
                "connections": [
                    {
                        "id": connection.id,
                        "kind": connection.kind,
                        "endpoints": [
                            endpoint.model_dump(mode="json")
                            for endpoint in sorted(
                                connection.endpoints,
                                key=lambda endpoint: (
                                    endpoint.member_id,
                                    endpoint.entity_id,
                                ),
                            )
                        ],
                    }
                    for connection in sorted(self.connections, key=lambda item: item.id)
                ],
            }
        )


class UnscopedBatch(_ScopeModel):
    """No event was declared; this is not a wildcard for a declared batch."""

    kind: Literal["unscoped"] = "unscoped"


class DeclaredBatch(_ScopeModel):
    kind: Literal["declared"] = "declared"
    id: ExperimentalBatchId


type BatchScope = Annotated[UnscopedBatch | DeclaredBatch, Field(discriminator="kind")]


class ScientificApplicability(_ScopeModel):
    """Frozen conditions for strict same-scope reuse inside one catalog.

    Setup is the reviewed execution-structure content, not a promise that the
    physical apparatus actually has that state. Working-point values and names
    are intentionally absent. Copying estimates is a separate operation.
    """

    target: MeasurementTarget
    batch: BatchScope
    setup_content_hash: Sha256ContentHash

    def require_same(self, other: ScientificApplicability) -> None:
        if self.target.content_hash != other.target.content_hash:
            raise ValueError("reuse requires the same exact sample revision and target")
        if self.batch != other.batch:
            raise ValueError("reuse requires the same declared batch scope")
        if self.setup_content_hash != other.setup_content_hash:
            raise ValueError(
                "reuse requires the same setup; explicitly copy estimates into a "
                "new working point instead"
            )


def single_sample_applicability(
    sample: SampleBinding, config: ConfigProfileSnapshot
) -> ScientificApplicability:
    """Project current records without rewriting their serialized content/hashes."""
    return ScientificApplicability(
        target=MeasurementTarget(
            members=(
                TargetMember(
                    id=sample.role,
                    sample_id=sample.sample_id,
                    revision=sample.revision,
                    content_hash=sample.content_hash,
                ),
            )
        ),
        batch=(
            UnscopedBatch()
            if sample.batch_id is None
            else DeclaredBatch(id=sample.batch_id)
        ),
        setup_content_hash=setup_content_hash(config),
    )


def setup_content_hash(config: ConfigProfileSnapshot) -> Sha256ContentHash:
    """Conservative execution-structure identity for the current config format.

    Exclude profile/system labels, entity metadata, role descriptions, parameter
    declarations and values. Keep topology, routing, physical connection/driver
    settings, lifecycle policy and domain configuration. Logical IDs still matter
    because current plans address them; this is not physical-device arbitration.
    """
    system = config.system.model_dump(
        mode="json",
        exclude={
            "id": True,
            "parameter_catalog": True,
            "topology": {"entities": {"__all__": {"metadata"}}},
            "routing": {"roles": {"__all__": {"description"}}},
        },
    )
    return sha256_json_hash({"codec": "scopecat.setup-content.v1", "system": system})
