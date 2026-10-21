# Flux correction foundation

The native `scopecat_quantum.flux` model is the first software slice of
[#1135](https://github.com/scopecat-project/scopecat/issues/1135). The model and
mixed-record adapter consume explicitly selected known coefficients. They do not
measure or fit them, or establish hardware qualification. There is no Bosing dependency or API.

## Numerical contract

`FluxCorrection` names ordered logical input IDs (`qubit:<id>` or `coupler:<id>`)
and physical output IDs. `delta_unit="V"` and explicit `input_reference_plane` /
`output_reference_plane` fields declare the laboratory voltage coordinates.
`implementation="software"` rejects unimplemented hardware/double-filter modes. Matrix rows are physical outputs and columns are logical pulse
deltas; the dimensionless **correction** matrix is directly applied, never
implicitly inverted. This is neither a measured response matrix nor flux in
webers or frequency shifts. Calibration must establish that these coordinates
and this factorization describe the selected apparatus.

For each output `o`, the fixed chain is:

```text
mixed[o,n] = sum_i correction_matrix[o,i] * pulse_delta_v[i,n]
filtered[o,:] = IIR[o](mixed[o,:])  # identity in v1
output_v[o,n] = baseline_v[o] + sum_k fir_taps[o,k] * filtered[o,n-k]
```

Baseline is an independently selected physical DC voltage, outside the matrix
and filters. Baseline is not a logical bias to be compensated, and is not silently
fed through a zero-state filter. No clipping, automatic matrix inversion, gain
normalization, delay cancellation or assumed physical line model occurs.

The bounded v1 implementation accepts whole finite, strict-grid scheduled
flux records with real unmodulated Constant, Gaussian or CosineFlatTop
plays in `V`, zero phase, and flux delays. Unknown axes, off-grid boundaries,
other units, complex/frequency-shifted flux envelopes and unknown
configuration fields are rejected. The native flux planner rejects IQ/acquisition
instructions by default; its explicit `allow_mixed_record=True` projection is
used by the enclosing mixed-record target described below. Absent logical inputs
are zero deltas; all declared physical outputs remain reserved and materialized,
including outputs activated only by compensation or baseline.

Each plan fixes one integer sample rate and sampled-midpoint semantics. History
before record sample zero is zero **delta**, not zero physical voltage. FIR tap
`k` multiplies input `n-k`; leading zero taps explicitly encode causal delay.
The FIR receives the full summed record, so adjacent and repeated pulses share
history within it. For v1, the plan appends `max(len(taps))` samples: the complete
`len(taps)-1` transient plus one baseline guard sample. The final sample is
baseline. Different outputs can have different tails/delays; no event or
acquisition timing is silently shifted. Tail samples count against device
sample/memory/playback budgets.

Records/repetitions independently start with zero delta history. Segmented
windows, continuous carried state, streaming and noncausal filters are outside
v1. Do not split an existing plan and restart its filters. This contract does
not qualify physical settling between repeated triggers.

## Selection and provenance

Store the small `FluxCorrection.model_dump_json()` payload in a declared ordinary
string parameter. `resolve_flux_correction(parameters=<ParameterRevision>,
parameter_id=..., setup_revision=<SetupRevisionRef>,
calibration_evidence_ids=(...))` reads that exact accepted cell and returns an
immutable `FluxCorrectionSnapshot`. It retains the full coefficient content,
parameter cell identity, existing parameter/setup revision IDs and content
hashes, and optional calibration evidence links. The resolver never selects the
latest revision or changes active parameters.

The laboratory must use its existing calibration evidence/applicability policies
when adopting that revision. Evidence links here do not assert applicability,
freshness, independent validation or physical qualification. `CalibrationProfile`
remains a requirement/policy contract, not a coefficient container. No new
calibration database or global active-calibration state is introduced.

`plan_flux_waveforms` binds one snapshot into a `FluxWaveformPlan`.
`render_flux_waveforms` is the shared deterministic preview/execution transform
and checks finite samples and final physical voltage limits after summing,
mixing, IIR, FIR and baseline. Snapshot JSON round-trips retain its content identity.
Historical reconstruction needs the retained scheduled program, exact snapshot,
sampled semantics and renderer version; a fingerprint alone is not coefficient
storage.

## Retained list-mode integration

The reference laboratory's list-mode compiler has an explicit opt-in flux path
that can share a complete record with existing IQ/readout/acquisition work. A lab constructs
`ListModeTarget` with `flux_correction`, matching active `setup_revision` and
`parameter_revision`, ordered `flux_signals`, and ordered `flux_outputs`.
Output IDs use `instrument_id:channel_id`; complete endpoint component paths
also participate in the target configuration identity. Existing configured IQ
bootstrap targets are not silently changed into flux targets.

The reference path requires strict target timing, matching rate/revisions/axes,
disjoint IQ/flux outputs, enabled outputs with finite positive voltage scales,
and reviewed zero hardware-offset requirements through the existing host-state
policy. `OutputChannelPreparation.amplitude_v` is explicitly the reference
fixture's volts-per-normalized-sample scale. The software divides physical volts
by that scale, checks normalized final DAC amplitude/finiteness, then hands the
same retained buffers to preview and AWG program encoding. No hardware filter
mode is implemented; a real adapter must not apply a second correction.

The current reference device contract does not qualify idle baseline holding,
so this target rejects nonzero `baseline_v`. The native numerical model can
preview nonzero baselines without implying that a device can execute them.
Nonzero physical DC operation requires a later explicit idle/hold/offset and
settling contract, using the existing DC ramp/settle/readback primitives.

The compiler disables IQ phase-template factoring for batches containing flux
plays; IQ-only batches can still use the existing factoring path. Full tails
participate in budgets; all correction outputs participate in preparation,
resource footprint, placement routes and AWG programs. The resolved snapshot is
retained on the artifact and in inspection facts. Coefficients, provenance and
voltage scales participate in configuration and cache identities. Execution
consumes the artifact, never re-resolves a current parameter revision. Pure
output execution also handles empty acquisition arrays in result fingerprints.

### Mixed-record adapter contract

This extension requires no new line model: it composes two disjoint output
projections of the same canonical scheduled program and one strict sample grid.
`plan_sampled_waveforms(output_signals=...)` restricts rendering while retaining
all original boundaries, event timings, schedule origin and full record duration.
The native flux projection uses this same boundary; it does not reschedule a
filtered subprogram or restart filtering around each pulse.

The complete record is the common body length, even if the last flux play ends
earlier. The selected IIR/FIR tail plus baseline guard extends only its final
boundary. IQ buffers are zero-padded to that length; existing IQ carrier phases,
mixer calibration, readout envelopes, acquisition start/count, demodulation,
normalization, result addresses and shot counts are unchanged. No acquisition
window is moved into or lengthened by the FIR tail. A device may capture a longer
raw segment to accommodate the record, but its authored DSP windows remain exact.
This does not promise physically identical measured IQ in the presence of flux;
that is a physical experiment, not a compiler assumption.

The compiler appends corrected/scaled flux lanes to the existing IQ lane map,
retaining zero-duration IQ routes as coordinates without activating their empty
outputs. It checks the final combined buffers, padded sample/memory budgets and
the union of IQ resources, acquisition resources, existing IQ offset guards and
all compensation outputs. The existing disjoint IQ/flux route, reviewed zero
flux hardware offset, setup/parameter revision and clock/rate checks still apply.
An IQ-only entry in a mixed batch has no flux buffers or extra tail and does not
trigger the flux AWG. All flux-bearing entries retain the selected snapshot; no
coefficient is resolved again during preview or execution.

Known-coefficient tests compare a readout record before/after adding flux:
bitwise-equal IQ prefixes and zero tails, exact acquisition timings and result
addresses, preserved DRAG/signed-IF realization, compensation-only outputs,
snapshot/cache changes and repeated entries. Tests also decode the actual AWG
and digitizer command payloads and compare them with artifact preview buffers
and unchanged acquisition windows, using only the recording executor.

Evidence is numerical and simulated: independent difference equations, known
coefficients, strict-grid rejection, tail/limit checks, snapshot reconstruction,
preview buffer hashes and the existing recording instrument executor. No real
instrument was connected. This is a library and retained target integration;
automatic configuration/UI authoring and real adapter enablement remain future
work.

## Dependencies and later slices

| Direction | Dependency and next bounded slice |
| --- | --- |
| Physical channel delay / acquisition windows | Explicit per-route timing references and device idle/hold behavior; then align measured delays and windows without hiding FIR latency. |
| Readout calibration | Reuse acquisition contracts, existing binary classifiers and evidence; keep assignment matrices separate from voltage mixing. |
| IQ mixer | Reuse existing IQ matrix, affine baseline and host offset policies; do not reinterpret the flux matrix as IQ calibration. |
| Fine applicability [#783](https://github.com/scopecat-project/scopecat/issues/783) | Bind coefficients to routes, reference plane, rate, bias/operating region and setup revisions through existing evidence policies. |
| Independent candidates [#1026](https://github.com/scopecat-project/scopecat/issues/1026) | Import/fit a candidate, retain source data and residuals, independently validate, then explicitly adopt its parameter revision. |
| Drift [#1028](https://github.com/scopecat-project/scopecat/issues/1028) | Monitor validated evidence and applicability; propose revisions without automatically replacing a run's selected snapshot. |
| Richer filters | Ringing/high-pass inverse models, noncausal advance and dynamic MIMO require separate contracts; stable SOS computation is available below. |

These directions do not require artificial stacked PR dependencies. Measurement
algorithms and hardware qualification are separate work from this foundation.

## Stable SOS IIR (v2)

Select `semantics_id="scopecat.flux.static-iir-fir.v2"` explicitly to use
`iir_filters`, one `SosIir` per ordered physical output, and `iir_tail`.
V1 payloads retain their original serialization and meaning; IIR fields on v1
are rejected. The chain is static mixing → IIR → FIR → baseline. FIR is optional
through identity taps `(1.0,)` per output. V2 requires zero baseline, including
numerical preview. This capability consumes external parameters; it does not
measure, fit, choose calibration methods or run an adoption/calibration loop.

### Coefficients and precision

`SosIir(sample_rate_hz=..., sections=(...))` accepts 1–32 ordered sections. A row
is `(b0,b1,b2,a0,a1,a2)`, with dimensionless finite real coefficients, **a0 exactly
1**, and transfer function

```text
H(z) = product_j (b0_j + b1_j z^-1 + b2_j z^-2)
                / (1 + a1_j z^-1 + a2_j z^-2)
y[n] = b0*x[n] + b1*x[n-1] + b2*x[n-2] - a1*y[n-1] - a2*y[n-2]
```

Normalize externally supplied coefficients by dividing all six entries by a0
before constructing the model. Rows execute in the supplied order using float64
DF-II transposed arithmetic via [SciPy sosfilt](https://docs.scipy.org/doc/scipy/reference/generated/scipy.signal.sosfilt.html).
Coefficients and the correction/target must have the same positive integer sample
rate in Hz; there is no resampling. Section poles must be strictly inside the
unit circle. This is the stability boundary of this finite settling, zero-baseline
capability, not a prohibition on unit-circle poles for future explicitly scoped
high-pass/integrator inverse compensation. Large gain, numerical overflow or
uncertifiable settling is rejected. Stable does not mean physically admissible.
No gain or delay is automatically normalized: numerator leading zeros are causal
delay, and frequency-dependent phase remains part of the supplied filter.

### Single measured exponential → exact discrete inverse

`inverse_exponential_sos(amplitude=A, tau_seconds=tau, sample_rate_hz=fs)` uses
an already measured **relative** discrete step response, with pre-step baseline
0, final plateau 1, and sample index zero at the first response sample:

```text
s[n] = 1 + A*r^n, n >= 0; s[n] = 0, n < 0; r = exp(-1/(fs*tau))
h[n] = s[n] - s[n-1]
H_response(z) = (1+A - (r+A) z^-1) / (1-r z^-1)
H_inverse(z)  = (1-r z^-1) / (1+A - (r+A) z^-1)
SOS = (1/(1+A), -r/(1+A), 0, 1, -(r+A)/(1+A), 0)
```

A positive A decays from above; a negative A recovers from below. Negating A is
not the inverse. Tau is finite and positive in seconds; r must be representably
strictly between 0 and 1. A=-1 is singular; other nonminimum-phase responses whose
inverse pole has magnitude ≥1 are rejected as unsupported stable causal inverses.
The sample-index convention specifies a discrete model, not a claim of exact
continuous-time inversion between DAC samples. Align externally measured/fitted
samples to that convention before adoption. The normalized sign convention is
consistent with the [Zurich exponential response model](https://docs.zhinst.com/hdawg_user_manual/functional_description/specific/pre_compensation.html);
these are software coefficients, not copies of its hardware approximations.

The helper accepts **one** exponential. `1 + sum_j A_j*r_j^n` is a sum of measured
step deviations, not a cascade of single-exponential responses. It must be
converted/inverted as one transfer function externally and supplied as SOS.
Cascading helper outputs is appropriate only when the physical response itself
is known to be a cascade. No ringing or high-pass-specific parameter helper is
provided in this slice.

### State, cutoff and constraints

Every rendered whole shot/repetition starts at rest, including adjacent pulses'
shared history across the entire shot. No state persists between shots.
`SosIir.filter_chunk(samples, state=None)` is a separate numerical primitive:
None starts at rest; passing its returned `SosState` continues the same record.
State carries the exact filter fingerprint (including rate), two DF-II delays
per section, and finite-value checks. Empty chunks preserve state. It appends no
tail. Full-record and chunked filtering are bitwise equal for identical chunks
and coefficient order. Callers must carry state across continuous chunks;
starting a new state around each pulse changes the waveform. The list-mode
adapter still accepts complete records only; this API does not add streaming
playback or physical state continuity across triggers.

`IirTailPolicy(zero_input_samples=N, max_residual_v=epsilon)` explicitly reserves
N zero-input samples after the complete body. At that cutoff, the renderer
certifies a conservative bound on **every future** zero-input IIR sample,
multiplied by `sum(abs(fir_taps[o]))`, against epsilon for each output. It uses
the cascade state transition A and output row C, solving
`P - A.T @ P @ A = C.T @ C + I`; a positive certificate bounds infinite future
output energy by `state.T @ P @ state`, hence any sample by its square root.
The computed certificate is checked for positive definiteness and a positive
Lyapunov residual; ill-conditioned cases fail closed. State is scaled before
squaring to avoid underflow for tiny tails. This is a conservative floating-point
certificate, not an exact-arithmetic proof; a sufficient tail may still be
rejected when the bound is too loose or the system is numerically ill-conditioned.

Only after certification does the selected policy replace the remaining IIR tail
with zero, then drain the full FIR tail plus one exact zero guard. Thus the total
record extends by `N + max(len(fir_taps[o]))` samples. The deliberate finite-record
approximation relative to an untruncated IIR has absolute error bounded by epsilon
after FIR. Checking only the last sample would miss delayed or oscillatory tails.
A failed certificate/tolerance check is an error; increase N or revisit the
explicit tolerance/coefficients, subject to device resource budgets. No hidden
clipping, arbitrary automatic tail search, or event/acquisition timing adjustment
occurs. Epsilon is an approximation tolerance, not permission to exceed voltage
limits: rendered physical/DAC samples are checked independently. Physical idle,
settling qualification and repetition spacing remain the laboratory's job.

The existing list-mode adapter reuses this shared render/snapshot path. IIR tails
count against sample and memory budgets before rendering; compensation-only
outputs remain in preparation/resource footprints. IQ prefixes and acquisition
windows stay fixed, with IQ buffers padded at the end. Nonzero timing correction
combined with any flux/FIR/IIR snapshot remains explicitly rejected; identity
(zero) timing is accepted. This PR does not expand #1034 platform qualification.

### Example with externally supplied parameters

```python
from scopecat_quantum.flux import FluxCorrection, IirTailPolicy
from scopecat_quantum.iir import inverse_exponential_sos

fs = 1_000_000_000
inverse = inverse_exponential_sos(amplitude=0.15, tau_seconds=8e-9, sample_rate_hz=fs)
correction = FluxCorrection(
    semantics_id="scopecat.flux.static-iir-fir.v2",
    input_reference_plane="desired-device-input-voltage",
    output_reference_plane="physical-dac-voltage",
    input_signals=("qubit:q0",),
    output_ids=("awg:z0",),
    sample_rate_hz=fs,
    correction_matrix=((1.0,),),
    iir_filters=(inverse,),
    iir_tail=IirTailPolicy(zero_input_samples=256, max_residual_v=1e-9),
    fir_taps=((1.0,),),  # optional FIR: identity
    baseline_v=(0.0,),
    max_abs_voltage_v=(0.8,),
)
# Retain correction.model_dump_json() in the existing parameter revision,
# resolve_flux_correction(...), then plan_flux_waveforms/render_flux_waveforms.
# N=256 is an example, not a universal hardware settling recommendation.
```
