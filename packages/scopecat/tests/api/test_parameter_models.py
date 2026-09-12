"""One descriptor declaration across concrete edits and symbolic references."""

from typing import TYPE_CHECKING, assert_type

import pytest

import scopecat as sc
from scopecat.api.parameters import (
    ParameterTable,
    ParameterWorkspace,
    TypedParameterTable,
    _TableData,
)
from scopecat.authoring.parameter_models import parameter_key, parameter_table_schema
from scopecat.program.value_refs import internal_value_ref_parameter_lookup


class Drive(sc.ParameterModel, table="drive"):
    qubit: sc.Param[str] = sc.param(key=True)
    duration: sc.Magnitude[float] = sc.quantity(unit="ns", default=64, minimum=4)
    amplitude: sc.Param[float | None] = sc.param(default=None)


class DriveUs(sc.ParameterModel, table="drive"):
    qubit: sc.Param[str] = sc.param(key=True)
    duration: sc.Magnitude[float] = sc.quantity(unit="us", default=0.064, minimum=0.004)
    amplitude: sc.Param[float | None] = sc.param(default=None)


def table() -> ParameterTable:
    data = _TableData(
        "drive", parameter_table_schema(Drive, primary_key=parameter_key(Drive))
    )
    data.load(({"qubit": "q0", "duration": sc.Quantity(0.064, "us")},))
    return ParameterTable(data)


def test_bound_views_reuse_values_units_unknowns_and_collection_edits() -> None:
    raw = table()
    rows, micro = TypedParameterTable(raw, Drive), TypedParameterTable(raw, DriveUs)
    row = rows["q0"]
    assert row.duration == 64
    assert row.amplitude is None
    assert raw["q0"]["duration"] == sc.Quantity(0.064, "us")
    row.duration = 80
    assert micro["q0"].duration == 0.08
    assert raw["q0"]["duration"] == sc.Quantity(80, "ns")
    rows["q1"] = Drive(qubit="q1")
    assert rows["q1"].duration == 64
    with pytest.raises(ValueError, match="key"):
        rows["q2"] = Drive(qubit="q3")
    with pytest.raises(ValueError, match="key"):
        row.qubit = "q3"
    del rows["q0"]
    with pytest.raises(KeyError, match="deleted"):
        _ = row.duration


def test_defaults_are_only_constructor_values_and_stored_unknown_is_not_default() -> (
    None
):
    raw = table()
    raw["q1"] = {"qubit": "q1"}
    rows = TypedParameterTable(raw, Drive)
    assert rows["q1"].amplitude is None
    with pytest.raises(ValueError, match=r"drive.*q1.*duration.*unknown"):
        _ = rows["q1"].duration
    first, second = Drive(qubit="q1"), Drive(qubit="q2")
    first.duration = 80
    assert second.duration == 64
    assert "duration=80" in repr(first)


def test_inheritance_keeps_reference_identity_on_the_selected_table() -> None:
    class OtherDrive(Drive, table="other_drive"):
        pass

    assert OtherDrive(qubit="q0").duration == 64
    assert Drive.duration.owner is Drive
    assert OtherDrive.duration.owner is OtherDrive
    locator = internal_value_ref_parameter_lookup(
        sc.parameter_ref(OtherDrive.duration, "q0")
    )
    assert locator is not None
    assert locator[0].table_id == "other_drive"
    assert dict(locator[1]) == {"qubit": "q0"}
    assert Drive.duration.owner is Drive


def test_composite_keys_require_every_component_and_fields_keep_units() -> None:
    class Pair(sc.ParameterModel, table="pairs"):
        left: sc.Param[str] = sc.param(key=True)
        right: sc.Param[str] = sc.param(key=True)
        duration: sc.Magnitude[float | None] = sc.quantity(unit="ns", default=None)

    with pytest.raises(ValueError, match=r"left.*right"):
        sc.parameter_ref(Pair.duration, "q0")
    locator = internal_value_ref_parameter_lookup(
        sc.parameter_ref(Pair.duration, ("q0", "q1"))
    )
    assert locator is not None
    assert dict(locator[1]) == {"left": "q0", "right": "q1"}
    assert locator[0].result_type.atom == sc.QuantityType(unit="ns")


if TYPE_CHECKING:

    def type_contract(params: ParameterWorkspace) -> None:
        row = Drive(qubit="q0")
        assert_type(row.duration, float)
        assert_type(row.amplitude, float | None)
        assert_type(Drive.duration, sc.Magnitude[float])
        assert_type(sc.parameter_ref(Drive.duration, "q0"), sc.ValueRef[sc.Quantity])
        assert_type(sc.parameter_ref(Drive.amplitude, "q0"), sc.ValueRef[float])
        assert_type(sc.parameter_ref(Drive.qubit, "q0"), sc.ValueRef[str])
        assert_type(params[Drive], TypedParameterTable[Drive])
        assert_type(params.declare_table(Drive), TypedParameterTable[Drive])
        row.duration = "bad"  # pyright: ignore[reportAttributeAccessIssue]
        _ = Drive(qubit=42)  # pyright: ignore[reportArgumentType]
        _ = Drive()  # pyright: ignore[reportCallIssue]
