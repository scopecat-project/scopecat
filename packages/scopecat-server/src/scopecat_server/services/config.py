"""Configuration registry application service."""

from __future__ import annotations

import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from threading import Lock

from scopecat.automation.calibrations import (
    CalibrationCohortMember,
    CalibrationConfigSourceRef,
    CalibrationSuccessPublication,
    CalibrationSuccessRef,
    calibration_freshness_fingerprint,
    calibration_target_sample_selectors,
)
from scopecat.automation.models import (
    AnalysisPublicationOutputRef,
    ProcedureStepAttempt,
    RunOutputRef,
)
from scopecat.config.changes import (
    PreparedParameterChangeApproval,
    load_parameter_change_proposal,
    prepare_parameter_change_approval,
)
from scopecat.config.contexts import (
    apply_context_overrides,
    context_value_origins,
    missing_context_values,
)
from scopecat.config.inventory import (
    InstrumentInventoryRekey,
    InstrumentInventoryRemoval,
    InstrumentInventoryRenameRekey,
)
from scopecat.config.registry import service as config_registry_service
from scopecat.config.registry.records import (
    CalibrationCohortMergeContribution,
    ConfigActivationOperation,
    ConfigCompositionEvidenceStepRef,
    ConfigPublishOperation,
    ConfigRegistryActivationRecord,
    ConfigRegistryEntry,
    ContextConfigRegistrySource,
    CrossRunCandidateAcceptance,
    ResolvedCalibrationCohortMergeContribution,
    ResolvedVerifiedParameterProposalProofV1,
)
from scopecat.config.registry.service import (
    publish_instrument_inventory_migration_revision,
)
from scopecat.config.structure import (
    ParameterStructurePlan,
    ParameterStructurePreview,
    preview_parameter_structure,
)
from scopecat.control.models import (
    DurableEventInput,
    InventoryMigrationBlocker,
    ResourceKey,
)
from scopecat.daemon.views import (
    ActiveConfigView,
    ConfigActivationPage,
    ConfigContextResolution,
    ConfigDraftPreview,
    ConfigEntryView,
    ConfigRegistryPage,
)
from scopecat.daemon.wire import (
    CalibrationCohortMergeRevisionSource,
    CalibrationPublicationCommand,
    CalibrationPublicationReceipt,
    CandidateConfigRevisionSource,
    ConfigActivationReceipt,
    ConfigContextResolveCommand,
    ConfigContextSaveCommand,
    ConfigDraftCommand,
    ConfigEntryActivationCommand,
    ConfigPublishCommand,
    ConfigPublishReceipt,
    DirectConfigRevisionSource,
    InstrumentInventoryMigrationCommand,
    InstrumentInventoryMigrationReceipt,
    ManualConfigDraftRevisionSource,
)
from scopecat.kernel.errors import (
    CheckFailed,
    Conflict,
    DataIntegrityError,
    NotFound,
)
from scopecat.project_state import ProjectStateServices
from scopecat.records.analysis import (
    ProjectAnalysisDecisionReference,
    ProjectAnalysisSubject,
)
from scopecat.records.config import config_content_hash
from scopecat.records.config_context import ContextRunConfigSource
from scopecat.records.run import (
    AnalysisCandidateRunConfigSource,
    ConfigRegistryRunConfigSource,
    RunSnapshot,
)

from scopecat_server.storage.sqlite.automation import (
    AutomationNotFound,
    SQLiteAutomationStore,
)
from scopecat_server.storage.sqlite.calibration_cohorts import (
    CalibrationCohortConflict,
    CalibrationCohortNotFound,
    SQLiteCalibrationCohortStore,
)
from scopecat_server.storage.sqlite.config_operations import SQLiteConfigOperationStore
from scopecat_server.storage.sqlite.config_registry import SQLiteConfigRegistryStore
from scopecat_server.storage.sqlite.control_plane import SQLiteControlPlane
from scopecat_server.storage.sqlite.run_repository import SQLiteRunRepository

from ..errors import BackendConflict, BackendNotFound
from ..instruments.actors import (
    InstrumentActorConflict,
    InstrumentActorRegistry,
    InstrumentActorShutdown,
)
from .analyses import AnalysisService
from .samples import SampleService


@dataclass(frozen=True, slots=True)
class _CalibrationMergeMemberProof:
    member: CalibrationCohortMember
    contribution: CalibrationCohortMergeContribution
    resolved: ResolvedCalibrationCohortMergeContribution
    succeeded_at: datetime
    approval: PreparedParameterChangeApproval


@dataclass(frozen=True, slots=True)
class _PreparedCalibrationMerge:
    revision_source: config_registry_service.CalibrationCohortMergeRevisionSource
    base_config_source: CalibrationConfigSourceRef
    members: tuple[_CalibrationMergeMemberProof, ...]


