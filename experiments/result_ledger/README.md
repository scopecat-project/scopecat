# Result ledger experiment — #1344

An isolated, disposable replacement of the acquisition-to-analysis path. This is
not a production backend, an adopted migration, or a hardware implementation.
Round 2 specifically tests single-source author intent and atomic post-provider
completion; the original milestone `698fda92725e7359a016cea8af1a2816d2dd6100`
and its negative/cost evidence remain preserved below.
All demonstration data and provider actions are synthetic.

Baseline: public main `f2e514a1a1075b9de0c1bf5de697bba455a72aae`.
The baseline already has invocation intents, execution evidence, sparse recovery
groups, output hashes, acquisition/projection separation, exact analysis slices,
and atomic sparse projection notifications. This experiment does not claim to
invent those capabilities. It tests a different completion protocol.

## Decisions being tested

- A scientific work unit names stable points and required outputs. One result
  bundle can contain multiple array blocks spanning several points; a point is
  neither an attempt nor a storage record. Coordinates and dimension identities
  remain explicit, including units, unavailable samples and provenance.
- An attempt is recorded before invoking the synthetic provider. A process loss
  with a running attempt leaves physical execution **unknown**. Restart does not
  infer that hardware did nothing and never automatically repeats unknown work.
  A recorded retry authorization is necessary to start a new attempt.
  Authorization never resolves the earlier physical unknown: it retains the
  original reason plus the authorizing owner/fence and a separate retry reason.
- Saved results are immutable candidates. Selection is an explicit step. Closed
  groups become ready from selected member units, without a global prefix.
  Publication freezes the selected inputs and emits its notification in the same
  SQLite transaction. In atomic mode, saving the returned result and recording
  its declared selection share this transaction; staged mode remains available. Acknowledging a notification is separate; delivery is at
  least once and consumers deduplicate durable event IDs.
- An open group admits additional work and must be closed explicitly. Its closed
  membership is the completeness contract. Readiness means inputs are fixed,
  not that all samples are valid or an analysis is scientifically successful.
- SQLite remains the persistence engine. The experiment does not introduce a
  general DAG, service fleet, universal event log, or data migration layer.

The separate durable facts are work membership, attempt/evidence history,
immutable results, explicit selections, and frozen publication/outbox. Completion
counts and prefix summaries are derived. This does not make state disappear:
provider execution still lies outside the database transaction, and consumers
still maintain a delivery cursor. Those boundaries are intentional.

## Current architecture and public boundary

`Intent/Axis → compile/initialize → drain(provider) → Store → snapshot/outbox → analysis/viewer`.
The declaration names ordered coordinate axes, output contracts, `work_by`,
`group_by`, traversal direction and closure. It never lists points or member IDs.
The compiler derives those identities by axis indices (including duplicate-value
points), separately from execution order. Grouping by index is explicit and is
not the production analysis rule that can combine equal coordinate values.

- `Intent.initialize/verify/extend`: persist a single admitted intent; extension
  binds a proposal ID, exact source snapshot and rule. Extension never closes a
  group. `Store.close_group` is the author's explicit membership commitment.
- `drain`: interpret admitted work order and declared `sole_candidate` policy,
  without scenario IDs, guessed closure, or implicit authorization to retry.
- `Store.begin_attempt`: persist the pre-provider external-effect boundary.
  Attempt lifecycle, physical certainty and retry authorization remain distinct.
- `Store.complete_attempt`: validate/save the result, audit the declared choice,
  and freeze requested ready closed groups with their outbox in one fenced commit.
  The result cannot be partially saved by failure inside this operation.
- `save_result`, `select_result`, `select_and_publish`: retain candidate-only and
  explicit later-choice paths; an ambiguous candidate set is never guessed.
- `snapshot/events/state`: immutable exact inputs, publication notifications,
  and one coherent derived readiness view. There is no mutable prefix authority.

State responsibilities are membership/intent, execution attempts/evidence,
immutable bundles, explicit selection history, frozen snapshots and outbox.
The numerical contract rejects unsupported narrow floats and integer components
that cannot be exactly represented by the declared float64/complex128 type.

The new author DSL is deliberately bounded: `group_by` must be a subset of
`work_by`, so a work unit cannot straddle analysis groups. Raw-core overlapping
groups are not exposed by this DSL. All result blocks still require a point axis
and every required output must cover the unit's points; point-free work-level
aggregates are not implemented. `state()`/GUI eagerly materialize a small run,
not a streaming long-run query interface.

## Scope and adoption boundary

