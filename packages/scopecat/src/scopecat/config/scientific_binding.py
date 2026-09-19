"""Construct scientific bindings from evidence already resolved by its owner."""

from scopecat.config.target_projection import (
    RetainedSampleRevisions,
    project_single_member_target,
)
from scopecat.records.config import ConfigProfileSnapshot, config_content_hash
from scopecat.records.sample import SampleBinding
from scopecat.records.scientific_binding import (
    EntityProjection,
    InlineSamplesSubject,
    RegisteredTargetSubject,
    ResolvedScientificBinding,
    ResolvedSubject,
    UnboundSubject,
)
from scopecat.records.scientific_scope import setup_content_hash
from scopecat.records.target_catalog import TargetRevision


def bind_scientific_evidence(
    *,
    catalog_id: str,
    config: ConfigProfileSnapshot,
    samples: tuple[SampleBinding, ...],
    target: TargetRevision | None = None,
    sample_revisions: RetainedSampleRevisions,
) -> ResolvedScientificBinding:
    """Never read a head or infer registration from an inline sample.

    The caller resolves sample bindings from its catalog. Registered targets also
    require exact revision content so the shared projection can validate topology.
    """
    if len({sample.role for sample in samples}) != len(samples):
        raise ValueError("scientific binding requires unique sample roles")
    samples = tuple(sorted(samples, key=lambda sample: sample.role))
    subject: ResolvedSubject
    if target is not None:
        projection = project_single_member_target(
            target,
            catalog_id=catalog_id,
            samples=sample_revisions,
            execution_topology=config.system.topology,
        )
        if len(samples) != 1:
            raise ValueError("registered target requires one subject sample")
        sample = samples[0]
        member = projection.member
        if (
            sample.role != "subject"
            or sample.sample_id != member.sample_id
            or sample.revision != member.revision
            or sample.content_hash != member.content_hash
        ):
            raise ValueError("sample evidence does not match the target projection")
        subject = RegisteredTargetSubject(
            ref=target.ref,
            content=target.content,
            sample=sample,
            projection=tuple(
                EntityProjection(
                    target_entity=entity.target_entity,
                    runtime_entity_id=entity.runtime_entity_id,
                )
                for entity in projection.entities
            ),
        )
    elif samples:
        subject = InlineSamplesSubject(catalog_id=catalog_id, samples=samples)
    else:
        subject = UnboundSubject()
    return ResolvedScientificBinding(
        subject=subject,
        config_content_hash=config_content_hash(config),
        setup_content_hash=setup_content_hash(config),
    )