class ConfigService:
    """Own config-registry commands and their in-process serialization."""

    def __init__(
        self,
        *,
        control: SQLiteControlPlane,
        config_registry: SQLiteConfigRegistryStore,
        config_operations: SQLiteConfigOperationStore,
        runs: SQLiteRunRepository,
        services: ProjectStateServices,
        actors: InstrumentActorRegistry,
        analyses: AnalysisService,
        automation: SQLiteAutomationStore,
        calibration_cohorts: SQLiteCalibrationCohortStore,
        samples: SampleService,
    ) -> None:
        self._samples = samples
        self._control = control
        self._config_registry = config_registry
        self._config_operations = config_operations
        self._runs = runs
        self._services = services
        self._actors = actors
        self._analyses = analyses
        self._automation = automation
        self._calibration_cohorts = calibration_cohorts
        self._mutation_lock = Lock()

    def save_context(self, command: ConfigContextSaveCommand) -> ConfigEntryView:
        with self._mutation_lock, self._config_errors():
            try:
                selector = command.sample.model_copy(
                    update={"context_id": command.working_point_id}
                )
                sample = self._samples.resolve_bindings((selector,))[0]
                snapshot = config_registry_service.save_config_context(
                    entry_id=command.entry_id,
                    base=command.base,
                    sample=sample,
                    working_point_id=command.working_point_id,
                    label=command.label,
                    parameters=command.parameters,
                    structure_plan=command.structure_plan,
                    actor=command.actor,
                    note=command.note,
                    unit_of_work=self._config_registry.write_unit_of_work,
                )
                return ConfigEntryView(entry=snapshot.entry, config=snapshot.config)
            except ValueError as error:
                raise BackendConflict(str(error)) from error

    def preview_structure(
        self, plan: ParameterStructurePlan
    ) -> ParameterStructurePreview:
        with self._config_errors():
            saved = self.get_config_entry(plan.base.entry_id)
            try:
                return preview_parameter_structure(saved.config, plan)
            except ValueError as error:
                raise BackendConflict(str(error)) from error

    def resolve_context(
        self, command: ConfigContextResolveCommand
    ) -> ConfigContextResolution:
        with self._config_errors():
            try:
                saved = config_registry_service.load_config_registry_entry_snapshot(
                    entry_id=command.context.entry_id,
                    unit_of_work=self._config_registry.read_unit_of_work,
                )
                if (
                    saved.entry.content_hash != command.context.content_hash
                    or not isinstance(saved.entry.source, ContextConfigRegistrySource)
                ):
                    raise ValueError(
                        "context reference does not match a saved parameter context"
                    )
                active = self.get_active_config()
                resolved = apply_context_overrides(saved.config, command.overrides)
                metadata = saved.entry.source.context
                return ConfigContextResolution(
                    config=resolved,
                    config_source=ContextRunConfigSource(
                        context=command.context,
                        content_hash=config_content_hash(resolved),
                        lab_generation=active.activation.generation,
                        sample=metadata.sample,
                        overrides=command.overrides,
                    ),
                    value_origins=context_value_origins(
                        resolved,
                        base=saved.config.parameter_snapshot,
                        base_ref=command.context,
                        selected_ref=command.context,
                        inherited=metadata.value_origins,
                        overrides=command.overrides,
                    ),
                    missing_values=missing_context_values(resolved),
                )
            except ValueError as error:
                raise BackendConflict(str(error)) from error

    def get_config_registry(
        self,
        *,
        limit: int = 100,
        before: int | None = None,
    ) -> ConfigRegistryPage:
        with self._config_errors():
            snapshot = config_registry_service.load_config_registry_page(
                limit=limit,
                before=before,
                unit_of_work=self._config_registry.read_unit_of_work,
            )
            return ConfigRegistryPage(
                entries=snapshot.entries,
                activation=snapshot.activation,
                next_cursor=snapshot.next_cursor,
            )

    def get_config_activation_history(
        self,
        *,
        limit: int = 100,
        before: int | None = None,
    ) -> ConfigActivationPage:
        with self._config_errors():
            page = config_registry_service.load_config_registry_activation_page(
                limit=limit,
                before=before,
                unit_of_work=self._config_registry.read_unit_of_work,
            )
            return ConfigActivationPage(
                items=page.items,
                next_cursor=page.next_cursor,
            )

    def get_active_config(self) -> ActiveConfigView:
        with self._config_errors():
            snapshot = config_registry_service.load_active_config_registry_snapshot(
                unit_of_work=self._config_registry.read_unit_of_work
            )
            return ActiveConfigView(
                entry=snapshot.entry,
                activation=snapshot.activation,
                config=snapshot.config,
            )

    def get_config_entry(self, entry_id: str) -> ConfigEntryView:
        with self._config_errors():
            snapshot = config_registry_service.load_config_registry_entry_snapshot(
                entry_id=entry_id,
                unit_of_work=self._config_registry.read_unit_of_work,
            )
            return ConfigEntryView(
                entry=snapshot.entry,
                config=snapshot.config,
                latest_activation=snapshot.latest_activation,
            )

    def get_config_activation_operation(
        self,
        operation_id: str,
    ) -> ConfigActivationReceipt:
        with self._config_errors():
            receipt = self._config_operations.find(operation_id)
            if receipt is None:
                raise BackendNotFound(
                    f"config activation operation was not found: {operation_id}"
                )
            if not isinstance(receipt, ConfigActivationReceipt):
                raise BackendConflict(
                    f"config operation is not an activation: {operation_id}"
                )
            return receipt

    def get_config_publish_operation(
        self,
        operation_id: str,
    ) -> ConfigPublishReceipt:
        with self._config_errors():
            receipt = self._config_operations.find(operation_id)
            if receipt is None:
                raise BackendNotFound(
                    f"config publish operation was not found: {operation_id}"
                )
            if type(receipt) is not ConfigPublishReceipt:
                raise BackendConflict(
                    f"config operation is not a config publication: {operation_id}"
                )
            return receipt

    def get_calibration_publication_operation(
        self,
        operation_id: str,
    ) -> CalibrationPublicationReceipt:
        with self._config_errors():
            receipt = self._config_operations.find(operation_id)
            if receipt is None:
                raise BackendNotFound(
                    f"calibration publication operation was not found: {operation_id}"
                )
            if not isinstance(receipt, CalibrationPublicationReceipt):
                raise BackendConflict(
                    f"config operation is not a calibration publication: {operation_id}"
                )
            return receipt

    def publish_config(
        self,
        command: ConfigPublishCommand,
    ) -> ConfigPublishReceipt:
        """Publish one revision; candidate approval shares the same commit."""

        receipt = self._publish_revision(command)
        assert type(receipt) is ConfigPublishReceipt
        return receipt

    def publish_calibration(
        self,
        command: CalibrationPublicationCommand,
    ) -> CalibrationPublicationReceipt:
        """Publish one verified cohort and finalize it in the same commit."""

        receipt = self._publish_revision(command)
        assert isinstance(receipt, CalibrationPublicationReceipt)
        return receipt

    def _publish_revision(
        self,
        command: ConfigPublishCommand | CalibrationPublicationCommand,
    ) -> ConfigPublishReceipt | CalibrationPublicationReceipt:
        calibration_publication = isinstance(
            command,
            CalibrationPublicationCommand,
        )

        with self._mutation_lock, self._config_errors():
            with self._config_transaction() as transaction:
                connection, services = transaction
                existing = self._config_operations.find_in_transaction(
                    connection,
                    command.operation_id,
                )
                if existing is not None:
                    expected_type = (
                        CalibrationPublicationReceipt
                        if calibration_publication
                        else ConfigPublishReceipt
                    )
                    if (
                        type(existing) is not expected_type
                        or existing.operation.intent_hash != command.intent_hash
                    ):
                        raise BackendConflict(
                            "config operation id is already committed for a different "
                            f"intent: {command.operation_id}"
                        )
                    return existing
                source = command.source
                calibration_merge: _PreparedCalibrationMerge | None = None
                if calibration_publication:
                    assert isinstance(command, CalibrationPublicationCommand)
                    assert isinstance(source, CalibrationCohortMergeRevisionSource)
                    calibration_merge = self._prepare_calibration_merge(
                        connection,
                        command,
                    )
                    self._publish_calibration_merge_approvals(
                        connection,
                        calibration_merge,
                        actor=command.actor,
                    )
                elif isinstance(source, CandidateConfigRevisionSource):
                    if isinstance(source.acceptance, CrossRunCandidateAcceptance):
                        self._analyses.validate_candidate_verification(
                            source.acceptance.decision,
                            source_run_id=source.run_id,
                            proposal_id=source.proposal_id,
                        )
                    prepared = prepare_parameter_change_approval(
                        run_id=source.run_id,
                        selector=source.proposal_id,
                        services=self._services,
                        actor=command.actor,
                        note=command.note,
                    )
                    if prepared.publication is not None:
                        publication = self._runs.prepare_content_publication(
                            prepared.publication
                        )
                        self._runs.publish_prepared_content_in_transaction(
                            connection,
                            publication,
                        )
                        self._control.append_event_in_transaction(
                            connection,
                            DurableEventInput(
                                run_id=source.run_id,
                                kind="parameter_proposal_approved",
                                payload={
                                    "proposal_id": source.proposal_id,
                                    "actor": command.actor,
                                },
                                occurred_at=prepared.approval.approved_at,
                            ),
                        )
                result = config_registry_service.publish_config_revision(
                    revision=_config_revision(
                        command,
                        calibration_merge=calibration_merge,
                    ),
                    unit_of_work=services.config_registry,
                    expected_generation=command.expected_generation,
                )
                self._append_revision_events(connection, command, result)
                activation = result.activation
                assert activation is not None
                operation = ConfigPublishOperation(
                    operation_id=command.operation_id,
                    intent_hash=command.intent_hash,
                    source_intent_hash=command.source_intent_hash,
                    entry_id=command.entry_id,
                    expected_generation=command.expected_generation,
                    actor=command.actor,
                    note=command.note,
                    activation_generation=activation.generation,
                )
                if calibration_merge is None:
                    receipt: ConfigPublishReceipt | CalibrationPublicationReceipt = (
                        ConfigPublishReceipt(
                            operation=operation,
                            entry=result.entry,
                            deltas=result.deltas,
                            activation=activation,
                        )
                    )
                else:
                    calibration_successes = _calibration_successes(
                        calibration_merge,
                        operation=operation,
                        result_entry=result.entry,
                        activation=activation,
                    )
                    receipt = CalibrationPublicationReceipt(
                        operation=operation,
                        entry=result.entry,
                        deltas=result.deltas,
                        activation=activation,
                        calibration_successes=calibration_successes,
                    )
                self._config_operations.commit_in_transaction(connection, receipt)
                if isinstance(receipt, CalibrationPublicationReceipt):
                    for success in receipt.calibration_successes:
                        self._calibration_cohorts.insert_success_publication_in_transaction(
                            connection,
                            success,
                        )
                    assert isinstance(command, CalibrationPublicationCommand)
                    source = command.source
                    if source.automatic_publication is not None:
                        expected_revision = command.expected_finalization_revision
                        assert expected_revision is not None
                        self._calibration_cohorts.complete_publication_in_transaction(
                            connection,
                            cohort_id=source.cohort_id,
                            policy=source.automatic_publication,
                            expected_revision=expected_revision,
                            operation_id=operation.operation_id,
                            at=activation.recorded_at,
                        )
                self._calibration_cohorts.supersede_stale_publications_in_transaction(
                    connection,
                    active_generation=activation.generation,
                    at=activation.recorded_at,
                )
            return receipt

    def _prepare_calibration_merge(
        self,
        connection: sqlite3.Connection,
        command: CalibrationPublicationCommand,
    ) -> _PreparedCalibrationMerge:
        """Resolve every immutable proof before publishing any logical state."""

        source = command.source

        try:
            cohort = self._calibration_cohorts.read_in_transaction(
                connection,
                source.cohort_id,
            )
            members = self._calibration_cohorts.list_members_in_transaction(
                connection,
                source.cohort_id,
            )
        except CalibrationCohortNotFound as error:
            raise BackendConflict(
                "calibration merge must identify an exact durable cohort"
            ) from error
        base = cohort.spec.config_source
        if (
            cohort.spec_hash != source.spec_hash
            or cohort.spec.automatic_publication != source.automatic_publication
            or (
                source.automatic_publication is not None
                and source.automatic_publication.composition_policy
                != source.composition_policy_ref
            )
            or base.entry_id != source.base_entry_id
            or base.content_hash != source.base_content_hash
            or base.registry_generation != source.base_generation
            or command.expected_generation != base.registry_generation
        ):
            raise BackendConflict(
                "calibration merge cohort or base config does not match its source"
            )
        if source.automatic_publication is not None:
            expected_revision = command.expected_finalization_revision
            assert expected_revision is not None
            try:
                finalization = (
                    self._calibration_cohorts.read_finalization_in_transaction(
                        connection,
                        source.cohort_id,
                    )
                )
            except CalibrationCohortNotFound as error:
                raise BackendConflict(
                    "automatic calibration publication state was not found"
                ) from error
            if (
                finalization.policy != source.automatic_publication
                or finalization.base_config_source != base
                or finalization.state != "ready"
                or finalization.revision != expected_revision
                or finalization.available_at is None
                or finalization.available_at > datetime.now(UTC)
            ):
                raise BackendConflict(
                    "automatic calibration publication is not eligible"
                )

        contributions = {
            contribution.member_id: contribution
            for contribution in source.contributions
        }
        if set(contributions) != {member.spec.member_id for member in members}:
            raise BackendConflict(
                "calibration merge contributions must cover every cohort member"
            )

        proofs: list[_CalibrationMergeMemberProof] = []
        resolved: list[ResolvedCalibrationCohortMergeContribution] = []
        for member in members:
            contribution = contributions[member.spec.member_id]
            evidence_ref = contribution.proof.evidence_step
            if (
                evidence_ref.procedure_run_id != member.procedure_run_id
                or member.spec.definition.success_policy != "published_result"
            ):
                raise BackendConflict(
                    "calibration merge contribution does not match its cohort member"
                )
            try:
                parent = self._automation.read_run_in_transaction(
                    connection,
                    member.procedure_run_id,
                )
                evidence_step = self._automation.read_step_attempt_in_transaction(
                    connection,
                    evidence_ref.procedure_run_id,
                    evidence_ref.step_key,
                    evidence_ref.attempt,
                )
            except AutomationNotFound as error:
                raise BackendConflict(
                    "calibration merge contribution step proof was not found"
                ) from error
            if (
                parent.state != "closed"
                or parent.closure is None
                or parent.closure.status != "succeeded"
                or parent.request_key != member.request_key
                or parent.definition != member.spec.procedure
                or parent.intent != member.spec.intent
                or parent.samples
                != calibration_target_sample_selectors(member.spec.target)
            ):
                raise BackendConflict(
                    "calibration merge member procedure must be closed succeeded"
                )
            _, evidence_step_run_ids = _validate_calibration_evidence_step(
                evidence_step,
                evidence_ref,
                contribution.proof.decision,
            )
            analysis_run_ids = self._analyses.calibration_merge_verification_run_ids(
                contribution.proof.decision
            )
            if set(analysis_run_ids) != set(evidence_step_run_ids):
                raise BackendConflict(
                    "calibration merge evidence step and project analysis inputs "
                    "do not match"
                )
            try:
                evidence_runs = (
                    self._runs.read_snapshot_in_transaction(
                        connection,
                        analysis_run_ids[0],
                    ),
                    self._runs.read_snapshot_in_transaction(
                        connection,
                        analysis_run_ids[1],
                    ),
                )
                baseline_run, candidate_run = _resolve_calibration_evidence_runs(
                    evidence_runs,
                    base,
                )
                candidate_source = candidate_run.config_source
                assert isinstance(
                    candidate_source,
                    AnalysisCandidateRunConfigSource,
                )
                proposal = load_parameter_change_proposal(
                    run_id=baseline_run.run_id,
                    selector=candidate_source.proposal_id,
                    services=self._services,
                )
            except NotFound as error:
                raise BackendConflict(
                    "calibration merge run or proposal proof was not found"
                ) from error
            if (
                baseline_run.outcome is None
                or baseline_run.outcome.result != "succeeded"
                or candidate_run.outcome is None
                or candidate_run.outcome.result != "succeeded"
                or candidate_source.source_run_id != baseline_run.run_id
                or candidate_source.base_config_content_hash != base.content_hash
                or proposal.id != candidate_source.proposal_id
                or proposal.source_run_id != baseline_run.run_id
                or proposal.analysis_record_id != candidate_source.analysis_record_id
                or proposal.base_config_content_hash != base.content_hash
            ):
                raise BackendConflict(
                    "calibration merge proposal does not match its exact baseline"
                )
            self._analyses.validate_calibration_merge_verification(
                contribution.proof.decision,
                source_run_id=baseline_run.run_id,
                fit_analysis_record_id=candidate_source.analysis_record_id,
                proposal_id=proposal.id,
                candidate_run_id=candidate_run.run_id,
                base_config_content_hash=base.content_hash,
            )
            resolved_contribution = ResolvedCalibrationCohortMergeContribution(
                member_id=member.spec.member_id,
                proof=ResolvedVerifiedParameterProposalProofV1(
                    evidence_step=evidence_ref,
                    baseline_run_id=baseline_run.run_id,
                    fit_analysis_record_id=candidate_source.analysis_record_id,
                    proposal_id=proposal.id,
                    candidate_run_id=candidate_run.run_id,
                    decision=contribution.proof.decision,
                ),
                result_input_fingerprint=contribution.result_input_fingerprint,
            )
            approval = prepare_parameter_change_approval(
                run_id=baseline_run.run_id,
                selector=proposal.id,
                services=self._services,
                actor=command.actor,
                note=command.note,
            )
            resolved.append(resolved_contribution)
            proofs.append(
                _CalibrationMergeMemberProof(
                    member=member,
                    contribution=contribution,
                    resolved=resolved_contribution,
                    succeeded_at=parent.closure.closed_at,
                    approval=approval,
                )
            )

        return _PreparedCalibrationMerge(
            revision_source=(
                config_registry_service.CalibrationCohortMergeRevisionSource(
                    cohort_id=source.cohort_id,
                    spec_hash=source.spec_hash,
                    automatic_publication_policy_id=(
                        None
                        if source.automatic_publication is None
                        else source.automatic_publication.id
                    ),
                    automatic_publication_policy_version=(
                        None
                        if source.automatic_publication is None
                        else source.automatic_publication.version
                    ),
                    automatic_publication_policy_fingerprint=(
                        None
                        if source.automatic_publication is None
                        else source.automatic_publication.fingerprint
                    ),
                    composition_policy_ref=source.composition_policy_ref,
                    merge_policy=source.merge_policy,
                    base_entry_id=source.base_entry_id,
                    base_content_hash=source.base_content_hash,
                    base_generation=source.base_generation,
                    candidate_id=source.candidate_id,
                    contributions=tuple(resolved),
                    expected_result_content_hash=(source.expected_result_content_hash),
                )
            ),
            base_config_source=base,
            members=tuple(proofs),
        )

    def _publish_calibration_merge_approvals(
        self,
        connection: sqlite3.Connection,
        merge: _PreparedCalibrationMerge,
        *,
        actor: str,
    ) -> None:
        for proof in merge.members:
            publication = proof.approval.publication
            if publication is None:
                continue
            prepared = self._runs.prepare_content_publication(publication)
            self._runs.publish_prepared_content_in_transaction(connection, prepared)
            self._control.append_event_in_transaction(
                connection,
                DurableEventInput(
                    run_id=proof.resolved.proof.baseline_run_id,
                    kind="parameter_proposal_approved",
                    payload={
                        "proposal_id": proof.resolved.proof.proposal_id,
                        "actor": actor,
                    },
                    occurred_at=proof.approval.approval.approved_at,
                ),
            )

    def migrate_instrument_inventory(
        self,
        command: InstrumentInventoryMigrationCommand,
    ) -> InstrumentInventoryMigrationReceipt:
        """Publish one destructive inventory change after fencing its keys."""

        declared = _inventory_migration_deltas(command)
        with self._mutation_lock, self._config_errors():
            active = config_registry_service.load_active_config_registry_snapshot(
                unit_of_work=self._config_registry.read_unit_of_work
            )
            # Do not retire healthy idle connections for an already-stale intent.
            if active.activation.generation != command.expected_generation:
                raise BackendConflict("config registry active state changed")
            plan = config_registry_service.plan_instrument_inventory_migration(
                current=active.config,
                target=command.config,
                declared=declared,
            )
            try:
                retirement = self._actors.begin_retirement(
                    plan.affected_exclusivity_keys
                )
            except (InstrumentActorConflict, InstrumentActorShutdown) as error:
                raise BackendConflict(str(error)) from error
            with retirement:
                # Known owners should fail before idle connections are disconnected.
                self._require_inventory_migration_drained(
                    plan.affected_exclusivity_keys
                )
                try:
                    retirement.retire_idle()
                except (InstrumentActorConflict, InstrumentActorShutdown) as error:
                    raise BackendConflict(str(error)) from error
                except Exception as error:
                    raise BackendConflict(
                        "instrument connection could not be retired safely"
                    ) from error

                with self._config_transaction() as transaction:
                    connection, services = transaction
                    # Close the claim race after the first drained snapshot.
                    blockers = (
                        self._control.inventory_migration_blockers_in_transaction(
                            connection,
                            tuple(
                                ResourceKey.instrument(key)
                                for key in plan.affected_exclusivity_keys
                            ),
                        )
                    )
                    _require_no_inventory_migration_blockers(blockers)
                    result = publish_instrument_inventory_migration_revision(
                        revision=config_registry_service.ConfigRevision(
                            source=(
                                config_registry_service.DirectConfigRevisionSource(
                                    command.config
                                )
                            ),
                            entry_id=command.entry_id,
                            actor=command.actor,
                            note=command.note,
                        ),
                        declared=declared,
                        unit_of_work=services.config_registry,
                        expected_generation=command.expected_generation,
                    )
                    activation = result.activation
                    assert activation is not None
                    self._append_inventory_migration_events(
                        connection,
                        result,
                        change_count=len(plan.changes),
                    )
                    self._calibration_cohorts.supersede_stale_publications_in_transaction(
                        connection,
                        active_generation=activation.generation,
                        at=activation.recorded_at,
                    )
                    # Old-snapshot claims still lose the generation CAS, while
                    # new-snapshot owners no longer see an activation-to-gate gap.
                    retirement.release_gate()
                    receipt = InstrumentInventoryMigrationReceipt(
                        entry=result.entry,
                        activation=activation,
                        changes=tuple(
                            _wire_inventory_migration_change(change)
                            for change in plan.changes
                        ),
                    )
                return receipt

    def _require_inventory_migration_drained(
        self,
        exclusivity_keys: tuple[str, ...],
    ) -> None:
        with self._control.read_transaction() as connection:
            blockers = self._control.inventory_migration_blockers_in_transaction(
                connection,
                tuple(ResourceKey.instrument(key) for key in exclusivity_keys),
            )
        _require_no_inventory_migration_blockers(blockers)

    def preview_config_draft(
        self,
        command: ConfigDraftCommand,
    ) -> ConfigDraftPreview:
        with self._config_errors():
            result = config_registry_service.preview_manual_config_draft(
                unit_of_work=self._config_registry.read_unit_of_work,
                base_entry_id=command.base_entry_id,
                base_config_content_hash=command.base_content_hash,
                base_generation=command.base_generation,
                candidate_id=command.candidate_id,
                updates=command.updates,
            )
            candidate = result.check.candidate
            return ConfigDraftPreview(
                valid=result.check.ok,
                base_entry=result.base_entry,
                base_generation=result.base_generation,
                base_content_hash=result.base_entry.content_hash,
                config=candidate,
                result_content_hash=(
                    None if candidate is None else config_content_hash(candidate)
                ),
                deltas=result.check.deltas,
                problems=result.check.problems,
            )

    def activate_config_entry(
        self,
        command: ConfigEntryActivationCommand,
    ) -> ConfigActivationReceipt:
        with self._mutation_lock, self._config_errors():
            with self._config_transaction() as transaction:
                connection, services = transaction
                existing = self._config_operations.find_in_transaction(
                    connection,
                    command.operation_id,
                )
                if existing is not None:
                    if (
                        not isinstance(existing, ConfigActivationReceipt)
                        or existing.operation.intent_hash != command.intent_hash
                    ):
                        raise BackendConflict(
                            "config operation id is already committed for a different "
                            f"intent: {command.operation_id}"
                        )
                    return existing
                result = config_registry_service.activate_config_registry_entry(
                    entry_id=command.entry_id,
                    unit_of_work=services.config_registry,
                    actor=command.actor,
                    expected_generation=command.expected_generation,
                    note=command.note,
                )
                activation = result.activation
                assert activation is not None
                if result.activated:
                    self._control.append_event_in_transaction(
                        connection,
                        DurableEventInput(
                            kind="config_activated",
                            payload={
                                "entry_id": activation.entry_id,
                                "generation": activation.generation,
                            },
                            occurred_at=activation.recorded_at,
                        ),
                    )
                operation = ConfigActivationOperation(
                    operation_id=command.operation_id,
                    intent_hash=command.intent_hash,
                    entry_id=command.entry_id,
                    expected_generation=command.expected_generation,
                    actor=command.actor,
                    note=command.note,
                    activation_generation=activation.generation,
                )
                receipt = ConfigActivationReceipt(
                    operation=operation,
                    activation=activation,
                )
                self._config_operations.commit_in_transaction(
                    connection,
                    receipt,
                )
                self._calibration_cohorts.supersede_stale_publications_in_transaction(
                    connection,
                    active_generation=activation.generation,
                    at=activation.recorded_at,
                )
            return receipt

    def _append_inventory_migration_events(
        self,
        connection: sqlite3.Connection,
        result: config_registry_service.ConfigRegistryMutationResult,
        *,
        change_count: int,
    ) -> None:
        if result.saved:
            self._control.append_event_in_transaction(
                connection,
                DurableEventInput(
                    kind="config_saved",
                    payload={"entry_id": result.entry.id},
                    occurred_at=result.entry.recorded_at,
                ),
            )
        activation = result.activation
        if result.activated and activation is not None:
            self._control.append_event_in_transaction(
                connection,
                DurableEventInput(
                    kind="instrument_inventory_migrated",
                    payload={
                        "entry_id": result.entry.id,
                        "generation": activation.generation,
                        "change_count": change_count,
                    },
                    occurred_at=activation.recorded_at,
                ),
            )

    def _append_revision_events(
        self,
        connection: sqlite3.Connection,
        command: ConfigPublishCommand | CalibrationPublicationCommand,
        result: config_registry_service.ConfigRegistryMutationResult,
    ) -> None:
        source = command.source
        run_id = (
            source.run_id if isinstance(source, CandidateConfigRevisionSource) else None
        )
        if result.saved:
            self._control.append_event_in_transaction(
                connection,
                DurableEventInput(
                    run_id=run_id,
                    kind="config_saved",
                    payload={"entry_id": result.entry.id},
                    occurred_at=result.entry.recorded_at,
                ),
            )
        activation = result.activation
        if result.activated and activation is not None:
            self._control.append_event_in_transaction(
                connection,
                DurableEventInput(
                    run_id=run_id,
                    kind="config_activated",
                    payload={
                        "entry_id": result.entry.id,
                        "generation": activation.generation,
                    },
                    occurred_at=activation.recorded_at,
                ),
            )

    @contextmanager
    def _config_transaction(
        self,
    ) -> Generator[tuple[sqlite3.Connection, ProjectStateServices]]:
        """Commit registry state and replay events through one SQLite writer."""

        with self._control.write_transaction() as connection:
            services = replace(
                self._services,
                config_registry=lambda: self._config_registry.borrowed_unit_of_work(
                    connection
                ),
            )
            yield connection, services

    @contextmanager
    def _config_errors(self) -> Generator[None]:
        try:
            yield
        except NotFound as error:
            raise BackendNotFound(str(error)) from error
        except (
            CalibrationCohortConflict,
            CheckFailed,
            Conflict,
            DataIntegrityError,
        ) as error:
            raise BackendConflict(str(error)) from error


