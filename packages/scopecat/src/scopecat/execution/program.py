"""Bounded residual operations consumed by the run interpreter."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING

from scopecat.inspection import (
    CompiledArtifactInspection,
    CompiledProgramInspectionQuery,
)
from scopecat.records.costs import RunCompilationCost
from scopecat.sdk.payloads import PayloadCodecRegistry

if TYPE_CHECKING:
    from scopecat.compiler.bound_facts import BoundMeasurementCompute
    from scopecat.execution.local.program import (
        ApplyStateOperation,
        ComputeOperation,
        LocalOperation,
    )
    from scopecat.kernel.graph_identity import ValueId
    from scopecat.kernel.points import AcceptedRunPoint, PointProposalAttempt
    from scopecat.kernel.resource_identity import (
        DomainTargetRequirement,
        ResourceRequirement,
    )
    from scopecat.measurements.points import RunPointCatalog
    from scopecat.measurements.projection import MeasurementProjection
    from scopecat.optimization import AdaptiveDomainPlan
    from scopecat.planning.point_order import PointExecutionGroup
    from scopecat.program.scans import PointSchedule
    from scopecat.records.config import ConfigContentHash
    from scopecat.sdk.domain.execution import PreparedDomainExecution
    from scopecat.sdk.instruments.contracts import InstrumentDescription


def _every_point_count_is_durable(_point_count: int) -> bool:
    return True


@dataclass(frozen=True, slots=True)
class RunHostBinding:
    """Logical instruments hosted by one daemon backend."""

    resource_order: tuple[str, ...]
    provider_id: str
    advertised_descriptions: dict[str, InstrumentDescription] = field(repr=False)
    payload_codecs: PayloadCodecRegistry = field(
        default_factory=PayloadCodecRegistry,
        repr=False,
        compare=False,
    )


@dataclass(frozen=True, slots=True)
class RunDomainJob:
    """One prepared domain operation over exact logical points."""

    id: str
    point_ordinals: tuple[int, ...]
    execution: PreparedDomainExecution = field(repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class RunCoverageEffect:
    """Execute one bound local operation for one logical point."""

    point_index: int
    operation: LocalOperation


@dataclass(frozen=True, slots=True)
class RunCoverageCheckpoint:
    """Commit one completed point recovery group."""

    group_id: str
    point_indices: tuple[int, ...]

    def __post_init__(self) -> None:
        if not self.group_id:
            raise ValueError("coverage checkpoint group id must be non-empty")
        if not self.point_indices or len(self.point_indices) != len(
            set(self.point_indices)
        ):
            raise ValueError("coverage checkpoint points must be non-empty and unique")


type RunCoveredOperation = RunCoverageCheckpoint | RunCoverageEffect | RunDomainJob


@dataclass(frozen=True, slots=True)
class RunPointInspection:
    """Pure compilation result for one planned or unaccepted coordinate row."""

    point_index: int | None
    candidate: PointProposalAttempt
    jobs: tuple[RunDomainJob, ...]


@dataclass(frozen=True, slots=True)
class RunAcceptedCoverage:
    """One accepted contiguous point range and its lazy operation stream."""

    points: tuple[AcceptedRunPoint, ...]
    operations: Iterator[RunCoveredOperation] = field(repr=False, compare=False)


class RunCoverage:
    """A lazy operation stream rebuilt for each planning or execution pass."""

    __slots__ = (
        "_accept_all",
        "_factory",
        "_inspect",
        "_inspection_snapshots",
        "_is_durable_cut",
        "_resume_factory",
    )

    def __init__(
        self,
        factory: Callable[[int], Iterator[RunCoveredOperation]],
        *,
        inspect: Callable[
            [int | PointProposalAttempt, CompiledProgramInspectionQuery | None],
            RunPointInspection,
        ]
        | None = None,
        accept_all: Callable[
            [tuple[PointProposalAttempt, ...]],
            RunAcceptedCoverage,
        ]
        | None = None,
        is_durable_cut: Callable[[int], bool] | None = None,
        resume_factory: Callable[
            [int, frozenset[str]],
            Iterator[RunCoveredOperation],
        ]
        | None = None,
    ) -> None:
        self._factory = factory
        self._inspect = inspect
        self._accept_all = accept_all
        self._is_durable_cut = is_durable_cut or _every_point_count_is_durable
        self._resume_factory = resume_factory
        self._inspection_snapshots: OrderedDict[
            tuple[str, int | str],
            RunPointInspection,
        ] = OrderedDict()

    def __iter__(self) -> Iterator[RunCoveredOperation]:
        return self._factory(0)

    def suffix(self, start_point_index: int) -> Iterator[RunCoveredOperation]:
        """Materialize operations only for the remaining static point suffix."""

        if start_point_index < 0:
            raise ValueError("coverage suffix start must be non-negative")
        if not self.is_durable_cut(start_point_index):
            raise ValueError("coverage suffix starts inside a point group")
        return self._factory(start_point_index)

    def resume(
        self,
        start_point_index: int,
        *,
        completed_group_ids: frozenset[str],
    ) -> Iterator[RunCoveredOperation]:
        """Materialize a suffix while omitting exact durable recovery groups."""

        if not completed_group_ids:
            return self.suffix(start_point_index)
        if start_point_index < 0:
            raise ValueError("coverage resume start must be non-negative")
        if not self.is_durable_cut(start_point_index):
            raise ValueError("coverage resume starts inside a point group")
        if self._resume_factory is None:
            raise ValueError("run coverage does not support sparse group recovery")
        return self._resume_factory(start_point_index, completed_group_ids)

    def is_durable_cut(self, completed_point_count: int) -> bool:
        """Return whether a canonical coverage prefix is safe to resume from."""

        return self._is_durable_cut(completed_point_count)

    def inspect(
        self,
        point: int | PointProposalAttempt,
        *,
        query: CompiledProgramInspectionQuery | None = None,
    ) -> RunPointInspection | None:
        """Compile once, then project artifact-owned inspection pages."""

        if self._inspect is None:
            return None
        key = (
            ("point", point)
            if isinstance(point, int)
            else ("candidate", point.proposal_fingerprint)
        )
        snapshot = self._inspection_snapshots.get(key)
        if snapshot is None:
            snapshot = self._inspect(point, None)
            self._inspection_snapshots[key] = snapshot
            if len(self._inspection_snapshots) > 8:
                self._inspection_snapshots.popitem(last=False)
        else:
            self._inspection_snapshots.move_to_end(key)
        if query is None:
            return snapshot
        return replace(
            snapshot,
            jobs=tuple(
                replace(
                    job,
                    execution=replace(
                        job.execution,
                        inspection=_project_compiled_inspection(
                            job.execution,
                            query,
                        ),
                    ),
                )
                for job in snapshot.jobs
            ),
        )

    def accept(self, candidate: PointProposalAttempt) -> RunAcceptedCoverage:
        """Append one candidate and return its lazy execution coverage."""

        if self._accept_all is None:
            raise ValueError("run coverage does not accept adaptive points")
        return self._accept_all((candidate,))

    def accept_all(
        self,
        candidates: tuple[PointProposalAttempt, ...],
    ) -> RunAcceptedCoverage:
        """Append one complete domain fragment with lazy batched coverage."""

        if self._accept_all is None:
            raise ValueError("run coverage does not accept adaptive domains")
        return self._accept_all(candidates)


def _project_compiled_inspection(
    execution: PreparedDomainExecution,
    query: CompiledProgramInspectionQuery,
) -> CompiledArtifactInspection | None:
    if execution.inspection is None:
        return None
    projector = execution.inspection_projector
    if projector is None:
        raise RuntimeError(
            "compiled program inspection queries require an artifact projector"
        )
    return projector(query)


@dataclass(frozen=True, slots=True)
class RunProgram:
    """Admissible residual effect program with lazily compiled coverage.

    Logical point identity and measurement correlation are independent of how
    ``coverage`` partitions physical work. Static resource authority is complete
    before admission; domain preparations are rebuilt one bounded batch at a
    time during explicit inspection or execution.
    """

    config_content_hash: ConfigContentHash
    host: RunHostBinding | None
    coverage: RunCoverage = field(repr=False, compare=False)
    points: RunPointCatalog = field(repr=False)
    measurements: MeasurementProjection = field(repr=False)
    point_schedule: PointSchedule
    point_groups: tuple[PointExecutionGroup, ...] = field(repr=False)
    resource_requirements: tuple[ResourceRequirement, ...]
    domain_target_requirement: DomainTargetRequirement | None
    adaptive_domain_plan: AdaptiveDomainPlan | None = field(
        default=None,
        repr=False,
    )
    success_state: tuple[ApplyStateOperation, ...] = field(
        default=(),
        repr=False,
        compare=False,
    )
    measurement_computes: tuple[BoundMeasurementCompute, ...] = field(
        default=(),
        repr=False,
        compare=False,
    )
    preview_compute_operations: tuple[ComputeOperation, ...] = field(
        default=(),
        repr=False,
        compare=False,
    )

    compilation_cost: RunCompilationCost | None = field(default=None, compare=False)

    @property
    def experiment_id(self) -> str:
        return self.points.experiment_id

    @property
    def resource_order(self) -> tuple[str, ...]:
        return () if self.host is None else self.host.resource_order

    @property
    def runtime_value_ids(self) -> tuple[ValueId, ...]:
        """Values whose execution results must cross the coverage boundary."""

        return tuple(
            dict.fromkeys(
                (
                    *self.measurements.runtime_value_ids,
                    *(
                        binding.value_id
                        for compute in self.measurement_computes
                        for binding in compute.value_inputs
                    ),
                )
            )
        )
