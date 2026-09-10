# Scopecat Quantum

Hardware-independent quantum building blocks for Scopecat.

The package provides logical gates, mixed gate-and-pulse programs,
implementation binding, and the checked target-compiler boundary. Laboratory
experiment definitions, parameters, wiring, artifacts, runtimes, and response
models remain in the integrating project.

The package root exports the `authoring` facade. Target integrations import
their contracts from the owning submodules so those boundaries stay explicit.

## Ordinary Python pulse recipes

See [Editing a pulse recipe](examples/README.md) for bounded arithmetic over
concrete quantities and scanned inputs, honest symbolic annotations, and a
device-free two-point waveform/timing preview.

## Reference benchmarking protocols

`scopecat_quantum.benchmarking` provides device-independent, versioned circuit
families and numerical analysis for randomized benchmarking and XEB. Sequence
identity is derived from a domain-separated `SequenceKey`, not arithmetic seed
offsets or Python's process-level random generator. A manifest therefore keeps
the protocol version, root seed, sample index, length policy, entity or pair
identity, exact Clifford choices, recovery, primitive decomposition, and stable
fingerprint needed for replay.

The reference surface includes uniform 1q Clifford RB, entity-keyed parallel 1q
RB, exact 1q interleaved RB, uniform 2q Clifford RB with an H/S/S†/CZ
decomposition, 1q and phased XEB ensembles, RB decay and interleaved-error
estimators, linear-XEB fidelity, and XEB cycle-decay fitting.
Independent-length sampling is the default; `shared_prefix` is an explicit
alternative.

An interleaved sequence reuses the exact random-Clifford backbone selected by
the corresponding reference sequence. Its `length` counts random Cliffords;
the executed circuit contains `length` random Cliffords, `length` copies of the
interleaved realization, and one recomputed recovery Clifford. The realization
is recorded as primitives so physically distinct implementations of the same
Clifford remain distinguishable:

```python
from scopecat_quantum.benchmarking import (
    single_qubit_interleaved_rb_sequence,
    single_qubit_rb_sequence,
)

reference = single_qubit_rb_sequence(17, 32, sample_index=3, member_id="q0")
interleaved = single_qubit_interleaved_rb_sequence(
    17,
    32,
    sample_index=3,
    member_id="q0",
    interleaved_primitives=("x90", "x90"),
)
assert interleaved.reference_fingerprint == reference.fingerprint
```

These tools stop at logical operations and numerical results. An integrating
project still owns experiment scans, selected qubits and pairs, readout and
classification, while a target owns gate-to-pulse lowering, scheduling, device
uploads, and acquisition. Large-circuit ideal XEB probabilities may therefore
come from a separately selected simulation backend without changing the
recorded protocol manifest.

```python
from typing import Annotated

import scopecat as sc
from scopecat import Quantity
from scopecat_quantum import authoring
from scopecat_quantum.gates import GateParameterKind

x90 = authoring.single_qubit_gate("x90")
beta_type = sc.ScalarType(sc.QuantityType(unit="ns"))


@authoring.implementation(of=x90, candidate="x90.drag")
def x90_drag(
    qubit: authoring.Qubit,
    beta: Annotated[Quantity, beta_type],
) -> authoring.QuantumFragment:
    return authoring.play(
        authoring.drive(qubit),
        authoring.drag(
            duration=Quantity(16, "ns"),
            amplitude=Quantity(0.2, "arb"),
            sigma=Quantity(4, "ns"),
            beta=beta,
        ),
    )


@authoring.program(id="x90-count")
def x90_count(
    qubit: authoring.Qubit,
    beta: Annotated[Quantity, beta_type],
    count: Annotated[int, GateParameterKind.INTEGER],
) -> authoring.QuantumFragment:
    return authoring.repeat(x90_drag(qubit, beta=beta), count)


call = x90_count(
    qubit="q0",
    beta=Quantity(0.75, "ns"),
    count=3,
).with_shots(32)
```

`authoring.drag(...)` is shorthand for a Gaussian envelope wrapped by a
derivative-quadrature correction. The correction is independent of the base
shape, so a calibrated half-cosine pulse can retain its shape while scanning
the same time-valued coefficient:

