"""Application service for atomic calibration-cohort admission."""

from __future__ import annotations

from collections.abc import Callable, Generator
from contextlib import contextmanager
from datetime import UTC, datetime

from scopecat.automation.calibration_wire import (
    CalibrationCohortCreateCommand,
    CalibrationCohortCreateReceipt,
    CalibrationCohortGetQuery,
    CalibrationCohortGetReceipt,
    CalibrationCohortListQuery,
    CalibrationCohortMemberListQuery,
    CalibrationCohortMemberPage,
    CalibrationCohortPage,
    CalibrationPublicationAttentionCommand,
    CalibrationPublicationAttentionReceipt,
    CalibrationPublicationDeferCommand,
    CalibrationPublicationDeferReceipt,
    CalibrationPublicationGetQuery,
    CalibrationPublicationGetReceipt,
    CalibrationPublicationReadyPage,
    CalibrationPublicationReadyQuery,
    CalibrationPublicationRetryCommand,
    CalibrationPublicationRetryReceipt,
    CalibrationStatusQuery,
    CalibrationStatusReceipt,
)
from scopecat.automation.calibrations import (
    CalibrationCohort,
    CalibrationCohortMember,
    calibration_cohort_member_request_key,
)
from scopecat.config.registry.records import ContextConfigRegistrySource
from scopecat.kernel.errors import DataIntegrityError, NotFound
from scopecat.records.calibration_scope import WorkingPointCalibrationScope
from scopecat.records.configuration_fence import SetupContentFence
from scopecat.records.scientific_scope import setup_content_hash

from scopecat_server.storage.sqlite.automation import (
    AutomationConflict,
    AutomationNotFound,
)
from scopecat_server.storage.sqlite.calibration_cohorts import (
    CalibrationCohortConflict,
    CalibrationCohortNotFound,
    SQLiteCalibrationCohortStore,
)
from scopecat_server.storage.sqlite.config_registry import SQLiteConfigRegistryStore

from ..errors import BackendConflict, BackendNotFound
from .automation import AutomationService


