# Result ledger experiment — #1344

An isolated, disposable replacement of the acquisition-to-analysis path. This is
not a production backend, an adopted migration, or a hardware implementation.
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
  SQLite transaction. Acknowledging a notification is separate; delivery is at
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

## Scope and adoption boundary

| Capability | Experiment intent | Baseline relationship |
| --- | --- | --- |
| 2D coordinates, duplicate-value-safe identity | Stable point IDs and explicit axes | Preserve |
| Full waveform and synchronized-channel provenance | Multi-point, multi-output blocks; synthetic common acquisition | Preserve model semantics; no physical timing qualification |
| Units, shapes, per-sample availability | Validate and retain; analysis reports valid/missing counts | Preserve selected subset |
| Sparse completion and retry history | Explicit selection; no global-prefix publication gate | Preserve with a different protocol |
| Frozen analysis inputs and outbox notification | Content identity and atomic publication | Preserve |
| Adaptive append/restart and open-group closure | Persist membership; resume from durable facts | New exploration, not claimed baseline parity |
| GUI selection/manual ranges | Minimal read-only local viewer | Preserve key reading behavior, not full UI parity |
| Arbitrary dtype, ragged/entity contracts | Only explicitly implemented types/shapes | Incomplete; cannot silently replace production |
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

Other boundaries are `result-save`, `group-publish`, and `notification`. Omit
`--hard-crash` for controlled exception exit. Every recovery is driven by the
ledger; the provider audit is inspection evidence, never a recovery oracle.
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
