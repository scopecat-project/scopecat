# Experiment Execution Semantics

This document defines the cross-cutting contract between experiment authoring,
planning, domain compilation, and execution. Exact field and method behavior
belongs to the types that implement it; this document explains how those types
fit together.

## Program lifecycle

```text
@module / @experiment
    | close symbolic definitions
    v
program model           reusable graph, effects, products, and point plan
    | elaborate and verify without lab configuration
    v
VerifiedLogicalProgram
    | specialize against one accepted configuration snapshot
    v
BoundPlan               logical proof plus transient bound facts
    | materialize host effects and prepared target executions
    v
RunProgram              closed residual effect program
    | execute once through fenced effects and correlated job transitions
    v
logical measurements and durable run records
```

The authoring package owns Python UX. `scopecat.program` owns the reusable
symbolic model consumed by compilation. `BoundProgramFacts` belongs to one
specialization pass, and `RunProgram` is the executable form for one accepted
run. Physical batching preserves logical point, product, and result identity.

Check, preview, and run specialize the same accepted semantics. Check and
preview stop before provider effects; run interprets the closed effect program.

## Run semantic boundary

A run is not merely a collection of rows that can be concatenated afterward.
It is one admitted scientific comparison under a single accepted configuration
snapshot and one closed execution contract. A point belongs in the same run
when all of the following remain true:

- every possible point program stays inside a statically declared resource,
  effect, result, and bounded-work envelope;
- every result has one stable typed schema, including acquisition-local axes
  such as shot, capture, round, cycle, entity, or sample;
- logical point order, state carry, reset policy, and named recovery groups are
  explicit enough to reproduce the comparison;
- splitting physical jobs at any legal cut does not change the demanded logical
  results; and
- execution does not publish or activate a new persistent configuration that
  changes the meaning of later points.

The resulting hierarchy is:

```text
procedure
  run: accepted configuration, authority, point design, result contract
    point recovery group: preferred scheduling and restart unit
      design row: one coordinate identity
        real-time trial: target-owned shots, rounds, and bounded feedback
```

An execution segment is deliberately absent from this semantic hierarchy. It
is immutable evidence for one interval of executor ownership. A resumed run may
have several execution segments while retaining the same run, point, and group
identities.

Ordinary sweeps, paired target/reference rows, tomography bases, bounded
point-dependent circuit families such as RB or XEB, repeated readouts, and
finite target-local feedback can therefore remain one run. Their rows need not
have identical pulse trees, but the family must have a finite declared envelope
and every selected member must verify against it. A scan followed by fitting and
configuration publication, an optimizer that intentionally changes the
accepted configuration, durable retries with policy decisions, or long-lived
monitoring instead belongs in a procedure composed from multiple runs.

Point recovery groups express comparisons that should remain close in execution
and be repeated together after an interruption. Core may shorten a compiler
candidate or split a host-state region at any point boundary. It publishes
measurement coverage and advances the durable watermark only after a complete
group, even when physical traversal visits canonical point ordinals out of
order. A smaller target capacity therefore splits physical work without
weakening the authored recovery policy.

This boundary keeps the common authoring path compact. A static program needs
no extra declaration. Point-dependent elaboration declares only its family
envelope; result-local repetition stays on typed local axes instead of becoming
anonymous point columns; and a comparison-sensitive scan opts into a named
coordinate grouping. The corresponding cost is intentional: unpublished result
buffering is bounded by the unfinished group, while physical batching freedom
is unchanged. Truly indivisible realtime sequences remain target-local axes
inside one point rather than being modeled as a scan grouping, and a target
that cannot implement a requested real-time construct rejects it
during compilation with a capability finding.

## Authoring ownership

A reusable module combines a pure typed graph with an ordered effect sequence.
Composing it scopes all resource, value, product, and effect identities to that
occurrence and places the effects once.

The public model has four boundaries:

1. `@module` defines a reusable fragment. Its Python return value is its
   composition result. `context.use(...)` places the occurrence and returns
   that typed result.
2. `@experiment` defines a complete invocation. `Input[T]` parameters become
   typed runtime values; ordinary Python parameters rebuild the structure.
3. A domain call is an ordered experiment effect. It returns products owned by
   that occurrence and may coexist with host-device work and derived-measurement
   compute.