def _config_revision(
    command: ConfigPublishCommand | CalibrationPublicationCommand,
    *,
    calibration_merge: _PreparedCalibrationMerge | None = None,
) -> config_registry_service.ConfigRevision:
    source = command.source
    if isinstance(source, DirectConfigRevisionSource):
        revision_source = config_registry_service.DirectConfigRevisionSource(
            source.config
        )
    elif isinstance(source, ManualConfigDraftRevisionSource):
        draft = source.draft
        revision_source = config_registry_service.ManualConfigDraftRevisionSource(
            base_entry_id=draft.base_entry_id,
            base_config_content_hash=draft.base_content_hash,
            base_generation=draft.base_generation,
            candidate_id=draft.candidate_id,
            updates=draft.updates,
            expected_result_content_hash=source.expected_result_content_hash,
        )
    elif isinstance(source, CandidateConfigRevisionSource):
        revision_source = config_registry_service.CandidateConfigRevisionSource(
            run_id=source.run_id,
            proposal_id=source.proposal_id,
            acceptance=source.acceptance,
        )
    else:
        if calibration_merge is None:
            raise ValueError("calibration merge proof must be resolved by the server")
        revision_source = calibration_merge.revision_source
    return config_registry_service.ConfigRevision(
        source=revision_source,
        entry_id=command.entry_id,
        actor=command.actor,
        note=command.note,
    )


