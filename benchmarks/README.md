# Benchmark Suite

Scopecat benchmarks share one discoverable local entrypoint:

```console
uv run python -m benchmarks list
uv run python -m benchmarks run historical-project --runs 10000
uv run python -m benchmarks run quantum-program --entities 100,1000 \
  --family-points 10,1000 --family-sequence-length 64
uv run python -m benchmarks run scan-execution --profile waveform \
  --points 1,10,100 --qubit-counts 1,2,4
uv run python -m benchmarks run scan-execution --profile lo-sweep \
  --points 10,100 --runners scopecat
uv run python -m benchmarks run scale-suite --list-profiles
uv run python -m benchmarks run scale-suite --through small
uv run python -m benchmarks run scale-suite --profiles medium,full \
  --mode benchmark
uv run python -m benchmarks run payload-attachments --arrays 300 \
  --samples 1024 --iterations 10
```

Every case emits records beginning with `BENCHMARK_RESULT=` and the common
`scopecat.benchmark_result.v1` identity. Case-specific fields remain typed by
`case_id` and `case_version`; the registry is the source of truth for available
cases and their measurement boundary.

## Boundaries

| Kind | Use | Current cases |
|---|---|---|
| `e2e` | User-visible behavior spanning daemon, compiler, runtime, devices, and persistence | `scan-execution`, `scale-suite` |
| `component` | One subsystem boundary with realistic inputs and setup outside the measured operation | `adaptive-context`, `historical-project`, `list-mode-compiler`, `quantum-program` |
| `micro` | One pure operation or data structure without service or storage setup | `inspection-index`, `payload-attachments` |

`scopecat-deployed` is the deployment-representative end-to-end result: a
notebook/client process talks over loopback HTTP to a separately spawned daemon,
and that daemon talks over the multiprocessing instrument transport to a
separately spawned driver worker. `scopecat` executes the same product path with
an in-process daemon and instrument endpoint, so it is useful for faster
diagnosis but does not represent deployed process overhead. `adhoc` is a
descriptive lower bound and `scopecat-core` excludes daemon transport.

Correctness, cache behavior, and retained-structure invariants belong in normal
package tests. The smoke tests here prove that realistic scales remain bounded,
the harness executes, and records keep their declared schema. Wall time, RSS,
and byte measurements are observations rather than machine-independent pytest
thresholds.

Run the deterministic harness contracts serially:

```console
uv run pytest -q -n 0 benchmarks/smoke
```

## Tooling policy

The end-to-end and component cases keep the typed local harness because they
record multiple phases, process RSS, durable bytes, cache facts, and physical
counts in one result. If micro timings become optimization gates, use `pyperf`
for calibrated subprocess timing and comparison rather than extending this
harness with another timing loop. Add `asv` only when cross-commit history and
environment matrices become a routine need.

Raw local results belong under `.benchmarks/`, which is intentionally ignored by
Git. Record the host label and compare equivalent machine and storage profiles.

## Named scale suite

`scale-suite` gives continuous-waveform acceptance and performance runs the
same memorable names. It defaults to `scopecat-deployed`, expands a virtual
target to the requested topology and physical I/Q outputs, and sends complete
contiguous float64 buffers through the normal command-payload and instrument
worker boundaries. Device-side NCO, predistortion, or template replay is not
part of the acceptance contract.

| Profile | Workload | Pressure | Routine |
|---|---|---|---|
| `smoke` | 1q, 1 point, 1,000 samples | complete execution path | every change |
| `small` | 4q, 10 points, 10,000 samples | reference topology and persistence | every change |
| `medium` | 16q, 10 points, 100,000 samples | routing and standard-length batches | daily or before merge |
| `full` | 64q, 10 points, 100,000 samples | intended parallel width | before release |
| `endurance` | 64q, 100 points, 10,000 samples | bounded long-scan working set | weekly or before release |

The `scan-execution` synthetic topology also supports explicit widths up to 128
qubits. The `results` profile prepares the same expanded hardware state as the
waveform profile before collecting retained shots. The named scale-suite profiles
above remain unchanged; a 128-qubit diagnostic is not a new release acceptance
profile.
Synthetic readout tones share one DAC pair; their per-tone amplitude is capped at
`min(0.05, 0.8 / qubit_count)` so expanding the result width does not exceed the
target's unchanged amplitude limit. This is a benchmark fixture, not a readout
calibration recommendation.

