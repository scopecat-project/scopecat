# Experiment authoring dataflow

This page records the evolving authoring contract for design review and
advanced use. It is not prerequisite reading for the
[first-run workflow](../getting-started/quickstart.md). Prefer simplifying the
authoring model when a common workflow needs implementation vocabulary from this
page merely to succeed.

Experiment authors describe one dataflow. They do not choose a compute engine,
hidden execution phase, or storage representation. Scopecat places each
operation at the earliest stage where all of its inputs are available, and the
value returned by the experiment defines the ordinary durable result.

## Think in availability and shape

Two independent properties explain the authoring model:

| Property | Question | Examples |
| --- | --- | --- |
| Availability | When does the value exist? | input, parameter, scan point, acquired measurement |
| Shape | What exists at one point? | scalar, fixed array, ragged array |

`ScalarType` therefore means “one value at one logical point.” It does not mean
“host-only,” “small,” or “not recordable.” `ArrayType` means that one logical
point carries an array with declared dtype, unit, and local dimensions. Both can
flow through `compute(...)` and both can become durable experiment outputs.

`compute(...)` uses its inputs to choose execution placement:

- literals, runtime inputs, parameters, and scan coordinates produce a symbolic
  value and run in the normal host value graph;
- acquired or domain-produced measurements produce a logical product and run
  point-locally after those measurements arrive;
- a call that mixes the two runs after the measurements arrive and receives the
  earlier values joined by logical point.

The return type follows availability, not shape. Authors normally receive a
`ValueRef` before measurement and a `ProductRef` after measurement, but they do
not construct either type themselves. A measured array is still a
`ProductRef`, while a precomputed array is still a `ValueRef`.

Host-side Python measurement-dependent feedback cannot run earlier in the same
invocation. End the run, analyze its durable result, and start another ordinary
run explicitly when a new instrument state or compiled program depends on that
result. Procedures own that state across multiple runs. A bounded real-time
conditional inside one domain target is different: the target compiler receives
the closed branch/loop IR, its resource union and worst-case work are known
before execution, and no acquired value escapes to host Python before the
branch. Measurement-dependent extensions that add new design points inside an
executing run use the explicit adaptive-domain abstraction instead.

## Return the result you mean to keep

Returning a value, product, product bundle, dataclass, tuple, or `PerEntity`
tree from `@experiment` selects its recordable leaves automatically:

```python
from dataclasses import dataclass

import scopecat as sc
from scopecat_instruments import NetworkSweepProducts, network_sweep


@dataclass(frozen=True, slots=True)
class Spectrum:
    bias: sc.CoordinateRef[sc.Quantity]
    trace: NetworkSweepProducts


@sc.experiment
def spectrum(experiment: sc.ExperimentContext) -> Spectrum:
    bias = experiment.scan(
        "bias",
        start=sc.Quantity(-0.2, "V"),
        stop=sc.Quantity(0.2, "V"),
        points=21,
    )
    vna = network_sweep(experiment)
    return Spectrum(bias=bias, trace=vna.sweep())
```

The same logical handles select variables from the completed dataset:

```python
result = run.result(spectrum().output)
trace = result.dataset.traces(result.output.trace.s_parameter)
```

For row-oriented fitting, load the complete returned schema once. The typed
result validates every leaf up front, and each point preserves the Python type
carried by its symbolic reference:

```python
observations = result.rows(
    lambda point: Observation(
        bias=point.value(result.output.bias),
        response=point.value(result.output.trace.response),
    )
)
```

Typed point access is strict: `point.value(...)` raises when that field is
unavailable. Filter deliberately before fitting when incomplete points are valid
input data:

```python
usable = result.where_available(
    result.output.bias,
    result.output.trace.response,
)
observations = usable.rows(build_observation)
```

When rejected points are evidence rather than noise, partition once and inspect
the existing typed reason vocabulary:

