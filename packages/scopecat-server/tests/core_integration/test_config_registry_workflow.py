from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from threading import Barrier
from typing import Literal

import pytest
from scopecat.config.candidates import (
    CandidateConfig,
    resolve_candidate_config_snapshot,
)
from scopecat.config.changes import load_parameter_change_proposal
from scopecat.config.registry import (
    CandidateConfigRegistrySource,
    CandidateConfigRevisionSource,
    ConfigRegistryActivationRecord,
    ConfigRegistryEntry,
    ConfigRegistryMutationResult,
    ConfigRegistryUnitOfWorkFactory,
    ConfigRevision,
    DirectConfigRegistrySource,
    DirectConfigRevisionSource,
    InstrumentInventoryMigrationDelta,
    InstrumentInventoryMigrationPlan,
    ManualCandidateAcceptance,
    activate_config_registry_entry,
    load_active_config_registry_snapshot,
    plan_instrument_inventory_migration,
    publish_config_revision,
    resolve_config_registry_config_source,
)
from scopecat.kernel.errors import (
    CheckFailed,
    Conflict,
    DataIntegrityError,
)
from scopecat.records.config import (
    ConfigProfileSnapshot,
    config_content_hash,
)
from scopecat.records.parameter_change import ParameterChangeProposal
from scopecat.records.run import ConfigRegistryRunConfigSource
from scopecat.runs.refs import record_content_ref
from scopecat_testkit.config_registry import (
    activate_candidate_config,
    initialize_setup,
    load_config,
    load_config_registry_config,
    review_parameter_change_proposal,
)
from scopecat_testkit.server.config_registry import signal_run_with_parameter_change
from scopecat_testkit.server.runtime import (
    sqlite_config_registry_unit_of_work,
    sqlite_project_services,
    sqlite_run_repository,
)


@dataclass(frozen=True)
class _ResolvedCandidate:
    candidate: CandidateConfig
    config: ConfigProfileSnapshot


# Test-only storage observations. Product callers use the exact snapshot/page
# services instead of these former convenience wrappers.
def current_config_registry_generation(
    *, unit_of_work: ConfigRegistryUnitOfWorkFactory
) -> int:
    with unit_of_work() as work:
        return work.registry.current_generation()


def list_config_registry_entries(
    *, unit_of_work: ConfigRegistryUnitOfWorkFactory
) -> list[ConfigRegistryEntry]:
    with unit_of_work() as work:
        return list(work.registry.list_entries())


def load_active_config_registry_activation(
    *, unit_of_work: ConfigRegistryUnitOfWorkFactory
) -> ConfigRegistryActivationRecord:
    return load_active_config_registry_snapshot(unit_of_work=unit_of_work).activation


def load_active_config_registry_config(
    *, unit_of_work: ConfigRegistryUnitOfWorkFactory
) -> ConfigProfileSnapshot:
    return load_active_config_registry_snapshot(unit_of_work=unit_of_work).config


def load_active_config_registry_entry(
    *, unit_of_work: ConfigRegistryUnitOfWorkFactory
) -> ConfigRegistryEntry:
    return load_active_config_registry_snapshot(unit_of_work=unit_of_work).entry


def load_config_registry_activation_history(
    *, unit_of_work: ConfigRegistryUnitOfWorkFactory
) -> tuple[ConfigRegistryActivationRecord, ...]:
    with unit_of_work() as work:
        return work.registry.list_activation_history()


def test_publish_revision_writes_and_activates_direct_entry(
    tmp_path: Path,
) -> None:
    config = load_config()
    initialize_setup(config, unit_of_work=sqlite_config_registry_unit_of_work(tmp_path))
    entry = _publish_direct_revision(
        config=config,
        unit_of_work=sqlite_config_registry_unit_of_work(tmp_path),
        entry_id="seed",
        actor="operator",
        note="seed config",
    ).entry

    assert isinstance(entry.source, DirectConfigRegistrySource)
    assert entry.config_ref == (
        "config-registry/configs/seed.config-profile-snapshot.json"
    )
    assert entry.content_hash == config_content_hash(config)
    persisted_config = load_config_registry_config(
        entry_id=entry.id,
        unit_of_work=sqlite_config_registry_unit_of_work(tmp_path),
    )
    assert persisted_config == config

    activated = _publish_direct_revision(
        config=load_config(),
        unit_of_work=sqlite_config_registry_unit_of_work(tmp_path),
        entry_id="active-seed",
        actor="operator",
        note="seed active config",
    )
    entry = activated.entry
    activation = activated.activation
    assert activation is not None
    assert activation.entry_id == entry.id
    assert (
        load_active_config_registry_entry(
            unit_of_work=sqlite_config_registry_unit_of_work(tmp_path)
        )
        == entry
    )
    assert (
        load_active_config_registry_activation(
            unit_of_work=sqlite_config_registry_unit_of_work(tmp_path)
        )
        == activation
    )
    assert (
        load_active_config_registry_config(
            unit_of_work=sqlite_config_registry_unit_of_work(tmp_path)
        )
        == load_config()
    )


