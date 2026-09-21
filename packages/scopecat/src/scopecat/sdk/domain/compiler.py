"""Compiler contract for complete, bounded domain batches."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Protocol

from scopecat.sdk.domain.batch import DomainBatchRequest
from scopecat.sdk.domain.execution import PreparedDomainExecution


@dataclass(frozen=True, slots=True)
class DomainBatchPreparationLimits:
    """Core-enforced bounds for one compiler candidate preparation."""

    max_points: int
    max_retained_bytes: int


@dataclass(frozen=True, slots=True)
class DomainBatchPreparationCost:
    """Stable working set retained by one prepared candidate closure.

    ``retained_bytes`` accounts for compiler-owned bulk buffers and serialized
    content that remain reachable after ``prepare_batch`` returns. Ordinary
    Python object headers need not be estimated. The closure must not grow this
    retained working set when exact subranges are compiled.
    """

    analyzed_point_count: int
    retained_bytes: int


@dataclass(frozen=True, slots=True)
class DomainBatchCandidate:
    """Reusable analysis of one candidate point prefix.

    Core may shorten or split the compatible prefix at the point cuts declared
    by ``DomainBatchRequest.legal_cut_offsets``. Point recovery groups are a
    scheduling preference, not a target batch constraint. The compiler-owned
    closure retains lowering or packing work shared by final subranges.
    """

    compatible_point_count: int
    preparation_cost: DomainBatchPreparationCost
    _compile: Callable[[DomainBatchRequest], PreparedDomainExecution] = field(
        repr=False,
        compare=False,
    )

    def compile(self, request: DomainBatchRequest) -> PreparedDomainExecution:
        """Close one exact subrange of the compatible prefix."""

        return self._compile(request)

    @classmethod
    def from_points[PointT](
        cls,
        request: DomainBatchRequest,
        prepared_points: Sequence[PointT],
        *,
        retained_bytes: int,
        compile_batch: Callable[
            [DomainBatchRequest, tuple[PointT, ...]], PreparedDomainExecution
        ],
    ) -> DomainBatchCandidate:
        """Retain one prepared value per point for a fully compatible candidate.

        Host-selected subranges receive the original values in request order,
        without repeating point preparation or copying bulk buffers. The caller
        must account for all retained bulk data, including callback captures, and
        must not grow that working set inside ``compile_batch``. Initial limits
        still belong to the compiler. Use the direct constructor when analysis
        accepts only a prefix or retains batch-wide packing rather than points.
        """

        by_ordinal = dict(zip(request.point_ordinals, prepared_points, strict=True))

        def compile_exact(selected: DomainBatchRequest) -> PreparedDomainExecution:
            return compile_batch(
                selected,
                tuple(by_ordinal[ordinal] for ordinal in selected.point_ordinals),
            )

        return cls(
            compatible_point_count=len(request.points),
            preparation_cost=DomainBatchPreparationCost(
                analyzed_point_count=len(request.points),
                retained_bytes=retained_bytes,
            ),
            _compile=compile_exact,
        )


class DomainCompiler(Protocol):
    """Negotiate and compile bounded batches for one configured domain target."""

    @property
    def target_id(self) -> str: ...

    @property
    def target_kind(self) -> str: ...

    @property
    def instrument_ids(self) -> tuple[str, ...]:
        """Return the physical footprint reserved before batch compilation."""
        ...

    def initial_batch_preparation_limits(
        self,
        point_count: int,
    ) -> DomainBatchPreparationLimits:
        """Bound candidate points and retained bytes before resolving inputs."""
        ...

    def prepare_batch(self, request: DomainBatchRequest) -> DomainBatchCandidate:
        """Analyze a candidate and retain work needed to close its executions.

        The compiler may inspect or lower every candidate point, but must not
        perform external effects. ``compatible_point_count`` must identify a
        legal request cut. Every non-empty subrange aligned to the declared
        cuts must be independently compilable. Core may shorten or split that
        prefix at those cuts to align host-state regions or another domain call
        before asking the returned candidate to compile each exact final batch.
        """
        ...


__all__ = [
    "DomainBatchCandidate",
    "DomainBatchPreparationCost",
    "DomainBatchPreparationLimits",
    "DomainCompiler",
]
