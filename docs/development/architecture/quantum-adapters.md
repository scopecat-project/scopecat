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
select candidate recipe inputs for gates wrapped in authored `recipe_scope` subtrees.
The exact implementation key includes the scope; measurements remain on baseline
and explicit pulse implementations retain their authored pulses. Missing names fail.

Device adapters retain payload-size batching, physical lane addressing, device timing
constraints, instruction encoding, upload and execution receipts. Scheduling and waveform
planning already available in the quantum package remain framework responsibilities.
Hardware-specific constraints belong in explicit capabilities or encoding rules, rather
than copied general-purpose schedulers.

## Pulse functions and parameter-source bindings

A recipe is an implementation rule for an operation, not a table schema. Prefer
`bind_gate_pulse_recipe` from `scopecat_quantum.recipe_bindings` for new gate
implementations. A pulse function receives Qubit operands, optional Coupler resources,
and keyword-only inputs. No row model, decorator or mirror calibration dataclass is
required. Operation arguments and calibration inputs remain separate:

```python
from scopecat import Quantity
from scopecat_quantum import authoring as q
from scopecat_quantum.recipe_bindings import bind_gate_pulse_recipe
from scopecat_quantum.pulse_recipes import PulseRecipeProfile
from scopecat_quantum.standard_gates import X90


def half_turn(target: q.Qubit, *, duration: Quantity, amplitude: Quantity):
    return q.play(q.drive(target), q.constant(duration=duration, amplitude=amplitude))


def resolve(parameters, call):
    # This binding owns source layout. It may join tables or compute inputs.
    calibration = parameters[call.qubits[0].value]
    return {"duration": calibration["duration"], "amplitude": calibration["amplitude"]}


profile = PulseRecipeProfile(
    bind_gate_pulse_recipe(of=X90, build=half_turn, inputs=resolve)
)
```

The constant envelope here only illustrates the interface; it is not a calibrated
physical X90. The framework cannot prove that a chosen waveform realizes a gate.

Bindings resolve only matching gate calls and their selected scopes. Repeated identical
calls resolve once per materialization. Missing gate arguments, input-name collisions
and resolver failures report the implementation, operands and scope. An omitted ID is
derived from function identity and gate ID; explicit IDs are available for catalog use.
Resources have a separate resolver and are passed as Coupler handles, not table rows.

Resolved calibration inputs are copied and fingerprinted by content before invoking the
pulse function. A later edit to the source does not change a prepared pulse program.
Cache entries do not retain every source object merely to memoize its identity. Keep
request snapshots fixed during a batch and keep resolvers/builders pure. Construct a new
profile/compiler after changing their code or function defaults; caches are never shared
across profiles. This is transient cache identity, not persisted code-version provenance.

Row maps remain a convenience for existing consumers. They are not the required adapter
boundary. `bind_measurement_pulse_recipe(kind=..., build=..., inputs=...)` provides the
same named-input boundary for readout. Its pulse function takes one `Qubit` and
keyword-only inputs; its resolver receives the effective baseline parameters and a
`Measure`. Only used objects and acquisition kinds resolve inputs. Gate candidate
scopes do not change readout calibration. The pulse body must provide one matching
acquisition contract and explicitly schedule its delay relative to the readout pulse.
Repeated measurements retain separate result addresses while reusing an implementation.
Integrated IQ is unclassified complex IQ, not raw ADC time samples; raw traces and
classified states have separate contracts. Typed snapshot reads
are available through `sc.parameter_rows`; a binding should select the required row
before consuming its required fields, so unrelated unknown calibration does not block it.

## Declarative calibration inputs

For keyed parameter tables, prefer a projection over a handwritten resolver:

```python
from scopecat_quantum.recipe_queries import (
    recipe_operand,
    recipe_operation,
    recipe_parameter_inputs,
)

inputs = recipe_parameter_inputs(
    sc.parameter_table(Rotation)
    .lookup(qubit=recipe_operand(), operation=recipe_operation())
    .select("width", "plateau", "amplitude", "beta", "detuning")
)
binding = bind_gate_pulse_recipe(of=X90, build=drag_rotation, inputs=inputs)
```

`Rotation` is the author's schema and `drag_rotation` their chosen pulse function.
The query is deferred until compilation selects a call and effective snapshot.
`recipe_operand(index)` supplies a logical-qubit entity; `recipe_operation()` supplies
the gate ID. Measurements support operand zero and literal keys, not a gate ID.
Units come from the selected field declarations. `.select(duration="width")` aliases
a source column without losing its unit. Complete primary keys and exactly one row
are required; missing, duplicate and unknown selected values report their source.
Unselected non-key values are neither materialized nor validated by the query;
normal workspace admission still validates stored tables.

The domain-independent query lives in `scopecat.authoring.parameter_queries`.
`projection.resolve(snapshot, context)` returns values plus snapshot ID, table,
resolved key and output-to-column mapping for inspection. This is transient evidence,
not automatic durable provenance. Current selection scans keys without building row
objects. It has no query optimizer or index cache yet.

Use `lookup["field"]` for a required field expression and `sc.parameter_inputs` to
combine tables or derive named inputs:

```python
gate = sc.parameter_table(CalibratedGate).lookup(qubit=recipe_operand())
shape = sc.parameter_table(Shape).lookup(name=gate["shape"])
inputs = recipe_parameter_inputs(
    sc.parameter_inputs(
        width=shape["width"],
        plateau=shape["flat"],
        amplitude=gate["scale"] * gate["correction"],
    )
)
```

A dependent key resolves against the same effective snapshot as its selected fields.
`parameter_inputs(...).resolve(snapshot, context)` returns per-output source records;
each source includes `key_sources` for fields that determined a dependent lookup's key.
The expression objects retain the declared arithmetic, while source records retain the
resolved values. These are inspectable in memory, not automatically stored with runs.
Each `resolve` owns a short-lived evaluation context. Fields with the same model and
normalized key reuse a selected row, and reused expression objects evaluate once.
Source evidence remains attached to each output and dependent key. This is not global
structural-expression deduplication or a full table index. Nothing is cached across
resolve calls, snapshot changes or operation contexts; equal snapshot IDs do not imply
equal contents. Quantity key matching retains the existing scientific comparison.

Arithmetic supports `+`, `-`, `*`, `/` using existing `Quantity` semantics: compatible
quantities add/subtract with unit conversion, numeric scaling preserves units, and
supported quantity ratios/products yield dimensionless values. Arbitrary compound-unit
algebra is not implemented. Invalid dimensions, unknown fields and division by zero fail
with the named output; there are no implicit defaults or writes to parameter tables.
Custom resolver callbacks remain available for computations outside this vocabulary.

`CompiledRecipeEntry.parameter_evidence` retains actual declarative resolutions through
materialization, independently of optional visual inspection. Each entry identifies its
recipe, implementation ID/fingerprint, selected scope and snapshot, resolved input values
and per-output source fields (including dependent lookup keys). Readout records baseline
scope. Pulse cache hits still receive the current resolution; evidence is not cached with
the reusable pulse body. Repeated operations with the same implementation key share one
evidence entry, rather than claiming a separate resolution for each shot or operation.

`RecipeParameterInputs` returns a mapping-compatible `ResolvedRecipeInputs`; ordinary
callback mappings and older row recipes have no automatically inferred source evidence.
An empty evidence tuple must not be interpreted as a complete dependency inventory.
`TypeAdapter(tuple[RecipeInputEvidence, ...])` supports current-format JSON round trips,
preserving quantities, entities and nested key sources. This is parameter evidence, not
code-version provenance or a designated persistent-data compatibility baseline.

Target adapters can attach evidence to the existing durable invocation intent using
`parameter_evidence_intent` from `scopecat_quantum.recipe_evidence_records`:

```python
target_intent = parameter_evidence_intent(
    [(point.ordinal, compiled)],
    target_intent={"realization": "iq"},
)
# Supply this target_intent when creating DomainInvocationSpec or closing the invocation.
```

Use the run's logical point ordinals, not chunk-local indices, and include only compiled
entries belonging to that invocation. The namespaced, current-format document is covered
by `DomainInvocationIntent.intent_fingerprint` and retained in the ordinary domain-job
transition ledger; no sidecar file or separate database schema is introduced. Duplicate
point/entry pairs and overwriting an existing attachment are rejected.

After restart, `read_parameter_evidence(invocation.intent)` reconstructs typed evidence
from `run.domain_jobs()` results (or an invocation transition from
`run.domain_job_transitions()`). It does not load author code or re-run queries. An absent
attachment is an explicit error, not an inferred empty dependency set.

Retention follows the target's `DomainTransitionPolicy`: use `write_ahead` to retain the
invocation before effects. `batched` admits a loss window; `abnormal_only` omits ordinary
synchronous successes and therefore cannot promise complete parameter evidence. A saved
invocation proves the compiled intent, not successful device execution or measured state.

The new private execution adapter must still attach these entries and verify that its
device preparation, pulse inputs and measurement records use the same effective point
parameters. The ledger integration alone does not establish that physical invariant.

## Circuit transformation contract

Current compilation binds, resolves implementations and lowers authored operations. It
does not automatically cancel gates, optimize circuits or remap physical objects. Recipe
selection is distinct from circuit decomposition and from device encoding.

Future transformation passes must distinguish mandatory legalization from explicitly
requested optimization/routing, preserve measurement identities and produce inspectable
before/after evidence. Explicit pulses, timing and candidate scopes must be preserved
unless a pass declares and checks a stronger contract. Ideal-unitary equivalence alone
cannot justify removing pulses in a calibration, echo or benchmarking experiment.
External circuit optimizers may be integrated at the logical circuit boundary; this
change does not add a pass manager or promise Qiskit-level optimization coverage.

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
parameter dependency tracking. Selected-input dependency diagnostics and durable binding provenance
remain open. Call-level `with_recipe_parameters` binds scanned cell values through
existing typed compiler inputs; `resolve_recipe_parameters` uses the core transient
context-update implementation to build snapshots and provenance. Bindings resolve
selected calibration inputs from those snapshots; adapters retain the returned evidence. The compiler now distinguishes:

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