def test_registry_rejects_invalid_actor_before_storage(tmp_path: Path) -> None:
    with pytest.raises(CheckFailed) as captured:
        _publish_direct_revision(
            config=load_config(),
            unit_of_work=sqlite_config_registry_unit_of_work(tmp_path),
            entry_id="seed",
            actor=" ",
        )

    assert captured.value.problems[0].code == "config_registry.actor_missing"
    assert (
        list_config_registry_entries(
            unit_of_work=sqlite_config_registry_unit_of_work(tmp_path)
        )
        == []
    )


def test_candidate_config_publish_preserves_parameter_proposal_source(
    tmp_path: Path,
) -> None:
    initialize_setup(
        load_config(), unit_of_work=sqlite_config_registry_unit_of_work(tmp_path)
    )
    _publish_direct_revision(
        config=load_config(),
        unit_of_work=sqlite_config_registry_unit_of_work(tmp_path),
        entry_id="seed",
        actor="operator",
    )
    run_id = signal_run_with_parameter_change(tmp_path)
    proposal = load_parameter_change_proposal(
        run_id=run_id,
        selector="best-signal",
        services=sqlite_project_services(tmp_path),
    )
    candidate = CandidateConfig(
        parameter_proposal=proposal,
    )
    approval = review_parameter_change_proposal(
        run_id=run_id,
        selector="best-signal",
        services=sqlite_project_services(tmp_path),
        reviewer="operator",
        note="looks good",
    )

    activation_result = activate_candidate_config(
        candidate=candidate,
        services=sqlite_project_services(tmp_path),
        entry_id="candidate-best-signal",
        actor="operator",
        note="looks good",
    )

    assert approval.actor == "operator"
    entry = activation_result.entry
    activation = activation_result.activation
    assert activation is not None
    assert isinstance(entry.source, CandidateConfigRegistrySource)
    assert entry.source.run_id == run_id
    assert entry.source.proposal_id == proposal.id
    assert activation.entry_id == entry.id

    stored_proposal = load_parameter_change_proposal(
        run_id=run_id,
        selector="best-signal",
        services=sqlite_project_services(tmp_path),
    )
    assert stored_proposal == proposal
    assert (
        load_config_registry_activation_history(
            unit_of_work=sqlite_config_registry_unit_of_work(tmp_path)
        )[-1]
        == activation
    )

    config, source = resolve_config_registry_config_source(
        selector="active",
        unit_of_work=sqlite_config_registry_unit_of_work(tmp_path),
    )
    assert source.kind == "config_registry"
    assert source.entry_id == entry.id
    assert source.config_ref == entry.config_ref
    assert source.content_hash == entry.content_hash
    assert source.registry_generation == activation.generation
    assert config == load_config_registry_config(
        entry_id=entry.id, unit_of_work=sqlite_config_registry_unit_of_work(tmp_path)
    )


