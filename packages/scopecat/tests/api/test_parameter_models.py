"""One descriptor declaration across concrete edits and symbolic references."""

from typing import TYPE_CHECKING, Literal, assert_type

import pytest

import scopecat as sc
from scopecat.api.parameters import (
    ParameterTable,
    ParameterWorkspace,
    TypedParameterTable,
    _TableData,
)
from scopecat.authoring.parameter_models import parameter_key, parameter_table_schema
from scopecat.config.parameter_resolution import validate_parameter_snapshot
from scopecat.program.value_refs import internal_value_ref_parameter_lookup
from scopecat.records.parameter import TableParameterValue


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


class Envelope(sc.ParameterModel, table="envelopes"):
    """Pulse envelope choices shared with the laboratory compiler."""

    qubit: sc.Param[str] = sc.param(key=True)
    kind: sc.Param[Literal["constant", "cosine_flat_top"]] = sc.param(
        default="constant"
    )
    duration: sc.Magnitude[float | None] = sc.quantity(
        unit="ns", default=None, minimum=4
    )


def test_initial_models_preserve_choices_unknown_cells_and_stored_units() -> None:
    catalog = sc.parameter_catalog("catalog", Envelope, Drive)
    snapshot = sc.parameter_snapshot(
        "initial",
        tables={
            Envelope: [Envelope(qubit="q0")],
            Drive: [Drive(qubit="q0", duration=80)],
        },
    )
    assert not validate_parameter_snapshot(catalog, snapshot, allow_missing=True)
    definition = sc.parameter_definition(Envelope)
    assert definition.description == Envelope.__doc__
    assert isinstance(definition.value_type, sc.TableType)
    assert definition.value_type.columns[1].value_type.atom == sc.StringType(
        choices=("constant", "cosine_flat_top")
    )
    envelope = snapshot.get("envelopes")
    assert isinstance(envelope, TableParameterValue)
    assert dict(envelope.rows[0]) == {"qubit": "q0", "kind": "constant"}
    drive = snapshot.get("drive")
    assert isinstance(drive, TableParameterValue)
    assert drive.rows[0]["duration"] == sc.Quantity(80, "ns")
    row = Envelope(qubit="q0")
    row.kind = "invalid"  # pyright: ignore[reportAttributeAccessIssue]
    with pytest.raises(ValueError, match="one of"):
        sc.parameter_snapshot("invalid", tables={Envelope: [row]})
    with pytest.raises(ValueError, match=r"minimum|>=|at least"):
        sc.parameter_snapshot(
            "invalid", tables={Drive: [Drive(qubit="q0", duration=1)]}
        )


def test_unknown_row_display_and_copy_do_not_consume_or_bind_values() -> None:
    from copy import copy, deepcopy

    raw = table()
    raw["new"] = {"qubit": "new"}
    rows = TypedParameterTable(raw, Drive)
    unknown = rows["new"]
    assert "duration=<unknown>" in repr(unknown)
    for detached in (unknown.copy(), copy(unknown), deepcopy(unknown)):
        assert type(detached) is Drive
        assert "duration=<unknown>" in repr(detached)
        with pytest.raises(ValueError, match="unknown"):
            _ = detached.duration
        detached.duration = 96
        assert raw["new"]["duration"] is None
        rows["new"] = detached
        assert rows["new"].duration == 96
        raw["new"]["duration"] = None
    original = rows["q0"]
    detached = original.copy()
    detached.duration = 100
    assert original.duration == 64


def test_model_binding_rejects_a_changed_lookup_key() -> None:
    from dataclasses import replace

    raw = table()
    changed = _TableData("drive", replace(raw.schema, primary_key=("duration",)))
    changed.load(({"qubit": "q0", "duration": sc.Quantity(64, "ns")},))
    with pytest.raises(TypeError, match="primary key"):
        TypedParameterTable(ParameterTable(changed), Drive)
