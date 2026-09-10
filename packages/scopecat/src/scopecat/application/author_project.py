"""Revision-aware notebook entry point; fresh workers own all project imports."""

from __future__ import annotations

import os
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, SupportsFloat, override
from uuid import uuid4

import httpx2
from pydantic import JsonValue

from scopecat.analysis.facts import ordinary_result_schema
from scopecat.api._config import LabConfigOperations
from scopecat.api._remote import RemoteRunOperations
from scopecat.api.parameters import ParameterWorkspace
from scopecat.api.published_analysis import AnalysisResult
from scopecat.api.run import RunHandle
from scopecat.application.experiment_plans import plan_definition, plan_launch_request
from scopecat.application.launch import (
    LaunchCatalog,
    LaunchField,
    LaunchPreview,
    LaunchSubmission,
)
from scopecat.automation.models import ProcedureRun, RunOutputRef
from scopecat.automation.wire import (
    ProcedureCancelCommand,
    ProcedureRunListQuery,
    ProcedureStepAttemptListQuery,
)
from scopecat.daemon.client import DaemonClient, DaemonUnavailableError
from scopecat.kernel.errors import SessionClosedError
from scopecat.kernel.quantity import Quantity
from scopecat.records.author_revision import (
    AuthorAnalysisReceipt,
    AuthorAnalysisRequest,
    AuthorRevisionRef,
    AuthorRevisionState,
)
from scopecat.records.config_context import ConfigContextRef
from scopecat.records.control_edit import ControlEdit
from scopecat.records.experiment_plan import ExperimentPlanRevision, ExperimentPlanSave
from scopecat.records.launch_request import LaunchRequest
from scopecat.records.parameter_update import ParameterUpdate
from scopecat.records.plan_ref import ExperimentPlanRef, PlanAnalysisSource
from scopecat.records.run_request import AxisValuesSourceRecord


