"""Server admission of declared checks; no laboratory Python is executed."""

import sqlite3
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Literal

from pydantic import ValidationError
from scopecat.analysis.calibration import CHECK_RESULT
from scopecat.automation import (
    AnalysisPublicationOutputRef,
    ProcedureRun,
    ProcedureStepOutputRef,
    RunOutputRef,
)
from scopecat.automation.calibration import (
    CapabilityAvailability,
    CheckEvidence,
    assess_capability_dependencies,
    select_calibration_check,
)
from scopecat.automation.calibration_tasks import (
    CalibrationTaskProgress,
    assess_calibration_task,
)
from scopecat.daemon.calibration_checks import (
    CalibrationCheckObservation,
    CalibrationCheckObservationResult,
    CalibrationCheckPage,
    CalibrationCheckQuery,
    CalibrationCheckView,
    CalibrationReport,
    CalibrationReportQuery,
    CalibrationRequirementStatus,
    CalibrationTaskPreview,
)
from scopecat.daemon.wire import RunSubmission
from scopecat.kernel.errors import NotFound
from scopecat.kernel.frozen import thaw_json_value
from scopecat.records.analysis import (
    AnalysisFactRecordOutput,
    AnalysisRecord,
    RunAnalysisSubject,
)
from scopecat.records.calibration_check import (
    CalibrationCheckRequest,
    CalibrationCheckResult,
    CalibrationContext,
)
from scopecat.records.run import ParameterRunConfigSource, RunConfigSource
from scopecat.records.sample import SampleSelector
from scopecat.records.scientific_binding import (
    ResolvedScientificBinding,
    UnboundSubject,
)
from scopecat.runs.refs import record_content_ref
from scopecat.sdk.compute import PYTHON_JSON_CODEC

from scopecat_server.errors import BackendConflict
from scopecat_server.services.parameter_resolution import resolve_parameters
from scopecat_server.services.samples import SampleService
from scopecat_server.services.scientific_binding import validate_scientific_binding
from scopecat_server.storage.sqlite.automation import (
    AutomationNotFound,
    SQLiteAutomationStore,
)
from scopecat_server.storage.sqlite.calibration_checks import CalibrationCheckStore
from scopecat_server.storage.sqlite.connection import SQLiteDatabase
from scopecat_server.storage.sqlite.run_repository import SQLiteRunRepository
from scopecat_server.storage.sqlite.setups import SQLiteSetupRepository
from scopecat_server.storage.sqlite.target_catalog import TargetCatalogStore


def declared_check(intent: Mapping[str, object]) -> CalibrationCheckRequest | None:
    if "calibration_check" not in intent:
        return None
    try:
        return CalibrationCheckRequest.model_validate(
            thaw_json_value(intent["calibration_check"])
        )
    except ValidationError as error:
        raise BackendConflict(
            f"invalid calibration check declaration: {error}"
        ) from error


class CalibrationCheckAdmission:
    def __init__(self, samples: SampleService, targets: TargetCatalogStore) -> None:
        self._samples = samples
        self._targets = targets

    def validate(
        self,
        connection: sqlite3.Connection,
        request: CalibrationCheckRequest,
        *,
        samples: tuple[SampleSelector, ...],
        binding: ResolvedScientificBinding | None,
    ) -> None:
        """Resolve authoritative inputs under the parent's admission transaction.

        Parameter/setup authority is read in that transaction. Subject validation
        resolves exact immutable sample/target revisions using existing services.
        """
        current = SQLiteSetupRepository(connection).read_current()
        if (
            current is None
            or current.revision.setup.execution_content_hash
            != request.context.setup_content_hash
        ):
            raise BackendConflict("check setup differs from current authority")
        resolved = resolve_parameters(
            connection,
            parameters=request.context.parameters,
            setup=current.revision.ref,
        )
        expected = ResolvedScientificBinding(
            subject=request.context.subject,
            scenario=request.context.scenario,
            config_content_hash=resolved.config_source.content_hash,
            setup_content_hash=request.context.setup_content_hash,
        )
        if isinstance(expected.subject, UnboundSubject) and expected.scenario is None:
            raise BackendConflict(
                "physical calibration checks require a declared subject"
            )
        validate_scientific_binding(
            expected,
            resolved.config,
            sample_service=self._samples,
            targets=self._targets,
        )
        if expected.sample_selectors() != samples:
            raise BackendConflict(
                "check subject differs from procedure sample selection"
            )
        if binding is not None and binding != expected:
            raise BackendConflict(
                "check context differs from procedure scientific binding"
            )


