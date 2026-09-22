# Durable procedure automation

Scopecat's first automation boundary coordinates a small number of related runs,
analysis publications, exact configuration publications, and saved-entry
activations without moving
user-authored Python into the daemon.
It is intended for one-lab calibration procedures that must survive a notebook
or worker restart. It is not yet a scheduler for a whole device.

## Ownership split

The project application owns a versioned `ProcedureRegistry`. A procedure is a
deterministic imperative Python function with a typed, JSON-encoded initial
intent. The daemon stores only its definition reference, intent, current state,
step attempts, leases, and typed output references. A project automation worker
loads the same registry and replays the function from its beginning.

The definition fingerprint covers the registered function source and intent
schema. Analysis invocation fingerprints additionally cover their bound
arguments, defaults, and nonlocal closure values. Durable procedure,
calibration, and automatic-publication callbacks reject nonlocal captures;
callback configuration must enter typed intent, definition, or reference
fields. Authors must still bump the corresponding definition or policy version
when a transitive imported dependency changes; a source fingerprint is not a
Python environment lockfile.

The daemon must never pickle, import, or deserialize a procedure closure. The
worker must never replace the authoritative run, analysis, configuration, or
procedure stores. A procedure step invokes those existing services and records
only the resulting durable reference.

```text
project procedure function
        |
        v
lease-fenced ProcedureContext
        |
        +-- run step ----------> RunOutputRef
        +-- analysis step -----> AnalysisPublicationOutputRef
        +-- config publish ----> ConfigPublishOutputRef
        +-- config activation -> ConfigActivationOutputRef
        `-- interpretation ---> InterpretationOutputRef
                 |
                 v
      existing authoritative services