class AuthorProject(DaemonClient):
    """Refresh and execute author code without mutating notebook module state.

    Prepared requests retain their source revision. Typed analysis uses the run's
    original source by default; explicitly refresh and select current to reanalyze.
    """

    def __init__(
        self,
        base_url: str,
        *,
        receipts: Path | None = None,
        timeout: float | httpx2.Timeout | None = 120,
        transport: httpx2.BaseTransport | None = None,
    ) -> None:
        super().__init__(base_url, timeout=timeout, transport=transport)
        self.receipts = receipts.resolve() if receipts is not None else None

    @property
    def run_operations(self) -> RemoteRunOperations:
        return RemoteRunOperations(self)

    @property
    def config(self) -> LabConfigOperations:
        return LabConfigOperations(self, self.run_operations, None, "operator")

    def run(self, run_id: str) -> RunHandle:
        """Reconnect a retained run without importing its original author module."""
        return RunHandle(self, run_id)

    def reopen(self, receipt: str | Path) -> AuthorJob:
        """Read a saved job receipt; recovery is read-only and never resubmits."""
        path = Path(receipt).resolve()
        return AuthorJob(
            self,
            path,
            LaunchRequest.model_validate_json(path.read_text(encoding="utf-8")),
        )

    @override
    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str | int] | None = None,
        json: object | None = None,
        content: bytes | Iterable[bytes] | None = None,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> httpx2.Response:
        if self.is_closed:
            raise SessionClosedError(
                "Author session is closed. Open project.authoring() again, then "
                "job.reconnect(session) or session.run(run_id) for retained data."
            )
        return super()._request(
            method,
            path,
            params=params,
            json=json,
            content=content,
            headers=headers,
            timeout=timeout,
        )

    def prepare(
        self,
        experiment: str,
        *,
        control_edits: dict[str, ControlEdit] | None = None,
        fixed: Mapping[str, SupportsFloat | Quantity] | None = None,
        scans: Mapping[str, Iterable[SupportsFloat | Quantity]] | None = None,
        parameters: ParameterWorkspace | None = None,
        code_revision: AuthorRevisionRef | None = None,
        inputs: dict[str, JsonValue] | None = None,
        context: ConfigContextRef | None = None,
        overrides: tuple[ParameterUpdate, ...] = (),
        sample: str | None = None,
        actor: str = "operator",
    ) -> AuthorPreparedLaunch:
        """Select the current declaration and retain a preview's exact submission."""
        catalog = self.catalog(code_revision=code_revision)
        edits = dict(control_edits or {})
        for name, value in (fixed or {}).items():
            if name in edits:
                raise ValueError(f"{name}: choose one fixed or scanned input")
            edits[name] = ControlEdit(mode="fixed", value=_control_value(value))
        for name, values in (scans or {}).items():
            if name in edits:
                raise ValueError(f"{name}: choose one fixed or scanned input")
            edits[name] = ControlEdit(
                mode="scan",
                axis=AxisValuesSourceRecord(
                    values=[_control_value(value) for value in values]
                ),
            )
        if parameters is not None:
            if context is not None or overrides or sample is not None:
                raise ValueError("parameters already selects the context and sample")
            frozen = parameters.freeze()
            context = frozen.config_source.context
            overrides = frozen.config_source.overrides
            sample = frozen.config_source.sample.sample_id
        entry = next((item for item in catalog.entries if item.id == experiment), None)
        if entry is None:
            raise ValueError(
                f"Unknown experiment {experiment!r}; "
                "inspect author.catalog() for available names"
            )
        declared_inputs = {
            name: field.default
            for name, field in entry.request.properties.items()
            if isinstance(field, LaunchField) and "default" in field.model_fields_set
        }
        request = LaunchRequest(
            action="preview",
            experiment=entry.id,
            version=entry.version,
            control_edits=edits,
            inputs=declared_inputs | (inputs or {}),
            context=context,
            overrides=overrides,
            sample=sample,
            actor=actor,
            code_revision=catalog.code_revision,
        )
        return AuthorPreparedLaunch(self, request, self.preview(request))

    def prepare_plan(
        self, ref: ExperimentPlanRef, *, actor: str
    ) -> AuthorPreparedLaunch:
        """Read an exact plan and obtain a new preview for this execution actor."""
        request = plan_launch_request(self.experiment_plan(ref), actor=actor)
        return AuthorPreparedLaunch(self, request, self.preview(request))

    def state(self) -> AuthorRevisionState:
        return self.author_revision_state()

    def refresh(self, *, expected_generation: int | None = None) -> AuthorRevisionState:
        """Explicitly validate current source and select it for future prepares.

        Existing prepared requests keep their original code. A concurrent refresh
        still conflicts; the managed call does not silently retry that decision.
        """
        generation = (
            self.state().generation
            if expected_generation is None
            else expected_generation
        )
        return self.refresh_authors(expected_generation=generation)

    def catalog(
        self, *, code_revision: AuthorRevisionRef | None = None
    ) -> LaunchCatalog:
        return self._get_model(
            "/api/v1/experiment-launcher",
            LaunchCatalog,
            params={"code_revision": code_revision.content_hash}
            if code_revision
            else {},
        )

    def preview(self, request: LaunchRequest) -> LaunchPreview:
        return self._post_model(
            "/api/v1/experiment-launcher/preview", request, LaunchPreview
        )

    def submit(self, request: LaunchRequest) -> LaunchSubmission:
        return self._post_model(
            "/api/v1/experiment-launcher/submit", request, LaunchSubmission
        )

    def analyze(
        self,
        run_id: str,
        analysis: str,
        *,
        code_revision: AuthorRevisionRef,
        key: str | None = None,
        arguments: Mapping[str, JsonValue] | None = None,
    ) -> AuthorAnalysisReceipt:
        return self.analyze_author_revision(
            AuthorAnalysisRequest(
                run_id=run_id,
                analysis=analysis,
                code_revision=code_revision,
                key=key,
                arguments=dict(arguments or {}),
            )
        )

    def analyze_as[ResultT](
        self,
        run_id: str,
        analysis: str,
        result_type: type[ResultT],
        *,
        source: Literal["original", "current"] = "original",
        arguments: Mapping[str, JsonValue] | None = None,
        key: str | None = None,
    ) -> AnalysisResult[ResultT]:
        """Publish registered analysis and reconstruct a materialized conclusion.

        Original source comes from the retained run. For edited source, call
        refresh() explicitly, then select source="current". Changed arguments
        or source create a separate revision; identical publication may reuse
        its existing receipt. Direct notebook function imports are never sent.
        """
        schema = ordinary_result_schema(result_type)
        run = self.run(run_id)
        if source == "original":
            revision_hash = run.request.metadata.get("author_code_revision")
            if not isinstance(revision_hash, str):
                raise ValueError(
                    "Run has no retained author source; "
                    "explicitly select source='current'"
                )
            revision = AuthorRevisionRef(content_hash=revision_hash)
        elif source == "current":
            revision = self.state().active
            if revision is None:
                raise ValueError("Refresh author source before analysis")
        else:
            raise ValueError("analysis source must be original or current")
        try:
            receipt = self.analyze(
                run_id, analysis, code_revision=revision, arguments=arguments, key=key
            )
        except httpx2.HTTPStatusError as error:
            error.add_note(error.response.text)
            raise
        publication = run.published_analysis(receipt.analysis_id)
        return AnalysisResult(publication.fact_as("result", schema), publication)


