# pyright: reportPrivateUsage=false
"""Guaranteed recipe query reads without pulse construction or scan expansion."""

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import cast

from scopecat.authoring.parameter_queries import ParameterQueryResult
from scopecat.domain.program import DomainParameterRead
from scopecat.kernel.entity import EntityRef
from scopecat.records.parameter import ParameterSnapshot

from scopecat_quantum.recipe_selection import RecipeSelection

from ._ir import (
    Measurement,
    ProgramInput,
    QuantumFragment,
    _ExpandedFragment,
    _FlatTopWindowFragment,
    _GateFragment,
    _ParallelFragment,
    _QuantumParallelFragment,
    _QuantumRepeatFragment,
    _QuantumSequenceFragment,
    _RecipeScopeFragment,
    _RepeatFragment,
    _SequenceFragment,
)


@dataclass(frozen=True, slots=True)
class QuantumParameterReads:
    body: QuantumFragment
    recipes: RecipeSelection

    def __call__(
        self, inputs: Mapping[str, object], parameters: object
    ) -> tuple[DomainParameterRead, ...]:
        profile = self.recipes.profile
        reads: list[DomainParameterRead] = []
        for operation in _guaranteed_operations(self.body, inputs):
            qubits = (
                operation.qubits
                if isinstance(operation, _GateFragment)
                else (operation.result.qubit,)
            )
            operands = tuple(inputs.get(qubit.id) for qubit in qubits)
            if not all(isinstance(value, EntityRef | str) for value in operands):
                continue
            context: dict[str, object] = {
                f"operand:{index}": value
                if isinstance(value, EntityRef)
                else EntityRef(id=str(value), kind="logical_qubit")
                for index, value in enumerate(operands)
            }
            if isinstance(operation, _GateFragment):
                context["operation"] = operation.gate.id
                queries = profile.declarative_inputs(
                    gate_id=operation.gate.definition.id
                )
            else:
                queries = profile.declarative_inputs(
                    measurement_kind=operation.result.acquisition_kind
                )
            for query in queries:
                result = query.projection.resolve(
                    cast("ParameterSnapshot", parameters), context
                )
                sources = (
                    (result,)
                    if isinstance(result, ParameterQueryResult)
                    else tuple(
                        source for group in result.sources.values() for source in group
                    )
                )
                for source in sources:
                    reads.extend(_query_reads(source))
        return tuple(reads)


def _query_reads(source: ParameterQueryResult) -> Iterator[DomainParameterRead]:
    parents = tuple(
        read for parent in source.key_sources for read in _query_reads(parent)
    )
    yield from parents
    yield DomainParameterRead(
        source.read.table,
        tuple((item.id, item.value) for item in source.read.key),
        tuple(item.id for item in source.read.cells),
        parents,
    )


def _guaranteed_operations(
    body: QuantumFragment, inputs: Mapping[str, object], *, scoped: bool = False
) -> Iterator[_GateFragment | Measurement]:
    if isinstance(body, Measurement) or (
        isinstance(body, _GateFragment) and not scoped
    ):
        yield body
    elif isinstance(body, _SequenceFragment | _QuantumSequenceFragment):
        for item in body.operations:
            yield from _guaranteed_operations(item, inputs, scoped=scoped)
    elif isinstance(body, _ParallelFragment | _QuantumParallelFragment):
        for item in body.branches:
            yield from _guaranteed_operations(item, inputs, scoped=scoped)
    elif isinstance(body, _ExpandedFragment | _FlatTopWindowFragment):
        yield from _guaranteed_operations(body.body, inputs, scoped=scoped)
    elif isinstance(body, _RecipeScopeFragment):
        yield from _guaranteed_operations(body.body, inputs, scoped=True)
    elif isinstance(body, _RepeatFragment | _QuantumRepeatFragment):
        count = (
            inputs.get(body.count.id)
            if isinstance(body.count, ProgramInput)
            else body.count
        )
        if isinstance(count, int) and count > 0:
            yield from _guaranteed_operations(body.operation, inputs, scoped=scoped)
    # Scoped gates, conditional branches, dynamic fragments and entity-set
    # maps are not guaranteed baseline reads. Do not expand or guess them.