```

## Replay contract

A submission is unique by `(procedure_id, request_key)`. Its content hash covers
the exact definition version and fingerprint plus the canonical initial intent.
Reusing the key with different content is a conflict.

A step is unique within a procedure run by its stable key. The persisted intent
records the operation kind, exact upstream output references, and an intent
hash. Replaying the same key and intent returns its recorded output. Reusing the
key with different content is a conflict and does not execute an effect.

A `context.run(...)` child inherits the procedure's exact scientific subject and
setup when no explicit child binding is supplied. Parameter candidates retain that
subject and setup but record their own configuration content hash; candidate source
provenance remains independently checked. A bound procedure cannot change its
subject or setup; start a new procedure for a different setup. Explicit bindings
are never repaired to fit the supplied configuration, and daemon admission still
validates retained target/sample evidence and the parent's exact subject/setup.

The daemon derives a stable operation ID from the procedure run, step key, and
attempt. A child run uses it as both the run submission ID and executor intent.
Analysis uses a procedure-specific logical key and first reopens the exact `r1`
record after a possible response loss. The publication subject, analysis step,
and declared durable upstream owners are checked before the reference is
checkpointed. Output-level lineage remains in the immutable analysis record
rather than being duplicated in the procedure step.

An activate-entry step passes the same stable operation ID to the configuration
command. The configuration registry commits that operation's canonical intent
and activation generation atomically with the mutation. After transport loss,
the worker reopens that exact operation before checkpointing a
`ConfigActivationOutputRef`. The target entry and expected generation are
explicit step intent; the worker never refreshes the generation during replay.

A config-publish step uses the same contract for a new immutable revision. Its
intent pins the exact candidate proposal, project-owned decision fact, target
entry, and expected generation. The candidate analysis and verification
publication are exact step inputs. Proposal approval, entry creation, activation,
and the operation receipt share one transaction; a lost response is reconciled
through the exact publish-operation lookup before the worker checkpoints a
`ConfigPublishOutputRef`.

Workers hold renewable leases. Every state change is fenced by the lease token
and expected revision; heartbeat renewal does not change the business revision.
An expired worker may be replaced, but it cannot checkpoint late work. The
notebook client therefore uses a process-local worker identity by default;
another process waits for lease expiry or supplies its own operator-managed
identity rather than impersonating the previous worker.

An interpretation step is a normal, declared pause for scientific judgment,
not an execution fault. The procedure publishes a title, instructions, exact
upstream output references, open JSON metadata, and an `AnalysisFactSchema`-
compatible structural response contract, plus an optional schema-valid editor
template. The daemon atomically moves both the step and procedure to
`waiting_for_input` and releases the worker lease. A
human, AI agent, or service submits one identified structured response. The
daemon checks it against the retained structure, records the response and its
server timestamp in an `InterpretationOutputRef`, and returns the procedure to
`ready`. Replay decodes the same response into the local Python type.

Submitting a judgment does not execute the next step in the same control-plane
request. It returns the procedure to `ready`: a notebook may resume it
explicitly, while a configured resident project worker may claim it on its next
poll. Labs that run a resident worker should therefore treat recording the
judgment as authorizing the declared continuation. The response reference can
be an input of later run, analysis, or configuration steps.
`waiting_for_input` is distinct from `attention_required`: the former is
expected experimental work; the latter quarantines an unknown or unsafe
execution outcome.

A known failure inside a step records a failed attempt and closes the procedure
as failed; a rejected verification therefore fails the candidate-publication
step without changing the registry. A validation failure between steps closes
only the procedure because every preceding effect is already known. An unknown
child-run, publication, or configuration-operation outcome instead moves the
owning step and procedure to `attention_required`; each domain service keeps its
own authoritative result. Restart does not retry the effect or execute later
steps. `attention_required` is a durable quarantine. After reconciling the
authoritative domain state, an operator may explicitly call
`procedure.retry_attention()`. The exact quarantined attempt remains immutable,
the procedure returns to ready, and replay creates the next numbered attempt.
All attempts of one stable `(procedure_run_id, step_key)` share one side-effect
operation id, so a child run or domain operation is looked up idempotently rather
than duplicated. Run-level attention without an owning step is not covered by
this command and remains an operator concern.

## Present supported slice

The maintained reference `drag_branch_calibration` demonstrates a bounded request
over explicit targets and independent parameters/setup:

1. freeze the complete target set and destination branch in typed intent;
2. acquire and fit each target from the same saved base;
3. compose exact proposals through `combine_parameter_candidates()`;
4. remeasure every requested target under the combined candidate;
5. retain checked, missing and rejected targets in one joint policy decision;
6. publish to the captured branch with `publish_parameter_candidate()` only after
   positive complete verification.

The branch publication step retains its accepted head and source/decision proof.
It never activates equipment or a shared default. A completed step replays without
reopening evidence; a committed publication with a lost response is retried by
the same command and recovers its historical receipt even after later branch edits.
Single-target requests use the same path without a synthetic merge.

The old DRAG default-publishing and verify-only cohort procedures, semantic merge
policy and automatic-publication registry have been retired. Peer-insensitive
freshness and implicit subset reruns are withdrawn, not mechanically translated.
Generic full-config cohort services remain legacy implementation debt, documented
below for their existing framework callers/tests.

The procedure replay layer deliberately has no DAG representation, automatic
retry policy, cron trigger, or dynamic loop checkpoint. A linear Python
procedure with durable step checkpoints remains the unit of execution; the flat
bounded cohort admission layer described below does not add another run engine.

## One-shot schedules and project automation workers

`ProjectAutomationWorker(lab.procedures)` dispatches submitted procedures and due
schedules directly, with optional interval planning. Legacy cohort evaluation and
finalization are no longer worker phases or constructor options.
New parameter-branch procedures carry their publication within their own ledger.

A durable procedure schedule freezes one exact definition reference, canonical
intent, and UTC due time. Materialization derives a stable request key from that
whole schedule and atomically admits one `ProcedureRun`. Recreating the same
schedule is idempotent; changing its exact content under the same schedule ID is
a conflict. Due-time processing never rebuilds intent from the active
configuration or the worker's current Python environment.

`scopecat automation work PROJECT` runs the project-owned Python worker. The old
`--working-point` option and application-level cohort/publication registries are
retired. Scientific targets and publication destinations belong to each explicit
procedure request. Workers remain processes separate from the daemon. Each
bounded CLI cycle evaluates registered interval planning, materializes already-frozen
due schedules, and asks
the daemon for oldest-first runnable procedures matching the worker registry's
exact definition references. A live lease or acquisition race does not stop
later work. A definition unavailable in this worker is not returned by
capability-filtered discovery and is not changed to operator attention; another
exact-version worker can claim it later.

Due discovery is keyset-paged in durable insertion order, oldest insertion
first; `due_at` remains a server-clock eligibility filter rather than the sort
key. Each traversal freezes the current highest durable sequence, so sustained
new arrivals cannot make that scan infinite. Reaching the high-water clears the
cursor and the next traversal wraps to the beginning, where it can observe a
lower-sequence schedule that became due after the prior cursor passed it. A
still-pending materialization conflict therefore cannot pin all later due
schedules behind the first bounded page.

Before due discovery, the same project automation worker evaluates an immutable
`ProcedureScheduleRegistry`. Its first recurring trigger is deliberately narrow:
an aware UTC anchor plus a fixed positive `timedelta`. Evaluation selects only
the latest due ordinal in constant time. It neither expands every missed slot
nor creates a future occurrence before its due time. Cron, civil-time zones,
DST policy, and catch-up-all remain outside this contract.
There is no durable series cursor yet: if the project wall clock moves backward,
the latest-only calculation can select an older ordinal that was never
materialized. Exact IDs still deduplicate ordinals that do exist, but deployments
must provide a trustworthy UTC clock until an explicit clock-watermark policy is
added.

Each selected interval slot becomes an ordinary exact one-shot schedule. Its ID
depends only on the logical schedule ID, schedule version, and ordinal—not on
the current procedure fingerprint, active configuration, or generated intent.
The planner first reopens that ID. Only a definite not-found result may invoke
the context-aware intent builder, whose context exposes read-only exact
configuration views. This ordering makes an existing occurrence authoritative
after restart or rolling code changes and prevents rebuilding the same slot from
mutable configuration. A concurrent create conflict or lost response is
reconciled by reopening the same ID. A different due time or exact procedure ref
under that existing shell is reported as drift and never overwritten.

The only overlap policy is currently `enqueue`: a newly selected slot is admitted
independently even if the previous occurrence has not closed. The registry
allows only one active version for a logical schedule ID, so registering v1 and
v2 together cannot accidentally double a stream. Removing or upgrading a
definition does not cancel one-shots already committed to the daemon. The
explicit schedule version is the policy compatibility boundary: changing its
builder, anchor, interval, target procedure, or overlap policy requires a version
bump. Reusing a version with a different shell is reported as drift for an
existing ordinal; future ordinals otherwise have no historical spec to compare.

`--once` performs one bounded
plan-materialize-dispatch cycle for manual operation and
testing, prints interval, schedule and procedure counters, and exits nonzero when the
cycle records a deterministic failure. The resident form polls with
interruptible waits and exponential control-plane backoff. Shutdown stops new
discovery, callbacks, mutations, or dispatch on `SIGINT` or `SIGTERM`. An
already-started procedure effect completes; at the
next durable step boundary the underlying procedure worker releases the procedure
ready for another exact worker. If there is no next step, the procedure closes
successfully. There is no mid-effect cancellation contract.

## Retired cohort planning and remaining backend contracts

The full-config `CalibrationDefinition` / `CalibrationRegistry` authoring layer,
project freshness evaluator, automatic-publication policy registry and finalizer
are removed. They have no remaining application consumers. Their old semantic
input projection, effective-success selection and implicit subset admission are
not compatibility requirements for independent parameter branches. New workers
execute explicit procedures; scientific freshness and dependency applicability
still need a replacement design.

The following legacy backend surfaces remain for a separate retirement:

- cohort wire/domain records, daemon client methods and HTTP services;
- SQLite cohort/member/finalization/status/publication-anchor storage;
- server-side full-config cohort publication and its evidence checks.

These are implementation debt, not another recommended automation path. They do
not provide a resident planner or automatic publisher. Retaining them does not
designate this development schema as a supported persistent-data baseline.
Historical stores are not rewritten or deleted by the authoring-layer retirement.

The remaining admission service atomically freezes the cohort spec and member
procedure requests, validates the observed status and fan-out capacity, and
returns the original result for an exact replay. It rejects stale observations
and cannot adopt unrelated pre-existing procedure runs. Sample/workpoint/batch
scope remains part of the retained ownership and evidence contract.

Legacy publication still checks complete member coverage, successful exact
procedure checkpoints and retained fit/proposal/decision lineage against one
full-config base. Non-conflicting scalar and keyed-table edits use the shared
common-base merge core. Independent member decisions are **not** joint scientific
verification; new branch procedures remeasure the composed result explicitly.

The publication transaction fences the exact destination and, where present,
ready-finalization revision. Approvals, the configuration revision, workspace
head, operation receipt and member publication anchors commit together or roll
back together. Anchors retain exact member and publication identities; they
cannot be substituted between operations. The old client-side publication plan,
evidence builder and receipt-reconciliation helpers are removed; only the typed
daemon transport remains for this legacy transaction. Current parameter-branch
publication owns its own exact command and receipt recovery.

These server invariants retain focused tests until those
interfaces are deliberately retired. The removed planner/finalizer tests are not
substitutes for current branch workflow tests. The latter cover joint evidence,
partial rejection, stale destinations, daemon restart and lost publication
responses in the durable procedure ledger.

## Capabilities needed at larger chip scale

Scaling explicit branch procedures to hundreds or thousands of related
calibrations requires additional control-plane concepts. The legacy 200-member
cohort limit is not the target platform model:

- selectors and immutable cohorts for qubits, couplers, channels, and regions;
- dependency and freshness records that explain why a calibration is due and
  which downstream values become stale after a change;
- resource-aware bounded fan-out, backpressure, priorities, and maintenance
  windows while retaining stable per-unit step identities;
- hierarchical ownership and composition policies extending the current
  common-base cell merge across regions and device layers, with explicit
  conflict summaries and any required joint-verification evidence;
- explicit quality gates, approval policy, stop conditions, and rollback to an
  exact entry rather than a relative undo;
- fleet-level publication priorities, maintenance windows, and operator tooling
  with explicit policy availability rather than restoring the retired registry;
- richer recurring scheduling and worker-fleet discovery, including cron/civil
  time, version availability, worker heartbeats, maintenance windows, and
  operator attention queues;
- bounded procedure, cohort, and lineage queries plus aggregate progress and
  failure summaries for the project console;
- operation receipts for other mutating non-run effects, including inventory
  migration or exact rollback commands;
- simulation, dry-run planning, workload budgets, and reference-device scale
  tests before enabling a large cohort.

These features should extend the same exact-reference and idempotency contracts.
They should not introduce a second run engine, analysis store, or configuration
authority inside an automation subsystem.

## Evolution order

The next safe increments are:

1. add indexed operator summaries for publication attention, unsupported exact
   capabilities, procedure attention, and cohort progress without an N+1 scan;
2. exercise the existing 200-member boundary with synthetic workload and query
   plan budgets before raising it;
3. add durable cohort traversal, priority, and workload budgets beyond the
   bounded in-memory selector;
4. extend the exact common-base merge with hierarchical ownership, policy gates,
   conflict reporting, and optional joint verification for device-wide
   campaigns;
5. extend fixed-UTC latest-only intervals with explicit maintenance-window and
   richer missed-slot policies only when operators require them.

A DAG becomes useful only when fan-out and dependency scheduling are real
requirements. Until then, persisted imperative checkpoints remain the smaller
and clearer model.

### Resource waiting boundary

Current run detail exposes competing owners through `resources[].blocked_by`.
This is an observational read model; it neither reserves hardware nor schedules
the waiting run. New synchronous runs still use terminal rejection on contention.

Procedure `context.run(...)` retains the exact admitted child when executor
admission is blocked before any execution segment starts. The parent becomes
`ready` with `resource_wait={step_key, run_id}`; its unfinished step keeps the
same attempt and the worker lease is released. Runnable selection filters these
parents using indexed resource claims. The GUI worker manager skips blocked
parents and resumes eligible ones with the same child admission identity. A
Python caller without a background host must explicitly resume the procedure.

Lease acquisition checks eligibility again atomically. The wait marker survives
reacquisition until step completion, allowing cancellation to close an unstarted
child and its parent in one transaction, including the wakeup race. Once the
child starts, cancellation follows the existing current-step contract. Closed
children replay their retained outcome. Restart never turns quarantined claims
or an unknown, already-started execution into permission to retry. A crash before
the wait checkpoint still follows ordinary unfinished-step recovery.

Resource-owner lookup, wakeups and these transitions belong to framework
services, not project scripts. This is bounded resource waiting, not a fairness,
priority or deadline scheduler; contenders may race again when resources free.

## Recovery after a known analysis failure

A project can offer a `ProcedureRecoveryAdapter` for a specific completed-run /
failed-analysis path. The adapter declares installed source and destination
procedures, the successful run step, the failed analysis step, and a pure
`build_intent(source_intent, retained_run)` function. That callback constructs
only typed intent: it must not acquire, publish configuration, or perform effects.
The [reference thermometer example](../../../examples/reference_lab/src/reference_lab/workflows/analysis_recovery.py)
keeps its deliberately failing source definition installed and uses a separate
analysis-only destination. It samples once and then analyzes the retained sample.

```python
available = lab.procedures.recovery_availability(adapter, failed_procedure_id)
if available.plan is None:
    print(available.reason)