@dataclass(frozen=True, slots=True)
class AuthorPreparedLaunch:
    client: AuthorProject
    request: LaunchRequest
    preview: LaunchPreview

    def save_plan(
        self,
        name: str,
        *,
        saved_by: str,
        previous: ExperimentPlanRef | None = None,
        copied_from: ExperimentPlanRef | None = None,
        source: PlanAnalysisSource | None = None,
    ) -> ExperimentPlanRevision:
        """Save immutable inputs/configuration; no run is admitted or activated."""
        return self.client.save_experiment_plan(
            ExperimentPlanSave(
                name=name,
                saved_by=saved_by,
                previous=previous,
                copied_from=copied_from,
                definition=plan_definition(self.request, self.preview, source=source),
            )
        )

    def run(self) -> AuthorJob:
        """Persist a receipt, then submit this exact preview once.

        An uncertain response exposes its job for read-only recovery. Re-running
        this method intentionally creates a new acquisition; retain the job.
        """
        if self.client.receipts is None:
            raise ValueError(
                "Use project.authoring() or configure a receipts directory"
            )
        key = str(uuid4())
        request = self._submission_request(key)
        job = AuthorJob.retain(
            self.client, self.client.receipts / f"{key}.json", request
        )
        try:
            submitted = self.client.submit(request)
        except httpx2.HTTPStatusError as error:
            if error.response.status_code < 500:
                raise
            raise AuthorSubmissionUncertain(job) from error
        except (httpx2.TransportError, DaemonUnavailableError) as error:
            raise AuthorSubmissionUncertain(job) from error
        if submitted.dispatch_error is not None:
            raise AuthorJobAttention(submitted.dispatch_error, job=job)
        return job

    def _submission_request(self, request_key: str) -> LaunchRequest:
        return LaunchRequest.model_validate(
            {
                **self.request.model_dump(),
                "action": "submit",
                "request_key": request_key,
                "expected_request_hash": self.preview.request_hash,
                "config_source": self.preview.config_source,
                "code_revision": self.preview.code_revision,
                "manual_state": self.preview.manual_state,
            }
        )

    def submit(self, *, request_key: str) -> LaunchSubmission:
        """Low-level submission retaining the explicitly selected request identity."""
        return self.client.submit(self._submission_request(request_key))


class AuthorJobTimeout(TimeoutError):
    """The wait ended; execution has not been cancelled."""


class AuthorJobAttention(RuntimeError):
    """The procedure needs a person's decision; no retry was attempted."""

    def __init__(self, reason: str, *, job: AuthorJob) -> None:
        self.job = job
        super().__init__(reason)


class AuthorJobCancelled(RuntimeError):
    """The procedure has durably closed as cancelled."""


class AuthorJobFailed(RuntimeError):
    """The procedure has durably closed as failed."""


class AuthorSubmissionUncertain(RuntimeError):
    """A submission response was lost. Recover this receipt without resubmitting."""

    def __init__(self, job: AuthorJob) -> None:
        self.job = job
        super().__init__(
            f"Submission outcome is unknown. Reopen {job.receipt} and call recover(); "
            "do not start another run."
        )