def _validate_calibration_evidence_step(
    attempt: ProcedureStepAttempt,
    reference: ConfigCompositionEvidenceStepRef,
    decision: ProjectAnalysisDecisionReference,
) -> tuple[AnalysisPublicationOutputRef, tuple[str, str]]:
    output = attempt.output
    if (
        attempt.procedure_run_id != reference.procedure_run_id
        or attempt.step_key != reference.step_key
        or attempt.attempt != reference.attempt
        or attempt.state != "succeeded"
        or output is None
    ):
        raise BackendConflict(
            "calibration merge contribution requires a successful evidence step"
        )
    if (
        attempt.operation != "analysis"
        or not isinstance(output, AnalysisPublicationOutputRef)
        or output.subject != ProjectAnalysisSubject()
        or output.analysis_record_id != decision.analysis_record_id
    ):
        raise BackendConflict(
            "calibration merge evidence step must publish its exact project decision"
        )
    run_ids = tuple(
        item.run_id for item in attempt.inputs if isinstance(item, RunOutputRef)
    )
    if len(attempt.inputs) != 2 or len(run_ids) != 2 or len(set(run_ids)) != 2:
        raise BackendConflict(
            "calibration merge evidence step must reference two distinct runs"
        )
    return output, run_ids


def _resolve_calibration_evidence_runs(
    runs: tuple[RunSnapshot, RunSnapshot],
    base: CalibrationConfigSourceRef,
) -> tuple[RunSnapshot, RunSnapshot]:
    baseline_runs = tuple(
        run
        for run in runs
        if isinstance(run.config_source, ConfigRegistryRunConfigSource)
        and _matches_calibration_base(run.config_source, base)
    )
    candidate_runs = tuple(
        run
        for run in runs
        if isinstance(run.config_source, AnalysisCandidateRunConfigSource)
    )
    if len(baseline_runs) != 1 or len(candidate_runs) != 1:
        raise BackendConflict(
            "calibration merge verification must uniquely identify its exact "
            "baseline and candidate runs"
        )
    return baseline_runs[0], candidate_runs[0]


