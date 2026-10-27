# Result ledger experiment — #1344

A runnable, disposable alternative acquisition-to-analysis chain using synthetic
providers only. It is not adopted, deployed, or a migration. Public baseline:
`f2e514a1a1075b9de0c1bf5de697bba455a72aae`.

**Round-2 result:** single-source author intent and fewer post-provider commits
are demonstrated for the bounded scene. Throughput/memory superiority and full
production equivalence are not demonstrated. Retain this as a replacement
candidate for independent adoption review; do not replace main.

## Architecture and scientific boundary

`Intent/Axis → compile/initialize → drain(provider) → Store → snapshot/outbox → analysis/viewer`.

One declaration names ordered coordinate axes, output contracts, acquisition and
analysis partitions, traversal direction, selection policy and closure. It does
not list point IDs or repeat memberships. The compiler derives stable identities
by axis indices, independently from physical execution order. Duplicate values
remain distinct. **Axis-index grouping is explicit**, unlike production analysis
that may combine equal coordinate values.

Core facts and public APIs:

- `Intent.initialize/verify/extend`: admitted intent and fingerprint; derived
  membership; idempotent accepted proposals with exact source snapshot and rule.
  Extension leaves a group open. `Store.close_group` fixes final membership.
- `drain`: bounded interpreter for persisted work order and the author's explicit
  `sole_candidate` policy; no scenario IDs, inferred closure or implicit retry.
- `Store.begin_attempt`: durable pre-provider boundary. Attempt lifecycle,
  physical certainty and retry authorization are independent. An authorized retry
  never changes the earlier attempt's physical `unknown` to success.
- `Store.complete_attempt`: one fenced transaction saves a returned result,
  audits the declared choice and publishes requested ready closed groups, frozen
  input manifests and outbox. No partially saved result survives rollback.
- `save_result/select_result/select_and_publish`: candidate-only and later-choice
  paths remain. Ambiguous choices are rejected; published selections are frozen.
- `snapshot/events/state`: hash-verified exact inputs, durable publication events,
  and one coherent derived readiness view. No mutable prefix is authoritative.

Result blocks span multiple points and outputs, retaining dimensions, units,
coordinates, availability and provenance. Required outputs must cover every unit
point, and every block still needs a point axis: work-level point-free aggregates
are not supported. Unsupported narrow floats and integer components not exactly
representable by their declared float64/complex128 type are explicitly rejected.

The 2D demo executes high bias before low bias, returns two-channel waveforms plus
a diagnostic, preserves partial Q samples, and derives one adaptive addition from
an exact published analysis. Static groups are explicitly closed; the adaptive
group is appended then explicitly closed. The viewer retains selections/manual
ranges through refresh, reload and disconnection and displays old unknowns after
successful retries. Simulation provenance does not prove physical synchronization.

## Run and test

From the repository root, Python 3.14+; demo/core use the standard library:

```sh
python -m experiments.result_ledger demo --directory /tmp/new-result-demo
python -m experiments.result_ledger inspect --directory /tmp/new-result-demo
python -m experiments.result_ledger serve --directory /tmp/new-result-demo --port 8765
# Read-only viewer: http://127.0.0.1:8765

python -m experiments.result_ledger demo --directory /tmp/new-result-fault \
  --crash-at provider-return --hard-crash
# Expected exit 75, bypassing Python/context-manager shutdown.
python -m experiments.result_ledger recover --directory /tmp/new-result-fault
# Expected exit 3; no repeated provider action without explicit authorization.
python -m experiments.result_ledger recover --directory /tmp/new-result-fault \
  --authorize-retry
```

Other boundaries: `result-save`, `group-publish`, `notification`, `adaptive-append`,
`adaptive-close`. Omit `--hard-crash` for controlled exception exit. Default
`--commit-mode atomic` uses begin + completion; `--commit-mode staged` preserves
the original begin/save/select/publish chain. Recovery uses the admitted mode.

| Interruption | Atomic | Staged |
| --- | --- | --- |
| provider-return | Result absent; physical unknown on recovery | Same |
| result-save | Inside transaction: rollback; physical unknown | After candidate commit: reuse saved result |
| group-publish | Result, choice, snapshot and event committed together | All four stages already committed |
| notification | Durable event redelivery, deduplicated effects | Same |
| adaptive append / close | Persisted proposal; open / closed membership respectively | Same |

Creation requires a new directory; existing data is never overwritten. Recovery
requires an experiment ledger. Use fresh data after changing experimental schema;
there is deliberately no migration. No device discovery, remote service, runner
installation or user project is involved. The provider audit is evidence, never a
recovery oracle. Notification delivery is at least once, not hardware exactly once.

With repository development dependencies:

```sh
python -m pytest -q -n 0 -p no:cacheprovider experiments/result_ledger/tests
ruff check experiments/result_ledger
ruff format --check experiments/result_ledger
python -m experiments.result_ledger.author_comparison
python -m experiments.result_ledger.baseline_flow --directory /tmp/new-baseline-control
python -m experiments.result_ledger benchmark --directory /tmp/new-result-benchmark \
  --baseline-repo /path/to/clean-baseline --points 16 64 256 --samples 32
# --comparison staged-atomic runs only the more comparable pair.
```

Browser tests use installed Playwright and a local `chromium`, otherwise explicitly
skip; they never download a browser. Baseline controls require installed production
packages, verify actual import paths and exact clean baseline SHA.