Acceptance mode runs once without warmup and exits unsuccessfully when a
correctness or resource invariant fails:

```console
uv run python -m benchmarks run scale-suite --through small
uv run python -m benchmarks run scale-suite --profiles full
uv run python -m benchmarks run scale-suite --profiles endurance
```

For a quick same-process diagnostic, select `--runner scopecat`; this is not the
formal deployment acceptance result:

```console
uv run python -m benchmarks run scale-suite --profiles medium \
  --runner scopecat
```

It verifies completed points, exact rendered and driver-received bytes,
one-entry live waveform retention, bounded batch and daemon payload-spool sizes,
released transient payloads, and a configurable host-memory fraction. The
deployed memory gate uses the concurrent combined RSS of client, daemon, and
instrument worker. Records also expose the three individual process peaks and
daemon startup time under `deployment`. Timings are deliberately not pass/fail
gates.

Benchmark mode defaults to one warmup and three recorded repetitions. Use it to
establish and compare Scopecat's own history on the same host and storage:

```console
uv run python -m benchmarks run scale-suite --profiles medium,full \
  --mode benchmark --host-label lab-m3-16g
```

The named suite isolates topology width, waveform working set, and total scan
volume. It does not by itself claim randomized-benchmarking semantics, flux
lowering, predistortion, or physical-device timing. Those remain separate
capability and hardware acceptance tracks; adding them must preserve these same
profile names and continuous-buffer baseline.

### Comparable measured run evidence

Run the hardware-free residency workload with:

```sh
uv run pytest -q packages/scopecat-server/tests/test_connection_residency_worker.py -k measured_costs_compare
```

It repeats the same two-point program cold, on the same warm connection, and after
an explicit idle-device release/reconnect. Every independently admitted run loads
setup once; the two trigger operations use that loaded program. Assertions compare
uploaded and reused UTF-8 program-buffer bytes, retained-buffer gauges, acquisition
construction intervals, connection identity and durable compilation/finalization
facts. The fixture names its measurements as virtual buffer/scalar work; it does
not claim physical transfer bandwidth or impose a speed threshold. Waveform
rendering is unavailable in this fixture. Existing scan benchmarks remain useful
for target/compiler work and their own phase timing; their estimates and measured
intervals are not added to these ordinary run records.

### Entity-selected reads

Run `uv run python -m benchmarks.component.entity_reads` for 128 entities,
4096 samples, a four-record immutable native chunk, and 30 repeated one-point
reads. Full and two-entity modes run in separate reader processes. Fixture creation
is outside their RSS measurement. The case reads the whole blob on every iteration;
it does not claim a storage pushdown or model the repository byte-cache benefit.

The JSON record separates blob bytes, decoded Arrow logical buffer bytes, copied
selected value bytes, IPC response payload bytes (excluding HTTP headers), and RSS.
RSS is sampled with psutil every 1 ms and at each page; it is a sampled peak, not an
OS high-water guarantee. Allocation caches may retain memory after GC. Do not use
wall time or RSS ratios as portable CI thresholds.

On Darwin, a local 128 × 4096, 30-page run read the same 33,959,258-byte blob in both
modes. Copied `signal` value ndarray bytes decreased from 4,194,304 to 65,536 bytes, and response payload
from 4,273,600 to 70,304 bytes. Sampled RSS peaks were 199,032,832 and 176,324,608 bytes
(baselines about 136 MB); wall times were 0.351 and 0.284 seconds. The immutable
chunk and interpreter remain a material memory floor. These are measured local
facts, not performance guarantees. The value-byte figure excludes validity masks,
metadata/evidence, Arrow buffers and Python objects; it is not total working set.
Only `signal` is requested, so unselected `time` coordinate arrays are not copied.

## Author interaction latency

`uv run --locked python -m benchmarks run author-prepare --repetitions 3`
measures the actual notebook prepare path on a copied virtual reference project,
including repeated calls, input/scan edits and source refresh, without acquisition.
See [author interaction performance](../docs/development/author-performance.md)
for measurement boundaries, worker lifecycle and Windows usage.

