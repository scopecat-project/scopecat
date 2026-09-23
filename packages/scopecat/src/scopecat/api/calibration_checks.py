"""Read declared calibration checks without importing laboratory author code."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal, Protocol

from scopecat.analysis.facts import AnalysisFactSchema
from scopecat.api.procedures import LabProcedureOperations
from scopecat.api.run import RunHandle
from scopecat.automation import AnalysisPublicationOutputRef, ProcedureRun, RunOutputRef
from scopecat.automation.calibration import (
    CheckEvidence,
    CheckSelection,
    select_calibration_check,
)
from scopecat.automation.wire import ProcedureRunListQuery
from scopecat.daemon.client import DaemonClient
from scopecat.kernel.frozen import thaw_json_value
from scopecat.records.calibration_check import (
    CalibrationCheckRequest,
    CalibrationCheckResult,
    CalibrationContext,
    CalibrationScope,
)
from scopecat.records.run import ParameterRunConfigSource

CHECK_RESULT = AnalysisFactSchema(
    "scopecat.calibration-check-result.v1", CalibrationCheckResult
)


class _CheckSession(Protocol):
    def get_run(self, run: str) -> RunHandle: ...


@dataclass(frozen=True)
class DeclaredCheck:
    execution: ProcedureRun
    request: CalibrationCheckRequest


@dataclass(frozen=True)
class CalibrationCheckHistory:
    requests: tuple[DeclaredCheck, ...]
    evidence: tuple[CheckEvidence, ...]
    unresolved_procedures: tuple[str, ...]
    scanned: int
    incomplete_reasons: tuple[
        Literal["scan_limit", "unresolved_checks", "journal_changed"], ...
    ]

    @property
    def complete(self) -> bool:
        return not self.incomplete_reasons

    def select(
        self,
        *,
        requested_scope: CalibrationScope,
        current: CalibrationContext,
        now: datetime,
        max_age: timedelta,
    ) -> CheckSelection:
        return select_calibration_check(
            self.evidence,
            requested_scope=requested_scope,
            current=current,
            now=now,
            max_age=max_age,
            history_complete=self.complete,
        )


class LabCalibrationChecks:
    """Bounded domain queries over declared checks in the procedure journal.

    This adapter is observational, not a publication fence or a specialized index.
    Only the standard calibration_check intent declaration identifies a check.
    Opaque procedures are not inferred from names or Python definitions.
    """

    def __init__(
        self,
        client: DaemonClient,
        procedures: LabProcedureOperations,
        session: _CheckSession,
    ) -> None:
        self._client = client
        self._procedures = procedures
        self._session = session

    def history(
        self,
        *,
        scope: CalibrationScope | None = None,
        context: CalibrationContext | None = None,
        max_requests: int = 200,
        page_size: int = 50,
    ) -> CalibrationCheckHistory:
        """Filter by declared intent even before any acquisition has started."""
        if max_requests < 1 or not 1 <= page_size <= 200:
            raise ValueError("history requires a positive budget and page size 1..200")
        requests: list[DeclaredCheck] = []
        evidence: list[CheckEvidence] = []
        unresolved: list[str] = []
        cursor: int | None = None
        scanned = 0
        first_id: str | None = None
        exhausted = False
        while scanned < max_requests:
            page = self._client.list_procedures(
                ProcedureRunListQuery(
                    limit=min(page_size, max_requests - scanned),
                    cursor=cursor,
                )
            )
            if scanned == 0 and page.items:
                first_id = page.items[0].procedure_run_id
            scanned += len(page.items)
            for run in page.items:
                if "calibration_check" not in run.intent:
                    continue
                request = CalibrationCheckRequest.model_validate(
                    thaw_json_value(run.intent["calibration_check"])
                )
                if (scope is not None and request.scope != scope) or (
                    context is not None and request.context != context
                ):
                    continue
                requests.append(DeclaredCheck(run, request))
                item = self._read(run, request)
                if item is None:
                    unresolved.append(run.procedure_run_id)
                else:
                    evidence.append(item)
            if page.next_cursor is None:
                exhausted = True
                break
            cursor = page.next_cursor
        changed = False
        for item in requests:
            run = item.execution
            if self._procedures.snapshot(run.procedure_run_id).revision != run.revision:
                changed = True
                if run.procedure_run_id not in unresolved:
                    unresolved.append(run.procedure_run_id)
        latest = self._client.list_procedures(ProcedureRunListQuery(limit=1))
        latest_id = latest.items[0].procedure_run_id if latest.items else None
        reasons: list[
            Literal["scan_limit", "unresolved_checks", "journal_changed"]
        ] = []
        if not exhausted:
            reasons.append("scan_limit")
        if unresolved:
            reasons.append("unresolved_checks")
        if changed or latest_id != first_id:
            reasons.append("journal_changed")
        return CalibrationCheckHistory(
            tuple(requests), tuple(evidence), tuple(unresolved), scanned, tuple(reasons)
        )

    def _read(
        self, run: ProcedureRun, request: CalibrationCheckRequest
    ) -> CheckEvidence | None:
        handle = self._procedures.get(run.procedure_run_id)
        try:
            measurement = handle.step(request.measurement_step)
            analysis = handle.step(request.analysis_step)
        except KeyError:
            return None
        if not isinstance(measurement.output, RunOutputRef) or not isinstance(
            analysis.output, AnalysisPublicationOutputRef
        ):
            return None
        measured = self._session.get_run(measurement.output.run_id)
        snapshot = measured.snapshot
        binding = snapshot.scientific_binding
        source = snapshot.config_source
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
            raise ValueError("check measurement does not match its declared context")
        report = measured.published_analysis(analysis.output.analysis_record_id)
        result = report.fact_as(request.result_output, CHECK_RESULT)
        if result.scope != request.scope:
            raise ValueError("check result does not match its declared scope")
        return CheckEvidence(snapshot, result.scope, report.id, result.passed)
