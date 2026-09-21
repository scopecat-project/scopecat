"""Typed compiler reads preserve stored meaning rather than author defaults."""

import pytest

import scopecat as sc
from scopecat.records.parameter import ParameterSnapshot, TableParameterValue


class Drive(sc.ParameterModel, table="drive"):
    qubit: sc.Param[sc.EntityRef] = sc.param(key=True, entity_kind="logical_qubit")
    operation: sc.Param[str] = sc.param(key=True)
    duration: sc.Magnitude[float] = sc.quantity(unit="ns", default=64)
    amplitude: sc.Param[float | None] = sc.param(default=0.2)


def test_snapshot_rows_normalize_units_and_detach_edits() -> None:
    snapshot = ParameterSnapshot(
        id="effective",
        values=(
            TableParameterValue(
                id="drive",
                rows=(
                    {
                        "qubit": sc.EntityRef(id="q0", kind="logical_qubit"),
                        "operation": "x90",
                        "duration": sc.Quantity(0.032, "us"),
                    },
                ),
            ),
        ),
    )
    [row] = sc.parameter_rows(snapshot, Drive)
    assert row.duration == pytest.approx(32)
    assert row.amplitude is None
    assert row.qubit.id == "q0" and row.operation == "x90"
    row.duration = 80
    [again] = sc.parameter_rows(snapshot, Drive)
    assert again.duration == pytest.approx(32)
    assert sc.parameter_update(
        Drive.duration, (again.qubit, again.operation), 40
    ).key == {
        "qubit": again.qubit,
        "operation": "x90",
    }


def test_unknown_required_cell_never_uses_constructor_default() -> None:
    snapshot = ParameterSnapshot(
        id="unknown",
        values=(
            TableParameterValue(
                id="drive",
                rows=(
                    {
                        "qubit": sc.EntityRef(id="q0", kind="logical_qubit"),
                        "operation": "x90",
                    },
                ),
            ),
        ),
    )
    [row] = sc.parameter_rows(snapshot, Drive)
    with pytest.raises(ValueError, match=r"duration.*unknown"):
        _ = row.duration
    assert row.amplitude is None


def test_empty_table_is_distinct_from_missing_table() -> None:
    empty = sc.parameter_snapshot("empty", tables={Drive: []})
    assert sc.parameter_rows(empty, Drive) == ()
    with pytest.raises(ValueError, match=r"drive.*missing"):
        sc.parameter_rows(ParameterSnapshot(id="absent"), Drive)


def test_declared_units_reject_incompatible_stored_values() -> None:
    snapshot = ParameterSnapshot(
        id="bad",
        values=(
            TableParameterValue(
                id="drive",
                rows=(
                    {
                        "qubit": sc.EntityRef(id="q0", kind="logical_qubit"),
                        "operation": "x90",
                        "duration": sc.Quantity(1, "V"),
                    },
                ),
            ),
        ),
    )
    with pytest.raises(ValueError, match="quantity must use dimension"):
        sc.parameter_rows(snapshot, Drive)
