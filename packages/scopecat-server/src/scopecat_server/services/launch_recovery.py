"""Retain raw edits and recover only the exact original admitted procedure."""

from scopecat.kernel.frozen import thaw_json_value

from scopecat_server.errors import BackendConflict
from scopecat_server.launch_recovery import (
    LaunchAttemptPage,
    LaunchAttemptRecord,
    LaunchAttemptResolution,
    LaunchAttemptSave,
    LaunchDraftPage,
    LaunchDraftSave,
    LaunchDraftTarget,
    LaunchDraftView,
)
from scopecat_server.storage.sqlite import launch_recovery as records
from scopecat_server.storage.sqlite.automation import SQLiteAutomationStore
from scopecat_server.storage.sqlite.connection import SQLiteDatabase


class LaunchRecoveryService:
    def __init__(self, database: SQLiteDatabase) -> None:
        self._database = database

    def read(self, target: LaunchDraftTarget) -> LaunchDraftView:
        with self._database.read_transaction() as connection:
            return LaunchDraftView(head=records.head(connection, target))

    def save(self, command: LaunchDraftSave) -> LaunchDraftView:
        with self._database.write_transaction() as connection:
            return records.save(connection, command)

    def history(self, before: int | None = None, limit: int = 50) -> LaunchDraftPage:
        with self._database.read_transaction() as connection:
            return records.history(connection, before, limit)

    def retain(self, command: LaunchAttemptSave) -> LaunchAttemptRecord:
        with self._database.write_transaction() as connection:
            return records.retain(connection, command)

    def attempts(self, before: int | None = None, limit: int = 50) -> LaunchAttemptPage:
        with self._database.read_transaction() as connection:
            return records.attempts(connection, before, limit)

    def resolve(self, sequence: int) -> LaunchAttemptResolution:
        with self._database.read_transaction() as connection:
            attempt = records.attempt(connection, sequence)
            request = attempt.request
            run = SQLiteAutomationStore(
                self._database
            ).find_run_by_request_in_transaction(
                connection, attempt.definition.id, request.request_key
            )
            if run is None:
                return LaunchAttemptResolution(attempt=attempt)
            assert request.reviewed is not None
            source_matches = (
                (
                    run.source is not None
                    and run.source.workspace_id == request.workspace_id
                    and run.source.code_revision == request.code_revision
                )
                if request.code_revision is not None
                else run.source is None
            )
            if not (
                run.definition == attempt.definition
                and run.request_key == request.request_key
                and run.intent.get("request_hash") == request.expected_request_hash
                and thaw_json_value(run.intent.get("config_source"))
                == request.reviewed.config_source.model_dump(mode="json")
                and run.scientific_binding == request.reviewed.binding
                and thaw_json_value(run.intent.get("manual_state"))
                == (
                    request.manual_state.model_dump(mode="json")
                    if request.manual_state
                    else None
                )
                and run.plan_ref == request.plan_ref
                and source_matches
            ):
                raise BackendConflict(
                    "Retained task does not match the original definition, request, "
                    "source and configuration identity"
                )
            return LaunchAttemptResolution(
                attempt=attempt, procedure_id=run.procedure_run_id
            )