```python
corrected = authoring.derivative_quadrature(
    authoring.cosine_flat_top(
        duration=Quantity(24, "ns"),
        amplitude=Quantity(0.2, "arb"),
        rise_duration=Quantity(12, "ns"),
        fall_duration=Quantity(12, "ns"),
    ),
    beta=beta,
)
```

Pulse-local detuning is a separate outer composition. Its phase ramp resets for
each envelope and defaults to zero phase at the envelope center, so idle gaps do
not accumulate detuning phase:

```python
detuned = authoring.frequency_shift(
    corrected,
    offset=Quantity(-1.2, "MHz"),
)
```

Apply the derivative correction before the frequency shift. This makes the
phase ramp modulate the complete corrected complex envelope and keeps it
distinct from a target's program-global carrier-frequency setting.

The concrete `DRAG` envelope type has been replaced by that composition. Code
which constructed `DRAG(...)` directly should construct
`DerivativeQuadrature(Gaussian(...), beta=...)`; the stable
`authoring.drag(...)` convenience function continues to author the same
Gaussian derivative correction.

Program inputs may bind directly to Scopecat values such as
`scopecat.parameter_lookup(...)`. A `Program` call is a native domain
occurrence that owns its effect, execution options, and named result products.
Place it with `context.use(call)` inside either `@sc.module` or
`@sc.experiment`. A lab can own a fixed experiment that injects compiler inputs,
measurement compute, recording policy, and independent auxiliary-device work;
the program call remains one domain effect rather than becoming the whole
experiment. The
[reference lab runner](../../examples/reference_lab/README.md) shows that path.

Point-dependent Python elaboration uses an explicitly bounded program family:

```python
@authoring.fragment(
    id="bounded-x90-family",
    envelope=authoring.ProgramFamilyEnvelope(
        allowed_gates=(x90,),
        max_operations=2,
        max_depth=2,
    ),
)
def bounded_x90_family(
    qubit: authoring.Qubit,
    variant: Annotated[int, sc.IntType(minimum=0, maximum=1)],
) -> authoring.QuantumFragment:
    if variant == 0:
        return x90(qubit)
    return authoring.sequence(x90(qubit), x90(qubit))
```

The function is evaluated only after its point inputs bind. Its typed qubit and
coupler ports are the complete allowed element surface; the envelope adds the
complete semantic gate catalog and hard size bounds. Binding rejects a generated
body that escapes either surface. `max_operations` counts expanded executable
leaves, including finite repeats, while `max_depth` bounds the longest sequential
path and takes the longest branch of a parallel composition. The envelope is
therefore cheap to inspect before elaborating every point, while each selected
point is still checked exactly. `Program.draw()` and the authored inspection
layer show every family call's allowed gate IDs and size bounds without
evaluating the family function.

Bounded measurement feedback is authored explicitly and remains visible to the
target compiler:

```python
x = authoring.single_qubit_gate("x")


@authoring.program(id="active-reset")
def active_reset(qubit: authoring.Qubit) -> authoring.QuantumFragment:
    state = authoring.measure(
        qubit,
        result="state",
        contract=authoring.CLASSIFIED_STATE_RESULT,
    )
    return authoring.sequence(
        state,
        authoring.switch(state.result, {1: x(qubit)}),
    )
```

`switch` has a finite integer case set and an optional no-op default. Its
predicate must be a classified-state result produced earlier in the same
sequence. Branches are result-free, so acquisition addresses and result shapes
do not depend on the state observed at run time. Real-time control is rejected
inside `parallel` and `parallel_each`; parallel static work may still occur
before or after it.

A fixed feedback loop can produce one result slot with a local round axis:

```python
rounds = 4
qubit = authoring.qubit("q0")
round_contract = authoring.CLASSIFIED_STATE_RESULT.with_dimensions(
    authoring.QuantumResultDimension("round", "round", rounds)
)
state = authoring.measure(
    qubit,
    result="state",
    contract=round_contract,
)
feedback_rounds = authoring.repeat(
    authoring.sequence(
        state,
        authoring.switch(state.result, {1: x(qubit)}),
    ),
    rounds,
    result_dimension="round",
)
```

Every result in a result-producing repeat declares the named dimension with the
same fixed count, or the same bounded integer input. The loop is retained once
through binding and target preparation, while its target envelope reports
worst-case duration, operations, acquisitions, and the union of resources and
events. Ordinary result-free repeats keep the static path unless their body
contains real-time control. `Program.draw()` exposes switches and repeat result
dimensions alongside the pre-expansion program-family envelopes.

