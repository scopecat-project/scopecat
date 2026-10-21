from __future__ import annotations

import math

import numpy as np
import pytest
from numpy.typing import NDArray
from pydantic import ValidationError

from scopecat_quantum.iir import SosIir, SosSection, SosState, inverse_exponential_sos


def reference(
    sections: tuple[SosSection, ...], x: NDArray[np.float64]
) -> NDArray[np.float64]:
    """Independent direct-form I recurrence, not scipy/production DF-II."""
    y = list(x)
    for b0, b1, b2, _, a1, a2 in sections:
        u, y = y, []
        for n, value in enumerate(u):
            y.append(
                b0 * value
                + (b1 * u[n - 1] - a1 * y[n - 1] if n >= 1 else 0)
                + (b2 * u[n - 2] - a2 * y[n - 2] if n >= 2 else 0)
            )
    return np.asarray(y)


@pytest.mark.parametrize(
    "sections",
    [
        ((1, 0, 0, 1, 0, 0),),
        ((0.5, 0, 0, 1, -0.5, 0),),
        ((0, 1, 0, 1, -0.2, 0.1), (1, -0.4, 0.2, 1, 0.3, 0.1)),
    ],
)
def test_reference_chunking_empty_chunk_and_independent_shots(
    sections: tuple[SosSection, ...],
) -> None:
    iir = SosIir(sample_rate_hz=1000, sections=sections)
    x = np.random.default_rng(82).normal(size=120)
    full, _ = iir.filter_chunk(x)
    np.testing.assert_allclose(full, reference(sections, x), atol=2e-15)
    chunks: list[NDArray[np.float64]] = []
    state = None
    for chunk in (x[:1], x[1:3], x[3:3], x[3:77], x[77:]):
        y, state = iir.filter_chunk(chunk, state=state)
        chunks.append(y)
    np.testing.assert_array_equal(np.concatenate(chunks), full)
    np.testing.assert_array_equal(iir.filter_chunk(x)[0], full)
    restored = SosIir.model_validate_json(iir.model_dump_json())
    assert restored.fingerprint == iir.fingerprint
    np.testing.assert_array_equal(restored.filter_chunk(x)[0], full)


@pytest.mark.parametrize("amplitude", [-0.2, 0, 0.1, 2.0])
@pytest.mark.parametrize("sample_rate_hz", [1000, 2000])
def test_exponential_response_and_exact_inverse(
    amplitude: float, sample_rate_hz: int
) -> None:
    tau = 0.004
    iir = inverse_exponential_sos(
        amplitude=amplitude, tau_seconds=tau, sample_rate_hz=sample_rate_hz
    )
    n = np.arange(300)
    step = 1 + amplitude * np.exp(-n / (sample_rate_hz * tau))
    corrected, _ = iir.filter_chunk(step)
    np.testing.assert_allclose(corrected, 1, atol=2e-14)
    # Also apply inverse then measured response to a finite bipolar pulse.
    x = np.zeros(300)
    x[2:13], x[17:20] = 0.2, -0.1
    r = math.exp(-1 / (sample_rate_hz * tau))
    response = ((1 + amplitude, -(r + amplitude), 0, 1, -r, 0),)
    np.testing.assert_allclose(
        reference(response, iir.filter_chunk(x)[0]), x, atol=2e-15
    )
    assert iir.sections[0][0] == pytest.approx(1 / (1 + amplitude))


@pytest.mark.parametrize(
    "changes",
    [
        {"sample_rate_hz": 1.5},
        {"sample_rate_hz": True},
        {"sample_rate_hz": 0},
        {"sections": ()},
        {"sections": ((1, 0, 0, 2, 0, 0),)},
        {"sections": ((float("nan"), 0, 0, 1, 0, 0),)},
        {"sections": ((1, 0, 0, 1, -1, 0),)},
        {"sections": ((1, 0, 0, 1, 0, 1),)},
        {"sections": ((1, 0, 0, 1, -2, 0.5),)},
        {"sections": ((1, 0, 0, 1, 0, 0),) * 33},
    ],
)
def test_invalid_sos(changes: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        SosIir.model_validate(
            {"sample_rate_hz": 1000, "sections": ((1, 0, 0, 1, 0, 0),), **changes}
        )


@pytest.mark.parametrize(
    ("amplitude", "tau", "rate"),
    [
        (float("nan"), 1, 1000),
        (0.1, 0, 1000),
        (0.1, 1, 0),
        (0.1, float("inf"), 1000),
        (-1, 1, 1000),
        (-0.99, 0.001, 1000),
        (0.1, 1e30, 1000),
        (0.1, 1e-30, 1000),
    ],
)
def test_invalid_exponential(amplitude: float, tau: float, rate: int) -> None:
    with pytest.raises(ValueError, match=r"exponential|SOS"):
        inverse_exponential_sos(
            amplitude=amplitude, tau_seconds=tau, sample_rate_hz=rate
        )


def test_state_binding_and_invalid_samples() -> None:
    iir = SosIir(sample_rate_hz=1000, sections=((1, 0, 0, 1, -0.5, 0),))
    _, state = iir.filter_chunk(np.ones(3))
    other = SosIir(sample_rate_hz=2000, sections=iir.sections)
    for bad in (
        state,
        SosState(other.fingerprint, ((float("nan"), 0),)),
        SosState(other.fingerprint, ()),
    ):
        with pytest.raises(ValueError, match="state"):
            other.filter_chunk(np.ones(1), state=bad)
    for bad_samples in (np.array([1j]), np.array([np.nan]), np.ones((1, 2))):
        with pytest.raises(ValueError, match="samples"):
            iir.filter_chunk(bad_samples)
    explosive = SosIir(sample_rate_hz=1000, sections=((1e308, 0, 0, 1, 0, 0),))
    with pytest.raises(ValueError, match="nonfinite"):
        explosive.filter_chunk(np.array([2.0]))


def test_tail_certificate_bounds_late_echo_and_long_future() -> None:
    # A pure two-sample delay has a zero endpoint but a later nonzero output.
    delayed = SosIir(sample_rate_hz=1000, sections=((0, 0, 1, 1, 0, 0),))
    y, state = delayed.filter_chunk(np.ones(1))
    assert y[-1] == 0
    assert delayed.remaining_output_bound(state) >= 1
    assert (
        delayed.remaining_output_bound(
            delayed.filter_chunk(np.zeros(2), state=state)[1]
        )
        == 0
    )
    iir = SosIir(
        sample_rate_hz=1000,
        sections=((1, 0.3, 0, 1, -0.7, 0), (1, 0, 0, 1, 0.2, 0.3)),
    )
    _, state = iir.filter_chunk(np.array([0.5, -0.1, 0.2]))
    bound = iir.remaining_output_bound(state)
    future, _ = iir.filter_chunk(np.zeros(10000), state=state)
    assert np.max(np.abs(future)) <= bound
    assert np.linalg.norm(future) <= bound


def test_tail_bound_does_not_underflow_when_state_is_tiny() -> None:
    iir = SosIir(sample_rate_hz=1000, sections=((1, 0, 0, 1, -0.5, 0),))
    _, state = iir.filter_chunk(np.array([1e-200]))
    future, _ = iir.filter_chunk(np.zeros(10), state=state)
    assert iir.remaining_output_bound(state) >= max(abs(future)) > 1e-250
