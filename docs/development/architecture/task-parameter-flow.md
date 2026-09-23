# Parameter flow through calibration tasks

Status: explicit candidate output bindings and an optional final procedure handoff
are implemented for passing checks. General adaptive flows and repair remain unfinished.
No supported persistent-data baseline is declared here.

The first implemented lineage slice is `first.then(second, name=...)`: it resolves
completed candidate-backed stages into one candidate against the initial saved
revision. Each stage must consume the preceding exact candidate. A new independent
measurement must verify the final candidate before branch publication. Schema 95
retains ordered sources and revalidates their net values. Candidate contexts are
supported by the shared resolver, check admission, child-input matching, retained
evidence and workbench consumers. They remain distinct from saved revision contexts.

## Existing primitives

`ParameterCandidate` references an analysis-backed retained proposal. Independent
verification binds candidate measurements to a laboratory decision. Branch
publication checks the exact base and generation, atomically saves values and
acceptance evidence, and supports identical retries. Sibling candidate composition
requires a common base and does not inherit individual verification. These are
implemented features, not pending work. Accepting candidate cells is not blanket
branch validity; ordinary parameter saves also make no calibration claim.

## Implemented stage transition

`StageCandidateOutput` selects a proposal from a prerequisite's adopted analysis.
Schema 97 stores `resolved_checks` atomically with procedure admission; the immutable
specification and those checks reconstruct the exact calls without duplicating a
second call ledger. The source check must pass and retain successful measurement
evidence. Scope/setup/subject/scenario/mapping stay fixed while the declared edge
replaces the parameter input. No arbitrary argument substitution occurs. Existing
check admission and child evidence validation enforce the resulting context.
See [the author contract](../../how-to/preview-calibration-tasks.md#bind-a-prerequisites-candidate-output).

## First complete workflow

1. Capture the daily branch revision/generation, setup, subject/binding, author
   definitions and required verification scope.
2. Retain the first stage's proposed values and evidence without publishing daily.
3. Run the next stage on those exact values; retain its input binding and proposal.
   Completion alone does not prove that predecessor output was consumed.
4. Verify the entire final combination with explicit laboratory policy. Negative
   decisions and acquisition failures differ, but neither publishes daily.
5. Publish against the originally captured branch generation. Concurrent edits
   preserve progress but block publication; reloading cannot silently rebase evidence.

Intermediate values can be incomplete. They need durable identity and derivation,
not certification. Do not use daily as transport. If task-local saved revisions
are introduced, preserve their candidate ancestry instead of copying values into
ordinary saves and losing provenance.

## Ownership and remaining boundaries

- Shared input resolution, admission and evidence now cover exact candidates and
  saved revisions without overrides. Use this same context when binding future
  stage outputs; do not add a task-only context or disguise candidates as revisions.
- Typed candidate outputs and binding receipts are implemented. They select only
  the proposal in the source check's adopted analysis; general Python object graphs
  and arbitrary JSON paths remain outside this contract.
- Distinguish sequential ancestry from common-base sibling composition. Later
  stages can intentionally refine earlier cells; retain sibling conflict checks.
  This distinction is implemented for flat retained proposal chains.
- Define final lineage verification/publication. Reuse exact-source, subject/setup
  and optimistic checks; current single-candidate publication does not already
  validate an arbitrary multi-stage chain. The flat-chain resolver now reduces
  validated sources to one original-base candidate. Verification must include all
  contributing source runs plus new data using that aggregate candidate.

The durable procedure composition API accepts `mode="sequential"` for these flat
chains. It retains the ordered source references and mode in step identity, and
uses the same server resolver as `ParameterCandidate.then()`. The maintained
two-target simulation now exercises both common-base and sequential fitting,
aggregate remeasurement, a complete decision and fenced publication, including
restart and recovery of a lost committed publication response. This completes
the explicitly authored procedure path. Schema 98 adds an optional finalization
call to a task: after every check passes, admission freezes all adopted evidence
in `CalibrationTaskInputs` and associates one procedure in the same transaction.
Workers recover that handoff after restart. The author-specified procedure owns
aggregation, final acquisition, laboratory policy and fenced branch publication;
the task does not infer any of these from check success. See
[the finalization contract](../../how-to/preview-calibration-tasks.md#final-verification-and-publication).

Start with two linear stages and serialized publication. General fan-in, adaptive
loops and repair follow a retained, explainable path. Capability prerequisites,
execution order and parameter flow remain distinct.

## Acceptance

Use independent setup and parameter revisions, with no global parameter default.

- The second measurement records the first stage's output; external branch edits
  cannot redirect it.
- Final verification covers the combination, including deliberate refinement of
  an earlier cell.
- Rejection, execution failure and missing evidence leave daily unchanged.
- Restart at retention/binding/admission/publication boundaries neither duplicates
  work nor changes inputs.
- A lost publication response is retryable. A conflicting edit blocks a different
  publication without destroying retained progress.
- Current-format backup/restore preserves lineage, progress and evidence.

Then extend to the synthetic array in
[calibration maintenance](../calibration-maintenance.md). Hardware behavior and
operator usability require a later Windows laboratory trial.