def _matches_calibration_base(
    source: ConfigRegistryRunConfigSource,
    base: CalibrationConfigSourceRef,
) -> bool:
    return (
        source.selector == "active"
        and source.entry_id == base.entry_id
        and source.config_ref == base.config_ref
        and source.content_hash == base.content_hash
        and source.registry_generation == base.registry_generation
    )


def _calibration_successes(
    merge: _PreparedCalibrationMerge,
    *,
    operation: ConfigPublishOperation,
    result_entry: ConfigRegistryEntry,
    activation: ConfigRegistryActivationRecord,
) -> tuple[CalibrationSuccessRef, ...]:
    result_source = CalibrationConfigSourceRef(
        entry_id=result_entry.id,
        config_ref=result_entry.config_ref,
        content_hash=result_entry.content_hash,
        registry_generation=activation.generation,
    )
    successes: list[CalibrationSuccessRef] = []
    for proof in merge.members:
        attempt = proof.member.attempt_ref
        publication = CalibrationSuccessPublication(
            operation_id=operation.operation_id,
            source_intent_hash=operation.source_intent_hash,
            result_input_fingerprint=(proof.contribution.result_input_fingerprint),
            result_freshness_fingerprint=calibration_freshness_fingerprint(
                definition=attempt.definition,
                target=attempt.target,
                procedure=attempt.procedure,
                input_fingerprint=proof.contribution.result_input_fingerprint,
                dependencies=attempt.dependencies,
            ),
            result_config_source=result_source,
            published_at=activation.recorded_at,
        )
        successes.append(
            CalibrationSuccessRef(
                attempt=attempt,
                base_config_source=merge.base_config_source,
                succeeded_at=proof.succeeded_at,
                publication=publication,
            )
        )
    return tuple(successes)


