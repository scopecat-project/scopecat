# Lab Daemon

Scopecat uses one long-running local daemon as the durable writer for each lab
instance. GUIs and Python processes are clients, including notebooks that retain
local experiment closures.

```text
GUI ─────────────────┐
                     ├─ HTTP + SSE ─ daemon ─ SQLite + object store
notebook client ─────┘                    │
  └─ client executor ── fenced results ──┤
                         hardware batches ─ instrument worker
```

The daemon owns admission, run state, resource claims, executor leases,
configuration activation, the sample registry and immutable sample revisions,
exact run-to-sample bindings, event order, and durable writes. SQLite is the
transaction and ordering boundary. Large immutable content lives in a SHA-256
object store, and clients open neither store directly.

Sample creation and revision activation append `sample_created` and
`sample_revision_activated` events in the same SQLite transaction as the
registry mutation. Admission resolves each requested sample role to one exact
revision and stores that binding atomically with the accepted run snapshot.

## Durable run ownership

`ControlRun` is the lifecycle authority with four states: `queued`, `leased`,
`attention_required`, and `closed`. Relational `runs` and `run_outcomes` rows
form the lightweight `RunSnapshot`; `run_contents` is the separately paged,
filterable content catalog. Accepted requests, configuration snapshots, and
payloads remain immutable objects referenced from SQLite. Run views join control
state with the lightweight snapshot and load content explicitly, so listing a
long run history never materializes every run's content metadata.

Admission closes the complete instrument claim set before hardware access. It
also binds the expected provider and instrument-description fingerprints. The
executor then atomically acquires a renewable lease with a unique fencing
identity. Every measurement, coverage, point-plan, domain-job transition, and
terminal command presents that identity, so an expired executor cannot continue
writing.

Lease validation and each coarse domain-job transition or run-terminal commit
share one SQLite transaction. Heartbeats renew the lease and return durable
cancellation state. Routine renewals stay out of the project timeline; lease
grant or loss, cancellation, quarantine, and attention resolution remain
visible state changes.

Each run claims a flat set of registered instruments. Planning derives it from
residual host effects and the exact footprints of prepared domain executions.
The scheduler holds those claims across provisioning and all coverage blocks.
Interface, entity, channel, component, and property addresses remain exact
command data, while instrument claims provide exclusion against other runs and
direct sessions.

