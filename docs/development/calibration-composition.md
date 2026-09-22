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
decide applicability or inherit verification. The legacy proposal adapter still
checks exact source identities/full-config bases and delegates value composition
to this core. It remains in the legacy framework publication API and focused
server tests, not as a compatibility requirement for independent-parameter candidates.

## Scientific verification

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
The output and SQLite operation contract use development schema 87, with no
prebaseline migration.

`combine_parameter_candidates()` retains composition as an analysis step. The
replacement `drag_branch_calibration` procedure freezes an explicit nonempty,
unique target list, exact resolved parameter/setup inputs, branch head and result
revision name. It fits every requested target, composes the proposals, remeasures
every target under that result, and retains checked/rejected/missing targets in
one joint decision before publication. Partial execution remains visible in the
step ledger and cannot publish. A deliberate single-target request uses the same
pipeline with no artificial composition.

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
or widen/narrow their target scope. Legacy generic cohort APIs still have other
framework test coverage and require a separate removal pass.

## Remaining implementation order

1. Define scientific freshness/applicability over explicit parameter dependencies,
   subject, setup and policy; do not reuse the retired full-config projection.
2. Retire unused generic working-point/cohort publication surfaces once their
   remaining callers and retained-data contracts have been reviewed.
3. Introduce a reusable teaching sandbox for this complete author workflow.

Track the retirement in [#773](https://github.com/scopecat-project/scopecat/issues/773).
No historical store rewrite or prebaseline migration is part of this work.