def test_candidate_activation_rejects_a_stale_base_config(tmp_path: Path) -> None:
    run_id = signal_run_with_parameter_change(tmp_path)
    proposal = load_parameter_change_proposal(
        run_id=run_id,
        selector="best-signal",
        services=sqlite_project_services(tmp_path),
    )
    candidate = CandidateConfig(
        parameter_proposal=proposal,
    )
    review_parameter_change_proposal(
        run_id=run_id,
        selector="best-signal",
        services=sqlite_project_services(tmp_path),
        reviewer="operator",
    )
    newer_config = load_config().model_copy(update={"id": "newer-base"})
    initialize_setup(
        newer_config, unit_of_work=sqlite_config_registry_unit_of_work(tmp_path)
    )
    active = _publish_direct_revision(
        config=newer_config,
        unit_of_work=sqlite_config_registry_unit_of_work(tmp_path),
        entry_id="newer-base",
        actor="operator",
    )
    activation = active.activation
    assert activation is not None

    with pytest.raises(Conflict) as error:
        activate_candidate_config(
            candidate=candidate,
            services=sqlite_project_services(tmp_path),
            entry_id="stale-candidate",
            actor="operator",
        )

    assert error.value.problems[0].code == "config_registry.stale_candidate"
    assert (
        load_active_config_registry_activation(
            unit_of_work=sqlite_config_registry_unit_of_work(tmp_path)
        )
        == activation
    )


def test_activation_generation_is_append_only_and_rejects_stale_writes(
    tmp_path: Path,
) -> None:
    initialize_setup(
        load_config(), unit_of_work=sqlite_config_registry_unit_of_work(tmp_path)
    )
    first = _publish_direct_revision(
        config=load_config(),
        unit_of_work=sqlite_config_registry_unit_of_work(tmp_path),
        entry_id="seed-a",
        actor="operator",
    )
    first_record = first.activation
    assert first_record is not None
    second = _publish_direct_revision(
        config=load_config().model_copy(update={"id": "seed-b-config"}),
        unit_of_work=sqlite_config_registry_unit_of_work(tmp_path),
        entry_id="seed-b",
        actor="operator",
    )
    second_record = second.activation
    assert second_record is not None

    assert first_record.generation == 1
    assert (
        current_config_registry_generation(
            unit_of_work=sqlite_config_registry_unit_of_work(tmp_path)
        )
        == 2
    )
    resolved_first, first_source = resolve_config_registry_config_source(
        selector=first.entry.id,
        unit_of_work=sqlite_config_registry_unit_of_work(tmp_path),
    )

    restored = activate_config_registry_entry(
        entry_id=first.entry.id,
        unit_of_work=sqlite_config_registry_unit_of_work(tmp_path),
        actor="operator",
        expected_generation=2,
    )
    restored_record = restored.activation
    assert restored_record is not None
    with pytest.raises(Conflict) as error:
        activate_config_registry_entry(
            entry_id=second.entry.id,
            unit_of_work=sqlite_config_registry_unit_of_work(tmp_path),
            actor="stale-actor",
            expected_generation=2,
        )

    assert error.value.problems[0].code == "config_registry.conflict"
    unchanged = load_active_config_registry_activation(
        unit_of_work=sqlite_config_registry_unit_of_work(tmp_path)
    )
    assert unchanged == restored_record
    assert [
        record.generation
        for record in load_config_registry_activation_history(
            unit_of_work=sqlite_config_registry_unit_of_work(tmp_path)
        )
    ] == [1, 2, 3]
    assert second_record.previous_entry_content_hash == first.entry.content_hash
    assert isinstance(first_source, ConfigRegistryRunConfigSource)
    assert first_source.entry_id == first.entry.id
    assert first_source.content_hash == first.entry.content_hash
    assert first_source.registry_generation is None
    assert config_content_hash(resolved_first) == first_source.content_hash


def test_publish_runs_full_config_semantic_validation(tmp_path: Path) -> None:
    config = load_config()
    invalid_route = config.routing.routes[0].model_copy(
        update={"instrument_id": "missing-source"}
    )
    invalid_config = config.model_copy(
        update={
            "system": config.system.model_copy(
                update={
                    "routing": config.routing.model_copy(
                        update={"routes": [invalid_route]}
                    )
                }
            )
        }
    )
    initialize_setup(
        load_config(), unit_of_work=sqlite_config_registry_unit_of_work(tmp_path)
    )
    with pytest.raises(CheckFailed) as error:
        _publish_direct_revision(
            config=invalid_config,
            unit_of_work=sqlite_config_registry_unit_of_work(tmp_path),
            entry_id="invalid",
            actor="operator",
        )

    assert error.value.problems[0].code == (
        "configuration.unknown_resource_route_instrument"
    )
    assert (
        list_config_registry_entries(
            unit_of_work=sqlite_config_registry_unit_of_work(tmp_path)
        )
        == []
    )


