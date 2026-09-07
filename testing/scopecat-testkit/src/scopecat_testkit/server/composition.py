"""SQLite compositions for repository tests."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from pydantic import BaseModel
from scopecat.config.registry.ports import ConfigRegistryUnitOfWorkFactory
from scopecat.daemon.wire import (
    RunDomainJobTransitionBatchCommand,
    RunDomainJobTransitionItem,
)
from scopecat.execution.services import ExecutionSession, RunDomainProposalWriter
from scopecat.project_state import ProjectStateServices
from scopecat.records.config import ConfigProfileSnapshot
from scopecat.records.execution import (
    DomainExecutionId,
    DomainExecutionReceipt,
    DomainJobCheckpoint,
    DomainJobCheckpointTransition,
    DomainJobInvocationTransition,
    DomainJobTerminalTransition,
    DomainJobTransitionRecord,
    RecoveryGroupCompletion,
)
from scopecat.records.measurement_recording import (
    MeasurementDatasetAppend,
    MeasurementDatasetBatch,
    MeasurementDatasetHeader,
    MeasurementDatasetReceipt,
    MeasurementDatasetSeal,
)
from scopecat.records.run import RunConfigSource, RunSnapshot
from scopecat.records.run_request import RunRequest
from scopecat.runs.admission import RunSkeleton, build_run_admission
from scopecat.runs.repository import RunRepository
from scopecat.sdk.domain.invocation import DomainInvocationIntent
from scopecat.sdk.instruments.execution import RunInstrumentHost
from scopecat_server.services.active_measurements import ActiveMeasurementStore
from scopecat_server.storage.sqlite.config_registry import SQLiteConfigRegistryStore
from scopecat_server.storage.sqlite.connection import SQLiteDatabase
from scopecat_server.storage.sqlite.control_plane import SQLiteControlPlane
from scopecat_server.storage.sqlite.execution import (
    SQLiteDomainJobTransitions,
    SQLiteMeasurementDatasetRepository,
    SQLiteRecoveryGroups,
    SQLiteRunCoverage,
)
from scopecat_server.storage.sqlite.project_store import SQLiteProjectStore
from scopecat_server.storage.sqlite.run_repository import (
    SQLiteRunRepository,
    _PreparedRef,
)

from scopecat_testkit.instrument_host import TestRunInstrumentHost


class SQLiteTestRunRepository(SQLiteRunRepository):
    """Fixture-only low-level writes excluded from the production port."""

    def write_snapshot(self, snapshot: RunSnapshot) -> None:
        with self._transaction() as connection:
            self._replace_run_snapshot(connection, snapshot)

    def write_run_skeleton(self, skeleton: RunSkeleton) -> None:
        _persist_run_skeleton(self, skeleton)

    def list_runs(self) -> list[RunSnapshot]:
        return list_test_runs(self)

    def write_model(self, run_id: str, ref: str, model: BaseModel) -> None:
        self._write_fixture_ref(
            run_id,
            self._prepare_model(run_id, ref, model),
        )

    def write_text(self, run_id: str, ref: str, content: str) -> None:
        if content and not content.endswith("\n"):
            content = f"{content}\n"
        self.write_bytes(run_id, ref, content.encode())

    def write_bytes(self, run_id: str, ref: str, content: bytes) -> None:
        self._write_fixture_ref(
            run_id,
            self._prepare_bytes(run_id, ref, content),
        )

    def _write_fixture_ref(self, run_id: str, prepared: _PreparedRef) -> None:
        with self._transaction() as connection:
            self._publish_refs(
                connection,
                run_id,
                (prepared,),
            )


class SQLiteTestMeasurementDatasetRepository(SQLiteMeasurementDatasetRepository):
    """Own transactions for in-process execution tests."""

    def __init__(self, runs: SQLiteRunRepository, *, run_id: str) -> None:
        super().__init__(runs, run_id=run_id)
        self._active = ActiveMeasurementStore()
        self._segment_id = f"in-process-segment:{run_id}"
        with SQLiteControlPlane(runs.sqlite).write_transaction() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO scheduler_runs(
                    submission_id, run_id, state, updated_at, admission_json
                )
                VALUES (?, ?, 'leased', ?, '{}')
                """,
                (f"in-process:{run_id}", run_id, datetime.now(UTC).isoformat()),
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO run_execution_segments(
                    segment_id, run_id, ordinal, executor_id,
                    run_contract_fingerprint, started_at, start_point_count
                )
                VALUES (?, ?, 0, 'in-process', ?, ?, 0)
                """,
                (
                    self._segment_id,
                    run_id,
                    "0" * 64,
                    datetime.now(UTC).isoformat(),
                ),
            )

    def initialize(
        self,
        header: MeasurementDatasetHeader,
    ) -> MeasurementDatasetReceipt:
        prepared = self.prepare_header(header)
        with SQLiteControlPlane(self._runs.sqlite).write_transaction() as connection:
            receipt, _header_created, _fragment_created = (
                self.header_prepared_in_transaction(
                    connection,
                    prepared,
                    segment_id=self._segment_id,
                )
            )
            self._active.initialize(
                header,
                segment_id=self._segment_id,
                acquisition_start=0,
            )
            return receipt

    def ingest(
        self,
        batch: MeasurementDatasetBatch,
    ) -> tuple[MeasurementDatasetReceipt, ...]:
        self._active.ingest(
            MeasurementDatasetAppend(
                run_id=batch.run_id,
                header_content_hash=batch.header_content_hash,
                acquisition_start=self._active.preview(
                    self._run_id
                ).received_record_count,
                records=batch.records,
            )
        )
        return self._flush(force=False)

    def flush(self) -> tuple[MeasurementDatasetReceipt, ...]:
        return self._flush(force=True)

    def append(self, append: MeasurementDatasetAppend) -> MeasurementDatasetReceipt:
        prepared = self.prepare_append(append)
        with SQLiteControlPlane(self._runs.sqlite).write_transaction() as connection:
            receipt, _created = self.append_prepared_in_transaction(
                connection,
                prepared,
                segment_id=self._segment_id,
            )
            return receipt

    def seal(self, seal: MeasurementDatasetSeal) -> MeasurementDatasetReceipt:
        prepared = self.prepare_seal(seal)
        with SQLiteControlPlane(self._runs.sqlite).write_transaction() as connection:
            receipt, _created = self.seal_prepared_in_transaction(
                connection,
                prepared,
                segment_id=self._segment_id,
            )
            return receipt

    def _flush(self, *, force: bool) -> tuple[MeasurementDatasetReceipt, ...]:
        receipts: list[MeasurementDatasetReceipt] = []
        while records := self._active.next_chunk(self._run_id, force=force):
            append = MeasurementDatasetAppend(
                run_id=self._run_id,
                header_content_hash=self._active.header_content_hash(self._run_id),
                acquisition_start=self._active.durable_record_count(self._run_id),
                records=records,
            )
            receipts.append(self.append(append))
            self._active.commit_chunk(self._run_id, records)
        return tuple(receipts)


class SQLiteTestDomainJobTransitionWriter:
    """Persist in-process domain evidence through the production ledger."""

    def __init__(self, runs: SQLiteRunRepository, *, run_id: str) -> None:
        self._runs = runs
        self._run_id = run_id
        self._ledger = SQLiteDomainJobTransitions(runs, run_id=run_id)
        self._pending: list[RunDomainJobTransitionItem] = []
        with SQLiteControlPlane(runs.sqlite).write_transaction() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO scheduler_runs(
                    submission_id, run_id, state, updated_at, admission_json
                )
                VALUES (?, ?, 'leased', ?, '{}')
                """,
                (f"in-process:{run_id}", run_id, datetime.now(UTC).isoformat()),
            )

    def invocation(
        self,
        *,
        logical_compute_node_id: str,
        point_ordinals: tuple[int, ...],
        execution_id: DomainExecutionId,
        intent: DomainInvocationIntent,
        write_ahead: bool,
    ) -> None:
        self._stage(
            logical_compute_node_id=logical_compute_node_id,
            point_ordinals=point_ordinals,
            transition=DomainJobInvocationTransition(
                execution_id=execution_id,
                intent=intent,
            ),
            write_ahead=write_ahead,
        )

    def checkpoint(
        self,
        *,
        logical_compute_node_id: str,
        point_ordinals: tuple[int, ...],
        checkpoint: DomainJobCheckpoint,
    ) -> None:
        self._stage(
            logical_compute_node_id=logical_compute_node_id,
            point_ordinals=point_ordinals,
            transition=DomainJobCheckpointTransition(checkpoint=checkpoint),
            write_ahead=True,
        )

    def terminal(
        self,
        *,
        logical_compute_node_id: str,
        point_ordinals: tuple[int, ...],
        receipt: DomainExecutionReceipt,
        write_ahead: bool,
    ) -> None:
        self._stage(
            logical_compute_node_id=logical_compute_node_id,
            point_ordinals=point_ordinals,
            transition=DomainJobTerminalTransition(receipt=receipt),
            write_ahead=write_ahead,
        )

    def flush(self) -> None:
        self._send_pending()

    def _stage(
        self,
        *,
        logical_compute_node_id: str,
        point_ordinals: tuple[int, ...],
        transition: DomainJobTransitionRecord,
        write_ahead: bool,
    ) -> None:
        self._pending.append(
            RunDomainJobTransitionItem(
                logical_compute_node_id=logical_compute_node_id,
                point_ordinals=point_ordinals,
                transition=transition,
            )
        )
        if write_ahead or len(self._pending) >= 64:
            self._send_pending()

    def _send_pending(self) -> None:
        items = tuple(self._pending)
        if not items:
            return
        with SQLiteControlPlane(self._runs.sqlite).write_transaction() as connection:
            self._ledger.commit_batch_in_transaction(
                connection,
                RunDomainJobTransitionBatchCommand(
                    lease_id="in-process",
                    items=items,
                ),
            )
        self._pending.clear()