```python
usable, rejected = result.partition_available(result.output.trace.response)
for point in rejected:
    match point.availability(result.output.trace.response):
        case "missing":
            ...
        case "invalid":
            ...
        case "overload":
            ...
```

`point.is_available(ref)` handles the simple boolean branch. Persisted result
paths returned by `run.result()` expose the same methods, so diagnostic code
does not need to reach into raw measurement records or parse exception text.
Use `point.unavailable(ref)` when diagnostic metadata or the unavailable value's
declared dtype, unit, and shape are also relevant.

With no arguments, `where_available()` requires every returned leaf. The
historical `run.result()` view offers the same operation with persisted paths.
Point-local compute uses the complementary rule: if any measured input is
unavailable, Scopecat does not call the kernel and propagates its reason and
metadata to every derived output. Filtering is therefore an explicit analysis
choice rather than a hidden acquisition policy.

This replaces independently loading columns, rejecting unavailable values, and
zipping them by position. Use `point.quantity(ref, "mV")` when a numeric leaf
should be converted to a requested unit.

Every new dataset also persists the experiment result contract itself. Historical
code can therefore inspect return paths without rebuilding the invocation that
created the run:

```python
result = run.result()
print(result.contract.id, result.contract.version, result.paths)
value = result[0].value("probabilities/probability_1")
```

`run.result(authored_output)` is the statically typed path when the originating
symbolic output is available. `run.result()` uses only the persisted contract and
is the durable historical interpretation boundary. Both expose their underlying
`dataset` for labeled slicing and ecosystem export; call `run.measurements()`
directly when the task starts from dataset variables rather than the experiment's
returned result.

There is no parallel `*Records` dataclass to declare or maintain. Product
bundles keep their author-facing field names, and returned nested values receive
hierarchical record names. These paths are the durable names: they do not change
when an internal product or compute operation is renamed. Repeating one source
at two return paths creates two aliases over the same product use. Homogeneous
`PerEntity` product leaves are stacked into one entity-indexed variable at their
shared return path; product bundles are transposed field-by-field.
`invocation.output` retains the authored `PerEntity` tree. Use
`invocation.result_ref(path)` for a durable result handle, or
`invocation.entity_result_ref(path)` when the path is known to be an
entity-indexed array. The durable entity index and ordered product provenance
describe that stored shape. Heterogeneous `PerEntity` trees retain explicit
entity-kind and entity-id path segments.

The return tree is the ordinary durability and liveness boundary. Put explicit
names, namespaces, roles, and metadata beside returned fields rather than
adding an imperative selection call:

```python
from typing import Annotated


@dataclass(frozen=True, slots=True)
class Spectrum:
    bias: sc.CoordinateRef[sc.Quantity]
    trace: Annotated[
        NetworkSweepProducts,
        sc.Result(namespace="science", metadata={"reviewed": True}),
    ]
```

`Result(id=...)` names one leaf; `namespace`, `role`, and `metadata` can annotate
a complete nested subtree. Returning an already-recorded leaf does not select
it twice. `experiment.alias(...)` remains only for the uncommon case where one
source needs an additional dynamic durable destination; return the resulting
`RecordRef` when that alias is also part of the result contract.

## Keep the common input path short

Function parameters separate values that vary per invocation from Python values
that change the experiment structure:

```python
from typing import Annotated


@sc.experiment
def repeated(
    experiment: sc.ExperimentContext,
    shots: sc.Input[int] = 100,
    detuning: Annotated[
        sc.Input[sc.Quantity],
        sc.QuantityType(unit="MHz"),
    ] = sc.Quantity(0, "MHz"),
) -> object: ...
```

Plain scalar Python types infer their value schema. Use `Annotated` only when a
unit, bound, entity kind, payload schema, array shape, or table schema matters.
Persistent lab configuration remains a parameter rather than an input because
it has different ownership, review, and history semantics.

## Choose and edit the point domain

For the normal Cartesian point domain, each `experiment.scan(...)` call creates
one typed coordinate, appends its axis in declaration order, and returns the
symbolic value used by the experiment body:

