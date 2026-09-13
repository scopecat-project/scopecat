# Scalability Benchmarks

For development feedback, test tiers and CI qualification, see
[test feedback](test-feedback.md).

This document turns the scalability direction in the
[project charter](project-charter.md) into representative workloads and
measurements. It separates current implementation facts from target envelopes
so measured bottlenecks can guide each internal change.

Scopecat preserves scalable semantics before optimizing every mechanism.
Logical point, product, and lineage identities remain independent of physical
batching; acquired rows form an append-only physical log; completed groups pin
a logical projection; and large data has a bounded read path. Planning,
storage, and orchestration may evolve behind those contracts.

Benchmark results use three labels:

- **demonstrated**: completed repeatably within a recorded resource budget;
- **target**: the next useful single-lab NISQ envelope;
- **stress**: a larger profile that reveals the next mechanism to evolve.

Executable cases, their component/end-to-end/micro classification, and the
single local CLI live in the
[benchmark suite](https://github.com/scopecat-project/scopecat/blob/main/benchmarks/README.md).
This document owns workload goals and scalability invariants rather than a
second benchmark registry.

Profiles are provisional until a repeatable benchmark records results. Routine
benchmarks use virtual backends and generated or replayed data so execution
volume, payload shape, and failure points remain reproducible. Physical-hardware
validation can then cover timing and integration behavior separately.

## Named Acceptance Ladder

The executable `scale-suite` uses five names for continuous-waveform scale
acceptance and repeatable performance observations:

| Level | Shape | Question answered |
|---|---|---|
| Smoke | 1q × 1 point × 1,000 samples | Does the complete product path execute? |
| Small | 4q × 10 points × 10,000 samples | Does the reference topology behave normally? |
| Medium | 16q × 10 points × 100,000 samples | Do routing and standard-length batches remain bounded? |
| Full | 64q × 10 points × 100,000 samples | Can intended parallel width upload complete buffers? |
| Endurance | 64q × 100 points × 10,000 samples | Does total scan volume stream through a bounded working set? |

The full and endurance profiles carry the same approximate total waveform
volume while stressing different axes. Full maximizes one physical entry;
endurance increases the number of bounded batches. This avoids one profile that
maximizes every dimension and makes failures easier to classify.

Run acceptance through the routine levels or select a release level directly:

```console
uv run python -m benchmarks run scale-suite --through small
uv run python -m benchmarks run scale-suite --profiles full
```

Formal acceptance defaults to the actual local deployment shape:

```text
notebook/client process --loopback HTTP--> daemon process
daemon process --multiprocessing pipe--> instrument worker process
```

Acceptance gates exact completed points, rendered bytes, driver-received bytes,
latest-view retention, payload cleanup, batch bounds, and a configurable
fraction of host memory. The deployed memory value is the concurrent combined
RSS of all three persistent processes; their individual peaks and daemon startup
time are recorded separately. The driver boundary receives ordinary contiguous
float64 arrays; target templates, predistortion, and device-side NCO are not
required. Benchmark mode records the same profiles with warmup and repetition
but never makes wall-clock time a machine-independent pass/fail condition.

`--runner scopecat` keeps the daemon and virtual endpoint in the benchmark
worker for fast diagnosis. It follows the same compilation and execution logic,
but it is not the deployment acceptance result.

This ladder is the scale and transport track, not the whole quantum capability
claim. Parallel randomized-benchmarking semantics, flux lowering, host-side
predistortion/crosstalk, and real-device timing require their own acceptance
cases. They should reuse these level names so capability coverage and scale
remain two readable dimensions instead of one combinatorial profile matrix.

## Workload Model

An individual experiment normally fits a useful hardware and stability window.
Scale accumulates through configured topology, repeated acquisition, structured
result payloads, and analysis-driven runs:

```text
execution volume = sum(points × shots × measured groups)
data volume      = sum(points × products × result payload)
workflow scale   = related runs × configuration and analysis lineage
```

Result payload may itself include measured qubits or channels, shots, and fixed
or ragged sample axes. Backend capacity determines physical partitions without
changing the logical workload.

## Reference Profiles

| Profile | Target shape | Primary pressure |
|---|---|---|
| Full-device calibration | 128 qubits; 100–1,000 related runs; 100–10,000 points per run | topology, resource claims, configuration, and lineage |
| Shot-heavy acquisition | at least 100 million aggregate shots; representative packed-bit and integrated-IQ payloads | batching, binary content, checkpoints, and bounded reads |
| Structured trace | 1,000–10,000 outer points; 1,601-sample complex VNA traces plus representative waveforms and per-shot arrays | object throughput, array shape, slicing, and analysis memory |
| Dense spectroscopy | 100,000 logical points, with a 1,000,000-point stress variant | planning time, peak memory, and compiler partitioning |
| Historical project | 10,000 retained runs, with a 100,000-run stress variant | pagination, indexing, and GUI/API latency |
| Large quantum program | 1,000 selected entities and 100,000 scheduled-event stress variant | retained Map/Repeat IR, target budgets, placement, and paged inspection |

The profiles exercise characteristic combinations rather than maximizing every
dimension at once. Compiler request size remains backend-declared. Shot counts,
trace lengths, and run duration can be varied independently to locate a specific
resource boundary.

## Shared Invariants

Every applicable profile should verify that:

- logical point and product identities remain identical across physical batch
  sizes;
- compiler requests respect backend-declared capacity;
- completed measurement groups are readable before terminal completion even
  when physical acquisition order is noncanonical;
- SQLite events and control records grow at batch or domain-job transition
  granularity;
- binary objects carry large values while control responses remain bounded
  descriptors;
- notebook reads keep memory proportional to the requested measurement batch;
- GUI tables and plots use bounded pages, selections, and previews;
- configuration, analysis, and run lineage remain queryable across a workflow.

Structured acquisition profiles should additionally preserve shot, channel,
and fixed or ragged sample axes instead of flattening them into control-plane
logical points. Dense spectroscopy should record planning costs separately from
execution and measurement costs.

Multi-entity acquisition has an additional schema-width invariant. Homogeneous
results use one indexed entity dimension and one variable per result field;
adding entities extends the dimension index and ordered source/evidence vectors,
but must not add Arrow columns or Dataset variables. The routine 128-entity
projection test checks this contract. Partial entity or shot failure remains one
array with null leaves and sparse reason groups, so a common all-success point
has no per-leaf diagnostic overhead while degraded points remain inspectable.

Large quantum programs have an additional representation invariant. Entity-set
maps and finite repeats remain one structural template through pulse-lowering
planning and until target-entry preparation; structural IR and inspection size
must not grow with the selected entity count.
Concrete scheduling may grow with physical work, but event, acquisition,
waveform, total-result, and single-result-chunk limits must reject an unsafe
request before device preparation. Semantic, placement, and final artifact
layout fingerprints are recorded separately so entry renaming or repetition
changes do not discard reusable logical work. Parallel-map verification checks
the template once, recipe matching streams concrete operations without
retaining an expanded circuit, and the target expansion budget is checked from
the retained workload before any per-entity operation is instantiated.

Inspection acceptance is transport-oriented as well as compiler-oriented:

- the initial response contains at most 128 nodes per authored, logical,
  scheduled, or physical layer;
- a follow-up query returns nodes for one layer only, with stable offset/limit,
  matching-count, and next-offset metadata;
- parent, kind, entity, resource, and result filters use retained ordinal
  indexes before pagination; text matching runs only across those candidates
  when an indexed filter is present;
- entity, resource, and result references inside one aggregate node are capped
  independently and retain their full counts;
- physical placement exposes configured routes plus shared endpoint, local
  oscillator, demodulator, and timing constraints as inspectable nodes;
- placement materializes at most eight relevant route candidates per selected
  signal, records structured rejection reasons, and retains the full candidate
  count plus a truncation marker instead of constructing a device-wide
  signal-by-route product;
- placement is selected through a fingerprinted provider boundary before
  waveform and acquisition planning. Its chosen physical endpoints determine
  calibration lookup, overlap checks, emitted AWG channels, digitizer windows,
  inspection provenance, and the placement cache key.

The GUI should therefore render layer summaries first, page the node list,
show Map/Repeat nodes as aggregates, draw timing only for the selected scheduled
page, and fetch detail by entity or physical resource. Browser memory and DOM
size must be proportional to the page, not to the expanded program.

Target inspection also reports the cache disposition of the exact compilation
that produced the preview. Artifact, semantic, placement, and layout stages are
shown independently as `hit`, `miss`, or `not_checked`; an artifact hit therefore
does not misleadingly claim that lower stages ran. These facts are transient
operator diagnostics and do not enter artifact or invocation identity. Every
stage is bounded by both an entry count and accounted retained bytes. Inspection
also reports its byte budget, retained bytes, oversize skips, and compile time.

The layer summary is an explicit resolution navigator from authored intent
through bound structure and timed events to physical placement. Each stage shows
its total node count, matching count, and loaded-page size. Timeline marks and
lowering links are interactive; a link whose target is outside the loaded page
uses an exact node-identity query backed by the retained inspection index, so
cross-layer navigation does not fetch or linearly scan an entire large layer.

## Measurements

Record the repository revision, machine description, workload shape, wall time,
peak resident memory, durable object bytes, database rows and bytes, event
count, and relevant cold and warm query latencies. Record planning, execution,
persistence, and analysis separately so one result identifies one limiting
boundary.

A target becomes demonstrated when the workload, resource budget, and result
are repeatable. Optimizations should address the first measured limit while
preserving the shared invariants.

## Quantum Program Structure Baseline

The quantum-program probe measures entity-set binding, template-level budget
preflight, and bounded authored/logical inspection without performing concrete
target lowering:

```console
uv run python -m benchmarks run quantum-program \
  --entities 100,1000,10000 \
  --inspection-page-size 128
```

It emits `scopecat.benchmark_result.v1` records for the versioned
`quantum-program` case, with one row per requested scale. Each row records
structural and expanded operation counts,
selected-entity count, preflight outcome, inspection size, cold snapshot and
page projection time, warm exact-node projection time, wall times, and
retained/peak `tracemalloc` bytes. Wall time and memory are recorded observations
because machine variance makes them poor routine CI gates. The deterministic
acceptance test runs 100- and 10,000-entity cases and requires:

- one retained operation and one unresolved template for the one-operation map;
- an expansion-budget rejection before concrete expansion;
- no more than 64 retained entity references on one inspection node;
- returned nodes bounded by layer count times the requested page size; and
- a serialized authored/logical inspection no larger than 32 KiB;
- a warm exact-node query returning one matching logical node from both scales.

Run the acceptance contract with:

```console
uv run pytest -q -n 0 benchmarks/smoke/test_quantum_program.py
```

The test also exercises the indexed table-primary-key path used by entity-set
binding. Non-quantity scalar keys use their canonical semantic identity, while
quantity keys retain tolerance-aware comparison. This keeps ordinary entity
sets linear without changing quantity equality semantics.

## Inspection Index Micro Baseline

Exact-node lookup and inverted-index filtering are pure data-structure costs,
so they run independently from quantum lowering and target configuration:

```console
uv run python -m benchmarks run inspection-index \
  --nodes 10000 \
  --page-size 128
```

The case verifies that an exact query returns one node, a resource query
materializes only its indexed matches, and both serialized responses remain
bounded independently of total index size. Its deterministic smoke contract is:

```console
uv run pytest -q -n 0 benchmarks/smoke/test_inspection_index.py
```

## Historical Project Baseline

The historical-project probe seeds the current relational schema outside the
measurement boundary, then reads through the production daemon HTTP API:

```console
uv run python -m benchmarks run historical-project \
  --runs 10000 \
  --project-analyses 1000 \
  --page-size 100
```

It records newest and deep keyset pages for runs and project analyses, an exact
middle-run read, serialized response sizes, database bytes, and repeated warm
latencies. The default profile represents roughly one hundred retained runs per
day for one hundred days. Setup time is reported separately and is not included
in the read measurements. The smoke contract uses a smaller fixture to verify
that every collection response remains page-bounded:

```console
uv run pytest -q -n 0 benchmarks/smoke/test_historical_project.py
```

## List-Mode Target Compiler Baseline

The target-compiler probe runs the configured reference target rather than a
synthetic inspection index. It records cold and warm semantic, placement,
layout, and artifact stages, along with their retained-memory cache snapshots:

```console
uv run python -m benchmarks run list-mode-compiler \
  --entries 4 \
  --repetitions 16
```

It emits a versioned `list-mode-compiler` record in the shared benchmark
schema. The acceptance test
requires a complete cold compile, an artifact-level warm hit, every stage to
remain within its byte budget, byte-pressure eviction before the entry limit,
and an oversize artifact to bypass the cache instead of violating its memory
bound:

```console
uv run pytest -q -n 0 benchmarks/smoke/test_list_mode_compiler.py
```

Stage byte accounting includes retained waveform buffers and explicit metadata
allowances. It is a deterministic cache budget, not a claim to equal process
RSS; the probe records `tracemalloc` retained and peak bytes separately.

## Scan Execution UX Baseline

The first executable profile compares Scopecat with the useful scaling
properties of a direct lab scan. The direct runner lazily renders one point,
uploads only that point's four real-valued waveforms, retains only the latest
waveforms for an optional live view, collects one integrated-IQ-derived scalar,
and appends the result to one sequential file. It deliberately does not retain
a Python list of all captured entries: total point count must not determine the
waveform working set.

The matched `scopecat` runner executes the reference-lab drag-beta experiment
through the production daemon client, HTTP models, run admission and leases,
operation-scoped payload spooling, instrument service, local driver endpoint,
collection, and durable run storage. `scopecat-core` is an
optional diagnostic that runs the same planning and execution semantics through
a direct test instrument host; it deliberately excludes daemon transport and
must not be used as the product acceptance result. The default comparison is
therefore `adhoc,scopecat`.

The matched runners use a fixed single-shot workload with four physical
channels and a 72-sample point waveform. Point count is the only scale variable
in the zero-delay run. The Scopecat workload declares its coordinate as a
compact start/stop range; the direct runner derives its point-local waveform
parameters from the loop index without retaining a coordinate list.
The default acquisition policy is `prefer_device`, so a digitizer advertising
compatible onboard integrated-IQ support returns the same scalar result shape
as the direct baseline. The direct baseline does not model raw-trace transport
or target-side DSP and cannot be selected with `--acquisition-dsp target`.
`--point-delay-ms` adds the same logical-point dwell to both virtual hardware
paths for a representative total-time run. Shot-heavy acquisition and long
waveforms use separate matched profiles so their working sets do not obscure
point-count costs.

Run a short comparison with:

```console
uv run python -m benchmarks run scan-execution \
  --points 1,10,100 \
  --host-label lab-pc-hdd \
  --storage-root /path/on/the/experiment-drive
```

The end-to-end benchmark acceptance tests launch nested worker processes and are
intentionally outside the default pytest test paths, especially because process
startup dominates their cost on Windows. Run them explicitly after changing the
benchmark harness or its execution boundaries:

```console
uv run pytest -q -n 0 benchmarks/smoke/test_scan_execution.py
```

`--storage-root` must name an existing directory on the storage device being
measured. The command creates isolated run directories below it and removes
them after each worker. Raw JSON Lines results default to
`.benchmarks/scan-execution.jsonl`. Every worker records the Git revision and
dirty state, host metadata, scenario, phase timings, resident-memory growth,
durable bytes, object-store bytes, file counts, logical point count, and
physical trigger count. `object_store_bytes` is especially important when
comparing `scopecat-core` with `scopecat`: it now measures only durable
content, not ordinary command transport. `peak_payload_spool_bytes` measures
the largest simultaneous command-payload working set and
`payload_spool_bytes_at_finish` must be zero after a completed run.

Use three measured repetitions after one warmup for a comparison run. Increase
the largest point count geometrically only while the previous step remains
within its time and disk budget, for example `300,1000` after the short run.
Run each runner in a separate process, as the command does, so allocator state
and prior Scopecat runs do not contaminate the direct baseline.

To isolate daemon and object-store overhead after a default comparison, add the
core diagnostic explicitly:

```console
uv run python -m benchmarks run scan-execution \
  --runners scopecat-core,scopecat \
  --points 1,10,100
```

Run the same staircase again with a representative acquisition duration when
evaluating total experiment UX:

```console
uv run python -m benchmarks run scan-execution \
  --points 1,10,100 \
  --point-delay-ms 10 \
  --host-label lab-pc-hdd \
  --storage-root /path/on/the/experiment-drive
```

The synthetic dwell makes the two virtual paths wait for equal aggregate
logical-point time. Physical-hardware validation remains necessary for driver
and timing behavior.

### Fixed-program Host-LO Sweep

The `lo-sweep` profile models dense spectroscopy where the outer scan changes
the host-controlled RF source and the quantum target receives the same fixed-IF
Ramsey program at every point:

```console
uv run python -m benchmarks run scan-execution \
  --profile lo-sweep \
  --runners scopecat \
  --points 10,100,1000 \
  --host-label lab-pc-hdd \
  --storage-root /path/on/the/experiment-drive
```

The logical regression requires the LO coordinate to appear in an RF interface
member assignment and not in the domain program inputs. Each host step still
causes one physical trigger, but an ordinary synchronous success uses
`abnormal_only` evidence: it adds no domain-job transition row. Measurement
appends remain bounded chunks, command payloads remain transient, and no
waveform or domain program becomes durable run content. Compare preparation,
active time, peak payload-spool bytes, control bytes, and file count on the
actual HDD; wall times remain observations rather than machine-independent CI
thresholds.

### Latest Multichannel Waveform Profile

The waveform profile varies the per-point physical waveform working set while
retaining the same device-integrated scalar IQ result. One to four driven
qubits map to four to ten physical output channels: two drive channels per
qubit plus one shared readout I/Q pair. Both runners render and upload the same
number of float64 waveform samples. With `--live-waveform`, each path retains
only the latest completed point for a plotting-tool stand-in.

Run a short staircase with:

```console
uv run python -m benchmarks run scan-execution \
  --profile waveform \
  --points 1,10,100 \
  --waveform-samples 4096 \
  --qubit-counts 1,2,4 \
  --live-waveform \
  --host-label lab-pc
```

In addition to timing and RSS, compare `waveform_bytes_uploaded`,
`max_waveform_batch_bytes`, and `live_waveform_bytes_retained`. Live-view
retention must equal exactly one point's channel-by-sample payload. Maximum
batch upload reveals whether target entry batching multiplies that working set.
Increase waveform length independently of point count; do not combine this
profile with shot-heavy payloads.

Target-side DSP is a separate Scopecat stress test because it transfers a raw
trace for every logical point. It deliberately excludes the ad hoc runner,
whose scalar device facade does not perform equivalent work:

```console
uv run python -m benchmarks run scan-execution \
  --profile waveform \
  --acquisition-dsp target \
  --runners scopecat-core,scopecat \
  --points 10,100 \
  --waveform-samples 100000 \
  --qubit-counts 4
```

Do not use this stress result as the default product comparison on digitizers
that support compatible onboard IQ integration.

### Multiqubit Result Retention Profile

The result profile varies scan points, measured qubits, shots, and the data the
user actually chooses to keep. Its durable direct-runner modes mirror the sample
storage mechanics: summary rows append to one sequential file, while shot arrays
use one uncompressed NPZ per point with complex128 IQ and integer bit arrays.
This is a descriptive baseline for current lab storage growth, not a taxonomy of
user intent or a target storage layout for Scopecat.

Run each durable selection independently:

```console
uv run python -m benchmarks run scan-execution \
  --profile results \
  --retention summary \
  --points 10,100 \
  --qubit-counts 1,2,4 \
  --shots 1000

uv run python -m benchmarks run scan-execution \
  --profile results \
  --retention bit-shots \
  --points 10,100 \
  --qubit-counts 1,2,4 \
  --shots 1000

uv run python -m benchmarks run scan-execution \
  --profile results \
  --retention iq-and-bits \
  --points 10,100 \
  --qubit-counts 1,2,4 \
  --shots 1000
```

`acquired_result_bytes` is the complex128 IQ volume produced by the matched
logical acquisitions. `selected_result_bytes` is the minimum semantic payload:
eight bytes per selected probability, one bit per selected classified shot,
and sixteen bytes per selected IQ shot. `measurement_dataset_bytes` counts the
durable measurement header and Arrow chunks, while
`control_and_provenance_bytes` contains the rest of project growth. Compare the
last two separately: a small selected payload can legitimately expose a fixed
run cost, but increasing shots must not enlarge a summary-only dataset.

The direct runner also provides a synthetic write-free lower bound. It performs
the acquisition and constructs the current point's results, then discards them
without creating metadata or result files:

```console
uv run python -m benchmarks run scan-execution \
  --profile results \
  --retention discard \
  --runners adhoc \
  --points 100 \
  --qubit-counts 4 \
  --shots 1000
```

`discard` is only a cost lower bound. It does not claim that the sample system's
`save=False` or similar switches represent one coherent no-save workflow. Those
switches can also compensate for hard-to-compose outer scans, unwanted child
datasets, coarse run organization, caller-owned persistence, or the absence of
pre-run waveform inspection.

Scopecat runners intentionally reject this mode. The compiler requires every
prepared acquisition address to have real downstream product demand, and adding
an artificial terminal consumer solely to make this benchmark symmetric would
change product semantics. A non-durable consumer should be added only after a
representative workflow demonstrates that durable derived results, composite
experiment ownership, and explicit waveform preview do not cover the need.

The retention invariants are:

- unselected IQ does not contribute measurement payload bytes;
- summary dataset growth is independent of shot count;
- bit-shot storage uses boolean measurement values rather than integer-width
  values, even though the descriptive ad hoc baseline retains integer arrays;
- IQ and bit arrays remain shot dimensions within each point rather than new
  logical scan points;
- the reference list-mode runtime retains raw integrated-IQ as one
  address-major `complex128` matrix plus a boolean availability mask; it does
  not allocate one Python frame object per acquisition shot;
- measurement files grow with bounded chunks, not one file per point;
- the direct-runner discard lower bound creates no durable metadata or
  measurement dataset.

Timing begins after the reusable lab/server and instrument composition exists.
The phase boundaries are:

- **prepare**: experiment submission, invocation construction, validation,
  planning, and compilation until the first physical trigger;
- **active**: first physical trigger through the final completed instrument
  collection; compilation, uploads, execution, and collection may overlap here;
- **finalize**: final collection through the completed, durable run return;
- **wall**: prepare plus active plus finalize;
- **first result**: submission through the first completed collection, retained
  as a secondary diagnostic rather than a required one-point batch.

Interpreter import, daemon cold start, reusable service composition, and backend
endpoint construction are outside this profile. Run-scoped instrument
provisioning and connection are inside preparation. Cold-start costs should be
measured separately if they affect the normal launch workflow. The primary UX
gates are preparation and wall time. Peak resident-memory growth and durable
object/file growth identify whether a failure is caused by eager planning,
in-memory retention, transport, or persistence amplification.

Until measurements justify tighter product budgets, use these provisional
acceptance gates:

- preparation remains below one second at the intended scan size and does not
  grow linearly with total points;
- with representative point dwell, Scopecat adds no more than 10% or one second
  to total wall time, whichever allowance is larger;
- peak memory is bounded by physical batch and current-point payload size, not
  total scan waveform volume;
- durable file and control-record counts grow with batches or domain-job
  transitions, not one object per logical point.

The absolute one-second limits are UX budgets, not ratios against a nearly
zero-duration direct loop. Change them only with a recorded lab workflow and
repeatable measurements.

### Launch Validation and Materialization

Launch should perform only checks whose cost is expected to remain small
relative to one physical point: symbolic and type verification, configuration
resolution, static resource authority, and one fully compiled domain probe. It
does not render every point's waveforms to discover later lowering failures
before the run starts. The reference target probes one point, then uses the
concrete artifact to size the following batch. Cartesian point rows, point
parameter overlays, runtime point objects, and static value records are now
random-access views evaluated for the current bounded coverage. Range and
center/span axes retain compact evaluated sources through planning, catalog
fingerprinting, and the durable dataset schema. Explicit values axes still
remain proportional to their declared coordinate count, as their individual
values are the source rather than generated points.

This is an intentional UX tradeoff. Structural mistakes fail before hardware;
shape- or value-dependent failures that require actual waveform lowering may
fail when their bounded batch is reached. A separate explicit exhaustive check
may be useful for unattended runs, but it must not become the default launch
path or silently materialize the full waveform volume.

Physical batching remains expressed as a bounded point count. When a named
point recovery group is present, core prefers a candidate end at a group
boundary if one fits the capacity, but every point boundary remains a legal
physical cut. Group completion controls result publication and interruption
recovery instead of target packing. Capacity is not point-only. The reference
compiler currently combines the device entry limit with an 8 MiB aggregate
waveform target derived from the largest entry in the preceding compiled batch.
Waveform channel count, sample count, and dtype therefore reduce the following
point count automatically. A
later abrupt increase in entry size can overshoot the target for one batch; the
next feedback corrects it. This adaptive target is a working-set control, not a
new logical experiment dimension.

The next capacity axes should be added only when their matched profile proves
they dominate a budget:

- shots and acquisition channels determine execution time and result volume;
- fixed or ragged trace samples determine binary result and analysis memory;
- configured topology determines routing and static authority cost;
- logical point count determines scalar point metadata, result rows, and batch
  count even when waveform memory is bounded.

Large shot and trace axes should remain structured result dimensions rather
than being flattened into more control-plane points.

## Current Implementation Boundaries

The present architecture provides a direct end-to-end baseline:

- linking and planning retain compact range and center/span sources or authored
  explicit values, while deriving Cartesian rows, point parameter bindings,
  runtime points, and static value records on demand; forward traversal is a
  range, while traversal modes that reorder points still retain an explicit
  ordinal sequence;
- local target preparation retains static route manifests and materializes one
  initial physical probe for fast validation and preview. Execution reuses that
  probe in its first bounded coverage batch instead of lowering the first point
  again. An explicit domain inspection compiles only its selected logical point
  and returns target-owned waveform statistics and min/max samples under a hard
  response budget without persisting them. The general point preview samples at
  most 64 edge points;
  local-only coverage starts with 32 points and then uses batches of at most 256
  points;
- domain compilation uses a backend-declared point capacity but prepares only
  the current batch during execution. Normal execution does not build target
  inspection data; preview and review compile only the selected point and ask
  for target-owned inspection explicitly. Each prepared domain job reports the
  maximum point count for the following batch;
- quantum target artifacts carry a fingerprinted device snapshot, per-event
  logical-to-physical placement, and an exact deduplicated resource footprint.
  Placement records a bounded set of selected and rejected route candidates
  with structured reasons. Inspection adds this as a bounded physical layer
  linked to scheduled events, so large programs can be navigated by abstraction
  level instead of rendered as one flat circuit or waveform list. Follow-up
  pages use artifact-scoped cursors, and a newer review query supersedes stale
  queued or in-flight work;
- bound quantum IR retains entity-set parallel boundaries and repeat counts,
  while inspection reports structural versus expanded operation counts. The
  program call may retain a topology-backed qubit-set intent; configuration
  binding resolves a fixed count or connected component in deterministic
  topology order and keeps the intent beside its resolved entity table. The
  authored program therefore does not encode chip size or physical numbering.
  A fingerprinted placement provider owns logical-to-physical routing, and its
  selected endpoints drive the compiled waveform and acquisition artifact rather
  than serving as explanation-only metadata. The list-mode compiler independently
  caches semantic analysis, waveform/placement planning, artifact layout, and
  final artifacts by their layered fingerprints.
  Artifact eviction therefore does not force target-independent analysis or
  waveform planning to repeat. Its continuation report exposes list-entry,
  waveform-memory, per-entry sample, and repetition budgets plus the limiting
  dimension used to choose the next batch size;
- the reference list-mode target combines its device entry capacity with an
  adaptive 8 MiB aggregate waveform target. Its AWG and virtual-capture codecs
  carry contiguous float64 samples in binary rather than expanding arrays into
  JSON numbers. Phase-only sweeps share one sampled template plus per-entry
  phase rows and synthesize contiguous DAC buffers only at the driver-upload
  boundary; structurally different entries retain ordinary materialized
  buffers. Direct and run-scoped hardware receipts likewise carry typed headers
  plus binary measurement-array attachments, and target result blocks remain
  array-native through correlation. Large shot results are collected in bounded
  chunks with explicit shot offsets, then correlated into the original domain
  and logical measurement axes without joining their buffers. The logical
  rectangular value retains those shot partitions through Arrow persistence and
  GUI decoding; NumPy-oriented compute, trace, and interop boundaries
  materialize it explicitly. Immutable byte-backed arrays are adopted across
  typed model and wire-decode boundaries rather than recopied. Domain
  realization validates the complete output inventory before transferring
  canonical values one at a time into the execution coverage sink; the daemon
  then owns bounded pending Arrow chunks until durable ingest acceptance rather
  than retaining a second complete candidate tuple;
- cancellation fences every hardware batch submitted from a domain job runtime,
  including each bounded shot chunk. The invocation identity is durable before
  domain setup or provider start, each checkpoint is durable before its next
  `resume`, and the terminal receipt is durable before result realization. A
  request arriving during a driver call is honored before the next batch; the
  completed receipt is still interpreted first, so cancellation cannot turn
  known hardware evidence into a fabricated indeterminate failure. The maximum
  cooperative cancellation delay is therefore one active driver call or one
  configured result chunk. Complete invocation intent, checkpoints, and
  receipts live only in the paged transition ledger; the run terminal retains
  aggregate counts and target ids instead of an O(job-count) attempt array;
- admission uses the domain compiler's static instrument footprint and all
  structurally compatible local route candidates. Point-local routing narrows
  the operations actually emitted, so a run may conservatively reserve an
  unused candidate rather than scanning every point before admission;
- one SQLite writer owns durable ordering while immutable object storage carries
  large content. The executor-to-daemon ingest path, durable chunks, and live
  GUI latest-point path all use the same schema-driven Arrow IPC columns, so
  numeric arrays never expand into JSON lists. Measurement chunks remain
  bounded by both record count and value bytes. Immutable chunks retain
  acquisition order, while dataset identity hashes the selected records in
  logical point order and is therefore independent of chunk boundaries and
  retries;
- recovery groups have stable unique ids and a schedule-level membership
  fingerprint. Their normalized SQLite ledger records exact sparse point
  coverage separately from the physical acquisition log and rejects
  overlapping points, conflicting retries, or measurement proofs whose record
  hashes have not yet been acquired. Each proof pins the corresponding
  acquisition rows in the logical projection. Orphan and retried rows remain in
  the append-only log, successful seal completes any non-static projection from
  the latest acquisition per point, and logical reads sort by point identity;
  recovery groups do not become file or hardware-batch boundaries;
- ordinary command payload uploads use an in-memory spool scoped by run and
  hardware operation, or by direct session and command. A completed, rejected,
  or replayed operation releases its bytes immediately; owner termination and
  daemon shutdown clear orphaned uploads. Unique waveform programs therefore
  do not accumulate in the permanent object store or with total point count;
- projected Arrow readers and GUI previews provide bounded read paths. The GUI
  pages oversized product-grid slices by logical offset while retaining their
  authored-axis selection; mixed-radix index projection jumps directly to a
  deep window without enumerating its prefix.

Generated axes no longer impose a point-count-sized preparation cost: their
evaluated generation parameters are fingerprinted and persisted directly, and
analysis materializes axis values only when a complete grid view requests them.
An explicit values scan necessarily carries one declared value per logical
point; users should prefer range or center/span intent when the coordinate is
generated. Cartesian products do not multiply any of those factors into
retained rows. Local effects, static value evaluation, runtime point projection,
and live prepared target artifacts are bounded by the current physical batch.
During active execution the completed point index set and durable scalar results
still grow with completed point count; neither contains waveform payloads.
Optimizer and operator fragments append one contiguous point range, then reuse
the static run's lazy batch compiler without generating per-point inspections.
The daemon's live proposal feed retains only the latest 64 events. Active feeds
are never pruned, while only the 32 most recently used inactive run feeds and 32
most recently updated inactive review sessions remain available. Explicit
selected-point inspections enforce point, waveform, and sample budgets and are
operator views, not durable run content.
Adaptive optimizer calls likewise receive exact counters but only the latest
1,024 domain decisions and 256 completed-point observations for their scope.
Durable domain decisions remain queryable through the daemon ledger.
Completed-point observations retain canonical point identity plus metadata-free
scalar and unavailable observable values. Array values, acquisition evidence, record
metadata, and scalar metadata are deliberately omitted; optimizers that need a
trace-derived feature should declare a scalar measurement compute for that
feature. Heavyweight measurement records therefore do not accumulate in the
in-process optimizer context.
Run the long-run memory probe with:

```console
uv run python -m benchmarks run adaptive-context \
  --decisions 20000 --domain-points 1024
```

The probe feeds a distinct 256 KiB waveform through every retained observation
and proposes large point-cloud domains without retaining their coordinate rows.
The emitted `retained_decisions` and `retained_observations` must stay at their
fixed windows, `retained_array_observables` and `retained_fragment_payloads`
must remain zero, and retained
tracemalloc/RSS measurements must not include the reported discarded waveform
payload.
The daemon retains the latest received measurement for the live Arrow view plus
the bounded not-yet-durable acquisition tail. That state exists only while its
executor lease is active and is released on seal, terminal commit, lease loss,
or daemon shutdown.
Inline command payloads retain raw bytes in memory and convert to base64 only
for an actual JSON wire representation; the daemon client uploads those bytes
to an operation-scoped content-addressed spool before posting a control command
containing only the blob descriptor. Hardware operation identities cover the
descriptor rather than serializing the payload body.

The spool is deliberately transient: if the daemon restarts after an upload but
before the command is accepted, the client must submit the command again and
re-upload its payload. A lost response while the daemon remains alive can be
replayed from its process-local operation ledger without materializing the
released bytes. Permanent
publication remains a separate future capability for payloads that must be
inspectable after execution.

The production waveform profile now separates transient transfer, durable
object retention, compilation, and driver work. For generated axes, its
remaining total-point scaling is active execution bookkeeping and durable
results, not launch preparation, waveform retention, or Cartesian row
materialization. Explicit value sources and non-forward traversal still retain
point-count-sized declarations. The other profiles establish whether
acquisition volume or history reaches its resource budget first. Run history
and project-level multi-run publications use relational keyset indexes; their
long-lived read boundary is covered by the historical-project probe.

## Development Cadence

1. Maintain deterministic generators for the reference profiles.
2. Record the demonstrated envelope before changing a scalability mechanism.
3. Select the first resource budget that prevents the next useful workload.
4. Replace that mechanism and rerun semantic tests across physical batch sizes.
5. Promote a target after its measurements are repeatable and its normal user
   workflow remains approachable.

This cadence keeps performance work tied to end-to-end product value while
leaving room for decisive internal changes during early development.
