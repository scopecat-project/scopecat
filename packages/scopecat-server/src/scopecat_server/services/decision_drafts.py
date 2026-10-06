"""Recover editing input without submitting, applying, or dispatching work."""

import sqlite3

from scopecat_server.decision_drafts import (
    DecisionDraft,
    DecisionDraftPage,
    DecisionDraftSave,
    DecisionDraftTarget,
    DecisionDraftView,
)
from scopecat_server.errors import BackendNotFound
from scopecat_server.storage.sqlite import decision_drafts as drafts
from scopecat_server.storage.sqlite.automation import (
    AutomationNotFound,
    SQLiteAutomationStore,
)
from scopecat_server.storage.sqlite.connection import SQLiteDatabase


class DecisionDraftService:
    def __init__(self, database: SQLiteDatabase) -> None:
        self._database = database
        self._automation = SQLiteAutomationStore(database)

    def read(self, target: DecisionDraftTarget) -> DecisionDraftView:
        with self._database.read_transaction() as connection:
            return self._view(connection, target, drafts.head(connection, target))

    def save(self, command: DecisionDraftSave) -> DecisionDraftView:
        with self._database.write_transaction() as connection:
            # Require a real retained interpretation identity, even for invalid text.
            try:
                step = self._automation.read_step_attempt_in_transaction(
                    connection,
                    command.target.procedure_run_id,
                    command.target.step_key,
                    command.target.attempt,
                )
            except AutomationNotFound as error:
                raise BackendNotFound(str(error)) from error
            if step.interpretation_request is None:
                raise BackendNotFound("Decision request was not found")
            saved = drafts.append(connection, command)
            return self._view(connection, command.target, saved)

    def history(
        self, *, before: int | None = None, limit: int = 50
    ) -> DecisionDraftPage:
        with self._database.read_transaction() as connection:
            return drafts.history(connection, before=before, limit=limit)

    def _view(
        self,
        connection: sqlite3.Connection,
        target: DecisionDraftTarget,
        draft: DecisionDraft | None,
    ) -> DecisionDraftView:
        current = drafts.head(connection, target)
        validity = "current"
        try:
            run = self._automation.read_run_in_transaction(
                connection, target.procedure_run_id
            )
            step = self._automation.read_step_attempt_in_transaction(
                connection,
                target.procedure_run_id,
                target.step_key,
                target.attempt,
            )
            if run.state != "waiting_for_input" or step.state != "waiting_for_input":
                validity = "no_longer_waiting"
            elif (
                draft is not None
                and draft.state != "discarded"
                and (
                    draft.baseline.run_revision != run.revision
                    or draft.baseline.step_revision != step.revision
                    or draft.baseline.request_hash != step.intent_hash
                )
            ):
                validity = "baseline_changed"
        except AutomationNotFound:
            validity = "no_longer_waiting"
        return DecisionDraftView(
            draft=draft,
            head_revision=0 if current is None else current.revision,
            validity=validity,
        )