| Capability | Experiment intent | Baseline relationship |
| --- | --- | --- |
| 2D coordinates, duplicate-value-safe identity | Stable point IDs and explicit axes | Preserve |
| Full waveform and channel provenance | Multi-point, multi-output blocks; synthetic common acquisition | Preserve this fixture; no physical synchronization qualification |
| Units, shapes, per-sample availability | Validate and retain; analysis reports valid/missing counts | Preserve selected subset |
| Sparse completion and retry history | Explicit selection; no global-prefix publication gate | Preserve with a different protocol |
| Frozen analysis inputs and outbox notification | Content identity and atomic publication | Preserve |
| Adaptive append/restart and open-group closure | Persist membership; resume from durable facts | New exploration, not claimed baseline parity |
| GUI selection/manual ranges | Minimal read-only local viewer | Preserve key reading behavior, not full UI parity |
| Arbitrary dtype, ragged/entity/cohort contracts | Numeric types and fixed contracts; split blocks only in deferred-schema mode | Incomplete; no general cohort/all-or-nothing policy or declared ragged dimensions |
| Production adaptive proposal ledger, region budgets, operator queue | Demo only persists one observation-derived append | Uncovered; not equivalent to the production optimizer |
| Real providers, compiler/lowering, device recovery | Not implemented | Uncovered |
| Calibration, procedure orchestration, exports, all existing UI | Not implemented | Uncovered |

A successful slice reduces author/protocol burden while retaining these tested
scientific and failure semantics. Unknown execution being silently replayed,
frozen input drift, dropped availability/provenance, or simplification achieved
only by removing required capability is a failure. Timing improvements alone do
not justify adoption. Whole adoption, partial absorption and discarding the
experiment all remain valid outcomes.

## Reproduction and measured evidence

Run from the repository root with Python 3.14+ (the demo itself uses only the
standard library). Use a new synthetic directory for each demo:

```sh
python -m experiments.result_ledger demo --directory /tmp/result-demo
python -m experiments.result_ledger inspect --directory /tmp/result-demo
python -m experiments.result_ledger serve --directory /tmp/result-demo --port 8765
```

The read-only viewer is at `http://127.0.0.1:8765`. It reads published snapshots;
saved but unselected/unpublished candidates are shown in counts and history.
Manual range and selection survive polling, reload and temporary disconnection.
The minimal viewer intentionally handles only this demo's declared tensor layout.

```sh
python -m experiments.result_ledger demo --directory /tmp/result-fault \
  --crash-at provider-return --hard-crash
# Expected exit 75, including when Python/context-manager shutdown is bypassed.
python -m experiments.result_ledger recover --directory /tmp/result-fault
# Expected exit 3: physical result unknown; no provider action repeated.
python -m experiments.result_ledger recover --directory /tmp/result-fault \
  --authorize-retry
```

Other boundaries are `result-save`, `group-publish`, `notification`,
`adaptive-append` and `adaptive-close`. Omit
`--hard-crash` for controlled exception exit. Every recovery is driven by the
ledger; the provider audit is inspection evidence, never a recovery oracle.
Default `--commit-mode atomic` saves/selects/publishes in one transaction;
`--commit-mode staged` preserves the four-commit first-round path. Recovery uses
the admitted mode. An atomic `result-save` fault is **inside** the transaction:
it rolls back and leaves physical execution unknown. A staged `result-save` fault
is **after** durable candidate saving and needs no repeated provider action.
The sole-candidate selection policy is explicit in the demo. Multiple candidates
are rejected rather than guessed; the core exposes explicit selection.

Never point this experiment at a user project. Creation refuses an existing
path. Recovery and reader commands require an existing experiment ledger.
Nothing discovers devices, installs services or contacts a remote endpoint.
The experimental schema is disposable: use a fresh directory after changing
commits; this branch supplies no migration of earlier experimental databases.

With the repository development environment:

```sh
python -m pytest -q -n 0 -p no:cacheprovider experiments/result_ledger/tests
ruff check experiments/result_ledger
ruff format --check experiments/result_ledger
python -m experiments.result_ledger benchmark --directory /tmp/result-benchmark \
  --baseline-repo /path/to/clean-baseline --points 16 64 256 --samples 32
```

The browser test requires installed Playwright and a local `chromium` executable;
it skips explicitly if unavailable and never downloads a browser. The benchmark
requires the baseline's installed development packages, verifies their actual
import paths and exact SHA, and refuses dirty tracked baseline files. The demo
and core do not import those production packages.

