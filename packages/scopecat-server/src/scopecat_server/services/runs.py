# pyright: reportUnknownMemberType=false, reportUnknownParameterType=false
# pyright: reportUnknownVariableType=false
"""Run records and read-side application service."""

from __future__ import annotations

from base64 import b64decode, b64encode
from collections.abc import Generator, Mapping, Sequence
from contextlib import contextmanager
from threading import Lock
from typing import TYPE_CHECKING, Literal, cast

from scopecat.analysis.repository import AnalysisPublicationSummary
from scopecat.config.changes import (
    list_parameter_change_proposals,
    load_parameter_change_approval_for_proposal,
    load_parameter_change_proposal,
)
from scopecat.control.models import (
    ControlRun,
    ControlRunState,
    DurableEventInput,
    EventPage,
    RunResourceRequirement,
)
from scopecat.daemon.points import RunPointPlanView
from scopecat.daemon.views import (
    MeasurementArrowQuery,
    MeasurementLivePreview,
    MeasurementPreview,
    MeasurementSlice,
    MeasurementSliceQuery,
    MeasurementTraceFailure,
    MeasurementTracePreview,
    MeasurementTracePreviewQuery,
    MeasurementTraceSeries,
    ParameterProposalPage,
    ParameterProposalView,
    RunAdmissionView,
    RunAnalysisPage,
    RunAnalysisSummary,
    RunAnalysisView,
    RunArtifactBytesView,
    RunConfigView,
    RunContentPage,
    RunControlView,
    RunDatasetBytesView,
    RunDetail,
    RunFailureEvidence,
    RunPlanView,
    RunRequestView,
    RunResourceBlocker,
    RunResourceView,
    RunSummary,
    RunSummaryPage,
)
from scopecat.daemon.wire import (
    AnalysisArtifactOutputPayload,
    AnalysisDatasetOutputPayload,
    AnalysisFactOutputPayload,
    AnalysisFigureOutputPayload,
    AnalysisInputPayload,
    AnalysisOutputPayload,
    AnalysisParameterProposalOutputPayload,
    AnalysisSaveCommand,
    AnalysisSaveReceipt,
    AnalysisTableOutputPayload,
    InterpretationAnalysisInputPayload,
    MeasurementAnalysisInputPayload,
    PublishedAnalysisInputPayload,
    RunAttachmentCommand,
)
from scopecat.kernel.errors import (
    CheckFailed,
    Conflict,
    DataIntegrityError,
    NotFound,
)
from scopecat.kernel.ids import artifact_slug
from scopecat.measurements.datasets import (
    RAW_MEASUREMENTS_DATASET_ID,
    product_grid_slice_indices,
    select_measurement_schema,
)
from scopecat.measurements.entity_selection import (
    MeasurementEntitySelection,
    bind_entity_selection,
)
from scopecat.project_state import ProjectStateServices
from scopecat.records.analysis import AnalysisRecord
from scopecat.records.content import ContentEntry
from scopecat.records.costs import RunCompilationCost, RunMeasuredCosts
from scopecat.records.measurement import (
    MeasurementDataset,
    MeasurementDatasetSchema,
    MeasurementProductGridPointDomain,
    MeasurementRecord,
)
from scopecat.records.measurement_recording import MeasurementDatasetAppend
from scopecat.runs.attachments import attach_run_artifact
from scopecat.runs.data import (
    RunArtifactJsonResult,
    RunArtifactTextResult,
    RunMeasurementDatasetResult,
    RunRecordJsonResult,
)
from scopecat.runs.service import (
    load_run_request,
    read_run_artifact_bytes,
    read_run_artifact_json,
    read_run_artifact_text,
    read_run_dataset_bytes,
    read_run_measurement_dataset,
    read_run_record_json,
)

from scopecat_server.storage.sqlite.control_plane import (
    ControlPlaneNotFound,
    SQLiteControlPlane,
)
from scopecat_server.storage.sqlite.execution import (
    SQLiteMeasurementDatasetRepository,
    SQLiteRunCoverage,
)
from scopecat_server.storage.sqlite.run_repository import SQLiteRunRepository

from ..errors import BackendConflict, BackendNotFound
from .active_measurements import ActiveMeasurementStore
from .point_plans import RunPointPlanService

if TYPE_CHECKING:
    import pyarrow as pa
    from scopecat.analysis.service import AnalysisInput, AnalysisOutput
    from scopecat.measurements.traces import MeasurementTraceProjection