def test_retained_config_can_record_another_domain_target(
    tmp_path: Path,
) -> None:
    unit_of_work = sqlite_config_registry_unit_of_work(tmp_path)
    config = load_config()
    initialize_setup(config, unit_of_work=unit_of_work)
    _publish_direct_revision(
        config=config,
        unit_of_work=unit_of_work,
        entry_id="seed",
        actor="operator",
    )
    target = config.domain_target
    assert target is not None
    renamed = config.model_copy(
        update={
            "id": "renamed-target",
            "system": config.system.model_copy(
                update={
                    "domain_target": target.model_copy(
                        update={"id": "tests.renamed-domain-target"}
                    )
                }
            ),
        }
    )

    _publish_direct_revision(
        config=renamed,
        unit_of_work=unit_of_work,
        entry_id="renamed-target",
        actor="operator",
    )

    activated = load_active_config_registry_config(unit_of_work=unit_of_work)
    assert activated.domain_target is not None
    assert activated.domain_target.id == "tests.renamed-domain-target"


def test_retained_config_can_record_logical_aliases(
    tmp_path: Path,
) -> None:
    unit_of_work = sqlite_config_registry_unit_of_work(tmp_path)
    config = load_config()
    initialize_setup(config, unit_of_work=unit_of_work)
    _publish_direct_revision(
        config=config,
        unit_of_work=unit_of_work,
        entry_id="seed",
        actor="operator",
    )
    [instrument] = config.instrument_registry.instruments
    renamed_id = "renamed-source"
    renamed_registry = config.instrument_registry.model_copy(
        update={"instruments": [instrument.model_copy(update={"id": renamed_id})]}
    )
    renamed_routing = config.routing.model_copy(
        update={
            "routes": [
                route.model_copy(update={"instrument_id": renamed_id})
                for route in config.routing.routes
            ]
        }
    )
    renamed = config.model_copy(
        update={
            "id": "renamed",
            "system": config.system.model_copy(
                update={
                    "instrument_registry": renamed_registry,
                    "routing": renamed_routing,
                }
            ),
        }
    )

    result = _publish_direct_revision(
        config=renamed,
        unit_of_work=unit_of_work,
        entry_id="renamed",
        actor="operator",
    )

    assert result.activation is not None
    assert result.activation.generation == 2


@pytest.mark.parametrize("kind", ("remove", "rekey", "rename_rekey"))
def test_inventory_plan_requires_exact_destructive_declaration(
    kind: Literal["remove", "rekey", "rename_rekey"],
) -> None:
    config = load_config()
    target, change, affected_keys = _inventory_migration_case(config, kind)
    assert plan_instrument_inventory_migration(
        current=config, target=target, declared=(change,)
    ) == InstrumentInventoryMigrationPlan(
        changes=(change,), affected_exclusivity_keys=affected_keys
    )
    with pytest.raises(Conflict, match="instrument_inventory_migration_mismatch"):
        plan_instrument_inventory_migration(current=config, target=target, declared=())


def test_inventory_migration_plan_ignores_non_destructive_inventory_changes() -> None:
    config = load_config()
    [instrument] = config.instrument_registry.instruments
    renamed_id = "renamed-source"
    target = config.model_copy(
        update={
            "id": "ordinary-inventory-changes",
            "system": config.system.model_copy(
                update={
                    "instrument_registry": config.instrument_registry.model_copy(
                        update={
                            "instruments": [
                                instrument.model_copy(update={"id": renamed_id}),
                                instrument.model_copy(
                                    update={
                                        "id": "added-source",
                                        "exclusivity_key": "added-source",
                                    }
                                ),
                            ]
                        }
                    ),
                    "routing": config.routing.model_copy(
                        update={
                            "routes": [
                                route.model_copy(update={"instrument_id": renamed_id})
                                for route in config.routing.routes
                            ]
                        }
                    ),
                }
            ),
        }
    )

    assert plan_instrument_inventory_migration(
        current=config,
        target=target,
        declared=(),
    ) == InstrumentInventoryMigrationPlan(
        changes=(),
        affected_exclusivity_keys=(),
    )


