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
output_v[o,n] = baseline_v[o] + sum_k fir_taps[o,k] * mixed[o,n-k]
```

Baseline is an independently selected physical DC voltage, outside the matrix
and FIR. Baseline is not a logical bias to be compensated, and is not silently
fed through a zero-state filter. No clipping, automatic matrix inversion, gain
normalization, delay cancellation or assumed physical line model occurs.

The bounded v1 implementation accepts whole finite, strict-grid scheduled
flux records with real unmodulated Constant, Gaussian or CosineFlatTop
plays in `V`, zero phase, and flux delays. Unknown axes, off-grid boundaries,
other units, complex/frequency-shifted flux envelopes, IIR fields and unknown
configuration fields are rejected. The native flux planner rejects IQ/acquisition
instructions by default; its explicit `allow_mixed_record=True` projection is
used by the enclosing mixed-record target described below. Absent logical inputs
are zero deltas; all declared physical outputs remain reserved and materialized,
including outputs activated only by compensation or baseline.

Each plan fixes one integer sample rate and sampled-midpoint semantics. History
before record sample zero is zero **delta**, not zero physical voltage. FIR tap
`k` multiplies input `n-k`; leading zero taps explicitly encode causal delay.
The FIR receives the full summed record, so adjacent and repeated pulses share
history within it. The plan appends `max(len(taps))` samples: the complete
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
mixing, FIR and baseline. Snapshot JSON round-trips retain its content identity.
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
earlier. The existing FIR tail plus baseline guard extends only its final
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
| Richer filters | Establish a concrete physical need before adding IIR stability, state/segmentation, noncausal advance or dynamic MIMO contracts. |

These directions do not require artificial stacked PR dependencies. Measurement
algorithms and hardware qualification are separate work from this foundation.
