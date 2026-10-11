from __future__ import annotations

import pytest

from reference_lab.targets.list_mode.iq_semantics import integrate_rectangular_iq


@pytest.mark.parametrize(
    ("trace", "start_sample", "sample_count", "sample_rate_hz", "if_hz", "expected"),
    (
        pytest.param(
            (10.0, 2.0, 4.0, 20.0), 1, 2, 8.0, 0.0, 3.0 + 0.0j, id="zero-if-average"
        ),
        pytest.param(
            (0.5,),
            0,
            1,
            4.0,
            1.0,
            0.7071067811865476 - 0.7071067811865475j,
            id="positive-if-negative-phase",
        ),
        pytest.param(
            (0.5,),
            0,
            1,
            4.0,
            -1.0,
            0.7071067811865476 + 0.7071067811865475j,
            id="negative-if-positive-phase",
        ),
        pytest.param(
            (99.0, 1.0, 1.0, 99.0),
            1,
            2,
            4.0,
            1.0,
            -1.4142135623730951 + 0.0j,
            id="offset-window-sample-centers",
        ),
    ),
)
def test_integrated_iq_semantics_match_literal_golden_vectors(
    trace: tuple[float, ...],
    start_sample: int,
    sample_count: int,
    sample_rate_hz: float,
    if_hz: float,
    expected: complex,
) -> None:
    """Fix convention values independently of either DSP implementation path."""

    actual = integrate_rectangular_iq(
        trace,
        start_sample=start_sample,
        sample_count=sample_count,
        sample_rate_hz=sample_rate_hz,
        demodulation_frequency_hz=if_hz,
    )

    assert actual == pytest.approx(expected, abs=1e-15)
