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
is not an API for mutable global calibration or operation-local candidate overrides.

Device adapters retain payload-size batching, physical lane addressing, device timing
constraints, instruction encoding, upload and execution receipts. Scheduling and waveform
planning already available in the quantum package remain framework responsibilities.
Hardware-specific constraints belong in explicit capabilities or encoding rules, rather
than copied general-purpose schedulers.

## Remaining framework work

This entry point centralizes existing orchestration; it does not implement automatic
parameter dependency tracking. Typed recipe row access, diagnostics, provenance and
candidate scopes still need a coherent framework design. In particular, distinguish:

- a working-point snapshot shared by the program;
- a candidate implementation applied to a whole program;
- a candidate applied only to an inserted operation, leaving reference gates unchanged.

Unit-bearing operation parameters use the core `QuantityType` contract through binding
and recipe resolution. Compatible linear units share a canonical call/implementation
identity. This avoids undocumented float units; it does not itself provide calibration
overlays or operation-local candidate scopes.

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
