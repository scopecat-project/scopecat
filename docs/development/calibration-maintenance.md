# Multi-target calibration and maintenance

Status: selected design direction, with executable check-only evidence and an
exact-context applicability assessor. This is
not an implemented automatic planner or a persistent-data compatibility baseline.
The [composition contract](calibration-composition.md) describes the existing
candidate, verification and publication boundaries.

## Three workflows, shared laboratory operations

Initial tune-up establishes usable parameters and evidence from coarse priors.
Routine maintenance checks existing capabilities and repairs only the necessary
parts. Recovery diagnoses failed prerequisites or equipment before attempting
further parameter optimization. These workflows reuse measurement and analysis
functions, but have different search ranges, budgets and failure policies.

A capability is scoped to an operation and its targets: a single-qubit drive,
an ordered pair's gate, simultaneous readout of a group, or a line response.
Do not reduce this to one calibrated flag per qubit, or require every target to
be a sample. Entity identity, table-row identity and verification scope differ.

Laboratory definitions describe prerequisites, parameter dependencies, proposed
writes, checks, fitting, acceptance and bounded recovery. Public provides durable
execution, provenance, planning mechanics and explainable outcomes. Physical
independence, acceptable quality and diagnostic meaning remain laboratory policy.
Keep ordinary experiments callable independently of this orchestration layer.

## Evidence is separate from parameter values

A check may leave every value unchanged. Retain its exact measurement and analysis
with the resolved parameter revision, scientific context and policy identity;
do not create an empty parameter proposal just to record that it passed.

Execution completion and scientific outcome are distinct. A completed check can
report an out-of-spec result. A device or analysis failure means no valid check
was obtained; it must not be interpreted as either passing or ordinary drift.
Rejected evidence remains inspectable. A retry of the same completed request
replays its result; a new observation requires a new request.

The installed `calibration` sandbox implements this boundary through `check_zero`
and a typed `CheckResult` analysis fact. It exercises passing and failing checks
on the same exact accepted revision, without candidates or branch publication.
It is a declared synthetic residual check, not a generic capability registry.

Applicability assessment must evaluate subject/setup/operating conditions,
policy and relevant parameter dependencies. Time is one reason to request a new
check, not proof that values are wrong. Preserve evidence time separately from
parameter modification time. Branch names are destinations, not scientific scope.
A historical check does not become evidence for the newest head merely because
both revisions have occupied the same branch.

`scopecat.automation.calibration` now provides `CalibrationScope`,
`CalibrationContext` and the pure `assess_calibration_check()` function. Scope
names a capability, ordered target addresses within the resolved subject,
laboratory conditions and policy version. The lab must update the policy version
when measurement/analysis semantics or acceptance criteria change, and conditions
when relevant external operating conditions change. These strings are explicit
laboratory contracts, not automatically detected physical state.

The initial assessor compares the exact saved parameter revision, resolved
subject, executable setup content, software scenario and declared scope. It uses
the run's creation time as a conservative age bound, so reanalysis cannot refresh
evidence. An applicable positive result is `usable`; an applicable negative result
is `out_of_spec`. Changed or expired evidence returns `recheck`, and incomplete
measurements, unsaved parameters, unbound physical subjects or future-dated data
return `unknown`. Reasons are retained together, rather than only the first failure.

This assessor is an advisory primitive for trusted orchestration code within one
catalog. Callers obtain the scientific result and checked scope from the retained
analysis of the supplied run, and construct the requested context independently.
It does not authenticate arbitrary caller-supplied facts, authorize publication,
select the latest relevant check, or prove complete scientific coverage. The
current exact-revision rule is deliberately conservative; it must not become a
permanent substitute for parameter dependency capture. There is no new store.

The calibration notebook retains scope with its typed check result and exercises
policy changes, parameter changes and expiry. Teaching setups explicitly declare
their software computation scenario; their evidence cannot become physical-device
evidence by omitting a sample binding.

## Dependency capture has explicit limits

Current `ExperimentPreviewParameterLookup` exposes table, column and key-column
names, explicitly not resolved row identities. It is useful author feedback but
is not an applicability fingerprint. A complete parameter/configuration hash is
also too coarse to decide which capabilities need rechecking.

