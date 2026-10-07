"""Ordinary procedure submissions owned by the application, never the kernel."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel

from scopecat.application.launch import LaunchSubmission
from scopecat.automation.definition import ProcedureDefinition
from scopecat.automation.models import ProcedureRun, ProcedureSource
from scopecat.automation.wire import ProcedureCancelCommand, ProcedureSubmitCommand
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
        )

    def get(self, procedure_id: str) -> AuthorProcedure:
        """Reconnect by durable identity without execution or source loading."""
        self.session.get_procedure(procedure_id)
        return AuthorProcedure(self.session, procedure_id)


@dataclass(frozen=True, slots=True)
class PreparedAuthorProcedure:
    session: DaemonClient
    command: ProcedureSubmitCommand

    def submit(self) -> AuthorProcedure:
        """Retry this exact command after an uncertain response; retain its key.

        The application checks the retained registry and intent before admission,
        then owns dispatch. This object and its command remain pinned even when
        source files change. No procedure code executes in this client.
        """
        result = self.session.submit_author_procedure(self.command)
        return AuthorProcedure(self.session, result.procedure_id, result.dispatch_error)

    def reconnect(self, session: DaemonClient) -> PreparedAuthorProcedure:
        assert self.command.source is not None
        if session.workspace_id != self.command.source.workspace_id:
            raise ValueError("Prepared procedure belongs to another workspace")
        return PreparedAuthorProcedure(session, self.command)


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
