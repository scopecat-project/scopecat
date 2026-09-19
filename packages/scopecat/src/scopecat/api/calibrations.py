"""High-level project calibration cohort operations."""

from __future__ import annotations

from scopecat.api._config import LabConfigOperations
from scopecat.api.calibration_finalizer import (
    CalibrationPublicationPlanningContext,
    ProjectCalibrationPublicationFinalizer,
)
from scopecat.api.calibration_planner import (
    CalibrationPlanningContext,
    ProjectCalibrationEvaluator,
)
from scopecat.api.calibration_policy import CalibrationPublicationPolicyRegistry
from scopecat.api.calibration_publication import (
    CalibrationCohortPublicationPlan,
    CalibrationPublicationReadSession,
    publish_calibration_cohort,
)
from scopecat.api.parameters import ParameterVersion
from scopecat.api.procedures import LabProcedureOperations
from scopecat.automation.calibration_definition import CalibrationRegistry
from scopecat.automation.calibration_wire import (
    CalibrationCohortCreateCommand,
    CalibrationCohortCreateReceipt,
    CalibrationCohortListQuery,
    CalibrationCohortMemberListQuery,
    CalibrationCohortMemberPage,
    CalibrationCohortPage,
    CalibrationPublicationAttentionCommand,
    CalibrationPublicationDeferCommand,
    CalibrationPublicationReadyPage,
    CalibrationPublicationReadyQuery,
    CalibrationPublicationRetryCommand,
    CalibrationStatusQuery,
)
from scopecat.automation.calibrations import (
    CalibrationCohort,
    CalibrationCohortFinalization,
    CalibrationCohortSpec,
    CalibrationConfigSourceRef,
    CalibrationStatusSnapshot,
)
from scopecat.config.registry.records import ContextConfigRegistrySource
from scopecat.daemon.client import DaemonClient
from scopecat.daemon.wire import (
    CalibrationPublicationReceipt,
)
from scopecat.records.calibration_scope import WorkingPointCalibrationScope
from scopecat.records.config_context import ConfigContextRef
from scopecat.records.run import ConfigRegistryRunConfigSource


