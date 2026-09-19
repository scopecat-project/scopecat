"""Authoritative validation shared by run and saved-plan admission."""

from scopecat.config.scientific_binding import bind_scientific_evidence
from scopecat.records.config import ConfigProfileSnapshot
from scopecat.records.sample import SampleBinding
from scopecat.records.scientific_binding import (
    InlineSamplesSubject,
    RegisteredTargetSubject,
    ResolvedScientificBinding,
)

from scopecat_server.errors import BackendConflict
from scopecat_server.services.samples import SampleService
from scopecat_server.storage.sqlite.target_catalog import TargetCatalogStore


def validate_scientific_binding(
    binding: ResolvedScientificBinding,
    config: ConfigProfileSnapshot,
    *,
    sample_service: SampleService,
    targets: TargetCatalogStore,
) -> tuple[SampleBinding, ...]:
    try:
        subject = binding.subject
        if (
            isinstance(subject, InlineSamplesSubject)
            and subject.catalog_id != targets.catalog_id
        ):
            raise BackendConflict("scientific binding belongs to another catalog")
        target = (
            targets.resolve(subject.ref)
            if isinstance(subject, RegisteredTargetSubject)
            else None
        )
        samples = sample_service.resolve_bindings(binding.sample_selectors())
        revisions = (
            {
                (member.sample_id, member.revision): sample_service.revision(
                    member.sample_id, member.revision
                )
                for member in target.content.members
            }
            if target is not None
            else {}
        )
        expected = bind_scientific_evidence(
            catalog_id=targets.catalog_id,
            config=config,
            samples=samples,
            target=target,
            sample_revisions=revisions,
        )
        if expected != binding:
            raise BackendConflict("scientific binding does not match retained evidence")
        return samples
    except ValueError as error:
        raise BackendConflict(str(error)) from error
