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
