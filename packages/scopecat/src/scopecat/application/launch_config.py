"""Resolve one scientific choice into immutable evidence and a current fence."""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from scopecat.config.candidates import CandidateConfig
from scopecat.config.scientific_binding import bind_scientific_evidence
from scopecat.records.config import ConfigProfileSnapshot
from scopecat.records.config_context import ContextRunConfigSource
from scopecat.records.launch_request import LaunchConfigSource, LaunchRequest
from scopecat.records.run import (
    AnalysisCandidateRunConfigSource,
    ConfigRegistryRunConfigSource,
)
from scopecat.records.sample import SampleBinding, SampleRevision
from scopecat.records.scientific_binding import ResolvedScientificBinding
from scopecat.records.scientific_scope import DeclaredBatch
from scopecat.records.scientific_selection import (
    ActiveConfiguration,
    CandidateConfiguration,
    RegisteredTargetChoice,
    ReviewedScientificSelection,
    SampleSubjectChoice,
    SavedConfiguration,
    UnboundSubjectChoice,
    WorkingPointConfiguration,
    require_selection_binding,
)

if TYPE_CHECKING:
    from scopecat.api.lab import LabClient


@dataclass(frozen=True)
class ResolvedLaunchScience:
    config: ConfigProfileSnapshot
    reviewed: ReviewedScientificSelection


def _without_generation(source: LaunchConfigSource) -> LaunchConfigSource:
    return (
        source.model_copy(update={"lab_generation": 1})
        if isinstance(source, ContextRunConfigSource)
        else source.model_copy(update={"registry_generation": None})
    )


def resolve_launch_config(
    lab: LabClient, request: LaunchRequest
) -> ResolvedLaunchScience:
    reviewed = request.reviewed
    retained: ResolvedScientificBinding | None = reviewed.binding if reviewed else None
    if request.plan_ref is not None:
        plan = lab.config.client.experiment_plan(request.plan_ref)
        if request.selection != plan.definition.selection:
            raise ValueError("launch selection differs from its saved plan")
        if retained is not None and retained != plan.definition.scientific_binding:
            raise ValueError("launch evidence differs from its saved plan")
        retained = plan.definition.scientific_binding
    config, source, candidate_binding = _resolve_configuration(lab, request)
    batch = request.selection.batch
    batch_id = batch.id if isinstance(batch, DeclaredBatch) else None
    if batch_id is not None:
        lab.experimental_batch(batch_id)
    subject = request.selection.subject
    target = None
    samples: tuple[SampleBinding, ...] = ()
    revisions: dict[tuple[str, int], SampleRevision] = {}
    if isinstance(subject, UnboundSubjectChoice):
        if batch_id is not None or isinstance(source, ContextRunConfigSource):
            raise ValueError("batch or working point requires a subject")
    else:
        if isinstance(subject, RegisteredTargetChoice):
            target = lab.resolve_target(subject.ref)
            if len(target.content.members) != 1 or target.content.connections:
                raise ValueError(
                    "execution requires one target member and no connections"
                )
            member = target.content.members[0]
            sample_id, revision_id = member.sample_id, member.revision
        else:
            assert isinstance(subject, SampleSubjectChoice)
            sample_id, revision_id = subject.sample_id, subject.revision
        exact = (
            source.sample
            if isinstance(source, ContextRunConfigSource)
            else candidate_binding.samples[0]
            if candidate_binding is not None and len(candidate_binding.samples) == 1
            else retained.samples[0]
            if retained is not None and len(retained.samples) == 1
            else None
        )
        if exact is not None:
            if (
                sample_id != exact.sample_id
                or revision_id not in (None, exact.revision)
                or batch_id != exact.batch_id
            ):
                raise ValueError(
                    "selected subject/batch differs from exact scientific evidence"
                )
            revision_id = exact.revision
        current = lab.config.client.get_sample(sample_id)
        revision = (
            current.revision
            if revision_id is None
            else lab.config.client.sample_revision(sample_id, revision_id)
        )
        revisions[(sample_id, revision.revision)] = revision
        sample = SampleBinding(
            role="subject",
            sample_id=sample_id,
            revision=revision.revision,
            content_hash=revision.content_hash,
            kind=current.record.kind,
            display_name=revision.content.display_name,
            context_id=exact.context_id if exact else None,
            batch_id=batch_id,
        )
        if exact is not None and exact != sample:
            raise ValueError("resolved sample differs from frozen scientific evidence")
        samples = (sample,)
    binding = bind_scientific_evidence(
        catalog_id=lab.health().project_id,
        config=config,
        samples=samples,
        target=target,
        sample_revisions=revisions,
    )
    require_selection_binding(request.selection, binding)
    if retained is not None and binding != retained:
        raise ValueError("scientific evidence changed since preview")
    if candidate_binding is not None and binding.subject != candidate_binding.subject:
        raise ValueError("candidate requires its original exact scientific subject")
    return ResolvedLaunchScience(
        config=config,
        reviewed=ReviewedScientificSelection(binding=binding, config_source=source),
    )


