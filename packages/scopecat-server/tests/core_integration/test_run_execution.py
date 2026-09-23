from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest
from scopecat.adaptive_domains import (
    DomainProposalAttempt,
    OperatorDomainRequest,
    ResolvedDomainFragment,
)
from scopecat.execution.services import QueuedOperatorDomainRequest
from scopecat.kernel.points import AcceptedRunPoint
from scopecat.kernel.quantity import Quantity
from scopecat.optimization import (
    DomainOptimizerContext,
    DomainProposalDecision,
    OptimizationComplete,
)
from scopecat.records.execution import InstrumentStateEvidence
from scopecat.records.instrument import InstrumentStateSnapshot
from scopecat.records.measurement import MeasurementScalar
from scopecat.runs.parameter_evidence import (
    HOST_PARAMETER_EVIDENCE_KIND,
    read_host_parameter_evidence,
)
from scopecat.runs.service import read_run_measurement_dataset, read_run_record_json
from scopecat.sdk.instruments import (
    InstrumentConnectionContext,
    InstrumentDriver,
    InstrumentProviderContext,
    InstrumentProviderDescription,
)
from scopecat_testkit.instrument_host import compose_test_instruments
from scopecat_testkit.server.composition import sqlite_run_repository
from scopecat_testkit.server.execution import execute_invocation_run
from scopecat_testkit.server.runtime import sqlite_project_services
from scopecat_testkit.signal_instruments import TestSignalInstrumentProvider
from scopecat_testkit.workflow_fixtures import (
    config_with_instrument_id,
    load_config,
    load_invocation,
)

from scopecat_server.storage.sqlite.execution import SQLiteMeasurementDatasetRepository


class _CountingProvider:
    def __init__(self) -> None:
        self.delegate = TestSignalInstrumentProvider()
        self.describe_calls = 0
        self.connect_calls = 0

    @property
    def provider_id(self) -> str:
        return self.delegate.provider_id

    def describe(
        self,
        context: InstrumentProviderContext,
    ) -> InstrumentProviderDescription:
        self.describe_calls += 1
        return self.delegate.describe(context)

    def connect(
        self,
        context: InstrumentConnectionContext,
    ) -> InstrumentDriver:
        self.connect_calls += 1
        return self.delegate.connect(context)


class _TwoDomainOptimizer:
    id = "tests.two-domain-optimizer"

    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root
        self.contexts: list[DomainOptimizerContext] = []
        self.durable_point_counts: list[int] = []

    def propose(
        self,
        context: DomainOptimizerContext,
    ) -> DomainProposalAttempt | OptimizationComplete:
        self.contexts.append(context)
        assert context.region is not None
        run_id = context.observations[0].measurements[0].run_id
        self.durable_point_counts.append(
            SQLiteMeasurementDatasetRepository(
                sqlite_run_repository(self.project_root),
                run_id=run_id,
            ).acquisition_record_count()
        )
        if context.region.completed_point_count >= 5:
            return OptimizationComplete("two adaptive domains completed")
        if not context.ledger.entries:
            return DomainProposalAttempt(
                ResolvedDomainFragment.points(
                    ({"drive_frequency": Quantity(5.15, "GHz")},)
                ),
                region_ids=(context.region.id,),
                based_on_region_revisions={
                    context.region.id: context.region.revision - 1
                },
            )
        return DomainProposalAttempt(
            ResolvedDomainFragment.points(
                (
                    {
                        "drive_frequency": Quantity(
                            5.2 + 0.1 * (context.region.completed_point_count - 3),
                            "GHz",
                        )
                    },
                )
            ),
            region_ids=(context.region.id,),
            based_on_region_revisions={context.region.id: context.region.revision},
        )


class _StopOptimizer:
    id = "tests.stop-optimizer"

    def propose(
        self,
        context: DomainOptimizerContext,
    ) -> OptimizationComplete:
        del context
        return OptimizationComplete("operator domain completed")


class _AlwaysStaleOptimizer:
    id = "tests.always-stale-optimizer"

    def propose(self, context: DomainOptimizerContext) -> DomainProposalAttempt:
        assert context.region is not None
        return DomainProposalAttempt(
            ResolvedDomainFragment.points(
                ({"drive_frequency": Quantity(5.16, "GHz")},)
            ),
            region_ids=(context.region.id,),
            based_on_region_revisions={context.region.id: context.region.revision - 1},
        )