def test_inventory_migration_plan_validates_target_before_returning_keys() -> None:
    config = load_config()
    target, change, _affected_keys = _inventory_migration_case(config, "rekey")
    invalid_route = target.routing.routes[0].model_copy(
        update={"instrument_id": "missing-source"}
    )
    invalid = target.model_copy(
        update={
            "system": target.system.model_copy(
                update={
                    "routing": target.routing.model_copy(
                        update={"routes": [invalid_route]}
                    )
                }
            )
        }
    )

    with pytest.raises(CheckFailed) as error:
        plan_instrument_inventory_migration(
            current=config,
            target=invalid,
            declared=(change,),
        )

    assert error.value.problems[0].code == (
        "configuration.unknown_resource_route_instrument"
    )


def test_concurrent_publishes_apply_one_generation(
    tmp_path: Path,
) -> None:
    unit_of_work = sqlite_config_registry_unit_of_work(tmp_path)
    initialize_setup(load_config(), unit_of_work=unit_of_work)
    initial = _publish_direct_revision(
        config=load_config(),
        unit_of_work=unit_of_work,
        entry_id="seed",
        actor="operator",
    )
    initial_activation = initial.activation
    assert initial_activation is not None
    barrier = Barrier(2)

    def activate(entry_id: str) -> tuple[str, str]:
        barrier.wait()
        try:
            result = _publish_direct_revision(
                config=load_config().model_copy(update={"id": entry_id}),
                unit_of_work=unit_of_work,
                entry_id=entry_id,
                actor="operator",
                expected_generation=initial_activation.generation,
            )
        except Conflict as error:
            return "error", error.problems[0].code
        return "activated", result.entry.id

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(activate, ("candidate-a", "candidate-b")))

    assert sorted(status for status, _detail in outcomes) == ["activated", "error"]
    assert next(detail for status, detail in outcomes if status == "error") == (
        "config_registry.conflict"
    )
    activation = load_active_config_registry_activation(unit_of_work=unit_of_work)
    assert activation.generation == initial_activation.generation + 1
    assert len(list_config_registry_entries(unit_of_work=unit_of_work)) == 2


@pytest.mark.parametrize(
    "proposal_update",
    (
        {"source_run_id": "different-run"},
        {"base_config_id": "different-config"},
    ),
)
def test_candidate_publish_validates_durable_proposal_source(
    tmp_path: Path,
    proposal_update: dict[str, str],
) -> None:
    run_id, proposal, resolved = _resolved_candidate(tmp_path)
    storage = sqlite_run_repository(tmp_path)
    storage.write_model(
        run_id,
        record_content_ref(
            record_id=proposal.id,
            kind="parameter_change_proposal",
        ),
        proposal.model_copy(update=proposal_update),
    )

    with pytest.raises(DataIntegrityError) as error:
        _publish_candidate_revision(
            unit_of_work=sqlite_config_registry_unit_of_work(tmp_path),
            entry_id="invalid-proposal-source",
            actor="operator",
            run_id=run_id,
            proposal_id=resolved.candidate.proposal_id,
        )

    assert error.value.problems[0].code == (
        "config_registry.candidate_proposal_mismatch"
    )


def test_candidate_publish_does_not_ignore_actor_metadata(
    tmp_path: Path,
) -> None:
    run_id, _proposal, resolved = _resolved_candidate(tmp_path)
    _publish_candidate_revision(
        unit_of_work=sqlite_config_registry_unit_of_work(tmp_path),
        entry_id="candidate-metadata",
        actor="operator-a",
        note="first review",
        run_id=run_id,
        proposal_id=resolved.candidate.proposal_id,
    )

    with pytest.raises(Conflict) as error:
        _publish_candidate_revision(
            unit_of_work=sqlite_config_registry_unit_of_work(tmp_path),
            entry_id="candidate-metadata",
            actor="operator-b",
            note="different review",
            run_id=run_id,
            proposal_id=resolved.candidate.proposal_id,
        )

    assert error.value.problems[0].code == "config_registry.duplicate_entry"


