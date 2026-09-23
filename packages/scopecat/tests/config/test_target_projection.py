"""A catalog member remains distinct from its single-subject runtime projection."""

from datetime import UTC, datetime

import pytest
from scopecat_testkit.workflow_fixtures import load_config

from scopecat.config.scientific_binding import bind_scientific_evidence
from scopecat.config.target_projection import (
    project_single_member_target,
    project_target,
    validate_target_members,
)
from scopecat.kernel.entity import EntityRef
from scopecat.records.config import Topology, TopologyConnection
from scopecat.records.sample import (
    SampleBinding,
    SampleRevision,
    SampleRevisionDraft,
    sample_revision_content_hash,
)
from scopecat.records.scientific_scope import (
    MeasurementTarget,
    TargetConnection,
    TargetEntity,
    TargetMember,
)
from scopecat.records.target_catalog import TargetRevision, TargetRevisionRef


def _sample(sample_id: str = "chip-a", *, topology: bool = True) -> SampleRevision:
    content = SampleRevisionDraft(
        display_name=sample_id,
        topology=Topology(
            entities=[
                EntityRef(id="q1", kind="qubit"),
                EntityRef(id="q0", kind="qubit"),
            ],
            connections=[
                TopologyConnection(id="edge", kind="coupling", endpoints=("q1", "q0"))
            ],
        )
        if topology
        else None,
    )
    return SampleRevision(
        sample_id=sample_id,
        revision=1,
        actor="operator",
        content=content,
        content_hash=sample_revision_content_hash(sample_id=sample_id, content=content),
    )


def _member(sample: SampleRevision, name: str = "A") -> TargetMember:
    return TargetMember(
        id=name,
        sample_id=sample.sample_id,
        revision=sample.revision,
        content_hash=sample.content_hash,
    )


def _target(content: MeasurementTarget) -> TargetRevision:
    return TargetRevision(
        ref=TargetRevisionRef(
            catalog_id="local:catalog",
            target_id="target",
            revision=1,
            content_hash=content.content_hash,
        ),
        name="Chip A",
        content=content,
        actor="operator",
        recorded_at=datetime.now(UTC),
    )


@pytest.mark.parametrize(
    "change", [None, "collision", "missing", "edge_collision", "wrong_link", "extra"]
)
def test_explicit_connected_projection_keeps_member_addresses(
    change: str | None,
) -> None:
    a, b = _sample(), _sample("chip-b")
    target = _target(
        MeasurementTarget(
            members=(_member(b, "B"), _member(a)),
            connections=(
                TargetConnection(
                    id="bus",
                    kind="link",
                    endpoints=(
                        TargetEntity(member_id="A", entity_id="q1"),
                        TargetEntity(member_id="B", entity_id="q0"),
                    ),
                ),
            ),
        )
    )
    entities = {
        "A": {"q0": "left0", "q1": "left1"},
        "B": {"q0": "right0", "q1": "right1"},
    }
    connections = {"A": {"edge": "left-edge"}, "B": {"edge": "right-edge"}}
    interconnections = {"bus": "bus-edge"}
    runtime = Topology(
        entities=[
            EntityRef(id=name, kind="qubit")
            for name in ("left0", "left1", "right0", "right1")
        ],
        connections=[
            TopologyConnection(
                id="left-edge", kind="coupling", endpoints=("left0", "left1")
            ),
            TopologyConnection(
                id="right-edge", kind="coupling", endpoints=("right0", "right1")
            ),
            TopologyConnection(
                id="bus-edge", kind="link", endpoints=("right0", "left1")
            ),
        ],
    )
    if change == "collision":
        entities["B"]["q0"] = "left0"
    elif change == "missing":
        del entities["B"]["q0"]
    elif change == "edge_collision":
        interconnections["bus"] = "left-edge"
    elif change == "wrong_link":
        runtime.connections[-1] = runtime.connections[-1].model_copy(
            update={"endpoints": ("left0", "right0")}
        )
    elif change == "extra":
        runtime.entities.append(EntityRef(id="spectator", kind="qubit"))

    def project():
        return project_target(
            target,
            catalog_id="local:catalog",
            samples={(a.sample_id, 1): a, (b.sample_id, 1): b},
            execution_topology=runtime,
            entities=entities,
            connections=connections,
            interconnections=interconnections,
        )

    if change is not None:
        errors = {
            "collision": "distinct runtime IDs",
            "missing": "cover its exact topology",
            "edge_collision": "distinct runtime IDs",
            "wrong_link": "execution topology does not match",
            "extra": "execution topology does not match",
        }
        with pytest.raises(ValueError, match=errors[change]):
            project()
        return
    result = project()
    assert result.target == target.ref
    assert tuple(member.id for member in result.members) == ("A", "B")
    assert [
        (
            row.target_entity.member_id,
            row.target_entity.entity_id,
            row.runtime_entity_id,
        )
        for row in result.entities
    ] == [
        ("A", "q0", "left0"),
        ("A", "q1", "left1"),
        ("B", "q0", "right0"),
        ("B", "q1", "right1"),
    ]
    assert [
        (row.member_id, row.connection_id, row.runtime_connection_id)
        for row in result.connections
    ] == [
        ("A", "edge", "left-edge"),
        ("B", "edge", "right-edge"),
        (None, "bus", "bus-edge"),
    ]