@dataclass(frozen=True, slots=True)
class AuthorJob:
    """A locally retained request identity, connected to one session.

    The receipt is written and flushed before submission. It contains the exact
    reviewed request, not result data or a second procedure database. Reopening
    only queries the daemon's existing request-key index; it never submits.
    """

    client: AuthorProject
    receipt: Path
    request: LaunchRequest

    @classmethod
    def retain(
        cls, client: AuthorProject, receipt: Path, request: LaunchRequest
    ) -> AuthorJob:
        receipt.parent.mkdir(parents=True, exist_ok=True)
        with receipt.open("x", encoding="utf-8") as stream:
            stream.write(request.model_dump_json())
            stream.flush()
            os.fsync(stream.fileno())
        return cls(client, receipt, request)

    def reconnect(self, session: AuthorProject) -> AuthorJob:
        """Attach this receipt to a new open session without submission."""
        return session.reopen(self.receipt)

    def recover(self, *, timeout: float | None = None) -> ProcedureRun | None:
        """Read the original admission, or None if not yet found. Never retry."""
        page = self.client.list_procedures(
            ProcedureRunListQuery(request_key=self.request.request_key, limit=1),
            timeout=timeout,
        )
        return page.items[0] if page.items else None

    @property
    def snapshot(self) -> ProcedureRun:
        snapshot = self.recover()
        if snapshot is None:
            raise AuthorSubmissionUncertain(self)
        return snapshot

    @property
    def id(self) -> str:
        return self.snapshot.procedure_run_id

    def wait(self, *, timeout: float = 60, interval: float = 0.2) -> AuthorJob:
        """Wait up to timeout seconds; attention and cancellation are distinct.

        A timeout leaves execution alone. Polling requests are capped by the
        remaining wait budget, rather than the session's longer launch deadline.
        """
        if timeout < 0 or interval <= 0:
            raise ValueError("timeout must be nonnegative and interval positive")
        deadline = time.monotonic() + timeout
        while True:
            try:
                snapshot = self.recover(timeout=max(0.001, deadline - time.monotonic()))
            except httpx2.TimeoutException as error:
                raise AuthorJobTimeout(
                    "Wait ended; execution was not cancelled"
                ) from error
            if snapshot is None:
                raise AuthorSubmissionUncertain(self)
            if snapshot.state in {"attention_required", "waiting_for_input"}:
                raise AuthorJobAttention(
                    snapshot.attention_reason or snapshot.state, job=self
                )
            if snapshot.closure is not None:
                if snapshot.closure.status == "cancelled":
                    raise AuthorJobCancelled(snapshot.closure.reason)
                if snapshot.closure.status == "failed":
                    raise AuthorJobFailed(snapshot.closure.reason)
                return self
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AuthorJobTimeout(
                    f"Wait ended for {snapshot.procedure_run_id}; execution continues."
                )
            time.sleep(min(interval, remaining))

    def cancel(self, *, reason: str = "Cancelled by author") -> ProcedureRun:
        """Request a stop after the current step settles."""
        snapshot = self.snapshot
        return self.client.cancel_procedure(
            ProcedureCancelCommand(
                procedure_run_id=snapshot.procedure_run_id,
                expected_run_revision=snapshot.revision,
                actor=self.request.actor,
                reason=reason,
            )
        ).run

    def result(self, *, step: str = "experiment") -> RunHandle:
        """Open a retained successful acquisition, even if later analysis failed.

        Lazy dataset reads require this session. Materialized arrays/tables and
        run snapshots remain ordinary local values after it closes. Use
        reconnect(new_session).result() to read more retained data.
        """
        cursor: int | None = None
        procedure_id = self.id
        while True:
            page = self.client.list_procedure_step_attempts(
                procedure_id, ProcedureStepAttemptListQuery(limit=200, cursor=cursor)
            )
            for attempt in page.items:
                if attempt.step_key == step:
                    if isinstance(attempt.output, RunOutputRef):
                        return self.client.run(attempt.output.run_id)
                    raise RuntimeError(f"Step {step!r} has no retained acquisition")
            if page.next_cursor is None:
                raise KeyError(f"Procedure has no step {step!r}")
            cursor = page.next_cursor


def _control_value(value: SupportsFloat | Quantity) -> float | Quantity:
    if isinstance(value, Quantity):
        return value
    if isinstance(value, bool):
        raise TypeError("controls require numeric values, not booleans")
    return float(value)