```python
bias = experiment.scan("bias", (-0.2, 0.0, 0.2), unit="V")
power = experiment.scan(
    "source_power",
    start=sc.Quantity(-30, "dBm"),
    stop=sc.Quantity(-10, "dBm"),
    points=41,
)
```

Independent axes form a Cartesian product. Generated axes include both
endpoints. They may instead use `center` and `span`, where `span` is the full
coordinate width. Coordinate values remain in their declared unit, including
logarithmic units such as dBm. Treat that declaration as the public scientific
unit of the axis. A physical target may convert it to samples, ticks, or another
transport unit during binding without changing the recorded coordinate unit.

Use separate `coordinate`, `axis`, and `grid` objects when an invocation must
edit the plan or axes are shared externally:

```python
power = sc.coordinate("source_power", sc.QuantityType(unit="dBm"))
experiment.grid(
    sc.axis(bias, (-0.2, 0.0, 0.2), unit="V"),
    sc.axis(power, (-30.0, -25.0, -20.0), unit="dBm"),
    repeat=4,
    repeat_mode="point",
    traversal="snake",
)
```

`repeat_mode="point"` measures all repeats of one base point before advancing;
`"sweep"` repeats the complete sweep. Counts above one add a typed `repeat`
coordinate. Snake traversal reduces retracing but changes only physical
execution order: the acquisition log retains that order, while logical point
identity and completed dataset reads remain canonical.

When several points form one comparison, declare that relation by naming the
coordinates allowed to vary inside the group. For example, this keeps the two
prepared states for each delay together when practical and repeats both states
if that comparison is interrupted:

```python
delay = experiment.scan("delay", (0, 100, 200), unit="ns")
prepared_state = experiment.scan("prepared_state", (False, True))
experiment.group_points(
    "prepared-state-comparison",
    varying=(prepared_state,),
)
```

Every coordinate not listed in `varying` forms the group key. Groups may have
different sizes for explicit point rows. Grouping is a scheduling preference
and recovery boundary, not a hardware batch requirement: target capacity or a
host-side state change may split a group. Each acquired record is appended
durably in physical order, but the group becomes resumably complete and enters
the logical dataset projection only after every member succeeds. Truly
indivisible real-time sequences belong inside one point as target-owned shots,
rounds, or feedback instead.

Every resolved recovery group has a unique stable id. The complete ordered
membership has a separate schedule fingerprint, so resumption rejects a group
ledger produced by a differently compiled point plan. Durable group completion,
logical coverage, and the physical acquisition log are separate facts. A
completion proof for a measurement group is accepted only after every
correlated record hash is present in that log, and it pins the exact acquired
rows selected by the logical dataset. A response loss can therefore leave an
orphan acquisition without advancing recovery state; the next executor
remeasures the complete group while preserving the orphan for diagnostics.
Runs without a dataset use the same sparse group ledger without record hashes.
On successful seal, points not governed by a static recovery group (for example
adaptive points) select their latest acquisition, and the dataset identity is
calculated in logical `point_index` order.

Use explicit rows when coordinates are correlated, sparse, duplicated, or do
not form a rectangular product:

```python
experiment.points(
    (
        {bias: sc.Quantity(-0.20, "V"), power: sc.Quantity(-30, "dBm")},
        {bias: sc.Quantity(-0.05, "V"), power: sc.Quantity(-24, "dBm")},
        {bias: sc.Quantity(-0.05, "V"), power: sc.Quantity(-24, "dBm")},
        {bias: sc.Quantity(0.18, "V"), power: sc.Quantity(-17, "dBm")},
    )
)
```

Every row has the same typed coordinate columns; row order and duplicates are
preserved. For an empty point cloud, pass its columns explicitly with
`experiment.points((), coordinates=(bias, power))`. A definition chooses either
grid axes or explicit rows because they carry different domain semantics.

