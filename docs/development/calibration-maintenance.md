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
and a public `CalibrationCheckResult` analysis fact. It exercises passing and failing checks
on the same exact accepted revision, without candidates or branch publication.
It is a declared synthetic residual check, not a generic capability registry.

Applicability assessment must evaluate subject/setup/operating conditions,
policy and relevant parameter dependencies. Time is one reason to request a new
check, not proof that values are wrong. Preserve evidence time separately from
parameter modification time. Branch names are destinations, not scientific scope.
A historical check does not become evidence for the newest head merely because
both revisions have occupied the same branch.

`scopecat.records.calibration_check` provides `CalibrationScope` and
`MeasurementContext`; `scopecat.automation.calibration` provides the pure
`assess_calibration_check()` function. Scope
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
or prove complete scientific coverage. The
current exact-revision rule is deliberately conservative; it must not become a
permanent substitute for parameter dependency capture. There is no new store.

The calibration notebook retains scope with its typed check result and exercises
policy changes, parameter changes and expiry. Teaching setups explicitly declare
their software computation scenario; their evidence cannot become physical-device
evidence by omitting a sample binding.

### Selecting retained checks

`lab.calibration_checks.report(context=..., requirements=...)` now evaluates an
explicit bounded set of requirements in one server read snapshot. Requirements
declare scope and maximum evidence age; results retain selection reasons, missing
evidence and scan limits. This is the read primitive for a future capability panel,
not an inferred capability registry or repair planner. See
[capability evidence reports](../how-to/read-calibration-report.md).

`select_calibration_check()` accepts `CheckEvidence` projections from the owning
catalog and an explicit `history_complete` declaration. It filters known context
mismatches, then selects the latest matching run by creation time. This is an
explicit conservative ordering policy, not a measurement timestamp inferred from
analysis publication. The returned selection carries the analysis reference,
assessment and selection reason; it creates no new evidence record.

A newer negative check supersedes an older positive check. A newer unfinished
attempt or missing analysis blocks reuse with `unknown`; an expired latest check
requires rechecking. The selector never searches backward for a passing result.
Different equally recent records (including competing reanalyses of one run)
return `ambiguous_latest` until the caller explicitly resolves their authority.
No matching history returns `no_matching_evidence`.

An incomplete/truncated history returns `incomplete_history`, regardless of the
visible results. A query that retrieves only successful analyses is insufficient:
include failed/unfinished requested checks with their original scope and no result.
The pure selector does not query the daemon or establish query completeness.
The notebook now obtains its history from `lab.calibration_checks.history()` instead
of maintaining an in-memory list of checks.

### Declaring checks and querying their history

A check intent carries a typed `calibration_check: CalibrationCheckRequest` field:
capability scope, exact scientific context, declared measurement/analysis step
addresses and result output ID. This declaration is retained and hashed with the
procedure intent at submission, before any acquisition. It remains readable using
public record types without executing the laboratory's procedure or decoder.
The analysis retains the public `CalibrationCheckResult` under `CHECK_RESULT`
from `scopecat.api.calibration_checks`; laboratory-specific metrics are separate
analysis facts. Execution completion and scientific acceptance remain distinct.

`lab.calibration_checks.history(scope=..., context=...)` queries declared checks
across procedure definitions, in all states. Scope/context filters use the request,
so an unstarted check in a different context need not block the requested context.
`requests` exposes each declaration alongside its execution state for future panel
consumers. Evidence is located by declared addresses, with standard result-schema,
scope and measured-context checks. Invalid evidence raises rather than looking like
an empty complete history. There is no laboratory reader callback or name guessing.

The short-lived `lab.procedures.check_history()` adapter is removed. Unmarked
procedures are generic executions, not inferred checks; no prebaseline reader or
backfill is introduced. Existing files remain untouched. Server admission validates
the declaration against retained parameters, current setup and authoritative
subject/scenario evidence before queueing. The declared child measurement must use
that exact context, without parameter overrides. Exact request retries return the
retained request even after authority changes; new measurements still undergo
normal admission. Laboratory definitions validate executable arguments. Before
recording the declared steps as complete, the server checks the measurement
context and verifies that the adopted analysis belongs to that measurement and
contains the declared standard fact with matching scope. Invalid evidence cannot
advance the procedure revision; a valid negative result can. Independent analysis
publications remain available even if they cannot be adopted as check results.
The server history reader also validates scope and context using the result
adoption contract.