@dataclass
class _OperatorQueuePort:
    queued: QueuedOperatorDomainRequest | None
    decisions: list[DomainProposalDecision]
    closed_reason: str | None = None
    empty_polls_before_ready: int = 0
    expected_completed_point_count: int = 4

    def next_queued(self) -> QueuedOperatorDomainRequest | None:
        if self.empty_polls_before_ready > 0:
            self.empty_polls_before_ready -= 1
            return None
        queued = self.queued
        self.queued = None
        return queued

    def append(
        self,
        proposal: DomainProposalAttempt,
        decision: DomainProposalDecision,
        accepted_points: tuple[AcceptedRunPoint, ...],
        *,
        operator_request_id: str | None = None,
    ) -> None:
        del proposal, accepted_points
        if decision.proposal.source == "operator":
            assert operator_request_id == "operator-queue-1"
        else:
            assert operator_request_id is None
        self.decisions.append(decision)

    def close(self, *, completed_point_count: int, reason: str) -> None:
        assert completed_point_count == self.expected_completed_point_count
        self.closed_reason = reason


def test_state_evidence_requires_matching_observed_and_baseline_order() -> None:
    with pytest.raises(ValueError, match="same order"):
        InstrumentStateEvidence(
            run_id="run-1",
            observed_state=[InstrumentStateSnapshot(instrument_id="source-a")],
            baseline_state=[InstrumentStateSnapshot(instrument_id="source-b")],
        )


def test_execution_uses_provider_selected_config_instrument(
    tmp_path: Path,
) -> None:
    config = config_with_instrument_id("source-a")
    composition = compose_test_instruments(
        config=config,
        provider=TestSignalInstrumentProvider(),
    )
    manifest = execute_invocation_run(
        config=config,
        experiment=load_invocation(),
        system=composition.system,
        instrument_backend=composition.backend,
        project_root=tmp_path,
    )
    snapshot = read_run_record_json(
        run_id=manifest.run_id,
        selector="instrument-state-evidence",
        services=sqlite_project_services(tmp_path),
        expected_kind="instrument_state_evidence",
    )
    evidence = InstrumentStateEvidence.model_validate(snapshot.content)

    assert manifest.status == "completed"
    repository = sqlite_run_repository(tmp_path)
    records = repository.list_contents(
        manifest.run_id, limit=100, kind=HOST_PARAMETER_EVIDENCE_KIND
    ).items
    assert records
    reads = [
        read
        for record in records
        for read in read_host_parameter_evidence(
            repository, manifest.run_id, record.id
        ).evidence.entries
    ]
    assert sorted(read.point_ordinal for read in reads) == list(range(len(reads)))
    assert [state.instrument_id for state in evidence.observed_state] == ["source-a"]
    assert evidence.baseline_state == evidence.observed_state


def test_execution_uses_resolved_catalog_without_redescribing_provider(
    tmp_path: Path,
) -> None:
    provider = _CountingProvider()
    config = load_config()
    composition = compose_test_instruments(
        config=config,
        provider=provider,
    )

    manifest = execute_invocation_run(
        config=config,
        experiment=load_invocation(),
        system=composition.system,
        instrument_backend=composition.backend,
        project_root=tmp_path,
    )

    assert manifest.status == "completed"
    assert provider.describe_calls == 1
    assert provider.connect_calls == 1


def test_adaptive_execution_observes_and_runs_optimizer_domains_in_one_session(
    tmp_path: Path,
) -> None:
    config = load_config()
    composition = compose_test_instruments(
        config=config,
        provider=TestSignalInstrumentProvider(),
    )
    optimizer = _TwoDomainOptimizer(tmp_path)

    manifest = execute_invocation_run(
        config=config,
        experiment=load_invocation().adaptive(optimizer, max_points=7),
        system=composition.system,
        instrument_backend=composition.backend,
        project_root=tmp_path,
    )
    dataset = read_run_measurement_dataset(
        run_id=manifest.run_id,
        services=sqlite_project_services(tmp_path),
    ).dataset

    assert manifest.status == "completed"
    completed_counts = [
        context.region.completed_point_count
        for context in optimizer.contexts
        if context.region
    ]
    assert completed_counts == [
        3,
        3,
        4,
        5,
    ]
    assert optimizer.durable_point_counts == [3, 3, 4, 5]
    assert optimizer.contexts[1].ledger.entries[0].outcome == "rejected"
    assert "stale" in (optimizer.contexts[1].ledger.entries[0].reason or "")
    assert all(
        len(context.observations[-1].measurements) == 1
        for context in optimizer.contexts
    )
    assert len(dataset.records) == 5
    assert [record.point_index for record in dataset.records] == list(range(5))
    assert dataset.records[-2].coordinates["drive_frequency"] == (
        MeasurementScalar.create(dtype="float64", value=5.2, unit="GHz")
    )
    assert dataset.records[-1].coordinates["drive_frequency"] == (
        MeasurementScalar.create(dtype="float64", value=5.3, unit="GHz")
    )


