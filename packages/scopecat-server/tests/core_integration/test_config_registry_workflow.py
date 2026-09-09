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
from scopecat.config.parameter_updates import ParameterUpdate
from scopecat.config.parameters import replace_scalar_parameter
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
    ManualConfigDraftRegistrySource,
    ManualConfigDraftResult,
    ManualConfigDraftRevisionSource,
    activate_config_registry_entry,
    load_active_config_registry_snapshot,
    plan_instrument_inventory_migration,
    preview_manual_config_draft,
    publish_config_revision,
    publish_instrument_inventory_migration_revision,
    resolve_config_registry_config_source,
)
from scopecat.kernel.errors import (
    CheckFailed,
    Conflict,
    DataIntegrityError,
)
from scopecat.kernel.quantity import Quantity
from scopecat.records.config import (
    ConfigContentHash,
    ConfigProfileSnapshot,
    config_content_hash,
)
from scopecat.records.parameter import ScalarParameterValue
from scopecat.records.parameter_change import ParameterChangeProposal
from scopecat.records.run import ConfigRegistryRunConfigSource
from scopecat.runs.refs import record_content_ref
from scopecat_testkit.config_registry import (
    activate_candidate_config,
    load_config,
    load_config_registry_config,
    load_config_registry_entry,
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


def test_manual_config_draft_preview_is_read_only_and_publish_records_source(
    tmp_path: Path,
) -> None:
    unit_of_work = sqlite_config_registry_unit_of_work(tmp_path)
    base, activation = _seed_active_config_registry(tmp_path)
    entries_before = list_config_registry_entries(unit_of_work=unit_of_work)

    preview = preview_manual_config_draft(
        unit_of_work=unit_of_work,
        base_entry_id=base.id,
        base_config_content_hash=base.content_hash,
        base_generation=activation.generation,
        candidate_id="manual-preview",
        updates=_manual_config_updates(),
    )

    assert isinstance(preview, ManualConfigDraftResult)
    assert preview.base_entry == base
    assert preview.base_generation == activation.generation
    assert preview.check.ok
    assert preview.check.candidate is not None
    assert preview.check.candidate.id == "manual-preview"
    frequency = preview.check.candidate.parameter_snapshot.get("drive_frequency")
    assert frequency == ScalarParameterValue(
        id="drive_frequency",
        value=Quantity(value=5.2, unit="GHz"),
    )
    assert list_config_registry_entries(unit_of_work=unit_of_work) == entries_before
    assert (
        load_active_config_registry_activation(unit_of_work=unit_of_work) == activation
    )

    mutation = _publish_draft_revision(
        unit_of_work=unit_of_work,
        base_entry_id=base.id,
        base_config_content_hash=base.content_hash,
        base_generation=activation.generation,
        candidate_id="manual-preview",
        updates=_manual_config_updates(),
        expected_result_content_hash=config_content_hash(
            preview.check.candidate,
        ),
        entry_id="manual-entry",
        actor="operator",
        note="adjust drive frequency",
    )

    entry = mutation.entry
    assert mutation.deltas == preview.check.deltas
    assert isinstance(entry.source, ManualConfigDraftRegistrySource)
    assert entry.source.base_entry_id == base.id
    assert entry.source.base_config_content_hash == base.content_hash
    assert entry.source.base_registry_generation == activation.generation
    persisted = load_config_registry_entry(
        entry_id=entry.id,
        unit_of_work=unit_of_work,
    )
    assert persisted == entry
    assert isinstance(persisted.source, ManualConfigDraftRegistrySource)
    assert (
        load_config_registry_config(
            entry_id=entry.id,
            unit_of_work=unit_of_work,
        )
        == preview.check.candidate
    )
    assert mutation.activation == load_active_config_registry_activation(
        unit_of_work=unit_of_work
    )


@pytest.mark.parametrize(
    ("stale_field", "expected_code"),
    (
        ("generation", "config_registry.conflict"),
        ("content_hash", "config_registry.config_draft_base_changed"),
    ),
)
def test_manual_config_draft_publish_rejects_stale_base_identity(
    tmp_path: Path,
    stale_field: Literal["generation", "content_hash"],
    expected_code: str,
) -> None:
    unit_of_work = sqlite_config_registry_unit_of_work(tmp_path)
    base, activation = _seed_active_config_registry(tmp_path)
    preview = preview_manual_config_draft(
        unit_of_work=unit_of_work,
        base_entry_id=base.id,
        base_config_content_hash=base.content_hash,
        base_generation=activation.generation,
        candidate_id="stale-preview",
        updates=_manual_config_updates(),
    )
    assert preview.check.candidate is not None
    base_generation = activation.generation
    base_content_hash = base.content_hash
    if stale_field == "generation":
        newer = _publish_direct_revision(
            config=load_config().model_copy(update={"id": "newer-config"}),
            unit_of_work=unit_of_work,
            entry_id="newer-entry",
            actor="operator",
            expected_generation=activation.generation,
        )
        assert newer.activation is not None
        assert newer.activation.generation == activation.generation + 1
    else:
        base_content_hash = "sha256:" + ("0" * 64)

    with pytest.raises(Conflict) as error:
        _publish_draft_revision(
            unit_of_work=unit_of_work,
            base_entry_id=base.id,
            base_config_content_hash=base_content_hash,
            base_generation=base_generation,
            candidate_id="stale-preview",
            updates=_manual_config_updates(),
            expected_result_content_hash=config_content_hash(
                preview.check.candidate,
            ),
            entry_id=f"stale-{stale_field}",
            actor="operator",
        )

    assert error.value.problems[0].code == expected_code
    assert f"stale-{stale_field}" not in {
        entry.id for entry in list_config_registry_entries(unit_of_work=unit_of_work)
    }


def test_manual_config_draft_publish_rejects_changed_preview_result(
    tmp_path: Path,
) -> None:
    unit_of_work = sqlite_config_registry_unit_of_work(tmp_path)
    base, activation = _seed_active_config_registry(tmp_path)

    with pytest.raises(Conflict) as error:
        _publish_draft_revision(
            unit_of_work=unit_of_work,
            base_entry_id=base.id,
            base_config_content_hash=base.content_hash,
            base_generation=activation.generation,
            candidate_id="changed-result",
            updates=_manual_config_updates(),
            expected_result_content_hash="sha256:" + ("0" * 64),
            entry_id="changed-result",
            actor="operator",
        )

    assert error.value.problems[0].code == (
        "config_registry.config_draft_result_changed"
    )
    assert [
        entry.id for entry in list_config_registry_entries(unit_of_work=unit_of_work)
    ] == [base.id]
    assert (
        load_active_config_registry_activation(unit_of_work=unit_of_work) == activation
    )


def test_manual_config_draft_set_default_stale_conflict_leaves_no_entry(
    tmp_path: Path,
) -> None:
    unit_of_work = sqlite_config_registry_unit_of_work(tmp_path)
    base, activation = _seed_active_config_registry(tmp_path)
    preview = preview_manual_config_draft(
        unit_of_work=unit_of_work,
        base_entry_id=base.id,
        base_config_content_hash=base.content_hash,
        base_generation=activation.generation,
        candidate_id="stale-default",
        updates=_manual_config_updates(),
    )
    assert preview.check.candidate is not None
    newer = _publish_direct_revision(
        config=load_config().model_copy(update={"id": "newer-config"}),
        unit_of_work=unit_of_work,
        entry_id="newer-entry",
        actor="operator",
        expected_generation=activation.generation,
    )
    newer_activation = newer.activation
    assert newer_activation is not None

    with pytest.raises(Conflict) as error:
        _publish_draft_revision(
            unit_of_work=unit_of_work,
            base_entry_id=base.id,
            base_config_content_hash=base.content_hash,
            base_generation=activation.generation,
            candidate_id="stale-default",
            updates=_manual_config_updates(),
            expected_result_content_hash=config_content_hash(
                preview.check.candidate,
            ),
            entry_id="stale-default",
            actor="operator",
        )

    assert error.value.problems[0].code == "config_registry.conflict"
    assert "stale-default" not in {
        entry.id for entry in list_config_registry_entries(unit_of_work=unit_of_work)
    }
    assert (
        load_active_config_registry_activation(unit_of_work=unit_of_work)
        == newer_activation
    )
    assert newer_activation.entry_id == newer.entry.id


def test_manual_config_draft_restores_after_its_original_base_changes(
    tmp_path: Path,
) -> None:
    unit_of_work = sqlite_config_registry_unit_of_work(tmp_path)
    base, activation = _seed_active_config_registry(tmp_path)
    preview = preview_manual_config_draft(
        unit_of_work=unit_of_work,
        base_entry_id=base.id,
        base_config_content_hash=base.content_hash,
        base_generation=activation.generation,
        candidate_id="manual-candidate",
        updates=_manual_config_updates(),
    )
    assert preview.check.candidate is not None
    manual = _publish_draft_revision(
        unit_of_work=unit_of_work,
        base_entry_id=base.id,
        base_config_content_hash=base.content_hash,
        base_generation=activation.generation,
        candidate_id="manual-candidate",
        updates=_manual_config_updates(),
        expected_result_content_hash=config_content_hash(preview.check.candidate),
        entry_id="manual-candidate",
        actor="operator",
    )
    newer = _publish_direct_revision(
        config=load_config().model_copy(update={"id": "newer-config"}),
        unit_of_work=unit_of_work,
        entry_id="newer-entry",
        actor="operator",
        expected_generation=activation.generation + 1,
    )
    newer_activation = newer.activation
    assert newer_activation is not None

    restored = activate_config_registry_entry(
        entry_id=manual.entry.id,
        unit_of_work=unit_of_work,
        actor="operator",
        expected_generation=newer_activation.generation,
    )
    assert manual.activation is not None
    assert restored.activation is not None
    assert restored.entry == manual.entry
    assert restored.activation.restored_from_generation == manual.activation.generation
    assert restored.activation.generation == newer_activation.generation + 1
    assert (
        load_active_config_registry_snapshot(unit_of_work=unit_of_work).config
        == preview.check.candidate
    )


def test_candidate_config_publish_preserves_parameter_proposal_source(
    tmp_path: Path,
) -> None:
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


def test_publish_rejects_rekeying_an_existing_logical_instrument(
    tmp_path: Path,
) -> None:
    unit_of_work = sqlite_config_registry_unit_of_work(tmp_path)
    config = load_config()
    _publish_direct_revision(
        config=config,
        unit_of_work=unit_of_work,
        entry_id="seed",
        actor="operator",
    )
    [instrument] = config.instrument_registry.instruments
    changed_registry = config.instrument_registry.model_copy(
        update={
            "instruments": [
                instrument.model_copy(update={"exclusivity_key": "replacement-key"})
            ]
        }
    )
    changed = config.model_copy(
        update={
            "id": "rekeyed",
            "system": config.system.model_copy(
                update={"instrument_registry": changed_registry}
            ),
        }
    )

    with pytest.raises(Conflict) as error:
        _publish_direct_revision(
            config=changed,
            unit_of_work=unit_of_work,
            entry_id="rekeyed",
            actor="operator",
        )

    assert error.value.problems[0].code == (
        "config_registry.instrument_exclusivity_key_changed"
    )
    assert current_config_registry_generation(unit_of_work=unit_of_work) == 1
    assert [
        entry.id for entry in list_config_registry_entries(unit_of_work=unit_of_work)
    ] == ["seed"]


def test_publish_allows_domain_target_rename(
    tmp_path: Path,
) -> None:
    unit_of_work = sqlite_config_registry_unit_of_work(tmp_path)
    config = load_config()
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


def test_publish_allows_logical_instrument_rename_with_the_same_key(
    tmp_path: Path,
) -> None:
    unit_of_work = sqlite_config_registry_unit_of_work(tmp_path)
    config = load_config()
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


def test_publish_rejects_logical_rename_that_also_rekeys(
    tmp_path: Path,
) -> None:
    unit_of_work = sqlite_config_registry_unit_of_work(tmp_path)
    config = load_config()
    _publish_direct_revision(
        config=config,
        unit_of_work=unit_of_work,
        entry_id="seed",
        actor="operator",
    )
    [instrument] = config.instrument_registry.instruments
    renamed_id = "renamed-source"
    renamed = config.model_copy(
        update={
            "id": "renamed-and-rekeyed",
            "system": config.system.model_copy(
                update={
                    "instrument_registry": config.instrument_registry.model_copy(
                        update={
                            "instruments": [
                                instrument.model_copy(
                                    update={
                                        "id": renamed_id,
                                        "exclusivity_key": "replacement-key",
                                    }
                                )
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

    with pytest.raises(Conflict) as error:
        _publish_direct_revision(
            config=renamed,
            unit_of_work=unit_of_work,
            entry_id="renamed-and-rekeyed",
            actor="operator",
        )

    assert error.value.problems[0].code == (
        "config_registry.instrument_exclusivity_key_removed"
    )
    assert current_config_registry_generation(unit_of_work=unit_of_work) == 1


@pytest.mark.parametrize("kind", ("remove", "rekey", "rename_rekey"))
def test_publish_inventory_migration_records_declared_destructive_change(
    tmp_path: Path,
    kind: Literal["remove", "rekey", "rename_rekey"],
) -> None:
    unit_of_work = sqlite_config_registry_unit_of_work(tmp_path)
    config = load_config()
    seed = _publish_direct_revision(
        config=config,
        unit_of_work=unit_of_work,
        entry_id="seed",
        actor="operator",
    )
    assert seed.activation is not None
    target, change, affected_keys = _inventory_migration_case(config, kind)

    plan = plan_instrument_inventory_migration(
        current=config,
        target=target,
        declared=(change,),
    )
    assert plan == InstrumentInventoryMigrationPlan(
        changes=(change,),
        affected_exclusivity_keys=affected_keys,
    )

    result = publish_instrument_inventory_migration_revision(
        revision=ConfigRevision(
            source=DirectConfigRevisionSource(target),
            entry_id=f"{kind}-target",
            actor="operator",
        ),
        declared=(change,),
        unit_of_work=unit_of_work,
        expected_generation=seed.activation.generation,
    )

    assert result.saved
    assert result.activated
    assert result.activation is not None
    assert result.activation.action == "inventory_migration"
    assert result.activation.generation == seed.activation.generation + 1
    assert load_active_config_registry_config(unit_of_work=unit_of_work) == target


def test_inventory_migration_rejects_declared_diff_mismatch(
    tmp_path: Path,
) -> None:
    unit_of_work = sqlite_config_registry_unit_of_work(tmp_path)
    config = load_config()
    seed = _publish_direct_revision(
        config=config,
        unit_of_work=unit_of_work,
        entry_id="seed",
        actor="operator",
    )
    assert seed.activation is not None
    target, rekey, _affected_keys = _inventory_migration_case(config, "rekey")
    mismatched = InstrumentInventoryMigrationDelta(
        kind="remove",
        old_instrument_id=rekey.old_instrument_id,
        old_exclusivity_key=rekey.old_exclusivity_key,
    )

    with pytest.raises(Conflict) as error:
        publish_instrument_inventory_migration_revision(
            revision=ConfigRevision(
                source=DirectConfigRevisionSource(target),
                entry_id="mismatched",
                actor="operator",
            ),
            declared=(mismatched,),
            unit_of_work=unit_of_work,
            expected_generation=seed.activation.generation,
        )

    assert error.value.problems[0].code == (
        "config_registry.instrument_inventory_migration_mismatch"
    )
    assert [
        entry.id for entry in list_config_registry_entries(unit_of_work=unit_of_work)
    ] == ["seed"]
    assert current_config_registry_generation(unit_of_work=unit_of_work) == 1


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


def test_inventory_migration_rejects_stale_generation_before_saving(
    tmp_path: Path,
) -> None:
    unit_of_work = sqlite_config_registry_unit_of_work(tmp_path)
    config = load_config()
    _publish_direct_revision(
        config=config,
        unit_of_work=unit_of_work,
        entry_id="seed",
        actor="operator",
    )
    target, change, _affected_keys = _inventory_migration_case(config, "rekey")

    with pytest.raises(Conflict) as error:
        publish_instrument_inventory_migration_revision(
            revision=ConfigRevision(
                source=DirectConfigRevisionSource(target),
                entry_id="stale-migration",
                actor="operator",
            ),
            declared=(change,),
            unit_of_work=unit_of_work,
            expected_generation=0,
        )

    assert error.value.problems[0].code == "config_registry.conflict"
    assert [
        entry.id for entry in list_config_registry_entries(unit_of_work=unit_of_work)
    ] == ["seed"]


def test_ordinary_activation_cannot_reverse_inventory_migration(
    tmp_path: Path,
) -> None:
    unit_of_work = sqlite_config_registry_unit_of_work(tmp_path)
    config = load_config()
    seed = _publish_direct_revision(
        config=config,
        unit_of_work=unit_of_work,
        entry_id="seed",
        actor="operator",
    )
    assert seed.activation is not None
    target, change, _affected_keys = _inventory_migration_case(config, "rekey")
    migrated = publish_instrument_inventory_migration_revision(
        revision=ConfigRevision(
            source=DirectConfigRevisionSource(target),
            entry_id="migrated",
            actor="operator",
        ),
        declared=(change,),
        unit_of_work=unit_of_work,
        expected_generation=seed.activation.generation,
    )
    assert migrated.activation is not None

    with pytest.raises(Conflict) as activate_error:
        activate_config_registry_entry(
            entry_id=seed.entry.id,
            unit_of_work=unit_of_work,
            actor="operator",
            expected_generation=migrated.activation.generation,
        )
    assert activate_error.value.problems[0].code == (
        "config_registry.instrument_exclusivity_key_changed"
    )
    assert current_config_registry_generation(unit_of_work=unit_of_work) == 2


def test_concurrent_publishes_apply_one_generation(
    tmp_path: Path,
) -> None:
    unit_of_work = sqlite_config_registry_unit_of_work(tmp_path)
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


def _publish_draft_revision(
    *,
    unit_of_work: ConfigRegistryUnitOfWorkFactory,
    base_entry_id: str,
    base_config_content_hash: ConfigContentHash,
    base_generation: int,
    candidate_id: str,
    updates: tuple[ParameterUpdate, ...],
    expected_result_content_hash: ConfigContentHash,
    entry_id: str,
    actor: str,
    note: str = "",
) -> ConfigRegistryMutationResult:
    return publish_config_revision(
        revision=ConfigRevision(
            source=ManualConfigDraftRevisionSource(
                base_entry_id=base_entry_id,
                base_config_content_hash=base_config_content_hash,
                base_generation=base_generation,
                candidate_id=candidate_id,
                updates=updates,
                expected_result_content_hash=expected_result_content_hash,
            ),
            entry_id=entry_id,
            actor=actor,
            note=note,
        ),
        unit_of_work=unit_of_work,
        expected_generation=base_generation,
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


def _seed_active_config_registry(
    project_root: Path,
) -> tuple[ConfigRegistryEntry, ConfigRegistryActivationRecord]:
    result = _publish_direct_revision(
        config=load_config(),
        unit_of_work=sqlite_config_registry_unit_of_work(project_root),
        entry_id="manual-base",
        actor="operator",
    )
    assert result.activation is not None
    return result.entry, result.activation


def _manual_config_updates() -> tuple[ParameterUpdate, ...]:
    return (
        replace_scalar_parameter(
            "drive_frequency",
            Quantity(value=5.2, unit="GHz"),
        ),
    )