def _inventory_migration_deltas(
    command: InstrumentInventoryMigrationCommand,
) -> tuple[config_registry_service.InstrumentInventoryMigrationDelta, ...]:
    changes: list[config_registry_service.InstrumentInventoryMigrationDelta] = []
    for change in command.changes:
        if isinstance(change, InstrumentInventoryRemoval):
            changes.append(
                config_registry_service.InstrumentInventoryMigrationDelta(
                    kind="remove",
                    old_instrument_id=change.instrument_id,
                    old_exclusivity_key=change.exclusivity_key,
                )
            )
        elif isinstance(change, InstrumentInventoryRekey):
            changes.append(
                config_registry_service.InstrumentInventoryMigrationDelta(
                    kind="rekey",
                    old_instrument_id=change.instrument_id,
                    old_exclusivity_key=change.from_exclusivity_key,
                    new_instrument_id=change.instrument_id,
                    new_exclusivity_key=change.to_exclusivity_key,
                )
            )
        else:
            assert isinstance(change, InstrumentInventoryRenameRekey)
            changes.append(
                config_registry_service.InstrumentInventoryMigrationDelta(
                    kind="rename_rekey",
                    old_instrument_id=change.from_instrument_id,
                    old_exclusivity_key=change.from_exclusivity_key,
                    new_instrument_id=change.to_instrument_id,
                    new_exclusivity_key=change.to_exclusivity_key,
                )
            )
    return tuple(changes)