def analysis_input_from_payload(item: AnalysisInputPayload) -> AnalysisInput:
    from scopecat.analysis.service import (
        InterpretationAnalysisInput,
        MeasurementAnalysisInput,
        PublishedAnalysisOutputInput,
    )

    if isinstance(item, MeasurementAnalysisInputPayload):
        return MeasurementAnalysisInput(
            id=item.id,
            run_id=item.run_id,
            target=item.target,
            kind=item.kind,
            content_hash=item.content_hash,
            codec=item.codec,
            role=item.role,
            title=item.title,
            metadata=item.metadata,
        )
    if isinstance(item, InterpretationAnalysisInputPayload):
        return InterpretationAnalysisInput(
            id=item.id,
            target=item.target,
            kind=item.kind,
            content_hash=item.content_hash,
            codec=item.codec,
            role=item.role,
            title=item.title,
            metadata=item.metadata,
            source=item.source,
        )
    assert isinstance(item, PublishedAnalysisInputPayload)
    return PublishedAnalysisOutputInput(
        id=item.id,
        target=item.target,
        kind=item.kind,
        content_hash=item.content_hash,
        codec=item.codec,
        role=item.role,
        title=item.title,
        metadata=item.metadata,
        source=item.source,
    )


def analysis_output_from_payload(item: AnalysisOutputPayload) -> AnalysisOutput:
    from scopecat.analysis.datasets import DerivedDataset
    from scopecat.analysis.service import (
        AnalysisArtifactOutput,
        AnalysisDatasetOutput,
        AnalysisFactOutput,
        AnalysisFigureOutput,
        AnalysisParameterProposalOutput,
        AnalysisTableOutput,
    )

    if isinstance(item, AnalysisFactOutputPayload):
        return AnalysisFactOutput(
            kind="fact",
            id=item.id,
            title=item.title,
            content=item.content,
            produced_by=item.produced_by,
            metadata=item.metadata,
        )
    if isinstance(item, AnalysisDatasetOutputPayload):
        return AnalysisDatasetOutput(
            kind="dataset",
            id=item.id,
            title=item.title,
            content=DerivedDataset.from_payload(item.content),
            produced_by=item.produced_by,
            derived_from=item.derived_from,
            metadata=item.metadata,
        )
    if isinstance(item, AnalysisTableOutputPayload):
        return AnalysisTableOutput(
            kind="table",
            id=item.id,
            title=item.title,
            content=item.content,
            metadata=item.metadata,
        )
    if isinstance(item, AnalysisFigureOutputPayload):
        return AnalysisFigureOutput(
            kind="figure",
            id=item.id,
            title=item.title,
            content=item.content,
            metadata=item.metadata,
        )
    if isinstance(item, AnalysisArtifactOutputPayload):
        return AnalysisArtifactOutput(
            kind="artifact",
            id=item.id,
            title=item.title,
            content=item.content_bytes(),
            filename=item.filename,
            media_type=item.media_type,
            produced_by=item.produced_by,
            metadata=item.metadata,
        )
    return AnalysisParameterProposalOutput(
        kind="parameter_change_proposal",
        id=item.id,
        title=item.title,
        content=item.content,
        metadata=item.metadata,
    )


def _run_control_view(
    control: ControlRun,
    *,
    completed_point_count: int,
    point_plan: RunPointPlanView,
) -> RunControlView:
    plan = control.admission.plan
    return RunControlView(
        sequence=control.sequence,
        admission=RunAdmissionView(
            run_id=control.run_id,
            run_contract_fingerprint=control.admission.submission_content_hash,
            admitted_at=control.admission.admitted_at,
            display_name=control.admission.display_name,
            tags=control.admission.tags,
            description=control.admission.description,
            plan=RunPlanView(
                experiment_id=plan.experiment_id,
                experiment_kind=plan.experiment_kind,
                point_plan_fingerprint=plan.point_plan_fingerprint,
                measurement_contract_fingerprint=(
                    plan.measurement_contract_fingerprint
                ),
                point_count=plan.point_count,
                initial_point_count=plan.initial_point_count,
                point_limit=plan.point_limit,
                adaptive_coordinate_ids=plan.adaptive_coordinate_ids,
                adaptive_scope=plan.adaptive_scope,
                per_region_point_limit=plan.per_region_point_limit,
                adaptive_region_count=plan.adaptive_region_count,
                adaptive_regions=plan.adaptive_regions,
                adaptive_regions_truncated=plan.adaptive_regions_truncated,
                coordinates=plan.coordinates,
                sampled_points=plan.sampled_points,
                sampled_points_truncated=plan.sampled_points_truncated,
                record_ids=plan.record_ids,
                run_resource_requirements=tuple(
                    RunResourceRequirement(kind=resource.kind, id=resource.id)
                    for resource in plan.run_resource_requirements
                ),
            ),
        ),
        state=control.state,
        updated_at=control.updated_at,
        attention_reason=control.attention_reason,
        cancellation_requested_at=control.cancellation_requested_at,
        completed_point_count=completed_point_count,
        point_plan=point_plan,
    )


