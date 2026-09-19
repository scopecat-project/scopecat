"""Pure target evidence checks; projection alone does not authorize execution."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from scopecat.kernel.entity import entity_identity
from scopecat.records.config import Topology
from scopecat.records.sample import SampleRevision
from scopecat.records.scientific_scope import (
    MeasurementTarget,
    TargetEntity,
    TargetMember,
)
from scopecat.records.target_catalog import TargetRevision, TargetRevisionRef

type RetainedSampleRevisions = Mapping[tuple[str, int], SampleRevision]


def validate_target_members(
    target: MeasurementTarget, samples: RetainedSampleRevisions
) -> None:
    """Check semantics after the owner loads every referenced local revision.

    The mapping is keyed by (sample_id, revision). Loading and missing-record
    errors belong to the catalog; this function performs no I/O or head lookup.
    """
    entities: dict[str, set[str]] = {}
    for member in target.members:
        revision = samples[member.sample_id, member.revision]
        if revision.content_hash != member.content_hash:
            raise ValueError(f"target member {member.id!r} sample hash changed")
        topology = revision.content.topology
        entities[member.id] = (
            {entity.id for entity in topology.entities}
            if topology is not None
            else set()
        )
    for link in target.connections:
        for endpoint in link.endpoints:
            if endpoint.entity_id not in entities[endpoint.member_id]:
                raise ValueError(
                    f"target connection {link.id!r} references unknown entity "
                    f"{endpoint.member_id}/{endpoint.entity_id}"
                )


@dataclass(frozen=True, slots=True)
class TargetEntityProjection:
    target_entity: TargetEntity
    runtime_entity_id: str


@dataclass(frozen=True, slots=True)
class SingleMemberTargetProjection:
    """Exact provenance and a deterministic single-subject runtime mapping.

    The target reference is provenance, including its catalog and revision.
    Scientific content identity uses its content hash rather than a display label.
    Neither this result nor topology equality qualifies batch/setup/calibration.
    """

    target: TargetRevisionRef
    member: TargetMember
    entities: tuple[TargetEntityProjection, ...]
    run_role: Literal["subject"] = "subject"


def project_single_member_target(
    target: TargetRevision,
    *,
    catalog_id: str,
    samples: RetainedSampleRevisions,
    execution_topology: Topology,
) -> SingleMemberTargetProjection:
    """Check the narrow projection contract for a future execution resolver.

    Callers supply local immutable evidence, never sample/target heads. Registered
    assemblies remain valid catalog entries but do not have this projection.
    """
    if target.ref.catalog_id != catalog_id:
        raise ValueError("target reference belongs to another catalog")
    content = target.content
    if len(content.members) != 1 or content.connections:
        raise ValueError(
            "single-member projection requires one member and no target connections"
        )
    validate_target_members(content, samples)
    member = content.members[0]
    topology = samples[member.sample_id, member.revision].content.topology
    if topology is None:
        raise ValueError("single-member projection requires a declared sample topology")
    if _topology_identity(topology) != _topology_identity(execution_topology):
        raise ValueError("execution topology does not match the target sample topology")
    return SingleMemberTargetProjection(
        target=target.ref,
        member=member,
        entities=tuple(
            TargetEntityProjection(
                target_entity=TargetEntity(member_id=member.id, entity_id=entity.id),
                runtime_entity_id=entity.id,
            )
            for entity in sorted(topology.entities, key=lambda entity: entity.id)
        ),
    )


def _topology_identity(
    topology: Topology,
) -> tuple[
    frozenset[tuple[str | None, str]],
    frozenset[tuple[str, str, tuple[str, ...], str | None]],
]:
    return (
        frozenset(entity_identity(entity) for entity in topology.entities),
        frozenset(
            (
                connection.id,
                connection.kind,
                tuple(sorted(connection.endpoints)),
                connection.entity_id,
            )
            for connection in topology.connections
        ),
    )
