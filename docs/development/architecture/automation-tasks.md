# Automation: domain tasks above durable execution

Status: declared checks, stage previews, durable fixed task specifications,
dependency-checked dispatch and sequential background advancement with task controls
are implemented. Bounded capability reports and sample/task workbench consumers
also exist. Continuous maintenance, parameter flow and large-scale scheduling remain.
See the [implementation order](../platform-status.md) and
[parameter-flow contract](task-parameter-flow.md).

## Ownership

| Layer | Responsibility | Lifetime |
|---|---|---|
| Session context | Page/kernel selections and defaults | Interactive session |
| Frozen execution context | Exact subject, conditions, setup, parameters and source | One admitted execution |
| Capability and maintenance policy | Required quality, dependencies and repair/check rules | Across executions |
| Task | Requested targets, stages, budget and explicit partial completion | One tune-up or maintenance round |
| Procedure | Durable steps, effects, waits, retries and recovery | One bounded execution |
| Background service | Admission/dispatch, environment routing and resource scheduling | Application service |
| Panel projection | Capability/evidence/task state and reasons | Read model derived from records |

A procedure is not the sample's health record or an indefinitely running
maintenance policy. Preserve its durable step ledger, exact definition identity,
leases and uncertainty handling. A procedure replays imperative Python code;
arbitrary control flow is not automatically a declarative dependency graph.
Effects belong in durable steps and planning must use retained inputs.

## Context transitions

Changing a page or kernel selection affects future requests. It cannot redirect
admitted work. A long-lived policy may name a parameter branch, but each dispatched
execution captures a concrete head. Branch publication remains explicitly fenced.

A higher-level task can coordinate stages using different working conditions or
setups. Each stage admits its own context and resource requirements; do not weaken
the existing bound-procedure subject/setup checks to allow invisible switching.
Joint samples require executable assembly support, not a sequence of single-sample
requests relabeled as a joint measurement.

## First implemented domain slice

`CalibrationCheckRequest` declares capability scope, exact context and execution
result addresses before measurement. It is persisted inside the procedure intent
under `calibration_check`, participating in existing identity and replay rules.
`CalibrationCheckResult` is a standard typed analysis fact. Extra residuals, fit
diagnostics and figures remain ordinary analysis outputs.

`lab.calibration_checks.history()` reads these declarations without importing or
calling an author-defined evidence reader. Pending requests are inspectable and
filterable by declared context. The generic procedure facade no longer owns the
calibration history query. Execution storage still owns the durable intent and
effects; this slice adds no second scientific-data store or migration.

The server validates declarations before admitting a new procedure: the exact
parameter revision must compose with the current setup, or the exact candidate
must resolve from its retained proposal under compatible setup authority. The declared
subject and software scenario must match authoritative evidence. Physical checks
require a subject. Procedure sample selection and any explicit scientific binding
must agree with the declaration. The designated child measurement must use the
declared saved revision without overrides or exact candidate and declared context.
An exact request retry returns the retained request before rechecking mutable
authority; it does not authorize new measurements against an obsolete setup.

This slice supports one declared measurement and one analysis result per check.
Laboratory procedures still validate their executable arguments. When completing
the declared measurement step, the server checks the retained run context. When
completing the analysis step, it requires a publication belonging to that exact
measurement, the declared fact output, the standard result schema and matching
scope. Invalid evidence leaves the step and procedure revisions unchanged;
negative scientific results are valid completed checks. This is result adoption,
not a restriction on saving independent analyses of the same run.

The standard schema lives in `scopecat.analysis.calibration`; the author-facing
`scopecat.api.calibration_checks.CHECK_RESULT` exposes the same contract. Historical
queries also check result scope and measurement context. Declaration queries use
the server's `calibration_check_requests` projection, with indexed exact scope,
context and combined filters before keyset pagination. The projection is written
in the request admission transaction and joins current procedure state; it does
not duplicate scientific evidence. Each response item now contains the execution,
typed declaration and optional resolved evidence. The server reads the page,
steps and measurement snapshots in one SQLite read transaction and resolves the
fixed immutable analysis publications with the same validation used at result
adoption. Pending checks have no evidence; invalid retained evidence is an error.
No laboratory Python or author session is needed to query this endpoint.

