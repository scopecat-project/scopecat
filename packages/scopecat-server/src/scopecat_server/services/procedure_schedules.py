"""Application service for durable one-shot procedure schedules."""

from __future__ import annotations

from collections.abc import Callable, Generator
from contextlib import contextmanager
from datetime import UTC, datetime

from scopecat.automation import (
    ProcedureSchedule,
    ProcedureScheduleCancelCommand,
    ProcedureScheduleCancellation,
    ProcedureScheduleCancelReceipt,
    ProcedureScheduleCreateCommand,
    ProcedureScheduleCreateReceipt,
    ProcedureScheduleDuePage,
    ProcedureScheduleDueQuery,
    ProcedureScheduleListQuery,
    ProcedureScheduleMaterialization,
    ProcedureScheduleMaterializeCommand,
    ProcedureScheduleMaterializeReceipt,
    ProcedureSchedulePage,
    procedure_schedule_request_key,
)

from scopecat_server.storage.sqlite.automation import (
    AutomationConflict,
    AutomationNotFound,
)
from scopecat_server.storage.sqlite.procedure_schedules import (
    ProcedureScheduleConflict,
    ProcedureScheduleNotFound,
    SQLiteProcedureScheduleStore,
)

from ..errors import BackendConflict, BackendNotFound
from .automation import AutomationService


