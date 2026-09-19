"""Scientific scope identity does not inherit UI labels or parameter values."""

import pytest
from pydantic import ValidationError
from scopecat_testkit.config_registry import load_config

from scopecat.kernel.entity import EntityRef
from scopecat.records.config import ResourceRoleSpec, config_content_hash
from scopecat.records.sample import SampleBinding
from scopecat.records.scientific_scope import (
    DeclaredBatch,
    MeasurementTarget,
    TargetConnection,
    TargetEntity,
    TargetMember,
    UnscopedBatch,
    setup_content_hash,
    single_sample_applicability,
)


def _member(name: str) -> TargetMember:
    return TargetMember(
        id=name, sample_id=f"chip-{name}", revision=1, content_hash="sha256:" + "a" * 64
    )


def _sample() -> SampleBinding:
    return SampleBinding(
        role="subject",
        sample_id="chip-A",
        revision=1,
        content_hash="sha256:" + "a" * 64,
        kind="chip",
        display_name="A",
        context_id="parked",
        batch_id="cooldown-1",
    )


def test_joint_target_qualifies_entities_and_has_order_independent_identity() -> None:
    a, b = _member("A"), _member("B")
    left, right = (
        TargetEntity(member_id="A", entity_id="q0"),
        TargetEntity(member_id="B", entity_id="q0"),
    )
    link = TargetConnection(id="link", kind="coupler", endpoints=(left, right))
    joint = MeasurementTarget(members=(a, b), connections=(link,))
    reordered = MeasurementTarget(
        members=(b, a),
        connections=(link.model_copy(update={"endpoints": (right, left)}),),
    )
    assert left != right
    assert joint.content_hash == reordered.content_hash
    assert joint.content_hash != MeasurementTarget(members=(a,)).content_hash
    assert joint.content_hash != MeasurementTarget(members=(a, b)).content_hash
    with pytest.raises(ValidationError, match="unknown member"):
        MeasurementTarget(members=(a,), connections=(link,))
    with pytest.raises(ValidationError, match="member IDs must be unique"):
        MeasurementTarget(members=(a, a))


def test_projection_preserves_old_records_and_rejects_new_conditions() -> None:
    sample, config = _sample(), load_config()
    old_sample, old_config = sample.model_dump_json(), config_content_hash(config)
    scope = single_sample_applicability(sample, config)
    assert scope.batch == DeclaredBatch(id="cooldown-1")
    renamed = sample.model_copy(update={"display_name": "new label"})
    scope.require_same(single_sample_applicability(renamed, config))
    assert sample.model_dump_json() == old_sample
    assert config_content_hash(config) == old_config
    for batch in (None, "cooldown-2"):
        changed = single_sample_applicability(
            sample.model_copy(update={"batch_id": batch}), config
        )
        with pytest.raises(ValueError, match="same declared batch"):
            scope.require_same(changed)
    assert (
        single_sample_applicability(
            sample.model_copy(update={"batch_id": None}), config
        ).batch
        == UnscopedBatch()
    )
    with pytest.raises(ValueError, match="same exact sample revision"):
        scope.require_same(
            single_sample_applicability(
                sample.model_copy(update={"revision": 2}), config
            )
        )


def test_setup_identity_excludes_labels_parameters_and_descriptive_metadata() -> None:
    config = load_config()
    system = config.system.model_copy(deep=True)
    system.id = "renamed system"
    system.topology.entities = [
        entity.model_copy(update={"metadata": {"label": "changed"}})
        for entity in system.topology.entities
    ]
    system.routing.roles = [ResourceRoleSpec(id="readout", description="before")]
    before = config.model_copy(update={"system": system})
    after = before.model_copy(deep=True)
    after.id = "renamed profile"
    after.system.parameter_catalog = after.system.parameter_catalog.model_copy(
        update={"id": "other parameter schema"}
    )
    after.parameter_snapshot = after.parameter_snapshot.model_copy(
        update={"values": ()}
    )
    after.system.routing.roles = [ResourceRoleSpec(id="readout", description="after")]
    assert setup_content_hash(config) == setup_content_hash(
        config.model_copy(
            update={"system": system.model_copy(update={"routing": config.routing})}
        )
    )
    assert setup_content_hash(before) == setup_content_hash(after)
    changed = after.model_copy(deep=True)
    changed.system.topology.entities.append(EntityRef(id="other"))
    assert setup_content_hash(after) != setup_content_hash(changed)
    changed = after.model_copy(deep=True)
    instrument = changed.system.instrument_registry.instruments[0]
    instrument.connection.options["physical-channel"] = 2
    assert setup_content_hash(after) != setup_content_hash(changed)