After reading pages, the client submits observed revisions and the filtered head
to `POST /api/v1/calibration-checks/observe`. The server compares both in one
read transaction, using only indexed declarations and execution revisions. Missing
or out-of-scope observations count as changed. The request accepts at most 2,000
observed checks; the Python history budget shares this bound (default 200).
A stable comparison means those observations still match at that read snapshot;
it is neither a retained snapshot spanning requests nor authorization to publish.
Schema 89 introduces the index without backfilling prebaseline stores; the evidence
view adds no persisted format.

## Stage-plan preview

`CalibrationTaskPlan` expands checks into explicit stage IDs and acyclic
dependencies, with each stage carrying its exact `CalibrationCheckRequest`.
`lab.calibration_checks.preview_task(plan, executions=...)` resolves explicitly
bound procedures and their evidence in one server read transaction. It rejects
mismatched declarations instead of guessing a match from names or the latest run.
Unbound stages become ready, waiting or blocked; already bound stages retain
their execution state. Negative checks and execution failures remain distinct.
Terminal partial completion is different from success of every stage.

This preview neither persists a task nor authorizes dispatch. It does not prove
that a dependent experiment consumed predecessor outputs, check evidence freshness,
or infer combined scientific readiness. Those contracts precede automatic
calibration/repair. Plans can span contexts but do not switch live equipment.
See [previewing staged work](../../how-to/preview-calibration-tasks.md).

`lab.calibration_tasks.create` separately persists an immutable plan and one exact
procedure call per stage. `dispatch(task_id, stage_id)` re-evaluates prerequisites,
uses normal procedure admission and records the association in the same write
transaction. A stage gets one procedure; retries return that association before
rechecking mutable authority. Different ready stages are separate admissions, so
one admission failure does not roll back progress on an independent stage.
Schema 90 retains tasks and associations across restart and backup/restore without
introducing prebaseline migration. Stored scientific evidence remains in procedures,
runs and analyses. Task creation validates declarations structurally; equipment and
parameter authority are checked when each stage is actually dispatched.

Schema 91 adds durable manual/running/paused/cancelled/finished modes, fenced control
commands and per-stage admission errors, with an index for running-task discovery.
The HTTP daemon's task runner advances each task sequentially and hands admitted
procedures to the existing bounded worker manager. Both admission and controls
serialize through SQLite write transactions. Each failed admission rolls back its
own savepoint and is retained without repeated retries; independent stages remain
eligible. Explicit start clears admission errors but never unpauses failed workers.
Restart recovers running tasks and the admission-to-worker handoff gap.

Pause/cancel stop future admission; already admitted procedures retain their own
cancellation and uncertainty protocols. Cancelled tasks cannot restart. Completion
is projected from stage evidence, not from control mode. There are no repair
attempts or implicit parameter flow. Plans spanning setups do not automatically
activate equipment. New observations/repairs need explicit new task intent.

## Panel requirements

The workbench now has a retained calibration-task list and detail view with stage
results, admission errors, frozen context and start/pause/cancel controls. Stage
execution links reuse the procedure operator view for resource/worker status,
review and cancellation. Sample capability report consumers are described below;
neither view is a continuously maintained health dashboard.

`POST /api/v1/calibration-checks/report` evaluates explicit capability requirements
for one exact context in one read transaction. It reuses indexed declarations,
resolved evidence and the applicability selector, without new persistence. Each
requirement retains its status, selected evidence, reasons and unresolved execution
IDs. Bounded or unresolved history remains unknown. The laboratory still owns
requirement coverage, policy and age limits. See
[capability reports](../../how-to/read-calibration-report.md).

Requirements can declare an acyclic `depends_on` graph within that report.
The separate `availability` projection propagates unmet prerequisites while
preserving each check's own evidence selection. Task execution order is not
inferred as scientific dependency policy. This graph does not track parameter
reads or permit evidence reuse across parameter revisions.

