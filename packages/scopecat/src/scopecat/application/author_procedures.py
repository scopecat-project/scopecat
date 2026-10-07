"""Ordinary procedure submissions owned by the application, never the kernel."""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

import httpx2
from pydantic import BaseModel

from scopecat.application.launch import LaunchSubmission
from scopecat.automation.definition import ProcedureDefinition
from scopecat.automation.models import (
    ProcedureRun,
    ProcedureSource,
    ProcedureStepOutputRef,
)
from scopecat.automation.wire import (
    ProcedureCancelCommand,
    ProcedureRunListQuery,
    ProcedureRunPage,
    ProcedureStepAttemptListQuery,
    ProcedureStepAttemptPage,
    ProcedureSubmitCommand,
)
from scopecat.daemon.client import DaemonClient
from scopecat.daemon.procedure_views import ProcedureOperatorView
from scopecat.kernel.errors import SessionClosedError


@dataclass(frozen=True, slots=True)
class AuthorProcedureOperations:
    session: DaemonClient
    project_root: Path | None

    def prepare[IntentT: BaseModel](
        self,
        definition: ProcedureDefinition[IntentT],
        intent: IntentT,
        *,
        request_key: str,
    ) -> PreparedAuthorProcedure:
        """Freeze one source-bound command. No imports or execution are refreshed.

        Use sc.notebook(), or explicitly refresh project.authoring() and import
        the definition and intent afterwards. Saved source changes require a new
        explicit refresh/reimport before preparing another command. Intent owns
        scientific choices; session experiment defaults are not applied here.
        """
        from scopecat.application.author_imports import require_procedure_source

        if self.session.is_closed:
            raise SessionClosedError("Cannot prepare a procedure on a closed session")
        root = self.project_root
        if root is None:
            raise ValueError("Use project.authoring() or sc.notebook()")
        revision = self.session.current_author_source()
        require_procedure_source(definition, project_root=root, revision=revision)
        health = self.session.health()
        return PreparedAuthorProcedure(
            self.session,
            ProcedureSubmitCommand(
                request_key=request_key,
                definition=definition.ref,
                intent=definition.encode_intent(intent),
                source=ProcedureSource(
                    workspace_id=self.session.workspace_id, code_revision=revision
                ),
            ),
            health.project_id,
            health.deployment_id,
        )

    def list(self, *, limit: int = 50, cursor: int | None = None) -> ProcedureRunPage:
        """Read a bounded history page without loading source or starting work."""
        return self.session.list_procedures(
            ProcedureRunListQuery(limit=limit, cursor=cursor)
        )

    def get(self, procedure_id: str) -> AuthorProcedure:
        """Reconnect by durable identity without execution or source loading."""
        self.session.get_procedure(procedure_id)
        return AuthorProcedure(self.session, procedure_id)


@dataclass(frozen=True, slots=True)
class PreparedAuthorProcedure:
    session: DaemonClient
    command: ProcedureSubmitCommand
    project_id: str
    deployment_id: str

    def submit(self) -> AuthorProcedure:
        """Retry this exact command after an uncertain response; retain its key.

        The application checks the retained registry and intent before admission,
        then owns dispatch. This object and its command remain pinned even when
        source files change. No procedure code executes in this client.
        """
        self._require_application(self.session)
        result = self.session.submit_author_procedure(self.command)
        return AuthorProcedure(self.session, result.procedure_id, result.dispatch_error)

    def reconnect(self, session: DaemonClient) -> PreparedAuthorProcedure:
        assert self.command.source is not None
        if session.workspace_id != self.command.source.workspace_id:
            raise ValueError("Prepared procedure belongs to another workspace")
        self._require_application(session)
        return PreparedAuthorProcedure(
            session, self.command, self.project_id, self.deployment_id
        )

    def _require_application(self, session: DaemonClient) -> None:
        health = session.health()
        if (health.project_id, health.deployment_id) != (
            self.project_id,
            self.deployment_id,
        ):
            raise ValueError(
                "Prepared procedure belongs to another data store or deployment"
            )


@dataclass(frozen=True, slots=True)
class AuthorProcedure:
    session: DaemonClient
    id: str
    dispatch_error: str | None = None

    @property
    def snapshot(self) -> ProcedureRun:
        return self.session.get_procedure(self.id)

    def progress(self) -> ProcedureOperatorView:
        return self.session.procedure_progress(self.id)

    def wait(self, *, timeout: float = 60, interval: float = 0.2) -> ProcedureRun:
        """Observe until closed, waiting for input or requiring attention.

        A failed scientific decision is a retained closure, not a client exception.
        Timeout only stops waiting; it neither cancels nor retries the procedure.
        """
        if timeout < 0 or interval <= 0:
            raise ValueError("timeout must be nonnegative and interval positive")
        deadline = time.monotonic() + timeout
        while True:
            try:
                snapshot = self.session.get_procedure(
                    self.id, timeout=max(0.001, deadline - time.monotonic())
                )
            except httpx2.TimeoutException as error:
                raise TimeoutError("Wait ended; procedure was not cancelled") from error
            if snapshot.closure is not None or snapshot.state in {
                "waiting_for_input",
                "attention_required",
            }:
                return snapshot
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Wait ended; procedure was not cancelled")
            time.sleep(min(interval, remaining))

    def steps(
        self, *, limit: int = 50, cursor: int | None = None
    ) -> ProcedureStepAttemptPage:
        """Read one bounded page of retained step attempts."""
        return self.session.list_procedure_step_attempts(
            self.id, ProcedureStepAttemptListQuery(limit=limit, cursor=cursor)
        )

    def output(self, step_key: str) -> ProcedureStepOutputRef:
        """Read a step's newest output; reject incomplete attempts without execution."""
        cursor: int | None = None
        while True:
            page = self.steps(limit=200, cursor=cursor)
            for attempt in page.items:
                if attempt.step_key == step_key:
                    if attempt.state != "succeeded" or attempt.output is None:
                        raise RuntimeError(
                            f"Step {step_key!r} has no successful output"
                        )
                    return attempt.output
            if page.next_cursor is None:
                raise KeyError(f"Procedure has no step {step_key!r}")
            cursor = page.next_cursor

    def cancel(self, *, actor: str, reason: str) -> ProcedureRun:
        return self.session.cancel_procedure(
            ProcedureCancelCommand(
                procedure_run_id=self.id,
                expected_run_revision=self.snapshot.revision,
                actor=actor,
                reason=reason,
            )
        ).run

    def resume(self) -> LaunchSubmission:
        """Explicitly dispatch ready work through the existing server gate.

        Answer waiting input using submit_procedure_step_input before resuming.
        Attention/unknown outcomes still require their existing reconciliation.
        """
        return self.session.dispatch_project_procedure(self.id)
