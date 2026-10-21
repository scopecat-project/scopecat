from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest
from pydantic import ValidationError
from scopecat import Quantity
from scopecat.records.parameter import (
    ParameterCatalog,
    ParameterSnapshot,
    ScalarParameterValue,
)
from scopecat.records.parameter_revision import (
    ParameterRevision,
    parameter_revision_hash,
)
from scopecat.records.setup import SetupRevisionRef

from scopecat_quantum._ids import PulseEventId, PulseProgramId, QubitId
from scopecat_quantum.flux import (
    FluxCorrection,
    FluxCorrectionSnapshot,
    plan_flux_waveforms,
    render_flux_waveforms,
    resolve_flux_correction,
)
from scopecat_quantum.pulses import (
    Constant,
    FluxSignal,
    Play,
    PulseProgram,
    Sequence,
    schedule,
)

Q0, Q1 = FluxSignal(QubitId("q0")), FluxSignal(QubitId("q1"))
SETUP = SetupRevisionRef(revision_id="setup-1", content_hash="sha256:" + "1" * 64)


def correction() -> FluxCorrection:
    return FluxCorrection(
        input_reference_plane="desired-device-input-voltage",
        output_reference_plane="physical-dac-voltage",
        input_signals=("qubit:q0", "qubit:q1"),
        output_ids=("awg:z0", "awg:z1"),
        sample_rate_hz=1_000_000_000,
        correction_matrix=((1.0, 0.5), (-0.25, 1.0)),
        fir_taps=((1.0, -0.5), (0.0, 0.5, 0.25)),
        baseline_v=(0.1, -0.1),
        max_abs_voltage_v=(2.0, 2.0),
    )


def snapshot(value: FluxCorrection | None = None) -> FluxCorrectionSnapshot:
    value = value or correction()
    catalog = ParameterCatalog(id="flux-parameters")
    parameters = ParameterSnapshot(
        id="parameters-1",
        values=(
            ScalarParameterValue(id="flux_correction", value=value.model_dump_json()),
        ),
    )
    revision = ParameterRevision(
        id="revision-1",
        catalog=catalog,
        parameters=parameters,
        content_hash=parameter_revision_hash(catalog, parameters),
        actor="simulation",
    )
    return resolve_flux_correction(
        parameters=revision,
        parameter_id="flux_correction",
        setup_revision=SETUP,
        calibration_evidence_ids=("known-coefficients",),
    )


def program(amplitude: float = 0.4, unit: str = "V", phase: float = 0.0):
    return schedule(
        PulseProgram(
            PulseProgramId("flux-step"),
            Sequence(
                (
                    Play(
                        PulseEventId("first"),
                        Q0,
                        Constant(
                            Quantity(2, "ns"),
                            Quantity(amplitude, unit),
                            Quantity(phase, "rad"),
                        ),
                    ),
                    Play(
                        PulseEventId("adjacent"),
                        Q0,
                        Constant(Quantity(1, "ns"), Quantity(-amplitude, unit)),
                    ),
                )
            ),
        )
    )


def test_matrix_direction_fir_order_baseline_tail_and_repeat() -> None:
    frozen = snapshot()
    plan = plan_flux_waveforms(program(), snapshot=frozen, sample_rate_hz=1_000_000_000)
    rendered = render_flux_waveforms(plan)
    # Independent difference equations, no convolution/matmul in the reference.
    inputs = ((0.4, 0.4, -0.4, 0, 0, 0), (0, 0, 0, 0, 0, 0))
    expected: list[list[float]] = [[], []]
    c = frozen.correction
    for o in range(2):
        for n in range(6):
            expected[o].append(
                c.baseline_v[o]
                + sum(
                    c.fir_taps[o][k] * c.correction_matrix[o][i] * inputs[i][n - k]
                    for k in range(len(c.fir_taps[o]))
                    if n >= k
                    for i in range(2)
                )
            )
    np.testing.assert_allclose(rendered.buffers, expected, atol=1e-15)
    assert plan.sampled.sample_count == 6
    assert rendered.buffers[1][1] < c.baseline_v[1]  # idle authored q1 is activated
    assert tuple(b[-1] for b in rendered.buffers) == c.baseline_v
    assert all(not b.flags.writeable for b in rendered.buffers)
    np.testing.assert_array_equal(render_flux_waveforms(plan).buffers, rendered.buffers)
    restored = FluxCorrectionSnapshot.model_validate_json(frozen.model_dump_json())
    assert restored.fingerprint == frozen.fingerprint
    assert (
        render_flux_waveforms(replace(plan, snapshot=restored)).lane_peaks
        == rendered.lane_peaks
    )