def require_check_measurement(
    intent: Mapping[str, object], submission: RunSubmission
) -> None:
    request = declared_check(intent)
    child = submission.procedure_child
    if request is None or child is None or child.step_key != request.measurement_step:
        return
    source = submission.config_source
    binding = submission.scientific_binding
    _require_context(request, source, binding)


def _require_context(
    request: CalibrationCheckRequest,
    source: RunConfigSource | None,
    binding: ResolvedScientificBinding,
) -> None:
    if (
        not isinstance(source, ParameterRunConfigSource)
        or source.overrides
        or CalibrationContext(
            source.parameters,
            binding.subject,
            binding.setup_content_hash,
            binding.scenario,
        )
        != request.context
    ):
        raise BackendConflict("check measurement differs from admitted declaration")


def require_check_completion(
    connection: sqlite3.Connection,
    *,
    run: ProcedureRun,
    step_key: str,
    output: ProcedureStepOutputRef,
    procedures: SQLiteAutomationStore,
    runs: SQLiteRunRepository,
) -> None:
    """Validate retained evidence before recording a declared step as complete.

    Ordinary analyses remain independently publishable. This transaction is the
    point at which an execution adopts a publication as its calibration result.
    """
    request = declared_check(run.intent)
    if request is None or step_key not in (
        request.measurement_step,
        request.analysis_step,
    ):
        return
    try:
        if step_key == request.measurement_step:
            if not isinstance(output, RunOutputRef):
                raise BackendConflict("check measurement requires a run output")
            snapshot = runs.read_snapshot_in_transaction(connection, output.run_id)
            _require_context(
                request, snapshot.config_source, snapshot.scientific_binding
            )
            return
        measurement = procedures.latest_step_attempt_in_transaction(
            connection,
            run.procedure_run_id,
            request.measurement_step,
        )
        if (
            measurement is None
            or measurement.state != "succeeded"
            or not isinstance(measurement.output, RunOutputRef)
        ):
            raise BackendConflict("check result requires its completed measurement")
        subject = RunAnalysisSubject(run_id=measurement.output.run_id)
        _read_result(request, subject, output, runs)
    except (NotFound, TypeError, ValueError) as error:
        raise BackendConflict(f"invalid calibration check evidence: {error}") from error


def _read_result(
    request: CalibrationCheckRequest,
    subject: RunAnalysisSubject,
    output: ProcedureStepOutputRef,
    runs: SQLiteRunRepository,
) -> CalibrationCheckResult:
    if (
        not isinstance(output, AnalysisPublicationOutputRef)
        or output.subject != subject
    ):
        raise BackendConflict("check result must analyze its declared measurement")
    publication = runs.read_analysis_publication(
        subject.run_id, output.analysis_record_id
    )
    record = runs.read_model(
        subject.run_id,
        record_content_ref(record_id=publication.record.id, kind="analysis"),
        AnalysisRecord,
    )
    fact_output = next(
        (item for item in record.outputs if item.id == request.result_output), None
    )
    if not isinstance(fact_output, AnalysisFactRecordOutput):
        raise BackendConflict("check result requires its declared fact output")
    fact = fact_output.content
    if (
        fact.schema_id != CHECK_RESULT.id
        or fact.schema_codec != CHECK_RESULT.schema_codec
        or fact.schema_hash != CHECK_RESULT.schema_hash
        or fact.codec != PYTHON_JSON_CODEC
    ):
        raise BackendConflict("check result requires the standard result schema")
    result = CHECK_RESULT.decode(fact.value)
    if result.scope != request.scope:
        raise BackendConflict("check result differs from its declared scope")
    return result


