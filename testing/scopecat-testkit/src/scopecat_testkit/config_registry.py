from __future__ import annotations

from scopecat.config.candidates import CandidateConfig
from scopecat.config.changes import prepare_parameter_change_approval
from scopecat.config.registry.ports import ConfigRegistryUnitOfWorkFactory
from scopecat.config.registry.records import (
    ConfigRegistryEntry,
    ManualCandidateAcceptance,
)
from scopecat.config.registry.service import (
    CandidateConfigRevisionSource,
    ConfigRegistryMutationResult,
    ConfigRevision,
    load_config_registry_entry_snapshot,
    publish_config_revision,
)
from scopecat.kernel.content_identity import sha256_json_hash
from scopecat.project_state import ProjectStateServices
from scopecat.records.config import ConfigProfileSnapshot
from scopecat.records.parameter_change import ParameterChangeApprovalRecord
from scopecat.records.setup import ExecutableSetupSnapshot, SetupRevision

from scopecat_testkit.workflow_fixtures import load_config as load_workflow_config


def load_config() -> ConfigProfileSnapshot:
    return load_workflow_config()


def initialize_setup(
    config: ConfigProfileSnapshot, *, unit_of_work: ConfigRegistryUnitOfWorkFactory
) -> None:
    """Explicitly provision a fixture's equipment before parameter publication."""
    setup = ExecutableSetupSnapshot.from_config(config)
    with unit_of_work() as work:
        revision = work.setups.save_revision(
            SetupRevision(
                id="fixture-setup",
                setup=setup,
                content_hash=setup.content_hash,
                actor="fixture",
            )
        )
        work.setups.activate(
            revision=revision.ref,
            expected_generation=0,
            operation_id="fixture-setup",
            actor="fixture",
            note="explicit test equipment",
            intent_hash=sha256_json_hash(
                {"setup": revision.ref.model_dump(mode="json")}
            ),
        )


def load_config_registry_entry(
    *,
    entry_id: str,
    unit_of_work: ConfigRegistryUnitOfWorkFactory,
) -> ConfigRegistryEntry:
    return load_config_registry_entry_snapshot(
        entry_id=entry_id,
        unit_of_work=unit_of_work,
    ).entry


def load_config_registry_config(
    *,
    entry_id: str,
    unit_of_work: ConfigRegistryUnitOfWorkFactory,
) -> ConfigProfileSnapshot:
    return load_config_registry_entry_snapshot(
        entry_id=entry_id,
        unit_of_work=unit_of_work,
    ).config


def _current_config_registry_generation(
    unit_of_work: ConfigRegistryUnitOfWorkFactory,
) -> int:
    with unit_of_work() as work:
        return work.registry.current_generation()


def activate_candidate_config(
    *,
    candidate: CandidateConfig,
    services: ProjectStateServices,
    entry_id: str | None = None,
    actor: str,
    note: str = "",
    expected_generation: int | None = None,
) -> ConfigRegistryMutationResult:
    generation = (
        _current_config_registry_generation(services.config_registry)
        if expected_generation is None
        else expected_generation
    )
    return publish_config_revision(
        revision=ConfigRevision(
            source=CandidateConfigRevisionSource(
                run_id=candidate.source_run_id,
                proposal_id=candidate.proposal_id,
                acceptance=ManualCandidateAcceptance(),
            ),
            entry_id=entry_id,
            actor=actor,
            note=note,
        ),
        unit_of_work=services.config_registry,
        expected_generation=generation,
    )


def review_parameter_change_proposal(
    *,
    run_id: str,
    selector: str,
    services: ProjectStateServices,
    reviewer: str,
    note: str = "",
) -> ParameterChangeApprovalRecord:
    prepared = prepare_parameter_change_approval(
        run_id=run_id,
        selector=selector,
        services=services,
        actor=reviewer,
        note=note,
    )
    if prepared.publication is not None:
        services.runs.publish_content(prepared.publication)
    return prepared.approval
