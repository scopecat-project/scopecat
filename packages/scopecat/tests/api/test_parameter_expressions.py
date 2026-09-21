import pytest

import scopecat as sc
from scopecat.authoring.parameter_queries import QueryInput


class Gate(sc.ParameterModel, table="gate"):
    name: sc.Param[str] = sc.param(key=True)
    shape: sc.Param[str] = sc.param()
    amplitude: sc.Magnitude[float] = sc.quantity(unit="arb")
    correction: sc.Param[float] = sc.param()


class Shape(sc.ParameterModel, table="shape"):
    name: sc.Param[str] = sc.param(key=True)
    width: sc.Magnitude[float] = sc.quantity(unit="us")


def test_dependent_lookup_arithmetic_and_source_evidence() -> None:
    gate = sc.parameter_table(Gate).lookup(name=QueryInput("operation"))
    shape = sc.parameter_table(Shape).lookup(name=gate["shape"])
    query = sc.parameter_inputs(
        width=shape["width"] + sc.Quantity(8, "ns"),
        amplitude=gate["amplitude"] * gate["correction"],
        ratio=shape["width"] / sc.Quantity(12, "ns"),
    )
    for name, correction in (("baseline", 0.5), ("candidate", 0.75)):
        snapshot = sc.parameter_snapshot(
            name,
            tables={
                Gate: (
                    Gate(
                        name="x90", shape="short", amplitude=0.2, correction=correction
                    ),
                ),
                Shape: (Shape(name="short", width=0.024),),
            },
        )
        result = query.resolve(snapshot, {"operation": "x90"})
        width = result.values["width"]
        assert isinstance(width, sc.Quantity)
        assert width.to("ns").value == pytest.approx(32)
        assert result.values["amplitude"] == sc.Quantity(0.2 * correction, "arb")
        assert result.values["ratio"] == pytest.approx(2)
        source = result.sources["width"][0]
        assert source.table == "shape"
        assert source.key == {"name": "short"}
        assert source.snapshot_id == name
        assert source.key_sources[0].table == "gate"
        assert source.key_sources[0].fields == {"shape": "shape"}
        assert len(result.sources["amplitude"]) == 2


def test_arithmetic_errors_identify_output_and_keep_units() -> None:
    row = sc.parameter_table(Shape).lookup(name="short")
    snapshot = sc.parameter_snapshot(
        "baseline", tables={Shape: (Shape(name="short", width=0.024),)}
    )
    for expression in (
        row["width"] + sc.Quantity(1, "V"),
        row["width"] / 0,
        row["width"] + 1,
    ):
        with pytest.raises(ValueError, match="parameter input 'duration'"):
            sc.parameter_inputs(duration=expression).resolve(snapshot, {})


def test_reflected_numeric_operations() -> None:
    row = sc.parameter_table(Gate).lookup(name="x90")
    snapshot = sc.parameter_snapshot(
        "baseline",
        tables={
            Gate: (Gate(name="x90", shape="short", amplitude=0.2, correction=0.5),)
        },
    )
    result = sc.parameter_inputs(
        offset=1 + row["correction"],
        remaining=1 - row["correction"],
        scaled=2 * row["amplitude"],
        inverse=1 / row["correction"],
        difference=row["amplitude"] - sc.Quantity(0.1, "arb"),
    ).resolve(snapshot, {})
    assert result.values == {
        "offset": 1.5,
        "remaining": 0.5,
        "scaled": sc.Quantity(0.4, "arb"),
        "inverse": 2.0,
        "difference": sc.Quantity(0.1, "arb"),
    }
