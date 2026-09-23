"""Compare captured query values without granting calibration applicability."""

from dataclasses import dataclass
from typing import Literal

from scopecat.kernel.value_identity import scalar_identity, scalar_values_equal
from scopecat.records.parameter import ParameterSnapshot, TableParameterValue
from scopecat.records.parameter_read import KeyedParameterRead


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
    reads: tuple[KeyedParameterRead, ...], snapshot: ParameterSnapshot
) -> tuple[ParameterReadChange, ...]:
    """Explain changed recorded reads, including lost/ambiguous membership.

    Empty changes mean only that these reads are unchanged. In particular an
    empty capture is not proof that a measurement has no parameter dependencies.
    Callers must supply the effective snapshot for the captured parameter scope.
    """
    changes: list[ParameterReadChange] = []
    for read in reads:
        table = snapshot.get(read.table)
        if not isinstance(table, TableParameterValue):
            changes.append(ParameterReadChange(read, "table_missing"))
            continue
        rows = [
            row
            for row in table.rows
            if all(_matches(row.get(key.id), key.value) for key in read.key)
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