class CalibrationCohortService:
    """Own consistent status reads and all-or-nothing cohort admission."""

    def __init__(
        self,
        store: SQLiteCalibrationCohortStore,
        automation: AutomationService,
        config_registry: SQLiteConfigRegistryStore,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._automation = automation
        self._config_registry = config_registry
        self._clock = clock or _utc_now

    def status(self, query: CalibrationStatusQuery) -> CalibrationStatusReceipt:
        with (
            _translate_store_errors(),
            self._store.read_transaction() as connection,
        ):
            snapshot = self._store.status_snapshot_in_transaction(
                connection,
                query.calibration_keys,
                fanout_scope=query.fanout_scope,
                clock=self._now,
            )
        return CalibrationStatusReceipt(snapshot=snapshot)

    def create(
        self,
        command: CalibrationCohortCreateCommand,
    ) -> CalibrationCohortCreateReceipt:
        with (
            _translate_store_errors(),
            self._store.write_transaction() as connection,
        ):
            try:
                existing = self._store.read_in_transaction(
                    connection,
                    command.cohort_id,
                )
            except CalibrationCohortNotFound:
                existing = None
            if existing is not None:
                if (
                    existing.spec_hash != command.spec_hash
                    or existing.spec != command.spec
                ):
                    raise CalibrationCohortConflict(
                        "calibration cohort id already has a different specification"
                    )
                return CalibrationCohortCreateReceipt(
                    cohort=existing,
                    members=self._store.list_members_in_transaction(
                        connection,
                        existing.cohort_id,
                    ),
                )

            source = command.spec.config_source
            with self._config_registry.borrowed_unit_of_work(connection) as work:
                entry = work.registry.read_entry(source.entry_id)
                config = work.registry.read_config(entry.config_ref)
                activation = work.registry.read_latest_activation()
                if (
                    activation is None
                    or entry.config_ref != source.config_ref
                    or entry.content_hash != source.content_hash
                ):
                    raise CalibrationCohortConflict(
                        "calibration cohort config source changed"
                    )
                active_entry = work.registry.read_entry(activation.entry_id)
                authority = work.registry.read_config(active_entry.config_ref)
                if setup_content_hash(config) != setup_content_hash(authority):
                    raise CalibrationCohortConflict(
                        "calibration cohort executable setup changed"
                    )
                scope = source.scope
                if isinstance(scope, WorkingPointCalibrationScope) and (
                    not isinstance(entry.source, ContextConfigRegistrySource)
                    or entry.source.context.sample != scope.sample
                    or (entry.source.context.workspace_id) != scope.workspace_id
                    or work.registry.context_head(scope.workspace_id) != entry.id
                ):
                    raise CalibrationCohortConflict("calibration working point changed")

            now = self._now()
            observed = self._store.status_snapshot_in_transaction(
                connection,
                tuple(
                    observation.calibration_key
                    for observation in command.spec.observations
                ),
                fanout_scope=command.spec.fanout_scope,
                clock=lambda: now,
            )
            if observed.statuses != command.spec.observations:
                raise CalibrationCohortConflict(
                    "calibration cohort status observations changed"
                )
            observations_by_key = {
                observation.calibration_key: observation
                for observation in observed.statuses
            }
            if any(
                (attempt := observations_by_key[member.calibration_key].latest_attempt)
                is not None
                and attempt.procedure_state != "closed"
                for member in command.spec.members
            ):
                raise CalibrationCohortConflict(
                    "calibration cohort member already has an active attempt"
                )
            if (
                observed.fanout_active_count
                != command.spec.observed_fanout_active_count
            ):
                raise CalibrationCohortConflict(
                    "calibration cohort fanout activity changed"
                )
            if (
                observed.fanout_active_count + len(command.spec.members)
                > command.spec.max_in_flight
            ):
                raise CalibrationCohortConflict(
                    "calibration cohort exceeds current fanout capacity"
                )

            cohort = CalibrationCohort(
                cohort_id=command.cohort_id,
                spec=command.spec,
                spec_hash=command.spec_hash,
                created_at=now,
            )
            self._store.insert_cohort_in_transaction(connection, cohort)
            members: list[CalibrationCohortMember] = []
            for index, member_spec in enumerate(command.spec.members):
                request_key = calibration_cohort_member_request_key(
                    command.cohort_id,
                    member_spec,
                )
                run = self._automation.submit_in_transaction(
                    connection,
                    definition=member_spec.procedure,
                    request_key=request_key,
                    intent=member_spec.intent,
                    samples=scope.sample_selectors(),
                    expected_configuration=SetupContentFence(
                        content_hash=setup_content_hash(config)
                    ),
                    at=now,
                    require_new=True,
                )
                member = CalibrationCohortMember(
                    cohort_id=command.cohort_id,
                    index=index,
                    spec=member_spec,
                    procedure_run_id=run.procedure_run_id,
                    request_key=request_key,
                    admitted_at=now,
                )
                self._store.insert_member_in_transaction(connection, member)
                members.append(member)
            return CalibrationCohortCreateReceipt(
                cohort=cohort,
                members=tuple(members),
            )

    def get(self, query: CalibrationCohortGetQuery) -> CalibrationCohortGetReceipt:
        with _translate_store_errors():
            cohort = self._store.read(query.cohort_id)
        return CalibrationCohortGetReceipt(cohort=cohort)

    def list(self, query: CalibrationCohortListQuery) -> CalibrationCohortPage:
        with _translate_store_errors():
            page = self._store.list(
                limit=query.limit,
                before=query.cursor,
                fanout_scope=query.fanout_scope,
            )
        return CalibrationCohortPage(
            items=page.items,
            next_cursor=page.next_cursor,
        )

    def list_members(
        self,
        query: CalibrationCohortMemberListQuery,
    ) -> CalibrationCohortMemberPage:
        with _translate_store_errors():
            page = self._store.list_members(
                query.cohort_id,
                limit=query.limit,
                after=query.cursor,
            )
        return CalibrationCohortMemberPage(
            cohort_id=query.cohort_id,
            items=page.items,
            next_cursor=page.next_cursor,
        )

    def ready_publications(
        self,
        query: CalibrationPublicationReadyQuery,
    ) -> CalibrationPublicationReadyPage:
        with _translate_store_errors():
            page = self._store.list_ready_publications(
                query.capabilities,
                at=self._now(),
                limit=query.limit,
                after=query.cursor,
                through_sequence=query.through_sequence,
            )
        return CalibrationPublicationReadyPage(
            items=page.items,
            next_cursor=page.next_cursor,
            through_sequence=page.through_sequence,
        )

    def get_publication(
        self,
        query: CalibrationPublicationGetQuery,
    ) -> CalibrationPublicationGetReceipt:
        with _translate_store_errors():
            finalization = self._store.read_finalization(query.cohort_id)
        return CalibrationPublicationGetReceipt(finalization=finalization)

    def require_publication_attention(
        self,
        command: CalibrationPublicationAttentionCommand,
    ) -> CalibrationPublicationAttentionReceipt:
        with (
            _translate_store_errors(),
            self._store.write_transaction() as connection,
        ):
            finalization = self._store.require_publication_attention_in_transaction(
                connection,
                cohort_id=command.cohort_id,
                policy=command.policy,
                expected_revision=command.expected_finalization_revision,
                actor=command.actor,
                reason=command.reason,
                at=self._now(),
            )
        return CalibrationPublicationAttentionReceipt(finalization=finalization)

    def retry_publication(
        self,
        command: CalibrationPublicationRetryCommand,
    ) -> CalibrationPublicationRetryReceipt:
        with (
            _translate_store_errors(),
            self._store.write_transaction() as connection,
        ):
            finalization = self._store.retry_publication_in_transaction(
                connection,
                cohort_id=command.cohort_id,
                policy=command.policy,
                expected_revision=command.expected_finalization_revision,
                at=self._now(),
            )
        return CalibrationPublicationRetryReceipt(finalization=finalization)

    def defer_publication(
        self,
        command: CalibrationPublicationDeferCommand,
    ) -> CalibrationPublicationDeferReceipt:
        with (
            _translate_store_errors(),
            self._store.write_transaction() as connection,
        ):
            finalization = self._store.defer_publication_in_transaction(
                connection,
                cohort_id=command.cohort_id,
                policy=command.policy,
                expected_revision=command.expected_finalization_revision,
                retry_after_seconds=command.retry_after_seconds,
                at=self._now(),
            )
        return CalibrationPublicationDeferReceipt(finalization=finalization)

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(
                "calibration cohort clock must return a timezone-aware datetime"
            )
        return value.astimezone(UTC)


@contextmanager
def _translate_store_errors() -> Generator[None]:
    try:
        yield
    except (CalibrationCohortNotFound, AutomationNotFound) as error:
        raise BackendNotFound(str(error)) from error
    except (
        CalibrationCohortConflict,
        AutomationConflict,
        DataIntegrityError,
        NotFound,
    ) as error:
        raise BackendConflict(str(error)) from error


def _utc_now() -> datetime:
    return datetime.now(UTC)


__all__ = ["CalibrationCohortService"]
