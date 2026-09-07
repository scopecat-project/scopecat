# Supervised laboratory pilot roadmap

Reviewed against public `f2a278e2` on 2026-09-08. The first target is one laboratory,
a supervised operator and a small maintained workflow catalog. Long-running
unattended calibration is a later qualification step. This roadmap supplements
the [project charter](project-charter.md), not a change to its single-user scope.

## Readiness assessment

The execution/data foundations can support developer-assisted trials now. A
routine operator pilot needs a reproducible deployment, data recovery, an
understandable operating surface and declared hardware failure boundaries.
Adding every conceivable experiment or rewriting the execution engine is not
required. A passed virtual test establishes software behavior, not physical
wiring, timing, analog limits or scientific calibration validity.

| Surface | Existing foundation | Remaining pilot or growth work |
|---|---|---|
| Installation and first use | Project discovery, CLI lifecycle, isolated wheel imports, GUI-bundle build script | Installed bundle acceptance and a first visible measurement, without a source/UI build in the operator workflow |
| Instrument operations | Typed capabilities, reservations, isolated workers, generation fencing, release/reconnect | Attributable byte-safe vendor logs and explicit supported reconnect/failure behavior |
| Experiment authoring | Typed inputs, immutable invocation edits, scan coordinates, modules and previews | Shared operator catalog and simpler scalar/axis controls; preserve ordinary Python composition |
| Planning and inspection | Point counts, grouping, selected-point inspection, layered program lineage and bounded compilation | Useful preflight summaries, retention/estimate visibility and target-supplied physical facts |
| Execution | Durable run state, cancellation, resource rejection, procedure waits and parent/child cancellation | Operator actions across run/procedure/worker state; no automatic replay of unknown hardware writes |
| Notebook sessions | Project clients and ID-based run attachment | Clear closed-session behavior and explicit immutable snapshots/reattachment |
| Configuration | Immutable snapshots, proposals, common-base cell merge, verification, generation-fenced acceptance and undo | Entity/field edit intent and unit-aware scientific review |
| Measurements | Arrow records, identity-preserving datasets, entity traces, paging and bounded previews | Narrow entity query pushdown, measured read costs and true complex scalar authoring |
| Analysis | Durable facts, views, artifacts, immutable provenance and multiple figure series | Layered measurement/fit/uncertainty views without duplicating scientific data |
| Automation | Procedures, durable decisions, CalibrationRegistry freshness/dependencies/cohorts | Project adoption, scoped known-software-failure recovery and bounded unattended qualification |
| Persistence and deployment | SQLite/object ownership, explicit schema rejection and source/version pinning | Stopped-project snapshots, restoration and a non-destructive upgrade/old-reader policy |
| Development | Linux/Windows tests, generated APIs/clients, architecture gates, benchmark smoke and reference lab | Shared acceptance fixtures, work-slice ownership and cross-repository adapter qualification |

This is a code/document review, not a fresh usability study or hardware
certification. Focused baseline checks passed: launcher integration, common-base
candidate merging, run handles and project worker APIs (62 tests). Existing
full-suite results are not represented as a fresh full-suite run here.

## Operator journeys and acceptance gates

A supervised pilot should demonstrate these complete journeys:

1. Install a pinned bundle into a clean environment, create a project and see a
   virtual measurement in both Python and the console.
2. Discover a maintained workflow, choose its target and sample, inspect resolved
   configuration/scope, submit once and find its progress and retained results.
3. Review a candidate with its source and verification evidence; rejection leaves
   active unchanged. Any acceptance/undo exercise is explicitly authorized and
   its prospective effect and history remain visible.
4. Cancel idle work or request cancellation after the current step; distinguish
   that request from a completed stop. Inspect the current child and resource
   blocker without correlating internal ledgers manually.
5. Encounter a supported failure, retain the data, identify the responsible phase
   and follow a valid next action. Unknown device outcomes remain quarantined.
6. Stop, snapshot and restore a project into a fresh directory/environment, then
   read the same results. Restoration must not automatically dispatch hardware.