Schema 94 retains immutable named capability profiles separately from tasks and
measurement contexts. The profile service saves, lists and reads explicit
requirements, then evaluates them with the same report query for a caller-chosen
context. Reports retain the profile ID and evaluated requirements. This provides
a shared policy source for notebook and future panel consumers without selecting
a global active policy. A profile name is unique within its project data store.

Policy definitions and prerequisite validation belong to
`records/calibration_policy.py`, independently of HTTP report models. Profiles
may exceed a report's 32-requirement limit. A focused report expands requested
IDs through the complete declared prerequisite graph, then enforces report size
and history budgets. It never truncates dependencies or interprets a focused
report as whole-profile readiness. These prerequisites remain separate from task
execution ordering. The stored profile representation is unchanged.

The task workbench consumes this endpoint per stage, with an explicit age policy
and visible evaluation time. Users can inspect matching checks from other tasks
without changing the stage's frozen context. This is an evidence-inspection entry
point; laboratory requirement coverage and sample capability panels remain separate.

Sample/target panels need scoped capability status, evidence time and parameter
revision, pending checks/repairs, blocked prerequisites and explicit missing scope.
They must not translate procedure completion into a calibrated flag. A completed
check can be negative; a failed acquisition can leave quality unknown.

Panel queries must not load laboratory Python or inspect hard-coded step names.
Use domain declarations and evidence references to build indexed read projections;
keep runs and analyses authoritative. A drill-down leads from capability to task,
procedure, measurements and analysis. Operator selection records attribution and
does not grant hardware authorization.

## Background execution and scale

The task runner admits stages sequentially within each task. The shared project
worker manager bounds concurrent subprocesses across tasks; leases and resource
waits retain hardware admission. This is not a fairness or scientific grouping
scheduler. Preserve serial task execution until those contracts are explicit.

Worker startup failures are isolated per procedure, including unavailable author
workspaces and failed state lookup. They pause the affected member without aborting
the scan of independent work. Only explicit procedure dispatch retries it; normal
task handoffs preserve that pause. Handoffs register ownership without performing
another whole-queue scan. This removes repeated scans when many task stages wait
for workers, while retaining the shared subprocess limit and resource admission.

Required next contracts:

1. Larger-history traversal and panel refresh policies beyond the bounded history
   facade. Pages have read-snapshot consistency and a final batch comparison;
   consumers must still use write-time authority checks when acting on observations.
2. Add parameter-flow contracts and bounded repair loops above fixed-stage
   advancement. Explicit partial-completion projections and task controls exist.
   Avoid one giant procedure containing every target and an unbounded maintenance loop.
3. Parameter-read contracts, including query membership
   and physical interactions. Exact revision matching remains conservative until
   reuse can be explained from complete dependencies.
4. Worker/environment routing, resource/scientific grouping, fairness, task budgets
   and coalescing repeated maintenance requests. Closing a notebook must not own
   or cancel admitted background work.
5. Continuous sample capability refresh and coverage beyond existing bounded
   reports. Task controls and execution drill-down already exist in the workbench.

The sample workspace now offers a bounded historical-context consumer: choose a
measurement bound to the displayed sample revision, then evaluate an explicit
saved profile. The complete joint subject is retained, and parameter overrides
or unsaved sources cannot supply an exact context; exact retained candidates now
can, preserving their source identity. This does not yet provide
a continuously refreshed sample health dashboard.

The sample page also resolves a parameter branch or exact saved revision and a
current or saved setup for
one exact inline sample revision. The context endpoint reads branch/setup heads
in one transaction and reuses parameter composition and scientific binding rules;
it does not dispatch work or select global defaults. The Python API additionally
supports joint inline samples and unbound software scenarios. Registered-target
selection retains exact catalog-qualified references and uses the shared
single-member/no-connections projection; unsupported targets are rejected.
Returned contexts freeze the observed versions;
report refresh and context re-resolution are distinct actions.

Validate these with a small synthetic array: full tune-up, local drift, one failed
resource, unaffected progress, restart and explicit partial results. Add a sample
panel consumer before claiming that a Python-only automation abstraction is ready
for the application. See [calibration maintenance](../calibration-maintenance.md).