## Evidence and decision

Measured implementation: `09d84696252cf943dc5e9ee411074f2e69859be2` (clean).
**78 tests passed, 14.28 s**; real Chromium, controlled/hard exits, both commit
modes, transaction rollback, lost-response replay, stale fencing, binary64
precision, duplicate coordinates, coarser groups and source-bound append/close
recovery. Ruff and whitespace checks pass.

An independent agent performed two read-only source reviews. Fixed findings:
numeric labels that did not enforce representation, missing adaptive source
identity, duplicate readiness logic, and missing dynamic-boundary fault coverage.
No additional blocking atomicity/fencing/frozen-selection issue remained.

**Author hypothesis established for the same scene.** `author_comparison.py` runs
the unchanged first milestone from Git in an isolated directory and the current
scene. Logical coordinate order, units, work/group membership, output contracts,
waveforms, availability and publication order match; each yields 6 points,
3 units/results/snapshots. Three handwritten member structures and a fixed
unit-ID traversal become one declaration: 53 → 35 author source lines. The
important reduction is repeated information, not LOC. New compiler/interpreter
code was added, so this is not evidence of a smaller whole system.

**Commit hypothesis established with an explicit tradeoff.** Atomic success uses
2 writes per two-point closed group versus staged 4, with the same success-scene
attempt/result/selection/snapshot/outbox facts. Begin before the provider remains
separate. Atomic result-save failure loses the uncommitted candidate and requires
unknown handling; candidate-only saving remains available. Open-group append and
close, later choices, analysis publication and consumer acknowledgement are
additional commitments outside this count.

Protocol v2 uses subprocess ABBA, untimed serialization warmup, setup outside
measurement, 2 channels × 32 samples and unchanged WAL/NORMAL/4096-byte pages/
1000-page checkpoint policy. The staged↔atomic pair isolates the experiment's
completion protocol more closely than the separate baseline↔atomic pair:

| Points | Mode | Publication seconds (two samples) | First sparse read ms | Write transactions | Timed Python peak KiB | Footprint growth / scientific JSON bytes |
| ---: | --- | --- | --- | ---: | ---: | --- |
| 16 | staged | .0358 / .0167 | 9.19 / 3.58 | 32 | 35.50 | 44.73 / 44.73 |
| 16 | atomic | .0208 / .0205 | 4.50 / 3.87 | 16 | 38.96 | 41.71 / 41.71 |
| 64 | staged | .0726 / .0738 | 3.26 / 3.38 | 128 | 43.45 | 43.53 / 43.53 |
| 64 | atomic | .0682 / .0842 | 3.60 / 3.73 | 64 | 49.01 | 40.37 / 40.37 |
| 256 | staged | .2549 / .2862 | 3.48 / 3.51 | 512 | 58.62 | 19.02 / 18.98 |
| 256 | atomic | .2737 / .2815 | 3.50 / 3.53 | 256 | 62.50 | 19.45 / 19.42 |

All 24 v2 samples passed persisted scientific-value equality and audit-count
checks. Timing ranges overlap, atomic allocation peak is slightly higher, and
256-point footprint is slightly higher. **Fewer commits is not proven speed,
memory or physical-write superiority.** Tracemalloc excludes native allocations
and prebuilt payloads; file growth is retained footprint, not physical write
amplification. First-readable is a storage read, not rendered GUI latency.

Baseline↔atomic remains unequal-responsibility component evidence: production
Arrow/daemon checks versus standalone JSON SQLite; forced per-group publication
bypasses production interpreter batching, and its two commits exclude the
experiment's unconditional attempt audit. This is not safe-prefix v1 (different
interpreter/workload/protocol) or end-to-end performance evidence.

`baseline_flow.py` separately verifies the genuine production author → compiler/
planner → interpreter → SQLite → dataset path: 4-point 2D, 2×8 waveform, checked
units/shapes/values. Its all-valid host-compute substitute is not full-scene parity.
Three existing baseline core cases also passed for adaptive optimizer, queued
operator work and sparse recovery.

First-round commit `698fda92725e7359a016cea8af1a2816d2dd6100` and its full README
and cost/failure conclusions are unchanged in Git. Preserved local evidence is in
`.benchmarks/result-ledger/cd4874f7/`; round-2 raw samples, author comparison and
baseline-flow report are in `.benchmarks/result-ledger/round2-09d84696/`. Raw results
are intentionally ignored by Git; all checks have reproducible commands above.

**GO:** retain this complete alternative chain as a viable replacement candidate
for independent adoption review: both scoped round-2 hypotheses now have
executable evidence, with the GUI/fault chain intact. **NO-GO:** replace main or
claim complete feature/performance superiority. This round stops because its two
questions have been answered, not because a time/LOC budget expired.

Uncovered/adoption blockers: the DSL requires `group_by ⊆ work_by`, unlike the
raw core's arbitrary/overlapping groups; axis-index grouping differs from
value-grouping; point-free aggregates, declared ragged dimensions and general
cohort/all-or-nothing rules are absent. Real providers, production distributed
leases, full optimizer proposal/region/operator semantics, calibration,
compiler/lowering breadth, exports and full product GUI remain outside the slice.
`state()`/GUI eagerly load a small run; long-run streaming/native-memory scale and
arbitrary instruction-level kills are unqualified. No omitted capability may be
silently removed during a future adoption decision.