class LabCalibrationOperations:
    """Evaluate, admit, and inspect project-owned calibration cohorts."""

    __slots__ = (
        "_client",
        "_config",
        "_procedures",
        "_publication_registry",
        "_publication_session",
        "_registry",
    )

    def __init__(
        self,
        *,
        client: DaemonClient,
        config: LabConfigOperations,
        procedures: LabProcedureOperations,
        publication_session: CalibrationPublicationReadSession,
        registry: CalibrationRegistry[CalibrationPlanningContext],
        publication_registry: CalibrationPublicationPolicyRegistry,
    ) -> None:
        for definition in registry.values():
            procedures.registry.resolve(definition.procedure.ref)
        for policy in publication_registry.active_bindings:
            registry.resolve(policy.calibration)
        self._client = client
        self._config = config
        self._procedures = procedures
        self._publication_session = publication_session
        self._registry = registry
        self._publication_registry = publication_registry

    @property
    def registry(self) -> CalibrationRegistry[CalibrationPlanningContext]:
        return self._registry

    @property
    def publication_registry(self) -> CalibrationPublicationPolicyRegistry:
        return self._publication_registry

    def planning_context(
        self,
        *,
        working_point: str | ConfigContextRef | ParameterVersion | None = None,
    ) -> CalibrationPlanningContext:
        if working_point is not None:
            if isinstance(working_point, ParameterVersion):
                ref = working_point.context
            elif isinstance(working_point, str):
                selected = self._config.entry(working_point)
                ref = ConfigContextRef(
                    entry_id=selected.entry.id, content_hash=selected.entry.content_hash
                )
            else:
                ref = working_point
            latest = self._config.latest_context(ref)
            provenance = latest.entry.source
            if not isinstance(provenance, ContextConfigRegistrySource):
                raise ValueError("calibration requires a saved working point")
            metadata = provenance.context
            return CalibrationPlanningContext(
                config=latest.config,
                config_source=CalibrationConfigSourceRef(
                    entry_id=latest.entry.id,
                    config_ref=latest.entry.config_ref,
                    content_hash=latest.entry.content_hash,
                    scope=WorkingPointCalibrationScope(
                        workspace_id=metadata.workspace_id or latest.entry.id,
                        sample=metadata.sample,
                    ),
                ),
            )
        config, source = self._config.resolve_with_source("active")
        assert isinstance(source, ConfigRegistryRunConfigSource)
        return CalibrationPlanningContext(
            config=config,
            config_source=CalibrationConfigSourceRef.from_run_config_source(source),
        )

    def evaluator(
        self,
        *,
        working_point: str | ConfigContextRef | ParameterVersion | None = None,
    ) -> ProjectCalibrationEvaluator:
        """Follow one selected workspace; catalog checks cannot publish parameters."""
        return ProjectCalibrationEvaluator(
            self,
            self._registry,
            lambda: self.planning_context(working_point=working_point),
            publication_policies=self._publication_registry,
        )

    def publication_planning_context(
        self,
    ) -> CalibrationPublicationPlanningContext:
        """Build the read-only project facade given to publication policies."""

        return CalibrationPublicationPlanningContext(
            self._client,
            self._config,
            self._procedures,
            self._publication_session,
        )

    def publication_finalizer(
        self,
        *,
        page_limit: int = 50,
        actor: str = "calibration-publication-finalizer",
    ) -> ProjectCalibrationPublicationFinalizer:
        """Build one stateful finite-traversal automatic finalizer."""

        return ProjectCalibrationPublicationFinalizer(
            self,
            self._publication_registry,
            page_limit=page_limit,
            actor=actor,
        )

    def status(
        self,
        calibration_keys: tuple[str, ...],
        *,
        fanout_scope: str,
    ) -> CalibrationStatusSnapshot:
        receipt = self._client.query_calibration_status(
            CalibrationStatusQuery(
                calibration_keys=calibration_keys,
                fanout_scope=fanout_scope,
            )
        )
        return receipt.snapshot

    def create(
        self,
        cohort_id: str,
        spec: CalibrationCohortSpec,
    ) -> CalibrationCohortCreateReceipt:
        return self._client.create_calibration_cohort(
            CalibrationCohortCreateCommand(cohort_id=cohort_id, spec=spec)
        )

    def get(self, cohort_id: str) -> CalibrationCohort:
        return self._client.get_calibration_cohort(cohort_id).cohort

    def list(
        self,
        *,
        limit: int = 50,
        before: int | None = None,
        fanout_scope: str | None = None,
    ) -> CalibrationCohortPage:
        return self._client.list_calibration_cohorts(
            CalibrationCohortListQuery(
                cursor=before,
                limit=limit,
                fanout_scope=fanout_scope,
            )
        )

    def members(
        self,
        cohort_id: str,
        *,
        limit: int = 50,
        after: int | None = None,
    ) -> CalibrationCohortMemberPage:
        return self._client.list_calibration_cohort_members(
            CalibrationCohortMemberListQuery(
                cohort_id=cohort_id,
                cursor=after,
                limit=limit,
            )
        )

    def ready_publications(
        self,
        query: CalibrationPublicationReadyQuery,
    ) -> CalibrationPublicationReadyPage:
        """Discover one finite page of exact supported publication work."""

        return self._client.list_ready_calibration_publications(query)

    def publication_finalization(
        self,
        cohort_id: str,
    ) -> CalibrationCohortFinalization:
        """Read exact durable automatic-publication state for one cohort."""

        return self._client.get_calibration_publication(cohort_id).finalization

    def require_publication_attention(
        self,
        command: CalibrationPublicationAttentionCommand,
    ) -> CalibrationCohortFinalization:
        """Move one exact ready finalization to durable operator attention."""

        receipt = self._client.require_calibration_publication_attention(command)
        return receipt.finalization

    def retry_publication(
        self,
        command: CalibrationPublicationRetryCommand,
    ) -> CalibrationCohortFinalization:
        """Return one exact attention state to the durable ready queue."""

        receipt = self._client.retry_calibration_publication(command)
        return receipt.finalization

    def defer_publication(
        self,
        command: CalibrationPublicationDeferCommand,
    ) -> CalibrationCohortFinalization:
        """Delay one exact ready occurrence using the server clock."""

        receipt = self._client.defer_calibration_publication(command)
        return receipt.finalization

    def publish(
        self,
        plan: CalibrationCohortPublicationPlan,
    ) -> CalibrationPublicationReceipt:
        """Publish an exact cohort plan with unknown-outcome reconciliation."""

        return publish_calibration_cohort(self._client, plan)


__all__ = ["LabCalibrationOperations"]
