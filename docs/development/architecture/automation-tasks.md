# Automation: domain tasks above durable execution

Status: selected design, with declared calibration checks implemented as the
first domain slice. Task graphs, capability projections and large-scale scheduling
below are requirements, not claims about the current worker.

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
parameter revision must exist and compose with the current setup, and the declared
subject and software scenario must match authoritative evidence. Physical checks
require a subject. Procedure sample selection and any explicit scientific binding
must agree with the declaration. The designated child measurement must use the
declared parameter revision without overrides and the exact declared context.
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
queries also check result scope and measurement context. History remains a
client-side bounded journal scan, not an indexed server domain query. Indexed
queries and task-level dispatch contracts remain future work.

## Panel requirements

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

The current project worker discovers runnable procedures and resumes them
sequentially in one dispatch loop. Leases and resource waits support recovery,
but are not a complete parallel task scheduler. Preserve serial execution while
making task boundaries explicit; add concurrency only after those boundaries work.

Required next contracts:

1. Indexed server domain queries, including consistent pagination
   and declarations for pending work. The current bounded journal scan is not a
   large-catalog query strategy.
2. Task/stage/target relationships with frozen intent, explicit partial completion,
   bounded repair loops and independently dispatchable units. Avoid one giant
   procedure containing every target and an unbounded maintenance loop.
3. Capability dependency and parameter-read contracts, including query membership
   and physical interactions. Exact revision matching remains conservative until
   reuse can be explained from complete dependencies.
4. Worker/environment routing, resource/scientific grouping, fairness, task budgets
   and coalescing repeated maintenance requests. Closing a notebook must not own
   or cancel admitted background work.
5. Panel projections and operator controls using the same domain service.

Validate these with a small synthetic array: full tune-up, local drift, one failed
resource, unaffected progress, restart and explicit partial results. Add a sample
panel consumer before claiming that a Python-only automation abstraction is ready
for the application. See [calibration maintenance](../calibration-maintenance.md).
