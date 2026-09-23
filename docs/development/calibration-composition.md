# Compose calibration candidates without inheriting acceptance

Parameter composition, scientific verification and publication are different
operations. The former reference DRAG cohort combined them through full-config
registry records and a project-specific semantic-input policy. That reference
implementation is retired; its interfaces are not the target design for branches.

## Parameter composition

`scopecat.config.candidate_merges.merge_parameter_deltas()` is the shared pure
core. It takes `ParameterContent`, groups of `ParameterValueDelta` and a result
snapshot ID. It returns parameter values and canonical changed-cell deltas.
It requires no setup, run, full configuration, branch or registry.

Each change must match the common base before-value. Keyed tables merge by
semantic primary-key identity and then by cell; equal edits coalesce, conflicting
edits fail, and order does not affect the result. Scalars and tables without keys
remain atomic. Unrelated missing calibration values may remain missing; preparing
an experiment is responsible for checking its required inputs.

This is a framework composition primitive, not an ordinary-author workflow for
publishing calibration. It does not authenticate proposal sources, save anything,
decide applicability or inherit verification. The old full-config proposal adapter is removed. Independent-parameter
candidates authenticate their sources at the server boundary and use this core
without inheriting the old working-point publication model.

## Scientific verification

Sequential candidates use `ParameterCandidate.then()` rather than sibling merge.
Schema 95 records an explicit composition mode and ordered exact sources. The
resolver checks each successful run's source against the preceding candidate,
including its full resolved hash and scientific scope. It applies each retained
proposal to that run's snapshot and computes net deltas against the initial saved
base. Analysis publication recomputes the chain, rejecting forged net values.
Later stages may refine the same cells; parallel merge conflict rules are unchanged.
Only flat chains of original proposals are supported. No branch is moved by this
operation, and a net-zero chain produces no candidate. See the
[author workflow](../how-to/verify-parameter-candidates.md#refine-a-candidate-in-a-later-experiment).

Two proposals changing different cells can still interact physically. For
example, independently chosen q0 and q1 drive settings can affect a shared
readout or a coupled evolution. Absence of a cell conflict says nothing about
the resulting experiment's validity.

The first ordinary multi-target workflow should retain the merged proposal and
its exact contributing sources, then collect independent verification using the
combined parameters in the intended scientific context. A registered laboratory
policy decides acceptance from that retained evidence. A list of individually
accepted proposals is not a positive decision for the combined result.

Reusing individual verification instead of measuring the combination requires a
separate, explicit composition policy that proves the relevant semantic inputs
remain valid in the merged result. The retired DRAG semantic-input comparison
was one fixture-specific policy, not a general independence theorem. Do not infer
independence merely from different row keys, target IDs or disjoint edited cells.

## Publication and automation

Publication selects one captured destination head and commits the values and
retained evidence atomically. Branch names do not establish sample/cooldown
applicability. Re-checking out a newer head is not permission to silently rebase
old verified candidates. Prepared runs remain pinned, and publication changes
neither equipment nor a shared default.

Single-candidate `publish_to_branch()` already provides this transaction boundary.
`ParameterCandidate.combine()` now saves a merged candidate under one contributing
baseline, retaining exact run/analysis/proposal/content references for every source
and their independent parameter base. The server resolves stored sources and
recomputes the merge at the analysis publication boundary. Composition provenance
participates in publication identity. Duplicate or nested sources, differing
parameter/setup/subject/scenario inputs and forged values are rejected.

The ordinary verification facade includes every contributing baseline. The server
requires these baselines plus successful data using the exact joint candidate;
individual acceptance is not inherited. Laboratory policy remains responsible for
the measurement's scientific coverage. A real q0/q1 DRAG journey now fits separate
proposals, composes them, remeasures each target under the joint parameters,
retains both decisions and explicitly publishes the joint result to a branch.

Automatic scheduling must additionally retain the requested targets, their
completed/rejected/missing results and the exact finalization decision. Retrying
a completed publication must replay its receipt; partial cohort completion must
not publish an implicit subset. A deliberate subset needs its own explicit
request and verification scope.

`LabProcedureContext.publish_parameter_candidate()` now connects the independent
branch transaction to the durable step ledger. Its intent binds the captured
branch generation/base, exact candidate and verification analyses and revision
name. Completed steps retain and replay the accepted head; interrupted publication
retries the same server command. Unknown outcomes require attention, and
analysis-only recovery cannot carry attempted publication into a new procedure.
The output and SQLite operation contract use development schema 88, with no
prebaseline migration.

`combine_parameter_candidates()` retains composition as an analysis step. The
replacement `drag_branch_calibration` procedure freezes an explicit nonempty,
unique target list, exact resolved parameter/setup inputs, branch head and result
revision name. It fits every requested target, composes the proposals, remeasures
every target under that result, and retains checked/rejected/missing targets in
one joint decision before publication. Partial execution remains visible in the
step ledger and cannot publish. A deliberate single-target request uses the same
pipeline with no artificial composition.

The same procedure supports sequential fitting: the next acquisition consumes
the preceding exact candidate, and `combine_parameter_candidates(...,
mode="sequential")` reduces the ordered chain to the initial branch base. The
default parallel mode retains common-base sibling conflict checks. Both modes
require fresh measurements under the aggregate and the complete joint decision;
neither treats individually accepted stages as verification of the final values.

The real-daemon journey stops after composition, restarts the daemon, resumes
through `ProjectAutomationWorker`, loses a committed publication response, then
recovers the historical receipt even after a later branch edit. Acquired runs are
not repeated. Separate cases retain a negative scientific decision, reject an
incomplete target set and reject a stale destination without publication.

The old reference freshness evaluator, verify-only member procedure, semantic
merge publisher and automatic-publication registration are removed with their
obsolete tests. In particular, automatically excluding peer DRAG values from
freshness and inferring q0-only reruns is **withdrawn**, not silently preserved.
The new worker accepts submitted requests; it does not decide scientific freshness
or widen/narrow their target scope. The generic cohort backend is also retired as described below.

Standard application composition and the installed worker no longer expose the
legacy enrollment path: `LabApplication`, `[lab.capabilities]` and
`scopecat automation work` register/dispatch procedures and schedules, not cohort
evaluators or publication registries. Removed declaration keys and the old
`--working-point` option fail visibly. Low-level cohort services and their storage
schema are now retired too; existing files are not rewritten or migrated.

The shared `ProjectAutomationWorker` also no longer accepts legacy evaluator or
finalizer components. Its cycle result contains only interval, schedule and
procedure outcomes. The old global publication-backlog planning barrier and
cohort-specific retry handling are retired; independent branch publication and
unknown-outcome recovery belong to each durable procedure. Stop-before-cycle,
stop-after-planning, bounded dispatch, lease races and transport backoff remain
covered independently of the legacy cohort fixtures.

The notebook client no longer constructs a cohort facade or accepts calibration
and publication registries. `LabClient.calibrations` and the root `calibration` /
`Calibration*` authoring exports are retired. Ordinary authors use registered
procedures and independent parameter candidates.

The unused generic freshness evaluator, automatic publication finalizer, policy
registry and calibration-definition authoring layer are now also removed, along
with tests whose only consumers were those retired components. No automatic
freshness or implicit subset behavior is carried forward. The old client-side
publication plan, contribution builder and receipt-reconciliation helpers are
also removed: their only remaining consumers were their own unit tests. Current
parameter candidates and durable procedure publication provide separate exact
evidence and recovery paths.

The server retirement is complete in development schema 88. Cohort routes and
client methods, wire records, publication receipts, config/setup supersession
hooks, daemon service/store composition and SQLite tables/queues/triggers are
removed together. The workbench and generated API schema also drop old cohort
provenance. Pure common-base parameter merging and independent candidate proof
checks remain. Existing stored files are untouched; schema 87 is rejected rather
than migrated. Current branch transaction, replay and backup/restore tests replace
the retired cohort-only fixtures.

The installed `calibration` teaching topic now provides a complete single-target
synthetic workflow, with editable model/fit/policy/procedure source, pause/resume
and retained rejection. Its shipped notebook cells are tested across a daemon
restart without reacquiring completed steps. The independent `joint-calibration`
topic adds two-target composition and complete verification, a coupled rejection
despite individually accepted candidates, and a missing-target decision that
cannot publish. Its pause is after composition; restart does not repeat prior
baseline or individual-check acquisitions. These are declared synthetic models,
not scientific freshness policies or completed human usability validation.

## Remaining implementation order

The [multi-target maintenance direction](calibration-maintenance.md) separates
initial tune-up, routine checks and recovery. The single-target sandbox now also
retains positive and negative check-only evidence without creating candidates or
advancing a branch. Procedure completion is distinct from the measured outcome.

1. Define scientific freshness/applicability over explicit parameter dependencies,
   subject, setup and policy; do not reuse the retired full-config projection.
2. Collect focused usability feedback on the single-target and joint notebooks
   before extending the teaching surface to automatic target selection.

Track the retirement in [#773](https://github.com/scopecat-project/scopecat/issues/773).
No historical store rewrite or prebaseline migration is part of this work.