class CalibrationCheckQueries:
    """One consistent page of executions, declared context and retained evidence."""

    def __init__(self, sqlite: SQLiteDatabase, runs: SQLiteRunRepository) -> None:
        self._sqlite = sqlite
        self._runs = runs
        self._procedures = SQLiteAutomationStore(sqlite)
        self._checks = CalibrationCheckStore()

    def query(self, query: CalibrationCheckQuery) -> CalibrationCheckPage:
        with self._sqlite.read_transaction() as connection:
            page = self._checks.query_in_transaction(connection, query)
            return CalibrationCheckPage(
                items=tuple(self._view(connection, run) for run in page.items),
                next_cursor=page.next_cursor,
            )

    def report(self, query: CalibrationReportQuery) -> CalibrationReport:
        items: list[CalibrationRequirementStatus] = []
        with self._sqlite.read_transaction() as connection:
            now = datetime.now(UTC)
            for requirement in query.requirements:
                page = self._checks.query_in_transaction(
                    connection,
                    CalibrationCheckQuery(
                        scope=requirement.scope,
                        context=query.context,
                        limit=query.history_limit,
                    ),
                )
                views = tuple(self._view(connection, run) for run in page.items)
                unresolved = tuple(
                    item.execution.procedure_run_id
                    for item in views
                    if item.evidence is None
                )
                reasons: list[Literal["scan_limit", "unresolved_checks"]] = []
                if page.next_cursor is not None:
                    reasons.append("scan_limit")
                if unresolved:
                    reasons.append("unresolved_checks")
                selection = select_calibration_check(
                    tuple(item.evidence for item in views if item.evidence is not None),
                    requested_scope=requirement.scope,
                    current=query.context,
                    now=now,
                    max_age=requirement.max_age,
                    history_complete=not reasons,
                )
                items.append(
                    CalibrationRequirementStatus(
                        requirement=requirement,
                        selection=selection,
                        availability=CapabilityAvailability(selection.status, ()),
                        scanned=len(views),
                        unresolved_procedures=unresolved,
                        incomplete_reasons=tuple(reasons),
                    )
                )
            availability = assess_capability_dependencies(
                {item.id: item.depends_on for item in query.requirements},
                {item.requirement.id: item.selection.status for item in items},
            )
            return CalibrationReport(
                context=query.context,
                observed_at=now,
                items=tuple(
                    item.model_copy(
                        update={"availability": availability[item.requirement.id]}
                    )
                    for item in items
                ),
            )

    def preview_task(self, preview: CalibrationTaskPreview) -> CalibrationTaskProgress:
        with self._sqlite.read_transaction() as connection:
            return self.preview_in_transaction(connection, preview)

    def preview_in_transaction(
        self, connection: sqlite3.Connection, preview: CalibrationTaskPreview
    ) -> CalibrationTaskProgress:
        executions: dict[str, tuple[ProcedureRun, CheckEvidence | None]] = {}
        for stage in preview.plan.stages:
            procedure_id = preview.executions.get(stage.id)
            if procedure_id is None:
                continue
            try:
                run = self._procedures.read_run_in_transaction(connection, procedure_id)
            except AutomationNotFound as error:
                raise BackendConflict("task stage execution was not found") from error
            view = self._view(connection, run)
            if view.request != stage.check:
                raise BackendConflict(
                    f"task stage {stage.id!r} differs from its declared check"
                )
            executions[stage.id] = (run, view.evidence)
        return assess_calibration_task(preview.plan, executions)

    def observe(
        self, observation: CalibrationCheckObservation
    ) -> CalibrationCheckObservationResult:
        query = CalibrationCheckQuery(
            scope=observation.scope, context=observation.context, limit=1
        )
        with self._sqlite.read_transaction() as connection:
            head = self._checks.query_in_transaction(connection, query)
            current = self._checks.revisions_in_transaction(
                connection,
                query,
                tuple(observation.revisions),
            )
            return CalibrationCheckObservationResult(
                head_changed=(head.items[0].procedure_run_id if head.items else None)
                != observation.head,
                changed_procedures=tuple(
                    procedure_id
                    for procedure_id, revision in observation.revisions.items()
                    if current.get(procedure_id) != revision
                ),
            )

    def _view(
        self, connection: sqlite3.Connection, run: ProcedureRun
    ) -> CalibrationCheckView:
        request = declared_check(run.intent)
        if request is None:
            raise BackendConflict("indexed check is missing its declaration")
        measurement = self._procedures.latest_step_attempt_in_transaction(
            connection,
            run.procedure_run_id,
            request.measurement_step,
        )
        analysis = self._procedures.latest_step_attempt_in_transaction(
            connection,
            run.procedure_run_id,
            request.analysis_step,
        )
        evidence: CheckEvidence | None = None
        if (
            measurement is not None
            and measurement.state == "succeeded"
            and isinstance(measurement.output, RunOutputRef)
            and analysis is not None
            and analysis.state == "succeeded"
            and isinstance(analysis.output, AnalysisPublicationOutputRef)
        ):
            try:
                snapshot = self._runs.read_snapshot_in_transaction(
                    connection, measurement.output.run_id
                )
                _require_context(
                    request, snapshot.config_source, snapshot.scientific_binding
                )
                result = _read_result(
                    request,
                    RunAnalysisSubject(run_id=snapshot.run_id),
                    analysis.output,
                    self._runs,
                )
                evidence = CheckEvidence(
                    snapshot,
                    result.scope,
                    analysis.output.analysis_record_id,
                    result.passed,
                )
            except (NotFound, TypeError, ValueError) as error:
                raise BackendConflict(
                    f"invalid calibration check evidence: {error}"
                ) from error
        return CalibrationCheckView(execution=run, request=request, evidence=evidence)
