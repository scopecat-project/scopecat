# Extend quantum workflows

`scopecat-quantum` supplies hardware-independent logical gates, mixed gate and
pulse programs, implementation binding, and the checked target-compiler
boundary. A lab integration owns its parameters, wiring, compiler inputs,
runtime, and response model.

Start with the [package guide](https://github.com/scopecat-project/scopecat/blob/main/packages/scopecat-quantum/README.md),
then inspect the reference lab's
[quantum compilation integration](https://github.com/scopecat-project/scopecat/tree/main/examples/reference_lab/src/reference_lab/quantum_compilation)
and
[list-mode target](https://github.com/scopecat-project/scopecat/tree/main/examples/reference_lab/src/reference_lab/targets/list_mode).

Logical point and product identity must not depend on physical batching. The
target may partition compilation, upload, shots, and acquisition inside its
declared capacity while retaining the authored measurement schema and lineage.
`prepare_batch` should retain point binding and pulse lowering shared by the
candidate, because core may later split it around host-state changes. Repeated
device-effective programs may share one scheduled representation; exact target
entry ids and result mappings remain request-local.

## Keep continuous intent and sampled realization separate

The canonical pulse scheduler retains requested boundaries as exact `Decimal`
seconds. `resolve_waveform_events(...)` applies logical `ShiftPhase` operations
and returns exact, continuous-time `ResolvedWaveformEvent` values before any
sample clock or output lane is chosen. It retains the authored envelope and the
accumulated `frame_phase_radians` without duplicating phase state;
`effective_phase_radians` derives their complete sum. A target using the latter
must not add the envelope's authored phase a second time. A target with native
envelopes, oscillators, or sequencer instructions can lower this layer directly
instead of first manufacturing a dense sampled buffer.

A sampled-output target should project events through one `SampleGrid` instead
of independently rounding durations or starts. Use
`realize_event_timings(...)` when the target keeps a device-specific waveform
renderer, `plan_sampled_waveforms(...)` for one complete scheduled program, or
`plan_sampled_waveform_window(...)` for one target-selected trigger or upload
window. These expose `RealizedEventTiming` with requested and realized start,
duration, sample counts, and signed timing errors.

A window receives the complete resolved-event context plus the selected event
instances. It uses the complete context for signal-first carrier references but
quantizes only the selection. This lets independent output timing domains choose
their own grids without an unrelated event producing a false strict-grid error;
acquisition domains can use `realize_event_timings(...)` independently. Selection
is by `ResolvedWaveformEvent` instance rather than authored event id because a
real-time repeat may place the same authored id at several absolute times. The
plan preserves those repeated occurrences:
`timings_for(...)` returns all of them, while the single-value `timing_for(...)`
reports an ambiguity instead of silently choosing one.

`SampledWaveformPlan.time_origin_seconds` records the absolute realized boundary
represented by local sample zero. Timing and carrier coordinates inside the plan
are relative to that origin, and `RenderedWaveforms` retains it beside the
buffers. Consequently rendering a window produces the same values as slicing
the corresponding interval from a full-program render, including sub-sample
continuous event origins and schedule-, signal-, or event-referenced carriers.

Choose the timing policy deliberately. `strict` rejects a boundary the selected
clock cannot express. `nearest` moves the instruction and its local envelope to
half-even rounded boundaries; it retains the quantization error and is suitable
only when that approximation is part of the reviewed target contract.
`continuous` instead selects the hardware sample locations inside each requested
half-open interval and evaluates the analytic envelope relative to its exact
requested origin. Smooth envelopes can therefore produce different device
samples for fractional-sample shifts even when their selected sample count is
unchanged. Constant or otherwise aliased waveforms may still collapse to the
same physical values. Device trigger ticks, transfer blocks, waveform padding,
and channel packing remain a later target-specific layer; they must not be
presented as the scientific time coordinate itself.

Boundary quantization and analytic-function evaluation are separate choices.
`SampleGrid.sample_location` defaults to `"midpoint"`; select `"left_edge"`
only when a device or established waveform contract evaluates each sample at
the beginning of its half-open interval. The selected convention and
continuous-time sampling mode are reflected in the waveform semantics id. Do
not emulate left-edge evaluation by shifting pulse starts or phases, because
that changes scheduling intent and obscures the experimentally relevant
half-sample offset.

Use an explicit analytic envelope whenever the target knows the pulse shape.
`CosineFlatTop` and `authoring.cosine_flat_top(...)` expose independent rise and
fall durations, with the remaining duration interpreted as the plateau. A
target should not replace a `Constant` envelope with a calibrated cosine shape
during lowering. Calibration may choose the envelope parameters, but the
resulting pulse IR should state the shape that will be rendered.

`DerivativeQuadrature` and `authoring.derivative_quadrature(...)` add
`i * beta * d(envelope) / dt` to either supported smooth base shape.
`FrequencyShift` and `authoring.frequency_shift(...)` then apply a pulse-local
phase ramp to the complete complex envelope. For signed offset `Δf`, the
rendered envelope is `E(t) exp(+i 2π Δf (t - t_ref))`; positive offset therefore
rotates the complex baseband phase from I toward +Q. The default `"center"`
reference uses `t_ref = duration / 2`, while `"start"` uses `t_ref = 0`.
Both references use pulse-local time and reset on every play, so neither
accumulates phase through an idle gap. A target must preserve that distinction
from a program-global oscillator or compiler detuning, and should either lower
the wrapper natively or sample it as part of the envelope.

`SampledOutputBinding.carrier_phase_reference` declares where one binding's
carrier phase is zero. `"schedule_origin"` preserves a carrier across the whole
program, `"signal_first_play"` preserves it relative to that signal's first
play, and `"event_origin"` restarts it at each play's exact continuous start.
The reference retains that pre-quantization time even when nearest boundary
quantization moves the realized play boundary. A midpoint sample therefore
still advances the carrier by half a sample from an on-grid event origin.
Signed intermediate frequency expresses the carrier direction. These choices
are independent of explicit `ShiftPhase` frame operations and of the
sample-location convention.

When different logical points produce identical final device codes and timing,
a target may retain a device-effective fingerprint that excludes point ordinals
and result mappings. Derive it from the target's final quantized representation,
not from authored quantities or program ids. This remains device-specific rather
than a portable waveform API. It makes physically indistinguishable scan points
inspectable without changing their logical identities or silently deduplicating
requested measurements.

## Cache opaque target setup by content

A target that uploads reusable device content may declare connection-owned
residency on its prepared execution:

```python
from scopecat.sdk.domain import (
    DomainResidencyAddress,
    DomainResidencyRequirement,
)

residency = DomainResidencyRequirement(
    address=DomainResidencyAddress(
        instrument_id="controller",
        slot_id="readout-program",
    ),
    content_fingerprint=program_fingerprint,
)

return preparation.build(
    ...,
    setup=setup,
    setup_residency_requirements=(residency,),
)
```

The fingerprint must cover device-effective setup content but exclude physical
batch ordinals, logical point identities, and result mappings. Scopecat runs
`setup` when the current slot differs, then retains that knowledge for the live
run connection. A later host effect on the same physical instrument
invalidates its opaque slots when it invokes a command or collects a result.
Ordinary state reconciliation changes only its declared interface members and
does not imply that unrelated device memory was erased; a device whose state
patch does erase loaded content must expose that fact through its driver/target
contract. Effects on an independently routed LO source likewise do not
invalidate a controller program. Targets may also declare explicit setup or
realtime residency invalidations.

Residency is not an instrument interface property, device readback, or durable
resume proof. Losing the connection loses the knowledge. The driver remains the
authority that performs an idempotent ensure and invalidates any lower-level
cache when its device session is reset. Use the shared
[connection-residency qualification](connection-residency.md) with a fake native
client to check setup reuse, reconnect and unknown-trigger behavior.

Residency also does not imply archival. Scopecat does not automatically publish
compiled programs, waveform payloads, or a run-level catalog of their
fingerprints. Many useful workloads intentionally create large or random
one-off sequences, and a cache identity is not evidence that users need the
content after the connection closes. When reproducibility requires a random
logical sequence, authors should make its seed or selected logical sequence an
experiment input and record that value with the results. A canonical pulse,
compiled artifact, or inspection is retained only through an explicit artifact
publication chosen by the workflow that will consume it.

## Choose transition evidence by recovery value

Prepared executions use write-ahead transition persistence by default. Their
invocation is durable before setup or realtime start, and their terminal
receipt is durable before result realization. Externally managed jobs for which
the latest submitted identity must survive an executor loss should retain this
mode.

A synchronous target may instead amortize transition transport and SQLite
transactions when losing the latest small progress window is inexpensive:

```python
return preparation.build(
    ...,
    transition_policy="batched",
)
```

Batched transitions retain their order and are appended in count-bounded atomic
groups; elapsed point time does not turn a slow hardware sweep back into one
storage transaction per point. A pending checkpoint always forces the
invocation and checkpoint to be durable before the target is resumed, and the
final pending group is flushed before instruments are released. A hard executor
loss can omit the latest unflushed group; a normally terminated run still
flushes its complete transition ledger. The run terminal records only a compact
summary and whether those details are complete, so a dense synchronous sweep
does not duplicate every target attempt into one large JSON object. This setting
changes evidence durability, not hardware ordering, target batching, or
ownership of an independently routed LO.

A synchronous target whose successful calls are cheap to repeat and already
represented by measurement coverage may omit its normal lifecycle ledger:

```python
return preparation.build(
    ...,
    transition_policy="abnormal_only",
)
```

This policy writes no invocation or terminal row for an ordinary synchronous
success. A negative receipt is retained together with its complete invocation;
an interruption without a receipt retains the invocation alone, and a completed
receipt is retained if host result realization then fails. If the runtime
does return a checkpoint, Scopecat promotes that execution to the complete
ledger before `resume` and later closes it with its terminal receipt. The compact
run evidence lists the policies it observed, so an empty current-job projection
is distinguishable from missing required details.

## Parallel alignment

`quantum.parallel(*branches, alignment="end")` ends static branches together.
The default `alignment="start"` keeps the existing common-start semantics.
End alignment is resolved after gate pulse implementations are known: the
scheduler translates every event in a shorter branch by the same amount.
Authored evolution delays, pulse durations, and internal phase-event ordering
remain unchanged. The structural preview labels end-aligned blocks explicitly.

Compose a sequence of parallel blocks to express synchronized layers; select
start or end alignment independently for each layer. This does not prescribe
hardware clock rounding or physical cable/ADC delays. Those remain target/lab
responsibilities. Retained `parallel_each` keeps its current start-aligned map
semantics; this addition applies to explicit static parallel composition.
