"""Read declared calibration checks without importing laboratory author code."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from scopecat.analysis.calibration import CHECK_RESULT as CHECK_RESULT
from scopecat.automation import ProcedureRun
from scopecat.automation.calibration import (
    CheckEvidence,
    CheckSelection,
    select_calibration_check,
)
from scopecat.daemon.calibration_checks import (
    MAX_CHECK_OBSERVATIONS,
    CalibrationCheckObservation,
    CalibrationCheckQuery,
)
from scopecat.daemon.client import DaemonClient
from scopecat.records.calibration_check import (
    CalibrationCheckRequest,
    CalibrationContext,
    CalibrationScope,
)


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
    """Bounded domain queries over server-indexed check declarations.

    This adapter is observational, not a publication fence or transaction snapshot.
    Only the standard calibration_check intent declaration identifies a check.
    Opaque procedures are not inferred from names or Python definitions.
    """

    def __init__(
        self,
        client: DaemonClient,
    ) -> None:
        self._client = client

    def history(
        self,
        *,
        scope: CalibrationScope | None = None,
        context: CalibrationContext | None = None,
        max_requests: int = 200,
        page_size: int = 50,
    ) -> CalibrationCheckHistory:
        """Filter by declared intent even before any acquisition has started."""
        if not 1 <= max_requests <= MAX_CHECK_OBSERVATIONS or not 1 <= page_size <= 200:
            raise ValueError(
                f"history requires budget 1..{MAX_CHECK_OBSERVATIONS} "
                "and page size 1..200"
            )
        requests: list[DeclaredCheck] = []
        evidence: list[CheckEvidence] = []
        unresolved: list[str] = []
        cursor: int | None = None
        scanned = 0
        first_id: str | None = None
        exhausted = False
        while scanned < max_requests:
            page = self._client.query_calibration_checks(
                CalibrationCheckQuery(
                    scope=scope,
                    context=context,
                    limit=min(page_size, max_requests - scanned),
                    cursor=cursor,
                )
            )
            if scanned == 0 and page.items:
                first_id = page.items[0].execution.procedure_run_id
            scanned += len(page.items)
            for item in page.items:
                requests.append(DeclaredCheck(item.execution, item.request))
                if item.evidence is None:
                    unresolved.append(item.execution.procedure_run_id)
                else:
                    evidence.append(item.evidence)
            if page.next_cursor is None:
                exhausted = True
                break
            cursor = page.next_cursor
        observation = self._client.observe_calibration_checks(
            CalibrationCheckObservation(
                scope=scope,
                context=context,
                head=first_id,
                revisions={
                    item.execution.procedure_run_id: item.execution.revision
                    for item in requests
                },
            )
        )
        for procedure_id in observation.changed_procedures:
            if procedure_id not in unresolved:
                unresolved.append(procedure_id)
        reasons: list[
            Literal["scan_limit", "unresolved_checks", "journal_changed"]
        ] = []
        if not exhausted:
            reasons.append("scan_limit")
        if unresolved:
            reasons.append("unresolved_checks")
        if observation.changed_procedures or observation.head_changed:
            reasons.append("journal_changed")
        return CalibrationCheckHistory(
            tuple(requests), tuple(evidence), tuple(unresolved), scanned, tuple(reasons)
        )