def _resolve_configuration(
    lab: LabClient, request: LaunchRequest
) -> tuple[ConfigProfileSnapshot, LaunchConfigSource, ResolvedScientificBinding | None]:
    choice = request.selection.configuration
    reviewed = request.reviewed
    candidate_binding = None
    if isinstance(choice, CandidateConfiguration):
        expected = choice.source
        proposal = lab.config.client.parameter_proposal(
            expected.source_run_id, expected.proposal_id
        ).proposal
        config, resolved_source = lab.config.resolve_with_source(
            CandidateConfig(proposal)
        )
        assert isinstance(resolved_source, AnalysisCandidateRunConfigSource)
        if _without_generation(resolved_source) != _without_generation(expected):
            raise ValueError("candidate no longer matches its exact saved proposal")
        source: LaunchConfigSource = resolved_source.model_copy(
            update={"registry_generation": lab.config.active().activation.generation}
        )
        candidate_binding = lab.config.client.get_run(
            expected.source_run_id
        ).snapshot.scientific_binding
    elif isinstance(choice, WorkingPointConfiguration):
        resolved = lab.config.resolve_context(choice.ref, overrides=choice.overrides)
        config, source = resolved.config, resolved.config_source
    elif isinstance(choice, SavedConfiguration):
        selected = lab.config.entry(choice.ref.entry_id)
        if selected.entry.content_hash != choice.ref.content_hash:
            raise ValueError(
                "saved configuration does not match its exact content hash"
            )
        config = selected.config
        source = ConfigRegistryRunConfigSource(
            selector=selected.entry.id,
            entry_id=selected.entry.id,
            config_ref=selected.entry.config_ref,
            content_hash=selected.entry.content_hash,
            registry_generation=lab.config.active().activation.generation,
        )
    else:
        assert isinstance(choice, ActiveConfiguration)
        if reviewed is None:
            config, active_source = lab.config.resolve_with_source("active")
            assert isinstance(active_source, ConfigRegistryRunConfigSource)
            source = active_source
        else:
            old = reviewed.config_source
            if (
                not isinstance(old, ConfigRegistryRunConfigSource)
                or old.selector != "active"
            ):
                raise ValueError("active selection has inconsistent reviewed source")
            selected = lab.config.entry(old.entry_id)
            config = selected.config
            active = lab.config.active()
            if request.action == "preview" and (
                active.entry.id != old.entry_id
                or active.entry.content_hash != old.content_hash
            ):
                raise ValueError(
                    "active configuration changed; "
                    "clear reviewed evidence and preview again"
                )
            source = ConfigRegistryRunConfigSource(
                selector="active",
                entry_id=selected.entry.id,
                config_ref=selected.entry.config_ref,
                content_hash=selected.entry.content_hash,
                registry_generation=active.activation.generation,
            )
    if reviewed is not None and _without_generation(
        reviewed.config_source
    ) != _without_generation(source):
        raise ValueError("configuration changed since preview; preview again")
    if request.action == "submit":
        assert reviewed is not None
        source = reviewed.config_source
    return config, source, candidate_binding


def launch_config_generation(source: LaunchConfigSource) -> int:
    if isinstance(source, ContextRunConfigSource):
        return source.lab_generation
    assert source.registry_generation is not None
    return source.registry_generation


def launch_preflight_configuration(
    source: LaunchConfigSource,
) -> Literal["accepted", "selected_context"]:
    return (
        "selected_context"
        if isinstance(source, ContextRunConfigSource | AnalysisCandidateRunConfigSource)
        else "accepted"
    )


def launch_preflight_meaning(source: LaunchConfigSource) -> str:
    if isinstance(source, AnalysisCandidateRunConfigSource):
        return (
            "Uses this exact saved candidate for this run; "
            "no validation or default change."
        )
    if isinstance(source, ContextRunConfigSource):
        return (
            "Uses this run's selected saved working point; selection does not "
            "establish calibration validity or change the lab default."
        )
    return "Uses the reviewed active configuration; no default changes."
