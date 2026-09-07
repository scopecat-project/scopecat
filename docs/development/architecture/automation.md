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

The original single-target reference DRAG procedure demonstrates the durable
procedure boundary:

1. run a baseline scan against an exact configuration snapshot and source;
2. publish the run-scoped fit and parameter proposal;
3. run the candidate configuration with exact analysis provenance;
4. publish a project-owned comparison and require an accepted decision;
5. publish and activate that candidate using the exact proposal and decision.

The procedure API can publish a verified candidate or activate one already-saved
configuration entry with generation compare-and-swap and an exact operation
receipt. Policy remains explicit in the procedure: only the project verification
fact authorizes DRAG candidate acceptance. The production run remains outside
the procedure, and final cleanup reactivates the exact starting entry instead of
calling the interactive `undo()` helper. That helper first resolves history to
an exact entry and then uses the ordinary idempotent activation operation; the
relative history lookup itself is not a replayable procedure compensation.

The bounded calibration path uses a second, verify-only DRAG procedure. Each
cohort member performs the first four steps and closes successfully without
publishing configuration. A cohort finalizer then composes the exact verified
member proposals and publishes one revision for the whole cohort. The explicit
API remains available for operator workflows; a cohort pinned to an exact
automatic-publication policy is instead discovered by the resident project
worker. This keeps per-target scientific work restartable without letting
independently finishing members overwrite the shared active configuration.

The procedure replay layer deliberately has no DAG representation, automatic
retry policy, cron trigger, or dynamic loop checkpoint. A linear Python
procedure with durable step checkpoints remains the unit of execution; the flat
bounded cohort admission layer described below does not add another run engine.

## One-shot schedules and project automation workers

A durable procedure schedule freezes one exact definition reference, canonical
intent, and UTC due time. Materialization derives a stable request key from that
whole schedule and atomically admits one `ProcedureRun`. Recreating the same
schedule is idempotent; changing its exact content under the same schedule ID is
a conflict. Due-time processing never rebuilds intent from the active
configuration or the worker's current Python environment.

`scopecat automation work PROJECT` runs the project-owned Python worker as a
process separate from the daemon. Each bounded cycle first finalizes supported
ready calibration publications, then performs config-sensitive interval planning
and calibration evaluation, materializes already-frozen due schedules, and asks
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
finalize-plan-evaluate-materialize-dispatch cycle for manual operation and
testing, prints publication and procedure counters, and exits nonzero when the
cycle records a deterministic failure. The resident form polls with
interruptible waits and exponential control-plane backoff. Shutdown stops new
discovery, callbacks, mutations, or dispatch on `SIGINT` or `SIGTERM`. An
already-started publication reconciliation or procedure effect completes; at the
next durable step boundary the underlying procedure worker releases the procedure
ready for another exact worker. If there is no next step, the procedure closes
successfully. There is no mid-effect cancellation contract.

## Freshness evaluation and bounded calibration cohorts

The project application may also register a small immutable
`CalibrationRegistry`. Each `CalibrationDefinition` owns a typed target
selector, a typed input/dependency observer, and a pure intent builder. One
cycle resolves the exact active configuration entry and generation once. The
selector, observer, and builder receive that same frozen planning context. The
builder additionally receives its stable target, validated freshness-input
model, and flat exact dependency-success evidence. The context contains the
captured configuration and exact source value, not a client or read interface,
so the builder cannot refresh mutable active configuration while admitting work.
The freshness-input model must include every semantic value whose change should
rerun the calibration, such as a relevant configuration content hash; invocation
provenance such as a registry generation can remain only in the context and
exact procedure intent when it must not make an otherwise identical result
stale.

The evaluator supports at most 200 selected members and 200 combined member and
dependency status keys per definition. It first queries all logical calibration
statuses using the server clock and the definition's fan-out scope. Only after
that status snapshot may it build intents for the deterministic ready frontier.
`active` and `attention_required` attempts suppress new work. A failed or
cancelled attempt suppresses only the same freshness fingerprint: changing the
explicit calibration definition, exact procedure, observed inputs, or exact
dependency successes establishes a new need that may be admitted. There is no
project-side forced flag because a constant force value would create a cohort on
every poll; an operator-forced retry must be an explicit manually authored
cohort with a stable external identity.

