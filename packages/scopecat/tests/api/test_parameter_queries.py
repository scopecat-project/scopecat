import pytest

import scopecat as sc
from scopecat.authoring.parameter_queries import QueryInput
from scopecat.records.parameter import ParameterSnapshot, TableParameterValue


class Timing(sc.ParameterModel, table="timing"):
    qubit: sc.Param[sc.EntityRef] = sc.param(key=True, entity_kind="logical_qubit")
    width: sc.Magnitude[float] = sc.quantity(unit="ns", minimum=0)
    unused: sc.Magnitude[float] = sc.quantity(unit="ns")


def test_projection_preserves_units_identity_and_source_without_unused_cells() -> None:
    query = (
        sc.parameter_table(Timing)
        .lookup(qubit=QueryInput("target"))
        .select(duration="width")
    )
    source = ParameterSnapshot(
        id="point",
        values=(
            TableParameterValue(
                id="timing",
                rows=(
                    {
                        "qubit": sc.EntityRef(
                            id="q0", kind="logical_qubit", metadata={"label": "sample"}
                        ),
                        "width": sc.Quantity(0.024, "us"),
                    },
                    {"qubit": sc.EntityRef(id="q1", kind="logical_qubit")},
                ),
            ),
        ),
    )
    result = query.resolve(source, {"target": "q0"})
    assert result.values == {"duration": sc.Quantity(24, "ns")}
    assert result.snapshot_id == "point"
    assert result.table == "timing"
    assert result.fields == {"duration": "width"}
    assert result.key == {"qubit": sc.EntityRef(id="q0", kind="logical_qubit")}
    with pytest.raises(ValueError, match="width is unknown"):
        query.resolve(source, {"target": "q1"})
    with pytest.raises(ValueError, match="found 0"):
        query.resolve(source, {"target": "q2"})


def test_duplicate_key_is_not_silently_selected() -> None:
    row = {
        "qubit": sc.EntityRef(id="q0", kind="logical_qubit"),
        "width": sc.Quantity(24, "ns"),
    }
    source = ParameterSnapshot(
        id="duplicate", values=(TableParameterValue(id="timing", rows=(row, row)),)
    )
    with pytest.raises(ValueError, match="found 2"):
        sc.parameter_table(Timing).lookup(qubit="q0").select("width").resolve(
            source, {}
        )


def test_query_validates_schema_and_selected_value() -> None:
    with pytest.raises(ValueError, match="expected keys"):
        sc.parameter_table(Timing).lookup()
    with pytest.raises(ValueError, match="declared fields"):
        sc.parameter_table(Timing).lookup(qubit="q0").select("widht")
    source = ParameterSnapshot(
        id="bad",
        values=(
            TableParameterValue(
                id="timing",
                rows=(
                    {
                        "qubit": sc.EntityRef(id="q0", kind="logical_qubit"),
                        "width": sc.Quantity(-1, "ns"),
                    },
                ),
            ),
        ),
    )
    with pytest.raises(ValueError, match="width"):
        sc.parameter_table(Timing).lookup(qubit="q0").select("width").resolve(
            source, {}
        )