class SQLiteTestRunCoverage:
    """Stage logical coverage until the execution flush boundary."""

    def __init__(self, runs: SQLiteRunRepository, *, run_id: str) -> None:
        self._runs = runs
        self._coverage = SQLiteRunCoverage(runs, run_id=run_id)
        self._groups = SQLiteTestRecoveryGroups(runs, run_id=run_id)
        self._pending_groups: list[RecoveryGroupCompletion] = []
        self._pending: list[tuple[int, int]] = []

    def advance(
        self,
        *,
        start_index: int,
        point_count: int,
        groups: tuple[RecoveryGroupCompletion, ...] = (),
    ) -> None:
        self._pending.append((start_index, point_count))
        self._pending_groups.extend(groups)

    def flush(self) -> None:
        if not self._pending:
            return
        if self._pending_groups:
            self._groups.commit(tuple(self._pending_groups))
            self._pending_groups.clear()
        with SQLiteControlPlane(self._runs.sqlite).write_transaction() as connection:
            for start_index, point_count in self._pending:
                self._coverage.advance_in_transaction(
                    connection,
                    start_index=start_index,
                    point_count=point_count,
                )
        self._pending.clear()

    def completed_count(self) -> int:
        return self._coverage.read()


class SQLiteTestRecoveryGroups:
    """Commit exact group proofs through the in-process execution segment."""

    def __init__(self, runs: SQLiteRunRepository, *, run_id: str) -> None:
        self._runs = runs
        self._run_id = run_id
        self._segment_id = f"in-process-segment:{run_id}"
        self._groups = SQLiteRecoveryGroups(runs, run_id=run_id)

    def commit(self, groups: tuple[RecoveryGroupCompletion, ...]) -> None:
        with SQLiteControlPlane(self._runs.sqlite).write_transaction() as connection:
            self._groups.append_in_transaction(
                connection,
                groups,
                segment_id=self._segment_id,
            )

    def completed(self) -> tuple[RecoveryGroupCompletion, ...]:
        return tuple(
            item.completion for item in self._groups.read(limit=1_000_000).items
        )