`uv run --locked python -m benchmarks run author-analysis --repetitions 2`
records virtual inputs outside measurement, then times ordinary analysis and
comparison inspect/fit with repeated and edited arguments. It includes separate
HTTP phase evidence for both surfaces and performs normal publication on every
operation. See the same author performance guide for pool and source semantics.

Measure ordinary author submission through first visible retained data:

```console
uv run --locked python -m benchmarks run author-first-data --repetitions 2
```

Case version 3 retains the raw monotonic clocks and reports each worker/data
event relative to both submission and the start of the ordinary API interaction.
The latter includes prepare and, for `after_refresh`, the explicit refresh cost.
It also reads the published analysis receipt through the ordinary run API and
reports interaction-to-analysis visibility, separately from the analysis call
and receipt/data reopening. This is API-visible retained content, not a rendered
GUI or a measured click. The result observer still polls every 0.2 seconds.

The serial workload separates initial and repeated calls, scalar and scan edits,
the first call after editing source **without** refresh (still pinned to the old
revision), and the first call after explicit refresh. All runs retain three
virtual points. Refresh validates and adopts its prepare worker, so its cost
must stay in comparisons of the whole interaction. `procedure_python_entry`
marks entry after the lightweight timing helper imports; `procedure_registered`
follows the parent registration gate. The existing `procedure_worker_entry`
follows process-receipt imports and publication. The gap before that existing
event therefore includes more than interpreter startup. The clocks are nested
boundaries, not durations to add together. Keep all trials, including outliers,
and run comparisons serially on the same host without competing test workloads.