def _inventory_migration_case(
    config: ConfigProfileSnapshot,
    kind: Literal["remove", "rekey", "rename_rekey"],
) -> tuple[
    ConfigProfileSnapshot,
    InstrumentInventoryMigrationDelta,
    tuple[str, ...],
]:
    [instrument] = config.instrument_registry.instruments
    if kind == "remove":
        instruments = []
        routes = []
        change = InstrumentInventoryMigrationDelta(
            kind="remove",
            old_instrument_id=instrument.id,
            old_exclusivity_key=instrument.exclusivity_key,
        )
    elif kind == "rekey":
        instruments = [
            instrument.model_copy(update={"exclusivity_key": "replacement-key"})
        ]
        routes = config.routing.routes
        change = InstrumentInventoryMigrationDelta(
            kind="rekey",
            old_instrument_id=instrument.id,
            old_exclusivity_key=instrument.exclusivity_key,
            new_instrument_id=instrument.id,
            new_exclusivity_key="replacement-key",
        )
    else:
        renamed_id = "renamed-source"
        instruments = [
            instrument.model_copy(
                update={
                    "id": renamed_id,
                    "exclusivity_key": "replacement-key",
                }
            )
        ]
        routes = [
            route.model_copy(update={"instrument_id": renamed_id})
            for route in config.routing.routes
        ]
        change = InstrumentInventoryMigrationDelta(
            kind="rename_rekey",
            old_instrument_id=instrument.id,
            old_exclusivity_key=instrument.exclusivity_key,
            new_instrument_id=renamed_id,
            new_exclusivity_key="replacement-key",
        )
    target = config.model_copy(
        update={
            "id": f"{kind}-target",
            "system": config.system.model_copy(
                update={
                    "instrument_registry": config.instrument_registry.model_copy(
                        update={"instruments": instruments}
                    ),
                    "routing": config.routing.model_copy(update={"routes": routes}),
                }
            ),
        }
    )
    affected_keys = tuple(
        sorted(
            {
                change.old_exclusivity_key,
                *(
                    ()
                    if change.new_exclusivity_key is None
                    else (change.new_exclusivity_key,)
                ),
            }
        )
    )
    return target, change, affected_keys


def _publish_direct_revision(
    *,
    config: ConfigProfileSnapshot,
    unit_of_work: ConfigRegistryUnitOfWorkFactory,
    entry_id: str,
    actor: str,
    expected_generation: int | None = None,
    note: str = "",
) -> ConfigRegistryMutationResult:
    generation = (
        current_config_registry_generation(unit_of_work=unit_of_work)
        if expected_generation is None
        else expected_generation
    )
    return publish_config_revision(
        revision=ConfigRevision(
            source=DirectConfigRevisionSource(config),
            entry_id=entry_id,
            actor=actor,
            note=note,
        ),
        unit_of_work=unit_of_work,
        expected_generation=generation,
    )


def _publish_candidate_revision(
    *,
    unit_of_work: ConfigRegistryUnitOfWorkFactory,
    entry_id: str,
    actor: str,
    run_id: str,
    proposal_id: str,
    note: str = "",
) -> ConfigRegistryMutationResult:
    return publish_config_revision(
        revision=ConfigRevision(
            source=CandidateConfigRevisionSource(
                run_id=run_id,
                proposal_id=proposal_id,
                acceptance=ManualCandidateAcceptance(),
            ),
            entry_id=entry_id,
            actor=actor,
            note=note,
        ),
        unit_of_work=unit_of_work,
        expected_generation=current_config_registry_generation(
            unit_of_work=unit_of_work
        ),
    )


def _resolved_candidate(
    project_root: Path,
) -> tuple[str, ParameterChangeProposal, _ResolvedCandidate]:
    initialize_setup(
        load_config(), unit_of_work=sqlite_config_registry_unit_of_work(project_root)
    )
    _publish_direct_revision(
        config=load_config(),
        unit_of_work=sqlite_config_registry_unit_of_work(project_root),
        entry_id="seed",
        actor="operator",
    )
    run_id = signal_run_with_parameter_change(project_root)
    proposal = load_parameter_change_proposal(
        run_id=run_id,
        selector="best-signal",
        services=sqlite_project_services(project_root),
    )
    candidate = CandidateConfig(
        parameter_proposal=proposal,
    )
    review_parameter_change_proposal(
        run_id=run_id,
        selector=proposal.id,
        services=sqlite_project_services(project_root),
        reviewer="operator",
    )
    return (
        run_id,
        proposal,
        _ResolvedCandidate(
            candidate=candidate,
            config=resolve_candidate_config_snapshot(
                candidate,
                services=sqlite_project_services(project_root),
            ),
        ),
    )
