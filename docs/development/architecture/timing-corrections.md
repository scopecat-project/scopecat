# Explicit sampled timing corrections

Related design work: #612 and #1135. This is a bounded software contract, not
latency measurement, automatic calibration or a physical compensation sign rule.

`SignalTimeShift` and `TimingCorrectionSnapshot` in
`scopecat_quantum.timing_corrections` select shifts of logical drive, readout and
acquisition signals. Coordinates are `(role, owner_kind, owner_id)`; readout-line
and qubit owners remain distinct. A positive number of seconds moves an event
later in the common record. Missing signals mean zero. The immutable snapshot
contains a semantics ID, sample rate, finite decimal seconds and optional evidence
IDs; its content fingerprint includes those evidence IDs. Evidence does not imply
hardware qualification or supply an automatically selected active calibration.

The first contract supports strict integer-sample shifts in complete records.
Decimal ratios are checked exactly, independently of decimal arithmetic context.
Nonzero shifts require strict-grid authored boundaries and the matching sample
rate. Fractional samples, non-finite values, duplicate signal coordinates, windows,
reapplication and output/acquisition intervals outside the original record fail.
There is no implicit pre-roll, clipping, record extension or resampling. Authors
must explicitly reserve any needed leading/trailing space in their program.

`apply_timing_corrections` consumes the sampled plan and the same scheduled
program that produced it. Full-record plans retain their source occurrences;
window plans do not. Each occurrence is checked against its source and processed
in tuple order, so repeated authored event IDs are not collapsed into a dictionary
entry. Delays and frame operations retain their authored coordinates. Every
output/acquisition occurrence on a selected signal receives the same shift.

`RealizedEventTiming.requested_start_seconds` retains the authored coordinate.
`correction_samples`/`correction_seconds` record the control value separately;
`start_sample` and `realized_start_seconds` describe the corrected coordinate.
`start_error_seconds` subtracts the correction, so a deliberate shift does not
become a quantization error. Duration and quantization policy retain their existing
meaning. This is a translation of rendered sample values: the renderer evaluates
envelope and carrier at their original coordinates and writes the same values at
the shifted indices. Existing carrier-origin and logical-frame policies are reused;
no extra carrier rotation is inferred. Phase-template factoring includes the
translation coordinate in its structure comparison.

The reference `ListModeTarget.timing_correction` is explicit target configuration.
Its configured routes must cover every selected signal. Planning applies the
correction before physical output and demodulator-slot conflict checks and before
acquisition-window construction. Nonzero correction conservatively disallows
shared-output overlaps, including multiplexed readout; zero correction preserves
the prior overlap policy. Complete record size and existing event, memory and
sample budgets are checked on the retained corrected artifact. The snapshot enters
target configuration, device snapshot, placement/layout/cache identity and artifact
identity. Inspection exposes the snapshot/fingerprint and corrected/requested
coordinates; previews, AWG payloads and acquisition windows consume the same
artifact. Explicit zero snapshots preserve device values, but change provenance
identity compared with an absent snapshot.

Acquisition timing shifts window indices only. Existing integrated-IQ
normalization, integration weights and absolute-time demodulation reference are
unchanged. Shifting capture relative to a carrier can change the measured complex
phase; the sample-translation model does not claim invariant experimental results.

Nonzero timing correction combined with any flux/FIR correction snapshot is
explicitly rejected. Filter/translation order, cross-coupled outputs, FIR tails and
acquisition behavior require a separately tested contract. Zero correction may
coexist with the existing flux path without changing its samples or windows.
No automatic calibration experiment, GUI redesign, generic DSP graph, continuous
segment state, hardware timing implementation or nonzero DC execution is added.
Formal laboratory adapters must implement and qualify their own consumption;
reference integration and synthetic recording executors are not hardware acceptance.