@pytest.mark.parametrize(("unit", "phase"), [("arb", 0), ("V", 0.5)])
def test_unsupported_coordinates_rejected(unit: str, phase: float) -> None:
    with pytest.raises(ValueError, match="volts and zero phase"):
        plan_flux_waveforms(
            program(unit=unit, phase=phase),
            snapshot=snapshot(),
            sample_rate_hz=1_000_000_000,
        )


def test_rate_and_segment_rejection() -> None:
    frozen = snapshot()
    with pytest.raises(ValueError, match="sample rate"):
        plan_flux_waveforms(program(), snapshot=frozen, sample_rate_hz=500_000_000)
    plan = plan_flux_waveforms(program(), snapshot=frozen, sample_rate_hz=1_000_000_000)
    with pytest.raises(ValueError, match="matching planned record"):
        render_flux_waveforms(
            replace(plan, sampled=replace(plan.sampled, sample_count=2))
        )


@pytest.mark.parametrize("matrix", [((1.0,),), ((float("nan"), 0), (0, 1))])
def test_invalid_coefficients_rejected(matrix: tuple[tuple[float, ...], ...]) -> None:
    with pytest.raises(ValidationError):
        FluxCorrection.model_validate(
            {**correction().model_dump(), "correction_matrix": matrix}
        )


@pytest.mark.parametrize("gain", [10.0, 1e308])
def test_checks_apply_to_final_combined_output(gain: float) -> None:
    value = FluxCorrection.model_validate(
        {**correction().model_dump(), "correction_matrix": ((gain, 0), (0, gain))}
    )
    plan = plan_flux_waveforms(
        program(2.0), snapshot=snapshot(value), sample_rate_hz=1_000_000_000
    )
    with pytest.raises(ValueError, match="final flux waveform"):
        render_flux_waveforms(plan)


def test_two_input_matrix_is_not_inverted_and_filters_follow_mixing() -> None:
    from scopecat_quantum.pulses import Parallel

    frozen = snapshot()
    scheduled = schedule(
        PulseProgram(
            PulseProgramId("both"),
            Parallel(
                (
                    Play(
                        PulseEventId("z0"),
                        Q0,
                        Constant(Quantity(1, "ns"), Quantity(0.4, "V")),
                    ),
                    Play(
                        PulseEventId("z1"),
                        Q1,
                        Constant(Quantity(1, "ns"), Quantity(0.2, "V")),
                    ),
                )
            ),
        )
    )
    rendered = render_flux_waveforms(
        plan_flux_waveforms(
            scheduled,
            snapshot=frozen,
            sample_rate_hz=1_000_000_000,
        )
    )
    np.testing.assert_allclose(
        rendered.buffers,
        (
            (0.6, -0.15, 0.1, 0.1),
            (-0.1, -0.05, -0.075, -0.1),
        ),
        atol=1e-15,
    )


def test_off_grid_and_complex_envelope_rejection() -> None:
    from scopecat_quantum.pulses import FrequencyShift

    frozen = snapshot()
    for envelope in (
        Constant(Quantity(0.5, "ns"), Quantity(0.2, "V")),
        FrequencyShift(
            Constant(Quantity(2, "ns"), Quantity(0.2, "V")), Quantity(10, "MHz")
        ),
    ):
        scheduled = schedule(
            PulseProgram(
                PulseProgramId("invalid"), Play(PulseEventId("bad"), Q0, envelope)
            )
        )
        with pytest.raises(ValueError, match=r"strict sample grid|modulated envelopes"):
            plan_flux_waveforms(
                scheduled, snapshot=frozen, sample_rate_hz=1_000_000_000
            )


def test_qubit_and_coupler_with_same_name_have_distinct_axes() -> None:
    from scopecat_quantum._ids import CouplerId
    from scopecat_quantum.flux import flux_signal_id

    assert flux_signal_id(FluxSignal(CouplerId("q0"))) == "coupler:q0"
    assert flux_signal_id(Q0) == "qubit:q0"