class ProcedureScheduleService:
    """Own one-shot schedule creation, cancellation, and run materialization."""

    def __init__(
        self,
        store: SQLiteProcedureScheduleStore,
        automation: AutomationService,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._automation = automation
        self._clock = clock or _utc_now

    def create(
        self,
        command: ProcedureScheduleCreateCommand,
    ) -> ProcedureScheduleCreateReceipt:
        with (
            _translate_store_errors(),
            self._store.write_transaction() as connection,
        ):
            try:
                existing = self._store.read_in_transaction(
                    connection,
                    command.schedule_id,
                )
            except ProcedureScheduleNotFound:
                existing = None
            if existing is not None:
                if (
                    existing.definition != command.definition
                    or existing.intent != command.intent
                    or existing.intent_hash != command.intent_hash
                    or existing.due_at != command.due_at
                ):
                    raise ProcedureScheduleConflict(
                        "procedure schedule id already has a different specification"
                    )
                return ProcedureScheduleCreateReceipt(schedule=existing)
            now = self._now()
            schedule = ProcedureSchedule(
                schedule_id=command.schedule_id,
                definition=command.definition,
                intent=dict(command.intent),
                intent_hash=command.intent_hash,
                source=command.source,
                due_at=command.due_at,
                revision=1,
                state="pending",
                created_at=now,
                updated_at=now,
            )
            self._store.insert_in_transaction(connection, schedule)
            return ProcedureScheduleCreateReceipt(schedule=schedule)

    def get(self, schedule_id: str) -> ProcedureSchedule:
        with _translate_store_errors():
            return self._store.read(schedule_id)

    def list(self, query: ProcedureScheduleListQuery) -> ProcedureSchedulePage:
        with _translate_store_errors():
            page = self._store.list(
                limit=query.limit,
                before=query.cursor,
                state=query.state,
            )
        return ProcedureSchedulePage(
            items=page.items,
            next_cursor=page.next_cursor,
        )

    def due(self, query: ProcedureScheduleDueQuery) -> ProcedureScheduleDuePage:
        with _translate_store_errors():
            page = self._store.due(
                at=self._now(),
                limit=query.limit,
                after=query.cursor,
                through_sequence=query.through_sequence,
            )
        return ProcedureScheduleDuePage(
            items=page.items,
            next_cursor=page.next_cursor,
            through_sequence=page.through_sequence,
        )

    def cancel(
        self,
        command: ProcedureScheduleCancelCommand,
    ) -> ProcedureScheduleCancelReceipt:
        with (
            _translate_store_errors(),
            self._store.write_transaction() as connection,
        ):
            schedule = self._store.read_in_transaction(
                connection,
                command.schedule_id,
            )
            if schedule.state == "cancelled":
                cancellation = schedule.cancellation
                if (
                    schedule.revision == command.expected_schedule_revision + 1
                    and cancellation is not None
                    and cancellation.actor == command.actor
                    and cancellation.reason == command.reason
                ):
                    return ProcedureScheduleCancelReceipt(schedule=schedule)
                if (
                    cancellation is not None
                    and cancellation.actor == command.actor
                    and cancellation.reason == command.reason
                ):
                    raise ProcedureScheduleConflict(
                        "procedure schedule revision changed"
                    )
                raise ProcedureScheduleConflict(
                    "procedure schedule is already cancelled differently"
                )
            if schedule.state == "materialized":
                raise ProcedureScheduleConflict(
                    "materialized procedure schedule cannot be cancelled"
                )
            self._require_revision(schedule, command.expected_schedule_revision)
            now = self._now()
            updated = ProcedureSchedule.model_validate(
                {
                    **schedule.model_dump(),
                    "revision": schedule.revision + 1,
                    "state": "cancelled",
                    "updated_at": now,
                    "cancellation": ProcedureScheduleCancellation(
                        actor=command.actor,
                        reason=command.reason,
                        cancelled_at=now,
                    ),
                }
            )
            self._store.replace_in_transaction(
                connection,
                updated,
                expected_revision=schedule.revision,
            )
            return ProcedureScheduleCancelReceipt(schedule=updated)

    def materialize(
        self,
        command: ProcedureScheduleMaterializeCommand,
    ) -> ProcedureScheduleMaterializeReceipt:
        with (
            _translate_store_errors(),
            self._store.write_transaction() as connection,
        ):
            schedule = self._store.read_in_transaction(
                connection,
                command.schedule_id,
            )
            if schedule.state == "materialized":
                if schedule.revision == command.expected_schedule_revision + 1:
                    return ProcedureScheduleMaterializeReceipt(schedule=schedule)
                raise ProcedureScheduleConflict("procedure schedule revision changed")
            if schedule.state == "cancelled":
                raise ProcedureScheduleConflict(
                    "cancelled procedure schedule cannot be materialized"
                )
            self._require_revision(schedule, command.expected_schedule_revision)
            now = self._now()
            if schedule.due_at > now:
                raise ProcedureScheduleConflict("procedure schedule is not due")
            request_key = procedure_schedule_request_key(
                schedule.schedule_id,
                schedule.due_at,
                schedule.definition,
                schedule.intent_hash,
            )
            run = self._automation.submit_in_transaction(
                connection,
                definition=schedule.definition,
                request_key=request_key,
                intent=schedule.intent,
                source=schedule.source,
                at=now,
                require_new=True,
            )
            updated = ProcedureSchedule.model_validate(
                {
                    **schedule.model_dump(),
                    "revision": schedule.revision + 1,
                    "state": "materialized",
                    "updated_at": now,
                    "materialization": ProcedureScheduleMaterialization(
                        procedure_run_id=run.procedure_run_id,
                        request_key=request_key,
                        materialized_at=now,
                    ),
                }
            )
            self._store.replace_in_transaction(
                connection,
                updated,
                expected_revision=schedule.revision,
            )
            return ProcedureScheduleMaterializeReceipt(schedule=updated)

    @staticmethod
    def _require_revision(
        schedule: ProcedureSchedule,
        expected_revision: int,
    ) -> None:
        if schedule.revision != expected_revision:
            raise ProcedureScheduleConflict("procedure schedule revision changed")

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(
                "procedure schedule clock must return a timezone-aware datetime"
            )
        return value.astimezone(UTC)


@contextmanager
def _translate_store_errors() -> Generator[None]:
    try:
        yield
    except (ProcedureScheduleNotFound, AutomationNotFound) as error:
        raise BackendNotFound(str(error)) from error
    except (ProcedureScheduleConflict, AutomationConflict) as error:
        raise BackendConflict(str(error)) from error


def _utc_now() -> datetime:
    return datetime.now(UTC)


__all__ = ["ProcedureScheduleService"]
