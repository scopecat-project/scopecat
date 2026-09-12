"""Portable edits retain keys, units, unknowns and an exact draft base."""

from __future__ import annotations

import json
from typing import Literal

import pytest

import scopecat as sc
from scopecat.api.parameters import ParameterTable, TypedParameterTable, _TableData
from scopecat.authoring.parameter_models import parameter_key, parameter_table_schema


class Drive(sc.ParameterModel, table="drive"):
    qubit: sc.Param[str] = sc.param(key=True)
    frequency: sc.Magnitude[float] = sc.quantity(unit="GHz", minimum=1)
    amplitude: sc.Param[float | None] = sc.param(default=None)
    shape: sc.Param[Literal["constant", "gaussian"]] = sc.param(default="constant")


def table() -> ParameterTable:
    data = _TableData(
        "drive", parameter_table_schema(Drive, primary_key=parameter_key(Drive))
    )
    data.load(
        (
            {
                "qubit": "001",
                "frequency": sc.Quantity(5100, "MHz"),
                "shape": "constant",
            },
            {
                "qubit": "002",
                "frequency": sc.Quantity(6, "GHz"),
                "amplitude": 0.2,
                "shape": "gaussian",
            },
        )
    )
    return ParameterTable(data)


def test_json_round_trip_partial_rows_zero_and_explicit_unknown() -> None:
    target = table()
    assert target.preview_json(target.export_json()).diff == ()
    document = json.loads(target.export_json())
    assert document["rows"][0]["frequency"] == 5.1
    assert document["rows"][0]["amplitude"] is None
    document["rows"] = [{"qubit": "001", "amplitude": 0}]
    pending = target.preview_json(json.dumps(document))
    assert [(e.field, e.before, e.after) for e in pending.diff] == [
        ("amplitude", None, 0)
    ]
    assert target["001"]["amplitude"] is None
    pending.apply()
    assert target["001"]["frequency"] == sc.Quantity(5100, "MHz")
    assert target["002"]["amplitude"] == 0.2
    document = json.loads(target.export_json())
    document["rows"][0]["frequency"] = None
    target.preview_json(json.dumps(document)).apply()
    typed = TypedParameterTable(target, Drive)
    assert "frequency=<unknown>" in repr(typed["001"])
    with pytest.raises(ValueError, match="unknown"):
        _ = typed["001"].frequency


def test_import_rejects_stale_export_and_stale_preview_without_partial_edits() -> None:
    target = table()
    original = target.export_json()
    document = json.loads(original)
    document["rows"][0]["frequency"] = 5.2
    pending = target.preview_json(json.dumps(document))
    target["002"]["amplitude"] = 0.3
    with pytest.raises(ValueError, match="changed after preview"):
        pending.apply()
    with pytest.raises(ValueError, match="stale"):
        target.preview_json(original)
    assert target["001"]["frequency"] == sc.Quantity(5100, "MHz")
    assert target["002"]["amplitude"] == 0.3


def test_row_deletion_is_explicit_and_invalid_external_shapes_are_rejected() -> None:
    target = table()
    selected = target["002"]
    document = json.loads(target.export_json())
    document["rows"] = document["rows"][:1]
    assert target.preview_json(json.dumps(document)).diff == ()
    pending = target.preview_json(json.dumps(document), delete_missing=True)
    assert len(pending.diff) == 1 and pending.diff[0].after is None
    pending.apply()
    with pytest.raises(KeyError, match="deleted"):
        _ = selected["frequency"]
    document = json.loads(target.export_json())
    document["rows"][0]["index"] = None
    with pytest.raises(ValueError, match="unknown columns"):
        target.preview_json(json.dumps(document))
    del document["rows"][0]["index"]
    document["rows"].append(dict(document["rows"][0]))
    with pytest.raises(ValueError, match="duplicates"):
        target.preview_json(json.dumps(document))
    document["rows"].pop()
    document["definition"]["id"] = "other-table"
    with pytest.raises(ValueError, match="schema or unit"):
        target.preview_json(json.dumps(document))


def test_dataframe_keeps_identifiers_metadata_and_explicit_nan_policy() -> None:
    pd = pytest.importorskip("pandas")
    target = table()
    frame = target.to_dataframe()
    assert frame.loc[0, "qubit"] == "001"
    assert frame.loc[1, "qubit"] == "002"
    assert frame.loc[0, "amplitude"] is None
    assert target.preview_dataframe(frame).diff == ()
    frame.loc[0, "frequency"] = 5.2
    preview = target.preview_dataframe(frame)
    assert [(e.field, e.after) for e in preview.diff] == [
        ("frequency", sc.Quantity(5.2, "GHz"))
    ]
    preview.apply()
    lost = target.to_dataframe()
    lost.attrs.clear()
    with pytest.raises(ValueError, match="lost parameter metadata"):
        target.preview_dataframe(lost)
    frame = target.to_dataframe()
    frame.loc[1, "amplitude"] = float("nan")
    with pytest.raises(ValueError, match="NaN is ambiguous"):
        target.preview_dataframe(frame)
    target.preview_dataframe(frame, nan_as_unknown=True).apply()
    assert target["002"]["amplitude"] is None
    frame = target.to_dataframe()
    frame.loc[0, "frequency"] = pd.NA
    target.preview_dataframe(frame).apply()
    assert target["001"]["frequency"] is None


def test_entity_composite_keys_and_new_rows_validate_before_any_edit() -> None:
    class Channel(sc.ParameterModel, table="channels"):
        device: sc.Param[sc.EntityRef] = sc.param(key=True, entity_kind="device")
        channel: sc.Param[int] = sc.param(key=True)
        frequency: sc.Magnitude[float] = sc.quantity(unit="GHz", minimum=1, default=5)

    device = sc.EntityRef(id="001", kind="device", metadata={"label": "rack A"})
    raw = ParameterTable(
        _TableData(
            "channels",
            parameter_table_schema(Channel, primary_key=parameter_key(Channel)),
        )
    )
    typed = TypedParameterTable(raw, Channel)
    typed[(device, 1)] = Channel(device=device, channel=1)
    document = json.loads(typed.export_json())
    assert document["rows"][0]["device"] == device.model_dump(mode="json")
    document["rows"][0]["frequency"] = 5.2
    document["rows"].append(
        {"device": device.model_dump(mode="json"), "channel": 2, "frequency": 0.5}
    )
    with pytest.raises(ValueError, match=r"minimum|>=|at least"):
        typed.preview_json(json.dumps(document))
    assert typed[(device, 1)].frequency == 5
    del document["rows"][1]["frequency"]
    preview = typed.preview_json(json.dumps(document))
    assert len(preview.diff) == 2
    preview.apply()
    assert typed[(device, 1)].frequency == 5.2
    assert raw[(device, 2)]["frequency"] is None
    assert typed[(device, 2)].device == device