Invocation edits are immutable and orthogonal:

```python
edited = (
    spectroscopy()
    .bind(sample="q0")
    .with_axis(sc.axis(power, (-35.0, -30.0, -25.0), unit="dBm"))
    .without_axis(bias)
    .with_repeat(3, mode="sweep")
    .with_traversal("snake")
    .with_point_grouping(
        "power-comparison",
        varying=(power,),
    )
)
definition_default = edited.reset_points()
```

`.grid(...)` and `.points(...)` replace the complete domain while retaining
repeat and grouping policy. Grid replacement retains traversal; explicit rows
restore forward traversal. `.with_axis(...)` replaces an axis in place or
appends one, and `.without_axis(...)` applies only to a grid. A retained grouping
must still reference coordinates in the replacement domain; use
`.without_point_grouping()` to clear it. `reset_points()` discards all invocation
point-plan edits.

Ordinary point plans are materialized before execution. A measurement-dependent
run opts into the separate adaptive-plan abstraction explicitly:

```python
class Optimizer:
    id = "example.optimizer"

    def propose(self, context: sc.DomainOptimizerContext):
        region = context.region
        assert region is not None
        if region.completed_point_count >= 20:
            return sc.RegionOptimizationComplete("enough evidence here")
        return sc.DomainProposalAttempt(
            sc.ResolvedDomainFragment.grid(
                sc.ResolvedDomainAxis.values_axis(
                    "frequency",
                    (choose_frequency(context.observations),),
                )
            ),
            region_ids=(region.id,),
            based_on_region_revisions={region.id: region.revision},
        )


adaptive = spectroscopy().adaptive(Optimizer(), max_points=32)
```

The authored points form the initial prefix. By default every coordinate is
adaptive. Passing `axes=(...)` instead treats the other static coordinates as
an outer scan and creates one stable adaptive region for each outer-coordinate
combination. The default `scope="per_region"` gives the optimizer independent
observations, revisions, stop state, and an optional per-region budget;
`scope="global"` gives it all regions in one context.

An optimizer proposes a compact compatible fragment: explicit values, a
range, an around-center range, or a point cloud. The fragment stays compact
while the runner checks its coordinate shape, selected-region revisions, and
point budgets, then expands it into complete logical rows and accepts or rejects
the group atomically. Acceptance does not compile every target artifact.
Accepted members receive consecutive logical point ordinals, and target
compilation consumes them lazily in bounded batches inside the same hardware
session, just like the authored static prefix. Freshness is checked against only
the selected region revisions, so progress elsewhere does not invalidate the
proposal. A one-row fragment is the degenerate point case.

The live Run view uses the same model for manual extension. An operator can add
a snapped or free scan to the current, selected, or all admitted regions. The
view reports the group decision and completion status; it does not render the
fragment's waveforms as admission feedback. Use the explicit selected-point
preview below when compiled waveform details matter. The total point limit,
optional per-region limit, and finite proposal retry budget keep the run
bounded. Durable multi-run adaptation remains a workflow concern rather than
hidden mutation of an ordinary static plan.

## Compute scalar and array data uniformly

A host calculation can return either shape and feed another compute:

```python
samples = experiment.compute(
    fn=make_window,
    output_type=sc.ArrayType(
        dtype="float64",
        dimensions=(sc.ArrayDimension("sample", 128),),
        unit="ratio",
    ),
    length=length,
)


def peak_value(*, values) -> float:
    return float(values.max())


peak = experiment.compute(
    fn=peak_value,
    values=samples,
)
```

When a named function returns `bool`, `int`, `float`, `complex`, or `str`, `compute`
infers the scalar output contract. Use an `Annotated` return with
`ScalarType(...)` or `ArrayType(...)` when units, bounds, dtype, or dimensions
matter. The annotation may be a reusable PEP 695 type alias, including payload
schemas used by hardware operations, so call sites do not repeat
`output_type=PayloadType(...)`. Inputs can be passed as named keywords;
`inputs={...}` remains useful when names are assembled dynamically.