Each definition also declares how success becomes effective.
`procedure_success` means a successful procedure closure is immediately fresh.
`published_result` means the closure has produced a verified proposal but has
not yet changed authoritative configuration. That closure is reported as
pending publication: the same exact definition, procedure, freshness
fingerprint, and base configuration suppress another run, but the pending result
is neither fresh nor valid dependency evidence. If the active configuration
source changes before publication, suppression ends and the evaluator emits a
typed `publication_base_changed` reason. The old finalizer still names its old
base and must lose the registry generation CAS; a later cycle may admit work
against the new base.

A prior effective success is fresh only when its definition, target procedure,
semantic input fingerprint, dependency-success identities, and optional
validity duration all still match. For `published_result`, those comparisons use
the result input and freshness fingerprints attached by the atomic publication,
not the inputs observed before its procedure started. An unrelated later
configuration revision therefore need not invalidate a result when the
definition's observer projects the same semantic inputs. Missing or pending
dependency success blocks that member. Dependencies can reference only effective
successes present in the status snapshot taken before cohort creation, including
the publication operation identity where one is required. A member cannot
depend on a success created in its own cohort, so the daemon never persists a
dynamic closure or performs recursive scheduling. Later cycles converge after
upstream successes become visible.

Available admission capacity is
`max_in_flight - observed_fanout_active_count`. The evaluator takes at most that
many canonically ordered ready members and the daemon atomically checks both the
status observation and capacity while creating the cohort. A cohort ID is
derived from the complete immutable cohort-spec hash. Each member uses its
logical calibration key as `member_id`; its procedure request key covers the
cohort ID and exact member spec. Concurrent workers can therefore race safely:
one atomic admission wins, exact replay returns the winner, and a stale
observation is a benign admission conflict retried by a later cycle.

The calibration-definition fingerprint covers its ID/version, success policy,
input schema, selector, observer, builder, exact procedure reference, fan-out
scope, and capacity. A transitive imported-code change still requires an
explicit version bump. The server stores immutable cohorts, member/run
associations, flat prior success evidence, publication anchors, and ordinary
procedure runs. It does not store selectors, Python closures, graph edges, or a
traversal cursor. The hard 200-member first slice makes every evaluation bounded;
larger cohorts will require an explicit durable traversal policy rather than an
in-memory cursor.

The config-sensitive prefix of a worker cycle is automatic publication, interval
planning, then calibration evaluation/admission. A completed publication is
therefore visible to freshness evaluation in the same cycle. If the bounded
publication page reports more work, the cycle raises a local planning barrier:
it skips both interval planning and calibration admission against the soon-to-be
obsolete active head, while still materializing frozen due schedules and
dispatching already-runnable procedures. Stop is checked before each project
callback and durable call. Retryable transport and 5xx/429 control failures use
the resident worker's interruptible exponential backoff. After an unknown
cohort-create transport outcome, the evaluator reopens the deterministic cohort
ID; not-found retries the original transport error, while a different
deterministic 4xx is fatal because the durable outcome cannot be classified
safely.

## Exact cohort finalization and published freshness

Publication is a project-side operation, not another procedure step or an
implicit daemon reaction to procedure closure. The explicit caller or registered
resident policy reopens one exact cohort and its complete member page, then
supplies one contribution for every member. A contribution carries a versioned
`verified_parameter_proposal_v1` proof: one self-contained exact procedure
checkpoint whose project-analysis output owns the accepted decision. The
semantic result-input fingerprint remains composition evidence rather than part
of that scientific proof. Only `published_result` members whose exact parent
procedures closed successfully are eligible.

The server resolves all evidence before publishing logical state. It checks the
cohort spec and base source, complete member coverage, and that the exact
evidence step published the referenced project decision. The project analysis
must have exactly two direct run inputs. From those durable inputs the server
uniquely identifies the cohort-base baseline and analysis-candidate run, then
derives the fit analysis and proposal from the candidate source and revalidates
the complete lineage. The durable registry source records the proof dialect,
checkpoint, and resolved run, analysis, proposal, and decision identities, so
later readers do not have to trust caller-supplied derived fields.

All proposals must branch from the cohort's one exact base. The generic
`common_base_cells_v1` merge combines non-conflicting whole scalar values and
keyed-table cells deterministically; incompatible edits to the same atomic value
or cell are a conflict. The finalizer also names a versioned and fingerprinted
project composition policy. Project code may use that policy to check how the
merged snapshot maps back to each member's semantic result inputs, but the
daemon neither imports the policy nor treats it as executable code.