Scopecat proves that this control flow is finite and exposes variable-duration
branches without interpreting controller timing. The target compiler remains
responsible for accepting or rejecting predicate support, feedback latency,
branch-duration constraints, loop execution, and hardware resource limits. A
fully static program is the single-scheduled-block case of the same target
program contract.

Variable-size programs use a retained `QubitSet` port and `parallel_each`:

```python
@authoring.program(id="parallel-readout")
def parallel_readout(
    qubits: authoring.QubitSet,
) -> authoring.QuantumFragment:
    return authoring.parallel_each(
        qubits,
        lambda qubit: authoring.measure(qubit, result="iq_shots"),
    )


call = parallel_readout(
    authoring.select_qubits(
        3,
        connected=True,
        anchor="q1",
        connection_kind="nearest_neighbor",
    )
).with_shots(32)
```

The declaration and call stay constant as chip size or numbering changes.
Configuration binding resolves this intent deterministically against the
accepted topology and retains both the intent and selected entity table in the
bound plan. Explicit qubit sequences remain available when identity is itself
part of the experiment. The named `iq_shots` product natively owns
`(entity, shot)` axes, and target result mapping assembles the per-qubit
acquisition addresses into that one logical value. `call.entity_results()`
remains useful for fixed-arity programs that deliberately expose one named
product per concrete qubit.

Compiler-owned defaults can use the pure row maps in
`scopecat_quantum.pulse_recipes`. The complete supported example is the
[DRAG-beta workflow](../../examples/reference_lab/README.md), including parameter-axis
overlays, analysis, candidate acceptance, target lowering, and production use.

## Scaling model

Qubits, configured topology, parameter bindings, shots, and result shape
contribute to the physical workload without changing the authored meaning of a
program call. A domain compiler bounds candidate point batches, selects the
largest compatible prefix after inspecting resolved inputs, and returns a
`DomainBatchCandidate` that retains this lowering work while core closes exact
host-state subregions. The target runtime may further group qubits and chunk
shots or binary results inside its own contract.

Bound programs and pulse lowering plans retain the `parallel_each`
entity-set boundary and finite repeat nodes. Recipe matching streams concrete
logical leaves without building an expanded circuit; concrete pulse branches
and scheduled events are created only when a target entry is prepared.
Inspection reports both structural and expanded operation counts, the selected
entity count, and maximum parallel width, so a GUI can show the compact intent
before drilling into its concrete realization.

Physical batch size, shot partitioning, and target placement do not enter
logical point, program, product, or measurement-record identity. This lets an
integration tune compilation, upload, and acquisition capacity while retaining
the same measurement schema and durable lineage. Point-level aggregates belong
in the canonical measurement stream; large shot-level or trace payloads cross
the data boundary as typed binary partitions rather than control-plane records.

Large programs follow three deliberately separate shapes:

1. The authored and bound-logical IR retains `parallel_each` and `repeat` as
   parameterized nodes. One template is stored regardless of entity-set size.
2. Target lowering instantiates stable entity-qualified operations only where a
   concrete schedule or placement requires them. Reordering the selected
   entities therefore does not change an entity's operation or acquisition
   identity.
3. The target artifact owns physical batching, event/acquisition limits,
   waveform memory, result volume, and result-chunk budgets. Those capacities
   may change without changing the program or result schema.

Program inspection uses the same separation. A default preview returns bounded
pages for authored, logical, scheduled, and physical layers. Subsequent queries
return just one layer page and may filter by node kind, entity, resource, parent,
or text:

```python
from scopecat.inspection import CompiledProgramInspectionQuery

preview = lab.prepare(experiment).preview(
    point="last",
    inspection_query=CompiledProgramInspectionQuery(
        layer_id="scheduled",
        entity_id="q17",
        kind="play",
        limit=128,
    ),
)
```

Each layer reports its full structural node count, matching count, returned
count, and next offset. Node entity/resource/result references are separately
bounded while retaining their full counts. This lets a GUI present an overview,
drill into a Map or Repeat aggregate, filter a concrete schedule, and inspect
physical placement constraints without receiving the complete expanded tree.

The [scalability benchmarks](../../docs/development/scalability.md) cover
multi-run calibration, shot-heavy acquisition, structured traces, and dense
spectroscopy.
