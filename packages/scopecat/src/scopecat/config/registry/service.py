"""Configuration-registry use cases and persistence ports.

The registry stores named configuration snapshots under the project-local
``config-registry`` tree. Its append-only activation log projects the active
entry for later runs and supplies an independent history view for clients that
want to select an earlier exact entry.
Revisions can be saved directly from a
``ConfigProfileSnapshot`` or from a candidate configuration.

Runs started from a registry entry carry source coordinates on the durable run
snapshot. Reporting code can then show which registry selector and entry were
used without mixing run lifecycle data into the config snapshot. Candidate
evidence is verified and frozen when the revision is saved; later events do not
retroactively revoke committed entries.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from scopecat.config.candidate_merges import (
    merge_common_base_parameter_proposals,
)
from scopecat.config.candidates import (
    CandidateConfig,
    resolve_candidate_config_from_snapshot,
)
from scopecat.config.contexts import context_value_origins, validate_context_config
from scopecat.config.drafts import ConfigDraft, ConfigDraftCheckResult
from scopecat.config.parameter_updates import ParameterUpdate
from scopecat.config.profile_validation import validate_config_profile
from scopecat.config.registry.ports import (
    ConfigRegistryRepository,
    ConfigRegistryUnitOfWork,
    ConfigRegistryUnitOfWorkFactory,
)
from scopecat.config.registry.records import (
    CalibrationCohortMergeRegistrySource,
    CandidateAcceptance,
    CandidateConfigRegistrySource,
    ConfigCompositionPolicyRef,
    ConfigRegistryActivationPage,
    ConfigRegistryActivationRecord,
    ConfigRegistryEntry,
    ContextConfigRegistrySource,
    DirectConfigRegistrySource,
    ManualConfigDraftRegistrySource,
    ResolvedCalibrationCohortMergeContribution,
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
    ConfigContentHash,
    ConfigProfileSnapshot,
    config_content_equal,
    config_content_hash,
)
from scopecat.records.config_context import ConfigContextMetadata, ConfigContextRef
from scopecat.records.content import ContentEntry, Sha256ContentHash
from scopecat.records.parameter import ParameterSnapshot
from scopecat.records.parameter_change import (
    ParameterChangeProposal,
    ParameterValueDelta,
)
from scopecat.records.run import (
    ConfigRegistryRunConfigSource,
    RunConfigSource,
)
from scopecat.records.sample import SampleBinding
from scopecat.runs.refs import record_content_ref
from scopecat.runs.repository import RunRepository

ACTIVE_CONFIG_REGISTRY_ENTRY_SELECTOR = "active"
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
class ManualConfigDraftRevisionSource:
    base_entry_id: str
    base_config_content_hash: ConfigContentHash
    base_generation: int
    candidate_id: str
    updates: tuple[ParameterUpdate, ...]
    expected_result_content_hash: ConfigContentHash


@dataclass(frozen=True, slots=True)
class CandidateConfigRevisionSource:
    run_id: str
    proposal_id: str
    acceptance: CandidateAcceptance


@dataclass(frozen=True, slots=True)
class CalibrationCohortMergeRevisionSource:
    """Individually verified contributions selected for one cell merge."""

    cohort_id: str
    spec_hash: Sha256ContentHash
    composition_policy_ref: ConfigCompositionPolicyRef
    merge_policy: Literal["common_base_cells_v1"]
    base_entry_id: str
    base_content_hash: ConfigContentHash
    base_generation: int
    candidate_id: str
    contributions: tuple[ResolvedCalibrationCohortMergeContribution, ...]
    expected_result_content_hash: ConfigContentHash
    automatic_publication_policy_id: str | None = None
    automatic_publication_policy_version: str | None = None
    automatic_publication_policy_fingerprint: Sha256ContentHash | None = None


type ConfigRevisionSource = (
    DirectConfigRevisionSource
    | ManualConfigDraftRevisionSource
    | CandidateConfigRevisionSource
    | CalibrationCohortMergeRevisionSource
)


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
    latest_activation: ConfigRegistryActivationRecord | None = None


@dataclass(frozen=True, slots=True)
class ConfigRegistryPageSnapshot:
    entries: tuple[ConfigRegistryEntry, ...]
    activation: ConfigRegistryActivationRecord | None
    next_cursor: int | None = None


@dataclass(frozen=True, slots=True)
class ActiveConfigRegistrySnapshot:
    entry: ConfigRegistryEntry
    activation: ConfigRegistryActivationRecord
    config: ConfigProfileSnapshot


@dataclass(frozen=True, slots=True)
class ConfigRegistryMutationResult:
    """Committed registry facts used to publish matching project events."""

    entry: ConfigRegistryEntry
    activation: ConfigRegistryActivationRecord | None = None
    saved: bool = False
    activated: bool = False
    deltas: tuple[ParameterValueDelta, ...] = ()


@dataclass(frozen=True, slots=True)
class ManualConfigDraftResult:
    """One daemon-authoritative check against an observed active entry."""

    base_entry: ConfigRegistryEntry
    base_generation: int
    check: ConfigDraftCheckResult


def preview_manual_config_draft(
    *,
    unit_of_work: ConfigRegistryUnitOfWorkFactory,
    base_entry_id: str,
    base_config_content_hash: ConfigContentHash,
    base_generation: int,
    candidate_id: str,
    updates: Sequence[ParameterUpdate],
) -> ManualConfigDraftResult:
    """Check transient typed edits without committing registry state."""

    with unit_of_work() as work:
        return _check_manual_config_draft_locked(
            work=work,
            base_entry_id=base_entry_id,
            base_config_content_hash=base_config_content_hash,
            base_generation=base_generation,
            candidate_id=candidate_id,
            updates=updates,
        )


def _prepare_manual_config_draft_locked(
    *,
    work: ConfigRegistryUnitOfWork,
    source: ManualConfigDraftRevisionSource,
) -> tuple[
    ConfigProfileSnapshot,
    ManualConfigDraftRegistrySource,
    tuple[ParameterValueDelta, ...],
]:
    result = _check_manual_config_draft_locked(
        work=work,
        base_entry_id=source.base_entry_id,
        base_config_content_hash=source.base_config_content_hash,
        base_generation=source.base_generation,
        candidate_id=source.candidate_id,
        updates=source.updates,
    )
    if not result.check.ok:
        raise CheckFailed(result.check.problems)
    candidate = result.check.candidate
    assert candidate is not None
    result_content_hash = config_content_hash(candidate)
    if result_content_hash != source.expected_result_content_hash:
        raise _registry_failure(
            Conflict,
            code="config_registry.config_draft_result_changed",
            message="config draft result changed since it was previewed",
            location=_registry_model_location("expected_result_content_hash"),
            details={
                "expected_content_hash": source.expected_result_content_hash,
                "actual_content_hash": result_content_hash,
            },
        )
    return (
        candidate,
        ManualConfigDraftRegistrySource(
            base_entry_id=result.base_entry.id,
            base_config_content_hash=result.base_entry.content_hash,
            base_registry_generation=result.base_generation,
        ),
        result.check.deltas,
    )


def _check_manual_config_draft_locked(
    *,
    work: ConfigRegistryUnitOfWork,
    base_entry_id: str,
    base_config_content_hash: ConfigContentHash,
    base_generation: int,
    candidate_id: str,
    updates: Sequence[ParameterUpdate],
) -> ManualConfigDraftResult:
    activation = _load_active_config_registry_activation_locked(work.registry)
    _require_expected_generation(
        activation,
        base_generation,
        active_ref=work.registry.active_ref,
    )
    if (
        activation.entry_id != base_entry_id
        or activation.entry_content_hash != base_config_content_hash
    ):
        raise _registry_failure(
            Conflict,
            code="config_registry.config_draft_base_changed",
            message="config draft base is no longer the active entry",
            location=_registry_model_location("base_entry_id"),
            related_locations=(_registry_storage_location(work.registry.active_ref),),
            details={
                "expected_entry_id": base_entry_id,
                "actual_entry_id": activation.entry_id,
                "expected_content_hash": base_config_content_hash,
                "actual_content_hash": activation.entry_content_hash,
            },
        )
    loaded = _load_config_registry_entry_locked(entry_id=base_entry_id, work=work)
    _validate_active_entry_identity(work.registry, activation, loaded.entry)
    check = ConfigDraft(loaded.config).apply(*updates).check(candidate_id=candidate_id)
    return ManualConfigDraftResult(
        base_entry=loaded.entry,
        base_generation=activation.generation,
        check=check,
    )


def publish_config_revision(
    *,
    revision: ConfigRevision,
    unit_of_work: ConfigRegistryUnitOfWorkFactory,
    expected_generation: int,
) -> ConfigRegistryMutationResult:
    """Save one immutable revision and select it in the same transaction."""

    _validate_config_revision(revision)
    with unit_of_work() as work:
        saved = _save_config_revision_locked(
            revision=revision,
            work=work,
        )
        activated = _activate_config_registry_entry_locked(
            entry_id=saved.entry.id,
            work=work,
            actor=revision.actor,
            note=revision.note,
            expected_generation=expected_generation,
        )
        return ConfigRegistryMutationResult(
            entry=saved.entry,
            activation=activated.activation,
            saved=saved.saved,
            activated=activated.activated,
            deltas=saved.deltas,
        )


def publish_instrument_inventory_migration_revision(
    *,
    revision: ConfigRevision,
    declared: Sequence[InstrumentInventoryMigrationDelta],
    unit_of_work: ConfigRegistryUnitOfWorkFactory,
    expected_generation: int,
) -> ConfigRegistryMutationResult:
    """Publish a declared destructive change to the instrument inventory."""

    _validate_config_revision(revision)
    source = revision.source
    if not isinstance(source, DirectConfigRevisionSource):
        raise _registry_failure(
            CheckFailed,
            code="config_registry.inventory_migration_requires_direct_revision",
            message="instrument inventory migrations require a direct config revision",
            location=_registry_model_location("revision", "source"),
        )
    with unit_of_work() as work:
        current_activation = _read_latest_activation(work.registry)
        _require_expected_generation(
            current_activation,
            expected_generation,
            active_ref=work.registry.active_ref,
        )
        if current_activation is None:
            raise _registry_failure(
                NotFound,
                code="config_registry.no_active_entry",
                message="config registry has no active entry",
                location=_registry_model_location("active"),
            )
        current = _load_config_registry_entry_locked(
            entry_id=current_activation.entry_id,
            work=work,
        )
        _validate_active_entry_identity(
            work.registry,
            current_activation,
            current.entry,
        )
        plan = plan_instrument_inventory_migration(
            current=current.config,
            target=source.config,
            declared=declared,
        )
        saved = _save_config_revision_locked(
            revision=revision,
            work=work,
        )
        activated = _activate_instrument_inventory_migration_locked(
            entry_id=saved.entry.id,
            work=work,
            actor=revision.actor,
            note=revision.note,
            expected_generation=expected_generation,
            plan=plan,
        )
        return ConfigRegistryMutationResult(
            entry=saved.entry,
            activation=activated.activation,
            saved=saved.saved,
            activated=activated.activated,
        )


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
    elif isinstance(source, ManualConfigDraftRevisionSource):
        config, entry_source, deltas = _prepare_manual_config_draft_locked(
            work=work,
            source=source,
        )
        entry_id = _required_revision_entry_id(revision)
    elif isinstance(source, CandidateConfigRevisionSource):
        validated = _validate_candidate_source_records(
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
    else:
        config, entry_source, deltas = _prepare_calibration_cohort_merge_locked(
            work=work,
            source=source,
        )
        entry_id = _required_revision_entry_id(revision)
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
    elif isinstance(revision.source, CalibrationCohortMergeRevisionSource):
        source = revision.source
        _validate_required_text(source.cohort_id, field="cohort_id")
        _validate_required_text(source.base_entry_id, field="base_entry_id")
        _validate_required_text(source.candidate_id, field="candidate_id")
        if source.base_generation < 1:
            raise _registry_failure(
                CheckFailed,
                code="config_registry.calibration_merge_generation_invalid",
                message="calibration merge base generation must be positive",
                location=_registry_model_location("revision", "source"),
            )
        if not 1 <= len(source.contributions) <= 200:
            raise _registry_failure(
                CheckFailed,
                code="config_registry.calibration_merge_contributions_invalid",
                message=("calibration merge requires between 1 and 200 contributions"),
                location=_registry_model_location(
                    "revision",
                    "source",
                    "contributions",
                ),
            )


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


def _validate_candidate_source_records(
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


def _prepare_calibration_cohort_merge_locked(
    *,
    work: ConfigRegistryUnitOfWork,
    source: CalibrationCohortMergeRevisionSource,
) -> tuple[
    ConfigProfileSnapshot,
    CalibrationCohortMergeRegistrySource,
    tuple[ParameterValueDelta, ...],
]:
    """Resolve exact proposal records and compose their common active base."""

    activation = _load_active_config_registry_activation_locked(work.registry)
    _require_expected_generation(
        activation,
        source.base_generation,
        active_ref=work.registry.active_ref,
    )
    if (
        activation.entry_id != source.base_entry_id
        or activation.entry_content_hash != source.base_content_hash
    ):
        raise _registry_failure(
            Conflict,
            code="config_registry.calibration_merge_base_changed",
            message="calibration merge base is no longer the active entry",
            location=_registry_model_location(
                "revision",
                "source",
                "base_entry_id",
            ),
            related_locations=(_registry_storage_location(work.registry.active_ref),),
            details={
                "expected_entry_id": source.base_entry_id,
                "actual_entry_id": activation.entry_id,
                "expected_content_hash": source.base_content_hash,
                "actual_content_hash": activation.entry_content_hash,
            },
        )
    base = _load_config_registry_entry_locked(
        entry_id=source.base_entry_id,
        work=work,
    )
    _validate_active_entry_identity(work.registry, activation, base.entry)

    proposals = tuple(
        _load_calibration_merge_proposal(
            storage=work.runs,
            contribution=contribution,
        )
        for contribution in source.contributions
    )
    result = merge_common_base_parameter_proposals(
        proposals,
        base_config=base.config,
        candidate_id=source.candidate_id,
    )
    if result.content_hash != source.expected_result_content_hash:
        raise _registry_failure(
            Conflict,
            code="config_registry.calibration_merge_result_changed",
            message="calibration merge result changed since it was evaluated",
            location=_registry_model_location(
                "revision",
                "source",
                "expected_result_content_hash",
            ),
            details={
                "expected_content_hash": source.expected_result_content_hash,
                "actual_content_hash": result.content_hash,
            },
        )
    return (
        result.config,
        CalibrationCohortMergeRegistrySource(
            cohort_id=source.cohort_id,
            spec_hash=source.spec_hash,
            automatic_publication_policy_id=(source.automatic_publication_policy_id),
            automatic_publication_policy_version=(
                source.automatic_publication_policy_version
            ),
            automatic_publication_policy_fingerprint=(
                source.automatic_publication_policy_fingerprint
            ),
            composition_policy_ref=source.composition_policy_ref,
            merge_policy=source.merge_policy,
            base_entry_id=base.entry.id,
            base_config_content_hash=base.entry.content_hash,
            base_registry_generation=activation.generation,
            candidate_id=source.candidate_id,
            contributions=source.contributions,
        ),
        result.deltas,
    )


def _load_calibration_merge_proposal(
    *,
    storage: RunRepository,
    contribution: ResolvedCalibrationCohortMergeContribution,
) -> ParameterChangeProposal:
    proof = contribution.proof
    proposal_record = _require_run_record(
        storage=storage,
        run_id=proof.baseline_run_id,
        record_id=proof.proposal_id,
        kind="parameter_change_proposal",
    )
    proposal_ref = record_content_ref(
        record_id=proposal_record.id,
        kind=proposal_record.kind,
    )
    proposal = storage.read_model(
        proof.baseline_run_id,
        proposal_ref,
        ParameterChangeProposal,
    )
    if (
        proposal.id != proof.proposal_id
        or proposal.source_run_id != proof.baseline_run_id
        or proposal.analysis_record_id != proof.fit_analysis_record_id
    ):
        raise _registry_failure(
            DataIntegrityError,
            code="config_registry.calibration_merge_proposal_mismatch",
            message="calibration contribution does not match its proposal record",
            location=_registry_storage_location(
                proposal_ref,
                run_id=proof.baseline_run_id,
            ),
            related_locations=(
                _registry_model_location(
                    "revision",
                    "source",
                    "contributions",
                    contribution.member_id,
                ),
            ),
            details={
                "member_id": contribution.member_id,
                "proposal_id": proof.proposal_id,
            },
        )
    return proposal


def load_config_registry_page(
    *,
    limit: int,
    before: int | None,
    unit_of_work: ConfigRegistryUnitOfWorkFactory,
) -> ConfigRegistryPageSnapshot:
    """Read one newest-first registry page and the active projection."""

    with unit_of_work() as work:
        page = work.registry.list_entry_page(limit=limit, before=before)
        activation = _read_latest_activation(work.registry)
        if activation is not None:
            loaded = _load_config_registry_entry_locked(
                entry_id=activation.entry_id,
                work=work,
            )
            _validate_active_entry_identity(
                work.registry,
                activation,
                loaded.entry,
            )
        return ConfigRegistryPageSnapshot(
            entries=page.items,
            activation=activation,
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
            latest_activation=_latest_entry_activation(work.registry, loaded.entry),
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


def load_active_config_registry_snapshot(
    *,
    unit_of_work: ConfigRegistryUnitOfWorkFactory,
) -> ActiveConfigRegistrySnapshot:
    """Read the active head and its immutable config in one transaction."""

    with unit_of_work() as work:
        activation = _load_active_config_registry_activation_locked(work.registry)
        loaded = _load_config_registry_entry_locked(
            entry_id=activation.entry_id,
            work=work,
        )
        _validate_active_entry_identity(work.registry, activation, loaded.entry)
        return ActiveConfigRegistrySnapshot(
            entry=loaded.entry,
            activation=activation,
            config=loaded.config,
        )


def resolve_config_registry_config_source(
    *, selector: str, unit_of_work: ConfigRegistryUnitOfWorkFactory
) -> tuple[ConfigProfileSnapshot, RunConfigSource]:
    if selector != ACTIVE_CONFIG_REGISTRY_ENTRY_SELECTOR:
        _validate_entry_id(selector)
    with unit_of_work() as work:
        if selector == ACTIVE_CONFIG_REGISTRY_ENTRY_SELECTOR:
            return _resolve_active_config_registry_config_source_locked(work=work)
        return _resolve_entry_config_registry_config_source_locked(
            selector=selector,
            work=work,
        )


def activate_config_registry_entry(
    *,
    entry_id: str,
    unit_of_work: ConfigRegistryUnitOfWorkFactory,
    actor: str,
    expected_generation: int,
    note: str = "",
) -> ConfigRegistryMutationResult:
    _validate_entry_id(entry_id)
    _validate_required_text(actor, field="actor")
    with unit_of_work() as work:
        return _activate_config_registry_entry_locked(
            entry_id=entry_id,
            work=work,
            actor=actor,
            expected_generation=expected_generation,
            note=note,
        )


def _activate_config_registry_entry_locked(
    *,
    entry_id: str,
    work: ConfigRegistryUnitOfWork,
    actor: str,
    expected_generation: int | None,
    note: str,
) -> ConfigRegistryMutationResult:
    return _commit_config_registry_activation_locked(
        entry_id=entry_id,
        work=work,
        actor=actor,
        expected_generation=expected_generation,
        note=note,
        inventory_migration=None,
    )


def _activate_instrument_inventory_migration_locked(
    *,
    entry_id: str,
    work: ConfigRegistryUnitOfWork,
    actor: str,
    expected_generation: int,
    note: str,
    plan: InstrumentInventoryMigrationPlan,
) -> ConfigRegistryMutationResult:
    return _commit_config_registry_activation_locked(
        entry_id=entry_id,
        work=work,
        actor=actor,
        expected_generation=expected_generation,
        note=note,
        inventory_migration=plan,
    )


def _commit_config_registry_activation_locked(
    *,
    entry_id: str,
    work: ConfigRegistryUnitOfWork,
    actor: str,
    expected_generation: int | None,
    note: str,
    inventory_migration: InstrumentInventoryMigrationPlan | None,
) -> ConfigRegistryMutationResult:
    current_activation = _read_latest_activation(work.registry)
    if expected_generation is not None:
        _require_expected_generation(
            current_activation,
            expected_generation,
            active_ref=work.registry.active_ref,
        )
    loaded = _load_config_registry_entry_locked(
        entry_id=entry_id,
        work=work,
    )
    entry = loaded.entry
    if isinstance(entry.source, ContextConfigRegistrySource):
        raise _registry_failure(
            Conflict,
            code="config_registry.context_not_global_default",
            message=(
                "parameter contexts are selected per run, "
                "not activated as the lab default"
            ),
            location=_registry_model_location("entry_id"),
            details={"entry_id": entry.id},
        )
    prior_activation = _latest_entry_activation(work.registry, entry)
    if prior_activation is None:
        _validate_derived_entry_base(current_activation, entry, work)
    if current_activation is not None and current_activation.entry_id == entry.id:
        _validate_active_entry_identity(work.registry, current_activation, entry)
        return ConfigRegistryMutationResult(
            entry=entry,
            activation=current_activation,
        )
    if current_activation is not None:
        current = _load_config_registry_entry_locked(
            entry_id=current_activation.entry_id,
            work=work,
        )
        _validate_active_entry_identity(
            work.registry,
            current_activation,
            current.entry,
        )
        if inventory_migration is None:
            _require_stable_instrument_exclusivity_keys(
                current=current.config,
                candidate=loaded.config,
            )
    previous_entry_id = (
        current_activation.entry_id if current_activation is not None else None
    )
    previous_content_hash = (
        current_activation.entry_content_hash
        if current_activation is not None
        else None
    )
    current_generation = (
        0 if current_activation is None else current_activation.generation
    )
    generation = current_generation + 1
    record = ConfigRegistryActivationRecord(
        generation=generation,
        action=("activation" if inventory_migration is None else "inventory_migration"),
        entry_id=entry.id,
        entry_content_hash=entry.content_hash,
        restored_from_generation=(
            prior_activation.generation if prior_activation is not None else None
        ),
        previous_entry_id=previous_entry_id,
        previous_entry_content_hash=previous_content_hash,
        actor=actor,
        note=note,
    )
    work.registry.commit_activation(
        expected_generation=current_generation,
        record=record,
    )
    return ConfigRegistryMutationResult(
        entry=entry,
        activation=record,
        activated=True,
    )


def load_config_registry_activation(
    *,
    generation: int,
    unit_of_work: ConfigRegistryUnitOfWorkFactory,
) -> ConfigRegistryActivationRecord:
    with unit_of_work() as work:
        return work.registry.read_activation(generation)


def load_config_registry_activation_page(
    *,
    limit: int,
    before: int | None,
    unit_of_work: ConfigRegistryUnitOfWorkFactory,
) -> ConfigRegistryActivationPage:
    with unit_of_work() as work:
        return work.registry.list_activation_page(limit=limit, before=before)


def _load_active_config_registry_activation_locked(
    repository: ConfigRegistryRepository,
) -> ConfigRegistryActivationRecord:
    activation = repository.read_latest_activation()
    if activation is None:
        raise _registry_failure(
            NotFound,
            code="config_registry.no_active_entry",
            message="config registry has no active entry",
            location=_registry_model_location("active"),
        )
    return activation


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


def _resolve_active_config_registry_config_source_locked(
    *, work: ConfigRegistryUnitOfWork
) -> tuple[ConfigProfileSnapshot, RunConfigSource]:
    activation = _load_active_config_registry_activation_locked(work.registry)
    loaded = _load_config_registry_entry_locked(
        entry_id=activation.entry_id,
        work=work,
    )
    entry = loaded.entry
    _validate_active_entry_identity(work.registry, activation, entry)
    source = ConfigRegistryRunConfigSource(
        selector=ACTIVE_CONFIG_REGISTRY_ENTRY_SELECTOR,
        entry_id=entry.id,
        config_ref=entry.config_ref,
        content_hash=entry.content_hash,
        registry_generation=activation.generation,
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


def _read_latest_activation(
    repository: ConfigRegistryRepository,
) -> ConfigRegistryActivationRecord | None:
    return repository.read_latest_activation()


def _require_expected_generation(
    activation: ConfigRegistryActivationRecord | None,
    expected_generation: int,
    *,
    active_ref: str,
) -> None:
    current_generation = 0 if activation is None else activation.generation
    if expected_generation == current_generation:
        return
    raise _registry_failure(
        Conflict,
        code="config_registry.conflict",
        message="config registry active state changed",
        location=_registry_model_location("expected_generation"),
        related_locations=(_registry_storage_location(active_ref),),
        details={
            "expected_generation": expected_generation,
            "actual_generation": current_generation,
        },
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


def _validate_active_entry_identity(
    repository: ConfigRegistryRepository,
    activation: ConfigRegistryActivationRecord,
    entry: ConfigRegistryEntry,
) -> None:
    if (
        activation.entry_id == entry.id
        and activation.entry_content_hash == entry.content_hash
    ):
        return
    raise _registry_failure(
        DataIntegrityError,
        code="config_registry.active_content_mismatch",
        message="active config registry state does not match its entry",
        location=_registry_storage_location(repository.active_ref),
        related_locations=(_registry_storage_location(repository.entry_ref(entry.id)),),
        details={"entry_id": entry.id},
    )


def _latest_entry_activation(
    repository: ConfigRegistryRepository, entry: ConfigRegistryEntry
) -> ConfigRegistryActivationRecord | None:
    activation = repository.read_latest_entry_activation(entry.id)
    if activation is not None:
        _validate_active_entry_identity(repository, activation, entry)
    return activation


def _validate_derived_entry_base(
    activation: ConfigRegistryActivationRecord | None,
    entry: ConfigRegistryEntry,
    work: ConfigRegistryUnitOfWork,
) -> None:
    if activation is None:
        return
    if activation.entry_id == entry.id:
        _validate_active_entry_identity(work.registry, activation, entry)
        return
    active = _load_config_registry_entry_locked(
        entry_id=activation.entry_id,
        work=work,
    )
    active_entry = active.entry
    _validate_active_entry_identity(work.registry, activation, active_entry)
    if isinstance(
        entry.source,
        (
            CalibrationCohortMergeRegistrySource,
            CandidateConfigRegistrySource,
            ManualConfigDraftRegistrySource,
        ),
    ):
        base_content_hash = entry.source.base_config_content_hash
    else:
        return
    if base_content_hash == active_entry.content_hash:
        return
    raise _registry_failure(
        Conflict,
        code="config_registry.stale_candidate",
        message="candidate config was based on a different active config",
        location=_registry_model_location(
            "entries",
            entry.id,
            "source",
            "base_config_content_hash",
        ),
        related_locations=(_registry_storage_location(work.registry.active_ref),),
        details={
            "entry_id": entry.id,
            "candidate_base_content_hash": base_content_hash,
            "active_content_hash": active_entry.content_hash,
        },
    )


def _require_valid_config(config: ConfigProfileSnapshot) -> None:
    problems = validate_config_profile(config)
    if bool(problems):
        raise CheckFailed(problems)


def plan_instrument_inventory_migration(
    *,
    current: ConfigProfileSnapshot,
    target: ConfigProfileSnapshot,
    declared: Sequence[InstrumentInventoryMigrationDelta],
) -> InstrumentInventoryMigrationPlan:
    """Match explicit intent to every destructive inventory change."""

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


def _require_stable_instrument_exclusivity_keys(
    *,
    current: ConfigProfileSnapshot,
    candidate: ConfigProfileSnapshot,
) -> None:
    """Reserve removal and rekeying for an explicit inventory migration."""

    current_by_id = {
        instrument.id: instrument.exclusivity_key
        for instrument in current.instrument_registry.instruments
    }
    for instrument in candidate.instrument_registry.instruments:
        previous_key = current_by_id.get(instrument.id)
        if previous_key is None or previous_key == instrument.exclusivity_key:
            continue
        raise _registry_failure(
            Conflict,
            code="config_registry.instrument_exclusivity_key_changed",
            message=(
                "an existing logical instrument cannot change its exclusivity key"
            ),
            location=_registry_model_location(
                "config",
                "system",
                "instrument_registry",
                "instruments",
                instrument.id,
                "exclusivity_key",
            ),
            details={"instrument_id": instrument.id},
        )
    candidate_keys = {
        instrument.exclusivity_key
        for instrument in candidate.instrument_registry.instruments
    }
    removed_keys = sorted(set(current_by_id.values()) - candidate_keys)
    if removed_keys:
        raise _registry_failure(
            Conflict,
            code="config_registry.instrument_exclusivity_key_removed",
            message=(
                "ordinary configuration activation cannot remove or replace "
                "an instrument exclusivity key"
            ),
            location=_registry_model_location(
                "config",
                "system",
                "instrument_registry",
            ),
            details={
                "instrument_ids": sorted(
                    instrument_id
                    for instrument_id, key in current_by_id.items()
                    if key in removed_keys
                )
            },
        )


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
    "ACTIVE_CONFIG_REGISTRY_ENTRY_SELECTOR",
    "ActiveConfigRegistrySnapshot",
    "CalibrationCohortMergeRevisionSource",
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
    "ManualConfigDraftResult",
    "ManualConfigDraftRevisionSource",
    "activate_config_registry_entry",
    "load_active_config_registry_snapshot",
    "load_config_registry_activation",
    "load_config_registry_activation_page",
    "load_config_registry_entry_snapshot",
    "load_config_registry_page",
    "plan_instrument_inventory_migration",
    "preview_manual_config_draft",
    "publish_config_revision",
    "publish_instrument_inventory_migration_revision",
    "resolve_config_registry_config_source",
]


def save_config_context(
    *,
    entry_id: str,
    base: ConfigContextRef,
    sample: SampleBinding,
    working_point_id: str,
    label: str,
    parameters: ParameterSnapshot | None,
    actor: str,
    note: str,
    unit_of_work: ConfigRegistryUnitOfWorkFactory,
) -> ConfigRegistryEntrySnapshot:
    """Save an alternative in the existing registry without an activation."""
    _validate_entry_id(entry_id)
    _validate_required_text(actor, field="actor")
    with unit_of_work() as work:
        loaded = _load_config_registry_entry_locked(entry_id=base.entry_id, work=work)
        if loaded.entry.content_hash != base.content_hash:
            raise ValueError("context base does not match the exact registry revision")
        config = loaded.config.model_copy(
            update={
                "parameter_snapshot": loaded.config.parameter_snapshot
                if parameters is None
                else parameters
            }
        )
        validate_context_config(config)
        selected_ref = ConfigContextRef(
            entry_id=entry_id, content_hash=config_content_hash(config)
        )
        inherited = (
            loaded.entry.source.context.value_origins
            if isinstance(loaded.entry.source, ContextConfigRegistrySource)
            else ()
        )
        source = ContextConfigRegistrySource(
            context=ConfigContextMetadata(
                sample=sample,
                working_point_id=working_point_id,
                label=label,
                base=base,
                value_origins=context_value_origins(
                    config,
                    base=loaded.config.parameter_snapshot,
                    base_ref=base,
                    selected_ref=selected_ref,
                    inherited=inherited,
                ),
            )
        )
        entry = ConfigRegistryEntry(
            id=entry_id,
            config_ref=work.registry.config_ref(entry_id),
            content_hash=selected_ref.content_hash,
            source=source,
            actor=actor,
            note=note,
        )
        committed = _commit_revision_locked(
            repository=work.registry, requested_entry=entry, config=config
        )
        return ConfigRegistryEntrySnapshot(entry=committed.entry, config=config)