def test_projection_preserves_exact_reference_and_member_to_subject_mapping() -> None:
    sample = _sample()
    target = _target(MeasurementTarget(members=(_member(sample),)))
    assert sample.content.topology is not None
    runtime = sample.content.topology.model_copy(deep=True)
    runtime.entities = [
        entity.model_copy(update={"metadata": {"label": "new display"}})
        for entity in reversed(runtime.entities)
    ]
    runtime.connections = [
        connection.model_copy(
            update={"endpoints": tuple(reversed(connection.endpoints))}
        )
        for connection in reversed(runtime.connections)
    ]
    evidence = {(sample.sample_id, sample.revision): sample}
    projection = project_single_member_target(
        target,
        catalog_id="local:catalog",
        samples=evidence,
        execution_topology=runtime,
    )
    assert projection.target == target.ref
    assert projection.member.id == "A"
    assert projection.run_role == "subject"
    assert [
        (
            item.target_entity.member_id,
            item.target_entity.entity_id,
            item.runtime_entity_id,
        )
        for item in projection.entities
    ] == [("A", "q0", "q0"), ("A", "q1", "q1")]
    renamed = target.model_copy(
        update={
            "name": "Renamed",
            "ref": target.ref.model_copy(update={"revision": 2}),
        }
    )
    later = project_single_member_target(
        renamed,
        catalog_id="local:catalog",
        samples=evidence,
        execution_topology=runtime,
    )
    assert later.target != projection.target
    assert later.target.content_hash == projection.target.content_hash
    assert later.entities == projection.entities
    with pytest.raises(ValueError, match="another catalog"):
        project_single_member_target(
            target,
            catalog_id="local:foreign",
            samples=evidence,
            execution_topology=runtime,
        )


def test_registered_assemblies_validate_but_have_no_single_member_projection() -> None:
    a, b = _sample(), _sample("chip-b")
    member_a, member_b = _member(a), _member(b, "B")
    evidence = {(a.sample_id, 1): a, (b.sample_id, 1): b}
    joint = _target(MeasurementTarget(members=(member_a, member_b)))
    validate_target_members(joint.content, evidence)
    assert a.content.topology is not None
    with pytest.raises(ValueError, match="one member and no target connections"):
        project_single_member_target(
            joint,
            catalog_id=joint.ref.catalog_id,
            samples=evidence,
            execution_topology=a.content.topology,
        )
    connected = _target(
        MeasurementTarget(
            members=(member_a,),
            connections=(
                TargetConnection(
                    id="internal",
                    kind="link",
                    endpoints=(
                        TargetEntity(member_id="A", entity_id="q0"),
                        TargetEntity(member_id="A", entity_id="q1"),
                    ),
                ),
            ),
        )
    )
    validate_target_members(connected.content, evidence)
    with pytest.raises(ValueError, match="one member and no target connections"):
        project_single_member_target(
            connected,
            catalog_id=connected.ref.catalog_id,
            samples=evidence,
            execution_topology=a.content.topology,
        )


@pytest.mark.parametrize(
    "change", ["entity_kind", "connection", "connection_entity", "subset"]
)
def test_projection_rejects_changed_execution_topology(change: str) -> None:
    sample = _sample()
    target = _target(MeasurementTarget(members=(_member(sample),)))
    assert sample.content.topology is not None
    runtime = sample.content.topology.model_copy(deep=True)
    if change == "entity_kind":
        runtime.entities[0] = EntityRef(id="q1", kind="resonator")
    elif change == "connection":
        runtime.connections = []
    elif change == "connection_entity":
        runtime.connections[0] = runtime.connections[0].model_copy(
            update={"entity_id": "q0"}
        )
    else:
        runtime = Topology(entities=[EntityRef(id="q0", kind="qubit")])
    with pytest.raises(ValueError, match="execution topology does not match"):
        project_single_member_target(
            target,
            catalog_id=target.ref.catalog_id,
            samples={(sample.sample_id, 1): sample},
            execution_topology=runtime,
        )


def test_catalog_can_retain_sample_without_topology_but_projection_cannot() -> None:
    sample = _sample(topology=False)
    target = _target(MeasurementTarget(members=(_member(sample),)))
    evidence = {(sample.sample_id, 1): sample}
    validate_target_members(target.content, evidence)
    with pytest.raises(ValueError, match="declared sample topology"):
        project_single_member_target(
            target,
            catalog_id=target.ref.catalog_id,
            samples=evidence,
            execution_topology=Topology(),
        )


def test_registered_binding_uses_projection_and_rejects_conflicting_sample() -> None:
    sample = _sample()
    target = _target(MeasurementTarget(members=(_member(sample),)))
    config = load_config()
    config = config.model_copy(
        update={
            "system": config.system.model_copy(
                update={"topology": sample.content.topology}
            )
        }
    )
    exact = SampleBinding(
        role="subject",
        sample_id=sample.sample_id,
        revision=sample.revision,
        content_hash=sample.content_hash,
        kind="chip",
        display_name="Chip A",
    )
    binding = bind_scientific_evidence(
        catalog_id="local:catalog",
        config=config,
        samples=(exact,),
        target=target,
        sample_revisions={(sample.sample_id, sample.revision): sample},
    )
    assert binding.subject.kind == "registered_target"
    assert binding.subject.ref == target.ref
    assert binding.target_binding is not None
    assert binding.target_binding.entities[0].target_entity.member_id == "A"
    assert binding.target_binding.target == target.ref
    assert binding.target_binding.setup_content_hash == binding.setup_content_hash
    assert binding.target_binding.connections[0].runtime_connection_id == "edge"
    assert "projection" not in binding.subject.model_dump()
    assert binding.sample_selectors()[0].role == "subject"
    with pytest.raises(ValueError, match="sample evidence"):
        bind_scientific_evidence(
            catalog_id="local:catalog",
            config=config,
            samples=(exact.model_copy(update={"revision": 2}),),
            target=target,
            sample_revisions={(sample.sample_id, sample.revision): sample},
        )