else:
    recovered = lab.procedures.submit_recovery(
        available.plan, request_key="analysis-recovery-001"
    )
    recovered.resume()
```

The plan records the exact source procedure revision and definition reference,
attempt identities and their intent hashes, the retained `RunOutputRef`, and the
new definition/intent/sample scope. The existing `ProcedureRegistry.resolve`
checks source and destination id, version, and fingerprint. Changed definitions
are rejected; this slice provides no automatic migration. The declared source
analysis must have exactly that run as its input and must have failed without a
publication output. Incompatible step/output contracts are rejected clearly.

Eligibility examines **all** attempt history. It requires a closed failed source,
a successful acquisition and a known successful retained run outcome. Any
configuration activation/publication attempt, unknown outcome, attention state,
or other failed effect excludes this adapter, even if a later attempt succeeded.
An adapter is not authority to replay unknown hardware effects. Sample bindings
remain those of the source procedure.

Admission rechecks those durable facts and the acquired run's original step
submission identity inside one transaction. A forged or stale plan cannot create
new work. Repeating the same recovery intent and request key returns the same new
procedure before unrelated admission checks; changing its intent or provenance
under that key is a conflict. The original failed procedure and attempts remain
immutable. The console links the new procedure to that history and its retained
run; it does not offer a universal retry action.

Recovery provenance is an optional field in the existing procedure JSON record.
There are no table or store-version changes. Non-recovery intent hashes retain
their existing format; only recovery invocations include the link in their hash.