`CalibrationCheckHistory` reports evidence, unresolved request IDs, scanned count
and `incomplete_reasons`: `scan_limit`, `unresolved_checks` or `journal_changed`.
Its `complete` property is true only when those reasons are absent. Call
`history.select(...)` to carry this completeness into evidence selection
automatically. A final batch observation compares all read request revisions and
the filtered head at one server read snapshot. Changed, missing or now out-of-scope
requests are reported as changed; a new matching request changes the head. Unrelated
tasks do neither. This remains an observational query, not a transaction fence or
permission to publish. A new request can arrive after the comparison returns.

`POST /api/v1/calibration-checks/query` pages admitted checks using an indexed
projection written atomically with the procedure. Exact scope and context filters
run before pagination. All execution states are included; ordinary procedures and
nonmatching checks do not consume the history budget (200 matching requests by
default, maximum 2,000). Hashes identify canonical declaration JSON, not Python class identity.
Each response item includes `execution`, `request` and optional `evidence` with
the retained measurement, analysis ID and positive or negative result. Evidence
resolution runs on the server without importing author code. Page selection,
step outputs and measurement snapshots share one read transaction; analysis
publications are fixed immutable records. Unfinished work returns no evidence,
while invalid retained evidence raises. Cross-page reads are not a transaction
snapshot. `POST /api/v1/calibration-checks/observe` performs the bounded revision
and head comparison without loading scientific evidence. It returns
`changed_procedures` and `head_changed`; the facade uses them for conservative
incomplete-history reporting. The Python facade makes one final observation
request instead of individual procedure reads, and no longer fetches individual
steps, measurements or analysis content to assemble each item.
Development schema 89 adds this projection without a prebaseline backfill or
migration. Earlier stores remain untouched and require their historical environment.

The real-daemon notebook journey covers pagination, scan-budget exhaustion,
unstarted check declarations, filtering another parameter context and history
recovery after reconnecting. A separate assertion
advances a procedure during reading and confirms that only a later stable read
can report complete history. It never filters out a newer negative check to
recover an older positive result.

## Dependency capture has explicit limits

Current `ExperimentPreviewParameterLookup` exposes table, column and key-column
names, explicitly not resolved row identities. It is useful author feedback but
is not an applicability fingerprint. A complete parameter/configuration hash is
also too coarse to decide which capabilities need rechecking.

Declarative keyed parameter queries now retain model-independent key and value
cells, including indirect lookups used by derived expressions. Recipe invocation
evidence retains these through the existing ledger and labels coverage as
`recipe_keyed_query_values`. The public comparison reports changed cells and
missing/ambiguous membership without inferring full measurement applicability.
See [adapter evidence](architecture/quantum-adapters.md).

Before selective invalidation, extend this coverage to scalar expressions,
runtime reads, selections and derived queries outside recipe preparation.
Query membership matters:
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
2. **Partially implemented:** scoped checks, exact-context applicability and
   selection from explicit complete histories, with inspectable reasons.
   Explicit capability requirements/dependencies now produce a bounded report
   with separate own-evidence and prerequisite-availability verdicts. Resolved
   parameter dependency capture has begun with retained recipe keyed queries;
   full run/analysis coverage and applicability integration remain needed.
   Indexed scoped queries, server
   evidence pages and bounded batch
   observation checks are implemented. Avoid a second analysis/evidence store.
3. **Fixed tasks implemented:** target-expanded stage plans declare exact checks
   and dependencies, with explicit partial completion. Tasks persist call templates
   and enforce prerequisites when dispatching each stage atomically. See
   [staged tasks](../how-to/preview-calibration-tasks.md). Sequential daemon advancement
   and fenced start/pause/cancel controls are implemented. Explicit candidate edges
   now bind passing stages' adopted proposals to later check inputs atomically.
   Finalization and a complete calibration/publication journey are implemented;
   bounded repair remains. Keep
   planning separate from resource dispatch and scientific policy.
4. Bounded sample/task reports already show required, available and blocked
   capabilities with evidence. Add continuous refresh, check-first maintenance and
   batching/resource scheduling. See the next [parameter-flow contract](architecture/task-parameter-flow.md).

The [six-target qualification](array-maintenance-qualification.md) exercises
logical shared readout and coupling, local drift, a synthetic readout outage,
unaffected progress, restart recovery and final combined checks. It also verifies
that concurrent branch edits survive a stale publication attempt. Selective
repair and physical resource-failure qualification remain separate work.
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