def _wire_inventory_migration_change(
    change: config_registry_service.InstrumentInventoryMigrationDelta,
) -> (
    InstrumentInventoryRemoval
    | InstrumentInventoryRekey
    | InstrumentInventoryRenameRekey
):
    if change.kind == "remove":
        return InstrumentInventoryRemoval(
            instrument_id=change.old_instrument_id,
            exclusivity_key=change.old_exclusivity_key,
        )
    if change.kind == "rekey":
        assert change.new_exclusivity_key is not None
        return InstrumentInventoryRekey(
            instrument_id=change.old_instrument_id,
            from_exclusivity_key=change.old_exclusivity_key,
            to_exclusivity_key=change.new_exclusivity_key,
        )
    assert change.new_instrument_id is not None
    assert change.new_exclusivity_key is not None
    return InstrumentInventoryRenameRekey(
        from_instrument_id=change.old_instrument_id,
        to_instrument_id=change.new_instrument_id,
        from_exclusivity_key=change.old_exclusivity_key,
        to_exclusivity_key=change.new_exclusivity_key,
    )


def _require_no_inventory_migration_blockers(
    blockers: tuple[InventoryMigrationBlocker, ...],
) -> None:
    if not blockers:
        return
    details = ", ".join(
        f"{blocker.owner_kind} {blocker.owner_id} ({blocker.state}) on {blocker.key.id}"
        for blocker in blockers
    )
    raise BackendConflict(
        f"instrument inventory migration requires drained resources: {details}"
    )
