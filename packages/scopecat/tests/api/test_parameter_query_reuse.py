from unittest.mock import patch

import pytest

import scopecat as sc
from scopecat.authoring import parameter_queries as queries
from scopecat.authoring.parameter_queries import QueryInput
from scopecat.records.parameter import ParameterSnapshot


class Calibration(sc.ParameterModel, table="calibration"):
    name: sc.Param[str] = sc.param(key=True)
    amplitude: sc.Magnitude[float] = sc.quantity(unit="arb")
    correction: sc.Param[float] = sc.param()


def test_shared_lookup_and_expression_reuse_is_local_to_resolve() -> None:
    row = sc.parameter_table(Calibration).lookup(name=QueryInput("target"))
    corrected = row["amplitude"] * row["correction"]
    query = sc.parameter_inputs(
        first=corrected, second=corrected / 2, raw=row["amplitude"]
    )
    for value, target in ((0.2, "q0"), (0.4, "q1"), (0.6, "q0")):
        # Deliberately reuse the snapshot ID; identity alone cannot authorize reuse.
        snapshot = sc.parameter_snapshot(
            "point",
            tables={
                Calibration: (
                    Calibration(name="q0", amplitude=value, correction=0.5),
                    Calibration(name="q1", amplitude=value, correction=0.25),
                )
            },
        )
        with (
            patch.object(
                ParameterSnapshot,
                "get",
                autospec=True,
                side_effect=ParameterSnapshot.get,
            ) as reads,
            patch.object(queries, "_calculate", wraps=queries._calculate) as arithmetic,
        ):
            result = query.resolve(snapshot, {"target": target})
        assert reads.call_count == 1
        assert arithmetic.call_count == 2  # Shared multiply once, then divide.
        expected = value * (0.5 if target == "q0" else 0.25)
        assert result.values["first"] == sc.Quantity(expected, "arb")
        assert result.values["second"] == sc.Quantity(expected / 2, "arb")
        assert result.values["raw"] == sc.Quantity(value, "arb")
        assert result.sources["first"] == result.sources["second"]
        assert result.sources["first"][0].key == {"name": target}
        other = "q1" if target == "q0" else "q0"
        changed_context = query.resolve(snapshot, {"target": other})
        assert changed_context.values["first"] == sc.Quantity(
            value * (0.25 if other == "q1" else 0.5), "arb"
        )


def test_lookup_reuse_does_not_hide_later_arithmetic_failure() -> None:
    row = sc.parameter_table(Calibration).lookup(name="q0")
    snapshot = sc.parameter_snapshot(
        "point",
        tables={Calibration: (Calibration(name="q0", amplitude=0.2, correction=0.5),)},
    )
    with pytest.raises(ValueError, match="parameter input 'bad'"):
        sc.parameter_inputs(good=row["amplitude"], bad=row["correction"] / 0).resolve(
            snapshot, {}
        )