Host orchestration and a domain job runtime may use the same provisioned
instrument.
Property-level write authority, state requirements, and invalidations are
verified during planning; the daemon schedules only the resulting instrument
claims. The [execution semantics](execution.md#physical-authority-and-shared-state)
define that boundary.

## Instrument workers and effects

Concrete providers, transports, codecs, and drivers live in one spawned project
instrument worker. The daemon control plane holds a serializable contract
catalog and opaque connection handles. Worker providers receive per-device
bindings projected from the accepted configuration; they do not receive the
lab topology, parameter system, routing graph, or lifecycle policy.

Hardware effects cross the boundary as ordered typed batches. For each claimed
instrument the daemon-owned execution path:

1. acquires a fresh owner epoch and observes initial hardware state;
2. applies configured run-start policy to establish the baseline;
3. validates and lowers complete commands before invoking the batch;
4. invokes the driver and validates its typed receipt;
5. applies success or failure policy, gathers terminal readback, and retires the
   connection when required.

The driver ABI contains physical commands, not run, point, product, or retry
semantics. Payload bytes and returned arrays cross the process boundary as
hash-checked binary attachments; bounded JSON messages carry their typed
descriptors and ordinary receipt fields.

Whole-batch operation identities provide process-local duplicate detection and
receipt correlation. They never turn an unknown hardware outcome into a safe
retry. Unknown effects stop dependent execution, initiate the available abort
path, and quarantine resources whose final state cannot be established.

Domain runtimes submit device programs through the same run-scoped instrument
executor. They receive neither raw drivers nor connection handles and can reach
only the instruments authorized by the prepared execution. Target-specific
batch protocols, timing guarantees, DSP placement, and result realization are
owned by the target implementation and retained as compact execution
provenance, rather than duplicated in the daemon architecture.

Observed, baseline, and final instrument snapshots are durable run evidence.
Run summaries expose neutral change counts, affected instruments, and missing
readbacks; full snapshots are loaded for diagnosis or provenance. Scientific
datasets contain only values selected by the experiment return value or an
explicit returned `alias(...)`.

## Notebook execution and durable commands

`sc.open_project(...).connect()` returns the normal `LabClient`. It loads the
project's planning composition, resolves the accepted configuration, and asks
the daemon for the instrument contract catalog bound to that snapshot.

An invocation that contains local closures or interactive objects may retain its
transient `RunProgram` and pure computation in the notebook process. The daemon
still admits the plan, fences the client executor, owns instrument sessions, and
writes measurement checkpoints and the terminal outcome. The notebook therefore
remains a compute client rather than a second durable writer.

The same client exposes replayable events, cancellation, and attention
resolution through `lab.control`. Analysis records, parameter proposals,
acceptance decisions, and default changes also use daemon commands. Local Python
may calculate arbitrary candidates. Reusable `run.analyze(...)` steps and the
exploratory path below establish durable analysis publications; configuration
changes remain explicit daemon commands.

```python
context = run.analysis("fit")
dataset = context.measurements()
fit = fit_dataset(dataset)
published = context.result().fact("fit-score", fit.score).save()
```

`lab.config.accept(...)` can accept a proposal from that saved publication,
while `lab.config.set_default(...)` records an explicit default change.

A candidate may be used for one run without changing the default. That run
retains its producing run, analysis, proposal, base configuration hash, and
resolved candidate hash. Acceptance may be attributed to a person or a named,
versioned automatic policy.

## Cancellation and attention

Cancelling queued work commits a known `cancelled` outcome immediately.
Cancelling leased work stores an idempotent request. The executor observes it on
a heartbeat and stops at the next operation, hardware-batch, domain-job
transition, coverage, or normal-completion boundary.

An active blocking driver or provider call cannot be interrupted in the middle.
A resumable domain job first commits its returned checkpoint and then observes
cancellation before the next `resume`. Provisioned hardware follows normal
failure finalization before the cancelled outcome is committed. The runtime
first aborts the active operation, then runs the configured ordered safe
operations and safe-state patch when the instrument selects
`abort_then_safe_state`. A configured required safe state that is rejected or
cannot be confirmed quarantines the affected resource. If any finalization
action has an unknown outcome, the run is cancelled with indeterminate
certainty and affected resources remain quarantined. The run-hardware receipt
retains the ordered finalization actions and their returned metadata so
commanded safety can be distinguished from measured safety. A returned receipt
is projected into the run's durable instrument-state evidence before terminal
commit; empty action lists retain the previous evidence wire shape.

Cancellation and terminal commit are serialized by the SQLite writer:

- a completed terminal commit rejects a later cancellation request;
- a prior request converts a pending successful intent to known cancellation;
- actual failed or indeterminate evidence remains stronger than cancellation.

If an executor disappears, the daemon fences it, attempts the same configured
failure finalization against every provisioned instrument, and then discards
the worker connection. This is a best-effort emergency action: there is no
surviving executor to accept a terminal receipt, so the run still enters
`attention_required` and its resources stay quarantined even when every command
returned normally. Graceful daemon shutdown makes the same attempt. Process or
host loss cannot run software cleanup and remains the responsibility of device
watchdogs, output interlocks, and safe hardware defaults.

After externally reconciling hardware, an operator resolves attention through
the GUI or `lab.control.resolve_attention(...)` with an explicit disposition.
`close` commits an indeterminate failed outcome, abandons the remaining point
plan, and releases the claims. `continue` checks the exact accepted run-contract
fingerprint, including complete ordered-point and measurement-schema
fingerprints rather than their bounded presentation samples, releases the
reconciled claims, and returns the same run to the queue. Its next executor
lease creates a new execution segment at the durable global coverage watermark;
the lost segment remains an immutable indeterminate interruption.

Continuation deliberately does not claim that the Python workspace, imported
environment, or external hardware is unchanged. Durable measurement chunks are
attributed to their execution segment. For a static local run, a later executor
can select and append the remaining suffix from the durable coverage watermark;
the daemon validates the new fragment and combines all fragment appends into the
terminal run-level dataset identity. Adaptive and domain-target execution remain
control-plane continuation only because coverage alone does not identify their
safe external-effect position. The daemon discards the lost executor's
pending/live measurement state as soon as the lease supervisor fences it. Any
already durable measurement prefix, coverage watermark, execution segment, and
domain-job transition ledger remain inspectable, but none alone authorizes
replaying those external effects.
The high-level `lab.resume(...)` path reconstructs and validates the accepted
run contract before it submits the `continue` attention disposition. Calling it
therefore serves as the operator's explicit confirmation that external hardware
has already been reconciled; a contract mismatch leaves the run quarantined.

Diagnosis can distinguish an invocation with no observed outcome, a pending
provider checkpoint, and a terminal provider receipt. Invocation-only state
deliberately does not claim that the provider received `start`, and none of the
three states is replay authority.

A daemon restart immediately fences executors from the previous process rather
than trusting their remaining lease time. Process-local measurement state is not
reconstructed; seal, terminal commit, executor loss, and daemon shutdown are all
release boundaries for it.

## API and event stream

The daemon serves the bundled GUI and a versioned typed HTTP API. Run detail,
resource state, configuration history, measurements, and domain-job transitions
are exposed through bounded queries. The domain-job collection projects one
current `invocation_unknown`, `pending`, or `terminal` state for each observed
execution, while its transition subresource retains the ordered evidence. The
projection includes the complete durable invocation intent, is explicitly
diagnostic, and exposes no replay operation. Run-terminal domain evidence is a
compact ledger index rather than a duplicate array of those transitions.
Measurement control commands remain small JSON documents;
measurement ingest and the live latest-point response use schema-driven Arrow
IPC, while direct and run-scoped hardware receipts use typed JSON headers with
binary measurement-array attachments. Numeric acquisition results therefore do
not expand into JSON lists between the instrument worker, daemon, and executor.

Server-sent events replay the same durable globally ordered event log used by
the API. On initial connection or reconnection, clients refresh canonical
queries and continue from an event cursor. The separate health poll represents
reachability only; it is not an observation stream or source of run state.

## Configuration and storage ownership

A project `scopecat.toml` points separately to its lightweight daemon
bootstrap, full project-worker application, and instrument worker backend:

```toml
[lab]
bootstrap = "my_lab.application:create_bootstrap"
application = "my_lab.application:create_application"
instrument_backend = "my_lab.backend:create_backend"
```

The bootstrap may construct an initial `ConfigProfileSnapshot` in Python. The
daemon loads only that lightweight composition and invokes its config factory
only when the registry is empty. Procedure, schedule, calibration, publication,
and notebook system callbacks belong to the full application loaded by the
project worker or notebook process, not by the daemon. Once
bootstrapped, immutable registry entries and their explicit activation
generations are authoritative. Editing Python source does not mutate an active
entry; publishing a changed snapshot is an explicit CLI or notebook action.

The backend entry is imported only by the instrument worker. The daemon sees its
serializable catalog and opaque handles.

| Owner | Contents |
|---|---|
| User project and Git | Experiment, system, and configuration code; `scopecat.toml`; exported snapshots |
| Lab daemon | Requests, run state, events, measurements, analysis, proposals, immutable configurations, activation history |
| GUI and notebooks | Views, commands, and transient client-planned computation |

One process-owner lock prevents two daemons from opening the same lab instance.
Within that boundary, resource claims coordinate runs and direct sessions,
leases reclaim abandoned clients, fencing identities reject stale executors,
and SQLite transactions make durable commits atomic.

The default transport is a same-user local control plane bound to loopback with
restricted host names. Remote or multi-user operation requires a separate
authenticated security boundary.

The single daemon, SQLite writer, and local object store define the current
single-lab scalability boundary. Durable large content remains in binary
objects. Opaque command payloads use an operation-scoped in-memory spool and
are released after completion or owner termination, so transient waveform
transport does not become permanent run history. Execution commits at batch or
checkpoint granularity; interactive reads use bounded pages and summaries. The
[scalability benchmarks](../scalability.md) measure this boundary.

### Current-store checks versus offline inspection

`SQLiteProjectStore.bootstrap()` is an initialization operation for a quiescent
store. The daemon owns its process lock before calling it. Its foreign-schema
preflight uses `inspect_project_schema()` so rejecting an unsupported store does
not create source sidecars, change journal mode or initialize object directories.
That offline probe requires a quiescent source: copying a database and WAL is not
a supported snapshot of a live writer.

Known current stores use `SQLiteProjectStore.schema_version()` instead. It reads
schema presence and version inside one SQLite read transaction, so concurrent WAL
commits/checkpoints cannot make those queries observe different snapshots. This
path participates in ordinary SQLite locking and may create sidecars; it is not
an alternative nonmutating probe for foreign stores. Test service compositions
reopening their own initialized databases use this path rather than re-running
bootstrap while another composition is still alive. No global version cache or
implicit migration is involved.

See SQLite's [live-copy warning](https://www.sqlite.org/howtocorrupt.html) and
[WAL read-only constraints](https://www.sqlite.org/wal.html#read_only_databases).