This virtual signal case records process startup, measurement transport and an
independent ordinary result reader, followed by retained analysis and receipt
reopening. It also records source-refresh duration. All author cases select
independent parameters and setup explicitly; fixture initialization is outside
the measured operations. See [timing boundaries](../docs/development/author-performance.md#submission-to-first-visible-data)
for observer overhead and the distinction from hardware acquisition and GUI rendering.


Measure retained author workers across source revisions:

```console
uv run --locked python -m benchmarks run author-residency --revisions 3
uv run --locked python -m benchmarks run author-residency --revisions 3 \
  --rounds 3 --operations-per-revision 10
```

This copies the virtual reference project, prepares and publishes analyses using
normal APIs, rotates through at least three revisions and revisits the original.
The optional bounded rounds repeat fresh revision churn and original restoration;
operations-per-revision adds consecutive prepare/analysis calls at each visit.
`--run-per-visit --concurrent-analysis` adds bounded three-point runs and analysis
requests submitted alongside preparation in the same daemon; see the workload
counts and limitations below. The defaults keep the short workload.
Case version 5 correlates every operation
with its revision, round, iteration, phase and analysis receipt, and links
checkpoints by operation index. It records checkpoint PID/creation-time/role/RSS,
asserts same-revision reuse and the two-worker limit per pool, and checks observed
process identities after normal shutdown. Per-process RSS includes shared pages;
its sum is not unique physical memory. Checkpoints do not capture transient
validation candidates, process peaks, long-session leaks or active-request shutdown.
See [worker residency](../docs/development/author-performance.md#worker-residency-and-revision-churn)
for interpretation and current measurements. Raw results belong outside Git.

## Safe-prefix publication cost

`safe-prefix` is a component benchmark of producer batching, daemon measurement
commits and continuous coverage checkpoints. It retains the synthetic scalar
workload used to investigate #1328 without a browser, actual instrument calls,
HTTP transport, or a different persistence policy. It calls the production
execution ports against a real local daemon database; the instrument endpoint
is the testkit's synthetic endpoint. It does not measure complete-screen latency.

The controller checkout owns the benchmark protocol and fixture. Each worker
uses the selected target checkout's **own Python 3.15.1 environment**, installed
with `uv sync --locked`. Git and the locked default development dependencies are
required; no extra benchmark dependency or Library file is needed. The worker
checks that core and server modules actually resolve inside the target checkout.
It does not substitute an old execution module into a newer runtime.

Every record includes the common identity (`case_id=safe-prefix`,
`case_version=1`, `kind=component`), `benchmark.sha`, `target.sha`, separate dirty
flags and lockfile hashes, Python version, platform, and a caller-supplied label.
The common `revision` is the benchmark revision. Use clean, fixed commits for
comparisons. This protocol is validated with baseline
`b18731ea55ce4b1c40c68ecf5055115147de020c` and the final fix
`9d9db1098418053bcfd70d0f73e8e77ac7cc5973`; future incompatible internal ports
should fail visibly or require a new protocol version.

For example, in PowerShell, start in the fixed benchmark checkout and create
separate target worktrees (choose new locations). Run `uv sync --locked` in each
target before returning to the benchmark checkout:

```powershell
git worktree add --detach ../prefix-baseline b18731ea55ce4b1c40c68ecf5055115147de020c
git worktree add --detach ../prefix-fixed 9d9db1098418053bcfd70d0f73e8e77ac7cc5973
uv sync --locked --project ../prefix-baseline
uv sync --locked --project ../prefix-fixed
uv sync --locked
```

Run one sample at a time, with no concurrent tests/typechecks. Set `$storage` to
an existing, dedicated synthetic benchmark parent directory on the target disk;
each `--data-root` below must not exist. Capture stdout outside that data root.

```powershell
$baseline = (Resolve-Path ../prefix-baseline).Path
$fixed = (Resolve-Path ../prefix-fixed).Path
$storage = (Resolve-Path ./synthetic-benchmark-data).Path
$targets = @($baseline, $fixed, $fixed, $baseline)
$labels = @('A1', 'B1', 'B2', 'A2')
for ($i = 0; $i -lt 4; $i++) {
  $target = $targets[$i]
  $label = $labels[$i]
  uv run --locked python -m benchmarks run safe-prefix --target-repo $target --target-python "$target/.venv/Scripts/python.exe" --data-root "$storage/$label" --points 1024 --label $label > "$storage/$label.jsonl"
  if ($LASTEXITCODE -ne 0) { throw "safe-prefix $label failed; inspect retained data" }
}
```

On Unix use the same CLI with `<target>/.venv/bin/python`. The controller performs
one run per invocation; ABBA order is explicit in the labels, not an implicit
warmup or averaging policy. The workload accepts 1–4096 scalar points, defaults
to 1024, transfers pending measurements every 64 points, reads live/durable
previews and renews the lease every 128 points (and at the final point), then
flushes the pending continuous prefix. It checks final durable count and coverage
against the requested point count. Setup, final metric collection and cleanup
are outside `seconds`; the timed loop includes those scheduled reads, lease
renewals and the final flush. No terminal/seal operation is included.

Metric meanings:

- `write_transactions`: successful `SQLiteDatabase.write_transaction` exits
  during the timed loop and tail flush, including lease renewals. Setup/header
  commits are excluded; failed or read-only transactions are not counted.
- `measurement_transactions`: committed `measurements_appended` events, one per
  measurement append transaction in this workload. `coverage_events` counts
  committed `run_coverage_advanced` events; old versions can correctly report zero.
- `encoded_append_bytes`: sum of logical encoded bytes referenced by committed
  measurement appends, including encoding overhead. This is **not physical disk
  write traffic**, WAL/fsync cost, or unique object-store allocation.
- `observed_project_bytes`: sum of file sizes under the synthetic project after
  the final flush while the runtime is open, including SQLite/WAL/object files.
  It is a point-in-time footprint, not cumulative bytes written.
- `stages`: sampled produced/received/durable counts and preview item counts;
  these are API observations, not rendered-screen evidence. Reading does not
  trigger publication.

The data root is exclusively created. On success the worker closes the runtime
and the controller removes only that newly created root. On failure it retains
the directory for diagnosis; it never cleans or reuses an existing data root.
There is a cooperative 600-second check between points and a post-run 250 MiB
footprint check. Neither is a hard resource cap: a blocked filesystem call can
outlast the time check, and the byte check runs after writes. Use an external
process-tree watchdog/resource limit if a hard cap is needed; interrupted runs
may leave synthetic data for explicit inspection and cleanup. No physical HDD
qualification or statistically significant performance claim follows from a
small ABBA comparison. Results from this full-checkout protocol are distinct
from the earlier temporary mixed-module probe and must be labeled accordingly.