4. The `@experiment` return value selects ordinary durable results. `Result(...)`
   annotations add stable identity, namespace, role, or metadata policy, while
   `alias(...)` handles the uncommon additional destination. These demanded
   roots determine the live compute and observation-stage graph.

A bare experiment uses its Python function name as its project-scoped technical
identity. An explicit `id=` distinguishes definitions when needed, while
`lab.run(..., name=...)` supplies the durable operator-facing name.

Every invocation resolves to one `PointPlan`: a grid or ordered point cloud,
repeat policy, and one `PointSchedule` that composes base traversal with an
optional grouping. Invocation edits replace or adjust that plan without
changing the experiment definition. An optional `AdaptiveDomainPlan`
pairs that initial prefix with a process-local domain optimizer and a hard point
limit.
The
[experiment dataflow model](../../concepts/experiment-dataflow.md#choose-and-edit-the-point-domain)
is the canonical task guide; the `PointPlan` docstring defines its exact local
contract. The durable admission records only optimizer identity and bounds; the
closure stays with the local runner. Static coordinates outside the adaptive
axis set partition the plan into stable regions with independent revisions,
observations, budgets, and stop state. A compact fragment proposal is normalized
into point candidates only at compilation; the whole group is accepted with
consecutive logical ordinals or rejected before effects. Measurement-dependent
selection across runs still belongs to a future workflow model.

### Local compute boundary

Python compute functions currently remain local closures owned by the runner
that planned the invocation. Preview reports a local diagnostic identity,
placement, and captured nonlocal names; none is a durable replay or remote
execution contract. Scopecat does not pickle closures or infer portability from
a function's module and qualified name, because either choice silently captures
environment and dependency state. Scientific dependencies should instead enter
the explicit typed input graph.

Completed-run analysis deliberately has a different authoring boundary. Inside
an analysis step, `context.trace(fn=fit, dataset=dataset)` eagerly runs ordinary
Python over the durable snapshot and optionally retains execution evidence. It
returns the native value; the author separately chooses whether to publish it
as a fact, dataset, table, figure, or artifact. If the published content exactly
matches the traced result's content and codec, a fact, dataset, or artifact is
linked to that execution automatically. A first-party native-dataset
normalization with field mapping instead records a typed derivation from that
result. Views remain explicit projections of a published dataset.

Every traced input is named and content-identified independently; JSON-safe
inline values are retained beside dataset targets. Analysis executions have no
experiment placement: they are not nodes in acquisition and do not mutate the
source measurement dataset. Full-dataset ordinary Python is the current
analysis path. A future streaming workflow may reuse publication and execution
evidence, but must own its cursors, checkpoints, and finalization state
explicitly.

Logical resource ports describe the interfaces and entities required by a
reusable definition. The accepted configuration maps those requirements onto a
finite catalog of physical endpoints. This keeps experiment definitions
independent of instrument names and channels while making every physical
selection deterministic and reviewable.

Product declaration, production, and durable selection remain distinct:

1. A module or domain call declares product identity, type, and shape.
2. An acquisition or domain execution produces those products at an exact
   effect position.
3. The experiment return structure chooses which products and values become
   dataset records; explicit recording only refines that policy.

## Specialization and lowering

Point composition remains symbolic through verification. Specialization binds
accepted inputs and configuration values, applies point-plan edits and parameter
overlays, folds pure computation, removes undemanded work, and leaves a residual
host/domain program. External reads, writes, acquisitions, and invocations occur
only while interpreting `RunProgram`.

Linking preserves one canonical random-access logical point sequence without
eagerly constructing generated rows. Range and center/span axes retain their
compact sources; explicit-value axes retain their authored values. Parameter
overlays, host lowering, entity selection, and domain projection evaluate the
selected ordinals for the current bounded coverage batch. The
[scalability benchmarks](../scalability.md) track the remaining point-count
costs; the stable semantic promise is that physical partitioning does not change
logical point identity or results.

The point schedule may declare one named, coordinate-defined recovery grouping.
The listed coordinates may vary within a group; all other coordinates form its
key. Scheduling first resolves the base forward or snake traversal, orders
groups by the first appearance of their key on that path, and preserves path
order among members of each group. Group sizes may differ and singleton groups
are valid. The grouping adds no dataset coordinate and imposes no divisibility
rule.

A recovery group is deliberately not a hardware batch. Domain limits, host-state
regions, and local coverage windows may split one group at any point boundary.
Measurements remain buffered until the complete group has executed; only then
may durable coverage advance. An interruption therefore restarts the unfinished
group even when earlier physical batches from that group had completed. Durable
coverage retains a canonical point-prefix watermark, while the durable recovery
ledger also records exact completed groups with their schedule fingerprint and
output hashes. Static continuation validates those records and skips completed
groups even beyond the watermark. This does not make an interrupted group
complete, and does not enable continuation of an already-started domain target;
target-aware recovery remains a separate contract.

Measurement ingest distinguishes received records from durable records. The
existing coalesced coverage checkpoint flushes measurements, commits exact group
output proofs, then advances the logical prefix. The server rejects a new prefix
whose actual logical points have not been durably acquired; acquisition count
alone is insufficient for reordered or repeated points.

Before another hardware batch, the client transfers pending completed output to
the daemon without forcing a durable write per shot or point. If a later hardware
result is unknown, the daemon attempts to retain that received tail before
revoking the executor lease. Retention failure is a separate persistence problem;
quarantine and fencing still occur. A process crash can still lose merely received
records. No unknown hardware operation is replayed to reconstruct output.

The bounded measurement preview includes durable acquisitions beyond the safe
coverage prefix: fixed group output wins, otherwise the latest acquisition is
shown. This preview neither seals a dataset nor certifies a recovery group.
Catalog readers and stable logical pages continue to use their published
projection. A retained tail therefore does not authorize continuation or make
terminal persistence confirmed.

One `ExperimentSystem` owns one domain compiler. The compiler may internally
route supported dialects or invoke a lower-level target compiler after resolving
inputs. Planning calls `prepare_batch` once per candidate window and receives a
compiler-owned `DomainBatchCandidate`. That candidate retains shared lowering
or packing work while closing prepared executions containing the exact target
artifact, point/product mapping, physical authority, and target job invocation.

Planning asks the domain compiler for a small initial candidate maximum before
any point-local inputs are resolved. For each candidate, the compiler inspects
the complete request and returns a candidate with the length of its largest
compatible prefix ending at a point boundary. Core may shorten or split that
prefix to align host-state regions or another domain call, then asks the same
candidate to close each exact final subrange. Group boundaries are preferred
candidate ends when capacity permits, but are not legal-cut restrictions. Every
prepared execution reports the maximum candidate point count for the following
window. The compiler may derive compatibility and
continuation feedback from aggregate payload bytes, channels, samples, shots,
device entries, or another target-owned limit. Core still schedules contiguous
logical points and uses the minimum prefix or feedback when a window contains
multiple domain jobs. This keeps launch preparation bounded without forcing a
target to reserve one worst-case resource unit for every point or repeat the
candidate's lowering work. A local-only run uses a small initial window and
bounded follow-up windows.

Each target batch is one bounded coverage window. Host lowering forms stable
regions inside the window from adjacent points with equal, statically known
desired state. It reconciles that state at a region anchor and suppresses an
identical anchor at the next window. A physical state change, invocation,
acquisition, or payload-backed write creates a point boundary. Pure host
computation creates no hardware boundary. A host-state boundary may split a
recovery group without changing its restart semantics.

Consequential stages retain author order inside each bounded region. Coverage
windows execute in point order; there is no promise that one stage remains
contiguous across every physical batch in a scan. Hardware behavior that truly
requires indivisible realtime adjacency belongs inside one logical point as a
target-owned shot, phase, round, or feedback axis, while stable peripheral state
may remain a host effect around each region.

Program inputs configure runtime semantics. Compiler inputs configure lowering.
Both resolve from the same accepted snapshot and point overlays before the
compiler is called.

## Physical authority and shared state

Admission claims the complete static instrument footprint before any prepared
batch exists. Domain compilers declare their exact instrument ids. Local target
preparation contributes every structurally compatible route candidate; later
point-local entity selection narrows actual operations but cannot expand
authority. A run may therefore lease an unused local candidate instead of
performing an O(total points) route scan before admission. Each claimed
instrument is leased once for the run and provisioned through one worker-owned
driver session. Host and domain stages may use different properties of the same
instrument in their declared region order.

The scheduler owns instrument-level exclusion. Routing retains more precise
interface, entity, channel, component, and property addresses as command and
provenance data. It does not turn those addresses into a hierarchical lease
system.

Each prepared domain execution declares:

- the instruments it may execute against;
- exact setup and realtime property write footprints;
- exact host-provided property values required before realtime execution; and
- properties whose previous state evidence it invalidates.

Planning rejects host and domain writers that claim the same property address.
Disjoint properties on one instrument are ordinary. Requirements are checked
against preceding host coverage and reconciled after setup immediately before
realtime execution. Invalidation removes knowledge without granting write
authority or asserting a replacement value.

Physical coupling is expressed where it is known. A device or lab adapter may
expand a named policy into concrete requirements and invalidations for coupled
properties. This lets a target invalidate a guard offset or crosstalk-sensitive
neighbor without claiming permission to choose its value. The core operates on
the resulting property addresses rather than a generic crosstalk graph.

A target authority is the realtime execution island. External signal-chain
devices such as LOs may remain ordinary host-controlled instruments. Lab
lowering can read their reviewed setpoints to derive signed IF values without
granting the target runtime permission to program them. Common experiments
reconcile stable LO state around a domain segment; specialized LO-sweep
experiments author the explicit host schedule.

Physical routing is a pure projection of the accepted snapshot. Point-local
entity selection may choose among its configured routes, while live
availability and provider failure never select a replacement route. Path-changing
devices such as switches and probers are explicit effects, so their state is
part of the reproducible plan.

## Completion, failure, and evidence

Every executor lease owns one durable execution segment. The initial execution
creates ordinal zero; an idempotent retry of the same live executor start returns
the same segment. A terminal run commit closes the segment with the run result
and its completed logical-point count. Lease loss closes it as an indeterminate
interruption before the executor token is fenced. Segment records retain the
accepted run-contract fingerprint and the global coverage interval, so later
continuation work has an immutable ownership boundary without adding a field to
every measurement record.

A point recovery group and a durable execution segment are different boundaries.
Grouping belongs to the accepted run contract and controls scheduling preference
and result publication, not physical partitioning. Segments are immutable
evidence for one executor lease and may contain many groups or part of the final
unfinished group. Because durable coverage is stored as a canonical point
prefix, a segment watermark advances only when completed points close that
prefix. Exact recovery-group proofs are stored separately, so a completed
reordered group beyond the watermark remains resumable without pretending the
intervening points completed.

Execution segments are immutable evidence rather than transparent process
resume. After hardware reconciliation, explicit continuation can return an
attention-required run to the queue; the next executor lease creates a new
segment at the durable global coverage watermark instead of reopening the
previous process owner. The accepted run contract is checked, but Scopecat does
not claim that the Python workspace or environment is unchanged.

The canonical measurement schema remains run-owned, while durable Arrow appends
are grouped into segment-owned fragments. Initializing a dataset in a new
segment creates one fragment at the next physical acquisition index. Each
existing chunk records its owning segment and retains acquisition order;
individual measurement records gain no segment field. A normalized logical
projection maps point identities to selected acquired rows. Run-level paging
sorts that projection by global point index, independent of fragment boundaries
and retries.

Before acquiring instruments, the interpreter reads the durable global coverage
watermark and exact recovery-group ledger. A continued static local run
materializes the remaining work, skips independently completed groups, and
initializes its ordering buffer at the watermark. Its seal identifies only the
new segment-owned acquisition fragment; the daemon verifies that fragment,
completes any non-static logical selections from the latest acquisition per
point, and derives the final run-level dataset identity in logical point order.
This avoids replaying completed point effects and avoids re-hashing array
payloads in the executor.

Automatic suffix continuation is deliberately narrower than control-plane
continuation. Adaptive runs and domain-target runs are rejected before
instrument acquisition because their exact program position also depends on a
durable proposal or external-job transition. They require a future resumable
program-position contract rather than guessing from measurement row count.

`lab.resume(run, invocation)` is the user-facing re-entry boundary. It replans
against the accepted config, requires the reconstructed request and plan summary
to reproduce the accepted run-contract fingerprint, and compares any initialized
measurement schema before it resolves an attention-required run or acquires a
new executor lease. The contract contains hashes of every ordered accepted point
and the complete durable measurement schema; the bounded point samples and
record identifiers remain presentation fields rather than recovery evidence.
The check is a durable shape and authority check, not a Git or environment
reproducibility claim.

Execution validates typed transitions for each consequential external
invocation. A domain job starts with its deterministic execution key. A
synchronous target returns terminal receipt/result evidence directly. A target
backed by an external job may instead return a JSON-serializable checkpoint
containing the provider job identity, a strictly increasing revision, an opaque
resume token, and inspectable progress, then implement `resume` until it returns
a terminal result or negative receipt. Core rejects transitions that change the
execution key or job identity, or replay a stale revision.

For a daemon-backed execution, selected invocation, checkpoint, and terminal
outcomes form one durable transition ledger. A write-ahead invocation transition records
the complete `DomainInvocationIntent` together with its deterministic execution
identity before domain setup, state reconciliation, or provider `start` can
perform effects. Target intent, compiler and capability fingerprints, artifact
identity, and execution summary therefore remain available without consulting
the transient compiler object. Every valid checkpoint is then synchronously
committed under the current executor fence before core calls `resume`. Every
terminal receipt, including a synchronous target's receipt, is committed before
result realization or provider failure escapes the domain effect. Invocation
and terminal writes are each idempotent by run and execution key; checkpoint
writes also include the revision. The ledger requires invocation to be first
and rejects changed job, node, or point identity and any transition after
terminal state. Batched policy retains the same complete sequence with a bounded
loss window rather than a transaction at every boundary.

Low-cost synchronous targets may instead select `abnormal_only`. Their ordinary
completed calls leave no lifecycle rows: measurement coverage and the compact
terminal counts are the success evidence. A `not_executed` or `unknown` receipt
persists the complete invocation and terminal outcome together, while an
interruption without a receipt persists invocation-only state. A completed
receipt is also retained when host result realization fails afterward. A
checkpoint is never discarded: observing one first promotes that execution to
the complete ledger, commits the invocation and checkpoint before `resume`, and later retains
its terminal receipt.

If invocation persistence is unavailable, no domain setup or provider call
starts and the failure is known. If checkpoint persistence is unavailable, the
pending provider job is not advanced and the run becomes indeterminate. A
cancellation observed during the write likewise retains the checkpoint and
stops before the next transition. If terminal persistence is unavailable, the
observed receipt still determines provider certainty and realization does not
start, but the terminal summary marks its detail ledger incomplete rather than
claiming that the exact receipt became durable.

Under a policy that retains it, an invocation without a later transition means
setup or the provider call may have been interrupted; it does not prove that
`start` reached the provider. A
checkpoint proves the last observed state was pending, while a terminal
transition proves the provider outcome was known to the executor. These facts
survive loss before the run-level terminal commit, but none alone grants replay
authority.

The daemon folds those facts into one bounded current-state row per observed
execution: `invocation_unknown`, `pending`, or `terminal`. This is a diagnostic
projection rather than a recovery policy, so it has no `recoverable` flag and no
resume command. An empty projection means that no invocation became durable; it
may be the intentional result of successful `abnormal_only` jobs and does not
prove that the client-owned program contains no domain job. Safe continuation
for non-static execution still requires an exact program position, an owned
instrument session, a reconstructed measurement sink, and any result payload
needed after a terminal receipt.

The run-level `DomainExecutionEvidence` is only a compact terminal index: target
ids, transition policies, and aggregate attempt, checkpoint, receipt, and status
counts plus a `detail_complete` flag. The flag means every detail selected by
those policies became durable; it does not promise one ledger row per successful
attempt. The terminal model does not copy every intent, checkpoint, and receipt
into a second terminal JSON document. Detailed diagnosis pages the transition
ledger, and the current-state projection carries the complete invocation beside
the latest retained transition. Consequently a fully audited dense sweep grows
the ledger by job, while `abnormal_only` grows it by exceptional job and the run
terminal model remains bounded by target and policy diversity.

Notebook diagnostics use the run facade rather than the executor transport:
`run.domain_jobs(limit=..., before=...)` pages current projections and
`run.domain_job_transitions(limit=..., before=...)` pages the retained timeline.
These are read-only evidence surfaces; exposing them on `RunHandle` does not add
resume or replay authority.

These durable transitions make interrupted provider state inspectable after
executor or daemon loss, but do not by themselves authorize continuing the run.
Daemon restart fences the instrument session, process-local measurement writer,
and original effect program. Reattaching those three owners safely is separate
from storing a resume token or terminal receipt. Partial target results also
remain a later result partitioning change; synchronous targets still return
terminal evidence without emulating an asynchronous provider.

Outside that domain-job sequence, execution does not maintain a durable
transition ledger for normal per-effect progress. Measurement prefixes,
coverage checkpoints, hardware-unknown outcomes, and the terminal result remain
the recovery boundaries. Effects have three semantic outcomes:

- **completed**: validated evidence proves the effect completed;
- **rejected**: evidence proves the effect did not occur; and
- **unknown**: hardware may have changed without correlated completion.

Unknown effects stop dependent execution and are never silently retried. Stable
operation identities provide correlation and duplicate detection; they do not
authorize retrying an unknown write. A terminal domain receipt refines this
boundary with `completed`, `not_executed`, and `unknown`; its exact evidence
requirements live on `DomainExecutionReceipt`.

`experiment.on_success(...)` is an authored state transition after complete
point coverage. Instrument configuration then applies its success action and
collects terminal readback before release. Failure and known cancellation abort
the instrument and may execute its configured safe operations followed by its
safe-state patch while it remains commandable. Required safe finalization keeps
the physical access domain quarantined after a known rejection or missing
confirmation. Ordered returned actions are retained with durable instrument
state evidence. The `InstrumentSpec` docstring owns the precise lifecycle order.

Operator cancellation is durable daemon control. Queued work can stop
immediately; executing work observes cancellation at effect, coverage, job
transition, and target-owned hardware-batch boundaries. It still cannot
interrupt a blocking provider call in the middle. Cancellation, terminal
commit, and resource quarantine are described in the
[lab daemon model](daemon.md).

Instrument state snapshots, measurement acquisition logs and logical
projections, point-plan decisions, and terminal outcomes are durable run
evidence. Individual normal hardware calls remain typed execution details.
Physical values become dataset columns only when the experiment explicitly
records a scientifically meaningful value. Output enable, for example, is
ordinary requested state and may itself be varied by an experiment.

Host acquisitions and domain executions feed one logical measurement stream.
Every physical result maps completely to logical points and product-use
identities before entering that stream. The terminal outcome and any explicit
hardware-unknown evidence are the source of truth for operator reconciliation.

## Semantic invariants

- A run uses one accepted request and identifiable configuration snapshot.
- Logical point identity and coordinates are independent of physical batching.
- Host and domain placement observe the same inputs and parameter overlays.
- Pure computation may be folded, shared, or hoisted without changing its value
  or its host-versus-observation availability semantics.
- Consequential effect stages retain their declared order inside each bounded
  coverage region.
- Stable state may reconcile once per region; observable or varying effects
  remain boundaries.
- Legal lowering and physical partitioning produce the same demanded products.
- Every physical selection is deterministic and retained in provenance.
- Every physical result maps unambiguously to logical point and product-use
  identities.
- Prepared target write authority is exact and does not expand from read-only
  dependencies or invalidations.
- External effects require current fenced executor authority.
- Unknown hardware effects are never silently retried.
- Provider failure never triggers implicit route failover.
- Abort, disconnection, quarantine, and terminal outcome remain explicit.

Tests should assert these laws rather than transient compiler fields, cache
behavior, or incidental batch counts.

## Host state writes and domain residency

Successful host ApplyState, Invoke, and Collect blocks invalidate opaque domain
residency on the addressed instruments. Property reconciliation is not proof that
target-owned program memory survived a write. Other instruments retain their
residency. This is conservative even when a state assignment reconciles as a no-op.
Domain-owned state requirements remain the target's responsibility and do not
independently revoke the target's setup proof. Failed blocks terminate execution;
this policy does not authorize resuming an uncertain physical state.
