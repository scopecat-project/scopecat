# Quantum compiler and device adapter boundary

A laboratory adapter should declare recipes, parameter access and device capabilities.
It should not assemble an independent binding, recipe-resolution and lowering pipeline.

`RecipeTargetCompiler` in `scopecat_quantum.compilation` owns the shared sequence:

1. Bind one authored program to a point's inputs.
2. Resolve only recipes used by the verified program, with a batch-local cache.
3. Apply the same expansion budget during recipe materialization and lowering.
4. Produce a target-ready program and optional authored/logical/scheduled inspection.

Construct one compiler per batch with the selected recipe profile and immutable parameter
snapshot. Its result includes the bound program for domain-specific provenance and the
prepared target entry for encoding. New compiler instances own separate caches; this
does not mutate global calibration. Named immutable `scoped_parameters` snapshots
select candidate recipe rows for gates wrapped in authored `recipe_scope` subtrees.
The exact implementation key includes the scope; measurements remain on baseline
and explicit pulse implementations retain their authored pulses. Missing names fail.

Device adapters retain payload-size batching, physical lane addressing, device timing
constraints, instruction encoding, upload and execution receipts. Scheduling and waveform
planning already available in the quantum package remain framework responsibilities.
Hardware-specific constraints belong in explicit capabilities or encoding rules, rather
than copied general-purpose schedulers.

## Windows around calibrated operations

`authoring.flat_top_window(signal, body, amplitude=..., rise_duration=...,
fall_duration=..., settle_duration=...)` composes a cosine-flat-top control waveform
around a static body. The body may contain calibrated gates, explicit pulses and
measurement. Its duration is determined after recipe and measurement lowering,
so authors do not forward gate widths or sum readout and acquisition durations.

The body starts after the rise and settling intervals. The plateau lasts for the
settling interval plus the complete lowered body; the fall begins only after that
body ends. Both the readout pulse and acquisition contribute through the existing
scheduler. The body is lowered once, retaining acquisition identities and recipe
scopes. Overlapping use of the window signal is subject to normal resource validation.
Realtime conditional bodies are outside this initial static-window contract.

Measurement recipes must include acquisition start delays in their pulse body, rather
than shifting acquisition later during target encoding. Author `capture = acquire(...)`
and compose `sequence(delay(capture.signal, offset), capture)` alongside the readout
pulse. `delay` accepts acquisition signals and nonnegative durations, including a
scanned zero offset. The window then covers whichever ends later: readout or capture.

The public waveform has no device-specific zero tail. A target may require an explicit
zero-delta segment before combining a waveform with a resident baseline. That segment,
DAC block alignment, baseline addition and range checks remain adapter/driver policy.
A zero waveform delta means return to the selected baseline, not necessarily zero
physical output. These semantics do not establish physical equivalence between a
smooth edge and an older stepped implementation.

## Remaining framework work

This entry point centralizes existing orchestration; it does not implement automatic
parameter dependency tracking. Typed recipe row access and dependency diagnostics
remain open. Call-level `with_recipe_parameters` binds scanned cell values through
existing typed compiler inputs; `resolve_recipe_parameters` uses the core transient
context-update implementation to build snapshots and provenance. Adapters translate
those snapshots to laboratory recipe rows and retain the returned evidence. The compiler now distinguishes:

- a working-point snapshot shared by the program;
- a candidate implementation applied to a whole program;
- a candidate applied only to an inserted operation, leaving reference gates unchanged.

Unit-bearing operation parameters use the core `QuantityType` contract through binding
and recipe resolution. Compatible linear units share a canonical call/implementation
identity. This avoids undocumented float units; it does not itself provide calibration
overlays. Recipe scopes select complete snapshots separately from gate arguments.

## Native infrastructure direction

Keep Python as the authoring and recipe extension surface. A potential Rust boundary is
below Python recipe evaluation: verified, resolved pulse/control-flow IR enters a bounded
native computation, and scheduled programs, sampled buffers and structured diagnostics
come back. Avoid per-event Python callbacks across that boundary.

Scheduling, time-grid realization, waveform rendering and large IR traversal are candidates,
not an approved rewrite list. Before selecting one, measure representative scan and retained
control-flow workloads, define numerical/timing semantics and compare against the Python
reference. Device restrictions must remain explicit inputs; laboratory parameter tables and
vendor calls do not belong in a generic native kernel.

The current compiler entry is Python. It introduces neither a Rust runtime nor a new data
compatibility baseline. A native implementation must preserve scientific relationships,
resource/timing constraints and diagnostics rather than undocumented legacy byte patterns.
