"""Immutable configuration evidence and exact named snapshot resolution.

Parameter branches own editing. Registry snapshots retain composed execution
inputs and scientific provenance; no entry is a project-wide default.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from scopecat.config.candidates import (
    CandidateConfig,
    resolve_candidate_config_from_snapshot,
)
from scopecat.config.contexts import validate_context_config
from scopecat.config.profile_validation import validate_config_profile
from scopecat.config.registry.ports import (
    ConfigRegistryRepository,
    ConfigRegistryUnitOfWork,
    ConfigRegistryUnitOfWorkFactory,
)
from scopecat.config.registry.records import (
    CandidateAcceptance,
    CandidateConfigRegistrySource,
    ConfigRegistryEntry,
    ContextConfigRegistrySource,
    DirectConfigRegistrySource,
)
from scopecat.kernel.errors import (
    CheckFailed,
    Conflict,
    DataIntegrityError,
    NotFound,
    ProblemFailure,
)
from scopecat.kernel.problems import (
    ModelLocation,
    Problem,
    ProblemLocation,
    ProblemPhase,
    StorageLocation,
)
from scopecat.records.config import (
    ConfigProfileSnapshot,
    config_content_equal,
    config_content_hash,
)
from scopecat.records.content import ContentEntry
from scopecat.records.parameter_change import (
    ParameterChangeProposal,
    ParameterValueDelta,
)
from scopecat.records.run import (
    ConfigRegistryRunConfigSource,
    RunConfigSource,
)
from scopecat.records.setup import (
    ExecutableSetupSnapshot,
)
from scopecat.runs.refs import record_content_ref
from scopecat.runs.repository import RunRepository

SAFE_ENTRY_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


@dataclass(frozen=True, slots=True)
class _ValidatedCandidateSource:
    config: ConfigProfileSnapshot
    source: CandidateConfigRegistrySource
    deltas: tuple[ParameterValueDelta, ...]


@dataclass(frozen=True, slots=True)
class DirectConfigRevisionSource:
    config: ConfigProfileSnapshot


@dataclass(frozen=True, slots=True)
class CandidateConfigRevisionSource:
    run_id: str
    proposal_id: str
    acceptance: CandidateAcceptance


type ConfigRevisionSource = DirectConfigRevisionSource | CandidateConfigRevisionSource


@dataclass(frozen=True, slots=True)
class ConfigRevision:
    source: ConfigRevisionSource
    entry_id: str | None
    actor: str
    note: str = ""


@dataclass(frozen=True, slots=True)
class InstrumentInventoryMigrationDelta:
    kind: Literal["remove", "rekey", "rename_rekey"]
    old_instrument_id: str
    old_exclusivity_key: str
    new_instrument_id: str | None = None
    new_exclusivity_key: str | None = None


@dataclass(frozen=True, slots=True)
class InstrumentInventoryMigrationPlan:
    changes: tuple[InstrumentInventoryMigrationDelta, ...]
    affected_exclusivity_keys: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ConfigRegistryEntrySnapshot:
    entry: ConfigRegistryEntry
    config: ConfigProfileSnapshot


@dataclass(frozen=True, slots=True)
class ConfigRegistryPageSnapshot:
    entries: tuple[ConfigRegistryEntry, ...]
    next_cursor: int | None = None


@dataclass(frozen=True, slots=True)
class ConfigRegistryMutationResult:
    """An immutable evidence save and its resolved parameter deltas."""

    entry: ConfigRegistryEntry
    saved: bool = False
    deltas: tuple[ParameterValueDelta, ...] = ()


def save_config_revision(
    *,
    revision: ConfigRevision,
    unit_of_work: ConfigRegistryUnitOfWorkFactory,
) -> ConfigRegistryMutationResult:
    """Save immutable parameter inputs without selecting a global default."""
    _validate_config_revision(revision)
    with unit_of_work() as work:
        return _save_config_revision_locked(revision=revision, work=work)


def _save_config_revision_locked(
    *,
    revision: ConfigRevision,
    work: ConfigRegistryUnitOfWork,
) -> ConfigRegistryMutationResult:
    source = revision.source
    deltas: tuple[ParameterValueDelta, ...] = ()
    if isinstance(source, DirectConfigRevisionSource):
        config = source.config
        entry_source = DirectConfigRegistrySource()
        entry_id = _required_revision_entry_id(revision)
    else:
        validated = validate_candidate_source_records(
            storage=work.runs,
            run_id=source.run_id,
            proposal_id=source.proposal_id,
            acceptance=source.acceptance,
        )
        config = validated.config
        entry_source = validated.source
        deltas = validated.deltas
        entry_id = revision.entry_id or f"{config.id}-{source.run_id}"
        _validate_entry_id(entry_id)
    entry = ConfigRegistryEntry(
        id=entry_id,
        config_ref=work.registry.config_ref(entry_id),
        content_hash=config_content_hash(config),
        source=entry_source,
        actor=revision.actor,
        note=revision.note,
    )
    committed = _commit_revision_locked(
        repository=work.registry,
        requested_entry=entry,
        config=config,
    )
    return ConfigRegistryMutationResult(
        entry=committed.entry,
        saved=committed.saved,
        deltas=deltas,
    )


def _validate_config_revision(
    revision: ConfigRevision,
) -> None:
    if revision.entry_id is not None:
        _validate_entry_id(revision.entry_id)
    _validate_required_text(revision.actor, field="actor")
    if isinstance(revision.source, CandidateConfigRevisionSource):
        _validate_required_text(revision.source.run_id, field="run_id")
        _validate_required_text(revision.source.proposal_id, field="proposal_id")


def _required_revision_entry_id(
    revision: ConfigRevision,
) -> str:
    if revision.entry_id is None:
        raise _registry_failure(
            CheckFailed,
            code="config_registry.entry_id_missing",
            message="config registry entry_id must be non-empty",
            location=_registry_model_location("entry_id"),
        )
    return revision.entry_id


def validate_candidate_source_records(
    *,
    storage: RunRepository,
    run_id: str,
    proposal_id: str,
    acceptance: CandidateAcceptance,
) -> _ValidatedCandidateSource:
    """Validate a candidate and capture its revision provenance."""

    source_config = storage.read_config_profile_snapshot(run_id)
    source_config_hash = config_content_hash(source_config)
    proposal_record = _require_run_record(
        storage=storage,
        run_id=run_id,
        record_id=proposal_id,
        kind="parameter_change_proposal",
    )
    proposal_ref = record_content_ref(
        record_id=proposal_record.id,
        kind=proposal_record.kind,
    )
    proposal = storage.read_model(
        run_id,
        proposal_ref,
        ParameterChangeProposal,
    )
    if (
        proposal.id != proposal_id
        or proposal.source_run_id != run_id
        or proposal.base_config_id != source_config.id
        or proposal.base_config_content_hash != source_config_hash
    ):
        raise _registry_failure(
            DataIntegrityError,
            code="config_registry.candidate_proposal_mismatch",
            message="candidate proposal does not match its source config",
            location=_registry_storage_location(proposal_ref, run_id=run_id),
            related_locations=(_registry_model_location("proposal_id"),),
            details={"proposal_id": proposal_id},
        )
    durable_config = resolve_candidate_config_from_snapshot(
        CandidateConfig(parameter_proposal=proposal),
        source_config=source_config,
    )
    source = CandidateConfigRegistrySource(
        run_id=run_id,
        proposal_id=proposal_id,
        base_config_content_hash=source_config_hash,
        acceptance=acceptance,
    )
    return _ValidatedCandidateSource(
        config=durable_config,
        source=source,
        deltas=proposal.deltas,
    )


def load_config_registry_page(
    *,
    limit: int,
    before: int | None,
    unit_of_work: ConfigRegistryUnitOfWorkFactory,
) -> ConfigRegistryPageSnapshot:
    """Read one newest-first page of immutable evidence entries."""

    with unit_of_work() as work:
        page = work.registry.list_entry_page(limit=limit, before=before)
        return ConfigRegistryPageSnapshot(
            entries=page.items,
            next_cursor=page.next_cursor,
        )


def load_config_registry_entry_snapshot(
    *,
    entry_id: str,
    unit_of_work: ConfigRegistryUnitOfWorkFactory,
) -> ConfigRegistryEntrySnapshot:
    _validate_entry_id(entry_id)
    with unit_of_work() as work:
        loaded = _load_config_registry_entry_locked(entry_id=entry_id, work=work)
        return ConfigRegistryEntrySnapshot(
            entry=loaded.entry,
            config=loaded.config,
        )


def _load_config_registry_entry_locked(
    *, entry_id: str, work: ConfigRegistryUnitOfWork
) -> ConfigRegistryEntrySnapshot:
    """Read one entry and verify its content-addressed config."""

    if not work.registry.entry_exists(entry_id):
        raise _registry_failure(
            NotFound,
            code="config_registry.not_found",
            message="config registry entry was not found",
            location=_registry_model_location("entry_id"),
            details={"entry_id": entry_id},
        )
    entry = work.registry.read_entry(entry_id)
    return ConfigRegistryEntrySnapshot(
        entry=entry,
        config=_read_entry_config(work.registry, entry),
    )


def resolve_config_registry_config_source(
    *, selector: str, unit_of_work: ConfigRegistryUnitOfWorkFactory
) -> tuple[ConfigProfileSnapshot, RunConfigSource]:
    _validate_entry_id(selector)
    with unit_of_work() as work:
        return _resolve_entry_config_registry_config_source_locked(
            selector=selector,
            work=work,
        )


def _resolve_entry_config_registry_config_source_locked(
    *, selector: str, work: ConfigRegistryUnitOfWork
) -> tuple[ConfigProfileSnapshot, RunConfigSource]:
    loaded = _load_config_registry_entry_locked(
        entry_id=selector,
        work=work,
    )
    entry = loaded.entry
    source = ConfigRegistryRunConfigSource(
        selector=selector,
        entry_id=entry.id,
        config_ref=entry.config_ref,
        content_hash=entry.content_hash,
    )
    return loaded.config, source


def _validate_entry_id(entry_id: str) -> None:
    if not SAFE_ENTRY_ID_RE.fullmatch(entry_id):
        raise _registry_failure(
            CheckFailed,
            code="config_registry.invalid_entry_id",
            message="config registry entry id is not safe",
            location=_registry_model_location("entry_id"),
            details={"entry_id": entry_id},
        )


def _validate_required_text(value: str, *, field: str) -> None:
    if value.strip():
        return
    raise _registry_failure(
        CheckFailed,
        code=f"config_registry.{field}_missing",
        message=f"config registry {field} must be non-empty",
        location=_registry_model_location(field),
    )


def _require_run_record(
    *,
    storage: RunRepository,
    run_id: str,
    record_id: str,
    kind: str,
) -> ContentEntry:
    try:
        record = storage.read_content(
            run_id,
            role="record",
            content_id=record_id,
        )
    except NotFound:
        raise _registry_failure(
            NotFound,
            code="config_registry.source_record_not_found",
            message="config registry source record was not found",
            location=StorageLocation(
                run_id=run_id,
                path=("records", record_id),
            ),
            related_locations=(_registry_model_location("record_id"),),
            details={"record_id": record_id},
        ) from None
    if record.kind != kind:
        raise _registry_failure(
            CheckFailed,
            code="config_registry.source_record_kind_mismatch",
            message="config registry source record has the wrong kind",
            location=StorageLocation(
                run_id=run_id,
                path=("records", record_id, "kind"),
            ),
            related_locations=(_registry_model_location("record_id"),),
            details={
                "record_id": record_id,
                "actual_kind": record.kind,
                "expected_kind": kind,
            },
        )
    return record


def _commit_revision_locked(
    *,
    repository: ConfigRegistryRepository,
    requested_entry: ConfigRegistryEntry,
    config: ConfigProfileSnapshot,
) -> ConfigRegistryMutationResult:
    if isinstance(requested_entry.source, ContextConfigRegistrySource):
        validate_context_config(config)
    else:
        _require_valid_config(config)
    existing = _find_existing_entry_locked(
        repository=repository,
        entry_id=requested_entry.id,
    )
    if existing is not None:
        existing_config = _read_entry_config(repository, existing)
        if not (
            _same_revision(existing, requested_entry)
            and config_content_equal(existing_config, config)
        ):
            raise _registry_failure(
                Conflict,
                code="config_registry.duplicate_entry",
                message="config registry entry id is already committed differently",
                location=_registry_model_location("entry_id"),
                related_locations=(
                    _registry_storage_location(repository.entry_ref(existing.id)),
                ),
                details={"entry_id": requested_entry.id},
            )
        return ConfigRegistryMutationResult(entry=existing)
    repository.commit_revision(
        entry=requested_entry,
        config=config,
    )
    return ConfigRegistryMutationResult(
        entry=requested_entry,
        saved=True,
    )


def _find_existing_entry_locked(
    *,
    repository: ConfigRegistryRepository,
    entry_id: str,
) -> ConfigRegistryEntry | None:
    if repository.entry_exists(entry_id):
        return repository.read_entry(entry_id)
    return None


def _same_revision(
    existing: ConfigRegistryEntry, requested: ConfigRegistryEntry
) -> bool:
    return (
        existing.config_ref == requested.config_ref
        and existing.content_hash == requested.content_hash
        and existing.source == requested.source
        and existing.actor == requested.actor
        and existing.note == requested.note
    )


def _read_entry_config(
    repository: ConfigRegistryRepository,
    entry: ConfigRegistryEntry,
) -> ConfigProfileSnapshot:
    config = repository.read_config(entry.config_ref)
    actual_hash = config_content_hash(config)
    if actual_hash != entry.content_hash:
        raise _registry_failure(
            DataIntegrityError,
            code="config_registry.content_hash_mismatch",
            message="config registry snapshot does not match its saved hash",
            location=_registry_storage_location(entry.config_ref),
            related_locations=(
                _registry_storage_location(repository.entry_ref(entry.id)),
            ),
            details={
                "entry_id": entry.id,
                "expected_content_hash": entry.content_hash,
                "actual_content_hash": actual_hash,
            },
        )
    return config


def _require_valid_config(config: ConfigProfileSnapshot) -> None:
    problems = validate_config_profile(config)
    if bool(problems):
        raise CheckFailed(problems)


def plan_instrument_inventory_migration(
    *,
    current: ConfigProfileSnapshot | ExecutableSetupSnapshot,
    target: ConfigProfileSnapshot | ExecutableSetupSnapshot,
    declared: Sequence[InstrumentInventoryMigrationDelta],
) -> InstrumentInventoryMigrationPlan:
    """Match explicit intent to every destructive inventory change."""

    if isinstance(target, ConfigProfileSnapshot):
        _require_valid_config(target)
    current_by_id = {
        instrument.id: instrument.exclusivity_key
        for instrument in current.instrument_registry.instruments
    }
    target_by_id = {
        instrument.id: instrument.exclusivity_key
        for instrument in target.instrument_registry.instruments
    }
    current_keys = set(current_by_id.values())
    target_keys = set(target_by_id.values())
    declared_changes = tuple(declared)
    rename_counts_by_old_id: dict[str, int] = {}
    rename_counts_by_new_id: dict[str, int] = {}
    rename_by_old_id: dict[str, InstrumentInventoryMigrationDelta] = {}
    for change in declared_changes:
        if not _is_well_formed_inventory_change(change):
            continue
        if change.kind != "rename_rekey":
            continue
        assert change.new_instrument_id is not None
        rename_counts_by_old_id[change.old_instrument_id] = (
            rename_counts_by_old_id.get(change.old_instrument_id, 0) + 1
        )
        rename_counts_by_new_id[change.new_instrument_id] = (
            rename_counts_by_new_id.get(change.new_instrument_id, 0) + 1
        )
        rename_by_old_id[change.old_instrument_id] = change

    inferred: list[InstrumentInventoryMigrationDelta] = []
    for old_instrument_id, old_exclusivity_key in sorted(current_by_id.items()):
        target_key = target_by_id.get(old_instrument_id)
        if target_key is not None:
            if target_key != old_exclusivity_key:
                inferred.append(
                    InstrumentInventoryMigrationDelta(
                        kind="rekey",
                        old_instrument_id=old_instrument_id,
                        old_exclusivity_key=old_exclusivity_key,
                        new_instrument_id=old_instrument_id,
                        new_exclusivity_key=target_key,
                    )
                )
            continue
        if old_exclusivity_key in target_keys:
            continue
        rename = rename_by_old_id.get(old_instrument_id)
        if (
            rename is not None
            and rename_counts_by_old_id[old_instrument_id] == 1
            and rename.new_instrument_id is not None
            and rename.new_exclusivity_key is not None
            and rename_counts_by_new_id[rename.new_instrument_id] == 1
            and rename.old_exclusivity_key == old_exclusivity_key
            and rename.new_instrument_id not in current_by_id
            and rename.new_exclusivity_key not in current_keys
            and target_by_id.get(rename.new_instrument_id) == rename.new_exclusivity_key
        ):
            inferred.append(rename)
            continue
        inferred.append(
            InstrumentInventoryMigrationDelta(
                kind="remove",
                old_instrument_id=old_instrument_id,
                old_exclusivity_key=old_exclusivity_key,
            )
        )

    changes = tuple(sorted(inferred, key=_inventory_change_sort_key))
    normalized_declared = tuple(
        sorted(declared_changes, key=_inventory_change_sort_key)
    )
    if normalized_declared != changes:
        raise _registry_failure(
            Conflict,
            code="config_registry.instrument_inventory_migration_mismatch",
            message=(
                "declared instrument inventory migration does not match "
                "the destructive config diff"
            ),
            location=_registry_model_location("declared"),
            details={
                "declared": [
                    _inventory_change_details(change) for change in normalized_declared
                ],
                "inferred": [_inventory_change_details(change) for change in changes],
            },
        )
    affected_keys = {
        key
        for change in changes
        for key in (
            change.old_exclusivity_key,
            change.new_exclusivity_key,
        )
        if key is not None
    }
    return InstrumentInventoryMigrationPlan(
        changes=changes,
        affected_exclusivity_keys=tuple(sorted(affected_keys)),
    )


def _is_well_formed_inventory_change(
    change: InstrumentInventoryMigrationDelta,
) -> bool:
    if change.kind == "remove":
        return change.new_instrument_id is None and change.new_exclusivity_key is None
    if change.kind == "rekey":
        return (
            change.new_instrument_id == change.old_instrument_id
            and change.new_exclusivity_key is not None
            and change.new_exclusivity_key != change.old_exclusivity_key
        )
    return (
        change.new_instrument_id is not None
        and change.new_instrument_id != change.old_instrument_id
        and change.new_exclusivity_key is not None
        and change.new_exclusivity_key != change.old_exclusivity_key
    )


def _inventory_change_sort_key(
    change: InstrumentInventoryMigrationDelta,
) -> tuple[str, str, str, str, str]:
    return (
        change.old_instrument_id,
        change.old_exclusivity_key,
        change.kind,
        change.new_instrument_id or "",
        change.new_exclusivity_key or "",
    )


def _inventory_change_details(
    change: InstrumentInventoryMigrationDelta,
) -> dict[str, object]:
    return {
        "kind": change.kind,
        "old_instrument_id": change.old_instrument_id,
        "old_exclusivity_key": change.old_exclusivity_key,
        "new_instrument_id": change.new_instrument_id,
        "new_exclusivity_key": change.new_exclusivity_key,
    }


def _registry_failure(
    failure_type: type[ProblemFailure],
    *,
    code: str,
    message: str,
    location: ProblemLocation | None = None,
    related_locations: Sequence[ProblemLocation] = (),
    details: Mapping[str, object] | None = None,
) -> ProblemFailure:
    return failure_type(
        [
            Problem(
                code=code,
                phase=ProblemPhase.CONFIGURATION,
                message=message,
                location=location,
                related_locations=tuple(related_locations),
                details={} if details is None else details,
            )
        ]
    )


def _registry_model_location(*path: str | int) -> ModelLocation:
    return ModelLocation(root="config_registry", path=path)


def _registry_storage_location(
    ref: str,
    *,
    run_id: str | None = None,
) -> StorageLocation:
    return StorageLocation(run_id=run_id, ref=ref)


__all__ = [
    "CandidateConfigRevisionSource",
    "ConfigRegistryEntrySnapshot",
    "ConfigRegistryMutationResult",
    "ConfigRegistryPageSnapshot",
    "ConfigRegistryRepository",
    "ConfigRegistryUnitOfWork",
    "ConfigRegistryUnitOfWorkFactory",
    "ConfigRevision",
    "ConfigRevisionSource",
    "DirectConfigRevisionSource",
    "InstrumentInventoryMigrationDelta",
    "InstrumentInventoryMigrationPlan",
    "load_config_registry_entry_snapshot",
    "load_config_registry_page",
    "plan_instrument_inventory_migration",
    "resolve_config_registry_config_source",
    "save_config_revision",
]
