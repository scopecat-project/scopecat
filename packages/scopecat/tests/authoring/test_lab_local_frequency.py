from __future__ import annotations

from dataclasses import dataclass
from typing import assert_type, cast

import scopecat as sc
from scopecat.compiler.frontend.resolution import compile_invocation
from scopecat.compiler.relations.context import EvalContext
from scopecat.compiler.relations.evaluation import evaluate_scalar
from scopecat.kernel.value_data import Row
from scopecat.program.value_refs import internal_lower_scalar_value_ref


@dataclass(frozen=True, slots=True)
class _FixedIfSweep:
    lo_frequency: sc.ValueRef[sc.Quantity]
    rf_frequency: sc.ValueRef[sc.Quantity]


def _fixed_if_lo_sweep(
    experiment: sc.ExperimentContext,
    *,
    signed_if: sc.Quantity,
) -> _FixedIfSweep:
    """Lab-local policy for the uncommon experiment that scans its LO."""

    lo_frequency = experiment.scan(
        "lo_frequency",
        (4.9, 5.0, 5.1),
        unit="GHz",
    )
    rf_frequency = assert_type(
        lo_frequency + signed_if,
        sc.ValueRef[sc.Quantity],
    )
    experiment.alias(
        rf_frequency,
        record_id="rf_frequency",
        role="coordinate",
        metadata={
            "relation": "rf_frequency = lo_frequency + signed_if",
            "signed_if_hz": float(signed_if.to("Hz").value),
        },
    )
    return _FixedIfSweep(
        lo_frequency=lo_frequency,
        rf_frequency=rf_frequency,
    )


def _evaluate(
    value: sc.ValueRef[sc.Quantity],
    **point: sc.Quantity,
) -> sc.Quantity:
    result = evaluate_scalar(
        internal_lower_scalar_value_ref(value),
        EvalContext(point_row=cast("Row", point)),
    )
    assert isinstance(result, sc.Quantity)
    return result


def test_fixed_if_lo_scan_is_a_small_lab_local_authoring_policy() -> None:
    captured: _FixedIfSweep | None = None

    @sc.experiment(id="test.fixed-if-spectrum", kind="spectroscopy")
    def spectrum(experiment: sc.ExperimentContext) -> None:
        nonlocal captured
        captured = _fixed_if_lo_sweep(
            experiment,
            signed_if=sc.Quantity(-100, "MHz"),
        )

    logical = compile_invocation(spectrum.build()).program.program

    assert captured is not None
    assert _evaluate(
        captured.rf_frequency,
        lo_frequency=sc.Quantity(5.0, "GHz"),
    ) == sc.Quantity(4.9, "GHz")
    assert [record.id for record in logical.value_record_selections] == [
        "rf_frequency",
    ]
    assert logical.value_record_selections[0].role == "coordinate"
    assert logical.value_record_selections[0].metadata == {
        "relation": "rf_frequency = lo_frequency + signed_if",
        "signed_if_hz": -100_000_000.0,
    }