`benchmark.py` implements a separately versioned component protocol inspired by
safe-prefix, not a claim of rerunning safe-prefix v1. It uses subprocess ABBA,
untimed serialization warmup, setup outside timing, real baseline daemon storage
ports, identical scientific values/masks/coordinates, and no changed durability
policy. Both sides use WAL/NORMAL. It records all samples and retained databases.
The baseline publishes per-point Arrow records and recovery proofs; the
experiment publishes multi-point JSON bundles and audits attempts/selections.
These are deliberately different protocols, not an end-to-end speed contest.
First-readable is a storage read, not rendered GUI latency. Tracemalloc omits
native allocations and prebuilt payloads. File growth is retained footprint,
not physical device write amplification. No portable threshold is claimed.


## Round 1 evidence and decision (preserved)

Measured implementation: `cd4874f7a8bf959341319b898785585c79963ebc` (clean).
Baseline: `f2e514a1a1075b9de0c1bf5de697bba455a72aae` (clean).
Python 3.14.7, SQLite 3.53.1, Linux x86_64; each row contains the two raw
observations in one ABBA cycle, 2 channels × 32 samples, 2 points per group.
Both settings: WAL, synchronous=NORMAL, foreign_keys=ON, page_size=4096,
wal_autocheckpoint=1000. These are local component observations, not adoption
thresholds or a comparison of equivalent end-to-end feature sets.

| Points | Path | Publication seconds | First sparse read ms | Write transactions | Timed Python peak KiB | Footprint growth / scientific JSON bytes |
| ---: | --- | --- | --- | ---: | --- | --- |
| 16 | baseline | 0.48 / 0.47 | 80.32 / 79.40 | 16 | 297.93 / 297.73 | 71.12 / 71.12 |
| 16 | experiment | 0.02 / 0.02 | 3.79 / 4.46 | 32 | 35.25 / 35.25 | 44.73 / 44.73 |
| 64 | baseline | 2.53 / 2.64 | 100.75 / 99.79 | 64 | 422.20 / 424.04 | 75.83 / 75.83 |
| 64 | experiment | 0.08 / 0.09 | 4.24 / 4.27 | 128 | 43.34 / 43.34 | 43.45 / 43.45 |
| 256 | baseline | 23.59 / 25.30 | 214.42 / 223.39 | 256 | 1002.26 / 1004.56 | 25.03 / 25.11 |
| 256 | experiment | 0.35 / 0.31 | 5.87 / 3.94 | 512 | 58.51 / 58.51 | 18.97 / 19.04 |

All 12 samples independently reopened persisted scientific values, availability
masks, units and coordinates and matched the same scientific-value hash. The
experimental path is faster in this fixture, but uses **4 write transactions per
group versus 2** for baseline publication. The additional attempts and selections
are durable facts, not free simplification. Arrow/daemon validation and standalone
JSON SQLite have different responsibilities. Neither measured path prices complete application responsibilities;
missing experimental features cannot be priced by these figures. Do not
extrapolate an end-to-end speedup.
Physical I/O write amplification and process RSS were not measured.

Author burden is **not yet improved**: the demo explicitly declares points,
work/group membership and output contracts (53 source lines across `point`,
`unit`, `declaration`), then drives a 111-line fixture orchestration function.
The existing `workflow_fixtures.load_invocation` is a 16-line grid declaration
backed by the production DSL; it is a simpler workload, so these counts describe
interfaces, not an equivalent LOC contest. The experiment removes the separate
pending-record/group/coverage handshakes from its execution chain, but still has
begin/save/select/publish plus a consumer cursor. It has no generic ergonomic
replacement for the existing author DSL, compiler, analysis framework or GUI.

Validation: **33 tests passed** (8.27 s), including real Chromium, four fault
boundaries with both controlled exit and `os._exit`, stale-fence rejection,
publication/outbox rollback, scientific-contract rejection and frozen snapshots.
Provider-return loss blocks recovery without authorization; authorized recovery
retains four attempts and three results, including the old physical unknown.
Other boundaries need only three provider calls. Notification replay increments
delivery attempts without duplicating publications or changing analysis inputs.
Ruff check/format and diff whitespace checks pass. Arbitrary instruction-level
SIGKILL, real devices, production distributed leases, native-memory scale and
complete product feature parity remain untested.

**Round 1 recommendation: partial absorption, not wholesale adoption.** The executable
slice supports multi-point result bundles and explicit closed-group readiness
without a global prefix, including an open group's persisted extension. It also
shows why attempt certainty and retry permission need independent state. The
experiment has not met the author-burden or fewer-durable-protocol goals, and
missing production capabilities rule out replacing main. Preserve these concrete
findings; do not treat positive fixture timings as a migration mandate.