class RunService:
    """Own run records, analysis content, and read-side queries."""

    def __init__(
        self,
        *,
        control: SQLiteControlPlane,
        runs: SQLiteRunRepository,
        services: ProjectStateServices,
        active_measurements: ActiveMeasurementStore,
        point_plans: RunPointPlanService,
    ) -> None:
        self._control = control
        self._runs = runs
        self._services = services
        self._active_measurements = active_measurements
        self._point_plans = point_plans
        self._analysis_publication_lock = Lock()

    def list_runs(
        self,
        *,
        limit: int,
        before: int | None,
        state: ControlRunState | None,
        sample_id: str | None = None,
    ) -> RunSummaryPage:
        with self._control.read_transaction() as connection:
            page = self._control.list_runs_in_transaction(
                connection,
                limit=limit,
                before=before,
                state=state,
                sample_id=sample_id,
            )
            return RunSummaryPage(
                items=tuple(
                    RunSummary(
                        control=_run_control_view(
                            control,
                            completed_point_count=SQLiteRunCoverage(
                                self._runs,
                                run_id=control.run_id,
                            ).read_in_transaction(connection),
                            point_plan=self._point_plans.read_in_transaction(
                                connection,
                                control.run_id,
                            ),
                        ),
                        snapshot=self._runs.read_snapshot_in_transaction(
                            connection,
                            control.run_id,
                        ),
                    )
                    for control in page.items
                ),
                next_cursor=page.next_cursor,
            )

    def find_run_by_submission_id(self, submission_id: str) -> RunDetail | None:
        """Locate an admitted effect without guessing its generated run ID."""
        run = self._control.find_run_by_submission_id(submission_id)
        return None if run is None else self.get_run(run.run_id)

    def get_run(self, run_id: str) -> RunDetail:
        try:
            with self._control.read_transaction() as connection:
                control = self._control.get_run_in_transaction(connection, run_id)
                snapshot = self._runs.read_snapshot_in_transaction(connection, run_id)
                claims = {
                    (claim.resource.kind, claim.resource.id): claim
                    for claim in self._control.list_resource_claims_in_transaction(
                        connection
                    )
                }
                executor_lease = self._control.executor_lease_for_run_in_transaction(
                    connection,
                    run_id,
                )
                completed_point_count = SQLiteRunCoverage(
                    self._runs,
                    run_id=run_id,
                ).read_in_transaction(connection)
                point_plan = self._point_plans.read_in_transaction(
                    connection,
                    run_id,
                )
        except ControlPlaneNotFound as error:
            raise BackendNotFound(str(error)) from error
        resources: list[RunResourceView] = []
        for logical_resource, canonical_resource in zip(
            control.admission.plan.run_resource_requirements,
            control.admission.resource_claims,
            strict=True,
        ):
            claim = claims.get((canonical_resource.kind, canonical_resource.id))
            if control.state == "closed":
                claim = None
            owned = (
                claim is not None
                and claim.owner_kind == "run"
                and claim.owner_id == run_id
            )
            resources.append(
                RunResourceView(
                    resource=RunResourceRequirement(
                        kind=logical_resource.kind,
                        id=logical_resource.id,
                    ),
                    status=(
                        (claim.status if owned else "blocked")
                        if claim is not None
                        else ("released" if control.state == "closed" else "required")
                    ),
                    expires_at=(
                        executor_lease.expires_at
                        if claim is not None
                        and owned
                        and claim.status == "active"
                        and executor_lease is not None
                        else None
                    ),
                    blocked_by=(
                        RunResourceBlocker(
                            owner_kind=claim.owner_kind,
                            owner_id=claim.owner_id,
                            status=claim.status,
                        )
                        if claim is not None and not owned
                        else None
                    ),
                )
            )
        return RunDetail(
            control=_run_control_view(
                control,
                completed_point_count=completed_point_count,
                point_plan=point_plan,
            ),
            snapshot=snapshot,
            resources=tuple(resources),
        )

    def get_run_failure_evidence(self, run_id: str) -> RunFailureEvidence:
        from .failure_evidence import failure_evidence

        detail = self.get_run(run_id)
        page = self._control.list_events(
            limit=128, after=None, run_id=run_id, latest=True
        )
        return failure_evidence(detail, page.items, truncated=len(page.items) == 128)

    def get_run_measured_costs(self, run_id: str) -> RunMeasuredCosts:
        from scopecat.execution.evidence import (
            COMPILATION_COST_KIND,
            compilation_cost_ref,
        )

        from .measured_costs import measured_costs

        self.get_run(run_id)
        page = self._control.list_events(
            limit=128, after=None, run_id=run_id, latest=True
        )
        compilation = (
            self._runs.read_model(run_id, compilation_cost_ref(), RunCompilationCost)
            if self._runs.list_contents(
                run_id, limit=1, kind=COMPILATION_COST_KIND
            ).items
            else None
        )
        return measured_costs(
            page.items, compilation=compilation, truncated=len(page.items) == 128
        )

    def get_run_config(self, run_id: str) -> RunConfigView:
        with self._config_errors():
            snapshot = self._runs.read_snapshot(run_id)
            return RunConfigView(
                run_id=run_id,
                config_content_hash=snapshot.config_content_hash,
                config=self._runs.read_config_profile_snapshot(run_id),
            )

    def get_run_request(self, run_id: str) -> RunRequestView:
        with self._config_errors():
            return RunRequestView(
                run_id=run_id,
                request=load_run_request(run_id=run_id, services=self._services),
            )

    def list_run_contents(
        self,
        run_id: str,
        *,
        limit: int = 100,
        before: int | None = None,
        role: Literal["artifact", "dataset", "record"] | None = None,
        kind: str | None = None,
    ) -> RunContentPage:
        with self._config_errors():
            page = self._runs.list_contents(
                run_id,
                limit=limit,
                before=before,
                role=role,
                kind=kind,
            )
            return RunContentPage(
                run_id=run_id,
                items=page.items,
                next_cursor=page.next_cursor,
            )

    def get_run_content(
        self,
        run_id: str,
        *,
        role: Literal["artifact", "dataset", "record"],
        content_id: str,
    ) -> ContentEntry:
        with self._config_errors():
            return self._runs.read_content(
                run_id,
                role=role,
                content_id=content_id,
            )

    def list_run_analyses(
        self,
        run_id: str,
        *,
        limit: int = 100,
        before: int | None = None,
    ) -> RunAnalysisPage:
        with self._config_errors():
            page = self._runs.list_analysis_publications(
                run_id,
                limit=limit,
                before=before,
            )
            return RunAnalysisPage(
                run_id=run_id,
                items=tuple(
                    self._run_analysis_summary(publication)
                    for publication in page.items
                ),
                next_cursor=page.next_cursor,
            )

    def get_run_analysis(self, run_id: str, selector: str) -> RunAnalysisView:
        with self._config_errors():
            try:
                return self._run_analysis_view(run_id, selector)
            except NotFound as exact_error:
                analysis_key = artifact_slug(selector, fallback="analysis")
                publication = self._runs.latest_analysis_publication(
                    run_id,
                    analysis_key,
                )
                if publication is None:
                    raise exact_error
                return self._run_analysis_view(run_id, publication.record.id)

    def _run_analysis_view(self, run_id: str, selector: str) -> RunAnalysisView:
        publication = self._runs.read_analysis_publication(run_id, selector)
        result = read_run_record_json(
            run_id=run_id,
            selector=publication.record.id,
            expected_kind="analysis",
            services=self._services,
        )
        return RunAnalysisView(
            run_id=run_id,
            entry=result.record,
            analysis=AnalysisRecord.model_validate(result.content),
            published_at=publication.published_at,
        )

    @staticmethod
    def _run_analysis_summary(
        publication: AnalysisPublicationSummary,
    ) -> RunAnalysisSummary:
        subject = publication.subject
        assert subject.kind == "run"
        return RunAnalysisSummary(
            run_id=subject.run_id,
            entry=publication.record,
            title=publication.title,
            key=publication.analysis_key,
            revision=publication.revision,
            publication_hash=publication.publication_hash,
            published_at=publication.published_at,
            step_id=publication.step_id,
            input_count=publication.input_count,
            output_count=publication.output_count,
        )

    def save_run_analysis(
        self,
        run_id: str,
        command: AnalysisSaveCommand,
    ) -> AnalysisSaveReceipt:
        from scopecat.analysis.service import prepare_analysis

        inputs = tuple(analysis_input_from_payload(item) for item in command.inputs)
        outputs = tuple(analysis_output_from_payload(item) for item in command.outputs)
        proposals = tuple(
            item.content
            for item in command.outputs
            if isinstance(item, AnalysisParameterProposalOutputPayload)
        )
        with self._analysis_publication_lock, self._config_errors():
            prepared = prepare_analysis(
                services=self._services,
                run_id=run_id,
                title=command.title,
                analysis_key=command.analysis_key,
                step_id=command.step_id,
                inputs=inputs,
                executions=command.executions,
                outputs=outputs,
                parameter_proposals=proposals,
            )
            if prepared.publication is None:
                return AnalysisSaveReceipt(
                    record=prepared.saved.record,
                    analysis_key=prepared.saved.analysis_key,
                    inputs=command.inputs,
                    parameter_proposals=prepared.saved.parameter_proposals,
                )
            publication = self._runs.prepare_analysis_publication(prepared.publication)
            with self._control.write_transaction() as connection:
                created = self._runs.publish_prepared_analysis_in_transaction(
                    connection,
                    publication,
                )
                if created:
                    self._control.append_event_in_transaction(
                        connection,
                        DurableEventInput(
                            run_id=run_id,
                            kind="analysis_saved",
                            payload={
                                "analysis_key": prepared.saved.analysis_key,
                                "record_id": prepared.saved.record.id,
                            },
                        ),
                    )
        return AnalysisSaveReceipt(
            record=prepared.saved.record,
            analysis_key=prepared.saved.analysis_key,
            inputs=command.inputs,
            parameter_proposals=prepared.saved.parameter_proposals,
        )

    def get_run_artifact_bytes(
        self,
        run_id: str,
        selector: str,
        *,
        expected_kind: str | None,
    ) -> RunArtifactBytesView:
        with self._config_errors():
            result = read_run_artifact_bytes(
                run_id=run_id,
                selector=selector,
                expected_kind=expected_kind,
                services=self._services,
            )
            return RunArtifactBytesView(
                run_id=run_id,
                artifact=result.artifact,
                content_base64=b64encode(result.content).decode("ascii"),
            )

    def get_run_artifact_text(
        self,
        run_id: str,
        selector: str,
        *,
        expected_kind: str | None,
    ) -> RunArtifactTextResult:
        with self._config_errors():
            return read_run_artifact_text(
                run_id=run_id,
                selector=selector,
                expected_kind=expected_kind,
                services=self._services,
            )

    def get_run_artifact_json(
        self,
        run_id: str,
        selector: str,
        *,
        expected_kind: str | None,
    ) -> RunArtifactJsonResult:
        with self._config_errors():
            return read_run_artifact_json(
                run_id=run_id,
                selector=selector,
                expected_kind=expected_kind,
                services=self._services,
            )

    def get_run_record_json(
        self,
        run_id: str,
        selector: str,
        *,
        expected_kind: str | None,
    ) -> RunRecordJsonResult:
        with self._config_errors():
            return read_run_record_json(
                run_id=run_id,
                selector=selector,
                expected_kind=expected_kind,
                services=self._services,
            )

    def get_run_dataset_content(
        self,
        run_id: str,
        selector: str,
    ) -> RunMeasurementDatasetResult:
        with self._config_errors():
            return read_run_measurement_dataset(
                run_id=run_id,
                selector=selector,
                services=self._services,
            )

    def get_run_dataset_bytes(
        self,
        run_id: str,
        selector: str,
        *,
        expected_kind: str | None,
    ) -> RunDatasetBytesView:
        with self._config_errors():
            result = read_run_dataset_bytes(
                run_id=run_id,
                selector=selector,
                expected_kind=expected_kind,
                services=self._services,
            )
            return RunDatasetBytesView(
                run_id=run_id,
                dataset=result.dataset,
                content_base64=b64encode(result.content).decode("ascii"),
            )

    def attach_run_content(
        self,
        run_id: str,
        command: RunAttachmentCommand,
    ) -> ContentEntry:
        content = (
            None
            if command.content_base64 is None
            else b64decode(command.content_base64, validate=True)
        )
        with self._config_errors():
            artifact = attach_run_artifact(
                services=self._services,
                run_id=run_id,
                key=command.key,
                kind=command.kind,
                text=command.text,
                content=content,
                filename=command.filename,
                media_type=command.media_type,
                metadata=command.metadata,
            )
        return artifact

    def list_parameter_proposals(
        self,
        run_id: str,
        *,
        limit: int = 100,
        before: int | None = None,
    ) -> ParameterProposalPage:
        with self._config_errors():
            page = list_parameter_change_proposals(
                run_id=run_id,
                services=self._services,
                limit=limit,
                before=before,
            )
            return ParameterProposalPage(
                run_id=run_id,
                items=tuple(
                    ParameterProposalView(
                        proposal=proposal,
                        approval=load_parameter_change_approval_for_proposal(
                            run_id=run_id,
                            proposal=proposal,
                            storage=self._runs,
                        ),
                    )
                    for proposal in page.items
                ),
                next_cursor=page.next_cursor,
            )

    def get_parameter_proposal(
        self,
        run_id: str,
        proposal_id: str,
    ) -> ParameterProposalView:
        with self._config_errors():
            proposal = load_parameter_change_proposal(
                run_id=run_id,
                selector=proposal_id,
                services=self._services,
            )
            return ParameterProposalView(
                proposal=proposal,
                approval=load_parameter_change_approval_for_proposal(
                    run_id=run_id,
                    proposal=proposal,
                    storage=self._runs,
                ),
            )

    def measurement_arrow(
        self,
        run_id: str,
        query: MeasurementArrowQuery,
    ) -> tuple[pa.Table, int | None, int]:
        """Read and project one finite page from Arrow-backed measurement chunks."""

        from scopecat.measurements.paging import project_measurement_page

        variable_ids = tuple(column.variable_id for column in query.columns)
        with self._config_errors():
            run_snapshot = self._runs.read_snapshot(run_id)
            items, next_offset, schema, snapshot_size = (
                SQLiteMeasurementDatasetRepository(
                    self._runs,
                    run_id=run_id,
                ).measurement_page(
                    limit=query.limit,
                    offset=query.offset,
                    snapshot_size=query.snapshot_size,
                    variable_ids=variable_ids,
                    entity_selection=query.entity_selection,
                )
            )
        if schema is None:
            raise BackendConflict("measurement dataset has no registered schema")
        try:
            entry = self._runs.read_content(
                run_id,
                role="dataset",
                content_id=RAW_MEASUREMENTS_DATASET_ID,
            )
        except NotFound:
            entry = ContentEntry(
                role="dataset",
                id=RAW_MEASUREMENTS_DATASET_ID,
                kind="measurement_dataset",
                schema=schema.model_dump(mode="json"),
                content_hash="live-measurement-dataset",
            )
        try:
            table = project_measurement_page(
                items,
                schema=schema,
                entry=entry,
                columns={column.name: column.variable_id for column in query.columns},
                units=query.units,
                diagnostics=query.diagnostics,
                include_identity=query.include_identity,
                layout=query.layout,
            )
        except (KeyError, TypeError, ValueError) as error:
            raise BackendConflict(str(error)) from error
        metadata = dict(cast("Mapping[bytes, bytes]", table.schema.metadata or {}))
        metadata.update(
            {
                b"scopecat.run_id": run_id.encode(),
                b"scopecat.config_content_hash": str(
                    run_snapshot.config_content_hash
                ).encode(),
                b"scopecat.snapshot_size": str(snapshot_size).encode(),
            }
        )
        return table.replace_schema_metadata(metadata), next_offset, snapshot_size

    def measurement_preview(
        self,
        run_id: str,
        *,
        limit: int,
    ) -> MeasurementPreview:
        """Return one bounded record preview for presentation in the operator UI."""

        with self._config_errors():
            self._runs.read_snapshot(run_id)
            items, next_offset, live_schema = SQLiteMeasurementDatasetRepository(
                self._runs,
                run_id=run_id,
            ).measurement_preview(limit=limit)
        try:
            dataset = self._runs.read_content(
                run_id,
                role="dataset",
                content_id=RAW_MEASUREMENTS_DATASET_ID,
            )
        except NotFound:
            dataset = None
        terminal_schema = (
            None
            if dataset is None or dataset.data_schema is None
            else MeasurementDatasetSchema.model_validate(dataset.data_schema)
        )
        return MeasurementPreview(
            items=items,
            dataset_schema=terminal_schema or live_schema,
            truncated=next_offset is not None,
        )

    def measurement_live_preview(
        self,
        run_id: str,
        *,
        after_record_count: int | None,
    ) -> MeasurementLivePreview:
        """Return daemon-received measurement state without forcing persistence."""

        with self._config_errors():
            self._runs.read_snapshot(run_id)
        return self._active_measurements.preview(
            run_id,
            after_record_count=after_record_count,
        )

    def measurement_live_arrow(
        self,
        run_id: str,
        *,
        after_record_count: int | None,
    ) -> tuple[MeasurementLivePreview, bytes]:
        """Encode only a newly received latest record as Arrow IPC."""

        from scopecat.measurements.recording_arrow import encode_measurement_append

        with self._config_errors():
            self._runs.read_snapshot(run_id)
        preview, header = self._active_measurements.snapshot(
            run_id,
            after_record_count=after_record_count,
        )
        latest = preview.latest
        if latest is None or header is None:
            return preview, b""
        append = MeasurementDatasetAppend(
            run_id=run_id,
            header_content_hash=header.content_hash,
            acquisition_start=preview.received_record_count - 1,
            records=(latest,),
        )
        return preview, encode_measurement_append(
            append,
            header.dataset_schema,
        )

    def measurement_slice(
        self,
        run_id: str,
        query: MeasurementSliceQuery,
    ) -> MeasurementSlice:
        """Read one bounded product-grid slice by authored axis indices."""

        with self._config_errors():
            self._runs.read_snapshot(run_id)
            repository = SQLiteMeasurementDatasetRepository(self._runs, run_id=run_id)
            schema = repository.measurement_schema()
        if schema is None:
            raise BackendConflict("measurement dataset has no registered schema")
        domain = schema.point_domain
        if not isinstance(domain, MeasurementProductGridPointDomain):
            raise BackendConflict("measurement slices require a product-grid domain")
        try:
            point_indices, selected_point_count = product_grid_slice_indices(
                domain,
                query.fixed_axis_indices,
                offset=query.offset,
                limit=query.limit,
            )
        except ValueError as error:
            raise BackendConflict(str(error)) from error
        response_schema = schema
        if query.variable_ids is not None:
            try:
                response_schema = select_measurement_schema(
                    response_schema,
                    query.variable_ids,
                )
            except ValueError as error:
                raise BackendConflict(str(error)) from error
        with self._config_errors():
            records = repository.measurement_records_at(
                point_indices,
                variable_ids=query.variable_ids,
            )
        return MeasurementSlice(
            items=records,
            dataset_schema=response_schema if query.include_schema else None,
            selected_point_count=selected_point_count,
            offset=query.offset,
            window_point_count=len(point_indices),
            next_offset=(
                query.offset + len(point_indices)
                if query.offset + len(point_indices) < selected_point_count
                else None
            ),
            previous_offset=(
                max(0, query.offset - query.limit) if query.offset > 0 else None
            ),
            truncated=selected_point_count > len(point_indices),
        )

    def measurement_trace_preview(
        self,
        run_id: str,
        query: MeasurementTracePreviewQuery,
    ) -> MeasurementTracePreview:
        """Return bounded numeric trace series for direct plotting."""

        with self._config_errors():
            self._runs.read_snapshot(run_id)
            repository = SQLiteMeasurementDatasetRepository(self._runs, run_id=run_id)
            schema = repository.measurement_schema()
        if schema is None:
            raise BackendConflict("measurement dataset has no registered schema")
        with self._config_errors():
            available_point_count = repository.measurement_record_count()
        selection = None
        bound = None
        source_projection = _project_trace_records(
            schema,
            (),
            query.model_copy(update={"entities": None, "entity_indices": None}),
        )
        if query.entities is not None or query.entity_indices is not None:
            dimension_id = source_projection.entity_dimension_id
            if dimension_id is None:
                raise BackendConflict("trace entities require an entity trace axis")
            entities = query.entities
            if entities is None:
                dimension = next(
                    item for item in schema.dimensions if item.id == dimension_id
                )
                assert dimension.index is not None and query.entity_indices is not None
                try:
                    entities = tuple(
                        dimension.index.values[index] for index in query.entity_indices
                    )
                except IndexError as error:
                    raise BackendConflict(
                        "trace entity index is out of range"
                    ) from error
            selection = MeasurementEntitySelection(
                dimension_id=dimension_id, entities=entities
            )
            bound = bind_entity_selection(schema, selection)
            schema = bound.schema
            query = query.model_copy(
                update={"entities": bound.selection.entities, "entity_indices": None}
            )
        projection = _project_trace_records(schema, (), query)
        series_read_limit = min(query.max_series, query.max_samples // 2)
        point_read_limit = max(
            1,
            (series_read_limit + projection.selected_entity_count - 1)
            // projection.selected_entity_count,
        )
        point_coordinate_ids = tuple(
            variable.id
            for variable in schema.variables
            if variable.role == "coordinate" and tuple(variable.dims) == ("point",)
        )
        trace_variable_ids = tuple(
            dict.fromkeys(
                (
                    *point_coordinate_ids,
                    *(
                        ()
                        if projection.source_coordinate_id is None
                        else (projection.source_coordinate_id,)
                    ),
                    projection.observable_id,
                )
            )
        )
        try:
            point_indices, selected_point_count = _trace_preview_point_indices(
                schema,
                query.fixed_axis_indices,
                offset=0,
                limit=point_read_limit,
                available_point_count=available_point_count,
            )
        except ValueError as error:
            raise BackendConflict(str(error)) from error
        records: Sequence[MeasurementRecord] = ()
        if point_indices:
            with self._config_errors():
                records = repository.measurement_records_at(
                    point_indices,
                    variable_ids=trace_variable_ids,
                    entity_selection=selection,
                )
            projection = _project_trace_records(schema, records, query)
        series = tuple(
            MeasurementTraceSeries(
                point_index=item.point_index,
                logical_point_id=item.logical_point_id,
                label=item.label,
                entity_index=(
                    item.entity_index
                    if bound is None or item.entity_index is None
                    else bound.target_to_schema[item.entity_index]
                ),
                entity=item.entity,
                x=tuple(float(value) for value in item.x),
                y=item.y,
                source_sample_count=item.source_sample_count,
                available_sample_count=item.available_sample_count,
                unavailable_reasons=item.unavailable_reasons,
                evidence=item.evidence,
            )
            for item in projection.series
        )
        failures = tuple(
            MeasurementTraceFailure(
                point_index=item.point_index,
                logical_point_id=item.logical_point_id,
                label=item.label,
                entity_index=(
                    item.entity_index
                    if bound is None or item.entity_index is None
                    else bound.target_to_schema[item.entity_index]
                ),
                entity=item.entity,
                reasons=item.reasons,
                evidence=item.evidence,
            )
            for item in projection.failures
        )
        inspected_series_count = min(
            series_read_limit,
            len(point_indices) * projection.selected_entity_count,
        )
        selected_series_count = selected_point_count * projection.selected_entity_count
        return MeasurementTracePreview(
            fixed_axis_indices=dict(query.fixed_axis_indices),
            dimension_id=projection.dimension_id,
            recording_group_id=projection.recording_group_id,
            coordinate_id=projection.coordinate_id,
            observable_id=projection.observable_id,
            coordinate_label=projection.coordinate_label,
            observable_label=projection.observable_label,
            coordinate_unit=projection.coordinate_unit,
            observable_unit=projection.observable_unit,
            entity_dimension_id=projection.entity_dimension_id,
            entity_acquisition=projection.entity_acquisition,
            layout=projection.layout,
            value_mode=projection.value_mode,
            value_unit=projection.value_unit,
            downsampling=projection.downsampling,
            series=series,
            failures=failures,
            selected_series_count=selected_series_count,
            inspected_series_count=inspected_series_count,
            returned_series_count=len(series),
            truncated_series=inspected_series_count < selected_series_count,
            source_sample_count=projection.source_sample_count,
            returned_sample_count=projection.returned_sample_count,
            samples_reduced=projection.samples_reduced,
        )

    @contextmanager
    def _config_errors(self) -> Generator[None]:
        try:
            yield
        except NotFound as error:
            raise BackendNotFound(str(error)) from error
        except (CheckFailed, Conflict, DataIntegrityError) as error:
            raise BackendConflict(str(error)) from error

    def list_events(
        self,
        *,
        limit: int,
        after: int | None,
        run_id: str | None,
        latest: bool = False,
    ) -> EventPage:
        return self._control.list_events(
            limit=limit,
            after=after,
            run_id=run_id,
            latest=latest,
        )


def _trace_preview_point_indices(
    schema: MeasurementDatasetSchema,
    fixed_axis_indices: dict[str, int],
    *,
    offset: int,
    limit: int,
    available_point_count: int,
) -> tuple[tuple[int, ...], int]:
    domain = schema.point_domain
    if isinstance(domain, MeasurementProductGridPointDomain):
        point_indices, selected_count = product_grid_slice_indices(
            domain,
            fixed_axis_indices,
            offset=offset,
            limit=limit,
        )
        return point_indices, selected_count
    if fixed_axis_indices:
        raise ValueError("trace fixed-axis selection requires a product-grid domain")
    point_dimension = next(
        dimension for dimension in schema.dimensions if dimension.id == "point"
    )
    point_count = (
        available_point_count if point_dimension.size is None else point_dimension.size
    )
    return (
        tuple(range(offset, min(point_count, offset + limit))),
        point_count,
    )


def _project_trace_records(
    schema: MeasurementDatasetSchema,
    records: Sequence[MeasurementRecord],
    query: MeasurementTracePreviewQuery,
) -> MeasurementTraceProjection:
    from scopecat.measurements.traces import project_measurement_trace_preview

    try:
        return project_measurement_trace_preview(
            MeasurementDataset(dataset_schema=schema, records=tuple(records)),
            query.observable_id,
            coordinate=query.coordinate_id,
            group=query.recording_group_id,
            max_series=query.max_series,
            max_samples=query.max_samples,
            value_mode=query.value_mode,
            downsampling=query.downsampling,
            entity_indices=query.entity_indices,
            entities=query.entities,
        )
    except ValueError as error:
        raise BackendConflict(str(error)) from error