Scientific quality, physical safe-state/readback and which workflows are admitted
remain the laboratory's responsibility. The framework supplies evidence and
control boundaries rather than claiming a universal laboratory safety system.

## Phases

| Phase | Exit condition | Explicitly deferred |
|---|---|---|
| Supervised pilot | The journeys above work on a pinned deployment and a small externally qualified catalog, with supported failure/restore behavior | Broad experiment coverage, multi-user scheduling, arbitrary hardware recovery |
| Daily workflow | Common edits, candidate review, analysis and data selection remain concise and bounded as workload grows | Universal scan joins, plotting language, target-specific performance mechanisms without a measured need |
| Bounded automation | A project uses existing freshness/dependency/publication contracts with finite budgets, auditable decisions and tested stop/restart behavior | Unlimited retries and a distributed/universal scheduler |

MCP is an optional adapter after operation contracts stabilize, not a prerequisite
for a human-operated pilot. Dynamic feedback and new hardware families require
separate target qualification; simulator support is not an admission decision.

## Parallel development foundations

Do not freeze the whole architecture or build a generic plugin platform first.
Stabilize a small set of concrete seams with executable producer/consumer fixtures:

- Catalog/operation contract: experiment identity, supported actions, project
  request/review schema, preview binding, submission identity and state/result refs.
- Configuration contract: entity/field edits, value/unit representation, base and
  generation, proposal provenance and conflict/acceptance meaning.
- Data contract: variable/entity identity, complex scalar shape, layer sources,
  missingness and snapshot versus live-view behavior.
- Hardware contract: connection-local preparation, generation changes, known versus
  unknown effects and target-owned physical limits.
- Deployment contract: exact package/application/schema identities, project state
  location and restoration behavior.

Each contract lands as a small typed change plus its focused fixture before
consumers build against it. Internal breaking changes are allowed; update affected
consumers together rather than layering compatibility adapters. Do not serialize
all feature work behind one giant contract redesign.

### Ownership and merge order

1. Establish shared fixtures and a work-slice template (P01).
2. Open independent lanes for catalog contracts (P02), distribution/data recovery
   (P03/P04), notebook session work (P05), and residency qualification (P16).
3. After the catalog seam lands, operator status (P07), preflight (P08) and project
   catalog consumers can progress independently. Separate form, preview and
   progress modules in P02 to avoid one shared launcher file becoming the lock.
4. Land P16 before P06 if both touch worker/runtime code. Configuration (P09),
   analysis views (P10), query selection (P11), scalar types (P12) and telemetry
   (P13) have independent domains but coordinate wire/schema edits explicitly.
5. Admit the project pilot only after external adapter qualification. Begin
   automated cohorts after the corresponding maintained calibration is qualified.

Keep at most a few active implementation lanes; a suggested starting limit is
four. More issues ready for work does not imply they should all be open branches.
One owner follows each vertical slice through its relevant backend/UI adapters.
A small shared-contract PR precedes consumers; generated files are regenerated
from their sources after rebasing, never manually resolved. Avoid mixing broad
renames/module moves with behavior changes.

Each checkout uses its own project state, endpoint and virtual environment. The
laboratory hardware node runs one integrated version; development worktrees are
not independent hardware owners. External adapters pin the validated public
revision and qualify changes before advancing that pin. The public CI must stay
hardware-free and independent of private source or evidence.

### Tracking rules

Each implementation issue contains a user-visible result, existing evidence,
acceptance criteria, owned seams, prerequisites, validation and exclusions.
Dependencies identify contract/merge prerequisites, not a ban on earlier design
or fixture work. Completion requires the observable outcome and relevant checks,
not only code merged in one package. Issues and their milestones own execution
status; this document owns the readiness rationale and development boundaries.

## GitHub execution index