@dataclass(frozen=True, slots=True)
class SQLiteExecutionSession(ExecutionSession):
    """Concrete SQLite session exposing read models to test assertions."""

    measurements: SQLiteTestMeasurementDatasetRepository


def admit_test_run(
    *,
    config: ConfigProfileSnapshot,
    request: RunRequest,
    repository: RunRepository,
    config_source: RunConfigSource | None = None,
) -> RunSnapshot:
    """Persist one accepted run for an in-process test."""

    skeleton = build_run_admission(
        config=config,
        request=request,
        config_source=config_source,
    )
    _persist_run_skeleton(repository, skeleton)
    return skeleton.snapshot


def _persist_run_skeleton(
    repository: RunRepository,
    skeleton: RunSkeleton,
) -> None:
    """Persist admission state without adding a test-only repository method."""

    runs = cast("SQLiteRunRepository", repository)
    prepared = runs.prepare_run_skeleton(skeleton)
    with SQLiteControlPlane(runs.sqlite).write_transaction() as connection:
        runs.commit_run_skeleton_in_transaction(connection, prepared)


def list_test_runs(repository: RunRepository) -> list[RunSnapshot]:
    """Inspect SQLite run snapshots for tests that do not own scheduler state."""

    runs = cast("SQLiteRunRepository", repository)
    with sqlite3.connect(runs.database) as connection:
        rows = cast(
            "list[tuple[str]]",
            connection.execute(
                """
                SELECT run_id FROM runs
                """,
            ).fetchall(),
        )
    snapshots = [runs.read_snapshot(row[0]) for row in rows]
    return sorted(
        snapshots, key=lambda snapshot: (snapshot.created_at, snapshot.run_id)
    )


