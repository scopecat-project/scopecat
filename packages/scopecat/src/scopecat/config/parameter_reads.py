"""Compare captured query values without granting calibration applicability."""

from dataclasses import dataclass
from typing import Literal

from scopecat.compiler.relations.scalar_eval import cell_matches
from scopecat.kernel.value_identity import scalar_identity, scalar_values_equal
from scopecat.records.parameter import (
    ParameterSnapshot,
    ScalarParameterValue,
    TableParameterValue,
)
from scopecat.records.parameter_read import (
    KeyedParameterRead,
    ScalarExpressionReadEvidence,
)


@dataclass(frozen=True)
class ParameterReadChange:
    read: KeyedParameterRead
    reason: Literal["table_missing", "membership_changed", "cells_changed"]
    columns: tuple[str, ...] = ()


def _matches(left: object, right: object) -> bool:
    try:
        return scalar_values_equal(left, right)
    except ValueError:
        # Incompatible quantity dimensions cannot select the same row.
        return False


def compare_parameter_reads(
    reads: tuple[KeyedParameterRead, ...],
    snapshot: ParameterSnapshot,
    *,
    key_semantics: Literal["query", "relation"] = "query",
) -> tuple[ParameterReadChange, ...]:
    """Explain changed recorded reads, including lost/ambiguous membership.

    Empty changes mean only that these reads are unchanged. In particular an
    empty capture is not proof that a measurement has no parameter dependencies.
    Callers must supply the effective snapshot for the captured parameter scope.
    """
    changes: list[ParameterReadChange] = []
    matches = _matches if key_semantics == "query" else cell_matches
    for read in reads:
        table = snapshot.get(read.table)
        if not isinstance(table, TableParameterValue):
            changes.append(ParameterReadChange(read, "table_missing"))
            continue
        rows = [
            row
            for row in table.rows
            if all(matches(row.get(key.id), key.value) for key in read.key)
        ]
        if len(rows) != 1:
            changes.append(ParameterReadChange(read, "membership_changed"))
            continue
        changed = tuple(
            cell.id
            for cell in read.cells
            if scalar_identity(rows[0].get(cell.id)) != scalar_identity(cell.value)
        )
        if changed:
            changes.append(ParameterReadChange(read, "cells_changed", changed))
    return tuple(changes)


@dataclass(frozen=True)
class ScalarExpressionReadComparison:
    changed_scalars: tuple[str, ...]
    changed_keyed: tuple[ParameterReadChange, ...]
    incomplete_reasons: tuple[str, ...]

    @property
    def status(self) -> Literal["changed", "unknown", "unchanged"]:
        if self.changed_scalars or self.changed_keyed:
            return "changed"
        return "unknown" if self.incomplete_reasons else "unchanged"


def compare_expression_parameter_reads(
    evidence: ScalarExpressionReadEvidence, snapshot: ParameterSnapshot
) -> ScalarExpressionReadComparison:
    """Compare one expression context; never upgrade incomplete capture to valid."""
    changed: list[str] = []
    for read in evidence.scalars:
        value = snapshot.get(read.id)
        if not isinstance(value, ScalarParameterValue) or scalar_identity(
            value.value
        ) != scalar_identity(read.value):
            changed.append(read.id)
    return ScalarExpressionReadComparison(
        tuple(changed),
        compare_parameter_reads(evidence.keyed, snapshot, key_semantics="relation"),
        evidence.incomplete_reasons,
    )