A coherent point-local response can be a complex scalar without a synthetic
one-element array or aggregate dimension:

```python
from typing import Annotated
import numpy as np
import scopecat as sc


def iq_mean(
    values: object,
) -> Annotated[complex, sc.ScalarType(sc.ComplexType(unit="V"))]:
    return complex(np.mean(np.asarray(values, dtype=np.complex128)))


mean = experiment.compute("iq_mean", fn=iq_mean, inputs={"values": iq_shots})
```

`ComplexType` requires finite real and imaginary components. Its optional unit
applies to both components, matching the existing `complex128` measurement scalar.
`experiment.convert` scales both components for compatible linear units. Nonlinear
or offset conversions are rejected; `QuantityType` continues to describe real
quantities. Complex values have no ordering bounds and cannot be used as table
primary keys or configuration/instrument-property scalar types. Use an explicit
compute kernel for complex arithmetic; unsupported symbolic arithmetic and a
complex result declared as `FloatType` raise normal type/compute errors.

Complex literals remain native values in the frozen authoring and compute graph.
Durable run intent describes them with an explicit `kind="complex"`, `real`, and
`imag` record. Loading that description preserves content identity; it does not
add an automatic execution-reconstruction mechanism. Arbitrary JSON metadata
remains JSON-only. Recording and Arrow use the existing complex scalar wire
representation, and scalar grids offer real, imaginary, magnitude, and phase
views of that single stored value.

The reference lab's `coherent_ramsey` experiment exercises this path with its
existing virtual IQ acquisition and a delay/phase grid. Its shared acceptance
fixture is generated from actual storage and HTTP responses.

The same annotations on function parameters are checked against every bound
`ValueRef` or `ProductRef` during authoring. Measurement units must match
exactly because native kernels receive numbers in the product's declared unit;
compatible-but-different units are not silently converted. Array contracts
also compare dtype, value unit, and each local dimension's ID, kind, unit, and
size. Leaving a parameter unannotated opts out when a generic kernel genuinely
accepts several schemas.

Symbolic quantity arithmetic may remove units when the result has an
unambiguous scalar representation. Multiplying frequency by time produces a
plain floating-point cycle count, while dividing two quantities in the same
linear dimension produces a normalized floating-point ratio. For example, a
Ramsey phase keeps the `2π` convention explicit:

```python
import math


delay = experiment.scan(
    "delay",
    (sc.Quantity(20, "ns"), sc.Quantity(40, "ns")),
)
detuning = sc.parameter("detuning", sc.QuantityType(unit="MHz"))
cycles = detuning * delay
phase = cycles * sc.Quantity(math.tau, "rad")
```

Here `cycles` is a `ValueRef[float]` and `phase` is a
`ValueRef[Quantity]` in radians. `Hz` retains its ordinary cycles-per-second
meaning; Scopecat never inserts `2π` implicitly. Operations whose result would
require an unregistered composite unit, such as voltage multiplied by time,
remain authoring errors. Logarithmic units such as `dBm` also cannot participate
in linear ratios.

Convert a symbolic reference explicitly when the next data edge should carry a
different unit:

```python
voltage_mv = experiment.convert(acquired.voltage, "mV")
```

The result preserves shape and availability. A host `ValueRef` produces another
host value; a measured `ProductRef` produces a point-local, recordable product.
Both scalar quantities and unit-bearing arrays use the same operation, and the
target unit becomes part of the returned data contract.

A named function supplies the default compute ID. Lambdas and repeated uses are
allocated as `compute`, `compute.2`, and so on; pass an explicit first argument
when that identity is part of a public data contract.

The same spelling accepts measured products. Native scalars or read-only NumPy
arrays are passed to the function, and its result becomes another recordable
product. An annotated product-bundle dataclass declares a typed structured
result once; `compute` returns that bundle directly, and the kernel can return a
tuple in field order:

```python
from dataclasses import dataclass
from typing import Annotated


@dataclass(frozen=True, slots=True)
class Probabilities(sc.ProductBundle):
    ground: Annotated[
        sc.DataRef[float],
        sc.ScalarType(sc.QuantityType(unit="ratio")),
    ]
    excited: Annotated[
        sc.DataRef[float],
        sc.ScalarType(sc.QuantityType(unit="ratio")),
    ]


@Probabilities.kernel
def discriminate(*, shots) -> tuple[float, float]: ...


probabilities = experiment.compute(
    fn=discriminate,
    shots=acquired.iq_shots,
)
```

Product identities are scoped below the compute identity, so repeated typed
computes only need distinct compute IDs. `ProductBundle.kernel` binds the native
tuple-, mapping-, or dataclass-returning kernel to that reusable symbolic result
schema once, so call sites do not repeat `output_type=...`. `DataRef` means that
each field follows
the compute's input availability: the same bundle schema produces `ValueRef`
fields on the host and `ProductRef` fields after acquisition. Host structured
compute is still one public compute; field projections in the scalar value graph
are compiler-owned. A mapping of names to types remains available for dynamic
schemas. The internal execution model still has separate host and observation
stages, but that distinction is compiler-owned rather than a second authoring
API.

When an array result preserves one measured input's local axes, name that input
with `axes_from` instead of redeclaring a parallel dimension identity:

```python
states = experiment.compute(
    fn=classify_shots,
    shots=acquired.iq_shots,
    axes_from=acquired.iq_shots,
    output_type=sc.ArrayType(
        dtype="int64",
        dimensions=(sc.ArrayDimension("shot", None),),
    ),
)
```

Matching dimension IDs reuse the source axis's exact logical identity, including
when the source came from a nested module or domain program. The derived array
therefore aligns with the original shot dimension rather than creating another
same-labelled axis.

Earlier values can be bound directly beside measured products; no closure-based
or second compute API is required:

```python
classified = experiment.compute(
    fn=classify,
    trace=acquired.trace,
    threshold=threshold,
)
```

Structured immutable Python policy belongs on the same explicit input graph:

```python
probabilities = experiment.compute(
    fn=discriminate,
    shots=acquired.iq_shots,
    discriminator=sc.constant(
        discriminator,
        schema="lab.binary-iq-discriminator.v1",
    ),
)
```

The kernel receives the unwrapped discriminator value, while preview retains
`discriminator` as a named input. Local Python functions may still close over
nonlocal values, but preview exposes their names in `captures` and makes no
replay promise. Put every value that affects the scientific result on the
explicit input graph with a symbolic reference or `constant(...)`; captures are
diagnostic evidence, not a second input or execution mechanism.

Completed-data analysis is ordinary eager Python over a durable run snapshot,
not another experiment compute placement. It publishes native-library results
as datasets, then derives bounded table or figure views from those datasets.
The [analysis publication model](analysis-publication.md) owns that workflow,
including annotated rows, dataframe field mappings, artifacts, facts, and
parameter proposals.

## Inspect placement and liveness before running

`lab.preview(...)` exposes the compiler's decision without leaking compiler IR:

```python
preview = lab.preview(spectrum())
for compute in preview.computes:
    print(
        compute.id,
        compute.placement,
        compute.implementation,
        compute.deterministic,
        compute.inputs,
        compute.outputs,
        compute.demanded_by,
        compute.captures,
    )

for binding in preview.bindings:
    print(binding.id, binding.kind, binding.owner, binding.origin)

for edge in preview.binding_edges:
    print(edge.source, edge.relation, edge.target)
```

Domain targets may also expose a bounded, non-durable inspection for one point.
Select by position, by the complete authored coordinate row, or explicitly
compile a valid off-grid candidate without snapping:

```python
preview = lab.preview(experiment, point="middle")
# Equivalent selectors: "first", "last", or a zero-based logical point index.

selected = preview.selected_point
for compiled in preview.domain_inspections:
    print(compiled.target_id, compiled.artifact_fingerprint)
    print(compiled.content)

same_point = lab.preview(
    experiment,
    coordinates=selected.coordinates,
)

off_grid = lab.preview(
    experiment,
    coordinates={"drive_frequency": sc.Quantity(5.137, "GHz")},
    coordinate_mode="free",
)
```

This compiles only the selected logical point and never admits a run, reserves
resources, invokes an instrument operation, or publishes the waveform projection. The
reference list-mode target returns requested/realized timing, channel
identities, peak and RMS values, content hashes, and a bounded min/max waveform
preview. Strict coordinate matching and off-grid compilation are separate
operator choices; neither silently snaps a physical value to an authored axis
index. Optimizer and operator domain fragments do not implicitly generate
waveform inspections: after coordinate and budget acceptance they use the same
lazy, bounded compilation path as a static scan. The live Run view records group
acceptance and completion status beside measurements. Detailed waveform
inspection remains an explicit selected-point preview; a normal run keeps only
compact execution provenance.

Placement is either `host` (all inputs exist before acquisition) or
`observation` (at least one input is a measured product). `demanded_by` names
the returned record, downstream compute, payload, or experiment effect that
keeps the compute live. Observation computes absent from this list were removed
because no durable output or downstream compute needs them.

Traced completed-run analysis uses `AnalysisExecution` for optional execution
evidence: a local Python identity, named content-identified inputs, encoded
result identities, and captured nonlocal names. It deliberately has no
`placement`, because it is eager code over a run snapshot rather than a node in
the experiment program. This evidence makes no replay, caching, or remote
execution promise. Ordinary analysis calls Python directly and only uses
`context.trace(...)` when retaining that evidence is useful.

`bindings` gives runtime inputs, scan coordinates, and parameter dependencies
one common inspection shape without giving them one lifecycle. Its `owner`
states who may change the value: `invocation` for call-time inputs, `point-plan`
for coordinates, and `configuration` for persistent parameters. `origin`
explains whether an input used its default or an override and which scan form
supplied a coordinate. `binding_edges` represents parameter relationships with
typed `centers` and `overlays` edges, so consumers never need to parse strings
such as `scan-center:drive_frequency`.
The existing function arguments and scan declarations remain the authoring
API; this is a review vocabulary, not another binding DSL.

## Judge authoring against complete paths

Three executable reference paths are the acceptance baseline for authoring
changes:

- flux spectroscopy acquires point-local complex traces, returns one typed result,
  and reads and plots it without restating a measurement schema;
- DRAG calibration turns per-shot arrays into durable probabilities, binds the
  returned result for a dataset fit, and publishes one analysis outcome;
- candidate verification obtains a configuration from that saved analysis,
  runs an independent experiment without changing the accepted default, and
  publishes a project analysis over the exact baseline and candidate datasets.

A convenience is valuable when it removes a declaration or concept from one of
these paths while preserving typed shape, availability, result identity, and
configuration provenance. Internal generality that does not shorten a complete
path is not by itself an authoring feature.

The baseline analysis in those paths uses ordinary Python over a recorded
snapshot and publishes its selected facts, datasets, views, artifacts, and
proposals. `trace(...)` is progressive audit evidence; a user must not need it
merely to fit data, attach a report, or propose a parameter.

## Deliberate remaining boundaries

Some distinctions should stay visible because removing them would hide real
experiment semantics:

- runtime inputs and persistent parameters have different owners and lifecycle;
- scan coordinates describe the point domain, while local array dimensions
  describe data inside one point;
- measurement-dependent configuration or program control requires a later run;
- an explicit `alias(...)` is an additional destination, not ordinary dataflow.

Further convenience should be judged against complete experiments. New
features should extend typed results, the shared compute model, or this
ownership vocabulary instead of introducing another kind of value or another
experiment-data transformation API.
