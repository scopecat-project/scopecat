"""Query evidence survives author code and detects indirect and membership edits."""

from pydantic import TypeAdapter

import scopecat as sc
from scopecat.config.parameter_reads import compare_parameter_reads
from scopecat.records.parameter import ParameterSnapshot, TableParameterValue
from scopecat.records.parameter_read import KeyedParameterRead


class Route(sc.ParameterModel, table="routes"):
    target: sc.Param[str] = sc.param(key=True)
    calibration: sc.Param[str] = sc.param()


class Calibration(sc.ParameterModel, table="calibrations"):
    name: sc.Param[str] = sc.param(key=True)
    amplitude: sc.Magnitude[float] = sc.quantity(unit="arb")
    unused: sc.Param[float] = sc.param()


def snapshot(
    *, selected: str = "drive", amplitude: float = 0.2, unused: float = 1.0
) -> ParameterSnapshot:
    return sc.parameter_snapshot(
        "same-id",
        tables={
            Route: (Route(target="q0", calibration=selected),),
            Calibration: (
                Calibration(name="drive", amplitude=amplitude, unused=unused),
                Calibration(name="other", amplitude=0.2, unused=unused),
            ),
        },
    )


def reads() -> tuple[KeyedParameterRead, ...]:
    route = sc.parameter_table(Route).lookup(target="q0")
    calibration = sc.parameter_table(Calibration).lookup(name=route["calibration"])
    amplitude = calibration["amplitude"]
    result = sc.parameter_inputs(first=amplitude, half=amplitude / 2).resolve(
        snapshot(), {}
    )
    codec = TypeAdapter(tuple[KeyedParameterRead, ...])
    retained = codec.validate_json(codec.dump_json(result.parameter_reads))
    assert retained == result.parameter_reads
    assert len(retained) == 2  # Shared expression and indirect key captured once.
    return retained


def test_read_values_ignore_unread_columns_but_retain_indirect_key() -> None:
    evidence = reads()
    assert compare_parameter_reads(evidence, snapshot(unused=9)) == ()
    changes = compare_parameter_reads(evidence, snapshot(amplitude=0.3))
    assert [(c.read.table, c.reason, c.columns) for c in changes] == [
        ("calibrations", "cells_changed", ("amplitude",))
    ]
    # The new row gives the same amplitude; a changed route is still a dependency.
    changes = compare_parameter_reads(evidence, snapshot(selected="other"))
    assert [(c.read.table, c.columns) for c in changes] == [
        ("routes", ("calibration",))
    ]


def test_membership_is_rechecked_instead_of_only_comparing_returned_cells() -> None:
    evidence = reads()
    original = snapshot()
    table = original.get("calibrations")
    assert isinstance(table, TableParameterValue)
    for rows in (table.rows[1:], (*table.rows, table.rows[0])):
        changed = ParameterSnapshot(
            id=original.id,
            values=tuple(
                TableParameterValue(id=table.id, rows=rows)
                if value.id == table.id
                else value
                for value in original.values
            ),
        )
        changes = compare_parameter_reads(evidence, changed)
        assert [(c.read.table, c.reason) for c in changes] == [
            ("calibrations", "membership_changed")
        ]
    missing = ParameterSnapshot(id="same-id", values=())
    assert all(
        c.reason == "table_missing" for c in compare_parameter_reads(evidence, missing)
    )


def test_new_unselected_row_does_not_change_keyed_query() -> None:
    original = snapshot()
    table = original.get("calibrations")
    assert isinstance(table, TableParameterValue)
    expanded = ParameterSnapshot(
        id=original.id,
        values=tuple(
            TableParameterValue(
                id=table.id,
                rows=(*table.rows, {**table.rows[0], "name": "new"}),
            )
            if value.id == table.id
            else value
            for value in original.values
        ),
    )
    assert compare_parameter_reads(reads(), expanded) == ()


def test_small_value_changes_are_not_tolerated_as_unchanged() -> None:
    changes = compare_parameter_reads(reads(), snapshot(amplitude=0.2 + 1e-14))
    assert len(changes) == 1
    assert changes[0].columns == ("amplitude",)
