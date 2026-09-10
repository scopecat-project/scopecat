from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Annotated, assert_type

import pytest

import scopecat as sc
from scopecat.api.parameters import (
    ParameterTable,
    ParameterWorkspace,
    TypedParameterTable,
    _TableData,
)
from scopecat.kernel.value_types import Quantity as QuantityType


@dataclass
class Drive:
    id: str
    frequency: Annotated[float, sc.ParameterSpec(unit="GHz", minimum=0, maximum=10)]
    amplitude: float | None = 0.25
    enabled: bool = True


@dataclass(slots=True)
class DriveMHz:
    id: str
    frequency: Annotated[float, sc.ParameterSpec(unit="MHz", minimum=0, maximum=10000)]
    amplitude: float | None = 0.25
    enabled: bool = True


def _table() -> ParameterTable:
    data = _TableData("drive", sc.dataclass_table_schema(Drive, primary_key=("id",)))
    data.load(({"id": "q0", "frequency": sc.Quantity(5100, "MHz"), "enabled": True},))
    return ParameterTable(data)


def test_declaration_is_pure_and_defaults_do_not_hydrate_unknown_cells() -> None:
    schema = sc.dataclass_table_schema(Drive, primary_key=("id",))
    assert schema.columns[1].value_type.atom == QuantityType(
        unit="GHz", minimum=0, maximum=10
    )
    table = _table()
    typed = TypedParameterTable(table, Drive)
    q0 = assert_type(typed["q0"], Drive)
    assert isinstance(q0, Drive)
    assert q0.amplitude is None
    assert table["q0"]["amplitude"] is None
    assert q0.frequency == 5.1
    assert table["q0"]["frequency"] == sc.Quantity(5100, "MHz")
    assert q0 == typed["q0"]
    assert "frequency=5.1" in repr(q0)
    new = typed.add(Drive("q1", 5.2))
    assert new.amplitude == 0.25
    assert table["q1"]["frequency"] == sc.Quantity(5.2, "GHz")
    with pytest.raises(ValueError, match="already exists"):
        typed.add(Drive("q1", 5.3))


def test_dictionary_and_typed_units_share_live_edits_without_read_rewrites() -> None:
    table = _table()
    typed = TypedParameterTable(table, Drive)
    mhz = TypedParameterTable(table, DriveMHz)
    q0 = typed["q0"]
    m0 = mhz["q0"]
    assert m0.frequency == 5100
    q0.frequency = 5.2
    assert m0.frequency == 5200
    table["q0"]["frequency"] = sc.Quantity(5300, "MHz")
    assert q0.frequency == 5.3
    assert table["q0"]["frequency"] == sc.Quantity(5300, "MHz")
    table["q0"]["frequency"] = 5.6  # Naked dictionary values use table units.
    assert m0.frequency == 5600
    assert table["q0"]["frequency"] == 5.6
    q0.amplitude = None  # Already unknown: no edit.
    assert table["q0"]["amplitude"] is None
    q0.amplitude = 0.4
    assert table["q0"]["amplitude"] == 0.4
    q0.amplitude = None
    assert table["q0"]["amplitude"] is None
    with pytest.raises(ValueError, match="row key"):
        q0.id = "q2"
    del table["q0"]
    with pytest.raises(KeyError, match="deleted"):
        _ = q0.amplitude


def test_missing_required_values_and_schema_mismatch_are_actionable() -> None:
    table = _table()
    table["q1"] = {"id": "q1"}
    with pytest.raises(ValueError, match=r"drive.*frequency.*unknown"):
        _ = TypedParameterTable(table, Drive)["q1"].frequency

    @dataclass
    class Wrong:
        id: str
        frequency: float
        amplitude: float | None
        enabled: bool

    with pytest.raises(TypeError, match=r"drive.frequency.*type/unit/bounds"):
        TypedParameterTable(table, Wrong)

    @dataclass
    class Missing:
        id: str

    with pytest.raises(TypeError, match=r"columns differ.*change the schema"):
        TypedParameterTable(table, Missing)


def test_metadata_and_scalar_types_use_existing_schema() -> None:
    @dataclass
    class Scalars:
        id: str
        count: int = field(
            default=1, metadata={"parameter": sc.ParameterSpec(minimum=0, maximum=100)}
        )
        enabled: bool = True
        frequency: float | None = field(
            default=None, metadata={"parameter": sc.ParameterSpec(unit="MHz")}
        )

    schema = sc.dataclass_table_schema(Scalars, primary_key=("id",))
    assert schema.columns[1].value_type.atom == sc.IntType(minimum=0, maximum=100)
    assert schema.columns[2].value_type.atom == sc.BoolType()
    assert schema.columns[3].value_type.atom == sc.QuantityType(unit="MHz")


def test_optional_annotation_nesting_and_frozen_declarations() -> None:
    @dataclass
    class Outer:
        id: str
        value: Annotated[float | None, sc.ParameterSpec(unit="GHz")] = None

    @dataclass
    class Inner:
        id: str
        value: Annotated[float, sc.ParameterSpec(unit="GHz")] | None = None

    assert sc.dataclass_table_schema(
        Outer, primary_key=("id",)
    ) == sc.dataclass_table_schema(Inner, primary_key=("id",))

    @dataclass(frozen=True)
    class Frozen:
        id: str

    with pytest.raises(TypeError, match="remove frozen=True"):
        sc.dataclass_table_schema(Frozen, primary_key=("id",))


if TYPE_CHECKING:

    def editor_examples(params: ParameterWorkspace) -> None:
        table = assert_type(
            params.table("drive", row_type=Drive), TypedParameterTable[Drive]
        )
        q0 = assert_type(table["q0"], Drive)
        assert_type(q0.frequency, float)
        assert_type(q0.amplitude, float | None)
        q0.frequncy = 5.2  # pyright: ignore[reportAttributeAccessIssue]
        q0.frequency = "5.2"  # pyright: ignore[reportAttributeAccessIssue]
        _ = q0.amplitude * 2  # pyright: ignore[reportOptionalOperand, reportUnknownVariableType]
        if q0.amplitude is not None:
            assert_type(q0.amplitude * 2, float)
        assert_type(params.table("drive"), ParameterTable)