def test_adaptive_execution_streams_a_queued_operator_range_before_optimizer(
    tmp_path: Path,
) -> None:
    config = load_config()
    composition = compose_test_instruments(
        config=config,
        provider=TestSignalInstrumentProvider(),
    )
    queue = _OperatorQueuePort(
        queued=QueuedOperatorDomainRequest(
            request=OperatorDomainRequest(
                request_id="operator-queue-1",
                coordinate_mode="free",
                region_scope="current",
                region_ids=(),
                region_count=1,
                requested_fragment=ResolvedDomainFragment.points(
                    (
                        {"drive_frequency": Quantity(5.17, "GHz")},
                        {"drive_frequency": Quantity(5.19, "GHz")},
                    )
                ),
                fragment=ResolvedDomainFragment.points(
                    (
                        {"drive_frequency": Quantity(5.17, "GHz")},
                        {"drive_frequency": Quantity(5.19, "GHz")},
                    )
                ),
            ),
        ),
        decisions=[],
        expected_completed_point_count=5,
    )

    manifest = execute_invocation_run(
        config=config,
        experiment=load_invocation().adaptive(_StopOptimizer(), max_points=6),
        system=composition.system,
        instrument_backend=composition.backend,
        project_root=tmp_path,
        domain_proposals=queue,
    )
    dataset = read_run_measurement_dataset(
        run_id=manifest.run_id,
        services=sqlite_project_services(tmp_path),
    ).dataset

    assert manifest.status == "completed"
    assert len(queue.decisions) == 1
    assert queue.decisions[0].proposal.source == "operator"
    assert queue.decisions[0].accepted_point_start == 3
    assert queue.decisions[0].accepted_point_count == 2
    assert queue.closed_reason == "operator domain completed"
    assert len(dataset.records) == 5
    assert [
        record.coordinates["drive_frequency"] for record in dataset.records[-2:]
    ] == [
        MeasurementScalar.create(dtype="float64", value=5.17, unit="GHz"),
        MeasurementScalar.create(dtype="float64", value=5.19, unit="GHz"),
    ]


def test_operator_request_remains_eligible_after_optimizer_retry_budget(
    tmp_path: Path,
) -> None:
    config = load_config()
    composition = compose_test_instruments(
        config=config,
        provider=TestSignalInstrumentProvider(),
    )
    optimizer_limit = 4 * 4
    queue = _OperatorQueuePort(
        queued=QueuedOperatorDomainRequest(
            request=OperatorDomainRequest(
                request_id="operator-queue-1",
                coordinate_mode="free",
                region_scope="current",
                region_ids=(),
                region_count=1,
                requested_fragment=ResolvedDomainFragment.points(
                    ({"drive_frequency": Quantity(5.17, "GHz")},)
                ),
                fragment=ResolvedDomainFragment.points(
                    ({"drive_frequency": Quantity(5.17, "GHz")},)
                ),
            ),
        ),
        decisions=[],
        empty_polls_before_ready=optimizer_limit,
    )

    execute_invocation_run(
        config=config,
        experiment=load_invocation().adaptive(
            _AlwaysStaleOptimizer(),
            max_points=4,
        ),
        system=composition.system,
        instrument_backend=composition.backend,
        project_root=tmp_path,
        domain_proposals=queue,
    )

    assert len(queue.decisions) == optimizer_limit + 1
    assert all(
        decision.proposal.source == "optimizer" for decision in queue.decisions[:-1]
    )
    assert queue.decisions[-1].proposal.source == "operator"
    assert queue.decisions[-1].outcome == "accepted"