Before selective invalidation, define retained, resolved read dependencies for
scalar cells, keyed rows, selections and derived queries. Query membership matters:
adding a row matching a filter can change a result without editing an earlier
returned cell. Preparation and runtime/analysis reads need clearly stated coverage.
Record captured values/identities and capture completeness, not arbitrary live
Python references. Supplement observed reads with declared physical dependencies;
neither disjoint writes nor observed reads prove absence of coupling.

Initially use explicit conservative laboratory scopes. Report unknown coverage
as unknown; do not claim that an unrecorded dependency is irrelevant. Introduce
this contract before implementing automatic reuse of checks across revisions.

## Staged progress and publication

Expand a reusable capability definition into target-specific work. Dependencies
order the work; bounded local loops support coarse-to-fine calibration and mutual
refinement. A failed target blocks its dependants rather than necessarily stopping
all unrelated work. Retain each stage's candidates and evidence for recovery.

Distinguish saved stage results from a revision declared ready for daily use.
Later calibration stages may deliberately consume incomplete working parameters;
this does not make them a globally accepted operating configuration. Final
readiness names the exact required capabilities, targets and verification scope.
An allowed degraded scope must be explicit and visible in the result. Never
silently shrink a request and call it fully successful.

Do not require one all-chip transaction before retaining any progress. Conversely,
do not equate a sequence of individually accepted edits with combined acceptance.
Joint tests are chosen by declared interaction scope and laboratory policy, not
an automatic assumption that every combination is either independent or requires
an exhaustive full-chip remeasurement.

Publication still targets a captured branch generation. Concurrent edits may be
retained as candidates but cannot silently rebase already verified evidence.
Reconciliation must reassess the new combination and its required checks. Start
with serialized publication to a shared daily branch; independent acquisition
does not require independent writers to mutate it concurrently.

## Scheduling has three independent constraints

1. Prerequisites: which capabilities must be available before this task?
2. Resources: which devices/settings must be reserved together?
3. Scientific grouping: which operations can be measured simultaneously under
   this policy, and which deliberately test simultaneous operation?

Concurrent jobs are different from one batched acquisition across many targets.
Batching must preserve per-target results and missing/failed coverage. Begin with
explicit laboratory groups and existing resource admission, then optimize using
measured execution cost. Do not infer parallel safety from distinct target IDs.

## Implementation sequence and acceptance scenario

1. **Done:** check-only retained evidence using existing runs, analysis and durable
   procedures; passing and negative scientific outcomes leave the branch unchanged.
2. **Partially implemented:** scoped checks and exact-context applicability with
   inspectable reasons. Still needed: capability requirements/dependencies,
   evidence selection and resolved parameter dependency capture. Avoid a second
   analysis/evidence store.
3. Build target-expanded, staged plans on the durable procedure machinery, with
   bounded recovery and explicit partial completion. Keep planning separate from
   resource dispatch and scientific policy.
4. Add check-first maintenance, batching/resource scheduling and workbench views
   showing required/available/blocked capabilities with their evidence.

Use a small synthetic array with shared readout and several coupling edges as the
next integration scenario. Establish an initial operating revision, inject local
drift and one equipment failure, then demonstrate selective repair, unaffected
progress, restart recovery, explicit degraded scope and final combined checks.
Validate explanations and provenance before benchmarking large target counts.
Hardware behavior and operator usability still require later laboratory trials.

## References

- [Kelly et al., Physical qubit calibration on a directed acyclic graph](https://arxiv.org/abs/1803.03226)
  motivates calibration dependencies and graph traversal.
- [QUAlibrate calibration graphs](https://qualibrate-docs.quantum-machines.co/calibration_graphs/)
  separates target selection and orchestration from calibration nodes.
- [QUAlibrate advanced graphs](https://qualibrate-docs.quantum-machines.co/advanced_calibration_graphs/)
  demonstrates nested flows, adaptive loops and failure handling. These inform the
  direction; they do not define Scopecat's persistence or scientific policy.
