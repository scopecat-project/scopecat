"""Pure target evidence checks; projection alone does not authorize execution."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from scopecat.kernel.entity import entity_identity
from scopecat.records.config import Topology, TopologyConnection
from scopecat.records.sample import SampleRevision
from scopecat.records.scientific_binding import ConnectionProjection, EntityProjection
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
class TargetProjection:
    """Checked topology mapping only; not a run binding or execution permission."""

    target: TargetRevisionRef
    members: tuple[TargetMember, ...]
    entities: tuple[EntityProjection, ...]
    connections: tuple[ConnectionProjection, ...]


def project_target(
    target: TargetRevision,
    *,
    catalog_id: str,
    samples: RetainedSampleRevisions,
    execution_topology: Topology,
    entities: Mapping[str, Mapping[str, str]],
    connections: Mapping[str, Mapping[str, str]],
    interconnections: Mapping[str, str],
) -> TargetProjection:
    """Validate explicit complete maps from member-local IDs to runtime IDs.

    No names, routes, resource independence or pulse semantics are inferred.
    Every runtime entity and edge must be accounted for exactly once. Target
    interconnections have no declared entity_id, so cannot imply a coupler entity.
    """
    if target.ref.catalog_id != catalog_id:
        raise ValueError("target reference belongs to another catalog")
    validate_target_members(target.content, samples)
    members = tuple(sorted(target.content.members, key=lambda member: member.id))
    member_ids = {member.id for member in members}
    if set(entities) != member_ids or set(connections) != member_ids:
        raise ValueError(
            "entity and connection maps must cover exactly the target members"
        )
    entity_rows: list[EntityProjection] = []
    connection_rows: list[ConnectionProjection] = []
    expected_entities: set[tuple[str | None, str]] = set()
    expected_edges: list[TopologyConnection] = []
    for member in members:
        topology = samples[member.sample_id, member.revision].content.topology
        if topology is None:
            raise ValueError("target projection requires declared sample topologies")
        names = entities[member.id]
        edge_names = connections[member.id]
        if set(names) != {entity.id for entity in topology.entities}:
            raise ValueError(
                f"entity map for {member.id!r} must cover its exact topology"
            )
        if set(edge_names) != {edge.id for edge in topology.connections}:
            raise ValueError(
                f"connection map for {member.id!r} must cover its exact topology"
            )
        for entity in sorted(topology.entities, key=lambda item: item.id):
            entity_rows.append(
                EntityProjection(
                    target_entity=TargetEntity(
                        member_id=member.id, entity_id=entity.id
                    ),
                    runtime_entity_id=names[entity.id],
                )
            )
            expected_entities.add((entity.kind, names[entity.id]))
        for edge in sorted(topology.connections, key=lambda item: item.id):
            connection_rows.append(
                ConnectionProjection(
                    member_id=member.id,
                    connection_id=edge.id,
                    runtime_connection_id=edge_names[edge.id],
                )
            )
            expected_edges.append(
                TopologyConnection(
                    id=edge_names[edge.id],
                    kind=edge.kind,
                    endpoints=(names[edge.endpoints[0]], names[edge.endpoints[1]]),
                    entity_id=None if edge.entity_id is None else names[edge.entity_id],
                )
            )
    if len({row.runtime_entity_id for row in entity_rows}) != len(entity_rows):
        raise ValueError("target entities must map to distinct runtime IDs")
    if set(interconnections) != {edge.id for edge in target.content.connections}:
        raise ValueError(
            "interconnection map must cover exactly the target connections"
        )
    for edge in sorted(target.content.connections, key=lambda item: item.id):
        left, right = edge.endpoints
        connection_rows.append(
            ConnectionProjection(
                member_id=None,
                connection_id=edge.id,
                runtime_connection_id=interconnections[edge.id],
            )
        )
        expected_edges.append(
            TopologyConnection(
                id=interconnections[edge.id],
                kind=edge.kind,
                endpoints=(
                    entities[left.member_id][left.entity_id],
                    entities[right.member_id][right.entity_id],
                ),
            )
        )
    if len({row.runtime_connection_id for row in connection_rows}) != len(
        connection_rows
    ):
        raise ValueError("target connections must map to distinct runtime IDs")
    runtime_entities, runtime_edges = _topology_identity(execution_topology)
    expected_edge_identity = frozenset(
        (edge.id, edge.kind, tuple(sorted(edge.endpoints)), edge.entity_id)
        for edge in expected_edges
    )
    if (
        frozenset(expected_entities) != runtime_entities
        or expected_edge_identity != runtime_edges
    ):
        raise ValueError(
            "execution topology does not match the explicit target mapping"
        )
    return TargetProjection(
        target.ref, members, tuple(entity_rows), tuple(connection_rows)
    )


@dataclass(frozen=True, slots=True)
class SingleMemberTargetProjection:
    """Exact provenance and a deterministic single-subject runtime mapping.

    The target reference is provenance, including its catalog and revision.
    Scientific content identity uses its content hash rather than a display label.
    Neither this result nor topology equality qualifies batch/setup/calibration.
    """

    target: TargetRevisionRef
    member: TargetMember
    entities: tuple[EntityProjection, ...]
    connections: tuple[ConnectionProjection, ...]
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
    member = content.members[0]
    topology = samples[member.sample_id, member.revision].content.topology
    if topology is None:
        raise ValueError("single-member projection requires a declared sample topology")
    projection = project_target(
        target,
        catalog_id=catalog_id,
        samples=samples,
        execution_topology=execution_topology,
        entities={member.id: {entity.id: entity.id for entity in topology.entities}},
        connections={member.id: {edge.id: edge.id for edge in topology.connections}},
        interconnections={},
    )
    return SingleMemberTargetProjection(
        target=target.ref,
        member=member,
        entities=projection.entities,
        connections=projection.connections,
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