[Roadmap tracker](https://github.com/scopecat-project/scopecat/issues/405) owns the phase checklists.
The work items are native sub-issues; same-repository prerequisites are also
GitHub blocking dependencies. Milestones track phase membership, not calendar
commitments.

| Work item | Phase | Lane | Prerequisites |
|---|---|---|---|
| [P01: Establish shared acceptance fixtures and change boundaries for parallel pilot work](https://github.com/scopecat-project/scopecat/issues/389) | Supervised pilot | integration | — |
| [P02: Type the experiment catalog and preview/submission responses across Python, HTTP and GUI](https://github.com/scopecat-project/scopecat/issues/390) | Supervised pilot | catalog | [P01 #389](https://github.com/scopecat-project/scopecat/issues/389) |
| [P03: Deliver an installable pilot bundle with a measured first-run workflow](https://github.com/scopecat-project/scopecat/issues/391) | Supervised pilot | distribution | [P01 #389](https://github.com/scopecat-project/scopecat/issues/389) |
| [P04: Back up and restore stopped projects with an explicit schema-version policy](https://github.com/scopecat-project/scopecat/issues/392) | Supervised pilot | persistence | [P01 #389](https://github.com/scopecat-project/scopecat/issues/389) |
| [P05: Make notebook session closure and run reattachment explicit and usable](https://github.com/scopecat-project/scopecat/issues/393) | Supervised pilot | notebook | [P01 #389](https://github.com/scopecat-project/scopecat/issues/389) |
| [P06: Capture vendor output and preserve the causal failure across worker cleanup](https://github.com/scopecat-project/scopecat/issues/400) | Supervised pilot | device-runtime | [P16 #399](https://github.com/scopecat-project/scopecat/issues/399) |
| [P07: Give operators one actionable view of procedure, run and worker state](https://github.com/scopecat-project/scopecat/issues/401) | Supervised pilot | operator-ui | [P02 #390](https://github.com/scopecat-project/scopecat/issues/390) |
| [P08: Expose bounded preflight summaries for scope, retention and estimated work](https://github.com/scopecat-project/scopecat/issues/402) | Supervised pilot | preview | [P02 #390](https://github.com/scopecat-project/scopecat/issues/390) |
| [P09: Represent candidate edits and reviews at entity/field scope with unit-aware conflicts](https://github.com/scopecat-project/scopecat/issues/394) | Daily workflow | configuration | [P01 #389](https://github.com/scopecat-project/scopecat/issues/389) |
| [P10: Render measured data, fitted curves and uncertainty in one provenance-preserving analysis view](https://github.com/scopecat-project/scopecat/issues/395) | Daily workflow | analysis-views | [P01 #389](https://github.com/scopecat-project/scopecat/issues/389) |
| [P11: Push entity selection into bounded measurement reads](https://github.com/scopecat-project/scopecat/issues/396) | Daily workflow | measurement-query | [P01 #389](https://github.com/scopecat-project/scopecat/issues/389) |
| [P12: Support complex scalar products throughout experiment authoring and measurement](https://github.com/scopecat-project/scopecat/issues/397) | Daily workflow | measurement-types | [P01 #389](https://github.com/scopecat-project/scopecat/issues/389) |
| [P13: Record comparable compile, transfer and acquisition cost facts](https://github.com/scopecat-project/scopecat/issues/398) | Daily workflow | telemetry | [P01 #389](https://github.com/scopecat-project/scopecat/issues/389) |
| [P14: Define scoped recovery from known software failures without reopening failed history](https://github.com/scopecat-project/scopecat/issues/403) | Bounded automation | automation | [P01 #389](https://github.com/scopecat-project/scopecat/issues/389), [P02 #390](https://github.com/scopecat-project/scopecat/issues/390) |
| [P15: Declare scalar overrides and scan controls once for notebooks and forms](https://github.com/scopecat-project/scopecat/issues/404) | Daily workflow | authoring-controls | [P02 #390](https://github.com/scopecat-project/scopecat/issues/390), [P08 #402](https://github.com/scopecat-project/scopecat/issues/402) |
| [P16: Qualify connection-local residency and reconnect behavior with a shared target fixture](https://github.com/scopecat-project/scopecat/issues/399) | Supervised pilot | device-runtime | [P01 #389](https://github.com/scopecat-project/scopecat/issues/389) |