def sqlite_run_repository(project: str | Path) -> SQLiteTestRunRepository:
    """Open an isolated SQLite run repository."""

    database, objects = _sqlite_paths(project)
    sqlite = SQLiteDatabase(database)
    SQLiteProjectStore(sqlite, objects).bootstrap()
    repository = SQLiteTestRunRepository(sqlite, objects)
    return repository


def sqlite_config_registry_unit_of_work(
    project: str | Path,
) -> ConfigRegistryUnitOfWorkFactory:
    """Open isolated configuration registry transactions."""

    runs = sqlite_run_repository(project)
    return _config_registry_store(project, runs=runs).write_unit_of_work


def sqlite_execution_session(
    project: str | Path,
    run_id: str,
    *,
    runs: SQLiteRunRepository | None = None,
    instruments: RunInstrumentHost | None = None,
    domain_proposals: RunDomainProposalWriter | None = None,
) -> SQLiteExecutionSession:
    """Bind one run's execution ports to isolated SQLite persistence."""

    selected_runs = sqlite_run_repository(project) if runs is None else runs
    coverage = SQLiteTestRunCoverage(selected_runs, run_id=run_id)
    recovery_groups = SQLiteTestRecoveryGroups(selected_runs, run_id=run_id)
    return SQLiteExecutionSession(
        accepted=selected_runs.read_snapshot(run_id),
        begin=lambda: None,
        commit_terminal=selected_runs.commit_terminal,
        measurements=SQLiteTestMeasurementDatasetRepository(
            selected_runs,
            run_id=run_id,
        ),
        coverage=coverage,
        recovery_groups=recovery_groups,
        instruments=instruments or TestRunInstrumentHost(),
        domain_job_transitions=SQLiteTestDomainJobTransitionWriter(
            selected_runs,
            run_id=run_id,
        ),
        domain_proposals=domain_proposals,
        durable_completed_point_count=coverage.completed_count,
        durable_recovery_groups=recovery_groups.completed,
    )


def sqlite_project_services(project: str | Path) -> ProjectStateServices:
    """Bind application ports to isolated SQLite adapters."""

    runs = sqlite_run_repository(project)
    config_registry = _config_registry_store(project, runs=runs)
    return ProjectStateServices(
        runs=runs,
        config_registry=config_registry.write_unit_of_work,
    )


def _sqlite_paths(project: str | Path) -> tuple[Path, Path]:
    state = Path(project) / ".scopecat-test"
    return state / "control.sqlite3", state / "objects"


def _config_registry_store(
    project: str | Path,
    *,
    runs: SQLiteRunRepository,
) -> SQLiteConfigRegistryStore:
    return SQLiteConfigRegistryStore(runs.sqlite, runs=runs)