This evidence means that every contribution was verified independently and that
their composition obeyed the declared deterministic policy. It does **not** mean
the merged device configuration received a joint scientific verification. A
campaign that needs crosstalk, simultaneous-operation, or device-wide validation
must publish that additional evidence explicitly rather than infer it from the
cell merge.

A dedicated calibration-publication command is the transaction boundary. Its
config revision intent uses the same source-intent and operation identity rules
as an ordinary config publication, while its optional finalization-revision
fence remains calibration-specific. After resolving an exact operation replay,
automatic publication also fences the exact still-ready finalization revision
and its server-clock availability. The server then prepares every proposal
approval, executes one registry save-and-activate CAS against the cohort base
generation, emits config events, commits one dedicated receipt containing the
complete typed member-success tuple, records it in the shared config-operation
ledger, marks the finalization published, and inserts one publication anchor per
member in the same transaction. Any proof, state fence, merge, result-hash,
generation, receipt, or anchor failure rolls the logical transaction back. A
successful two-member finalization therefore creates two anchors but only one
entry and one new registry generation; when both proposals were previously
unapproved, it also creates their two approvals.

Each anchor binds the exact member success and closure time to the cohort base,
publish operation and source-intent hash, semantic result-input fingerprint,
server-recomputed result freshness fingerprint, result entry and generation,
and publication time. The calibration receipt must cover exactly the resolved
contribution set; the generic config command and receipt do not expose
finalization fences or calibration anchors. Status loads
the anchor by the exact successful procedure-run key, so a stored anchor cannot
be substituted from another operation or member. Once anchored, the result is
an effective success and may supply flat dependency evidence.

The project finalizer derives deterministic entry and operation IDs from the
complete merge source, actor, and note. An automatic plan additionally carries
the ready finalization revision without changing those replay identities. A
successful response is validated against that frozen plan. If transport loss, a
server failure, or an invalid response leaves the outcome uncertain,
reconciliation only reopens the exact operation and finalization; it does not
refresh the active head, rebuild contributions, or issue a new intent. An
unclassified outcome retains the original plan and is always retryable by the
resident backoff loop, even when the immediate cause was response validation or
receipt drift.

Automatic publication policies are immutable, fingerprinted, and pinned into
the cohort spec. The application registry retains every exact historical
capability needed to drain already-admitted cohorts, independently of its active
admission bindings. Multiple policy versions may therefore target the same exact
calibration definition; the application must explicitly select one active policy
when that selection is otherwise ambiguous. The planning callback sees only
read-only procedure/run projections and plan builders; the resident engine alone
owns publish, defer, and attention mutations. Ready work is durable and
capability-filtered by every retained exact policy reference, while new cohorts
pin only the selected active binding. Discovery uses a finite insertion-sequence
high-water, wrapping only after the terminal page, so late readiness cannot be
lost and continuous arrivals cannot make a cycle unbounded.

Workers do not persist a second claim or lease. They prepare deterministically
and race through the finalization revision plus base-generation fences. A lost
publish response is reconciled by exact operation and cohort finalization;
published and superseded states are benign completion, while an unresolved
outcome retains the same plan for retry. Deterministic proof errors move the
exact ready revision to operator attention. A typed temporary block defers that
same ready occurrence using server-clock availability. Disposition responses
are never blindly replayed: response loss is reconciled by exact finalization,
and a different worker's revision is counted as a benign race.

The resident path is complete for publication policies retained by the loaded
project application. Operator-wide discovery is deliberately narrower: there
is not yet an indexed query that combines publication attention with ready work
whose exact historical policy is unavailable to the current worker. Adding
that view requires a durable state/capability ordering; it must not be emulated
by repeatedly listing all cohorts and reopening every member page. Until that
query exists, automatic publication should be enabled only where policy
capabilities are deployed and monitored as part of the project application.

## Capabilities needed at larger chip scale

Moving beyond the current bounded 200-member flat slice to hundreds or thousands
of related calibrations requires additional control-plane concepts, not a larger
procedure function:

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
  beyond the current bounded exact-capability queue;
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

Automatic procedure waiting remains unimplemented. Its durable boundary must
retain the exact admitted child run and unfinished step before releasing the
worker. A later worker must replay that same child, with an atomic check that it
has not begun execution or been cancelled. Cancelling a waiting parent must close
its unstarted child in the same transaction; an already-started child continues
to follow the current-step cancellation contract. Restart may make an unstarted
wait eligible again, but must not turn an unknown execution outcome or a
quarantined resource into permission to retry. Resource-owner lookup, wakeups,
and these transitions belong to framework services, not project scripts.