@pytest.mark.parametrize(
    "fields",
    [
        {"implementation": "hardware"},
        {"iir_sos": ((1, 0, 0, 1, 0, 0),)},
        {"delta_unit": "Hz"},
    ],
)
def test_unsupported_filter_modes_and_units_are_not_silently_accepted(
    fields: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        FluxCorrection.model_validate({**correction().model_dump(), **fields})


def iir_correction(**changes: object) -> FluxCorrection:
    from scopecat_quantum.iir import SosIir

    iir = SosIir(sample_rate_hz=1_000_000_000, sections=((1, 0, 0, 1, -0.5, 0),))
    return FluxCorrection.model_validate(
        {
            **correction().model_dump(),
            "semantics_id": "scopecat.flux.static-iir-fir.v2",
            "baseline_v": (0, 0),
            "iir_filters": (iir, iir),
            "iir_tail": {"zero_input_samples": 40, "max_residual_v": 1e-10},
            **changes,
        }
    )


def test_iir_fir_full_chain_tail_reset_and_snapshot() -> None:
    from scopecat_quantum.iir import SosIir

    iir = SosIir(sample_rate_hz=1_000_000_000, sections=((0, 1, 0, 1, -0.3, 0),))
    c = iir_correction(iir_filters=(iir, iir))
    frozen = snapshot(c)
    plan = plan_flux_waveforms(
        program(), snapshot=frozen, sample_rate_hz=c.sample_rate_hz
    )
    rendered = render_flux_waveforms(plan)
    assert plan.sampled.sample_count == 3 + 40 + 3
    for o in range(2):
        raw = np.zeros(43)
        raw[:3] = np.array([0.4, 0.4, -0.4]) * c.correction_matrix[o][0]
        # Explicit independent first-order delay recurrence.
        filtered = np.zeros(43)
        for n in range(1, 43):
            filtered[n] = raw[n - 1] + 0.3 * filtered[n - 1]
        expected = np.convolve(filtered, c.fir_taps[o])
        expected = np.pad(expected, (0, 46 - len(expected)))
        np.testing.assert_allclose(rendered.buffers[o], expected, atol=1e-16)
        assert rendered.buffers[o][-1] == 0
    assert rendered.buffers[0][0] == 0  # causal delay is not silently canceled
    np.testing.assert_array_equal(render_flux_waveforms(plan).buffers, rendered.buffers)
    restored = FluxCorrectionSnapshot.model_validate_json(frozen.model_dump_json())
    assert restored.fingerprint == frozen.fingerprint
    assert snapshot(iir_correction()).fingerprint != frozen.fingerprint
    assert "iir_filters" not in correction().model_dump()
    assert "iir_tail" not in correction().model_dump()


def test_iir_with_optional_identity_fir_and_tail_rejection() -> None:
    c = iir_correction(fir_taps=((1,), (1,)))
    plan = plan_flux_waveforms(
        program(), snapshot=snapshot(c), sample_rate_hz=c.sample_rate_hz
    )
    assert render_flux_waveforms(plan).buffers[0][0] == 0.4
    c = iir_correction(iir_tail={"zero_input_samples": 0, "max_residual_v": 1e-15})
    plan = plan_flux_waveforms(
        program(), snapshot=snapshot(c), sample_rate_hz=c.sample_rate_hz
    )
    with pytest.raises(ValueError, match="tail exceeds"):
        render_flux_waveforms(plan)


def test_iir_tail_check_includes_fir_gain() -> None:
    c = iir_correction(
        fir_taps=((100,), (1,)),
        max_abs_voltage_v=(100, 100),
        iir_tail={"zero_input_samples": 0, "max_residual_v": 0.5},
    )
    plan = plan_flux_waveforms(
        program(), snapshot=snapshot(c), sample_rate_hz=c.sample_rate_hz
    )
    with pytest.raises(ValueError, match="tail exceeds"):
        render_flux_waveforms(plan)


def test_iir_combined_amplitude_and_nonfinite_rejected() -> None:
    c = iir_correction(max_abs_voltage_v=(0.1, 0.1))
    plan = plan_flux_waveforms(
        program(), snapshot=snapshot(c), sample_rate_hz=c.sample_rate_hz
    )
    with pytest.raises(ValueError, match="physical voltage limit"):
        render_flux_waveforms(plan)


@pytest.mark.parametrize(
    "changes",
    [
        {"semantics_id": "scopecat.flux.static-fir.v1"},
        {"iir_filters": ()},
        {"iir_tail": None},
        {"iir_tail": {"zero_input_samples": -1, "max_residual_v": 1e-6}},
        {"iir_tail": {"zero_input_samples": 1, "max_residual_v": 0}},
        {"baseline_v": (0.1, 0)},
    ],
)
def test_iir_contract_rejections(changes: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        iir_correction(**changes)


def test_iir_filter_sample_rate_rejected() -> None:
    from scopecat_quantum.iir import SosIir

    bad = SosIir(sample_rate_hz=1, sections=((1, 0, 0, 1, 0, 0),))
    with pytest.raises(ValidationError, match="matching sample rate"):
        iir_correction(iir_filters=(bad, bad))
